"""Derived-value discovery index: find values that are computed from received fields, not only the fields themselves.

Questions often ask for a value nobody received as a field: "average days from booking to visit", "days since the last A1c",
"how many ED visits per patient", "change in LDL from first to latest". The semantic layer already stores many of these
(stay.los_days, appointment.lead_time_days, claim.net_payer_paid_cents) and every table supports more through a few generic
derivations. This module indexes both, with the executable recipe, so a question can be matched to a derivation instead of
failing as an unknown column (AGERE_CHIEF_STAFF_2026-10-20: the generic derived-value index remained incomplete).

  stored        a column of the semantic layer that is not a received payload field (the layer computed it): numeric, flag or
                date columns, described by the layer's own table documentation (semantic_docs.schema_doc)
  days_between  two date columns of one table that are ordered in the data (a <= b in >= 95% of rows, 0..3650 days apart)
  days_since    days from a table's latest dated row per patient to the as-of date
  count         records per patient in a table
  concept       per measurement concept: latest value, first value, change (latest - first), mean
  age           patient age at the as-of date from the received birth date
  ratio         one money (cents) column over another of the same table (sum / sum) per patient
  governed      a governed metric or measure-library entry that already defines the value: discovery returns it first and the
                derived-value engine defers to it (the governed definition wins)

build(stack) writes <stack>/derived-values.sqlite3 once per source digest: every entry carries its SQL (patient_id, value),
the inputs it reads, the derivation in words and the support measured on a bounded sample (rows, patients, min, max, mean); an
entry that returns no value is kept with support 0 and never offered. discover(stack, question) ranks entries by lexical match
(FTS5 over name, aliases and the layer's own description) and states how much of the entry's name the question covers.
Operation 'derived_value' (engine_suite): aggregate (mean / median / min / max / sum / count / share over a threshold /
distribution) of one discovered value over the population or a cohort. Synthetic data only; no model calls.
"""
from collections import Counter
import json
import math
from pathlib import Path
import re
import sqlite3
import statistics
import time

VERSION = 'derived-value-index/1'
FILE = 'derived-values.sqlite3'
SAMPLE_ROWS = 30000
SKIP_TABLES = {'record', 'meta', 'link', 'current_record', 'concept_patient', 'drug_class_vocabulary'}
ABBREV = {'los': 'length of stay', 'dx': 'diagnosis', 'rx': 'prescription', 'ed': 'emergency department', 'ip': 'inpatient', 'bp': 'blood pressure',
          'dob': 'birth date', 'pcp': 'primary care physician', 'npi': 'provider', 'sdoh': 'social needs', 'svi': 'social vulnerability index',
          'hba1c': 'a1c', 'egfr': 'kidney function', 'ldl': 'cholesterol', 'awv': 'annual wellness visit', 'adt': 'admission discharge transfer',
          'cents': 'dollars', 'pmpm': 'per member per month', 'mrn': 'medical record number'}
STOP = set('the a an of for to in on at by and or is are was were be been what which who how many much average mean median total number count '
           'patients patient members member per each every all any with without that this those these from over under than then days day have has had '
           'do does did our we us show list give tell me find'.split())


STACKS = {}   # source digest -> build folder, registered by source_profile.load so a text-only route can find the index


def attach(path, digest):
    STACKS[str(digest)] = str(path)


# ---- helpers --------------------------------------------------------------------------------------------------------
def _ro(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, check_same_thread=False)


def _semantic(stack):
    p = Path(stack) / 'semantic.sqlite3'
    if p.exists(): return p
    from . import semantic_layer
    return semantic_layer.ensure(stack)


def _digest(stack):
    run = Path(stack) / 'run.json'
    try: return json.loads(run.read_text()).get('source_digest')
    except (OSError, ValueError): return None


def tokens(text):
    return [t for t in re.findall(r'[a-z0-9]+', str(text).lower().replace('_', ' ')) if t]


def humanize(name):
    words = []
    for t in tokens(name):
        words.append(ABBREV.get(t, t))
    return ' '.join(words)


def _kind(values, declared):
    vals = [v for v in values if v is not None and v != '']
    if not vals: return None
    if all(isinstance(v, (int, float)) for v in vals):
        return 'flag' if set(vals) <= {0, 1} and len(set(vals)) <= 2 else 'number'
    if all(isinstance(v, str) and re.fullmatch(r'\d{4}-\d{2}(-\d{2})?([T ].*)?', v) for v in vals): return 'date' if all(len(v) >= 10 for v in vals) else None
    return None


def _docs():
    """{table: doc text} from the layer's own documentation."""
    try:
        from . import semantic_docs
        return semantic_docs.schema_doc()
    except Exception:  # noqa: BLE001 - descriptions are an aid, never a reason to fail the index
        return {}


def _sentence(doc, column):
    for s in re.split(r'(?<=[.;])\s+', doc or ''):
        if re.search(r'\b' + re.escape(column) + r'\b', s): return s.strip()[:300]
    return ''


def _payload_keys(db):
    keys = set()
    for (stream,) in db.execute('SELECT DISTINCT stream FROM record'):
        for (p,) in db.execute('SELECT payload FROM record WHERE stream=? LIMIT 60', (stream,)):
            try: keys.update(json.loads(p).keys())
            except (ValueError, AttributeError): pass
    return keys


def _support(db, sql, params=None):
    t = time.perf_counter()
    try:
        row = db.execute(f'SELECT count(*), count(DISTINCT patient_id), min(value), max(value), avg(value) FROM (SELECT * FROM ({sql}) LIMIT {SAMPLE_ROWS})', params or {}).fetchone()
    except sqlite3.Error as exc:
        return {'rows': 0, 'patients': 0, 'error': str(exc)[:120]}
    return {'rows': row[0], 'patients': row[1], 'min': row[2], 'max': row[3], 'mean': None if row[4] is None else round(row[4], 4), 'seconds': round(time.perf_counter() - t, 3)}


# ---- entries --------------------------------------------------------------------------------------------------------
def _entries(db, as_of):
    docs = _docs()
    received = _payload_keys(db)
    tables = [t for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name") if t not in SKIP_TABLES]
    out = []

    def add(kind, key, name, table, sql, unit, derivation, inputs, aliases=(), description=''):
        out.append({'id': f'{kind}:{key}', 'kind': kind, 'name': name, 'table': table, 'sql': sql, 'unit': unit, 'derivation': derivation,
                    'inputs': inputs, 'aliases': sorted(set(aliases)), 'description': description})

    for t in tables:
        cols = [r[1] for r in db.execute(f'PRAGMA table_info("{t}")')]
        if 'patient_id' not in cols and t != 'patient': continue
        key = 'patient_id'
        sample = {c: [r[0] for r in db.execute(f'SELECT "{c}" FROM "{t}" WHERE "{c}" IS NOT NULL LIMIT 300')] for c in cols if c != key}
        kinds = {c: _kind(v, None) for c, v in sample.items()}
        doc = docs.get(t, '')
        dates = [c for c in cols if kinds.get(c) == 'date']
        for c in cols:
            k = kinds.get(c)
            if c == key or k is None: continue
            if c in received and c not in ('value', ): continue  # a received field: the layer did not compute it
            if re.search(r'(^|_)(id|ids|key|hash|npi|tin|zip|code|digest|record)(_|$)', c) and k != 'flag': continue
            unit = {'flag': 'flag', 'date': 'date', 'number': 'days' if re.search(r'days|los', c) else 'cents' if c.endswith('_cents') else 'number'}[k]
            h = humanize(c)
            add('stored', f'{t}.{c}', f'{h} ({t})', t, f'SELECT patient_id, "{c}" AS value FROM "{t}" WHERE "{c}" IS NOT NULL', unit,
                f'{t}.{c} as computed by the semantic layer', [f'{t}.{c}'], aliases=[h, c.replace('_', ' ')], description=_sentence(doc, c))
        ordered = []
        for i, a in enumerate(dates):
            for b in dates:
                if a == b or (a, b) in ordered: continue
                row = db.execute(f'SELECT count(*), sum(julianday(substr("{b}",1,10)) >= julianday(substr("{a}",1,10))), '
                                 f'sum(julianday(substr("{b}",1,10)) - julianday(substr("{a}",1,10)) BETWEEN 0 AND 3650) FROM "{t}" '
                                 f'WHERE "{a}" IS NOT NULL AND "{b}" IS NOT NULL LIMIT {SAMPLE_ROWS}').fetchone()
                if row[0] >= 20 and row[1] / row[0] >= 0.95 and row[2] / row[0] >= 0.95 and (a, b) not in ordered:
                    ordered.append((a, b))
        for a, b in ordered:
            if (b, a) in ordered: continue
            ha, hb = humanize(a), humanize(b)
            add('days_between', f'{t}.{a}>{b}', f'days from {ha} to {hb} ({t})', t,
                f'SELECT patient_id, julianday(substr("{b}",1,10)) - julianday(substr("{a}",1,10)) AS value FROM "{t}" WHERE "{a}" IS NOT NULL AND "{b}" IS NOT NULL '
                f'AND length("{a}")>=10 AND length("{b}")>=10', 'days', f'julianday({t}.{b}) - julianday({t}.{a})', [f'{t}.{a}', f'{t}.{b}'],
                aliases=[f'time from {ha} to {hb}', f'{ha} to {hb}', f'interval between {ha} and {hb}'])
        for c in dates:
            h = humanize(c)
            add('days_since', f'{t}.{c}', f'days since last {h} ({t})', t,
                f'SELECT patient_id, julianday(:as_of) - julianday(max(substr("{c}",1,10))) AS value FROM "{t}" WHERE "{c}" IS NOT NULL AND substr("{c}",1,10) <= :as_of GROUP BY patient_id',
                'days', f'as_of - max({t}.{c}) per patient', [f'{t}.{c}'], aliases=[f'time since last {h}', f'days since {h}', f'recency of {h}']
                + ([f'days since last {humanize(t)}', f'time since last {humanize(t)}', f'days since the last {humanize(t)}'] if c in ('event_date', 'service_date', 'fill_date', 'scheduled_date') else []))
        if t not in ('patient',) and cols:
            ht = humanize(t)
            add('count', t, f'number of {ht} records per patient ({t})', t, f'SELECT patient_id, count(*) AS value FROM "{t}" GROUP BY patient_id', 'count',
                f'count of {t} rows per patient', [t], aliases=[f'how many {ht}', f'{ht} count', f'{ht} per patient'])
        cents = [c for c in cols if c.endswith('_cents') and kinds.get(c) == 'number']
        for i, a in enumerate(cents):
            for b in cents[i + 1:]:
                for x, y in ((a, b), (b, a)):
                    hx, hy = humanize(x).replace(' dollars', ''), humanize(y).replace(' dollars', '')
                    add('ratio', f'{t}.{x}/{y}', f'{hx} as a share of {hy} ({t})', t,
                        f'SELECT patient_id, sum("{x}") * 1.0 / nullif(sum("{y}"), 0) AS value FROM "{t}" GROUP BY patient_id HAVING sum("{y}") > 0', 'ratio',
                        f'sum({t}.{x}) / sum({t}.{y}) per patient', [f'{t}.{x}', f'{t}.{y}'], aliases=[f'{hx} to {hy} ratio', f'{hx} over {hy}'])
    tabs = set(tables)
    if 'patient' in tabs and 'birth_date' in {r[1] for r in db.execute('PRAGMA table_info(patient)')}:
        add('age', 'patient.birth_date', 'age in years at the as-of date (patient)', 'patient',
            "SELECT patient_id, (julianday(:as_of) - julianday(substr(birth_date,1,10))) / 365.2425 AS value FROM patient WHERE birth_date IS NOT NULL AND length(birth_date)>=10",
            'years', 'age_at(birth_date, as_of)', ['patient.birth_date'], aliases=['patient age', 'how old'])
    if 'measurement' in tabs and 'measurement_latest' in tabs:
        for concept, n in db.execute("SELECT concept, count(DISTINCT patient_id) FROM measurement WHERE concept IS NOT NULL AND value_canonical IS NOT NULL GROUP BY 1 HAVING count(DISTINCT patient_id) >= 30 ORDER BY 1"):
            hc = humanize(concept)
            base = f"FROM measurement WHERE concept = '{concept}' AND value_canonical IS NOT NULL"
            add('concept', f'{concept}.latest', f'latest {hc} value', 'measurement_latest',
                f"SELECT patient_id, value_canonical AS value FROM measurement_latest WHERE concept = '{concept}' AND value_canonical IS NOT NULL", 'result',
                f'latest {concept} per patient (measurement_latest)', ['measurement_latest.value_canonical'], aliases=[f'most recent {hc}', f'current {hc}', f'last {hc}'])
            add('concept', f'{concept}.first', f'first {hc} value', 'measurement',
                f"SELECT patient_id, value_canonical AS value FROM (SELECT patient_id, value_canonical, row_number() OVER (PARTITION BY patient_id ORDER BY event_date, record_id) rn {base}) WHERE rn = 1",
                'result', f'earliest {concept} per patient', ['measurement.value_canonical', 'measurement.event_date'], aliases=[f'baseline {hc}', f'initial {hc}'])
            add('concept', f'{concept}.change', f'change in {hc} from first to latest value', 'measurement',
                f"SELECT patient_id, last - first AS value FROM (SELECT patient_id, first_value(value_canonical) OVER w AS first, last_value(value_canonical) OVER (w ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS last, row_number() OVER w AS rn, count(*) OVER (PARTITION BY patient_id) AS n {base} WINDOW w AS (PARTITION BY patient_id ORDER BY event_date, record_id)) WHERE rn = n AND n >= 2",
                'result', f'latest - first {concept} per patient, patients with at least two results', ['measurement.value_canonical', 'measurement.event_date'],
                aliases=[f'{hc} improvement', f'{hc} trend', f'{hc} delta', f'{hc} drop'])
            add('concept', f'{concept}.mean', f'per-patient mean of every {hc} result', 'measurement',
                f"SELECT patient_id, avg(value_canonical) AS value {base} GROUP BY patient_id", 'result', f'mean of every {concept} result per patient', ['measurement.value_canonical'],
                aliases=[f'mean {hc}'])
    return out


def _governed():
    out = []
    try:
        from . import metrics
        for mid, m in metrics.library().items():
            out.append({'id': f'governed:metric:{mid}', 'kind': 'governed', 'name': m.get('name') or mid, 'table': None, 'sql': None, 'unit': None,
                        'derivation': f"governed metric {mid} v{m.get('version')}: {str(m.get('summary') or '')[:200]}", 'inputs': [], 'aliases': [mid.replace('_', ' ')],
                        'description': str(m.get('summary') or '')[:300]})
    except Exception:  # noqa: BLE001
        pass
    return out


def build(stack, *, force=False):
    stack = Path(stack)
    out = stack / FILE
    digest = _digest(stack)
    if out.exists() and not force:
        try:
            db = _ro(out)
            meta = dict(db.execute('SELECT key, value FROM meta')); db.close()
            if meta.get('version') == VERSION and meta.get('source_digest') == str(digest): return json.loads(meta['report'])
        except sqlite3.Error:
            pass
    t0 = time.time()
    sem = _ro(_semantic(stack))
    try:
        as_of = dict(sem.execute('SELECT key, value FROM meta')).get('as_of') or '2030-05-27'
        entries = _entries(sem, as_of)
        for e in entries:
            e['support'] = _support(sem, e['sql'], {'as_of': as_of} if ':as_of' in e['sql'] else None)
        entries += _governed()
    finally:
        sem.close()
    tmp = out.with_suffix('.tmp'); tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    try:
        db.executescript('CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);'
                         'CREATE TABLE entry(id TEXT PRIMARY KEY, kind TEXT, name TEXT, tbl TEXT, sql TEXT, unit TEXT, derivation TEXT, inputs TEXT, aliases TEXT, description TEXT, support TEXT, patients INTEGER);'
                         'CREATE VIRTUAL TABLE entry_fts USING fts5(id UNINDEXED, name, aliases, description, tokenize="porter unicode61");')
        for e in entries:
            sup = e.get('support') or {}
            db.execute('INSERT INTO entry VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', (e['id'], e['kind'], e['name'], e['table'], e['sql'], e['unit'], e['derivation'],
                                                                               json.dumps(e['inputs']), json.dumps(e['aliases']), e['description'], json.dumps(sup), sup.get('patients', 0)))
            db.execute('INSERT INTO entry_fts VALUES (?,?,?,?)', (e['id'], e['name'], ' ; '.join(e['aliases']), f"{e['derivation']} {e['description']}"))
        report = {'schema': VERSION, 'source_digest': digest, 'entries': len(entries), 'by_kind': dict(Counter(e['kind'] for e in entries)),
                  'with_support': sum(1 for e in entries if (e.get('support') or {}).get('patients')), 'seconds': round(time.time() - t0, 1), 'new_paid_api_calls': 0}
        db.executemany('INSERT INTO meta VALUES (?,?)', [('version', VERSION), ('source_digest', str(digest)), ('as_of', as_of), ('report', json.dumps(report))])
        db.commit()
    finally:
        db.close()
    tmp.replace(out)
    return report


# ---- discovery ------------------------------------------------------------------------------------------------------
GENERIC = {'date', 'time', 'record', 'records', 'value', 'values', 'number', 'per', 'patient', 'patients', 'count', 'result', 'results'}
CUES = {'count': r'\bhow many\b|\bnumber of\b|\bcount\b|\bper patient\b|\btotal\b', 'days_since': r'\bsince\b|\blast\b|\brecen\w*|\bago\b|\bhaven.?t\b|\blatest\b',
        'days_between': r'\bbetween\b|\bfrom\b.*\bto\b|\binterval\b|\blead time\b|\bwait\w*\b|\bturnaround\b|\btime to\b|\bgap\b|\bto\b',
        'ratio': r'\bshare\b|\bratio\b|\bpercent\w*\b|\bproportion\b|\bfraction\b|\brate\b|\bover\b|\bas a\b'}


def _name_tokens(name):
    base = re.sub(r'\([^)]*\)', ' ', name)
    return {t for t in tokens(base) if t not in STOP and t not in GENERIC and len(t) > 1}


def _stem(t):
    return re.sub(r'(ies|es|s|ed|ing)$', '', t) if len(t) > 4 else t


def discover(stack, question, k=5, *, include_governed=True):
    """-> [{'id', 'kind', 'name', 'coverage', 'score', 'unit', 'derivation', 'inputs', 'support'}] best first. coverage = share of the entry's
    name tokens present in the question (1.0 = every word of the name is asked for)."""
    stack = Path(stack)
    if not (stack / FILE).exists(): build(stack)
    db = _ro(stack / FILE)
    try:
        q = [t for t in tokens(question) if t not in STOP and len(t) > 1]
        qs = {_stem(t) for t in q} | {_stem(t) for w in q for t in tokens(ABBREV.get(w, ''))}
        if not q: return []
        match = ' OR '.join(f'"{t}"' for t in dict.fromkeys(q))
        rows = db.execute('SELECT e.id, e.kind, e.name, e.unit, e.derivation, e.inputs, e.support, e.patients, e.aliases, bm25(entry_fts, 4.0, 2.0, 0.5) '
                          'FROM entry_fts f JOIN entry e ON e.id = f.id WHERE entry_fts MATCH ? ORDER BY bm25(entry_fts, 4.0, 2.0, 0.5) LIMIT 80', (match,)).fetchall()
    finally:
        db.close()
    out = []
    for eid, kind, name, unit, deriv, inputs, support, patients, aliases, bm in rows:
        if kind != 'governed' and not patients: continue
        if kind == 'governed' and not include_governed: continue
        nt = {_stem(t) for t in _name_tokens(name)} | {_stem(t) for t in _name_tokens(name) for t in tokens(ABBREV.get(t, ''))}
        cov, matched = (len(nt & qs) / len(nt), len(nt & qs)) if nt else (0.0, 0)
        for a in json.loads(aliases or '[]'):
            at = {_stem(t) for t in _name_tokens(a)}
            if at and len(at & qs) / len(at) > cov or (at and len(at & qs) / len(at) == cov and len(at & qs) > matched):
                cov, matched = len(at & qs) / len(at), len(at & qs)
        if kind in CUES and not re.search(CUES[kind], question or '', re.I): cov *= 0.5   # a kind of derivation the question does not ask for
        if kind == 'ratio' and ' as a share of ' in name:      # direction: 'X as a share of Y' needs X named before Y
            x, y = (re.sub(r'\([^)]*\)', ' ', part) for part in name.split(' as a share of ', 1))
            pos = lambda words: min((i for i, t in enumerate(q) if _stem(t) in {_stem(w) for w in tokens(words)}), default=None)
            px, py = pos(x), pos(y)
            if px is not None and py is not None:
                if px < py: matched += 1
                else: cov *= 0.5
        out.append({'id': eid, 'kind': kind, 'name': name, 'coverage': round(cov, 3), 'matched': matched, 'score': round(-bm, 3), 'unit': unit, 'derivation': deriv,
                    'inputs': json.loads(inputs or '[]'), 'support': json.loads(support or '{}')})
    out.sort(key=lambda r: (-r['coverage'], -r['matched'], r['kind'] == 'governed', -r['score'], r['id']))
    return out[:k]


def entry(stack, entry_id):
    db = _ro(Path(stack) / FILE)
    try:
        row = db.execute('SELECT id, kind, name, tbl, sql, unit, derivation, inputs, support FROM entry WHERE id=?', (entry_id,)).fetchone()
    finally:
        db.close()
    if row is None: raise ValueError(f'unknown derived value {entry_id!r}')
    return dict(zip(('id', 'kind', 'name', 'table', 'sql', 'unit', 'derivation', 'inputs', 'support'), row[:7] + (json.loads(row[7]), json.loads(row[8]))))


# ---- engine ---------------------------------------------------------------------------------------------------------
OP = 'derived_value'
AGGREGATES = ('mean', 'median', 'min', 'max', 'sum', 'count', 'share_over', 'share_under', 'distribution', 'patients_over', 'patients_under', 'count_over', 'count_under')
GROUP_BY = ('none', 'practice', 'payer')
PRODUCT = {'population': 'optional', 'view': 'table',
           'use': 'DERIVED VALUES that are computed from received fields rather than received as fields: average days between two dates, days since '
                  'the last record, records per patient, change in a lab from first to latest result, a money ratio, age: found by name in the '
                  'derived-value index, aggregated (mean, median, min, max, share above a threshold, distribution) over the population or a cohort, '
                  'with the executable recipe and its inputs shown.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'entry': 'derived-value index id (derived_value_index.discover)', 'aggregate': '|'.join(AGGREGATES), 'threshold': 0, 'group_by': 'none|practice|payer'},
            'meaning': 'Compute one indexed derived value per patient (the entry SQL, patient_id + value) and aggregate it: mean, median, min, max, sum, '
                       'count of patients with a value, share (or list) of patients over / under a threshold, or a decile distribution; optional split by '
                       'practice or payer. The answer names the derivation, its input fields and how many patients have a value.'}
NATIVE = ('count', 'value')


def schema(obj, string, array):
    return obj({'entry': {'type': 'string', 'description': 'derived-value index id, e.g. stored:stay.los_days or days_between:appointment.booked_date>scheduled_date'},
                'aggregate': string(AGGREGATES, 'mean, median, min, max, sum, count (patients with a value), share_over / share_under (a threshold), patients_over / patients_under (list), distribution'),
                'threshold': {'type': 'number'}, 'group_by': string(GROUP_BY, 'split by the chart\'s PCP practice or payer')})


def validate(p):
    p = dict(p or {})
    if set(p) - {'entry', 'aggregate', 'threshold', 'group_by'}: raise ValueError('derived_value parameters: entry, aggregate, threshold, group_by')
    if not isinstance(p.get('entry'), str) or not 3 <= len(p['entry']) <= 200: raise ValueError('entry: a derived-value index id')
    p.setdefault('aggregate', 'mean'); p.setdefault('group_by', 'none')
    if p['aggregate'] not in AGGREGATES: raise ValueError(f'aggregate: one of {AGGREGATES}')
    if p['group_by'] not in GROUP_BY: raise ValueError(f'group_by: one of {GROUP_BY}')
    if p['aggregate'] in ('share_over', 'share_under', 'patients_over', 'patients_under', 'count_over', 'count_under'):
        if type(p.get('threshold')) not in (int, float) or isinstance(p.get('threshold'), bool) or not math.isfinite(p['threshold']): raise ValueError('threshold: a number')
    else:
        p.pop('threshold', None)
    return p


def _agg(vals, how, thr):
    vals = sorted(vals)
    if not vals: return None
    if how == 'mean': return round(sum(vals) / len(vals), 4)
    if how == 'median': return round(statistics.median(vals), 4)
    if how == 'min': return vals[0]
    if how == 'max': return vals[-1]
    if how == 'sum': return round(sum(vals), 4)
    if how == 'count': return len(vals)
    if how == 'count_over': return sum(v > thr for v in vals)
    if how == 'count_under': return sum(v < thr for v in vals)
    if how in ('share_over', 'patients_over'): return round(sum(v > thr for v in vals) / len(vals), 4)
    if how in ('share_under', 'patients_under'): return round(sum(v < thr for v in vals) / len(vals), 4)
    return {f'p{q}': round(vals[min(len(vals) - 1, int(len(vals) * q / 100))], 4) for q in (0, 10, 25, 50, 75, 90, 100)}


def execute(stack, patient_ids, params, as_of):
    p = validate(params)
    stack = Path(stack)
    if not (stack / FILE).exists(): build(stack)
    e = entry(stack, p['entry'])
    if e['kind'] == 'governed': raise ValueError(f"{e['name']} is a governed metric: ask it through its metric, not as a derived value")
    sem = _ro(_semantic(stack))
    try:
        as_of = str(as_of)[:10]
        rows = sem.execute(e['sql'], {'as_of': as_of} if ':as_of' in e['sql'] else {}).fetchall()
        groups = {}
        if p['group_by'] != 'none':
            col = {'practice': 'pcp_practice_id', 'payer': 'payer_id'}[p['group_by']]
            groups = dict(sem.execute(f'SELECT patient_id, {col} FROM patient'))
    finally:
        sem.close()
    scope = set(patient_ids) if patient_ids is not None else None
    pairs = [(pid, float(v)) for pid, v in rows if v is not None and (scope is None or pid in scope)]
    # grain: stored columns and date-to-date intervals of an event table are one value per event (a median booking lead time is over
    # appointments, not over patient averages); every other recipe is one value per patient by construction
    event_grain = e['kind'] in ('stored', 'days_between') and e['table'] != 'patient'
    if not event_grain:
        seen = {}
        for pid, v in pairs: seen.setdefault(pid, []).append(v)
        pairs = [(pid, sum(vs) / len(vs)) for pid, vs in seen.items()]
    thr = p.get('threshold')
    values = [v for _, v in pairs]
    overall = _agg(values, p['aggregate'], thr)
    table = []
    if p['group_by'] != 'none':
        g = {}
        for pid, v in pairs: g.setdefault(groups.get(pid) or 'unknown', []).append(v)
        table = [{p['group_by']: k, 'values': len(v), 'value': _agg(v, p['aggregate'], thr)} for k, v in sorted(g.items(), key=lambda kv: (-len(kv[1]), str(kv[0])))]
    patients_with_value = len({pid for pid, _ in pairs})
    members = sorted({pid for pid, v in pairs if (p['aggregate'] == 'patients_over' and v > thr) or (p['aggregate'] == 'patients_under' and v < thr)})
    return {'schema': VERSION, 'parameters': p, 'entry': {k: e[k] for k in ('id', 'kind', 'name', 'unit', 'derivation', 'inputs', 'support')}, 'as_of': as_of, 'sql': e['sql'],
            'patients_with_value': patients_with_value, 'values_aggregated': len(values), 'grain': 'event' if event_grain else 'patient', 'overall': overall, 'table': table,
            'patient_ids': members, 'rows_read': len(rows)}


def answer(report, plan, question):
    p, e = report['parameters'], report['entry']
    partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
    unit = e.get('unit') or ''
    how = p['aggregate']
    o = report['overall']
    unit_of = 'events' if report['grain'] == 'event' else 'patients'
    n_values = report['values_aggregated']
    if o is None: value = f"No patient has a value for {e['name']}."
    elif how in ('share_over', 'share_under'): value = f"{o:.1%} of {n_values} {unit_of} have {e['name']} {'over' if how == 'share_over' else 'under'} {p['threshold']:g}."
    elif how in ('patients_over', 'patients_under'): value = f"{len(report['patient_ids'])} of {report['patients_with_value']} patients have {e['name']} {'over' if how == 'patients_over' else 'under'} {p['threshold']:g}" + (' on at least one record.' if report['grain'] == 'event' else '.')
    elif how in ('count_over', 'count_under'): value = f"{o} of {n_values} {unit_of} have {e['name']} {'over' if how == 'count_over' else 'under'} {p['threshold']:g}" + (f" ({report['patients_with_value']} patients)." if report['grain'] == 'event' else '.')
    elif how == 'distribution': value = f"{e['name']} across {n_values} {unit_of} ({report['patients_with_value']} patients): " + ', '.join(f'{k} {v}' for k, v in o.items()) + '.'
    elif how == 'count': value = f"{o} {unit_of} have a value for {e['name']}."
    else: value = f"{how.capitalize()} {e['name']} is {o}{' ' + unit if unit in ('days', 'years', 'cents') else ''} across {n_values} {unit_of}" + (f" from {report['patients_with_value']} patients." if report['grain'] == 'event' else '.')
    method = (f"Derived value {e['id']} ({e['kind']}): {e['derivation']}; inputs {', '.join(e['inputs']) or 'n/a'}; one value per {report['grain']} from the semantic layer, "
              f"aggregated as {how}. Patients without the inputs have no value and are not counted.")
    return {'status': 'partial' if partial else 'answered', 'answer_type': 'table' if report['table'] or how in ('patients_over', 'patients_under') else 'scalar',
            'table': report['table'], 'count': len(report['patient_ids']) if how in ('patients_over', 'patients_under') else o if how in ('count_over', 'count_under') else report['patients_with_value'],
            'patient_ids': report['patient_ids'], 'value': value, 'method': method, 'membership_role': 'selected',
            'definitions': [{'term': e['name'], 'basis': e['derivation']}], 'limitations': [method, 'A derivation is only as complete as its input fields; the support counts above are the patients that have them.'],
            'overall': {'derived_value': {'entry': e['id'], 'value': o, 'patients': report['patients_with_value']}}}


_AGG_CUES = [(r'\bmedian\b', 'median'), (r'\b(?:average|mean|typical)\b', 'mean'), (r'\b(?:maximum|max|longest|highest|largest|most)\b', 'max'),
             (r'\b(?:minimum|min|shortest|lowest|smallest|least)\b', 'min'), (r'\b(?:distribution|spread|quartiles?|percentiles?)\b', 'distribution')]


def route(question, profile, as_of):
    from .engine_suite import plan
    low = ' '.join(str(question).lower().split())
    stack = STACKS.get(str((profile or {}).get('source_digest'))) if isinstance(profile, dict) else None
    if not stack or not (Path(stack) / FILE).exists(): return None
    how, thr = None, None
    m = re.search(r'\b(?:more than|over|above|greater than|exceed(?:s|ing)?|longer than|at least)\s+(\d+(?:\.\d+)?)\b', low)
    n = re.search(r'\b(?:fewer than|less than|under|below|shorter than)\s+(\d+(?:\.\d+)?)\b', low)
    who = bool(re.search(r'\b(?:patients?|members?|people|persons?|who)\b', low))
    if m and re.search(r'\bhow many|number of|which|who|list\b', low): how, thr = 'patients_over' if who else 'count_over', float(m.group(1))
    elif n and re.search(r'\bhow many|number of|which|who|list\b', low): how, thr = 'patients_under' if who else 'count_under', float(n.group(1))
    elif m and re.search(r'\b(?:share|percent|proportion|%)\b', low): how, thr = 'share_over', float(m.group(1))
    else:
        for rx, h in _AGG_CUES:
            if re.search(rx, low): how = h; break
    if how is None: return None
    try:
        from . import metrics
        if metrics.candidates(question): return None  # a governed metric already defines it
    except Exception:  # noqa: BLE001
        pass
    hits = discover(stack, question, k=3)
    if not hits or hits[0]['kind'] == 'governed' or hits[0]['coverage'] < 0.99: return None
    if len(hits) > 1 and hits[1]['coverage'] >= hits[0]['coverage'] and hits[1]['matched'] >= hits[0]['matched'] and hits[1]['kind'] != 'governed' and hits[1]['score'] >= hits[0]['score'] * 0.95: return None  # ambiguous: leave to the other tiers
    params = {'entry': hits[0]['id'], 'aggregate': how, **({'threshold': thr} if thr is not None else {})}
    if re.search(r'\bby (?:pcp )?practice\b|\bper practice\b|\beach practice\b', low): params['group_by'] = 'practice'
    elif re.search(r'\bby payer\b|\bper payer\b', low): params['group_by'] = 'payer'
    return plan(OP, params, 'Derived value discovered in the derived-value index', question, [f"derived value {hits[0]['name']}"])


TEMPLATES = ['What is the average length of stay?', 'What is the median number of days between booking and the scheduled visit?',
             'How many patients have gone more than 365 days since their last visit?', 'What is the average change in A1c from first to latest result?']
