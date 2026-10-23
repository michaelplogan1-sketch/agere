"""Metric layer: typed metric definitions, encoded once, used by every route.

A metric (config/agere/metrics/*.json, schema agere-metric/1) states what a number MEANS:
  grain            the entity counted (patient, visit, stay, claim, member_month, appointment, account, ...)
  population       roster / age band / attribution rule
  denominator, numerator, exclusions
  time_anchor      which date field the period applies to (service vs arrival vs discharge; event_time vs
                   event_date), the mode (period, trailing window, as-of snapshot, any time), follow-up windows
  value_sets       which source codes / labels are members, built by retrieval + classification (see
                   build_value_set): standard codes (ICD-10-CM prefixes, LOINC, RxNorm ingredients, drug classes)
                   classify deterministically; label-only candidates go to review; codes are never invented
  versioning       how successive versions of an entity are combined (latest table, latest per key by a clock,
                   net over all versions, distinct key)
  units, expected_range, output, citations (definitions.json keys, measure ids, or direct) or an internal reason
  sql_template     the canonical SELECT over the semantic layer ({as_of}, {period_start}, {period_end},
                   {window_start}, {index_end})

Uses: nl2sql pins the matched metric (definition + template) into the hosted prompt; verify.metric_lint checks
generated SQL against the metric's grain, time anchor, window, value sets, population and versioning
(rules ML-*); answer_checks states the metric id/version an answer was computed as; resolve() maps question
slots to a metric for the compiler-rules layer and engines (measure-backed metrics carry engine refs).
Synthetic data only; no network, no paid calls.
"""
from __future__ import annotations

import calendar, datetime as dt, json, os, re, sqlite3, threading
from pathlib import Path

VERSION = 'agere-metric-layer/1'
SCHEMA = 'agere-metric/1'
FILE_SCHEMA = 'agere-metric-file/1'
ROOT = Path(__file__).resolve().parents[3]
METRICS_DIR = ROOT / 'config' / 'agere' / 'metrics'
DEFINITIONS_FILE = ROOT / 'config' / 'agere' / 'definitions.json'
GRAINS = ('patient', 'visit', 'stay', 'discharge', 'claim', 'adjudication', 'member_month', 'appointment', 'account',
          'referral', 'allocation', 'lab_row', 'index_event', 'record')  # record: one row of a semantic-layer table (generated_metrics.py)
ANCHOR_MODES = ('period', 'trailing', 'snapshot', 'any_time')
DEFAULT_WINDOWS = ('as_of_year', 'trailing', 'as_of', 'all', 'measure_year')
VERSIONING = ('latest_version_table', 'latest_per_key', 'net_all_versions', 'distinct_key', 'any_version', 'none')
RETURNS = ('count', 'value', 'patient_set', 'table')
SLOT_TYPES = ('enum', 'enum_list', 'integer', 'number', 'boolean', 'period', 'population')  # metric_router typed slots
RULES = {
    'ML-GRAIN': 'counting unit differs from the metric grain',
    'ML-ANCHOR': 'date field differs from the metric time anchor',
    'ML-WINDOW': 'window bounds differ from the metric window',
    'ML-VALUESET': 'value set narrower/wider than the metric value set',
    'ML-POP': 'population (age band, attribution) differs from the metric',
    'ML-SOURCE': 'numerator/denominator source differs from the metric',
    'ML-VERSION': 'versions combined differently from the metric',
}
# Value-set dimensions: the semantic table, the columns a query may filter on, the member key, raw payload fields.
DIMENSIONS = {
    'visit.type': {'table': 'visit', 'columns': ['type', 'type_concept', 'setting'], 'key': 'type',
                   'raw': {'type': 'type'}},
    'visit.disposition': {'table': 'visit', 'columns': ['disposition'], 'key': 'disposition', 'raw': {'disposition': 'disposition'}},
    'medication.ingredient': {'table': 'medication', 'columns': ['drug_name', 'drug_class', 'therapeutic_category', 'ingredients'],
                              'key': 'ingredients', 'raw': {'medication_name': 'drug_name', 'drug_name': 'drug_name'}},
    'measurement.name': {'table': 'measurement', 'columns': ['name', 'concept', 'loinc', 'component'], 'key': 'name',
                         'raw': {'name': 'name', 'test': 'name'}},
    'condition.label': {'table': 'condition', 'columns': ['label', 'concept', 'icd10', 'snomed'], 'key': 'label',
                        'raw': {'description': 'label', 'icd10': 'icd10', 'snomed': 'snomed'}},
}
_LOCK = threading.Lock()
_CACHE = {}
_UNIVERSE = {}


# ---------------------------------------------------------------------------------------------- library
def _definitions():
    if 'definitions' not in _CACHE:
        _CACHE['definitions'] = json.loads(DEFINITIONS_FILE.read_text())['definitions'] if DEFINITIONS_FILE.exists() else {}
    return _CACHE['definitions']


def _measures():
    try:
        from . import measures
        return measures.library()
    except Exception:  # noqa: BLE001 - the metric layer never depends on the measure engine loading
        return {}


def validate_metric(m, where='metric'):
    """Raise ValueError when a metric is not a complete typed definition."""
    for k in ('id', 'version', 'name', 'status', 'summary', 'grain', 'population', 'denominator', 'numerator',
              'time_anchor', 'value_sets', 'unit', 'expected_range', 'versioning', 'output'):
        if k not in m: raise ValueError(f'{where}: missing {k}')
    if not re.fullmatch(r'[a-z0-9_.]+', m['id']): raise ValueError(f'{where}: bad id {m["id"]!r}')
    if not isinstance(m['version'], int) or m['version'] < 1: raise ValueError(f'{where}: version must be a positive integer')
    if m['status'] not in ('cited', 'internal'): raise ValueError(f'{where}: status cited|internal')
    if m['status'] == 'internal' and not m.get('internal_reason'): raise ValueError(f'{where}: internal metrics state why (internal_reason)')
    if m['status'] == 'cited' and not citations(m): raise ValueError(f'{where}: cited metric has no resolvable citation')
    for d in m.get('definitions') or []:
        if d not in _definitions(): raise ValueError(f'{where}: definitions.json has no {d!r}')
    g = m['grain']
    if g.get('entity') not in GRAINS or not g.get('key'): raise ValueError(f'{where}: grain needs entity in {GRAINS} and key')
    ta = m['time_anchor']
    if ta.get('mode') not in ANCHOR_MODES or not ta.get('field'): raise ValueError(f'{where}: time_anchor needs field and mode in {ANCHOR_MODES}')
    if ta.get('default', 'all') not in DEFAULT_WINDOWS: raise ValueError(f'{where}: time_anchor.default in {DEFAULT_WINDOWS}')
    if ta['mode'] == 'trailing' and not isinstance(ta.get('months'), int): raise ValueError(f'{where}: trailing window needs months')
    fu = ta.get('followup_days')
    if fu is not None and (not isinstance(fu.get('min'), list) or not isinstance(fu.get('max'), int)):
        raise ValueError(f'{where}: followup_days needs min (list) and max (int)')
    if m['versioning'].get('rule') not in VERSIONING: raise ValueError(f'{where}: versioning.rule in {VERSIONING}')
    if m['output'].get('returns') not in RETURNS: raise ValueError(f'{where}: output.returns in {RETURNS}')
    lo, hi = m['expected_range']
    if lo > hi: raise ValueError(f'{where}: expected_range')
    age = (m['population'] or {}).get('age')
    if age is not None and not ({'min', 'max'} & set(age)): raise ValueError(f'{where}: population.age needs min or max')
    for name, vs in (m['value_sets'] or {}).items():
        if vs.get('dimension') not in DIMENSIONS: raise ValueError(f'{where}: value set {name} dimension in {sorted(DIMENSIONS)}')
        if not (vs.get('members') or vs.get('standard') or vs.get('concept')): raise ValueError(f'{where}: value set {name} needs members, standard codes or a concept')
        if not vs.get('provenance'): raise ValueError(f'{where}: value set {name} needs provenance')
        for p in vs.get('label_patterns') or []: re.compile(p)
    for c in m.get('checks') or []:
        if c.get('rule') not in RULES or not (c.get('requires') or c.get('forbids')) or not c.get('why'):
            raise ValueError(f'{where}: check needs rule in {sorted(RULES)}, requires|forbids and why')
        re.compile(c.get('requires') or c.get('forbids'))
    for part in ('all', 'any', 'none'):
        for p in (m.get('match') or {}).get(part, []): re.compile(p, re.I)
    for vname, v in (m.get('variants') or {}).items():
        if not v.get('when') or not v.get('summary'): raise ValueError(f'{where}: variant {vname} needs when and summary')
    if m.get('sql_template') is not None and not re.match(r'(?is)^\s*(select|with)\b', m['sql_template']):
        raise ValueError(f'{where}: sql_template must be one SELECT')
    if m.get('builder') is not None:  # parameterised family (metric_families.py): typed slots in, deterministic SQL out
        from .metric_families import BUILDERS
        if m['builder'] not in BUILDERS: raise ValueError(f'{where}: builder must be one of {sorted(BUILDERS)}')
        if m.get('sql_template'): raise ValueError(f'{where}: a builder metric has no sql_template')
    for name, s in (m.get('slots') or {}).items():
        if not re.fullmatch(r'[a-z_]+', name) or s.get('type') not in SLOT_TYPES:
            raise ValueError(f'{where}: slot {name} needs a type in {SLOT_TYPES}')
        if s['type'] in ('enum', 'enum_list') and not (s.get('values') or s.get('vocabulary')):
            raise ValueError(f'{where}: slot {name} needs values or a vocabulary')
    if m.get('slots') and not m.get('builder'): raise ValueError(f'{where}: declared slots need a builder')
    return m


def citations(m):
    """Every citation behind a metric: definitions.json standards, measure citations, direct citations."""
    out = []
    for d in m.get('definitions') or []:
        std = (_definitions().get(d) or {}).get('standard') or {}
        out += [{'title': s.get('title'), 'url': s.get('url'), 'via': f'definitions.json:{d}', 'standard': std.get('name')}
                for s in std.get('sources') or [] if s.get('url')]
    lib = _measures() if m.get('measures') else {}
    for mid in m.get('measures') or []:
        out += [{'title': c.get('title'), 'url': c.get('url'), 'via': f'measure:{mid}'} for c in (lib.get(mid) or {}).get('citations') or [] if c.get('url')]
    out += [{**c, 'via': 'direct'} for c in m.get('citations') or [] if c.get('url') and c.get('title')]
    return out


def _measure_metric(mid, mm):
    """A measure in config/agere/measures becomes a metric backed by the measure engine (no SQL template)."""
    age = (mm.get('denominator') or {}).get('age') or {}
    unit = mm.get('unit')
    return {'id': f'measure.{mid}', 'version': 1, 'name': mm['name'], 'domain': 'quality_measures', 'status': 'cited',
            'measures': [mid], 'summary': (mm.get('denominator') or {}).get('summary') or mm['name'],
            'aliases': mm.get('aliases') or [],
            'grain': {'entity': 'patient' if unit == 'patient' else 'index_event' if unit == 'index_event' else 'member_month', 'key': 'patient_id'},
            'population': {'summary': (mm.get('denominator') or {}).get('summary', ''),
                           **({'age': {k: age[k] for k in ('min', 'max') if k in age and not (k == 'max' and age[k] >= 120)}}
                              if {'min', 'max'} & set(age) and not (age.get('min', 0) == 0 and age.get('max', 200) >= 120) else {})},
            'denominator': mm.get('denominator') or {}, 'numerator': {'summary': (mm.get('numerator') or {}).get('summary', '')},
            'time_anchor': {'field': 'measure.period', 'mode': 'period', 'default': 'measure_year'}, 'value_sets': {},
            'unit': 'percent' if unit in ('patient', 'index_event') else 'per 100,000 member-years',
            'expected_range': [0, 100] if unit in ('patient', 'index_event') else [0, 100000],
            'versioning': {'rule': 'none'}, 'output': {'returns': 'value'}, 'engine': {'measures': mid},
            'status_note': mm.get('status'), 'spec_year': mm.get('spec_year'), 'sql_template': None}


def library(directory=None, include_measures=True):
    """{metric id: metric} from config/agere/metrics/*.json (+ one measure-backed metric per measure)."""
    directory = Path(directory or METRICS_DIR)
    files = sorted(directory.glob('*.json')) if directory.is_dir() else []
    key = ('library', str(directory), include_measures, tuple((p.name, p.stat().st_mtime_ns) for p in files))
    with _LOCK:
        if key in _CACHE: return _CACHE[key]
    out = {}
    for f in files:
        data = json.loads(f.read_text())
        if data.get('schema') != FILE_SCHEMA: continue
        for m in data.get('metrics') or []:
            m = {**m, 'domain': data.get('domain'), 'source_file': f.name}
            validate_metric(m, f'{f.name}:{m.get("id")}')
            if m['id'] in out: raise ValueError(f'duplicate metric id {m["id"]} in {f.name}')
            out[m['id']] = m
    if include_measures:
        for mid, mm in _measures().items():
            mm_metric = _measure_metric(mid, mm)
            if mm_metric['id'] not in out: out[mm_metric['id']] = validate_metric(mm_metric, f'measure {mid}')
    with _LOCK:
        _CACHE[key] = out
    return out


def get(metric_id, directory=None):
    lib = library(directory)
    if metric_id not in lib: raise KeyError(f'unknown metric {metric_id}')
    return lib[metric_id]


# ---------------------------------------------------------------------------------------------- resolve
def _match_score(m, question):
    """Deterministic match: every 'all' pattern, at least one 'any' pattern (when given), no 'none' pattern."""
    rules = m.get('match') or {}
    q = question.lower()
    if not rules.get('all') and not rules.get('any'): return 0
    if any(not re.search(p, q, re.I) for p in rules.get('all', [])): return 0
    anys = [p for p in rules.get('any', []) if re.search(p, q, re.I)]
    if rules.get('any') and not anys: return 0
    if any(re.search(p, q, re.I) for p in rules.get('none', [])): return 0
    return len(rules.get('all', [])) * 2 + len(anys)


def _variant(m, question):
    for name, v in (m.get('variants') or {}).items():
        if re.search(v['when'], question or '', re.I): return name, v
    return None, None


def apply_variant(m, name):
    """The metric with a variant's overrides applied (value sets/checks dropped, template replaced)."""
    v = (m.get('variants') or {}).get(name)
    if not v: return m
    out = {**m, 'variant': name, 'summary': v['summary']}
    if v.get('drop_value_sets'): out['value_sets'] = {k: s for k, s in m['value_sets'].items() if k not in v['drop_value_sets']}
    if v.get('drop_checks'): out['checks'] = [c for c in m.get('checks') or [] if c['rule'] not in v['drop_checks']]
    if v.get('time_anchor'): out['time_anchor'] = {**m['time_anchor'], **v['time_anchor']}  # an anchor variant moves the clock
    if 'sql_template' in v: out['sql_template'] = v['sql_template']
    if not v.get('sql_template') and not v.get('drop_value_sets'): out['sql_template'] = None  # the variant changes meaning; no template
    return out


def candidates(question, directory=None):
    """[(score, metric id)] of metrics whose deterministic match rules accept the question, best first."""
    scored = [(_match_score(m, question), mid) for mid, m in library(directory).items()]
    return sorted([s for s in scored if s[0] > 0], key=lambda s: (-s[0], s[1]))


def _encoder_candidates(question, directory=None, encoder=None, threshold=0.55):
    """Encoder similarity between the question and each templated metric's name + summary (pins only, never lint)."""
    if os.environ.get('AGERE_METRIC_ENCODER', 'on') == 'off': return []
    if encoder is None:
        try:
            from . import schema_rerank
            encoder = schema_rerank.load_encoder()
        except Exception:  # noqa: BLE001
            encoder = None
    if encoder is None: return []
    lib = {k: v for k, v in library(directory).items() if v.get('sql_template')}
    ids = sorted(lib)
    key = ('enc', id(encoder), tuple(ids))
    if key not in _CACHE:
        _CACHE[key] = encoder.encode([f"{lib[i]['name']}. {lib[i]['summary']}"[:600] for i in ids])
    import numpy as np
    mat = np.asarray(_CACHE[key], dtype=float)
    qv = np.asarray(encoder.encode([question[:600]])[0], dtype=float)
    norms = np.linalg.norm(mat, axis=1) * (np.linalg.norm(qv) or 1.0)
    sims = mat @ qv / np.where(norms == 0, 1.0, norms)
    return sorted([(float(s), i) for s, i in zip(sims, ids) if s >= threshold], key=lambda x: -x[0])


def resolve(question_slots, as_of=None, directory=None, encoder=None, allow_encoder=False):
    """Map question slots to a metric.

    question_slots: a question string, or {'question', 'metric' (explicit id), 'measure' (measure id), 'as_of',
    'period': (start, end)}. Returns None when no metric applies, else {'id', 'version', 'variant', 'metric' (variant
    applied), 'matched_by' (rules | explicit | measure | encoder), 'score', 'sql' (rendered template when as_of is
    known), 'params', 'computed_as', 'engine'}. allow_encoder lets an encoder-similar metric resolve (prompt pins);
    lint uses deterministic resolution only."""
    slots = {'question': question_slots} if isinstance(question_slots, str) else dict(question_slots or {})
    question = slots.get('question') or ''
    as_of = slots.get('as_of') or as_of
    lib = library(directory)
    chosen, by, score = None, None, 0.0
    if slots.get('metric') in lib:
        chosen, by, score = slots['metric'], 'explicit', 1.0
    elif slots.get('measure'):
        templated = [mid for mid, m in lib.items() if slots['measure'] in (m.get('measures') or []) and m.get('sql_template')]
        chosen = templated[0] if templated else (f"measure.{slots['measure']}" if f"measure.{slots['measure']}" in lib else None)
        by, score = 'measure', 1.0
    if chosen is None and question:
        cands = candidates(question, directory)
        if cands: score, chosen = cands[0]; by = 'rules'
    if chosen is None and question:
        try:
            from . import measures
            mid, _alias = measures.match(question)
        except Exception:  # noqa: BLE001
            mid = None
        if mid:
            templated = [k for k, m in lib.items() if mid in (m.get('measures') or []) and m.get('sql_template')]
            chosen = templated[0] if templated else (f'measure.{mid}' if f'measure.{mid}' in lib else None)
            by, score = 'measure', 1.0
    if chosen is None and question and allow_encoder:
        sims = _encoder_candidates(question, directory, encoder)
        if sims: score, chosen = sims[0]; by = 'encoder'
    if chosen is None: return None
    base = lib[chosen]
    vname, _ = _variant(base, question)
    m = apply_variant(base, vname) if vname else base
    out = {'id': base['id'], 'version': base['version'], 'variant': vname, 'metric': m, 'matched_by': by,
           'score': round(float(score), 3), 'engine': m.get('engine'), 'citations': citations(base)}
    if as_of and m.get('sql_template'):
        out['params'] = parameters(m, question, as_of, slots.get('period'))
        out['sql'] = render_sql(m, out['params'])
    out['computed_as'] = computed_as(out)
    return out


def computed_as(resolution):
    m = resolution['metric']
    variant = f" ({resolution['variant']} variant)" if resolution.get('variant') else ''
    return f"metric {resolution['id']} v{resolution['version']}{variant}: {m['summary']}"


# ---------------------------------------------------------------------------------------------- periods + SQL
def _d(text): return dt.date.fromisoformat(str(text)[:10])


def minus_months(day, months):
    y, mo = divmod(day.year * 12 + day.month - 1 - months, 12)
    mo += 1
    return dt.date(y, mo, min(day.day, calendar.monthrange(y, mo)[1]))


def trailing_start(as_of, months=12):
    """Inclusive start of a trailing window ending at as_of: 12 months ending 2029-08-15 start 2028-08-16 (365 days)."""
    return minus_months(_d(as_of), months) + dt.timedelta(days=1)


def period_for(question, as_of, default='as_of_year'):
    """(start, end, kind) the question's period names, else the metric default. Ends never pass the as-of date."""
    a = _d(as_of)
    q = (question or '').lower()
    years = re.findall(r'\b(20\d\d)\b', q)
    if re.search(r'\b(?:last|past|trailing|prior|previous)\s+(?:12|twelve)\s+months\b|\blast year\b(?! of)|\bpast year\b', q):
        return trailing_start(a, 12), a, 'trailing'
    if years and not re.search(r'since\s+(?:\w+\s+)?20\d\d', q):
        y = int(years[0])
        return dt.date(y, 1, 1), min(dt.date(y, 12, 31), a), 'year'
    if re.search(r'\bthis year\b|\byear[- ]to[- ]date\b|\bytd\b|\bso far\b', q):
        return dt.date(a.year, 1, 1), a, 'ytd'
    if default == 'trailing': return trailing_start(a, 12), a, 'trailing'
    if default in ('as_of_year', 'measure_year'): return dt.date(a.year, 1, 1), a, 'ytd'
    return dt.date(1900, 1, 1), a, 'all'


def parameters(m, question, as_of, period=None):
    ta = m['time_anchor']
    if period: start, end, kind = _d(period[0]), _d(period[1]), 'given'
    else: start, end, kind = period_for(question, as_of, ta.get('default', 'all'))
    months = ta.get('months') or 12
    a = _d(as_of)
    offset = ta.get('index_end_offset_days') or 0
    return {'as_of': a.isoformat(), 'period_start': start.isoformat(), 'period_end': end.isoformat(), 'period_kind': kind,
            'window_start': trailing_start(a, months).isoformat(), 'index_end': (a - dt.timedelta(days=offset)).isoformat(),
            'year': str(start.year)}


def render_sql(m, params):
    if not m.get('sql_template'): return None
    return m['sql_template'].format(**params)


def prompt_pin(question, as_of, directory=None, encoder=None):
    """Hosted-prompt block for the matched metric: its typed definition and canonical SQL ('' when none applies)."""
    if os.environ.get('AGERE_METRIC_PINS', 'on') == 'off': return ''
    r = resolve({'question': question, 'as_of': as_of}, directory=directory, encoder=encoder, allow_encoder=True)
    if not r: return ''
    return describe(r)


def describe(r):
    m = r['metric']
    lines = [f"GOVERNED METRIC {r['id']} v{r['version']}" + (f" ({r['variant']} variant)" if r.get('variant') else '') +
             f" - {m['name']}. Use this definition unless the question explicitly asks for a different one.",
             f"- definition: {m['summary']}",
             f"- grain: one {m['grain']['entity']} per {m['grain']['key']}",
             f"- population: {m['population'].get('summary', '')}" + (f" age {m['population']['age']}" if m['population'].get('age') else ''),
             f"- denominator: {m['denominator'].get('summary', '')}",
             f"- numerator: {m['numerator'].get('summary', '')}"]
    ta = m['time_anchor']
    anchor = f"- time anchor: {ta['field']} ({ta['mode']}"
    if ta['mode'] == 'trailing': anchor += f", {ta.get('months')} months ending at the as-of date inclusive: start = as-of minus {ta.get('months')} months plus 1 day"
    if ta.get('followup_days'): anchor += f"; follow-up {min(ta['followup_days']['min'])}-{ta['followup_days']['max']} days after the index date"
    if ta.get('index_end_offset_days'): anchor += f"; index events end {ta['index_end_offset_days']} days before the as-of date"
    if ta.get('forbid_fields'): anchor += f"; never {', '.join(ta['forbid_fields'])}"
    lines.append(anchor + ')')
    for name, vs in (m.get('value_sets') or {}).items():
        mem = vs.get('members') or []
        std = vs.get('standard') or {}
        desc = ', '.join(mem[:14]) or '; '.join(f"{k}: {', '.join(map(str, v[:12]))}" for k, v in std.items())
        lines.append(f"- value set {name} ({vs['dimension']}): {desc}" + (f"; NOT {', '.join(vs['exclude'][:8])}" if vs.get('exclude') else '') +
                     ''.join(f"; {q['why']}" for q in vs.get('qualifiers') or []))
    v = m['versioning']
    if v.get('rule') not in ('none', None):
        lines.append(f"- versions: {v['rule'].replace('_', ' ')}" + (f" per {v['key']}" if v.get('key') else '') + (f" ordered by {v['order_by']}" if v.get('order_by') else ''))
    for c in m.get('checks') or []:
        lines.append(f"- rule: {c['why']}")
    lines.append(f"- unit: {m['unit']}; expected range {m['expected_range'][0]}..{m['expected_range'][1]}")
    if r.get('sql'): lines.append(f"Canonical SQL for this question (adapt only what the question changes):\n```sql\n{r['sql']}\n```")
    return '\n'.join(lines)


# ---------------------------------------------------------------------------------------------- value sets (RASC)
def _norm_ingredients(text):
    return [x.strip().lower() for x in re.split(r'[,/;+]| and ', str(text or '')) if x.strip()]


def classify(vs, row):
    """Membership of one candidate row: ('member'|'excluded'|'review'|'other', basis). Standard codes decide; labels the
    definition adopted are members; label-only pattern hits go to review; nothing is invented."""
    dim = DIMENSIONS[vs['dimension']]
    key = str(row.get(dim['key']) or '')
    std = vs.get('standard') or {}
    excl = {str(x).lower() for x in vs.get('exclude') or []}
    if vs['dimension'] == 'medication.ingredient':
        ings = _norm_ingredients(row.get('ingredients'))
        if any(i in excl for i in ings) and not any(i in {x.lower() for x in std.get('ingredients', [])} for i in ings):
            return 'excluded', 'excluded ingredient'
        if any(i in {x.lower() for x in std.get('ingredients', [])} for i in ings): return 'member', 'RxNorm ingredient'
        if row.get('drug_class') and row['drug_class'] in (std.get('drug_classes') or []): return 'member', f"drug class {row['drug_class']}"
    else:
        if key.lower() in excl: return 'excluded', 'excluded label'
        icd = str(row.get('icd10') or '').upper()
        if icd and any(icd.startswith(p) for p in std.get('icd10_prefixes', [])): return 'member', 'ICD-10-CM prefix'
        if row.get('snomed') and str(row['snomed']) in std.get('snomed', []): return 'member', 'SNOMED CT code'
        if row.get('loinc') and row['loinc'] in std.get('loinc', []): return 'member', 'LOINC code'
        if key in (vs.get('members') or []): return 'member', 'adopted label'
    if any(re.search(p, key, re.I) for p in vs.get('label_patterns') or []): return 'review', 'label pattern only'
    if vs.get('concept') and vs['concept'] in (row.get('concept'), row.get('type_concept')): return 'review', 'library concept, not in the adopted set'
    return 'other', None


def build_value_set(vs, rows, dictionary=None):
    """RASC-style construction over retrieved candidates (rows: distinct values from the received data with counts 'n';
    dictionary: normalization entries {label: entry}). Returns members / review / excluded with provenance, plus the
    library concept's coverage against the members (undercount = members the concept misses; overcount = concept rows
    that are not members)."""
    dim = DIMENSIONS[vs['dimension']]
    out = {'dimension': vs['dimension'], 'members': {}, 'review': {}, 'excluded': {}, 'provenance': vs.get('provenance')}
    for row in rows:
        status, basis = classify(vs, row)
        if status == 'other': continue
        key = str(row.get(dim['key']))
        slot = out[{'member': 'members', 'review': 'review', 'excluded': 'excluded'}[status]].setdefault(key, {'rows': 0, 'basis': basis})
        slot['rows'] += int(row.get('n') or 0)
    for label, entry in (dictionary or {}).items():  # dictionary candidates the data did not surface (none are invented)
        if label in out['members'] or label in out['review'] or label in out['excluded']: continue
        conc = entry.get('agere_concept')
        if vs.get('concept') and conc == vs['concept']:
            out['review'][label] = {'rows': entry.get('source_count') or 0, 'basis': f"normalization dictionary maps to {conc} (confidence {entry.get('confidence')})"}
    if vs.get('concept'):
        concept_rows = [r for r in rows if vs['concept'] in (r.get('concept'), r.get('type_concept'))]
        in_concept = {str(r.get(dim['key'])) for r in concept_rows}
        out['concept_coverage'] = {
            'concept': vs['concept'],
            'missed_members': sorted(k for k in out['members'] if k not in in_concept),
            'missed_member_rows': sum(v['rows'] for k, v in out['members'].items() if k not in in_concept),
            'non_member_labels': sorted(k for k in in_concept if k not in out['members']),
            'non_member_rows': sum(int(r.get('n') or 0) for r in concept_rows if str(r.get(dim['key'])) not in out['members']),
        }
    return out


def universe(source, dimension):
    """Distinct candidate rows (with counts n) for a value-set dimension, read-only. source: a stack path, a semantic
    sqlite path, an open sqlite3 connection, a scalar executor (oracles.readonly_executor), or a list of rows."""
    if isinstance(source, list): return source
    dim = DIMENSIONS[dimension]
    cols = ', '.join(dim['columns'])
    obj = ', '.join(f"'{c}', {c}" for c in dim['columns'])
    sql = f"SELECT json_group_array(json_object({obj}, 'n', n)) FROM (SELECT {cols}, count(*) AS n FROM {dim['table']} GROUP BY {cols})"
    if isinstance(source, sqlite3.Connection):
        return json.loads(source.execute(sql).fetchone()[0] or '[]')
    if callable(source):
        key = (id(source), dimension)
        if key not in _UNIVERSE: _UNIVERSE[key] = json.loads(source(sql) or '[]')
        return _UNIVERSE[key]
    path = Path(source)
    target = path if path.suffix == '.sqlite3' else path / 'semantic.sqlite3'
    if not target.exists(): return []
    key = (str(target), target.stat().st_mtime_ns, dimension)
    if key not in _UNIVERSE:
        db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
        try: _UNIVERSE[key] = json.loads(db.execute(sql).fetchone()[0] or '[]')
        finally: db.close()
    return _UNIVERSE[key]


# ---------------------------------------------------------------------------------------------- lint
_LIT = r"(\([^)]*\)|'[^']*')"
_OP = r'(not\s+in|in|not\s+like|like|=|==|<>|!=)'


def strip_comments(sql):
    return re.sub(r'--[^\n]*', ' ', sql or '')


def _predicates(sql, dimension):
    """Positive atomic predicates on a dimension's columns or raw payload fields: [(column, lowered, op, literal)]."""
    dim = DIMENSIONS[dimension]
    out = []
    for col in dim['columns']:
        rx = re.compile(r"(lower\(\s*)?(?<![\w.])(?:\w+\.)?" + col + r"\b\s*\)?\s*" + _OP + r'\s*' + _LIT, re.I)
        for m in rx.finditer(sql):
            out.append((col, bool(m.group(1)), m.group(2).lower(), m.group(3)))
    for field, col in dim['raw'].items():
        rx = re.compile(r"(lower\(\s*)?json_extract\(\s*(?:\w+\.)?payload\s*,\s*'\$\." + field + r"'\s*\)\s*\)?\s*" + _OP + r'\s*' + _LIT, re.I)
        for m in rx.finditer(sql):
            out.append((col, bool(m.group(1)), m.group(2).lower(), m.group(3)))
    return [p for p in out if not p[2].startswith('not') and p[2] not in ('<>', '!=')]


def selected_rows(sql, dimension, rows):
    """Universe rows the query's positive predicates on this dimension select (OR across predicates); None when the
    query never filters the dimension."""
    preds = _predicates(strip_comments(sql), dimension)
    if not preds: return None
    cols = DIMENSIONS[dimension]['columns']
    db = sqlite3.connect(':memory:')
    try:
        db.execute(f"CREATE TABLE u(i INTEGER, {', '.join(c + ' TEXT' for c in cols)})")
        db.executemany(f"INSERT INTO u VALUES ({', '.join('?' * (len(cols) + 1))})",
                       [(i, *[None if r.get(c) is None else str(r.get(c)) for c in cols]) for i, r in enumerate(rows)])
        hit = set()
        for col, low, op, lit in preds:
            expr = f"lower({col})" if low else col
            op_sql = 'IN' if op == 'in' else 'LIKE' if op == 'like' else '='
            try:
                hit |= {r[0] for r in db.execute(f'SELECT i FROM u WHERE {expr} {op_sql} {lit}')}
            except sqlite3.Error:
                continue
        return [rows[i] for i in sorted(hit)]
    finally:
        db.close()


def _f(rule, r, message, **detail):
    return {'rule': rule, 'metric': r['id'], 'version': r['version'], 'message': message, **detail}


def lint(question, sql, as_of=None, source=None, resolution=None, directory=None):
    """Findings where the SQL departs from the metric the question resolves to (deterministic match only).
    source: where value-set universes come from (stack path / connection / scalar executor / {dimension: rows});
    without it the value-set membership check is skipped (qualifier and required checks still run)."""
    r = resolution or resolve({'question': question, 'as_of': as_of}, directory=directory)
    if not r or not sql: return []
    m = r['metric']
    code = strip_comments(sql)
    low = code.lower()
    q = question or ''
    found = []
    # ML-POP: age band and attribution
    age = (m.get('population') or {}).get('age')
    if age and not re.search(r'\bage\w*\b\s*(?:>=|>|<=|<|between|=)|\bage_at\([^)]*\)\s*(?:>=|>|<=|<|between|=)', low) and not re.search(r'\bage_band\b', low):
        band = f"{age.get('min', 0)}-{age['max']}" if 'max' in age else f"{age['min']}+"
        found.append(_f('ML-POP', r, f"metric {r['id']} restricts the population to ages {band}; the query applies no age band"))
    att = (m.get('population') or {}).get('attribution')
    if att and not re.search(att['requires'], low, re.I):
        found.append(_f('ML-POP', r, f"metric {r['id']}: {att['why']}; the query does not use it"))
    # ML-ANCHOR: forbidden date fields used instead of the anchor
    ta = m['time_anchor']
    arrival_asked = re.search(r'\barriv|\breceived\b|\bavailable\b|\bwarehouse\b|\bfeed\b', q, re.I)
    for fb in ta.get('forbid_fields') or []:
        if re.search(r'\b' + re.escape(fb) + r'\b', low) and not (arrival_asked and 'available' in fb):
            found.append(_f('ML-ANCHOR', r, f"metric {r['id']} anchors time on {ta['field']}; the query uses {fb}"))
    # ML-WINDOW: trailing-window start, follow-up day range, index end
    a = _d(as_of) if as_of else None
    if a and ta['mode'] == 'trailing':
        months = ta.get('months') or 12
        exact = minus_months(a, months).isoformat()
        start = trailing_start(a, months).isoformat()
        if (re.search(r">=\s*'" + exact + "'", code) or re.search(r"between\s+'" + exact + "'", low) or
                re.search(r">=\s*date\(\s*'" + a.isoformat() + r"'\s*,\s*'-" + str(months) + r" months'\s*\)", low)):
            found.append(_f('ML-WINDOW', r, f"metric {r['id']}: the trailing {months}-month window runs {start}..{a.isoformat()} "
                                            f"(inclusive, {(a - _d(start)).days + 1} days); the query starts on {exact}, one day early"))
    fu = ta.get('followup_days')
    if fu:
        for lo_, hi_ in re.findall(r'julianday\([^()]*\)\s*-\s*julianday\([^()]*\)\s*between\s+(-?\d+)\s+and\s+(\d+)', low):
            if int(lo_) not in fu['min'] or int(hi_) != fu['max']:
                found.append(_f('ML-WINDOW', r, f"metric {r['id']}: follow-up counts {min(fu['min'])}-{fu['max']} days after the index date; "
                                                f"the query uses {lo_}-{hi_}"))
    if a and ta.get('index_end_offset_days'):
        n = ta['index_end_offset_days']
        cut = (a - dt.timedelta(days=n)).isoformat()
        if re.search(r"\b\w+\s*<=\s*'" + a.isoformat() + "'", low) and cut not in code and not re.search(r"'-" + str(n) + r" days?'", low):
            found.append(_f('ML-WINDOW', r, f"metric {r['id']}: index events must end {n} days before the as-of date ({cut}) so every "
                                            f"follow-up window is fully observed; the query runs them to {a.isoformat()}"))
    # ML-VERSION / ML-GRAIN from the versioning rule
    v = m['versioning']
    if v.get('rule') == 'net_all_versions' and re.search(r'current_version\s*=\s*1|\bcurrent_record\b|row_number\s*\(', low):
        found.append(_f('ML-VERSION', r, f"metric {r['id']} nets amounts across EVERY adjudication version (reversals net out); "
                                         f"the query keeps only the current version"))
    if v.get('rule') == 'latest_per_key':
        if not re.search(r'row_number\s*\(|\bcurrent_record\b|max\s*\(|\bclaim\b', low):
            found.append(_f('ML-VERSION', r, f"metric {r['id']} reads the latest version per {v.get('key')}; the query combines all versions"))
        elif v.get('order_by') == 'event_time' and re.search(r'row_number\s*\(\s*\)\s*over\s*\([^)]*order\s+by\s+(?:\w+\.)?available_date', low) \
                and 'event_time' not in low:
            found.append(_f('ML-VERSION', r, f"metric {r['id']}: the latest version per {v.get('key')} is by business time (event_time), "
                                             f"not arrival order (available_date); versions arrive out of order"))
    if v.get('rule') == 'distinct_key' and v.get('stream'):
        reads_raw = re.search(r"stream\s*=\s*'" + v['stream'] + "'", low)
        if reads_raw and re.search(r'\bcount\s*\(\s*\*\s*\)|\bsum\s*\(', low) and not re.search(r'count\s*\(\s*distinct|group\s+by|row_number|current_record', low):
            found.append(_f('ML-GRAIN', r, f"metric {r['id']} counts one {m['grain']['entity']} per {v['key']}; the query counts raw "
                                           f"{v['stream']} versions"))
    # explicit checks
    for c in m.get('checks') or []:
        if c.get('when') and not re.search(c['when'], q, re.I): continue
        if c.get('unless') and re.search(c['unless'], q, re.I): continue
        if c.get('requires') and not re.search(c['requires'], code, re.I):
            found.append(_f(c['rule'], r, f"metric {r['id']}: {c['why']}"))
        if c.get('forbids') and re.search(c['forbids'], code, re.I):
            found.append(_f(c['rule'], r, f"metric {r['id']}: {c['why']}"))
    # ML-VALUESET
    for name, vs in (m.get('value_sets') or {}).items():
        if vs.get('question_overrides') and re.search(vs['question_overrides'], q, re.I): continue
        for qual in vs.get('qualifiers') or []:
            if not re.search(qual['requires'], code, re.I):
                found.append(_f('ML-VALUESET', r, f"metric {r['id']} value set {name}: {qual['why']}; the query does not restrict it"))
        rows = None
        if isinstance(source, dict): rows = source.get(vs['dimension'])
        elif source is not None:
            try: rows = universe(source, vs['dimension'])
            except Exception:  # noqa: BLE001 - no universe, no membership check
                rows = None
        if not rows:
            if vs.get('required') and not _predicates(code, vs['dimension']):
                found.append(_f('ML-VALUESET', r, f"metric {r['id']} value set {name} ({vs['dimension']}) is not applied"))
            continue
        chosen = selected_rows(code, vs['dimension'], rows)
        if chosen is None:
            if vs.get('required'):
                found.append(_f('ML-VALUESET', r, f"metric {r['id']} defines the numerator by {name} ({vs['dimension']}: "
                                                  f"{', '.join((vs.get('members') or [])[:6])}); the query never applies it"))
            continue
        others = [o for k, o in m['value_sets'].items() if k != name and o['dimension'] == vs['dimension']]
        key = DIMENSIONS[vs['dimension']]['key']
        wrong = sorted({str(x.get(key)) for x in chosen if classify(vs, x)[0] == 'excluded'
                        and not any(classify(o, x)[0] == 'member' for o in others)})
        if wrong:
            found.append(_f('ML-VALUESET', r, f"metric {r['id']} value set {name} excludes {', '.join(wrong[:6])}; the query's filter includes them",
                            over=wrong))
        picked = {str(x.get(key)) for x in chosen}  # a member is covered when any of its rows is selected
        missing = sorted({str(x.get(key)) for x in rows if str(x.get(key)) not in picked and classify(vs, x)[0] == 'member' and int(x.get('n') or 0) > 0})
        if missing and not vs.get('members_optional'):
            found.append(_f('ML-VALUESET', r, f"metric {r['id']} value set {name} includes {', '.join(missing[:6])}; the query's filter misses them",
                            under=missing))
    return found


def heal(question, sql, as_of=None, resolution=None, directory=None):
    """Mechanical, certain repairs (heal-not-block): a trailing window that starts exactly N months before the as-of
    date (one day too many) is moved to the metric's inclusive start. Returns (sql, [repairs]); nothing else is
    rewritten."""
    r = resolution or resolve({'question': question, 'as_of': as_of}, directory=directory)
    if not r or not sql or not as_of: return sql, []
    ta = r['metric']['time_anchor']
    if ta['mode'] != 'trailing': return sql, []
    a = _d(as_of)
    months = ta.get('months') or 12
    exact, start = minus_months(a, months).isoformat(), trailing_start(a, months).isoformat()
    out, n = re.subn(r"(>=\s*)'" + exact + "'", lambda m: m.group(1) + f"'{start}'", sql)
    out, n2 = re.subn(r"(?i)(between\s+)'" + exact + "'", lambda m: m.group(1) + f"'{start}'", out)
    out, n3 = re.subn(r"(?i)>=(\s*date\(\s*'" + a.isoformat() + r"'\s*,\s*'-" + str(months) + r" months'\s*\))", r'>\1', out)
    if not (n or n2 or n3): return sql, []
    return out, [{'rule': 'ML-WINDOW', 'metric': r['id'], 'version': r['version'],
                  'message': f"window start moved from {exact} to {start}: metric {r['id']} counts the trailing {months} months ending "
                             f"{a.isoformat()} inclusive ({(a - _d(start)).days + 1} days)"}]


def check_result(resolution, value):
    """Expected-range check on a scalar result ('' when in range or not numeric)."""
    if not resolution or not isinstance(value, (int, float)) or isinstance(value, bool): return ''
    lo, hi = resolution['metric']['expected_range']
    if lo <= value <= hi: return ''
    return f"{value} is outside the expected range {lo}..{hi} for metric {resolution['id']} ({resolution['metric']['unit']})"


def audit_concepts(source):
    """RASC audit of the concept library against the received data: for each condition concept, distinct source rows whose
    standard ICD-10-CM code falls under the concept's prefixes but are not assigned the concept (undercount), and rows
    assigned by label text only (no standard code: review); for each measurement concept, LOINC-coded rows outside the
    concept and concept rows without a LOINC code. Nothing is added to any value set here; this is the review list."""
    from . import concepts
    out = {'conditions': {}, 'measurements': {}}
    rows = universe(source, 'condition.label')
    by_code = {}
    for r in rows:
        by_code.setdefault((r.get('label'), r.get('icd10')), set()).add(r.get('concept'))
    for c, d in concepts.CONDITIONS.items():
        missed, label_only, coded, snomed = {}, {}, 0, 0
        for r in rows:
            icd = str(r.get('icd10') or '').upper()
            std = bool(icd) and any(icd.startswith(p) for p in d['icd10'])
            assigned = c in by_code.get((r.get('label'), r.get('icd10')), set())
            if std and r.get('concept') == c: coded += int(r.get('n') or 0)
            if std and not assigned: missed[f"{r.get('label')} [{icd}]"] = missed.get(f"{r.get('label')} [{icd}]", 0) + int(r.get('n') or 0)
            if r.get('concept') == c and not icd and r.get('snomed'): snomed += int(r.get('n') or 0)
            if r.get('concept') == c and not icd and not r.get('snomed'):
                label_only[str(r.get('label'))] = label_only.get(str(r.get('label')), 0) + int(r.get('n') or 0)
        if missed or label_only:
            out['conditions'][c] = {'icd10_coded_rows': coded, 'snomed_only_rows': snomed, 'missed_by_concept': missed, 'label_only_rows': label_only}
    mrows = universe(source, 'measurement.name')
    for c, d in concepts.MEASUREMENTS.items():
        loincs = set(d.get('loinc') or [])
        if not loincs: continue
        outside = {f"{r.get('name')} [{r.get('loinc')}]": int(r.get('n') or 0) for r in mrows if r.get('loinc') in loincs and r.get('concept') != c}
        uncoded = {str(r.get('name')): int(r.get('n') or 0) for r in mrows if r.get('concept') == c and not r.get('loinc')}
        other_code = {f"{r.get('name')} [{r.get('loinc')}]": int(r.get('n') or 0) for r in mrows
                      if r.get('concept') == c and r.get('loinc') and r.get('loinc') not in loincs}
        if outside or uncoded or other_code:
            out['measurements'][c] = {'coded_rows_outside_concept': outside, 'concept_rows_without_loinc': uncoded,
                                      'concept_rows_with_other_loinc': other_code}
    return out


def coverage_report(source, directory=None, dictionaries=None):
    """Value-set coverage of every templated metric against the received data and the concept library."""
    out = {}
    dicts = dictionaries or {}
    for mid, m in library(directory, include_measures=False).items():
        for name, vs in (m.get('value_sets') or {}).items():
            rows = universe(source, vs['dimension']) if not isinstance(source, dict) else source.get(vs['dimension'], [])
            built = build_value_set(vs, rows, dicts.get(vs['dimension']))
            out[f'{mid}.{name}'] = {'dimension': vs['dimension'],
                                    'members': {k: v['rows'] for k, v in built['members'].items()},
                                    'review': {k: v for k, v in built['review'].items()},
                                    'excluded': {k: v['rows'] for k, v in built['excluded'].items()},
                                    'concept_coverage': built.get('concept_coverage'), 'provenance': built['provenance']}
    return out
