"""flow_decomposition: stage-level decomposition of ED and inpatient flow with censoring-aware percentiles and bottleneck attribution (wave 2).

Stages come from the governed visit_group clocks. Open stays are right-censored at the snapshot (Kaplan-Meier percentiles), never
dropped and never treated as finished. Stages whose clock pair was not received are counted as missing, not as zero.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta

from . import hospital_ops as H

OP = 'flow_decomposition'
VERSION = 'flow-decomposition/1'
STAGES = {  # name -> (start clock, end clock, visit classes, description)
    'ed_arrival_to_departure': ('ed_arrival', 'ed_departure', ('emergency',), 'ED length of stay, treat-and-release visits'),
    'ed_arrival_to_admit_decision': ('ed_arrival', 'admit_decision', ('ed_to_inpatient', 'ed_to_observation'), 'ED work-up until the admit decision (ADT class change)'),
    'admit_decision_to_inpatient_admit': ('admit_decision', 'inpatient_admit', ('ed_to_inpatient', 'ed_to_observation'), 'boarding: decision to admit until the inpatient start'),
    'ed_arrival_to_inpatient_admit': ('ed_arrival', 'inpatient_admit', ('ed_to_inpatient', 'ed_to_observation'), 'door to inpatient start for admitted ED patients'),
    'inpatient_los': ('inpatient_admit', 'discharge_time', ('ed_to_inpatient', 'direct_inpatient', 'observation_to_inpatient', 'observation', 'ed_to_observation'),
                      'inpatient / observation stay until discharge (open stays censored at the snapshot)'),
}
GROUPS = ('none', 'facility', 'month', 'visit_class')
DEFAULTS = {'facility': None, 'months': None, 'start': None, 'end': None, 'group_by': 'none', 'stages': []}
SPEC = {'facility': ('str',), 'months': ('int', 1, 120), 'start': ('date',), 'end': ('date',), 'group_by': ('enum', GROUPS),
        'stages': ('list_enum', tuple(STAGES))}
PRODUCT = {'population': 'optional', 'view': 'patient_flow',
           'use': 'PATIENT FLOW DECOMPOSITION: ED length of stay, door to admit decision, boarding (decision to inpatient start), door to inpatient and '
                  'inpatient LOS by stage, with median / 90th percentile that handle still-open stays, the bottleneck stage, by facility, month or visit class.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'facility': None, 'months': None, 'group_by': '|'.join(GROUPS), 'stages': list(STAGES), 'start': None, 'end': None},
            'meaning': 'Stage-interval decomposition of hospital flow from visit_group clocks: n, missing clocks, censored stays, Kaplan-Meier median and 90th '
                       'percentile hours per stage, share of the door-to-discharge path and the bottleneck stage.'}
NATIVE = ('count', 'value')


def schema(obj, string, array): return H.spec_schema(SPEC, obj, string, array)
def validate(p): return H.spec_validate(OP, DEFAULTS, SPEC, p)


def intervals(r, name, as_of_dt):
    """-> (hours, observed, precision) or None when the clock pair was not received."""
    a_key, b_key, _, _ = STAGES[name]
    a = H.ts(r[a_key]) if r.get(a_key) else None
    b = H.ts(r[b_key]) if r.get(b_key) else None
    if name == 'inpatient_los':
        if a is None and r.get('has_inpatient') or a is None and r.get('has_observation'): a = H.ts(None, r['start_date'])
        if b is None and r.get('discharge_known') and r.get('end_date'): b = H.ts(None, r['end_date'])
        if a is None: return None
        if b is None:
            return (max(H.hours(a, as_of_dt), 0.0), False, 'censored') if not r.get('discharge_known') else None
        return (H.hours(a, b), True, 'minute' if H.timed(r[a_key] or '') and H.timed(r[b_key] or '') else 'date')
    if a is None or b is None: return None
    h = H.hours(a, b)
    return (h, True, 'minute') if h is not None and h >= 0 else None


def execute(path, patient_ids, params, as_of):
    p = validate(params)
    db, snap = H.connect(path)
    try:
        as_of = str(as_of or snap)[:10]
        rows, basis, (start, end), fac, notes = H.visits_in(db, as_of, p, patient_ids)
    finally:
        db.close()
    as_of_dt = datetime.fromisoformat(as_of + 'T00:00:00') + timedelta(days=1)
    names = p['stages'] or list(STAGES)
    gkey = {'none': lambda r: 'all', 'facility': lambda r: r['organization_id'] or 'unknown facility', 'month': lambda r: r['start_date'][:7],
            'visit_class': lambda r: r['visit_class']}[p['group_by']]
    cells = defaultdict(lambda: {'dur': [], 'obs': [], 'eligible': 0, 'prec': defaultdict(int)})
    for r in rows:
        g = gkey(r)
        for n in names:
            if r['visit_class'] not in STAGES[n][2]: continue
            c = cells[(g, n)]; c['eligible'] += 1
            iv = intervals(r, n, as_of_dt)
            if iv is None: continue
            c['dur'].append(iv[0]); c['obs'].append(iv[1]); c['prec'][iv[2]] += 1
    table = []
    for (g, n), c in sorted(cells.items()):
        if not c['eligible']: continue
        q = H.km_quantiles(c['dur'], c['obs'], (0.5, 0.9)) if c['dur'] else {0.5: None, 0.9: None}
        done = [d for d, o in zip(c['dur'], c['obs']) if o]
        table.append({**({p['group_by']: g} if p['group_by'] != 'none' else {}), 'stage': n, 'eligible_visits': c['eligible'], 'with_clocks': len(c['dur']),
                      'missing_clock_pair': c['eligible'] - len(c['dur']), 'censored_open': len(c['dur']) - len(done),
                      'median_hours': None if q[0.5] is None else round(q[0.5], 2), 'p90_hours': None if q[0.9] is None else round(q[0.9], 2),
                      'mean_hours_completed': None if not done else round(sum(done) / len(done), 2), 'minute_precision': c['prec'].get('minute', 0),
                      'date_precision': c['prec'].get('date', 0)})
    # bottleneck: the larger of ED work-up and boarding (the sequential ED stages of admitted patients) by median
    seq = [t for t in table if t['stage'] in ('ed_arrival_to_admit_decision', 'admit_decision_to_inpatient_admit') and t['median_hours'] is not None]
    pool = defaultdict(list)
    for t in seq: pool[t.get(p['group_by'], 'all')].append(t)
    bott = {}
    for g, ts_ in pool.items():
        tot = sum(t['median_hours'] for t in ts_) or 1
        top = max(ts_, key=lambda t: t['median_hours'])
        bott[g] = {'stage': top['stage'], 'share_of_path': round(top['median_hours'] / tot, 3)}
        for t in ts_: t['share_of_path'] = round(t['median_hours'] / tot, 3)
    miss = sum(t['missing_clock_pair'] for t in table)
    receipt = {'count': len(rows), 'visits': len(rows), 'stages': len({t['stage'] for t in table}), 'cells': len(table), 'bottleneck': bott,
               'missing_clock_pairs': miss, 'censored_open': sum(t['censored_open'] for t in table)}
    if not table: notes.append('No visits with the requested stages in the window.')
    return {'schema': VERSION, 'status': 'computed' if table else 'unknown', 'as_of': as_of, 'parameters': p, 'window': [start, end], 'facility': fac,
            'basis': basis, 'table': table, 'receipt': receipt, 'notes': notes, 'patient_ids': sorted({r['patient_id'] for r in rows}),
            'method': ('Stage intervals from visit_group clocks (ED arrival, admit decision, inpatient admit, discharge); Kaplan-Meier median / p90 with open '
                       'stays right-censored at the snapshot; bottleneck = the larger of ED work-up and boarding by median (inpatient LOS is reported beside, not ranked against ED stages).'),
            'assumptions': ['Triage, provider-seen, bed-assigned and ED-departure-after-admission clocks are not received; the ED stage of an admitted patient '
                            'ends at the admit decision (ADT A06) and boarding is decision to inpatient start where both clocks exist.',
                            'Date-only sources have no clock: stages that need minutes use only clocked visits; inpatient LOS falls back to date differences '
                            '(date_precision column).',
                            'Admit-decision clocks are received for a minority of admitted ED visits; missing_clock_pair says how many.']}


def headline(r):
    tb = [t for t in r['table'] if t['median_hours'] is not None]
    parts = ', '.join(f"{t['stage'].replace('_', ' ')} median {t['median_hours']}h (p90 {t['p90_hours']}h, n={t['with_clocks']})" for t in tb[:5])
    b = next(iter(r['receipt']['bottleneck'].values()), None)
    return parts + (f"; bottleneck {b['stage'].replace('_', ' ')} ({b['share_of_path']:.0%} of the path)" if b else '')


def answer(report, plan, question):
    if report['status'] != 'computed':
        return {'status': 'unknown', 'answer_type': 'text', 'value': ' '.join(report['notes']), 'method': report['method'], 'limitations': []}
    return H.generic_answer(OP, report, plan, headline(report) + '.')


FLOW = (r'\b(?:patient[- ]flow|flow decomposition|door[- ]to[- ](?:\w+[- ]?)?(?:time|decision|admit|bed|inpatient|provider)|boarding (?:time|hours)|ed (?:length of stay|los|boarding)|'
        r'length of stay (?:by|in) (?:stage|the ed)|time (?:in|spent in) the ed|where (?:is|are) (?:the )?(?:bottleneck|delay)s?|bottlenecks? in (?:our )?(?:flow|ed|admissions?)|'
        r'decision[- ]to[- ](?:admit|inpatient)|ed throughput|ed dwell)\b')


def route(question, profile, as_of):
    from .engine_suite import plan
    low = ' '.join(str(question).lower().split())
    if not re.search(FLOW, low) or H.is_definition(low): return None
    if re.search(r'\bif we (?:open|add)\b|\bsimulat', low): return None  # capacity twin
    params = H.common_params(low)
    if re.search(r'\bby (?:facility|hospital|site)\b|\bper (?:facility|hospital)\b', low): params['group_by'] = 'facility'
    elif re.search(r'\bby month\b|\bmonthly\b|\bper month\b', low): params['group_by'] = 'month'
    elif re.search(r'\bby (?:visit )?class\b', low): params['group_by'] = 'visit_class'
    st = []
    if re.search(r'\bboarding\b|\bdecision[- ]to[- ](?:admit|inpatient)\b', low): st.append('admit_decision_to_inpatient_admit')
    if re.search(r'\bdoor[- ]to[- ](?:decision|admit)', low): st += ['ed_arrival_to_admit_decision']
    if re.search(r'\bed (?:length of stay|los)\b|\btime in the ed\b|\btreat[- ]and[- ]release\b', low): st.append('ed_arrival_to_departure')
    if re.search(r'\binpatient (?:length of stay|los)\b|\blos by stage\b', low): st.append('inpatient_los')
    if st: params['stages'] = sorted(set(st), key=list(STAGES).index)
    return plan(OP, params, 'Stage-level patient-flow decomposition from governed visit clocks', question, ['flow decomposition'])


TEMPLATES = ['What is the median and 90th percentile ED boarding time for admitted patients?',
             'where are the bottlenecks in our patient flow by facility',
             'ED length of stay by month for treat-and-release visits',
             'decompose door-to-admit time into ED work-up and boarding',
             'patient flow decomposition for hospital 2 in 2029']
