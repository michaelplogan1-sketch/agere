"""Quality measure library engine: evaluates any measure in config/agere/measures/*.json over the semantic layer.

A measure file states its denominator (age/sex, enrollment, qualifying conditions or index events), exclusions,
numerator and look-back windows as criteria over Agere concepts and standard codes (ICD-10-CM, LOINC, CPT/HCPCS,
drug classes from RxNorm-coded medication records), plus citation and spec year. Nothing here is a certified
measure: HEDIS measures are approximations built from NCQA's public summaries (the licensed value sets are not used);
CMS eCQM, PQA and AHRQ PQI measures are approximations of public specifications with simplified code sets.

Three measure units:
  patient        - one row per eligible patient (screenings, control, therapy, adherence).
  index_event    - one row per index event (ED visit, discharge): follow-up, readmission, transitions of care.
  admission_rate - admissions per 100,000 member-years (AHRQ PQI composites, unplanned admissions).

When the received data lacks an input a measure needs (no screening results, no claim lines, no survey), the
engine declines and names the missing input instead of reporting a rate built on silence. Inputs needed only for
exclusions are reported as 'exclusion not applied'.

Window expressions: anchor[+|-N(d|m|y)], anchors period_start, period_end, index (event start), index_end
(discharge). Example: {"start": "period_end-10y", "end": "period_end"}.
"""
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
import calendar, json, re, sqlite3

VERSION = 'agere-measure-engine/1'
SCHEMA = 'agere-measure/1'
LIBRARY_DIR = Path(__file__).resolve().parents[3] / 'config' / 'agere' / 'measures'
UNITS = ('patient', 'index_event', 'admission_rate')
TYPES = ('condition', 'measurement', 'blood_pressure', 'medication', 'service', 'encounter', 'stay', 'pdc')
GROUP_BY = ('none', 'pcp', 'practice', 'coverage_type', 'payer', 'age_band', 'sex', 'race', 'ethnicity', 'race_ethnicity', 'language', 'interpreter')
OUTPUTS = ('rates', 'gaps')
DISPENSE_STATUSES = ('dispensed', 'paid')
INACTIVE_VISIT = ('cancelled', 'canceled', 'entered-in-error', 'entered_in_error', 'no_show', 'noshow')
_EXPR = re.compile(r'^(period_start|period_end|index_end|index)(?:([+-])(\d+)([dmy]))?$')
_CACHE = {}


# ---- library ------------------------------------------------------------------------
def library(directory=None):
    """{measure id: measure} for every agere-measure/1 file (other JSON files, e.g. sets.json, are skipped)."""
    directory = Path(directory or LIBRARY_DIR)
    key = (str(directory), tuple(sorted((p.name, p.stat().st_mtime_ns) for p in directory.glob('*.json'))) if directory.is_dir() else ())
    if key in _CACHE: return _CACHE[key]
    out = {}
    for f in sorted(directory.glob('*.json')) if directory.is_dir() else []:
        m = json.loads(f.read_text())
        if m.get('schema') != SCHEMA: continue
        validate_measure(m, f.name)
        if m['id'] in out: raise ValueError(f'duplicate measure id {m["id"]} in {f.name}')
        out[m['id']] = m
    _CACHE.clear(); _CACHE[key] = out
    return out


def measure_sets(directory=None):
    f = Path(directory or LIBRARY_DIR) / 'sets.json'
    return json.loads(f.read_text()).get('sets', {}) if f.is_file() else {}


def _check_window(w, where):
    if not isinstance(w, dict) or set(w) - {'start', 'end'} or not w.get('start') or not w.get('end'):
        raise ValueError(f'{where}: window needs start and end expressions')
    for k in ('start', 'end'):
        if not _EXPR.match(str(w[k]).replace(' ', '')): raise ValueError(f'{where}: bad window expression {w[k]!r}')


def _check_criterion(c, where):
    if not isinstance(c, dict) or c.get('type') not in TYPES: raise ValueError(f'{where}: criterion type must be one of {TYPES}')
    if not c.get('label'): raise ValueError(f'{where}: every criterion needs a label (used in gap reasons and declines)')
    if c['type'] != 'pdc': _check_window(c.get('window'), where)
    if c['type'] == 'condition' and not (c.get('concepts') or c.get('icd10_prefixes')): raise ValueError(f'{where}: condition needs concepts or icd10_prefixes')
    if c['type'] == 'measurement' and not (c.get('concept') or c.get('loinc')): raise ValueError(f'{where}: measurement needs concept or loinc')
    if c['type'] in ('medication', 'pdc') and not c.get('classes'): raise ValueError(f'{where}: {c["type"]} needs drug classes')
    if c['type'] == 'service' and not (c.get('cpt') or c.get('hcpcs') or c.get('loinc') or c.get('streams') or c.get('visit_concepts')):
        raise ValueError(f'{where}: service needs cpt, hcpcs, loinc, streams or visit_concepts')
    if c['type'] == 'encounter' and not c.get('concepts'): raise ValueError(f'{where}: encounter needs visit concepts')
    v = c.get('value')
    if v is not None and (not isinstance(v, dict) or v.get('op') not in ('>', '>=', '<', '<=') or not isinstance(v.get('threshold'), (int, float))):
        raise ValueError(f'{where}: value rule needs op (> >= < <=) and numeric threshold')


def validate_measure(m, where='measure'):
    for k in ('id', 'name', 'unit', 'status', 'citations', 'spec_year', 'direction', 'denominator', 'numerator'):
        if k not in m: raise ValueError(f'{where}: missing {k}')
    if m['unit'] not in UNITS: raise ValueError(f'{where}: unit must be one of {UNITS}')
    if m['direction'] not in ('higher_is_better', 'lower_is_better'): raise ValueError(f'{where}: direction')
    if m['status'] not in ('approximation', 'internal'): raise ValueError(f'{where}: status approximation|internal (nothing here is certified)')
    if not m['citations'] or not all(c.get('url') and c.get('title') for c in m['citations']): raise ValueError(f'{where}: citations need title and url')
    den = m['denominator']
    for i, c in enumerate(den.get('criteria', [])): _check_criterion(c, f'{where} denominator[{i}]')
    for i, c in enumerate(m.get('exclusions', [])): _check_criterion(c, f'{where} exclusions[{i}]')
    num = m['numerator']
    if m['unit'] == 'admission_rate':
        if not num.get('components'): raise ValueError(f'{where}: admission_rate numerator needs components')
        for comp in num['components']:
            if not comp.get('id') or not comp.get('icd10_prefixes'): raise ValueError(f'{where}: component needs id and icd10_prefixes')
    else:
        if not num.get('criteria'): raise ValueError(f'{where}: numerator needs criteria')
        if num.get('combine', 'all') not in ('all', 'any'): raise ValueError(f'{where}: numerator combine all|any')
        for i, c in enumerate(num['criteria']): _check_criterion(c, f'{where} numerator[{i}]')
    if m['unit'] in ('index_event', 'admission_rate'):
        idx = m.get('index') or {}
        if idx.get('type') not in ('encounter', 'stay'): raise ValueError(f'{where}: index type encounter|stay')
        _check_window(idx.get('window'), f'{where} index')
    return m


def match(text, directory=None):
    """(measure id, alias) named in free text: the longest library alias found, else (None, None)."""
    low = ' ' + re.sub(r'\s+', ' ', (text or '').lower()) + ' '
    best = (None, None)
    for mid, m in library(directory).items():
        for alias in m.get('aliases', []):
            if re.search(r'(?<![a-z0-9])' + re.escape(alias.lower()) + r'(?![a-z0-9])', low) and len(alias) > len(best[1] or ''):
                best = (mid, alias)
    return best


# ---- dates ----------------------------------------------------------------------------
def shift(day, n, unit):
    d = date.fromisoformat(day[:10])
    if unit == 'd': return (d + timedelta(days=n)).isoformat()
    months = n * (12 if unit == 'y' else 1)
    y, mo = divmod(d.month - 1 + months, 12)
    y += d.year
    return date(y, mo + 1, min(d.day, calendar.monthrange(y, mo + 1)[1])).isoformat()


def at(expr, anchors):
    m = _EXPR.match(str(expr).replace(' ', ''))
    base = anchors[m.group(1)]
    if not m.group(2): return base
    return shift(base, int(m.group(3)) * (1 if m.group(2) == '+' else -1), m.group(4))


def default_period(as_of):
    end = as_of[:10]
    return shift(shift(end, -12, 'm'), 1, 'd'), end


# ---- evidence ---------------------------------------------------------------------------
class Context:
    def __init__(self, db):
        self.db = db
        self._cols = {}
        self._cache = {}

    def columns(self, table):
        if table not in self._cols:
            self._cols[table] = {r[1] for r in self.db.execute(f'PRAGMA table_info({table})')}
        return self._cols[table]

    def has(self, table, column=None):
        cols = self.columns(table)
        return bool(cols) and (column is None or column in cols)


def _icd_clause(column, prefixes):
    return '(' + ' OR '.join(f"replace({column},'.','') LIKE ?" for _ in prefixes) + ')', [p.replace('.', '') + '%' for p in prefixes]


def _rows(ctx, c):
    """All evidence rows for one criterion: {patient_id: [(date or None, value, detail)]} (no window applied).
    Returns (rows, missing) where missing names inputs the warehouse lacks entirely."""
    key = json.dumps({k: v for k, v in c.items() if k not in ('window', 'label', 'value', 'min_dates', 'note')}, sort_keys=True)
    if key in ctx._cache: return ctx._cache[key]
    db, out, missing, t = ctx.db, defaultdict(list), [], c['type']
    if t == 'condition':
        if c.get('concepts'):
            q = ','.join('?' * len(c['concepts']))
            for pid, d, concept in db.execute(f'SELECT patient_id, coalesce(event_date, onset, recorded_date), concept FROM condition WHERE concept IN ({q})', c['concepts']):
                out[pid].append((d[:10] if d else None, None, concept))
        if c.get('icd10_prefixes'):
            found = False
            if ctx.has('condition', 'icd10'):
                clause, args = _icd_clause('icd10', c['icd10_prefixes'])
                for pid, d, code in db.execute(f'SELECT patient_id, coalesce(event_date, onset, recorded_date), icd10 FROM condition WHERE {clause}', args):
                    out[pid].append((d[:10] if d else None, None, code)); found = True
            if ctx.has('encounter_diagnosis'):
                clause, args = _icd_clause('d.icd10', c['icd10_prefixes'])
                for pid, d, code in db.execute(f'SELECT d.patient_id, v.event_date, d.icd10 FROM encounter_diagnosis d JOIN visit v ON v.encounter_id = d.encounter_id '
                                               f'WHERE {clause}', args):
                    out[pid].append((d[:10] if d else None, None, code)); found = True
            if not found and not c.get('concepts') and not ctx.has('condition', 'icd10'):
                missing.append(f"{c['label']}: ICD-10-CM diagnosis codes (not in this release)")
    elif t == 'measurement':
        terms, args = [], []
        if c.get('concept'): terms.append('concept = ?'); args.append(c['concept'])
        if c.get('loinc'):
            if ctx.has('measurement', 'loinc'):
                terms.append('loinc IN (' + ','.join('?' * len(c['loinc'])) + ')'); args += c['loinc']
            elif not c.get('concept'):
                missing.append(f"{c['label']}: LOINC-coded results (not in this release)")
        if terms:
            for pid, d, v, raw in db.execute(f"SELECT patient_id, event_date, value_canonical, value FROM measurement WHERE ({' OR '.join(terms)}) "
                                             "AND (value_canonical IS NOT NULL OR value IS NOT NULL)", args):
                out[pid].append((d[:10] if d else None, v if v is not None else raw, None))
    elif t == 'blood_pressure':
        for pid, concept, d, v in db.execute("SELECT patient_id, concept, event_date, value_canonical FROM measurement "
                                             "WHERE concept IN ('systolic_bp','diastolic_bp') AND value_canonical IS NOT NULL AND event_date IS NOT NULL"):
            out[pid].append((d[:10], v, concept))
    elif t in ('medication', 'pdc'):
        evidence = c.get('evidence') or (['dispensed', 'claim'] if t == 'pdc' else ['dispensed', 'claim', 'administered'])
        statuses = c.get('statuses', list(DISPENSE_STATUSES) + ['completed', 'given', 'administered'])
        q1, q2 = ','.join('?' * len(c['classes'])), ','.join('?' * len(evidence))
        sql = (f'SELECT m.patient_id, m.event_date, m.drug_class, m.status, m.drug_name, json_extract(r.payload, "$.days_supply_reported"), '
               f'json_extract(r.payload, "$.days_supply") FROM medication m LEFT JOIN record r ON r.record_id = m.record_id '
               f'WHERE m.drug_class IN ({q1}) AND m.evidence IN ({q2})')
        exclude = re.compile(c['exclude_names'], re.I) if c.get('exclude_names') else None
        for pid, d, cls, status, name, ds1, ds2 in db.execute(sql, [*c['classes'], *evidence]):
            if statuses and status not in statuses: continue
            if exclude and exclude.search(name or ''): continue
            out[pid].append((d[:10] if d else None, ds1 or ds2, cls))
    elif t == 'service':
        any_source = False
        for system in ('cpt', 'hcpcs'):
            codes = c.get(system)
            if not codes: continue
            if ctx.has('claim_line', system):
                any_source = True
                q = ','.join('?' * len(codes))
                for pid, d, code in db.execute(f'SELECT patient_id, service_date, {system} FROM claim_line WHERE {system} IN ({q})', codes):
                    out[pid].append((d[:10] if d else None, None, f'{system.upper()} {code}'))
            else:
                missing.append(f"{c['label']}: {system.upper()} claim lines (not in this release)")
        if c.get('loinc') and ctx.has('measurement', 'loinc'):
            any_source = True
            q = ','.join('?' * len(c['loinc']))
            for pid, d, code in db.execute(f'SELECT patient_id, event_date, loinc FROM measurement WHERE loinc IN ({q})', c['loinc']):
                out[pid].append((d[:10] if d else None, None, f'LOINC {code}'))
        for s in c.get('streams', []):
            any_source = True
            if s.get('values'):
                q = ','.join('?' * len(s['values']))
                sql, args = f"SELECT patient_id, event_date, json_extract(payload, '$.{s['field']}') FROM record WHERE stream = ? AND json_extract(payload, '$.{s['field']}') IN ({q})", [s['stream'], *s['values']]
            elif s.get('contains'):
                sql, args = f"SELECT patient_id, event_date, ? FROM record WHERE stream = ? AND instr(lower(json_extract(payload, '$.{s['field']}')), ?) > 0", [s['contains'], s['stream'], s['contains'].lower()]
            elif s.get('field'):
                sql, args = f"SELECT patient_id, event_date, json_extract(payload, '$.{s['field']}') FROM record WHERE stream = ? AND json_extract(payload, '$.{s['field']}') IS NOT NULL", [s['stream']]
            else:
                sql, args = 'SELECT patient_id, event_date, stream FROM record WHERE stream = ?', [s['stream']]
            for pid, d, detail in db.execute(sql, args):
                out[pid].append((d[:10] if d else None, detail, f"{s['stream']}"))
        if c.get('visit_concepts'):
            any_source = True
            q = ','.join('?' * len(c['visit_concepts']))
            for pid, d, concept in db.execute(f'SELECT patient_id, event_date, type_concept FROM visit WHERE type_concept IN ({q})', c['visit_concepts']):
                out[pid].append((d[:10] if d else None, None, concept))
        if not any_source and not missing: missing.append(f"{c['label']}: no source configured")
    elif t == 'encounter':
        q = ','.join('?' * len(c['concepts']))
        for pid, d, concept, status in db.execute(f'SELECT patient_id, event_date, type_concept, status FROM visit WHERE type_concept IN ({q})', c['concepts']):
            if (status or '').lower() in INACTIVE_VISIT: continue
            out[pid].append((d[:10] if d else None, None, concept))
    elif t == 'stay':
        types = c.get('stay_types')
        for pid, a, dis, typ in db.execute('SELECT patient_id, admit_date, discharge_date, type FROM stay'):
            if types and typ not in types: continue
            out[pid].append((a[:10] if a else None, dis[:10] if dis else None, typ))
    for v in out.values(): v.sort(key=lambda x: x[0] or '')
    if not out and not missing:
        missing.append(f"{c['label']}: no such records in the received data")
    ctx._cache[key] = (dict(out), missing)
    return ctx._cache[key]


def _in(d, lo, hi): return d is not None and lo <= d <= hi


def _compare(v, rule):
    if v is None: return False
    v = float(v); t = rule['threshold']
    return {'>': v > t, '>=': v >= t, '<': v < t, '<=': v <= t}[rule['op']]


def _evaluate(ctx, c, pid, anchors):
    """(met, evidence text) for one criterion and patient within its window."""
    rows, _ = _rows(ctx, c)
    if c['type'] == 'pdc': return _pdc(rows.get(pid, []), c, anchors)
    lo, hi = at(c['window']['start'], anchors), at(c['window']['end'], anchors)
    got = rows.get(pid, [])
    if c['type'] == 'condition' and c.get('groups'):
        # Distinct condition groups (e.g. 'two or more high-risk chronic conditions'): each group = concepts and/or ICD-10 prefixes.
        usable = [r for r in got if _in(r[0], lo, hi) or (r[0] is None and c.get('undated', 'include') == 'include')]
        hit = sorted(name for name, g in c['groups'].items()
                     if any(r[2] in g.get('concepts', ()) or any(str(r[2]).replace('.', '').startswith(p.replace('.', '')) for p in g.get('icd10_prefixes', ()))
                            for r in usable))
        need = c.get('min_groups', 1)
        return len(hit) >= need, f"{c['label']}: {len(hit)} of {need} ({', '.join(hit) or 'none'})"
    if c['type'] == 'condition':
        dated = [r for r in got if _in(r[0], lo, hi)]
        if dated:
            dates = {r[0] for r in dated}
            ok = len(dates) >= c.get('min_dates', 1)
            return ok, f"{c['label']}: {len(dates)} date(s), latest {max(dates)}"
        if c.get('undated', 'include') == 'include' and any(r[0] is None for r in got):
            return True, f"{c['label']}: undated record"
        return False, f"{c['label']}: none {lo}..{hi}"
    if c['type'] == 'stay':
        inside = [r for r in got if _in(r[0], lo, hi)]
        return (bool(inside), f"{c['label']}: admitted {inside[0][0]}" if inside else f"{c['label']}: none {lo}..{hi}")
    inside = [r for r in got if _in(r[0], lo, hi)]
    if c.get('after_index_only') and anchors.get('index_end'):
        inside = [r for r in inside if r[0] > anchors['index_end']]
    if c['type'] == 'blood_pressure':
        by_day = defaultdict(dict)
        for d, v, concept in inside:
            cur = by_day[d].get(concept)
            by_day[d][concept] = v if cur is None else min(cur, v)  # lowest reading of the representative day
        days = sorted(d for d, x in by_day.items() if 'systolic_bp' in x and 'diastolic_bp' in x)
        if not days: return False, f"{c['label']}: no BP {lo}..{hi}"
        sbp, dbp = by_day[days[-1]]['systolic_bp'], by_day[days[-1]]['diastolic_bp']
        ok = sbp < c.get('systolic_below', 140) and dbp < c.get('diastolic_below', 90)
        return ok, f"BP {sbp:.0f}/{dbp:.0f} on {days[-1]}"
    if c['type'] == 'measurement' and c.get('value'):
        if not inside:
            return bool(c['value'].get('missing_counts_as_met', False)), f"{c['label']}: no result {lo}..{hi}"
        day = inside[-1][0]
        values = [float(r[1]) for r in inside if r[0] == day and _num(r[1])]
        if not values: return bool(c['value'].get('missing_counts_as_met', False)), f"{c['label']}: non-numeric result on {day}"
        v = min(values)
        return _compare(v, c['value']), f"{c['label']}: {v:g} on {day}"
    dates = sorted({r[0] for r in inside})
    if len(dates) < c.get('min_dates', 1): return False, f"{c['label']}: {len(dates)} of {c.get('min_dates', 1)} dates {lo}..{hi}"
    detail = next((str(r[2]) for r in reversed(inside) if r[2]), '')
    return True, f"{c['label']}: {dates[-1]}" + (f' ({detail})' if detail else '')


def _num(v):
    try: float(v); return True
    except (TypeError, ValueError): return False


def _pdc(rows, c, anchors):
    """Proportion of days covered (PQA): from the first fill in the period to period end; overlapping supply shifts forward.
    Eligible only with >= min_fills fills on distinct dates. Per class group (any listed class counts)."""
    lo, hi = anchors['period_start'], anchors['period_end']
    fills = sorted({(d, int(float(s)) if _num(s) else c.get('default_days_supply', 30)) for d, s, _ in rows if _in(d, lo, hi)})
    if len({d for d, _ in fills}) < c.get('min_fills', 2):
        return None, f"{c['label']}: fewer than {c.get('min_fills', 2)} fills"
    start = date.fromisoformat(fills[0][0]); end = date.fromisoformat(hi)
    covered, cursor = set(), start
    for d, days in fills:
        cursor = max(cursor, date.fromisoformat(d))
        for i in range(days):
            day = cursor + timedelta(days=i)
            if day > end: break
            covered.add(day)
        cursor = cursor + timedelta(days=days)
    span = (end - start).days + 1
    if span < c.get('min_treatment_days', 1):
        return None, f"{c['label']}: treatment period {span} days (< {c['min_treatment_days']})"
    pdc = len(covered) / span if span > 0 else 0.0
    return pdc >= c.get('threshold', 0.8), f"{c['label']}: PDC {pdc:.2f} over {span} days from {fills[0][0]} ({len(fills)} fills)"


# ---- availability (declines) -----------------------------------------------------------
def availability(ctx, m, start, end):
    """(missing_inputs that force a decline, warnings). Numerator/denominator inputs absent -> decline;
    exclusion inputs absent -> warning; evidence that starts after the look-back begins -> warning."""
    anchors = {'period_start': start, 'period_end': end, 'index': start, 'index_end': start}
    missing, warnings = [], []

    def check(c):
        rows, miss = _rows(ctx, c)
        if not rows: return miss or [f"{c['label']}: no such records in the received data"], None
        if c['type'] in ('pdc',) or not c.get('window'): return [], None
        first = min((r[0] for v in rows.values() for r in v if r[0]), default=None)
        lo = at(c['window']['start'], anchors)
        if 'index' not in c['window']['start'] + c['window']['end'] and c['type'] != 'condition':
            hi = at(c['window']['end'], anchors)
            if not any(_in(r[0], lo, hi) for v in rows.values() for r in v):
                return [f"{c['label']}: no records dated {lo} to {hi} in the received data"], None
        if first and first > lo and c['type'] in ('service', 'measurement') and c.get('lookback_check', True):
            return [], f"{c['label']}: earliest received evidence is {first}; the look-back starts {lo}, so earlier care is invisible"
        return [], None

    for c in m['denominator'].get('criteria', []):
        miss, warn = check(c)
        missing += miss
        if warn: warnings.append(warn)
    if m['unit'] != 'admission_rate':
        num = m['numerator']
        results = [check(c) for c in num['criteria']]
        if num.get('combine', 'all') == 'any':
            if all(r[0] for r in results): missing += [x for r in results for x in r[0]]
            else: warnings += [f'numerator source absent (other routes still count): {x}' for r in results for x in r[0]]
        else:
            missing += [x for r in results for x in r[0]]
        warnings += [r[1] for r in results if r[1]]
    for c in m.get('exclusions', []):
        miss, _ = check(c)
        warnings += [f'exclusion not applied: {x}' for x in miss]
    if m['unit'] in ('index_event', 'admission_rate'):
        rows, miss = _rows(ctx, m['index'])
        if not rows: missing += miss
        if m['unit'] == 'admission_rate' and not ctx.has('visit', 'principal_icd10'):
            missing.append('principal ICD-10 diagnosis on admissions (not in this release)')
    for need in m.get('requires_inputs', []):  # inputs with no warehouse source at all (e.g. survey results)
        missing.append(need)
    return missing, warnings


# ---- people ----------------------------------------------------------------------------
def _age_at(person, day, as_of):
    born = person.get('birth_date')
    if born:
        b, d = date.fromisoformat(born[:10]), date.fromisoformat(day[:10])
        return d.year - b.year - ((d.month, d.day) < (b.month, b.day))
    if person.get('age') is None: return None
    return person['age'] - (int(as_of[:4]) - int(day[:4]))


def _people(db, as_of, start, end, ctx):
    from . import vbc
    people = vbc.eligibility(db, as_of, start, end)
    if ctx.has('patient', 'birth_date'):
        for pid, born in db.execute('SELECT patient_id, birth_date FROM patient'):
            if pid in people: people[pid]['birth_date'] = born
    return people


def _group(group_by, pid, people, attr):
    from . import vbc
    return vbc._group_key('coverage_type' if group_by == 'payer' else group_by, pid, people, attr)


def _enrolled(info, start, end, allowed_gap_months):
    from . import vbc
    window = vbc._months(start, end)
    months = set(info.get('months') or [])
    return bool(months) and end[:7] in months and len(window) - len(months & set(window)) <= allowed_gap_months


# ---- evaluation ------------------------------------------------------------------------
def evaluate_db(db, m, start, end, as_of, group_by='none', keep=None, people=None, attr=None, output='rates', top=500):
    """Evaluate one measure dict on an open semantic-layer connection."""
    from . import vbc
    vbc.require_demographics(db, group_by)  # health-equity stratification only where race/language are received
    ctx = Context(db)
    missing, warnings = availability(ctx, m, start, end)
    base = {'schema': VERSION, 'measure': m['id'], 'name': m['name'], 'status_of_definition': m['status'],
            'certification': m.get('certification', ''), 'spec_year': m['spec_year'], 'steward': m.get('steward'),
            'citations': m['citations'], 'direction': m['direction'], 'unit': m['unit'], 'period': {'start': start, 'end': end},
            'group_by': group_by, 'warnings': warnings}
    if missing:
        return {**base, 'declined': True, 'missing_inputs': [f'Missing input: {x}' for x in missing], 'rows': [], 'care_gaps': [],
                'reading': f"{m['name']} cannot be computed from the received data: " + '; '.join(missing) + '.',
                'method': 'Declined: a denominator or numerator input is absent from the received data, so a rate would count silence as care.'}
    if people is None:
        people = _people(db, as_of, start, end, ctx)
    elif ctx.has('patient', 'birth_date') and not any('birth_date' in v for v in people.values()):
        born = dict(db.execute('SELECT patient_id, birth_date FROM patient'))
        for pid, info in people.items(): info['birth_date'] = born.get(pid)
    if keep is not None: people = {k: v for k, v in people.items() if k in keep}
    if attr is None: attr = vbc.attribution(db, people, start, end)
    den = m['denominator']
    age = den.get('age') or {}
    sex = den.get('sex')
    gap_months = (den.get('enrollment') or {}).get('allowed_gap_months', 1)
    lower = m['direction'] == 'lower_is_better'
    anchors0 = {'period_start': start, 'period_end': end}
    groups = defaultdict(lambda: {'eligible': 0, 'excluded': 0, 'denominator': 0, 'numerator': 0})
    gaps = []

    def demographic(pid, info, day):
        if sex and (info.get('sex') or '')[:1].upper() != sex[:1].upper(): return False
        a = _age_at(info, day, as_of)
        if age and (a is None or a < age.get('min', 0) or a > age.get('max', 200)): return False
        return True

    if m['unit'] == 'patient':
        for pid, info in people.items():
            if not _enrolled(info, start, end, gap_months): continue
            if not demographic(pid, info, at(age.get('as_of', 'period_end'), anchors0)): continue
            reasons = [_evaluate(ctx, c, pid, anchors0) for c in den.get('criteria', [])]
            if not all(ok for ok, _ in reasons): continue
            num_results = [_evaluate(ctx, c, pid, anchors0) for c in m['numerator']['criteria']]
            if any(ok is None for ok, _ in num_results): continue  # PDC: not enough fills to be in the denominator
            g = groups[_group(group_by, pid, people, attr)]
            g['eligible'] += 1
            excl = next((t for ok, t in (_evaluate(ctx, c, pid, anchors0) for c in m.get('exclusions', [])) if ok), None)
            if excl: g['excluded'] += 1; continue
            g['denominator'] += 1
            combine = any if m['numerator'].get('combine', 'all') == 'any' else all
            met = combine(ok for ok, _ in num_results)
            g['numerator'] += bool(met)
            if bool(met) == lower:  # gap: lower-is-better measures flag numerator hits, others flag misses
                gaps.append({'patient_id': pid, 'group': _group(group_by, pid, people, attr), 'practice': attr.get(pid, {}).get('practice'),
                             'pcp': attr.get(pid, {}).get('pcp'), 'measure': m['id'],
                             'gap': m.get('gap_label', m['name']), 'evidence': '; '.join(t for _, t in num_results)})
    elif m['unit'] == 'index_event':
        idx = m['index']
        rows, _ = _rows(ctx, idx)
        lo, hi = at(idx['window']['start'], anchors0), at(idx['window']['end'], anchors0)
        use_end = idx.get('index_date') == 'discharge'
        for pid, events in rows.items():
            info = people.get(pid)
            if info is None or not info.get('months'): continue
            last_counted = None
            for e in events:
                admit, dis = e[0], (e[1] if idx['type'] == 'stay' else None)
                if idx['type'] == 'stay' and use_end and not dis: continue  # discharge unknown: cannot place the window
                day = dis if use_end else admit
                if not _in(day, lo, hi): continue
                if day[:7] not in set(info['months']): continue
                if last_counted and idx.get('one_per_days') and day <= shift(last_counted, idx['one_per_days'], 'd'): continue
                if not demographic(pid, info, day): continue
                anchors = {**anchors0, 'index': admit, 'index_end': dis or admit}
                if not all(ok for ok, _ in (_evaluate(ctx, c, pid, anchors) for c in den.get('criteria', []))): continue
                last_counted = day
                g = groups[_group(group_by, pid, people, attr)]
                g['eligible'] += 1
                excl = next((t for ok, t in (_evaluate(ctx, c, pid, anchors) for c in m.get('exclusions', [])) if ok), None)
                if excl: g['excluded'] += 1; continue
                g['denominator'] += 1
                num_results = [_evaluate(ctx, c, pid, anchors) for c in m['numerator']['criteria']]
                combine = any if m['numerator'].get('combine', 'all') == 'any' else all
                met = combine(ok for ok, _ in num_results)
                g['numerator'] += bool(met)
                if bool(met) == lower:
                    gaps.append({'patient_id': pid, 'index_date': day, 'group': _group(group_by, pid, people, attr),
                                 'practice': attr.get(pid, {}).get('practice'), 'pcp': attr.get(pid, {}).get('pcp'), 'measure': m['id'],
                                 'gap': m.get('gap_label', m['name']), 'evidence': '; '.join(t for _, t in num_results)})
    else:  # admission_rate
        idx = m['index']
        per = m['numerator'].get('per', 100000)
        exposure = defaultdict(float)
        period_months = set(vbc._months(start, end))
        for pid, info in people.items():
            if not info.get('months'): continue
            if sex and (info.get('sex') or '')[:1].upper() != sex[:1].upper(): continue
            a = _age_at(info, end, as_of)
            if age and (a is None or a < age.get('min', 0) or a > age.get('max', 200)): continue
            if not all(ok for ok, _ in (_evaluate(ctx, c, pid, anchors0) for c in den.get('criteria', []))): continue
            exposure[_group(group_by, pid, people, attr)] += len(set(info['months']) & period_months) / 12
        events = _principal_admissions(ctx, idx, start, end)
        counts = defaultdict(lambda: defaultdict(int))
        for pid, day, code in events:
            info = people.get(pid)
            if info is None or not info.get('months') or day[:7] not in set(info['months']): continue
            a = _age_at(info, day, as_of)
            if sex and (info.get('sex') or '')[:1].upper() != sex[:1].upper(): continue
            if age and (a is None or a < age.get('min', 0) or a > age.get('max', 200)): continue
            if any(code.replace('.', '').startswith(p.replace('.', '')) for p in m['numerator'].get('exclude_icd10_prefixes', [])): continue
            comp = next((cp for cp in m['numerator']['components']
                         if any(code.replace('.', '').startswith(p.replace('.', '')) for p in cp['icd10_prefixes'])
                         and (a is None or cp.get('age_min', 0) <= a <= cp.get('age_max', 200))), None)
            if comp is None: continue
            g = _group(group_by, pid, people, attr)
            counts[g]['admissions'] += 1; counts[g][comp['id']] += 1
            gaps.append({'patient_id': pid, 'index_date': day, 'group': g, 'practice': attr.get(pid, {}).get('practice'),
                         'pcp': attr.get(pid, {}).get('pcp'), 'measure': m['id'], 'gap': f"{comp['label']} admission ({code})",
                         'evidence': f"principal diagnosis {code} on {day}"})
        rows = []
        for gk in sorted(set(exposure) | set(counts), key=str):
            my, n = exposure.get(gk, 0.0), counts[gk]['admissions'] if gk in counts else 0
            rows.append({'measure': m['id'], 'definition': m['name'], 'group': gk, 'member_years': round(my, 2), 'admissions': n,
                         f'rate_per_{per}': round(per * n / my, 1) if my else None,
                         'components': {k: v for k, v in counts[gk].items() if k != 'admissions'} if gk in counts else {}})
        total_my, total_n = sum(exposure.values()), sum(c['admissions'] for c in counts.values())
        overall = {'member_years': round(total_my, 2), 'admissions': total_n, f'rate_per_{per}': round(per * total_n / total_my, 1) if total_my else None}
        return _finish(base, m, rows, overall, gaps, output, top, start, end)
    rows = []
    for gk, v in sorted(groups.items(), key=lambda kv: str(kv[0])):
        rows.append({'measure': m['id'], 'definition': m['name'], 'group': gk, **v,
                     'rate': round(v['numerator'] / v['denominator'], 4) if v['denominator'] else None})
    if len(rows) > 1:
        vbc._beta_binomial(rows, 'numerator', 'denominator')
        for r in rows:
            if 'shrunk_rate' in r: r['shrunk_rate'] = round(r['shrunk_rate'], 4); r['rate_ci95'] = [round(x, 4) for x in r['rate_ci95']]
    tot = {k: sum(r[k] for r in rows) for k in ('eligible', 'excluded', 'denominator', 'numerator')}
    tot['rate'] = round(tot['numerator'] / tot['denominator'], 4) if tot['denominator'] else None
    return _finish(base, m, rows, tot, gaps, output, top, start, end)


def _principal_admissions(ctx, idx, start, end):
    """(patient, admit date, principal ICD-10) for acute admissions in the period: visit principal diagnosis, else the
    claim's principal diagnosis on the admission date."""
    out = []
    concepts = idx.get('concepts') or ['inpatient_admission']
    q = ','.join('?' * len(concepts))
    if ctx.has('visit', 'principal_icd10'):
        for pid, d, code in ctx.db.execute(f'SELECT patient_id, event_date, principal_icd10 FROM visit WHERE type_concept IN ({q}) '
                                           'AND event_date BETWEEN ? AND ? AND principal_icd10 IS NOT NULL', [*concepts, start, end + 'T99']):
            out.append((pid, d[:10], code))
    return sorted(set(out))


def _finish(base, m, rows, overall, gaps, output, top, start, end):
    period = f'{start} to {end}'
    if m['unit'] == 'admission_rate':
        per = m['numerator'].get('per', 100000)
        r = overall.get(f'rate_per_{per}')
        reading = (f"{m['name']}: {overall['admissions']} admissions over {overall['member_years']} member-years = "
                   f"{r} per {per:,} ({period}).") if r is not None else f"{m['name']}: no eligible member-years in {period}."
    else:
        rate = overall.get('rate')
        reading = (f"{m['name']}: {overall['numerator']} of {overall['denominator']} = {rate:.1%} ({period}; "
                   f"{overall['excluded']} excluded; {'lower' if m['direction'] == 'lower_is_better' else 'higher'} is better).") if rate is not None \
            else f"{m['name']}: nobody met the denominator in {period}."
    method = (f"{m.get('certification') or m['status']}. Spec year {m['spec_year']}. Denominator: {m['denominator'].get('summary', '')} "
              f"Numerator: {m['numerator'].get('summary', '')} Exclusions applied: {', '.join(c['label'] for c in m.get('exclusions', [])) or 'none'}. "
              f"Eligibility: enrolled at period end with at most {(m['denominator'].get('enrollment') or {}).get('allowed_gap_months', 1)} "
              "unenrolled month(s); age from birth date when received. Group rates shrunk with beta-binomial when grouped.")
    out = {**base, 'declined': False, 'overall': overall, 'reading': reading, 'method': method, 'gap_count': len(gaps),
           'care_gaps': sorted(gaps, key=lambda g: (str(g['group']), g['patient_id']))[:top], 'rows': rows}
    if output == 'gaps':
        out['rows'] = out['care_gaps']
    return out


# ---- entry points -----------------------------------------------------------------------
def _connect(path):
    from . import semantic_layer
    return sqlite3.connect(f'file:{semantic_layer.ensure(path)}?mode=ro', uri=True)


def validate(p):
    lib = library()
    ids = p.get('measures') or ([p['measure']] if p.get('measure') else [])
    if not ids: raise ValueError('measure: one of ' + ', '.join(sorted(lib)))
    sets = measure_sets()
    expanded = [x for i in ids for x in (sets[i]['measures'] if i in sets else [i])]
    unknown = [i for i in expanded if i not in lib]
    if unknown: raise ValueError('unknown measure(s): ' + ', '.join(unknown))
    if p.get('group_by', 'none') not in GROUP_BY: raise ValueError(f'group_by: {GROUP_BY}')
    if p.get('output', 'rates') not in OUTPUTS: raise ValueError(f'output: {OUTPUTS}')
    return {'measures': expanded, 'group_by': p.get('group_by', 'none'), 'period': p.get('period') or {}, 'output': p.get('output', 'rates'),
            'top': int(p.get('top', 500))}


def evaluate(path, measure, period=None, group_by='none', patient_ids=None, as_of=None, output='rates', top=500):
    """Evaluate one library measure (id) on a product build. period = {'start','end'}; default = 12 months to as_of."""
    m = library()[measure] if isinstance(measure, str) else measure
    db = _connect(path)
    try:
        as_of = as_of or dict(db.execute('SELECT key, value FROM meta')).get('as_of')
        start, end = (period or {}).get('start'), (period or {}).get('end')
        if not start or not end:
            s0, e0 = default_period(end or as_of)
            start, end = start or s0, end or e0
        return evaluate_db(db, m, start[:10], end[:10], as_of, group_by, set(patient_ids) if patient_ids is not None else None,
                           output=output, top=top)
    finally:
        db.close()


def vbc_quality(db, people, attr, p, start, end, as_of):
    """Rows for the vbc 'quality' metric when a library measure or set is named. Returns (rows, method, extra)."""
    ids = p['measure_ids']
    reports = [evaluate_db(db, library()[i], start, end, as_of, p['group_by'], None, people, attr, p.get('output', 'rates')) for i in ids]
    rows = [r for rep in reports for r in rep['rows']]
    if len(reports) == 1:
        rep = reports[0]
        extra = {k: rep[k] for k in ('declined', 'missing_inputs', 'warnings', 'reading', 'care_gaps', 'gap_count', 'overall', 'citations',
                                     'certification', 'spec_year') if k in rep}
        return rows, rep['method'], extra
    summary = [{'measure': rep['measure'], 'name': rep['name'], 'declined': rep['declined'], 'reading': rep['reading'],
                **({'missing_inputs': rep['missing_inputs']} if rep['declined'] else {'overall': rep['overall']})} for rep in reports]
    if p.get('output') != 'gaps' and p['group_by'] == 'none':
        rows = [{'measure': s['measure'], 'definition': s['name'], 'group': 'all', **(s.get('overall') or {}),
                 'status': 'declined' if s['declined'] else 'computed', **({'missing_inputs': s['missing_inputs']} if s['declined'] else {})}
                for s in summary]
    return rows, 'Measure library: each row follows its own measure file (definition, citation, spec year).', {
        'measure_reports': summary, 'warnings': [w for rep in reports for w in rep['warnings']],
        'care_gaps': [g for rep in reports for g in rep['care_gaps']][:500]}


def care_gaps(db, measure_ids, as_of, keep=None, period=None):
    """{patient_id: [(measure id, gap label, evidence)]} for outreach; declined measures contribute nothing (listed in skipped)."""
    start, end = period or default_period(as_of)
    out, skipped = defaultdict(list), []
    for mid in measure_ids:
        rep = evaluate_db(db, library()[mid], start, end, as_of, 'none', keep, top=10 ** 6)
        if rep['declined']: skipped.append({'measure': mid, 'missing_inputs': rep['missing_inputs']}); continue
        for g in rep['care_gaps']:
            out[g['patient_id']].append((mid, g['gap'], g['evidence']))
    return dict(out), skipped
