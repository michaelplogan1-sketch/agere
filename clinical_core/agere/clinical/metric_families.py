"""Parameterised governed metric families: deterministic SQL from validated typed slots.

A family metric (config/agere/metrics/families.json, key "builder") states its meaning once, like every metric in the
metric layer, and exposes typed slots (period, population atoms, visit classes, group-by, thresholds, ...). The metric
router (metric_router.py) asks a small model only to CHOOSE the metric and FILL the slots; this module turns validated
slots into one read-only SELECT over the semantic layer. Value sets (visit classes, drug groups, social needs) come from
config/agere/metric_vocabulary.json; condition and lab concepts come from concepts.py through the semantic layer
(concept_patient, measurement, measurement_latest). Nothing here calls a model or the network. Synthetic data only.
"""
from __future__ import annotations

import datetime as dt, json, re
from pathlib import Path

VERSION = 'agere-metric-families/1'
ROOT = Path(__file__).resolve().parents[3]
VOCABULARY_FILE = ROOT / 'config' / 'agere' / 'metric_vocabulary.json'
_CACHE = {}
OPS = {'lt': '<', 'le': '<=', 'gt': '>', 'ge': '>=', 'eq': '=', 'ne': '<>'}
PERIOD_PRESETS = ('all_time', 'year', 'ytd', 'trailing_months', 'trailing_days', 'custom', 'before_days')
ATOM_KINDS = ('condition', 'lab', 'blood_pressure', 'demographic', 'medication', 'visit', 'social_need', 'appointment')
ROLES = ('all', 'any', 'exclude')
AGE_BANDS = "CASE WHEN p.age_latest < 18 THEN '0-17' WHEN p.age_latest < 45 THEN '18-44' WHEN p.age_latest < 65 THEN '45-64' ELSE '65+' END"
PATIENT_GROUPS = {'none': None, 'coverage_type': 'p.coverage_type', 'sex': 'p.sex_reported', 'age_band': AGE_BANDS,
                  'attributed_practice': 'p.pcp_practice_id'}


def vocabulary():
    if 'vocab' not in _CACHE:
        _CACHE['vocab'] = json.loads(VOCABULARY_FILE.read_text())
    return _CACHE['vocab']


def _concepts():
    from . import concepts
    return concepts


def condition_concepts(): return sorted(_concepts().CONDITIONS)
def lab_concepts(): return sorted(_concepts().MEASUREMENTS)
def visit_classes(): return sorted(vocabulary()['visit_classes'])
def drug_groups(): return sorted(vocabulary()['drug_groups'])
def social_needs(): return sorted(vocabulary()['social_needs'])


def q(value):
    """SQL string literal (values here are validated enums or ISO dates; quotes are still escaped)."""
    return "'" + str(value).replace("'", "''") + "'"


def qlist(values): return '(' + ', '.join(q(v) for v in values) + ')'


def num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)): raise ValueError(f'expected a number, got {value!r}')
    return repr(int(value)) if float(value).is_integer() else repr(float(value))


# ------------------------------------------------------------------------------------------------ periods
def _date(text, what):
    try:
        return dt.date.fromisoformat(str(text)[:10])
    except ValueError as exc:
        raise ValueError(f'{what}: not an ISO date: {text!r}') from exc


def _minus_months(day, months):
    from .metrics import minus_months
    return minus_months(day, months)


def period_bounds(spec, as_of):
    """(start, end) ISO strings (either may be None = unbounded) for a typed period; None spec / all_time -> (None, None)."""
    if spec in (None, {}, 'all_time'): return None, None
    if not isinstance(spec, dict): raise ValueError('period must be an object')
    a = _date(as_of, 'as_of')
    preset = spec.get('preset') or ('custom' if spec.get('start') or spec.get('end') else 'all_time')
    if preset not in PERIOD_PRESETS: raise ValueError(f'period.preset must be one of {PERIOD_PRESETS}')
    if preset == 'all_time': return None, None
    if preset == 'year':
        y = spec.get('year')
        if not isinstance(y, int) or not 1990 <= y <= a.year: raise ValueError(f'period.year must be a year up to {a.year}')
        return f'{y}-01-01', f'{y}-12-31'
    if preset == 'ytd': return f'{a.year}-01-01', a.isoformat()
    if preset == 'trailing_months':
        n = spec.get('months', 12)
        if not isinstance(n, int) or not 1 <= n <= 120: raise ValueError('period.months must be 1..120')
        return (_minus_months(a, n) + dt.timedelta(days=1)).isoformat(), a.isoformat()
    if preset == 'trailing_days':
        n = spec.get('days')
        if not isinstance(n, int) or not 1 <= n <= 3650: raise ValueError('period.days must be 1..3650')
        return (a - dt.timedelta(days=n)).isoformat(), a.isoformat()  # "the last 180 days": from as-of minus 180 days
    if preset == 'before_days':
        n = spec.get('days')
        if not isinstance(n, int) or not 0 <= n <= 3650: raise ValueError('period.days must be 0..3650')
        return None, (a - dt.timedelta(days=n)).isoformat()
    start = _date(spec['start'], 'period.start').isoformat() if spec.get('start') else None
    end = _date(spec['end'], 'period.end').isoformat() if spec.get('end') else None
    if start and end and start > end: raise ValueError('period.start is after period.end')
    if start and start > a.isoformat(): raise ValueError('period starts after the snapshot date')
    return start, end


def date_clause(column, bounds):
    start, end = bounds
    if start and end: return f"{column} BETWEEN {q(start)} AND {q(end)}"
    if start: return f"{column} >= {q(start)}"
    if end: return f"{column} <= {q(end)}"
    return None


def describe_period(bounds):
    start, end = bounds
    if not start and not end: return 'any time (undated records included)'
    return f"{start or 'the beginning'} through {end or 'the snapshot date'}"


# ------------------------------------------------------------------------------------------------ population atoms
def _and(parts): return ' AND '.join(f'({x})' for x in parts if x) or '1=1'


def _visit_types(classes):
    vc = vocabulary()['visit_classes']
    bad = [c for c in classes if c not in vc]
    if bad or not classes: raise ValueError(f'visit classes must be from {sorted(vc)}; got {classes}')
    return [t for c in classes for t in vc[c]['types']]


def _drug_clause(groups, alias='m'):
    dg = vocabulary()['drug_groups']
    bad = [g for g in groups if g not in dg]
    if bad or not groups: raise ValueError(f'drug groups must be from {sorted(dg)}; got {groups}')
    classes = sorted({c for g in groups for c in dg[g].get('classes', [])})
    likes = sorted({x for g in groups for x in dg[g].get('ingredients_like', []) + dg[g].get('names_like', [])})
    parts = [f"{alias}.drug_class IN {qlist(classes)}"] if classes else []
    parts += [f"lower({alias}.drug_name) LIKE {q('%' + x + '%')}" for x in likes]
    return '(' + ' OR '.join(parts) + ')'


def _evidence(e):
    ev = vocabulary()['medication_evidence']
    if e not in ev: raise ValueError(f'medication evidence must be one of {sorted(ev)}')
    return ev[e]['where']


_VALID_LAB = "m.value_canonical IS NOT NULL AND coalesce(m.result_status, '') <> 'cancelled'"


def _in(select):
    """Membership in an uncorrelated patient-id subquery (computed once, not per patient)."""
    return f"p.patient_id IN ({select})"


def _lab_latest(concept, op, value, bounds):
    """The most recent dated, non-cancelled result satisfies op value; bounds restrict the latest date. Several results on
    the latest date resolve to the WORST value for the comparison (highest for >, >=; lowest for <, <=)."""
    window = date_clause('m.event_date', bounds)
    worst = 'max' if op in ('gt', 'ge') else 'min'
    base = _and([f"m.concept = {q(concept)}", _VALID_LAB, 'm.event_date IS NOT NULL', window])
    return _in(f"SELECT m.patient_id FROM measurement m JOIN (SELECT m.patient_id AS pid, max(m.event_date) AS d FROM measurement m "
               f"WHERE {base} GROUP BY m.patient_id) l ON l.pid = m.patient_id AND l.d = m.event_date WHERE {base} "
               f"GROUP BY m.patient_id HAVING {worst}(m.value_canonical) {OPS[op]} {num(value)}")


def atom_sql(atom, as_of):
    """One population atom -> a boolean SQL expression over patient alias p."""
    if not isinstance(atom, dict): raise ValueError('population atoms must be objects')
    kind = atom.get('kind')
    if kind not in ATOM_KINDS: raise ValueError(f'atom.kind must be one of {ATOM_KINDS}')
    bounds = period_bounds(atom.get('period'), as_of)
    if kind == 'condition':
        cs = atom.get('concepts') or ([atom['concept']] if atom.get('concept') else [])
        bad = [c for c in cs if c not in condition_concepts()]
        if not cs or bad: raise ValueError(f'condition atom: concepts must be from the concept library; unknown {bad or "none given"}')
        return _in(f"SELECT c.patient_id FROM concept_patient c WHERE c.kind = 'condition' AND c.concept IN {qlist(cs)}")
    if kind in ('lab', 'blood_pressure'):
        agg = atom.get('aggregate') or 'any'
        if agg not in ('any', 'latest'): raise ValueError('lab atom: aggregate any|latest')
        if kind == 'blood_pressure':
            s, d = atom.get('systolic_below'), atom.get('diastolic_below')
            if s is None or d is None: raise ValueError('blood_pressure atom needs systolic_below and diastolic_below')
            if agg != 'latest': raise ValueError('blood_pressure atom: aggregate must be latest')
            return _and([_lab_latest('systolic_bp', 'lt', s, bounds), _lab_latest('diastolic_bp', 'lt', d, bounds)])
        concept = atom.get('concept')
        if concept not in lab_concepts(): raise ValueError(f'lab atom: concept must be one of {lab_concepts()}')
        op = atom.get('op') or 'exists'
        if op != 'exists' and op not in OPS: raise ValueError(f'lab atom: op must be exists or one of {sorted(OPS)}')
        if op != 'exists' and atom.get('value') is None: raise ValueError('lab atom: a comparison needs value')
        if agg == 'latest' and op != 'exists':
            return _lab_latest(concept, op, atom['value'], bounds)
        window = date_clause('m.event_date', bounds)
        if atom.get('dated_only') and not window: window = 'm.event_date IS NOT NULL'
        cmp = f"m.value_canonical {OPS[op]} {num(atom['value'])}" if op != 'exists' else None
        return _in(f"SELECT m.patient_id FROM measurement m WHERE {_and([f'm.concept = {q(concept)}', _VALID_LAB, cmp, window])}")
    if kind == 'demographic':
        field = atom.get('field')
        col = {'age': 'p.age_latest', 'sex': 'p.sex_reported', 'coverage_type': 'p.coverage_type'}.get(field)
        if not col: raise ValueError('demographic atom: field age|sex|coverage_type')
        if field == 'age':
            parts = []
            if atom.get('min') is not None: parts.append(f"{col} >= {num(atom['min'])}")
            if atom.get('max') is not None: parts.append(f"{col} <= {num(atom['max'])}")
            if not parts: raise ValueError('age atom needs min and/or max (inclusive)')
            return _and(parts)
        values = atom.get('values') or ([atom['value']] if atom.get('value') else [])
        allowed = {'sex': ['F', 'M'], 'coverage_type': ['commercial', 'medicaid', 'medicare', 'uninsured']}[field]
        if not values or any(v not in allowed for v in values): raise ValueError(f'{field} atom: values from {allowed}')
        return f"{col} IN {qlist(values)}"
    if kind == 'medication':
        groups = atom.get('groups') or []
        ings = atom.get('ingredients') or []
        if any(not isinstance(x, str) or not re.fullmatch(r'[a-z][a-z -]{2,40}', x) for x in ings):
            raise ValueError('medication atom: ingredients are lower-case RxNorm ingredient names')
        if not groups and not ings: raise ValueError('medication atom needs groups or ingredients')
        drug = ' OR '.join(([_drug_clause(groups)] if groups else []) +
                           [f"(',' || replace(lower(m.ingredients), ' ', '') || ',') LIKE {q('%,' + x.replace(' ', '') + ',%')}" for x in ings])
        where = [f'({drug})', _evidence(atom.get('evidence') or 'dispensed'), date_clause('m.event_date', bounds)]
        return _in(f"SELECT m.patient_id FROM medication m WHERE {_and(where)}")
    if kind == 'visit':
        types = _visit_types(atom.get('classes') or [])
        n = atom.get('min_count', 1)
        if not isinstance(n, int) or n < 1: raise ValueError('visit atom: min_count >= 1')
        where = _and([f"v.type IN {qlist(types)}", date_clause('v.event_date', bounds)])
        having = f" GROUP BY v.patient_id HAVING count(DISTINCT v.encounter_id) >= {n}" if n > 1 else ''
        return _in(f"SELECT v.patient_id FROM visit v WHERE {where}{having}")
    if kind == 'social_need':
        sn = vocabulary()['social_needs']
        needs = atom.get('needs') or ([atom['need']] if atom.get('need') else [])
        if not needs or any(x not in sn for x in needs): raise ValueError(f'social_need atom: needs from {sorted(sn)}')
        src = [x for n_ in needs for x in sn[n_]['needs']]
        n = atom.get('min_count', 1)
        if not isinstance(n, int) or n < 1: raise ValueError('social_need atom: min_count >= 1')
        where = _and([f"s.need IN {qlist(src)}", None if atom.get('screened_only') else 's.positive = 1', date_clause('s.event_date', bounds)])
        having = f" GROUP BY s.patient_id HAVING count(DISTINCT s.need) >= {n}" if n > 1 else ''
        return _in(f"SELECT s.patient_id FROM social_need s WHERE {where}{having}")
    if kind == 'appointment':
        status = atom.get('status') or 'no_show'
        col = {'no_show': 'a.ever_no_show = 1', 'kept': 'a.kept = 1', 'cancelled': 'a.ever_cancelled = 1'}.get(status)
        if not col: raise ValueError('appointment atom: status no_show|kept|cancelled')
        return _in(f"SELECT a.patient_id FROM appointment a WHERE {_and([col, date_clause('a.scheduled_date', bounds)])}")
    raise ValueError(f'unsupported atom {kind}')


def population_sql(atoms, as_of):
    """Atoms (each with role all|any|exclude) -> a WHERE predicate over patient p."""
    atoms = atoms or []
    if not isinstance(atoms, list): raise ValueError('population must be a list of atoms')
    groups = {'all': [], 'any': [], 'exclude': []}
    for a in atoms:
        role = (a or {}).get('role') or 'all'
        if role not in ROLES: raise ValueError(f'atom.role must be one of {ROLES}')
        groups[role].append(atom_sql(a, as_of))
    parts = list(groups['all'])
    if groups['any']: parts.append(' OR '.join(f'({x})' for x in groups['any']))
    parts += [f'NOT ({x})' for x in groups['exclude']]
    return _and(parts)


# ------------------------------------------------------------------------------------------------ builders
def _group_expr(column, by):
    if by == 'month': return f"substr({column}, 1, 7)"
    if by == 'quarter': return f"substr({column}, 1, 4) || '-Q' || ((CAST(substr({column}, 6, 2) AS INTEGER) + 2) / 3)"
    if by == 'half_year': return f"substr({column}, 1, 4) || '-H' || CASE WHEN substr({column}, 6, 2) <= '06' THEN '1' ELSE '2' END"
    if by == 'year': return f"substr({column}, 1, 4)"
    return None


def build_cohort(slots, as_of, m=None):
    """Patients matching the population (count / list / table by group); with a numerator, the percent of them who also
    match the numerator atoms."""
    where = population_sql(slots.get('population') or [], as_of)
    returns = slots.get('returns') or 'count'
    by = slots.get('group_by') or 'none'
    if by not in PATIENT_GROUPS: raise ValueError(f'group_by must be one of {sorted(PATIENT_GROUPS)}')
    numer = slots.get('numerator') or []
    if returns == 'percent' and not numer: raise ValueError('percent needs numerator atoms')
    if returns != 'percent' and numer and by == 'none': raise ValueError('numerator atoms only apply to returns=percent or a grouped table')
    nsql = population_sql(numer, as_of) if numer else None
    if by != 'none':
        g = PATIENT_GROUPS[by]
        cols = f"{g} AS {by}, count(*) AS patients"
        if nsql: cols += f", sum(CASE WHEN {nsql} THEN 1 ELSE 0 END) AS numerator, 100.0 * sum(CASE WHEN {nsql} THEN 1 ELSE 0 END) / count(*) AS percent"
        return {'query': f"SELECT {cols} FROM patient p WHERE {where} GROUP BY 1 ORDER BY 1", 'returns': 'table'}
    if returns == 'patient_set':
        return {'query': f"SELECT p.patient_id FROM patient p WHERE {where} ORDER BY 1", 'returns': 'patient_set'}
    if returns == 'percent':
        return {'query': f"SELECT 100.0 * sum(CASE WHEN {nsql} THEN 1 ELSE 0 END) / count(*) AS percent FROM patient p WHERE {where}",
                'returns': 'value'}
    if returns == 'count':
        return {'query': f"SELECT count(*) AS patients FROM patient p WHERE {where}", 'returns': 'count'}
    raise ValueError('returns must be count|patient_set|percent (table needs group_by)')


def build_encounter_volume(slots, as_of, m=None):
    types = _visit_types(slots.get('visit_classes') or [])
    bounds = period_bounds(slots.get('period'), as_of)
    by = slots.get('group_by') or 'none'
    measure = slots.get('measure') or 'visits'
    pop = slots.get('population') or []
    vwhere = _and([f"v.type IN {qlist(types)}", date_clause('v.event_date', bounds)])
    pwhere = population_sql(pop, as_of) if pop else '1=1'
    classes = slots.get('visit_classes') or []
    vcol = f'{classes[0]}_visits' if len(classes) == 1 and re.fullmatch(r'[a-z_]+', classes[0]) else 'visits'  # measure-named column
    if measure not in ('visits', 'patients', 'per_1000_roster'): raise ValueError('measure visits|patients|per_1000_roster')
    if by in PATIENT_GROUPS and by != 'none':
        g = PATIENT_GROUPS[by]
        if measure == 'per_1000_roster':
            return {'query': f"SELECT {g} AS {by}, count(DISTINCT p.patient_id) AS patients, count(DISTINCT v.encounter_id) AS {vcol}, "
                             f"1000.0 * count(DISTINCT v.encounter_id) / count(DISTINCT p.patient_id) AS per_1000 "
                             f"FROM patient p LEFT JOIN visit v ON v.patient_id = p.patient_id AND {vwhere} WHERE {pwhere} GROUP BY 1 ORDER BY 1",
                    'returns': 'table'}
        agg = f'count(DISTINCT v.encounter_id) AS {vcol}' if measure == 'visits' else 'count(DISTINCT v.patient_id) AS patients'
        return {'query': f"SELECT {g} AS {by}, {agg} FROM visit v JOIN patient p ON p.patient_id = v.patient_id "
                         f"WHERE {vwhere} AND {pwhere} GROUP BY 1 ORDER BY 1", 'returns': 'table'}
    if by == 'facility':
        agg = f'count(DISTINCT v.encounter_id) AS {vcol}' if measure != 'patients' else 'count(DISTINCT v.patient_id) AS patients'
        return {'query': f"SELECT v.organization_id AS organization_id, {agg} FROM visit v JOIN patient p ON p.patient_id = v.patient_id "
                         f"WHERE {vwhere} AND {pwhere} GROUP BY 1 ORDER BY 1", 'returns': 'table'}
    ge = _group_expr('v.event_date', by)
    if by != 'none' and not ge: raise ValueError('group_by must be none|month|quarter|half_year|year|facility|coverage_type|sex|age_band|attributed_practice')
    if measure == 'per_1000_roster':
        if ge: raise ValueError('per_1000_roster by a time period is not defined here; use visits')
        return {'query': f"SELECT 1000.0 * (SELECT count(DISTINCT v.encounter_id) FROM visit v JOIN patient p ON p.patient_id = v.patient_id "
                         f"WHERE {vwhere} AND {pwhere}) / (SELECT count(*) FROM patient p WHERE {pwhere}) AS per_1000", 'returns': 'value'}
    agg = f'count(DISTINCT v.encounter_id) AS {vcol}' if measure == 'visits' else 'count(DISTINCT v.patient_id) AS patients'
    if ge:
        return {'query': f"SELECT {ge} AS {by}, {agg} FROM visit v JOIN patient p ON p.patient_id = v.patient_id "
                         f"WHERE {vwhere} AND {pwhere} GROUP BY 1 ORDER BY 1", 'returns': 'table'}
    return {'query': f"SELECT {agg} FROM visit v JOIN patient p ON p.patient_id = v.patient_id WHERE {vwhere} AND {pwhere}", 'returns': 'count'}


def build_visit_followup(slots, as_of, m=None):
    idx = _visit_types(slots.get('index_classes') or [])
    fu = _visit_types(slots.get('followup_classes') or [])
    bounds = period_bounds(slots.get('period'), as_of)
    lo, hi = slots.get('days_min', 0), slots.get('days_max')
    if not isinstance(hi, int) or not isinstance(lo, int) or not 0 <= lo <= hi <= 365: raise ValueError('days_min..days_max must be 0..365 integers')
    anchor = slots.get('index_anchor') or 'visit_date'
    col = {'visit_date': 'e.event_date', 'discharge_date': 'e.end_date'}.get(anchor)
    if not col: raise ValueError('index_anchor visit_date|discharge_date')
    observed = slots.get('fully_observed', False)
    where = _and([f"e.type IN {qlist(idx)}", date_clause(col, bounds),
                  f"{col} <= {q((_date(as_of, 'as_of') - dt.timedelta(days=hi)).isoformat())}" if observed else None])
    hit = (f"EXISTS (SELECT 1 FROM visit f WHERE f.patient_id = e.patient_id AND f.encounter_id <> e.encounter_id AND f.type IN {qlist(fu)} "
           f"AND julianday(f.event_date) - julianday({col}) BETWEEN {lo} AND {hi})")
    returns = slots.get('returns') or 'percent'
    if returns == 'count':
        return {'query': f"SELECT count(DISTINCT e.encounter_id) AS index_visits_followed_up FROM visit e WHERE {where} AND {hit}", 'returns': 'count'}
    if returns == 'percent':
        return {'query': f"SELECT 100.0 * count(DISTINCT CASE WHEN {hit} THEN e.encounter_id END) / count(DISTINCT e.encounter_id) "
                         f"AS pct_followed_up FROM visit e WHERE {where}", 'returns': 'value'}
    raise ValueError('returns count|percent')


def build_length_of_stay(slots, as_of, m=None):
    types = _visit_types(slots.get('classes') or ['inpatient'])
    bounds = period_bounds(slots.get('period'), as_of)
    anchor = slots.get('anchor') or 'admission'
    col = {'admission': 'substr(start_time, 1, 10)', 'discharge': 'substr(end_time, 1, 10)'}.get(anchor)
    if not col: raise ValueError('anchor admission|discharge')
    unit = slots.get('unit') or 'days'
    factor = {'days': '1.0', 'hours': '24.0'}.get(unit)
    if not factor: raise ValueError('unit days|hours')
    stat = slots.get('statistic') or 'mean'
    where = _and([f"type IN {qlist(types)}", "status = 'completed'", "start_time IS NOT NULL", "end_time IS NOT NULL", date_clause(col, bounds)])
    los = f"(julianday(end_time) - julianday(start_time)) * {factor}"
    if stat == 'mean':
        return {'query': f"SELECT avg({los}) AS average_length_of_stay FROM visit WHERE {where}", 'returns': 'value'}
    if stat == 'median':
        return {'query': f"SELECT avg(x) AS median_length_of_stay FROM (SELECT {los} AS x, row_number() OVER (ORDER BY {los}) AS rn, "
                         f"count(*) OVER () AS n FROM visit WHERE {where}) WHERE rn IN ((n + 1) / 2, (n + 2) / 2)", 'returns': 'value'}
    raise ValueError('statistic mean|median')


CLAIM_MEASURES = {
    'claims': ('count(*)', 'claims'),
    'first_pass_denied': ("sum(first_status = 'denied')", 'first_pass_denied'),
    'first_pass_denial_rate': ("100.0 * sum(first_status = 'denied') / count(*)", 'first_pass_denial_rate'),
    'ever_denied': ('sum(ever_denied)', 'ever_denied'),
    'ever_denied_rate': ('100.0 * sum(ever_denied) / count(*)', 'ever_denied_rate'),
    'denied_then_paid': ('sum(denied_then_paid)', 'denied_then_paid'),
    'first_pass_denied_then_paid': ("sum(first_status = 'denied' AND denied_then_paid = 1)", 'first_pass_denied_then_paid'),
    'overturn_rate': ('100.0 * sum(denied_then_paid) / nullif(sum(ever_denied), 0)', 'overturn_rate'),
    'first_pass_overturn_rate': ("100.0 * sum(first_status = 'denied' AND denied_then_paid = 1) / nullif(sum(first_status = 'denied'), 0)", 'first_pass_overturn_rate'),
    'net_payer_paid_usd': ('sum(net_payer_paid_cents) / 100.0', 'net_payer_paid_usd'),
    'billed_usd': ('sum(billed_cents) / 100.0', 'billed_usd'),
    'allowed_usd': ('sum(allowed_cents) / 100.0', 'allowed_usd'),
    'avg_days_to_first_submission': ('avg(julianday(first_submitted_date) - julianday(service_date))', 'avg_days_to_first_submission'),
    'avg_days_to_first_payer_cash': ('avg(julianday(first_payer_cash_date) - julianday(service_date))', 'avg_days_to_first_payer_cash'),
    'avg_days_to_first_adjudication': ('avg(julianday(first_adjudication_date) - julianday(service_date))', 'avg_days_to_first_adjudication'),
}
CLAIM_MEASURES['avg_days_to_first_paid'] = ('avg(julianday(first_paid_date) - julianday(service_date))', 'avg_days_to_first_paid')
_CLAIM_DAYS = {'avg_days_to_first_submission': 'first_submitted_date', 'avg_days_to_first_payer_cash': 'first_payer_cash_date',
               'avg_days_to_first_adjudication': 'first_adjudication_date', 'median_days_to_first_payer_cash': 'first_payer_cash_date',
               'avg_days_to_first_paid': 'first_paid_date'}
# Claim clocks a question can name. 'first paid in' is the first PAID adjudication (the payer's payment decision), not the
# cash date; cash/received questions use first_payer_cash_date; 'reversed in' is the date of a reversal adjudication event.
_ADJ = "json_extract(payload, '$.claim_id')"
FIRST_PAID = (f"SELECT {_ADJ} AS claim_id, min(event_date) AS first_paid_date FROM record WHERE stream = 'medical_claims' "
              "AND json_extract(payload, '$.status') = 'paid' GROUP BY 1")
CLAIM_ANCHORS = {
    'service_date': 'the date of service',
    'benefit_year': 'the benefit year (one calendar year)',
    'first_adjudication_date': "the claim's first adjudication date ('adjudicated in', 'first adjudicated in', 'processed in')",
    'first_paid_date': "the claim's first PAID adjudication date ('first paid in', 'paid in'; not the cash date)",
    'first_payer_cash_date': "the first payer cash posting date ('cash received in', 'payment received in', 'cash posted in')",
    'reversal_date': "the date of a reversal adjudication event ('reversed in', 'reversed and reprocessed in')",
}


def claim_anchor_clause(anchor, bounds):
    """WHERE clause for the claim clock a question names (see CLAIM_ANCHORS)."""
    if anchor == 'service_date': return date_clause('service_date', bounds)
    if anchor == 'benefit_year':
        if not bounds[0] or bounds[0][5:] != '01-01' or not bounds[1] or bounds[1][:4] != bounds[0][:4]: raise ValueError('benefit_year anchor needs a period of one calendar year')
        return f"benefit_year = {q(bounds[0][:4])}"
    if anchor in ('first_adjudication_date', 'first_payer_cash_date', 'first_paid_date'):
        return _and([f'{anchor} IS NOT NULL', date_clause(anchor, bounds)])
    if anchor == 'reversal_date':
        when = date_clause('event_date', bounds)
        return (f"claim_id IN (SELECT {_ADJ} FROM record WHERE stream = 'medical_claims' AND json_extract(payload, '$.status') = 'reversed'"
                + (f" AND {when}" if when else '') + ')')
    raise ValueError(f'anchor must be one of {sorted(CLAIM_ANCHORS)}')


def build_medical_claims(slots, as_of, m=None):
    measures = slots.get('measures') or ([slots['measure']] if slots.get('measure') else [])
    allowed = set(CLAIM_MEASURES) | {'median_days_to_first_payer_cash'}
    if not measures or any(x not in allowed for x in measures): raise ValueError(f'measures from {sorted(allowed)}')
    anchor = slots.get('anchor') or 'service_date'
    bounds = period_bounds(slots.get('period'), as_of)
    dclause = claim_anchor_clause(anchor, bounds)
    where = _and([dclause, *[f"{_CLAIM_DAYS[x]} IS NOT NULL" for x in measures if x in _CLAIM_DAYS]])
    source = ('(SELECT c.*, fp.first_paid_date FROM claim c LEFT JOIN (' + FIRST_PAID + ') fp ON fp.claim_id = c.claim_id)'
              if anchor == 'first_paid_date' or 'avg_days_to_first_paid' in measures else 'claim')
    by = slots.get('group_by') or 'none'
    clock = anchor if anchor not in ('benefit_year', 'reversal_date') else 'service_date'
    gexpr = {'none': None, 'payer': 'payer'}.get(by) or _group_expr(clock, by)
    if by != 'none' and not gexpr: raise ValueError('group_by none|payer|month|quarter|year')
    if 'median_days_to_first_payer_cash' in measures:
        if len(measures) > 1 or gexpr: raise ValueError('median is a single ungrouped measure')
        x = 'julianday(first_payer_cash_date) - julianday(service_date)'
        return {'query': f"SELECT avg(x) AS median_days_to_first_payer_cash FROM (SELECT {x} AS x, row_number() OVER (ORDER BY {x}) AS rn, "
                         f"count(*) OVER () AS n FROM {source} WHERE {where}) WHERE rn IN ((n + 1) / 2, (n + 2) / 2)", 'returns': 'value'}
    cols = ', '.join(f'{CLAIM_MEASURES[x][0]} AS {CLAIM_MEASURES[x][1]}' for x in measures)
    if gexpr:
        return {'query': f"SELECT {gexpr} AS {by}, {cols} FROM {source} WHERE {where} GROUP BY 1 ORDER BY 1", 'returns': 'table'}
    returns = 'table' if len(measures) > 1 else ('count' if measures[0] in ('claims', 'first_pass_denied', 'ever_denied', 'denied_then_paid', 'first_pass_denied_then_paid') else 'value')
    return {'query': f"SELECT {cols} FROM {source} WHERE {where}", 'returns': returns}


def _payload(field, alias='r'): return f"json_extract({alias}.payload, '$.{field}')"


def build_record_event(slots, as_of, m=None):
    """A governed record event (metric JSON 'event'): one stream, a fixed filter, a counting key or an amount, a date field,
    named group-by fields. The model fills only period / group_by / measure."""
    ev = (m or {}).get('event') or {}
    if not ev.get('stream'): raise ValueError('record_event metric needs an event definition')
    bounds = period_bounds(slots.get('period'), as_of)
    date_col = ev.get('date', 'r.event_date')
    where = _and([f"r.stream = {q(ev['stream'])}", ev.get('where'), date_clause(date_col, bounds)])
    measure = slots.get('measure') or ev.get('default_measure', 'count')
    exprs = ev.get('measures') or {}
    if measure not in exprs: raise ValueError(f'measure must be one of {sorted(exprs)}')
    by = slots.get('group_by') or 'none'
    groups = ev.get('group_by') or {}
    if by != 'none' and by not in groups: raise ValueError(f"group_by must be none or one of {sorted(groups)}")
    agg = exprs[measure]
    if by != 'none':
        return {'query': f"SELECT {groups[by]} AS {by}, {agg} AS {measure} FROM record r WHERE {where} GROUP BY 1 ORDER BY 1", 'returns': 'table'}
    return {'query': f"SELECT {agg} AS {measure} FROM record r WHERE {where}", 'returns': ev.get('returns', {}).get(measure, 'value')}


FEED_STREAMS = ('labs', 'encounters', 'medical_claims', 'conditions', 'vitals', 'pharmacy_dispenses', 'appointments')


def build_feed_quality(slots, as_of, m=None):
    """Received-feed quality per stream: rows, undated rows, arrival lag (available_date - event_date)."""
    stream = slots.get('stream')
    if stream not in FEED_STREAMS: raise ValueError(f'stream must be one of {FEED_STREAMS}')
    unit = slots.get('unit') or 'rows'
    if unit not in ('rows', 'encounters'): raise ValueError('unit rows|encounters')
    if unit == 'encounters' and stream != 'encounters': raise ValueError('unit encounters applies to the encounters stream')
    bounds = period_bounds(slots.get('period'), as_of)
    basis = slots.get('period_basis') or 'event_date'
    if basis not in ('event_date', 'arrival_date'): raise ValueError('period_basis event_date|arrival_date')
    measures = slots.get('measures') or ([slots['measure']] if slots.get('measure') else ['rows'])
    lag_days = slots.get('lag_days', 30)
    lag_op = slots.get('lag_op') or 'gt'
    if lag_op not in ('gt', 'ge') or not isinstance(lag_days, int): raise ValueError('lag_op gt|ge and integer lag_days')
    if unit == 'encounters':
        base = (f"SELECT json_extract(payload, '$.encounter_id') AS k, min(event_date) AS d, min(available_date) AS a, "
                f"min(json_extract(payload, '$.source_system')) AS src FROM record WHERE stream = 'encounters' GROUP BY 1")
    else:
        base = (f"SELECT record_id AS k, event_date AS d, available_date AS a, json_extract(payload, '$.source_system') AS src "
                f"FROM record WHERE stream = {q(stream)}")
    lag = 'julianday(a) - julianday(d)'
    cmp = OPS[lag_op]
    M = {'rows': 'count(*)', 'undated_rows': 'sum(d IS NULL)', 'pct_undated': '100.0 * sum(d IS NULL) / count(*)',
         'avg_lag_days': f'avg(CASE WHEN d IS NOT NULL THEN {lag} END)',
         'late_rows': f'sum(d IS NOT NULL AND {lag} {cmp} {lag_days})',
         'pct_late': f'100.0 * sum(d IS NOT NULL AND {lag} {cmp} {lag_days}) / sum(d IS NOT NULL)'}
    if any(x not in M for x in measures): raise ValueError(f'measures from {sorted(M)}')
    dclause = date_clause('d' if basis == 'event_date' else 'a', bounds)
    by = slots.get('group_by') or 'none'
    gexpr = {'none': None, 'source_system': 'src', 'year': "CAST(substr(d, 1, 4) AS INTEGER)"}.get(by, 'x')
    if gexpr == 'x': raise ValueError('group_by none|source_system|year')
    cols = ', '.join(f'{M[x]} AS {x}' for x in measures)
    where = f" WHERE {dclause}" if dclause else ''
    if gexpr:
        return {'query': f"SELECT {gexpr} AS {by}, {cols} FROM ({base}){where} GROUP BY 1 ORDER BY 1", 'returns': 'table'}
    returns = 'table' if len(measures) > 1 else ('count' if measures[0] in ('rows', 'undated_rows', 'late_rows') else 'value')
    return {'query': f"SELECT {cols} FROM ({base}){where}", 'returns': returns}


BUILDERS = {'cohort': build_cohort, 'encounter_volume': build_encounter_volume, 'visit_followup': build_visit_followup,
            'length_of_stay': build_length_of_stay, 'medical_claims': build_medical_claims, 'record_event': build_record_event,
            'feed_quality': build_feed_quality}


def build(metric, slots, as_of):
    name = metric.get('builder')
    if name not in BUILDERS: raise ValueError(f'unknown builder {name!r}')
    return BUILDERS[name](slots, as_of, metric)
