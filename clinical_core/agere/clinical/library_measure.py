"""Any measure-library measure (config/agere/measures, measures.py) as a registered engine with exact routing.

The behavioural-health measures (FUH 7/30, FUM 7/30, IET initiation/engagement) and every other library measure answer
plain rate questions ("What is our FUH 7-day follow-up rate?", "IET initiation rate by practice") from the measure file
itself: measures.match finds the longest alias, and the question is claimed only when nothing but the alias, a period, a
grouping and rate/gap wording remains. The answer names the measure, its certification/status, numerator, denominator,
exclusions and the follow-up window (follow_up_days, read from the numerator windows). Declined measures (an input absent
from the received data) answer unsupported with the missing input named. Synthetic data only; no model calls.
"""
import datetime as dt
import re
import sqlite3
from pathlib import Path

VERSION = 'agere-library-measure/2'
OUTPUTS = ('rate', 'gaps', 'patients')
GROUP_BY = ('none', 'pcp', 'practice', 'coverage_type', 'payer', 'age_band', 'sex', 'race', 'ethnicity', 'language')

PRODUCT = {'population': 'optional', 'view': 'table',
           'use': 'MEASURE LIBRARY rate for one named measure file (HEDIS/CMS approximations incl. behavioural health FUH 7/30-day, '
                  'FUM 7/30-day after ED visits for mental illness on the governed ED definition, IET initiation/engagement): '
                  'numerator, denominator, rate, exclusions, care-gap patient list, by practice/PCP/payer.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'measure': 'measure-library id', 'period': {'start': '', 'end': ''}, 'group_by': 'none|practice|pcp|payer',
                           'output': 'rate|gaps|patients', 'follow_up_days': 7},
            'meaning': 'Evaluate one measure-library measure exactly as its file defines it (citation, spec year, certification '
                       'status). Use for named quality measures such as FUH, FUM, IET when the question asks their rate or gaps.'}
NATIVE_SCALARS = ('count', 'value')


def parameter_schema(catalog, obj, string, array):
    from . import measures
    return obj({'measure': string(sorted(measures.library()), 'Measure-library id'),
                'period': obj({'start': {'type': 'string'}, 'end': {'type': 'string'}}),
                'group_by': string(GROUP_BY), 'output': string(OUTPUTS),
                'follow_up_days': {'type': ['integer', 'null'], 'description': 'Follow-up window the measure file states (informational)'}})


def follow_up_days(m):
    """The follow-up window in days the measure states: 'N-day' / 'N days' in its name, else the latest numerator window end
    (index+13d counts the index day: 14 days)."""
    import json
    named = re.search(r'(\d+)[- ]days?\b', m.get('name') or '')
    if named: return int(named.group(1))
    ends = [(int(n), base) for base, n in re.findall(r'"end": "(index(?:_end)?)\+(\d+)d"', json.dumps(m.get('numerator') or {}))]
    if not ends: return None
    n, base = max(ends)
    return n + (1 if base == 'index' else 0)


def validate(p):
    from . import measures
    if not isinstance(p, dict) or set(p) - {'measure', 'period', 'group_by', 'output', 'follow_up_days'}:
        raise ValueError('library_measure parameters: measure, period, group_by, output, follow_up_days')
    if p.get('measure') not in measures.library(): raise ValueError('measure: a measure-library id')
    period = p.get('period') or {}
    if not isinstance(period, dict) or set(period) - {'start', 'end'} or any(
            v and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(v)) for v in period.values()):
        raise ValueError('period: {start, end} as YYYY-MM-DD')
    for value in period.values():
        if value:dt.date.fromisoformat(value)
    if period.get('start') and period.get('end') and period['start']>period['end']:
        raise ValueError('Measure period is reversed')
    if p.get('group_by', 'none') not in GROUP_BY: raise ValueError(f'group_by: {GROUP_BY}')
    if p.get('output', 'rate') not in OUTPUTS: raise ValueError(f'output: {OUTPUTS}')
    return {'measure': p['measure'], 'period': {'start': period.get('start', ''), 'end': period.get('end', '')},
            'group_by': p.get('group_by', 'none'), 'output': p.get('output', 'rate'),
            'follow_up_days': follow_up_days(measures.library()[p['measure']])}


def execute(path, patient_ids, params, as_of):
    from . import measures, semantic_layer
    p = validate(params)
    start, end = p['period']['start'], p['period']['end']
    if not start or not end:
        s0, e0 = measures.default_period(str(as_of)[:10])
        start, end = start or s0, end or e0
    if start>end:raise ValueError('Measure period is reversed')
    if end>str(as_of)[:10]:raise ValueError('Measure period exceeds the snapshot cutoff')
    target = semantic_layer.ensure(path) if (Path(path)/'run.json').exists() else Path(path)/'semantic.sqlite3'
    db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    try:
        from .age import register
        register(db)
        m = measures.library()[p['measure']]
        rep = measures.evaluate_db(db, m, start[:10], end[:10], str(as_of)[:10], p['group_by'],
                                   set(patient_ids) if patient_ids is not None else None, output=p['output'],top=None)
    finally:
        db.close()
    return {**rep, 'parameters': p, 'period': {'start': start[:10], 'end': end[:10]}, 'measure_id': p['measure'],
            'measure_name': m['name'], 'measure_status': m['status'], 'certification': m.get('certification'), 'version': VERSION}


def answer(report, plan, question):
    cite = '; '.join(c['url'] for c in report.get('citations') or [])
    used = f"Measure used ({report['measure_name']}, {report['measure_status']}): {report.get('certification') or ''}".strip()
    if report.get('declined') and not report.get('partial'):
        return {'status': 'unsupported', 'answer_type': 'unsupported', 'value': report['reading'], 'headline': report['reading'],
                'missing': report.get('missing_inputs') or [], 'patient_ids': [], 'limitations': [used]}
    o = report['overall']
    gaps = sorted({g['patient_id'] for g in report.get('care_gaps') or []})
    output=report['parameters']['output']
    table=(report.get('patient_rows') or []) if output=='patients' else report['rows']
    ids=sorted({r['patient_id'] for r in table if r.get('patient_id')}) if output=='patients' else gaps
    partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements') or report.get('partial')
    missing=list(report.get('missing_inputs') or [])
    complete_projection=True
    if output=='patients' and 'patient_rows' not in report:
        table=report['rows']
        partial=True
        complete_projection=False
        missing=['Complete patient-status rows were not computed for this measure output; aggregate observations are retained.']
    out = {'status': 'partial' if partial else 'answered', 'answer_type': 'table', 'table': table,
           'value': report['reading'], 'headline': report['reading'], 'method': report.get('method'),
           'missing':missing,
           'definitions': [{'term': report['measure_name'], 'basis': report.get('method'), 'citations': cite}],
           'limitations': [used, *(report.get('warnings') or [])][:12], 'patient_ids': ids,
           'membership_role': 'evaluated' if output=='patients' else 'gap',
           'denominator': f"{o.get('denominator')} eligible index events/patients" if 'denominator' in o else None,
           'measure': {'id': report['measure_id'], 'numerator': o.get('numerator'), 'denominator': o.get('denominator'),
                       'rate': o.get('rate'), 'excluded': o.get('excluded'), 'follow_up_days': report['parameters']['follow_up_days']}}
    if report['parameters']['output'] == 'rate' and o.get('rate') is not None and report['parameters']['group_by'] == 'none':
        out['value'] = round(o['rate'] * 100, 1); out['unit'] = 'percent'
    if output != 'rate': out['count'] = len(ids) if complete_projection else None
    return out


# ---- exact deterministic routing ------------------------------------------------------------------------------------
_FILLER = set('''what is are was were the our we did do does have had has there of a an in for all during to how
our s my show me give tell overall current measure score performance result results open'''.split())


def local_question(question, as_of):
    from . import measures
    from .metrics import period_for
    mid, alias = measures.match(question)
    if not mid or mid not in measures.measure_sets().get('behavioral_health', {}).get('measures', ()): return None  # other measures keep their routes
    stated = re.search(r'\b(?:within )?(\d+)[- ]days?\b', question.lower())
    if stated and follow_up_days(measures.library()[mid]) != int(stated.group(1)):  # FUH/FUM 7 vs 30: the window the question states
        stem = re.sub(r'_\d+day$', '', mid)
        sibling = next((s for s in measures.measure_sets()['behavioral_health']['measures']
                        if re.sub(r'_\d+day$', '', s) == stem and follow_up_days(measures.library()[s]) == int(stated.group(1))), None)
        if sibling is None: return None
        mid = sibling
    t = ' ' + re.sub(r'[?.!,;:()]', ' ', question.lower()) + ' '
    t = re.sub(r'(?<![a-z0-9])' + re.escape(alias.lower()) + r'(?![a-z0-9])', ' ', t, count=1)
    params = {'measure': mid, 'group_by': 'none', 'output': 'rate'}
    def take(rx, fn=None):
        nonlocal t
        mm = re.search(rx, t)
        if not mm: return
        if fn: fn(mm)
        t = t[:mm.start()] + ' ' + t[mm.end():]
    take(r'\bby (practice|pcp|payer|sex|race|ethnicity|language)\b', lambda mm: params.update(group_by=mm.group(1)))
    take(r'\b(?:which|list(?: the)?|who are the) (?:patients|members|people)\b(?: (?:with|in) (?:an? )?(?:open )?(?:care )?gaps?| missed| who missed| not meeting)?|'
         r'\b(?:open )?care gaps?\b|\bgaps?\b', lambda mm: params.update(output='gaps'))
    take(r'\b(?:open )?(?:care )?gaps?\b', lambda mm: params.update(output='gaps'))
    take(r'\b(?:in|during|for) (20\d\d)\b|\b(?:in |during |over )?(?:the )?(?:last|past|trailing|prior|previous) (?:12|twelve) months\b|'
         r'\b(?:in |during )?(?:the )?(?:last|past) year\b|\b(?:so far )?this year\b|\byear[- ]to[- ]date\b|\bytd\b|\b(20\d\d)\b')
    take(r'\b(?:follow[- ]?up |initiation |engagement |compliance |completion )?(?:rate|percentage|percent|share|proportion)\b')
    take(r'\bhedis\b|\bmeasure\b')
    take(r'\b(?:had|with|got|received)(?: an?)? (?:follow[- ]?up|treatment initiation|initiation)\b')
    if mid.startswith('iet_'): take(r'\b(?:for|among|in) (?:patients with )?(?:an? )?(?:alcohol|substance|sud)(?: (?:or|and) (?:other )?drug)?(?: use)?(?: disorders?)?\b')
    days = follow_up_days(measures.library()[mid])
    if days: take(rf'\b{days}[- ]?days?\b|\bwithin {days} days\b')
    left = [w for w in t.split() if w not in _FILLER]
    if left: return None
    if re.search(r'\b(?:last|past|trailing|this|ytd|year[- ]to[- ]date|20\d\d)\b', question.lower()):
        start, end, _ = period_for(question, as_of or dt.date.today().isoformat(), default='as_of_year')
        params['period'] = {'start': start.isoformat(), 'end': end.isoformat()}
    elif as_of:  # no stated period: the measure's default measurement period, written into the plan so it is explicit
        from . import measures as _m
        s0, e0 = _m.default_period(str(as_of)[:10])
        params['period'] = {'start': str(s0)[:10], 'end': str(e0)[:10]}
    from .foundation_engines import graph
    return graph(question, 'library_measure', validate(params), f'Measure library: {mid} ({VERSION})')
