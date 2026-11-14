"""discharge_forecast: discharge-count and bed-day forecast from the in-house census (open visits) plus the capacity twin (wave 2).

Patients in house at the snapshot get a conditional remaining stay drawn from the empirical LOS distribution given the time already spent;
new admissions arrive by weekday rate and run through the same twin as capacity_simulation, so the forecast includes bed waits. A
hold-out backtest (last 8 weeks) measures a separate weekday-mean baseline. The capacity twin's historical accuracy is unmeasured.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime, timedelta

import numpy as np

from . import capacity_simulation as C, hospital_ops as H

OP = 'discharge_forecast'
VERSION = 'discharge-forecast/1'
DEFAULTS = {'facility': None, 'months': 12, 'start': None, 'end': None, 'horizon_days': 7, 'beds': None, 'replications': 100}
SPEC = {'facility': ('str',), 'months': ('int', 1, 120), 'start': ('date',), 'end': ('date',), 'horizon_days': ('int', 1, 60),
        'beds': ('int', 1, 5000), 'replications': ('int', 5, 500)}
PRODUCT = {'population': 'optional', 'view': 'discharge_forecast',
           'use': 'DISCHARGE FORECAST: expected discharges, bed-days and census for the next days from the in-house census and the discrete-event '
                  'capacity twin (conditional remaining stay for patients in house, weekday admission rates, bed waits). A separate weekday baseline backtest does not validate the twin.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'facility': None, 'months': 12, 'horizon_days': 7, 'beds': None, 'replications': 100, 'start': None, 'end': None},
            'meaning': 'Per-day expected discharges (10th-90th percentile), census and bed-days for the next horizon_days from open governed visits plus '
                       'new admissions through the capacity twin, with a weekday-mean hold-out backtest.'}
NATIVE = ('count', 'value')


def schema(obj, string, array): return H.spec_schema(SPEC, obj, string, array)
def validate(p): return H.spec_validate(OP, DEFAULTS, SPEC, p)


def backtest(rows, as_of, weeks=8):
    """Weekday-mean forecast of daily discharges for the last `weeks` weeks from the earlier history -> MAE, bias, naive MAE."""
    end = H.d(as_of); cut = end - timedelta(weeks * 7)
    dis = Counter(H.d(r['end_date']) for r in rows if (r['has_inpatient'] or r['has_observation']) and r['discharge_known'] and r['end_date'])
    hist = [d for d in dis if d < cut]
    if not hist: return None
    first = min(hist); n_days = (cut - first).days
    dow_n = Counter((first + timedelta(i)).weekday() for i in range(n_days))
    mean = {k: sum(c for d, c in dis.items() if first <= d < cut and d.weekday() == k) / max(dow_n.get(k, 0), 1) for k in range(7)}
    errs = [(mean[(cut + timedelta(i)).weekday()], dis.get(cut + timedelta(i), 0)) for i in range(weeks * 7)]
    overall = sum(c for d, c in dis.items() if first <= d < cut) / max(n_days, 1)
    return {'method':'weekday_mean','validation_target':'weekday_mean_baseline','days': len(errs),
            'origins':[cut.isoformat()],'horizon_days':weeks*7,'training_before':cut.isoformat(),
            'availability_safe_reconstruction':False,
            'mae_weekday_mean': round(float(np.mean([abs(f - a) for f, a in errs])), 3),
            'mae_overall_mean': round(float(np.mean([abs(overall - a) for _, a in errs])), 3),
            'bias': round(float(np.mean([f - a for f, a in errs])), 3), 'actual_mean': round(float(np.mean([a for _, a in errs])), 3)}


def execute(path, patient_ids, params, as_of):
    p = validate(params)
    db, snap = H.connect(path)
    try:
        as_of = str(as_of or snap)[:10]
        rows, basis, (start, end), fac, notes = H.visits_in(db, as_of, p, patient_ids)
    finally:
        db.close()
    twin = C.fit(rows, start, end)
    if not twin['los_hours'] or not sum(twin['rates']):
        return {'schema': VERSION, 'status': 'unknown', 'as_of': as_of, 'parameters': p, 'window': [start, end], 'facility': fac, 'basis': basis,
                'table': [], 'receipt': {'count': 0}, 'notes': notes + ['No completed inpatient or observation stays in the window.'], 'method': '', 'assumptions': [],
                'patient_ids': []}
    open_v = [r for r in rows if (r['has_inpatient'] or r['has_observation']) and not r['discharge_known']]
    los = np.array(twin['los_hours'])
    asof_dt = datetime.fromisoformat(as_of + 'T00:00:00') + timedelta(hours=24)
    est = int(math.ceil(float(np.quantile(twin['census'], 0.95)))) if twin['census'] else 1
    beds = p['beds'] or max(est, len(open_v), 1)
    if not p['beds']: notes.append(f'Staffed bed count is not received; beds = {beds}, the 95th-percentile daily census observed in the window.')
    H_, reps = p['horizon_days'], p['replications']
    start_dow = (H.d(as_of) + timedelta(1)).weekday()
    dis, cen = [], []
    for i in range(reps):
        rng = np.random.default_rng(20400 + i)
        remaining = []
        for r in open_v:
            a = H.ts(r['inpatient_admit'] or r['ed_arrival'], r['start_date'])
            el = max(H.hours(a, asof_dt) or 0.0, 0.0)
            tail = los[los > el]
            remaining.append(float(rng.choice(tail) - el) if len(tail) else float(rng.uniform(4, 24)))
        arr = C.simulate(rng, H_, twin['rates'], twin['los_hours'], beds, twin['ed_share'], warm=30, start_dow=start_dow, initial=remaining)
        m = C.metrics(arr, H_, beds); dis.append(m['discharges'][:H_]); cen.append(m['census'][:H_])
    dis, cen = np.array(dis), np.array(cen)
    table = []
    for k in range(H_):
        day = (H.d(as_of) + timedelta(k + 1))
        table.append({'date': day.isoformat(), 'weekday': day.strftime('%a'), 'expected_discharges': round(float(dis[:, k].mean()), 2),
                      'p10': float(np.quantile(dis[:, k], 0.1)), 'p90': float(np.quantile(dis[:, k], 0.9)),
                      'expected_census': round(float(cen[:, k].mean()), 2), 'bed_days': round(float(cen[:, k].mean()), 2)})
    bt = backtest(rows, as_of)
    receipt = {'count': int(round(float(dis.sum(axis=1).mean()))), 'horizon_days': H_, 'in_house_now': len(open_v), 'beds': beds,
               'expected_discharges_total': round(float(dis.sum(axis=1).mean()), 1), 'p10_total': float(np.quantile(dis.sum(axis=1), 0.1)),
               'p90_total': float(np.quantile(dis.sum(axis=1), 0.9)), 'expected_bed_days': round(float(cen.sum(axis=1).mean()), 1), 'backtest': bt,
               'forecast_validation':{'method':'capacity_twin','status':'unmeasured','historical_predictions':None}}
    return {'schema': VERSION, 'status': 'computed', 'as_of': as_of, 'parameters': p, 'window': [start, end], 'facility': fac, 'basis': basis,
            'table': table, 'receipt': receipt, 'notes': notes, 'patient_ids': sorted({r['patient_id'] for r in open_v}),
            'method': ('In-house visits get a remaining stay drawn from the empirical LOS conditional on time already in house; new admissions follow the '
                       'weekday Poisson rates and empirical LOS through the capacity twin (FIFO beds); percentiles are across replications.'),
            'assumptions': ['Open visits are inpatient/observation visits without a discharge received by the snapshot (discharge_known = 0); '
                            'a visit whose discharge feed is late is counted as still in house.',
                            'Discharge timing within a day is taken from simulated LOS hours; no discharge-order or discharge-before-noon clock is received.',
                            'Bed counts are not received (see notes); the twin never sees a pending discharge order, so the first days of a large census are approximate.',
                            'Historical error shown belongs to a weekday-mean baseline, not the capacity twin. Historical availability and twin interval coverage are unqualified.']}


def headline(r):
    rc = r['receipt']; bt = rc.get('backtest')
    txt = (f"Next {rc['horizon_days']} days: about {rc['expected_discharges_total']:.0f} discharges (80% range {rc['p10_total']:.0f}-{rc['p90_total']:.0f}), "
           f"{rc['expected_bed_days']:.0f} bed-days; {rc['in_house_now']} patients in house at the snapshot")
    if bt: txt += f"; weekday-mean baseline backtest MAE {bt['mae_weekday_mean']} discharges/day vs {bt['mae_overall_mean']} for a flat mean; capacity-twin historical accuracy unmeasured"
    return txt


def answer(report, plan, question):
    if report['status'] != 'computed':
        return {'status': 'unknown', 'answer_type': 'text', 'value': ' '.join(report['notes']), 'method': report['method'], 'limitations': []}
    return H.generic_answer(OP, report, plan, headline(report) + '.')


FC = (r'\b(?:discharge (?:forecast|prediction|projection)|forecast (?:of )?(?:the )?(?:discharges?|bed[- ]days|census)|how many (?:patients|discharges) (?:will|are we going to|should we expect to) '
      r'(?:be )?(?:discharg\w*|go home|leave)|expected discharges|predict(?:ed)? discharges|projected census|census (?:forecast|projection)|discharges? (?:expected )?(?:this|next|over the next) (?:week|\d+ days))\b')


def route(question, profile, as_of):
    from .engine_suite import plan
    low = ' '.join(str(question).lower().split())
    if not re.search(FC, low) or H.is_definition(low): return None
    params = H.common_params(low)
    params.pop('start', None); params.pop('end', None); params.pop('months', None) if 'months' not in low else None
    m = re.search(r'\bnext (\d{1,2}|two|three|four|seven|ten|fourteen) days?\b', low)
    if m: params['horizon_days'] = {'two': 2, 'three': 3, 'four': 4, 'seven': 7, 'ten': 10, 'fourteen': 14}.get(m.group(1)) or int(m.group(1))
    elif re.search(r'\bnext week\b|\bthis week\b', low): params['horizon_days'] = 7
    elif re.search(r'\bnext two weeks\b|\b14 days\b', low): params['horizon_days'] = 14
    return plan(OP, params, 'Discharge / bed-day forecast from the in-house census and capacity twin', question, ['discharge forecast'])


TEMPLATES = ['how many patients will be discharged over the next 7 days',
             'discharge forecast for hospital 2 next week',
             'projected census and bed-days for the next 14 days',
             'expected discharges this week with the uncertainty range']
