"""Source-defect detectors over the semantic layer (Raha-style multi-strategy detection + discovered functional dependencies).

Detection runs once per semantic-layer source digest, read-only, and is cached beside the store (stack/source-defects.json).
Each strategy proposes candidate rules from a bounded row sample of each table; every candidate is then confirmed by one
exact full-table SQL count, so a finding's count is exact while discovery stays fast:

  fd_violation       approximate functional dependencies X -> Y discovered on the sample (TANE-style: an id-like determinant
                     whose multi-row groups carry one Y value in >= 98% of groups), plus declared domain FDs
                     (DECLARED_FDS, e.g. encounter_id -> patient_id); a group with more than one Y is a violation
  pattern            value-shape rules (digits -> 9, letters -> A): the accepted shapes cover >= 97% of the sample; values
                     outside every accepted shape are violations (malformed NPI, ICD-10, ZIP, LOINC ...)
  date_order         paired dates out of order (discharge before admission, service_to before service_from, supply end
                     before fill, claim received before service)
  future_date        event dates after the snapshot as-of date (scheduling / coverage-end columns exempt)
  invalid_date       a date column value that is not a calendar date or lies before 1900
  out_of_range       negative values in non-negative quantities (days supply, LOS, billed/charge/allowed cents, pages)
  outlier            values beyond median +/- 10 x MAD of the sample (robust z), counted over the full table
  orphan_key         foreign keys with no parent row (patient, organization, clinician, encounter)
  duplicate_key      a business key carried by more than one row (claim_id, payer_claim_id, encounter_id, patient_id ...)
  unit_mismatch      one lab/vital name reported in several units whose typical values differ >= 5x (scale mismatch)
  source_flag        defect flags the feed itself sets (discharge_before_admit, out_of_order, duplicate_submission ...)

    detect(path, as_of=None, refresh=False) -> {'schema', 'source_digest', 'as_of', 'findings': [...], 'rules': [...], 'tables'}
    findings(path, tables=None, detectors=None) -> [finding]       (cached)
    annotate(stack, question, graph, answer) -> [message]          answer_checks hook: defects of the tables an answer read
Engine 'source_defects' on the foundation-engine registry answers data-quality questions from the same findings.
No model calls; synthetic data only.
"""
import json
import math
import os
import re
import sqlite3
import statistics
import time
from pathlib import Path

VERSION = 'source-defects/1'
CACHE = 'source-defects.json'
SAMPLE_ROWS = 6000          # rows sampled per table for discovery (4 evenly spaced chunks)
FD_SUPPORT = 0.98           # share of multi-row groups with one Y value for an approximate FD
FD_MIN_GROUPS = 15
PATTERN_COVER = 0.97
SHAPE_MIN_SHARE = 0.02
MAX_SHAPES = 4
MAD_K = 10.0
UNIT_RATIO = 5.0
TABLE_BUDGET_SECONDS = 25.0
DETECTORS = ('fd_violation', 'pattern', 'date_order', 'future_date', 'invalid_date', 'out_of_range', 'outlier', 'orphan_key',
             'duplicate_key', 'unit_mismatch', 'source_flag')
TABLES = ('claim', 'claim_line', 'payer_claim', 'payer_claim_line', 'payer_pharmacy_claim', 'encounter', 'visit', 'adt_visit',
          'stay', 'measurement', 'fill', 'prescription', 'patient', 'clinician', 'organization', 'enrollment_span', 'condition',
          'encounter_diagnosis', 'scanned_document', 'immunization', 'utilization_event', 'clinical_note', 'appointment')
# plain-language feed names -> tables (questions and answers)
FEEDS = {'claims': ('claim', 'claim_line', 'payer_claim', 'payer_claim_line', 'payer_pharmacy_claim'),
         'medical claims': ('claim', 'claim_line', 'payer_claim', 'payer_claim_line'), 'payer claims': ('payer_claim', 'payer_claim_line', 'payer_pharmacy_claim'),
         'pharmacy': ('fill', 'payer_pharmacy_claim', 'prescription'), 'pharmacy claims': ('payer_pharmacy_claim',), 'fills': ('fill',),
         'prescriptions': ('prescription',), 'encounters': ('encounter', 'visit', 'encounter_diagnosis'), 'visits': ('visit', 'encounter'),
         'adt': ('adt_visit',), 'admissions': ('adt_visit', 'stay'), 'hospital stays': ('stay', 'adt_visit'), 'stays': ('stay',),
         'labs': ('measurement',), 'lab results': ('measurement',), 'vitals': ('measurement',), 'measurements': ('measurement',),
         'patients': ('patient',), 'demographics': ('patient',), 'members': ('patient', 'enrollment_span'), 'enrollment': ('enrollment_span',),
         'eligibility': ('enrollment_span',), 'providers': ('clinician', 'organization'), 'clinicians': ('clinician',),
         'organizations': ('organization',), 'diagnoses': ('condition', 'encounter_diagnosis'), 'conditions': ('condition',),
         'problem list': ('condition',), 'scanned documents': ('scanned_document',), 'faxes': ('scanned_document',),
         'immunizations': ('immunization',), 'notes': ('clinical_note',), 'appointments': ('appointment',), 'utilization': ('utilization_event',)}
KEYS = {'claim': 'claim_id', 'payer_claim': 'payer_claim_id', 'visit': 'encounter_id', 'patient': 'patient_id',
        'clinician': 'clinician_id', 'organization': 'organization_id', 'adt_visit': 'visit_number', 'fill': 'fill_id',
        'scanned_document': 'record_id'}
FOREIGN = {'patient_id': ('patient', 'patient_id'), 'organization_id': ('organization', 'organization_id'),
           'service_organization_id': ('organization', 'organization_id'), 'attending_clinician_id': ('clinician', 'clinician_id'),
           'pcp_clinician_id': ('clinician', 'clinician_id')}
DECLARED_FDS = (('encounter_id', 'patient_id'), ('claim_id', 'patient_id'), ('payer_claim_id', 'patient_id'), ('npi', 'clinician_id'),
                ('visit_number', 'patient_id'), ('document_id', 'patient_id'), ('member_id', 'patient_id'))
ORDER_PAIRS = (('admit_date', 'discharge_date'), ('admission_date', 'discharge_date'), ('service_from', 'service_to'),
               ('event_date', 'end_date'), ('fill_date', 'supply_end'), ('service_from', 'claim_received_date'),
               ('service_date', 'first_paid_date'), ('service_from', 'paid_date'), ('birth_date', 'event_date'),
               ('start_date', 'end_date'), ('first_submitted_date', 'first_paid_date'))
NONNEG = re.compile(r'(days_supply|los_days$|^billed_cents$|^charge_cents$|^allowed_cents$|page_count|text_chars|^versions$|age_reported|age_latest)')
CODE_FORMATS = (  # column pattern -> (standard, regex); ICD-10-CM letters allowed after the first character (F03.A0, I1A.0)
    (re.compile(r'(icd10$|icd10_category|principal_icd10|principal_diagnosis$)'), ('ICD-10-CM', r'^[A-Z][0-9][0-9A-Z](\.[0-9A-Z]{1,4})?$')),
    (re.compile(r'(^npi$|_npi$)'), ('NPI (10 digits)', r'^[0-9]{10}$')),
    (re.compile(r'(^zip$|postal_code)'), ('ZIP code', r'^[0-9]{5}(-[0-9]{4})?$')),
    (re.compile(r'^loinc$'), ('LOINC', r'^[0-9]{1,7}-[0-9]$')),
    (re.compile(r'(rxcui|rxnorm)'), ('RxNorm CUI (list)', r'^[0-9]+(,[0-9]+)*$')),
    (re.compile(r'^snomed$'), ('SNOMED CT id', r'^[0-9]{6,18}$')),
    (re.compile(r'^cpt$'), ('CPT', r'^[0-9]{4}[0-9A-Z]$')),
    (re.compile(r'^hcpcs$'), ('HCPCS', r'^[A-Z0-9][0-9]{4}$')),
)
OUTLIER_COLS = re.compile(r'(cents$|amount|quantity|days_supply|los_days)')
FUTURE_EXEMPT = re.compile(r'(supply_end|through|due|schedul|expir|end_date|term|discharge_date|service_to|next|appointment)')
FUTURE_EXEMPT_TABLES = {'appointment', 'enrollment_span'}
DEFECT_FLAGS = ('discharge_before_admit', 'out_of_order', 'admit_message_missing', 'duplicate_submission', 'rendering_npi_missing',
                'ever_denied')
DEFECT_FLAGS = DEFECT_FLAGS[:-1]  # denial is a business outcome, not a source defect
_FD_IGNORE = re.compile(r'(^record_id$|source_table|status$|version|sequence|available_date|recorded_date|supersed|_time$|_utc$|messages|updates)')
_DATE = re.compile(r'^\d{4}-\d{2}-\d{2}')
_SKIP_PATTERN = re.compile(r'(^record_id$|_ids$|record_ids|payload|text$|^name$|title|display|reason|description|adjudications|sources|messages|diagnosis_codes)')


def _ro(db_path):
    db = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    db.execute('PRAGMA query_only=1')
    db.create_function('agere_regexp', 2, lambda rx, v: None if v is None else re.search(rx, str(v)) is not None, deterministic=True)
    db.create_function('agere_shape', 2, lambda v, c: None if v is None else shape(v, c or ''), deterministic=True)
    return db


def _target(path):
    p = Path(path)
    return p if p.suffix == '.sqlite3' else p/'semantic.sqlite3'


def _meta(db):
    try:
        return dict(db.execute('SELECT key, value FROM meta'))
    except sqlite3.Error:
        return {}


def _qi(name): return '"' + name.replace('"', '""') + '"'


def _q(v): return "'" + str(v).replace("'", "''") + "'"


def _columns(db, table):
    return [r[1] for r in db.execute(f'PRAGMA table_info({_qi(table)})')]


def _sample(db, table, cols, n=SAMPLE_ROWS):
    hi = db.execute(f'SELECT max(rowid) FROM {_qi(table)}').fetchone()[0] or 0
    sel = ', '.join(_qi(c) for c in cols)
    if hi <= n:
        return [dict(zip(cols, r)) for r in db.execute(f'SELECT {sel} FROM {_qi(table)}')], hi
    chunk, rows = n // 4, []
    for i in range(4):
        start = int(hi * i / 4)
        rows += [dict(zip(cols, r)) for r in db.execute(f'SELECT {sel} FROM {_qi(table)} WHERE rowid > ? LIMIT ?', (start, chunk))]
    return rows, hi


_CODE = re.compile(r'(icd|diagnosis|snomed|loinc|cpt|hcpcs|rxcui|rxnorm|revenue_code|drg|taxonomy)')
_ID_SHAPE = re.compile(r'(_id$|_number$)')


def shape(v, column=''):
    """Value shape: digits -> 9, letters -> A. Code columns (ICD-10 'F03.A0', SNOMED) map any alphanumeric to X, keeping length and
    punctuation (a dotless ICD-10 still differs); identifiers collapse runs (ORG-NAME-12 ~ ORG-OTHERNAME-3)."""
    s = str(v)
    if _CODE.search(column):
        out = re.sub(r'[A-Za-z0-9]', 'X', s)
        return re.sub(r'X+', 'X+', out) if re.search(r'(snomed|rxcui|rxnorm)', column) else out
    out = re.sub(r'[A-Za-z]', 'A', re.sub(r'\d', '9', s))
    return re.sub(r'A+', 'A', re.sub(r'9+', '9', out)) if _ID_SHAPE.search(column) else out


def _like_shape(column, shp):
    """SQL predicate: column's value has shape shp (Python shape() registered as a SQLite function on the connection)."""
    return f'agere_shape({_qi(column)}, {_q(column)}) = {_q(shp)}'


def _examples(db, table, where, limit=5):
    cols = _columns(db, table)
    key = 'record_id' if 'record_id' in cols else KEYS.get(table) if KEYS.get(table) in cols else cols[0]
    return [r[0] for r in db.execute(f'SELECT {_qi(key)} FROM {_qi(table)} WHERE {where} LIMIT {limit}')]


def _finding(detector, table, columns, count, total, description, examples=(), **extra):
    rate = count / total if total else 0.0
    severity = 'warning' if (rate >= 0.001 or detector in ('duplicate_key', 'orphan_key', 'date_order', 'fd_violation')) else 'info'
    if detector == 'outlier': severity = 'info'
    return {'id': f'{detector}:{table}:{"+".join(columns)}', 'detector': detector, 'table': table, 'columns': list(columns),
            'count': int(count), 'total': int(total), 'rate': round(rate, 6), 'severity': severity,
            'description': description, 'examples': list(examples), **extra}


def _numeric(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _table(db, table, as_of, deadline):
    """Findings and confirmed rules for one table."""
    cols = _columns(db, table)
    rows, approx = _sample(db, table, cols)
    if not rows: return [], []
    total = db.execute(f'SELECT count(*) FROM {_qi(table)}').fetchone()[0]
    found, rules = [], []
    T = _qi(table)

    def count(where):
        return db.execute(f'SELECT count(*) FROM {T} WHERE {where}').fetchone()[0]

    values = {c: [r[c] for r in rows if r[c] not in (None, '')] for c in cols}
    date_cols = [c for c in cols if values[c] and sum(1 for v in values[c] if isinstance(v, str) and _DATE.match(v)) >= 0.9 * len(values[c])]
    # dates
    for c in date_cols:
        if time.perf_counter() > deadline: break
        C = _qi(c)
        bad = count(f"{C} IS NOT NULL AND {C} <> '' AND (date(substr({C},1,10)) IS NULL OR substr({C},1,4) < '1900')")
        if bad: found.append(_finding('invalid_date', table, [c], bad, total, f'{bad:,} {table}.{c} values are not full calendar dates (e.g. '
                                      + ', '.join(repr(v) for v, in db.execute(f"SELECT DISTINCT {C} FROM {T} WHERE {C} IS NOT NULL AND {C} <> '' AND "
                                                                               f"(date(substr({C},1,10)) IS NULL OR substr({C},1,4) < '1900') LIMIT 3")) + ')',
                                      _examples(db, table, f"{C} IS NOT NULL AND {C} <> '' AND (date(substr({C},1,10)) IS NULL OR substr({C},1,4) < '1900')")))
        if as_of and table not in FUTURE_EXEMPT_TABLES and not FUTURE_EXEMPT.search(c) and c not in ('available_date', 'last_available_date'):
            where = f"substr({C},1,10) > {_q(str(as_of)[:10])}"
            n = count(where)
            if n: found.append(_finding('future_date', table, [c], n, total, f'{n:,} {table}.{c} dates fall after the snapshot date {str(as_of)[:10]}',
                                        _examples(db, table, where)))
    for a, b in ORDER_PAIRS:
        if a in date_cols and b in date_cols and time.perf_counter() < deadline:
            where = f"{_qi(a)} IS NOT NULL AND {_qi(b)} IS NOT NULL AND {_qi(b)} <> '' AND substr({_qi(b)},1,10) < substr({_qi(a)},1,10)"
            n = count(where)
            rules.append({'rule': f'{table}.{a} <= {table}.{b}', 'detector': 'date_order', 'violations': n})
            if n: found.append(_finding('date_order', table, [a, b], n, total, f'{n:,} {table} rows have {b} before {a}', _examples(db, table, where)))
    # numbers
    for c in cols:
        if time.perf_counter() > deadline: break
        nums = [v for v in values[c] if _numeric(v)]
        if not nums or len(nums) < 0.95 * len(values[c]) or c.endswith('_id'): continue
        C = _qi(c)
        if NONNEG.search(c):
            n = count(f'{C} < 0')
            if n: found.append(_finding('out_of_range', table, [c], n, total, f'{n:,} {table}.{c} values are negative', _examples(db, table, f'{C} < 0')))
        if len(set(nums)) > 20 and OUTLIER_COLS.search(c) and table != 'measurement':  # mixed analytes: unit_mismatch covers labs
            med = statistics.median(nums)
            mad = statistics.median(abs(x - med) for x in nums)
            if mad > 0:
                lo, hi = med - MAD_K * 1.4826 * mad, med + MAD_K * 1.4826 * mad
                where = f'{C} IS NOT NULL AND ({C} < {lo!r} OR {C} > {hi!r})'
                n = count(where)
                if n and n / max(total, 1) < 0.02:
                    found.append(_finding('outlier', table, [c], n, total, f'{n:,} {table}.{c} values lie beyond 10 robust deviations '
                                          f'of the median ({med:g}; accepted range {lo:.4g} to {hi:.4g})', _examples(db, table, where), bounds=[lo, hi]))
    # patterns
    for c in cols:
        if time.perf_counter() > deadline: break
        vs = [v for v in values[c] if isinstance(v, str)]
        if c in date_cols or _SKIP_PATTERN.search(c) or len(vs) < 50: continue
        standard = next((f for rx, f in CODE_FORMATS if rx.search(c)), None)
        if standard:  # a published code format: check every value against it (dotless ICD-10, 9-digit NPI, short ZIP ...)
            name, regex = standard
            where = f"{_qi(c)} IS NOT NULL AND {_qi(c)} <> '' AND NOT agere_regexp({_q(regex)}, {_qi(c)})"
            n = count(where)
            rules.append({'rule': f'{table}.{c} is a {name}', 'detector': 'pattern', 'violations': n})
            if n: found.append(_finding('pattern', table, [c], n, total, f'{n:,} {table}.{c} values are not valid {name} format (e.g. '
                                        + ', '.join(repr(v) for v, in db.execute(f'SELECT DISTINCT {_qi(c)} FROM {T} WHERE {where} LIMIT 3')) + ')',
                                        _examples(db, table, where), standard=name))
            continue
        if _ID_SHAPE.search(c) or len(set(vs)) < 20: continue  # synthetic/vendor identifiers vary by design; orphan_key checks them
        if sum(len(v) for v in vs) / len(vs) > 24: continue
        shapes = {}
        for v in vs: shapes[shape(v, c)] = shapes.get(shape(v, c), 0) + 1
        accepted = [s for s, k in sorted(shapes.items(), key=lambda kv: -kv[1]) if k / len(vs) >= SHAPE_MIN_SHARE][:MAX_SHAPES]
        cover = sum(shapes[s] for s in accepted) / len(vs)
        if not accepted or cover < PATTERN_COVER or cover == 1.0 and len(shapes) == len(accepted) and False: continue
        where = (f"{_qi(c)} IS NOT NULL AND {_qi(c)} <> '' AND agere_shape({_qi(c)}, {_q(c)}) NOT IN ("
                 + ', '.join(_q(s) for s in accepted) + ')')
        n = count(where)
        rules.append({'rule': f'{table}.{c} shape in {accepted}', 'detector': 'pattern', 'violations': n})
        if n: found.append(_finding('pattern', table, [c], n, total, f'{n:,} {table}.{c} values do not match the column\'s usual '
                                    f'format ({", ".join(accepted)}; e.g. ' + ', '.join(repr(v) for v, in db.execute(
                                        f'SELECT DISTINCT {_qi(c)} FROM {T} WHERE {where} LIMIT 3')) + ')', _examples(db, table, where), shapes=accepted))
    # keys, foreign keys, flags
    key = KEYS.get(table)
    if key in cols and time.perf_counter() < deadline:
        n = db.execute(f'SELECT count(*), coalesce(sum(k), 0) FROM (SELECT count(*) - 1 AS k FROM {T} WHERE {_qi(key)} IS NOT NULL '
                       f'GROUP BY {_qi(key)} HAVING count(*) > 1)').fetchone()
        rules.append({'rule': f'{table}.{key} unique', 'detector': 'duplicate_key', 'violations': n[0]})
        if n[0]: found.append(_finding('duplicate_key', table, [key], n[0], total, f'{n[0]:,} {table}.{key} values appear on more than one row '
                                       f'({n[1]:,} extra rows)', [r[0] for r in db.execute(
                                           f'SELECT {_qi(key)} FROM {T} GROUP BY {_qi(key)} HAVING count(*) > 1 LIMIT 5')], extra_rows=n[1]))
    for c, (ref, refkey) in FOREIGN.items():
        if c in cols and ref != table and time.perf_counter() < deadline:
            try:
                where = f'{_qi(c)} IS NOT NULL AND {_qi(c)} <> \'\' AND {_qi(c)} NOT IN (SELECT {_qi(refkey)} FROM {_qi(ref)})'
                n = count(where)
            except sqlite3.Error:
                continue
            rules.append({'rule': f'{table}.{c} -> {ref}.{refkey}', 'detector': 'orphan_key', 'violations': n})
            if n: found.append(_finding('orphan_key', table, [c], n, total, f'{n:,} {table} rows name a {c} with no {ref} record', _examples(db, table, where)))
    for c in DEFECT_FLAGS:
        if c in cols:
            n = count(f'{_qi(c)} = 1')
            if n: found.append(_finding('source_flag', table, [c], n, total, f'{n:,} {table} rows carry the feed\'s own {c} flag', _examples(db, table, f'{_qi(c)} = 1')))
    # functional dependencies
    # auto-discovered determinants: the table's own entity identifiers (a patient or provider id determines nothing per row)
    ids = [c for c in cols if c in ({KEYS.get(table), 'visit_number', 'document_id', 'interface_message_id', 'fill_id'}
                                    | ({'encounter_id'} if table in ('encounter', 'visit') else set())) and values[c]]
    candidates = []
    for x in ids:
        groups = {}
        for r in rows:
            if r[x] not in (None, ''): groups.setdefault(r[x], []).append(r)
        multi = [g for g in groups.values() if len(g) > 1]
        if len(multi) < FD_MIN_GROUPS: continue
        for y in cols:
            if y == x or _FD_IGNORE.search(y) or _SKIP_PATTERN.search(y) and y != 'name': continue
            if not values[y] or len(set(map(str, values[y]))) < 2: continue
            const = sum(1 for g in multi if len({str(r[y]) for r in g if r[y] not in (None, '')}) <= 1)
            if const / len(multi) >= FD_SUPPORT: candidates.append((const / len(multi), len(multi), x, y))
    declared = [(x, y) for x, y in DECLARED_FDS if x in cols and y in cols and x != KEYS.get(table)]
    chosen = list(dict.fromkeys(declared + [(x, y) for _, _, x, y in sorted(candidates, key=lambda t: (-t[0], -t[1]))]))[:10]
    for x, y in chosen:
        if time.perf_counter() > deadline: break
        X, Y = _qi(x), _qi(y)
        n = db.execute(f"SELECT count(*) FROM (SELECT {X} FROM {T} WHERE {X} IS NOT NULL AND {X} <> '' AND {Y} IS NOT NULL AND {Y} <> '' "
                       f"GROUP BY {X} HAVING count(DISTINCT {Y}) > 1)").fetchone()[0]
        groups = db.execute(f"SELECT count(DISTINCT {X}) FROM {T} WHERE {X} IS NOT NULL").fetchone()[0]
        rules.append({'rule': f'{table}: {x} -> {y}', 'detector': 'fd_violation', 'violations': n, 'declared': (x, y) in declared})
        if n and (n / max(groups, 1) <= 0.05 or (x, y) in declared):
            ex = [r[0] for r in db.execute(f"SELECT {X} FROM {T} WHERE {X} IS NOT NULL AND {Y} IS NOT NULL AND {Y} <> '' GROUP BY {X} "
                                           f"HAVING count(DISTINCT {Y}) > 1 LIMIT 5")]
            found.append(_finding('fd_violation', table, [x, y], n, groups, f'{n:,} {table}.{x} values map to more than one {y} '
                                  f'(the dependency {x} -> {y} holds for the rest)', ex, declared=(x, y) in declared))
    # unit mismatch (lab / vital names reported in several units of different scale)
    if table == 'measurement' and {'name', 'unit', 'value'} <= set(cols) and time.perf_counter() < deadline:
        stats = {}
        for name, unit, n, avg in db.execute(f"SELECT name, unit, count(*), avg(CAST(value AS REAL)) FROM {T} WHERE value IS NOT NULL "
                                             f"AND unit IS NOT NULL AND unit <> '' GROUP BY name, unit"):
            stats.setdefault(name, []).append((n, unit, avg))
        for name, units in stats.items():
            units = [u for u in units if u[2] not in (None, 0)]
            if len(units) < 2: continue
            units.sort(reverse=True)
            main = units[0]
            off = [u for u in units[1:] if abs(math.log10(abs(u[2]) / abs(main[2]))) >= math.log10(UNIT_RATIO)]
            if off:
                n = sum(u[0] for u in off)
                found.append(_finding('unit_mismatch', table, ['name', 'unit', 'value'], n, sum(u[0] for u in units),
                                      f"{n:,} {name} results are in {', '.join(u[1] for u in off)} while most are in {main[1]}; typical values "
                                      f"differ {abs(off[0][2] / main[2]):.3g}x (unconverted units)",
                                      _examples(db, table, f"name = {_q(name)} AND unit IN ({', '.join(_q(u[1]) for u in off)})"), subject=name))
    return found, rules


def detect(path, as_of=None, refresh=False, tables=TABLES, cache_dir=None):
    """Run every detector (or read the cache for this source digest). path: a stack dir or a semantic.sqlite3 file."""
    target = _target(path)
    cache = Path(cache_dir or target.parent)/CACHE
    db = _ro(target)
    try:
        meta = _meta(db)
        digest = meta.get('source_digest') or str(target.stat().st_mtime_ns)
        as_of = as_of or meta.get('as_of')
        if not refresh and cache.exists():
            try:
                found = json.loads(cache.read_text())
                if found.get('source_digest') == digest and found.get('version') == VERSION: return found
            except (OSError, ValueError):
                pass
        present = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        started, out, rules, timing = time.perf_counter(), [], [], {}
        for t in tables:
            if t not in present: continue
            t0 = time.perf_counter()
            try:
                f, r = _table(db, t, as_of, t0 + TABLE_BUDGET_SECONDS)
            except sqlite3.Error as exc:  # one table failing never stops the others
                timing[t] = f'error: {exc}'[:200]; continue
            out += f; rules += r
            timing[t] = round(time.perf_counter() - t0, 2)
    finally:
        db.close()
    report = {'schema': 'agere-source-defects/1', 'version': VERSION, 'source_digest': digest, 'as_of': as_of,
              'findings': sorted(out, key=lambda f: (f['severity'] != 'warning', -f['count'])), 'rules': rules,
              'tables': timing, 'seconds': round(time.perf_counter() - started, 2)}
    try:
        tmp = cache.with_suffix('.tmp'); tmp.write_text(json.dumps(report, indent=1, default=str)); os.replace(tmp, cache)
    except OSError:
        pass  # read-only location: answers still use the computed report
    return report


_MEMO = {}


def cached(path):
    """The cached report for this stack without computing (None when detection never ran)."""
    target = _target(path)
    cache = target.parent/CACHE
    try:
        st = cache.stat()
    except OSError:
        return None
    key = (str(cache), st.st_mtime_ns)
    if key not in _MEMO:
        try: _MEMO.clear(); _MEMO[key] = json.loads(cache.read_text())
        except (OSError, ValueError): return None
    return _MEMO[key]


def findings(path, tables=None, detectors=None):
    report = detect(path)
    return [f for f in report['findings'] if (not tables or f['table'] in tables) and (not detectors or f['detector'] in detectors)]


# ============================================================================== engine on the foundation registry
OUTPUTS = ('findings', 'summary', 'rules')
PRODUCT = {'population': 'optional', 'view': 'table',
           'use': 'DATA QUALITY / SOURCE DEFECTS: detected defects per received table with exact counts and example records: '
                  'functional-dependency violations (an encounter or claim id mapping to two patients), malformed values (NPI, ICD-10, '
                  'ZIP formats), dates out of order (discharge before admission), future or invalid dates, negative quantities, '
                  'outliers, orphan keys, duplicate business keys, unconverted lab units and the feed\'s own defect flags.'}
CONTRACT = {'inputs': ['optional earlier_set'], 'parameters': {'tables': [], 'detectors': [], 'output': 'findings|summary|rules', 'min_count': 1},
            'meaning': 'Source-defect findings (dq_detectors.py, detected once per source digest, exact full-table counts): tables = semantic '
                       'tables or empty for all; detectors = ' + '|'.join(DETECTORS) + '. Use for data-quality, feed-defect and data-integrity '
                       'questions about the received data itself, not clinical questions.'}
NATIVE_SCALARS = ('count',)


def parameter_schema(catalog, obj, string, array):
    return obj({'tables': array(string(TABLES), 'Semantic tables to report; empty = all'),
                'detectors': array(string(DETECTORS), 'Detector kinds; empty = all'),
                'output': string(OUTPUTS, 'findings: one row per defect; summary: counts per table; rules: discovered rules with violations'),
                'min_count': {'type': 'integer', 'minimum': 1, 'maximum': 1000000}})


def validate(p):
    if not isinstance(p, dict) or set(p) - {'tables', 'detectors', 'output', 'min_count'}:
        raise ValueError('source_defects parameters: tables, detectors, output, min_count')
    tables = list(p.get('tables') or [])
    detectors = list(p.get('detectors') or [])
    if any(t not in TABLES for t in tables): raise ValueError(f'tables: subset of {TABLES}')
    if any(d not in DETECTORS for d in detectors): raise ValueError(f'detectors: subset of {DETECTORS}')
    output = p.get('output') or 'findings'
    if output not in OUTPUTS: raise ValueError(f'output: one of {OUTPUTS}')
    m = p.get('min_count', 1)
    if type(m) is not int or m < 1: raise ValueError('min_count: integer >= 1')
    return {'tables': tables, 'detectors': detectors, 'output': output, 'min_count': m}


def execute(path, patient_ids, params, as_of):
    p = validate(params)
    report = detect(path, as_of)
    rows = [f for f in report['findings'] if (not p['tables'] or f['table'] in p['tables'])
            and (not p['detectors'] or f['detector'] in p['detectors']) and f['count'] >= p['min_count']]
    rules = [r for r in report['rules'] if (not p['tables'] or r['rule'].split('.')[0].split(':')[0] in p['tables'])
             and (not p['detectors'] or r['detector'] in p['detectors'])]
    ids = sorted({str(x) for f in rows for x in f['examples']}) if patient_ids is None else []
    return {'parameters': p, 'findings': rows, 'rules': rules, 'source_digest': report['source_digest'], 'as_of': report['as_of'],
            'tables_checked': sorted(report['tables']), 'example_records': ids, 'detector_version': VERSION}


def answer(report, plan, question):
    p, rows = report['parameters'], report['findings']
    scope = ', '.join(p['tables']) if p['tables'] else f"{len(report['tables_checked'])} received tables"
    kinds = ', '.join(p['detectors']) if p['detectors'] else 'all detectors'
    if p['output'] == 'summary':
        agg = {}
        for f in rows:
            a = agg.setdefault(f['table'], {'table': f['table'], 'findings': 0, 'defective_values': 0, 'warnings': 0})
            a['findings'] += 1; a['defective_values'] += f['count']; a['warnings'] += f['severity'] == 'warning'
        table = sorted(agg.values(), key=lambda a: -a['defective_values'])
    elif p['output'] == 'rules':
        table = report['rules']
    else:
        table = [{k: f[k] for k in ('table', 'detector', 'columns', 'count', 'total', 'rate', 'severity', 'description', 'examples')} for f in rows]
    head = (f"{len(rows)} source defect finding(s) in {scope} ({kinds}); largest: {rows[0]['description']}" if rows else
            f'No source defects found in {scope} ({kinds}).')
    method = (f"Detected by {VERSION} on source digest {report['source_digest'][:12]}: candidate rules discovered on a bounded row sample, "
              'every finding counted exactly over the full table (functional dependencies, value shapes, date order, ranges, keys, units, feed flags).')
    return {'status': 'answered', 'answer_type': 'table', 'table': table, 'count': len(rows), 'value': head, 'headline': head,
            'patient_ids': [], 'method': method, 'limitations': [method, 'Defects describe the received data, not clinical facts; a '
                                                                  'finding is a record a reviewer should check, not proof the source is wrong.'],
            'definitions': [{'term': 'source defect', 'basis': method}]}


# ------------------------------------------------------------------------------ exact local routing
_WORDS = set('''a an the and or of in on at to for from by with our my we any there are is was were do does did have has
which what how many much show list find me all every each where that this these those any get give tell be been data feed
feeds source sources received file files table tables records record rows row problems problem issues issue errors error
quality defects defect defective integrity check checks look looks like see wrong bad broken invalid detected detect found'''.split())
_DQ = re.compile(r'\b(data[- ]quality|data[- ]integrity|source defects?|(?:data|feed|source) (?:problems?|issues?|errors?|defects?)|'
                 r'quality (?:problems?|issues?)|defects?|bad data|dirty data)\b', re.I)
_SPECIFIC = [  # phrase -> (detectors, tables, phrase tokens it consumes)
    (re.compile(r'\bdischarge[sd]?(?: dates?)?(?: (?:is|was|are|were|comes?|came|falls?))? (?:before|prior to|earlier than) (?:the |their )?(?:admission|admit)(?: dates?)?|discharged before (?:they were |being )?admitted\b', re.I),
     ['date_order', 'source_flag'], ['adt_visit', 'stay', 'payer_claim', 'claim', 'encounter']),
    (re.compile(r'\b(?:future|impossible future) dates?|dates? in the future\b', re.I), ['future_date'], []),
    (re.compile(r'\binvalid dates?|bad dates?|malformed dates?\b', re.I), ['invalid_date'], []),
    (re.compile(r'\bdates? out of order|out[- ]of[- ]order dates?\b', re.I), ['date_order'], []),
    (re.compile(r'\bduplicate (?:claim|business|primary)? ?(?:ids?|keys?|numbers?)\b', re.I), ['duplicate_key'], []),
    (re.compile(r'\borphan(?:ed)? (?:records?|keys?|rows?)|(?:records?|rows?) with no (?:matching )?(?:patient|parent)\b', re.I), ['orphan_key'], []),
    (re.compile(r'\b(?:malformed|invalid|badly formatted) (?:npis?|codes?|icd-?10 codes?|zip codes?|values?)\b', re.I), ['pattern'], []),
    (re.compile(r'\b(?:unit|units) (?:mismatch(?:es)?|problems?|errors?)|(?:mixed|inconsistent|unconverted) units\b', re.I), ['unit_mismatch'], ['measurement']),
    (re.compile(r'\boutliers?|implausible values?\b', re.I), ['outlier', 'out_of_range'], []),
    (re.compile(r'\bfunctional dependenc(?:y|ies)|(?:ids?|encounters?|claims?) (?:mapped|mapping|linked) to (?:two|more than one|multiple) patients?\b', re.I), ['fd_violation'], []),
]


def local_question(question, as_of=None):
    text = ' '.join(str(question).replace('?', ' ').replace(',', ' ').split())
    detectors, tables, rest = [], [], text
    for rx, dets, tbls in _SPECIFIC:
        m = rx.search(rest)
        if m:
            detectors += dets; tables += tbls; rest = rest[:m.start()] + ' ' + rest[m.end():]
    m = _DQ.search(rest)
    if not detectors and not m: return None
    if m: rest = rest[:m.start()] + ' ' + rest[m.end():]
    named = []
    for feed in sorted(FEEDS, key=len, reverse=True):
        rx = re.compile(r'\b' + re.escape(feed) + r'\b', re.I)
        if rx.search(rest):
            named += FEEDS[feed]; rest = rx.sub(' ', rest)
    leftover = [w for w in re.findall(r"[a-z0-9']+", rest.lower()) if w not in _WORDS and w not in ('admission', 'admissions', 'discharge')]
    if leftover: return None  # a qualifier the engine would not apply (a year, a practice, a patient): not claimed
    tables = sorted(set(named) & set(tables)) if named and tables else sorted(set(named or tables))
    from .foundation_engines import graph
    params = {'tables': tables, 'detectors': list(dict.fromkeys(detectors)), 'output': 'findings', 'min_count': 1}
    return graph(question, 'source_defects', params, 'Detected source defects (dq_detectors)')


# ------------------------------------------------------------------------------ answer annotation (answer_checks hook)
def annotate_tables(path, tables, limit=3):
    """Messages naming warning-level defects of the tables an answer read (cache only; never computes)."""
    report = cached(path)
    if not report or not tables: return []
    out = []
    for t in sorted(tables):
        hits = [f for f in report['findings'] if f['table'] == t and f['severity'] == 'warning']
        if hits:
            out.append(f'Source defects in {t} (read by this answer): ' + '; '.join(f['description'] for f in hits[:limit])
                       + (f' (+{len(hits) - limit} more)' if len(hits) > limit else '') + '. The answer counts the data as received.')
    return out


def annotate(stack, question, graph, answer):
    """answer_checks hook (heal, never block): plain messages when the SQL an answer ran reads a table with detected source
    defects (cache from detect()) or a feed period with an unrecovered sequence gap (feed_gaps). Never computes detection."""
    from .answer_checks import _queries, _tables_read
    from . import feed_gaps
    path = getattr(stack, 'path', stack)
    sqls = _queries(graph, answer if isinstance(answer, dict) else {})
    tables = _tables_read(sqls)
    if not tables: return []
    return annotate_tables(path, tables) + feed_gaps.annotate_tables(path, tables, [*sqls, str(question or '')])
