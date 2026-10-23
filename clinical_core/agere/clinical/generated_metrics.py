"""Generated governed metrics: the semantic layer's own structure, turned into governed metric definitions.

System One chooses among governed metrics (s1_governed.py). A governed metric exists only when someone authored it, so a plain
question over a categorical field ("how many replacement claims were submitted in 2028?") had no metric to choose and fell
through to a paid router, although the warehouse clearly holds the field. This module closes that gap without authoring: it reads
the semantic layer (tables, columns, values) and generates, for every table that has a date and every low-cardinality
categorical column of it,

  count   the rows (distinct entity key) whose column has the value v, in a period            returns count
  share   the percent of the table's rows in the period whose column has the value v         returns value
  by      the rows per value of the column, in a period                                      returns table

Discovery uses measured structure only, never a list of table or column names and never a question:
  entity   the first unique `_id` column of the table (verified over all rows), else one row = one entity
  date     columns whose values are ISO dates in >= 50% of rows; the period applies to the preferred one (a date an authored
           metric already anchors on, else service / event / start words before administrative ones); other dates a question
           can name ("received in 2028") become anchor variants of the same metric
  column   text or small-integer columns with 2..MAX_DISTINCT distinct values, an average group of >= MIN_AVG_GROUP rows, not
           dates, years or the entity key; a value needs >= MIN_VALUE_ROWS rows
  words    a value is named in words from, in order: a sibling label column of the table (code -> description, checked to be a
           function), the received value vocabulary (config/agere/canonical/value_sets.json dictionaries whose key names the
           column and whose codes cover the observed values), else the value itself ("replacement of prior claim (claim
           frequency code 7)")

Every metric is a template metric in the existing governed format (metrics.validate_metric), marked origin 'generated' and
certification 'generated_unreviewed' (below an authored metric: gate_policy gives its answers no certificate, so confidence is
'unverified'), validated at build time through the same slot and compile pipeline System One's picks go through and checked
against the live schema (EXPLAIN). The metric router's own gates (exact fit, number accounting, shape, literal checks) decide.

build(stack) writes <stack>/generated-metrics.json once per semantic digest (or into AGERE_GENERATED_METRICS_DIR / cache_dir, so a
read-only stack can be used), compactly: one record per (table, column) with its values. Metrics are expanded from those records
on demand; select(question) ranks by idf word overlap over name + definition words (+ encoder re-rank when one is given) and
returns only the few that can fit. Synthetic data only; no model calls, no network.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time

VERSION = 'generated-metrics/2'
FILE = 'generated-metrics.json'
ROOT = Path(__file__).resolve().parents[3]
VALUE_SETS = ROOT / 'config' / 'agere' / 'canonical' / 'value_sets.json'
SAMPLE_ROWS = 20000
MIN_TABLE_ROWS = 20
MAX_DISTINCT = 100
MIN_VALUE_ROWS = 5
MIN_AVG_GROUP = 5
MAX_ALT_DATES = 3
SKIP_TABLES = {'record', 'meta', 'link', 'current_record', 'concept_patient', 'drug_class_vocabulary'}
GRAIN = 'record'
ISO = re.compile(r'^\d{4}-\d{2}-\d{2}')
YEARLIKE = re.compile(r'^(?:19|20)\d\d(?:-\d\d)?$')
# date-column name words: the period's clock is a service / event / start date before an administrative one
PREFER = ('service', 'event', 'visit', 'encounter', 'scheduled', 'start', 'admission', 'admit', 'from', 'effective', 'received', 'collected',
          'filled', 'dispensed', 'occurred', 'onset', 'recorded', 'order', 'date')
AVOID = frozenset('birth death end to available last updated created expir thru through stop'.split())
# words a question uses for a date clock that its column names differently
CLOCK_WORDS = {'received': ('submitted', 'filed'), 'admission': ('admitted',), 'discharge': ('discharged',), 'paid': ('payment',),
               'scheduled': ('booked',), 'booked': ('scheduled',), 'filled': ('dispensed',), 'dispensed': ('filled',),
               'reversal': ('reversed',), 'submitted': ('received', 'filed'), 'adjudication': ('adjudicated',), 'start': ('started',),
               'collected': ('drawn',), 'resulted': ('reported',)}
CLOCK_GENERIC = frozenset('date time utc at dt first last from to event'.split())
LABEL_SUFFIX = frozenset('label description desc display text name'.split())
VOCAB_GENERIC = frozenset('code codes group groups famil families set sets type types'.split())
NEGATION = re.compile(r"\b(?:not|never|without|non|didn'?t|weren'?t|wasn'?t|hadn'?t|haven'?t)\b")
INDEX_STOP = frozenset('the and for with that this what which how many much who are was were our their have has had from into over per than '
                       'then them they any all can does did not out its but about row rows number total count were was submitted '
                       'there during year years'.split())
REASON = 'Generated from the semantic layer\'s own structure (generated_metrics.py); not an authored definition.'


# -- small helpers -------------------------------------------------------------------------------------------------------
def tokens(text):
    return re.findall(r'[a-z0-9]+', str(text).lower().replace('_', ' '))


def stem(word):
    w = str(word).lower()
    if w.isdigit(): return w
    if len(w) > 4 and w.endswith('ies'): w = w[:-3] + 'y'
    elif len(w) > 4 and w.endswith('sses'): w = w[:-2]
    elif len(w) > 3 and w.endswith('s') and not w.endswith('ss'): w = w[:-1]
    return w[:6]


def stems(text):
    return {stem(w) for w in tokens(text) if w not in INDEX_STOP and (w.isdigit() or len(w) > 2)}


def words(name):
    return ' '.join(tokens(name))


def quote(name):
    return '"' + str(name).replace('"', '""') + '"'


def literal(value):
    """A SQL literal that survives str.format (the template is formatted with the period)."""
    if isinstance(value, bool): value = int(value)
    if isinstance(value, int): return str(value)
    return "'" + str(value).replace("'", "''").replace('{', '{{').replace('}', '}}') + "'"


def slug(value):
    return re.sub(r'[^a-z0-9]+', '_', str(value).lower()).strip('_') or 'x'


def metric_id(table, column, value, kind):
    base = f'gen.{slug(table)}.{slug(column)}' + ('' if kind == 'by' else f'.{slug(value)}') + f'.{kind}'
    if len(base) + len('metric_') > 60:  # metric_router.tool_name cuts at 64 characters; keep ids distinct there
        h = hashlib.sha1(base.encode()).hexdigest()[:6]
        base = base[:44].rstrip('._') + f'_{h}.{kind}'
    return base


def _ro(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, check_same_thread=False)


def semantic_file(stack):
    p = Path(stack) / 'semantic.sqlite3'
    if p.exists(): return p
    from . import semantic_layer
    return semantic_layer.ensure(stack)


def digest_of(db):
    """The semantic layer's digest: its source digest and layer version; a layer without meta is hashed by its schema."""
    try:
        meta = dict(db.execute('SELECT key, value FROM meta').fetchall())
    except sqlite3.Error:
        meta = {}
    if meta.get('source_digest'): return f"{meta.get('version')}:{meta['source_digest']}"
    h = hashlib.sha256()
    for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name").fetchall():
        h.update(name.encode())
        for c in db.execute(f'PRAGMA table_info({quote(name)})').fetchall(): h.update(f'{c[1]}:{c[2]}'.encode())
        try: h.update(str(db.execute(f'SELECT count(*) FROM {quote(name)}').fetchone()[0]).encode())
        except sqlite3.Error: pass
    return 'schema:' + h.hexdigest()


# -- the received value vocabulary -----------------------------------------------------------------------------------------
_VOCAB = {}


def vocabulary():
    """[{'tokens': stems of the dictionary's name, 'map': {code: label}}] from the received value-set dictionaries: code -> label
    dictionaries, and name -> [codes] dictionaries inverted."""
    if 'v' in _VOCAB: return _VOCAB['v']
    out = []
    try:
        data = json.loads(VALUE_SETS.read_text())
    except (OSError, ValueError):
        data = {}

    def walk(node, path):
        if not isinstance(node, dict): return
        vals = list(node.values())
        name = ' '.join(path[-2:])
        if len(vals) >= 2 and all(isinstance(v, str) for v in vals) and all(len(str(k)) <= 12 for k in node):
            out.append({'name': path[-1] if path else '', 'tokens': stems(name) - {stem(w) for w in VOCAB_GENERIC}, 'map': dict(node)})
        elif len(vals) >= 2 and all(isinstance(v, list) and v and all(isinstance(c, str) for c in v) for v in vals) \
                and (not path or path[-1] not in ('codes', 'retired', 'members')):
            seen = [c for v in node.values() for c in v]
            inv = {c: words(k) for k, v in node.items() for c in v if seen.count(c) == 1}  # a code under two names names nothing
            out.append({'name': path[-1] if path else '', 'tokens': stems(name) - {stem(w) for w in VOCAB_GENERIC}, 'map': inv})
        for k, v in node.items():
            if isinstance(v, dict): walk(v, path + [str(k)])
    walk(data.get('families') or data, [])
    _VOCAB['v'] = out
    return out


def _acronym(*names):
    out = set()
    for n in names:
        t = [w for w in tokens(n) if w]
        if len(t) >= 2: out.add(''.join(w[0] for w in t))
    return out


def code_like(v):
    s = str(v)
    return not isinstance(v, str) or bool(re.search(r'\d', s)) or len(s) <= 3 or s.isupper()


def vocabulary_labels(table, column, observed):
    """{value: label} from a received dictionary that names the column and covers its code-like observed values (>= 60%).
    Words that describe themselves ('paid', 'denied') need no label; only codes ('7', '11', 'CO-16') get one."""
    observed = [v for v in observed if code_like(v)]
    if not observed: return {}
    names = stems(f'{table} {column}') | _acronym(column)
    names = names | {stem(a) for a in _acronym(column)}
    best, hit = {}, 0.0
    for entry in vocabulary():
        if not entry['tokens'] & names: continue
        covered = {v: entry['map'][str(v)] for v in observed if str(v) in entry['map']}
        share = len(covered) / max(1, len(observed))
        if share >= 0.6 and share > hit: best, hit = covered, share
    return best


def sibling_labels(db, table, column, cols):
    """{value: label} from a label/description column of the same table that is a function of this column."""
    own = set(tokens(column)) | {'code'}
    for name in cols:
        t = tokens(name)
        if name == column or len(t) < 2 or t[-1] not in LABEL_SUFFIX or not set(t[:-1]) <= own: continue
        try:
            broken = db.execute(f'SELECT count(*) FROM (SELECT {quote(column)} FROM {quote(table)} GROUP BY {quote(column)} '
                                f'HAVING count(DISTINCT {quote(name)}) > 1)').fetchone()[0]
            if broken: continue
            return {r[0]: str(r[1]) for r in db.execute(f'SELECT {quote(column)}, max({quote(name)}) FROM {quote(table)} '
                                                         f'WHERE {quote(name)} IS NOT NULL GROUP BY {quote(column)}') if r[0] is not None}
        except sqlite3.Error:
            continue
    return {}


# -- discovery -----------------------------------------------------------------------------------------------------------
def table_docs():
    """{table: the semantic layer's own documentation} (semantic_docs.schema_doc)."""
    try:
        from . import semantic_docs
        return semantic_docs.schema_doc()
    except Exception:  # noqa: BLE001 - documentation is an aid to reading a definition, never a reason to skip one
        return {}


def doc_sentence(doc, column=None, limit=130):
    """The layer's sentence about a column (else, without a column, the table's first sentence), cut at `limit`."""
    parts = [x.strip() for x in re.split(r'(?<=[.;])\s+', doc or '') if x.strip()]
    if column:
        for x in parts:
            if re.search(r'\b' + re.escape(column) + r'\b', x): return x[:limit]
        return ''
    return parts[0][:limit] if parts else ''


def _authored_anchors():
    """{table: {date column}} the authored metrics already anchor their period on (table.column of time_anchor.field)."""
    out = {}
    try:
        from . import metrics
        for m in metrics.library().values():
            field = str((m.get('time_anchor') or {}).get('field') or '')
            if re.fullmatch(r'\w+\.\w+', field):
                t, c = field.split('.')
                out.setdefault(t, set()).add(c)
    except Exception:  # noqa: BLE001 - the anchor preference is an aid; discovery never depends on it
        pass
    return out


def date_rank(table, column, authored):
    if column in authored.get(table, ()): return -1
    t = tokens(column)
    penalty = 100 if set(t) & AVOID else 0
    first = next((i for i, w in enumerate(PREFER) if w in t), len(PREFER))
    return penalty + first


def clock_words(column, table):
    """The words a question uses for this date's clock: the column's distinctive words and their synonyms ('received', 'submitted')."""
    own = [w for w in tokens(column) if w not in CLOCK_GENERIC and w not in set(tokens(table))]
    return own, sorted({w for o in own for w in (o, *CLOCK_WORDS.get(o, ()))})


def _profile_table(db, table, authored, docs):
    cols = [(r[1], (r[2] or '').upper()) for r in db.execute(f'PRAGMA table_info({quote(table)})')]
    n = db.execute(f'SELECT count(*) FROM {quote(table)}').fetchone()[0]
    if n < MIN_TABLE_ROWS or not cols: return None, {'table': table, 'reason': f'{n} rows'}, None
    sample = db.execute(f'SELECT {",".join(quote(c) for c, _ in cols)} FROM {quote(table)} LIMIT {SAMPLE_ROWS}').fetchall()
    m = len(sample)
    prof = {}
    for i, (name, _) in enumerate(cols):
        vals = [r[i] for r in sample if r[i] is not None]
        distinct = set(vals)
        strs = [v for v in vals if isinstance(v, str)]
        prof[name] = {'nonnull': len(vals), 'distinct': distinct, 'strings': len(strs), 'ints': sum(isinstance(v, int) and not isinstance(v, bool) for v in vals),
                      'maxlen': max((len(v) for v in strs), default=0),
                      'date': bool(strs) and len(strs) == len(vals) and all(ISO.match(v) for v in strs[:2000]), 'values': vals}
    key = None
    for name, _ in cols:
        p = prof[name]
        if (name == 'id' or name.endswith('_id')) and p['nonnull'] == m and len(p['distinct']) == m:
            if db.execute(f'SELECT count(DISTINCT {quote(name)}) FROM {quote(table)}').fetchone()[0] == n:
                key = name
                break
    dates = [c for c, _ in cols if prof[c]['date'] and prof[c]['nonnull'] >= 0.5 * m]
    ranked = sorted(dates, key=lambda c: (date_rank(table, c, authored), -prof[c]['nonnull'], [x for x, _ in cols].index(c)))
    if not ranked or date_rank(table, ranked[0], authored) >= 100: return None, {'table': table, 'reason': 'no usable date column'}, key
    primary = ranked[0]
    alts, seen = [], set(clock_words(primary, table)[0])
    for c in ranked[1:]:
        if date_rank(table, c, authored) >= 100: continue
        own, forms = clock_words(c, table)
        if not own or set(own) <= seen: continue
        seen |= set(own)
        alts.append({'name': '_'.join(own), 'column': c, 'forms': forms})
        if len(alts) >= MAX_ALT_DATES: break
    candidates = []
    for name, _ in cols:
        p = prof[name]
        if name == key or name in dates or p['date'] or not p['nonnull']: continue
        if len(p['distinct']) > MAX_DISTINCT or len(p['distinct']) < 1: continue
        if p['strings'] and p['strings'] != p['nonnull'] or (not p['strings'] and p['ints'] != p['nonnull']): continue
        if p['strings'] and p['maxlen'] > 40: continue
        if p['ints'] and not all(0 <= v < 1000 for v in p['distinct']): continue
        if all(YEARLIKE.match(str(v)) for v in p['distinct']): continue
        candidates.append(name)
    group = {'table': table, 'key': key, 'rows': n, 'date': primary, 'alt_dates': alts, 'doc': doc_sentence(docs.get(table)), 'columns': []}
    skipped = []
    for name in candidates:
        rows = db.execute(f'SELECT {quote(name)}, count(*) FROM {quote(table)} WHERE {quote(name)} IS NOT NULL GROUP BY {quote(name)}').fetchall()
        total = sum(r[1] for r in rows)
        if len(rows) < 2 or len(rows) > MAX_DISTINCT or total < MIN_TABLE_ROWS or total / len(rows) < MIN_AVG_GROUP:
            skipped.append({'table': table, 'column': name, 'reason': f'{len(rows)} distinct over {total} rows'})
            continue
        kept = [(v, c) for v, c in sorted(rows, key=lambda r: (-r[1], str(r[0]))) if c >= MIN_VALUE_ROWS]
        if not kept: continue
        integer = all(isinstance(v, int) for v, _ in kept)
        labels = sibling_labels(db, table, name, [c for c, _ in cols]) or vocabulary_labels(table, name, [v for v, _ in kept])
        flag = integer and {v for v, _ in rows} <= {0, 1}
        group['columns'].append({'column': name, 'kind': 'flag' if flag else 'int' if integer else 'text', 'nonnull': total, 'distinct': len(rows),
                                 'doc': doc_sentence(docs.get(table), name),
                                 'values': [{'v': v, 'n': c, **({'label': str(labels[v])} if v in labels and str(labels[v]).strip() else {})}
                                            for v, c in kept]})
    return (group if group['columns'] else None), skipped or ([] if group['columns'] else {'table': table, 'reason': 'no categorical column'}), key


def discover(db):
    """-> (groups, skipped): one group per table with a date and at least one categorical column. A column named like another table's
    unique key (patient_id, organization_id) is a reference to an entity, not a category."""
    authored, docs = _authored_anchors(), table_docs()
    groups, skipped, keys = [], [], {}
    tables = [n for (n,) in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name").fetchall()
              if n not in SKIP_TABLES and not n.startswith('sqlite_') and '_fts' not in n]
    for t in tables:
        try:
            group, why, key = _profile_table(db, t, authored, docs)
        except sqlite3.Error as exc:
            group, why, key = None, {'table': t, 'reason': f'{type(exc).__name__}: {exc}'[:120]}, None
        if key: keys.setdefault(key, set()).add(t)
        if group: groups.append(group)
        skipped += why if isinstance(why, list) else [why] if why else []
    kept = []
    for g in groups:
        refs = [c for c in g['columns'] if keys.get(c['column'], set()) - {g['table']}]
        skipped += [{'table': g['table'], 'column': c['column'], 'reason': 'references another table\'s key'} for c in refs]
        g['columns'] = [c for c in g['columns'] if c not in refs]
        if g['columns']: kept.append(g)
    return kept, skipped


# -- metric expansion ----------------------------------------------------------------------------------------------------
def describe(col, v):
    """-> (fragment, clause): how a value reads in a metric name ('claim frequency code is replacement of prior claim (claim
    frequency code 7)') and in a definition ('whose claim frequency code is ...'). A flag column reads as the flag itself."""
    raw, cw = v['v'], words(col['column'])
    if col['kind'] == 'flag':
        return (cw if raw == 1 else f'not {cw}'), f"whose {cw} flag is {raw} ({'true' if raw == 1 else 'false'})"
    label = v.get('label')
    shown = f"{label} ({cw} {raw})" if label and words(label) != words(raw) else (words(raw) if isinstance(raw, str) else str(raw))
    return f'{cw} is {shown}', f'whose {cw} is {shown}'


def _period(date):
    return f"substr({quote(date)}, 1, 10) BETWEEN '{{period_start}}' AND '{{period_end}}'"


def _sql(kind, group, col, value, date):
    t, key, c = quote(group['table']), group.get('key'), quote(col['column'])
    entity = f'count(DISTINCT {quote(key)})' if key else 'count(*)'
    if kind == 'count':
        return f"SELECT {entity} AS n FROM {t} WHERE {c} = {literal(value)} AND {_period(date)}"
    if kind == 'share':
        return (f"SELECT round(100.0 * sum(CASE WHEN {c} = {literal(value)} THEN 1 ELSE 0 END) / count(*), 2) AS share_pct "
                f"FROM {t} WHERE {_period(date)}")
    return f"SELECT {c} AS category, {entity} AS n FROM {t} WHERE {_period(date)} GROUP BY {c} ORDER BY n DESC, category"


def _variants(kind, group, col, value, summary):
    """Anchor variants: the same metric with the period on another date a question can name ('received in 2028'); summary(clock)
    states the full definition on that clock."""
    out = {}
    for alt in group.get('alt_dates') or []:
        pattern = '|'.join(re.escape(f) for f in alt['forms'])
        out[alt['name']] = {
            'when': rf'\b(?:{pattern})\s+(?:in|on|during|between|before|after|since|within|by)\b|\b(?:{pattern})\s+dates?\b|\bdate (?:of )?(?:{pattern})\b',
            'summary': summary(f"{group['table']}.{alt['column']}"),
            'anchor': alt['column'], 'time_anchor': {'field': f"{group['table']}.{alt['column']}"},
            'sql_template': _sql(kind, group, col, value, alt['column'])}
    return out


def expand(group, col, v=None, kind='count'):
    """One governed metric (the existing template format) for (table, column, value, kind)."""
    table, key, date = group['table'], group.get('key'), group['date']
    cw, tw = words(col['column']), words(table)
    entity = f"{tw} rows"
    value = v['v'] if v else None
    frag, clause = describe(col, v) if v else ('', '')
    clock = f"{table}.{date}"
    notes = (f" Other clocks a question can name: {', '.join(a['name'] for a in group['alt_dates'])}." if group.get('alt_dates') else '')
    nulls = group['rows'] - col['nonnull']
    layer = (col.get('doc') or group.get('doc') or '').strip()
    notes = (f' Layer: {layer}' if layer else '') + notes
    if kind == 'count':
        name = f"{tw.capitalize()}: {frag}"
        summary = lambda on, extra='': f"Number of {entity} {clause}; period on {on}.{extra}"
        num, den, unit, rng, returns = {'summary': f"{tw} rows with {col['column']} = {value!r}"}, {'summary': 'None (count).'}, 'rows', [0, 10 ** 9], 'count'
    elif kind == 'share':
        name = f"{tw.capitalize()}: share of rows where {frag}"
        summary = lambda on, extra='': (f"Percent of {entity} {clause}; period on {on}; denominator all {tw} rows in the period"
                                        + (f" (rows with no {cw} included)." if nulls else ".") + extra)
        num, den, unit, rng, returns = ({'summary': f"{tw} rows with {col['column']} = {value!r}"}, {'summary': f"All {tw} rows dated in the period."},
                                        'percent', [0, 100], 'value')
    else:
        name = f"{tw.capitalize()}: rows by {cw}"
        summary = lambda on, extra='': f"Number of {entity} for each {cw} ({col['distinct']} values) as a table; period on {on}.{extra}"
        num, den, unit, rng, returns = {'summary': f"{tw} rows per {col['column']}"}, {'summary': 'None (counts per value).'}, 'rows', [0, 10 ** 9], 'table'
    mid = metric_id(table, col['column'], value, kind)
    return {'id': mid, 'version': 1, 'name': name, 'domain': 'generated', 'status': 'internal', 'internal_reason': REASON,
            'origin': 'generated', 'certification': 'generated_unreviewed',
            'generated_from': {'table': table, 'column': col['column'], 'value': value, 'kind': kind, 'date': date, 'entity_key': key},
            'summary': summary(clock, notes), 'grain': {'entity': GRAIN, 'key': key or 'rowid', 'table': table},
            'population': {'summary': f"All rows of the semantic layer's {table} table."}, 'denominator': den, 'numerator': num,
            'time_anchor': {'field': clock, 'mode': 'period', 'default': 'all'}, 'value_sets': {}, 'exclusions': [], 'unit': unit,
            'expected_range': rng, 'versioning': {'rule': 'distinct_key' if key else 'none', **({'stream': table, 'key': key} if key else {})},
            'output': {'returns': returns}, 'variants': _variants(kind, group, col, value, summary), 'sql_template': _sql(kind, group, col, value, date),
            'source_file': FILE}


def iter_specs(groups):
    """(group index, column index, value index | None, kind) for every metric the groups define."""
    for gi, g in enumerate(groups):
        for ci, col in enumerate(g['columns']):
            yield gi, ci, None, 'by'
            for vi in range(len(col['values'])):
                yield gi, ci, vi, 'count'
                yield gi, ci, vi, 'share'


# -- build, cache, load --------------------------------------------------------------------------------------------------
def cache_file(stack, cache_dir=None, digest=None):
    where = cache_dir or os.environ.get('AGERE_GENERATED_METRICS_DIR')
    if not where: return Path(stack) / FILE
    return Path(where) / (f"generated-metrics-{hashlib.sha1(str(Path(stack).resolve()).encode()).hexdigest()[:10]}.json")


def _check(groups, db, as_of):
    """Validate every generated metric through the governed pipeline: metric schema, slot validation, compile for a year, and
    EXPLAIN of the rendered SQL (each date clock) against the live schema. -> (valid groups, rejected)."""
    from . import metrics, metric_router as MR
    rejected, probe = [], {'period': {'preset': 'year', 'year': int(str(as_of)[:4])}}
    for g in groups:
        for col in g['columns']:
            good = []
            for v in col['values']:
                ok = True
                for kind in ('count', 'share'):
                    ok = ok and _validate_one(db, g, col, v, kind, as_of, probe, metrics, MR, rejected)
                if ok: good.append(v)
            col['values'] = good
            if col['values'] and not _validate_one(db, g, col, None, 'by', as_of, probe, metrics, MR, rejected):
                col['values'] = []
        g['columns'] = [c for c in g['columns'] if c['values']]
    return [g for g in groups if g['columns']], rejected


def _validate_one(db, g, col, v, kind, as_of, probe, metrics, MR, rejected):
    try:
        m = metrics.validate_metric(expand(g, col, v, kind), 'generated')
        slots = MR.validate_slots(m, probe)
        node = MR.compile_metric(m['id'], m, slots, as_of)
        db.execute('EXPLAIN ' + node['parameters']['query']).fetchall()
        for name in MR.anchor_variants(m):
            alt = MR.compile_metric(m['id'], m, {**slots, 'anchor': name}, as_of)
            db.execute('EXPLAIN ' + alt['parameters']['query']).fetchall()
        return True
    except (ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        rejected.append({'table': g['table'], 'column': col['column'], 'value': v and v['v'], 'kind': kind, 'error': str(exc)[:160]})
        return False


def build(stack, *, cache_dir=None, force=False):
    """Generate the metrics of one stack's semantic layer; cached per semantic digest. -> the cache record's report."""
    started = time.time()
    target = semantic_file(stack)
    db = _ro(target)
    try:
        digest = digest_of(db)
        out = cache_file(stack, cache_dir, digest)
        if out.exists() and not force:
            try:
                data = json.loads(out.read_text())
                if data.get('version') == VERSION and data.get('semantic_digest') == digest: return data['report']
            except (OSError, ValueError):
                pass
        try:
            as_of = dict(db.execute('SELECT key, value FROM meta').fetchall()).get('as_of') or time.strftime('%Y-%m-%d')
        except sqlite3.Error:
            as_of = time.strftime('%Y-%m-%d')
        groups, skipped = discover(db)
        groups, rejected = _check(groups, db, as_of)
        data = {'schema': VERSION, 'version': VERSION, 'semantic_digest': digest, 'as_of': as_of, 'groups': groups}
        data['report'] = report(data, skipped, rejected, time.time() - started)
    finally:
        db.close()
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, default=str))
        tmp.replace(out)
        data['report']['file'] = str(out)
    except OSError as exc:  # a read-only stack: keep the record in memory for this process
        data['report']['file'] = None
        data['report']['not_written'] = str(exc)[:120]
        _MEMO[(str(out), 'mem')] = Index(data)
    return data['report']


def report(data, skipped, rejected, seconds):
    groups = data['groups']
    kinds = {'count': 0, 'share': 0, 'by': 0}
    for gi, ci, vi, kind in iter_specs(groups): kinds[kind] += 1
    return {'schema': VERSION, 'semantic_digest': data['semantic_digest'], 'tables': len(groups),
            'columns': sum(len(g['columns']) for g in groups), 'values': sum(len(c['values']) for g in groups for c in g['columns']),
            'metrics': sum(kinds.values()), 'by_kind': kinds, 'labeled_values': sum(1 for g in groups for c in g['columns'] for v in c['values'] if v.get('label')),
            'skipped': len(skipped), 'rejected': len(rejected), 'rejected_examples': rejected[:5], 'seconds': round(seconds, 1), 'new_paid_api_calls': 0}


_MEMO = {}


class Index:
    """The generated metrics of one stack: compact records in memory, metrics expanded on demand, ranked by word overlap."""

    def __init__(self, data):
        self.data = data
        self.groups = data['groups']
        self.digest = data.get('semantic_digest')
        self.specs, self.cards, self.df, self.inverted = {}, [], {}, {}
        for gi, ci, vi, kind in iter_specs(self.groups):
            g = self.groups[gi]
            col = g['columns'][ci]
            v = col['values'][vi] if vi is not None else None
            negated = bool(v) and col['kind'] == 'flag' and v['v'] == 0
            if not v: value_words = set()
            elif col['kind'] == 'flag': value_words = set(own_words)
            else: value_words = stems(f"{v.get('label') or ''} {words(v['v'])} {_expanded(v['v'])}")
            col_words = stems(f"{words(col['column'])} {_expanded(col['column'])}")
            table_words = stems(f"{words(g['table'])} {_expanded(g['table'])}")
            own_words = (col_words - table_words) or col_words
            clock = set()
            for alt in g.get('alt_dates') or []: clock |= stems(' '.join(alt['forms']))
            mid = metric_id(g['table'], col['column'], v['v'] if v else None, kind)
            idx = len(self.cards)
            self.specs[mid] = (gi, ci, vi, kind)
            self.cards.append({'id': mid, 'kind': kind, 'value': value_words, 'column': col_words, 'table': table_words, 'clock': clock,
                               'all': value_words | col_words | table_words, 'negated': negated, 'twin': (g['table'], kind, tuple(sorted(value_words))), 'n': v['n'] if v else col['nonnull']})
            for w in self.cards[-1]['all']:
                self.df[w] = self.df.get(w, 0) + 1
                self.inverted.setdefault(w, []).append(idx)

    def __len__(self):
        return len(self.cards)

    def metric(self, mid):
        gi, ci, vi, kind = self.specs[mid]
        g = self.groups[gi]
        col = g['columns'][ci]
        return expand(g, col, col['values'][vi] if vi is not None else None, kind)

    def metrics(self, ids):
        return {mid: self.metric(mid) for mid in ids if mid in self.specs}

    def rank(self, question, k=8):
        """[(score, id)] best first. A metric is offered only when the question names its value (or its column, for 'by'); a bare
        number counts only beside the column's words. Of one table's columns that carry the same value words, only the one the
        question matches best is offered."""
        q = stems(question)
        low = question.lower()
        share = bool(re.search(r'\b(?:share|percent\w*|%|proportion|fraction|rate|ratio)\b', low))
        negation = bool(NEGATION.search(low))
        by = bool(re.search(r'\b(?:by|per|each|across|break\w*|split|grouped)\b', low))
        n = len(self.cards) or 1
        hit = {}
        for w in q:
            for idx in self.inverted.get(w, ()): hit.setdefault(idx, set()).add(w)
        scored = []
        for idx, ws in hit.items():
            c = self.cards[idx]
            value_hits = {w for w in ws & c['value'] - c['table'] if not w.isdigit()}  # the table's own words name no value
            numeric = {w for w in ws & c['value'] if w.isdigit()}
            col_hits = ws & c['column'] - c['table']
            if c['kind'] == 'by':
                if not col_hits: continue
            elif c['negated'] and not negation: continue
            elif c['negated'] and not ws & c['column']: continue
            elif not (value_hits or (numeric and col_hits)): continue
            weight = sum(math.log(1 + n / self.df[w]) * (2.0 if w in c['value'] else 1.0) for w in ws)
            score = weight / math.sqrt(len(c['all']) + 4)
            score *= {'share': 1.25, 'count': 0.9, 'by': 0.8}[c['kind']] if share else {'by': 1.25, 'count': 1.0, 'share': 0.8}[c['kind']] if by \
                else {'count': 1.1, 'share': 0.8, 'by': 0.6}[c['kind']]
            if c['clock'] & q: score *= 1.05
            scored.append((score, c['id'], c['twin']))
        out, seen = [], set()
        for score, mid, twin in sorted(scored, key=lambda x: (-x[0], x[1])):
            if twin in seen and twin[1] != 'by': continue  # the same value of two columns of one table (final_status / first_status): the better match only
            seen.add(twin)
            out.append((score, mid))
        return out[:k]

    def select(self, question, k=8, encoder=None):
        """The ids of the generated metrics that may fit the question, best first (at most k)."""
        ranked = self.rank(question, max(k, 30) if encoder is not None else k)
        if encoder is not None and len(ranked) > 1:
            try:
                import numpy as np
                texts = [f"{self.metric(mid)['name']}. {self.metric(mid)['summary']}"[:600] for _, mid in ranked]
                mat = np.asarray(encoder.encode(texts), dtype=float)
                qv = np.asarray(encoder.encode([question[:600]])[0], dtype=float)
                sims = mat @ qv / (np.linalg.norm(mat, axis=1) * (np.linalg.norm(qv) or 1.0) + 1e-9)
                top = max(s for s, _ in ranked) or 1.0
                ranked = sorted(((0.5 * s / top + 0.5 * float(c), mid) for (s, mid), c in zip(ranked, sims)), key=lambda x: (-x[0], x[1]))
            except Exception:  # noqa: BLE001 - the encoder re-rank is an aid; word overlap stands
                pass
        return [mid for _, mid in ranked[:k]]


def _expanded(name):
    """Abbreviations of a name spelled out ('ed' -> emergency department) so a question's words reach it."""
    try:
        from .derived_value_index import humanize
        return humanize(name)
    except Exception:  # noqa: BLE001
        return ''


def load(stack, *, cache_dir=None, build_if_missing=True):
    """The Index of a stack's generated metrics (built once per semantic digest; memoized per process)."""
    target = semantic_file(stack)
    db = _ro(target)
    try:
        digest = digest_of(db)
    finally:
        db.close()
    out = cache_file(stack, cache_dir, digest)
    key = (str(out), digest)
    if key in _MEMO and _MEMO[key].digest == digest: return _MEMO[key]
    mem = _MEMO.get((str(out), 'mem'))
    if mem is not None and mem.digest == digest: return _MEMO.setdefault(key, mem)
    data = None
    if out.exists():
        try:
            data = json.loads(out.read_text())
        except (OSError, ValueError):
            data = None
    if not data or data.get('version') != VERSION or data.get('semantic_digest') != digest:
        if not build_if_missing: return None
        build(stack, cache_dir=cache_dir, force=True)
        mem = _MEMO.get((str(out), 'mem'))
        if mem is not None: return _MEMO.setdefault(key, mem)
        data = json.loads(out.read_text())
    _MEMO[key] = Index(data)
    return _MEMO[key]
