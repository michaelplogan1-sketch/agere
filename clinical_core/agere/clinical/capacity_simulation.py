"""capacity_simulation: discrete-event simulation of inpatient beds and ED boarding with what-if scenarios (wave 2, hospital ops).

Twin = empirical admission arrivals by day of week (Poisson), empirical length-of-stay draws, a fixed bed count, FIFO bed assignment.
An admitted patient who finds no free bed waits (boards in the ED when ED-origin) until the first discharge. Scenarios change beds,
LOS and admissions with common random numbers (same seed) so differences are the scenario, not noise.

Inputs not received are modelled from what exists and stated in every answer: staffed bed counts (beds = stated or the 95th-percentile
daily census observed in the window), bed-assignment clocks (boarding is simulated, never read), ED arrival curve (admission origin
share and day-of-week rates come from the governed visit_group table).
"""
from __future__ import annotations

import heapq
import math
import re
from collections import Counter, defaultdict
from datetime import timedelta

import numpy as np

from . import hospital_ops as H

OP = 'capacity_simulation'
VERSION = 'capacity-simulation/1'
DEFAULTS = {'facility': None, 'months': 12, 'start': None, 'end': None, 'beds': None, 'add_beds': 0, 'los_change_pct': 0.0,
            'admission_change_pct': 0.0, 'horizon_days': 365, 'replications': 30, 'sensitivity': True}
SPEC = {'facility': ('str',), 'months': ('int', 1, 120), 'start': ('date',), 'end': ('date',), 'beds': ('int', 1, 5000),
        'add_beds': ('int', -2000, 2000), 'los_change_pct': ('num', -90, 300), 'admission_change_pct': ('num', -90, 500),
        'horizon_days': ('int', 30, 1500), 'replications': ('int', 2, 200), 'sensitivity': ('bool',)}
PRODUCT = {'population': 'optional', 'view': 'capacity_what_if',
           'use': 'CAPACITY WHAT-IF by simulation: inpatient beds and ED boarding from empirical admissions and length of stay - what happens to '
                  'boarding hours, occupancy and days at capacity if beds are added, LOS shortens or admissions grow; bed-count sensitivity.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'facility': None, 'months': 12, 'beds': None, 'add_beds': 0, 'los_change_pct': 0, 'admission_change_pct': 0,
                           'horizon_days': 365, 'replications': 30, 'sensitivity': True, 'start': None, 'end': None},
            'meaning': 'Discrete-event capacity twin fitted to the governed visit_group table (day-of-week admission rates, LOS draws, ED-origin '
                       'share); baseline vs scenario with common random numbers; boarding hours, occupancy, days at capacity and a bed sensitivity curve.'}
NATIVE = ('count', 'value')


def schema(obj, string, array): return H.spec_schema(SPEC, obj, string, array)
def validate(p): return H.spec_validate(OP, DEFAULTS, SPEC, p)


# ---- twin ---------------------------------------------------------------------------------------------------------------------
def fit(rows, start, end):
    """Admission rates by weekday, LOS hours, ED-origin share, observed census and bed estimate from governed visits."""
    adm = [r for r in rows if r['has_inpatient'] or r['has_observation']]
    s, e = H.d(start or min((r['start_date'] for r in adm), default=end)), H.d(end)
    days = max((e - s).days + 1, 1)
    dow_days = Counter((s + timedelta(n)).weekday() for n in range(days))
    cnt = Counter(H.d(r['start_date']).weekday() for r in adm)
    rates = [cnt.get(k, 0) / max(dow_days.get(k, 0), 1) for k in range(7)]
    los = []
    for r in adm:
        if not r['discharge_known']: continue
        h = r['los_hours'] if r['los_hours'] is not None else (None if r['los_days'] is None else r['los_days'] * 24 + 12)
        if h is not None and h > 0: los.append(float(h))
    delta = defaultdict(int)
    allv = [r for r in rows if r['has_inpatient'] or r['has_observation']]
    for r in allv:  # every visit overlapping the window counts, including ones that started before it
        a = H.d(r['start_date']); b = H.d(r['end_date']) if r['end_date'] and r['discharge_known'] else e
        if a is None or b is None or b < s or a > e: continue
        a, b = max(a, s), min(b, e)
        delta[a] += 1; delta[b + timedelta(1)] -= 1
    census, cur = [], 0
    for n in range(days):
        cur += delta.get(s + timedelta(n), 0); census.append(cur)
    ed_share = sum(r['visit_class'].startswith('ed_to') for r in adm) / len(adm) if adm else 0.0
    return {'rates': rates, 'los_hours': los, 'ed_share': ed_share, 'admissions': len(adm), 'census': census, 'days': days,
            'mean_los_days': (sum(los) / len(los) / 24) if los else None}


def simulate(rng, days, rates, los_hours, beds, ed_share, warm=30, start_dow=0, initial=(), adm_mult=1.0, los_mult=1.0):
    """One replication -> dict of arrays. `initial` = remaining hours for patients already in house at t=0."""
    cap = max(int(beds), len(initial), 1)
    free = [-1e9] * (cap - len(initial)) + [float(x) for x in initial]
    heapq.heapify(free)
    los = np.asarray(los_hours, dtype=float)
    recs = []
    for dd in range(-warm, days):
        n = rng.poisson(max(rates[(start_dow + dd) % 7] * adm_mult, 0.0))
        if not n: continue
        t = np.sort(dd * 24 + rng.uniform(0, 24, n)); draws = rng.choice(los, n) * los_mult; ed = rng.random(n) < ed_share
        for a, l, e in zip(t, draws, ed):
            f = heapq.heappop(free); st = max(a, f); heapq.heappush(free, st + l)
            recs.append((a, st, st + l, st - a, e))
    arr = np.array(recs) if recs else np.zeros((0, 5))
    return arr


def metrics(arr, days, beds):
    keep = arr[(arr[:, 0] >= 0) & (arr[:, 0] < days * 24)] if len(arr) else arr
    ed = keep[keep[:, 4] == 1] if len(keep) else keep
    wait = ed[:, 3] if len(ed) else np.zeros(0)
    grid = np.arange(0, days) * 24.0 + 12
    census = np.array([int(np.sum((arr[:, 1] <= t) & (arr[:, 2] > t))) for t in grid]) if len(arr) else np.zeros(days, int)
    disch = np.array([int(np.sum((arr[:, 2] >= d * 24) & (arr[:, 2] < d * 24 + 24))) for d in range(days)]) if len(arr) else np.zeros(days, int)
    return {'ed_admissions': int(len(ed)), 'boarding_mean_h': float(wait.mean()) if len(wait) else 0.0,
            'boarding_p90_h': float(np.quantile(wait, 0.9)) if len(wait) else 0.0, 'boarded_share': float((wait > 0.5).mean()) if len(wait) else 0.0,
            'occupancy': float(census.mean() / beds), 'census_p95': float(np.quantile(census, 0.95)), 'days_at_capacity': float((census >= beds).mean()),
            'boarding_hours_total': float(wait.sum()), 'discharges': disch, 'census': census}


def scenario(twin, beds, reps, days, seed=20301, adm_mult=1.0, los_mult=1.0, warm=30):
    out = defaultdict(list)
    for i in range(reps):
        rng = np.random.default_rng(seed + i)
        m = metrics(simulate(rng, days, twin['rates'], twin['los_hours'], beds, twin['ed_share'], warm, 0, (), adm_mult, los_mult), days, beds)
        for k, v in m.items():
            if k not in ('discharges', 'census'): out[k].append(v)
    return {k: (round(float(np.mean(v)), 4), round(float(np.std(v, ddof=1)) if len(v) > 1 else 0.0, 4)) for k, v in out.items()}


def execute(path, patient_ids, params, as_of):
    p = validate(params)
    db, snap = H.connect(path)
    try:
        as_of = str(as_of or snap)[:10]
        rows, basis, (start, end), fac, notes = H.visits_in(db, as_of, p, patient_ids)
    finally:
        db.close()
    twin = fit(rows, start, end)
    base = {'status': 'insufficient', 'reason': 'No inpatient or observation admissions with a known length of stay in the window.'}
    if not twin['los_hours'] or not sum(twin['rates']):
        return {'schema': VERSION, 'status': 'unknown', 'as_of': as_of, 'parameters': p, 'window': [start, end], 'facility': fac, 'basis': basis,
                'table': [], 'receipt': {'count': 0, 'admissions': twin['admissions']}, 'notes': notes + [base['reason']],
                'method': 'discrete-event capacity twin', 'assumptions': [], 'patient_ids': []}
    est = int(math.ceil(float(np.quantile(twin['census'], 0.95)))) if twin['census'] else 1
    beds0 = p['beds'] or max(est, 1)
    if not p['beds']: notes.append(f'Staffed bed count is not received; baseline beds = {beds0}, the 95th-percentile daily census observed in the window.')
    beds1 = max(beds0 + p['add_beds'], 1)
    los_mult, adm_mult = 1 + p['los_change_pct'] / 100, 1 + p['admission_change_pct'] / 100
    days, reps = p['horizon_days'], p['replications']
    b = scenario(twin, beds0, reps, days)
    s = scenario(twin, beds1, reps, days, adm_mult=adm_mult, los_mult=los_mult)
    changed = (beds1, adm_mult, los_mult) != (beds0, 1.0, 1.0)
    labels = {'boarding_mean_h': 'mean ED boarding hours per ED-origin admission', 'boarding_p90_h': '90th percentile boarding hours',
              'boarded_share': 'share of ED admissions that waited for a bed', 'occupancy': 'mean bed occupancy',
              'census_p95': '95th percentile daily census', 'days_at_capacity': 'share of days at capacity'}
    table = [{'metric': labels[k], 'baseline': b[k][0], 'baseline_sd': b[k][1], **({'scenario': s[k][0], 'scenario_sd': s[k][1],
              'change': round(s[k][0] - b[k][0], 4)} if changed else {})} for k in labels]
    sens = []
    if p['sensitivity']:
        for add in sorted({-4, 0, 2, 4, 8, 12, 16, p['add_beds']}):
            bb = max(beds0 + add, 1); m = scenario(twin, bb, max(reps // 3, 3), days, adm_mult=adm_mult, los_mult=los_mult)
            sens.append({'beds': bb, 'add_beds': add, 'boarding_mean_h': m['boarding_mean_h'][0], 'days_at_capacity': m['days_at_capacity'][0],
                         'occupancy': m['occupancy'][0]})
    receipt = {'count': twin['admissions'], 'admissions_fitted': twin['admissions'], 'los_samples': len(twin['los_hours']),
               'mean_los_days': None if twin['mean_los_days'] is None else round(twin['mean_los_days'], 2), 'ed_origin_share': round(twin['ed_share'], 3),
               'admissions_per_day': round(sum(twin['rates']) / 7, 3), 'beds_baseline': beds0, 'beds_scenario': beds1, 'replications': reps,
               'horizon_days': days, 'scenario_changed': changed}
    return {'schema': VERSION, 'status': 'computed', 'as_of': as_of, 'parameters': p, 'window': [start, end], 'facility': fac, 'basis': basis,
            'table': table, 'scenarios': {'bed_sensitivity': sens} if sens else None, 'receipt': receipt, 'notes': notes, 'patient_ids': [],
            'method': ('Discrete-event simulation: Poisson admissions by weekday from the window, empirical LOS draws, FIFO beds, ED-origin patients '
                       'board until a bed frees; replications share seeds across baseline and scenario (common random numbers); 30 warm-up days dropped.'),
            'assumptions': ['Admissions are inpatient and observation visits of the visit_group table; ED-origin = ED-to-inpatient / ED-to-observation.',
                            'Date-only stays use LOS = days x 24h + 12h; clocked stays use the received hours.',
                            'Arrival hour within a day is uniform (no hourly arrival clock is received for inpatient admissions).',
                            'Bed assignment clocks are not received, so boarding is a simulated outcome of bed supply, not a measurement.']}


def headline(r):
    rc = r['receipt']
    rows = {t['metric']: t for t in r['table']}
    bm = rows['mean ED boarding hours per ED-origin admission']
    txt = (f"With {rc['beds_baseline']} beds the twin shows {bm['baseline']} mean boarding hours per ED admission "
           f"({rows['share of days at capacity']['baseline']:.0%} of days at capacity)")
    if rc['scenario_changed']:
        txt += f"; scenario with {rc['beds_scenario']} beds: {bm['scenario']} hours ({bm['change']:+.2f})"
    return txt + f" from {rc['admissions_fitted']} fitted admissions"


def answer(report, plan, question):
    if report['status'] != 'computed':
        return {'status': 'unknown', 'answer_type': 'text', 'value': ' '.join(report['notes']), 'method': report['method'], 'limitations': []}
    return H.generic_answer(OP, report, plan, headline(report) + '.')


CAP = (r'\b(?:capacity (?:simulation|what[- ]if|twin|scenario)|simulate (?:the )?(?:ed|beds?|capacity|boarding|census|occupancy)|'
       r'(?:open(?:ed|ing)?|add(?:ed|ing)?|more|fewer|remove|close)\s+\d+\s+(?:more |additional |fewer )?(?:inpatient |staffed )?beds?|boarding (?:hours|time) (?:fall|drop|change|if)|'
       r'what happens to (?:ed )?boarding|(?:if|when) (?:los|length of stay) (?:drops|falls|shortens|rises|increases)|bed sensitivity|how many beds do we need)\b')


def route(question, profile, as_of):
    from .engine_suite import plan
    low = ' '.join(str(question).lower().split())
    if not re.search(CAP, low) or H.is_definition(low): return None
    if re.search(r'\bblocking\b|\bblocked\b', low) and not re.search(r'\bboarding\b|\bsimulat\w*\b|\bwhat[- ]if\b|\bscenario\b', low): return None  # a blocking-probability target is the queueing (Erlang loss) route
    params = H.common_params(low)
    m = re.search(r'\b(?:open(?:ed|ing)?|add(?:ed|ing)?|more)\s+(\d{1,3})\s+(?:more |additional )?(?:inpatient |staffed )?beds?', low)
    if m: params['add_beds'] = int(m.group(1))
    m = re.search(r'\b(?:close|remove|fewer)\s+(\d{1,3})\s+(?:more |additional )?(?:inpatient |staffed )?beds?', low)
    if m: params['add_beds'] = -int(m.group(1))
    m = re.search(r'\b(?:los|length of stay)[^.?\d]{0,30}?(\d{1,2})\s?%|(\d{1,2})\s?%[^.?\d]{0,12}(?:shorter|lower|longer) (?:los|length of stay)', low)
    if m:
        v = int(m.group(1) or m.group(2))
        params['los_change_pct'] = -v if re.search(r'drop|fall|short|lower|reduc|decreas|cut', low) else v
    m = re.search(r'\badmissions?[^.?\d]{0,30}?(\d{1,3})\s?%', low)
    if m: params['admission_change_pct'] = int(m.group(1)) * (-1 if re.search(r'fall|drop|decreas|fewer', low) else 1)
    m = re.search(r'\b(\d{2,4})\s+(?:staffed )?beds?\b', low)
    if m and 'add_beds' not in params and re.search(r'\bwith (?:only )?\d{2,4}\s', low): params['beds'] = int(m.group(1))
    return plan(OP, params, 'Discrete-event capacity twin: beds, LOS and admissions what-if with ED boarding', question, ['capacity simulation'])


TEMPLATES = ['If we opened 12 more inpatient beds, how much would ED boarding hours fall?',
             'simulate capacity if length of stay drops 10% at hospital 2',
             'what happens to boarding if admissions grow 15% next year',
             'capacity what-if: how many beds do we need to keep boarding under an hour',
             'bed sensitivity for boarding with 8 fewer beds']
