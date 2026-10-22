"""or_block_optimization: OR block allocation / release from surgical claim volume by service and weekday (wave 2).

Cases are surgical CPT lines (10000-69999) at inpatient hospital (21), outpatient hospital (22) or ASC (24) places of service, grouped to one case per
patient, day and rendering clinician; the service is the rendering clinician's specialty. OR case times are not received, so case minutes are modelled
from the CPT chapter (an explicit table, stated in every answer) plus a turnover allowance. Weekly demand minutes per service and weekday feed an integer
program (scipy HiGHS) that allocates block-days to services to maximise covered demand within the room limit, compared with a flat allocation, with
release candidates where a block would run below the release threshold.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import timedelta

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

from . import hospital_ops as H

OP = 'or_block_optimization'
VERSION = 'or-block/1'
DAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri')
CHAPTER_MINUTES = ((10000, 19999, 'integumentary', 45), (20000, 29999, 'musculoskeletal', 105), (30000, 32999, 'respiratory / chest', 120),
                   (33000, 37999, 'cardiovascular', 150), (38000, 39999, 'hemic / lymphatic', 120), (40000, 49999, 'digestive', 60),
                   (50000, 59999, 'urinary / genital / obstetric', 90), (60000, 60999, 'endocrine', 120), (61000, 64999, 'nervous system', 150),
                   (65000, 68999, 'eye', 30), (69000, 69999, 'ear', 60))
SETTINGS = {'all': ('21', '22', '24'), 'hospital': ('21', '22'), 'asc': ('24',)}
DEFAULTS = {'facility': None, 'months': 12, 'start': None, 'end': None, 'setting': 'all', 'rooms': None, 'block_hours': 4.0, 'target_utilization': 0.85,
            'turnover_minutes': 30, 'release_threshold': 0.5, 'min_cases': 5}
SPEC = {'facility': ('str',), 'months': ('int', 1, 120), 'start': ('date',), 'end': ('date',), 'setting': ('enum', tuple(SETTINGS)), 'rooms': ('int', 1, 200),
        'block_hours': ('num', 2, 14), 'target_utilization': ('num', 0.3, 1.0), 'turnover_minutes': ('int', 0, 120), 'release_threshold': ('num', 0.05, 0.95),
        'min_cases': ('int', 1, 500)}
PRODUCT = {'population': 'optional', 'view': 'or_blocks',
           'use': 'OR BLOCK OPTIMISATION: weekday demand minutes by surgical service from claims, optimal block allocation across ORs vs a flat schedule, covered demand, '
                  'utilisation, unmet minutes and blocks to release.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'facility': None, 'months': 12, 'setting': '|'.join(SETTINGS), 'rooms': None, 'block_hours': 4, 'target_utilization': 0.85,
                           'turnover_minutes': 30, 'release_threshold': 0.5, 'min_cases': 5, 'start': None, 'end': None},
            'meaning': 'Per service and weekday mean weekly surgical demand minutes (CPT-chapter duration model + turnover) -> integer block allocation maximising covered '
                       'demand subject to rooms per day; optimised vs flat allocation utilisation, unmet minutes, release candidates.'}
NATIVE = ('count', 'value')


def schema(obj, string, array): return H.spec_schema(SPEC, obj, string, array)
def validate(p): return H.spec_validate(OP, DEFAULTS, SPEC, p)


def minutes(cpt):
    try: c = int(str(cpt)[:5])
    except (TypeError, ValueError): return None, None
    for lo, hi, name, m in CHAPTER_MINUTES:
        if lo <= c <= hi: return m, name
    return None, None


def load_cases(db, setting, keep):
    q = ','.join('?' * len(SETTINGS[setting]))
    lines = db.execute(f"SELECT pl.patient_id, pl.service_date, pl.cpt, pc.rendering_npi, o.organization_id, cl.specialty, pl.claim_id FROM procedure_line pl "
                       f"JOIN payer_claim pc ON pc.payer_claim_id = pl.claim_id LEFT JOIN organization o ON o.npi = pc.billing_npi "
                       f"LEFT JOIN clinician cl ON cl.npi = pc.rendering_npi WHERE pl.cpt BETWEEN '10000' AND '69999' AND pl.place_of_service IN ({q}) "
                       f"AND pl.service_date IS NOT NULL AND COALESCE(pl.counts_in_metrics, 1) = 1 AND pl.duplicate_of_record_id IS NULL", SETTINGS[setting]).fetchall()
    cases = defaultdict(list)
    for pid, day, cpt, npi, org, spec, cid in lines:
        if keep is not None and pid not in keep: continue
        m, chap = minutes(cpt)
        if m is None: continue
        cases[(pid, str(day)[:10], npi or '')].append((m, chap, org, spec))
    out = []
    for (pid, day, npi), ls in cases.items():
        ms = sorted((x[0] for x in ls), reverse=True)
        out.append({'patient_id': pid, 'day': day, 'minutes': ms[0] + 0.2 * sum(ms[1:]), 'chapter': ls[0][1], 'facility': next((x[2] for x in ls if x[2]), None),
                    'service': next((x[3] for x in ls if x[3]), 'unassigned (no rendering clinician on the claim)')})
    return out


def execute(path, patient_ids, params, as_of):
    p = validate(params)
    db, snap = H.connect(path)
    try:
        as_of = str(as_of or snap)[:10]
        keep = set(patient_ids) if patient_ids is not None else None
        cases = load_cases(db, p['setting'], keep)
        orgs = H.facilities(db)
    finally:
        db.close()
    start, end = H.window(as_of, p['months'], p['start'], p['end'])
    fac = H.resolve_facility(p['facility'], orgs) if p['facility'] else None
    notes = []
    if p['facility'] and not fac: notes.append(f"No facility matches '{p['facility']}'; every facility is counted.")
    cs = [c for c in cases if (not start or c['day'] >= start) and c['day'] <= end and (not fac or c['facility'] == fac) and H.d(c['day']).weekday() < 5]
    base = {'schema': VERSION, 'as_of': as_of, 'parameters': p, 'window': [start, end], 'facility': fac, 'notes': notes, 'patient_ids': [], 'basis': 'procedure_line + payer_claim + clinician'}
    if len(cs) < p['min_cases']:
        return {**base, 'status': 'unknown', 'table': [], 'receipt': {'count': len(cs)}, 'method': 'OR block allocation', 'assumptions': [],
                'notes': notes + [f'Only {len(cs)} weekday surgical cases in the window; too few to allocate blocks.']}
    weeks = max(((H.d(end) - H.d(start or min(c['day'] for c in cs))).days + 1) / 7.0, 1.0)
    block_min = p['block_hours'] * 60; cap = block_min * p['target_utilization']
    svc_n = Counter(c['service'] for c in cs)
    services = sorted(s for s, n in svc_n.items() if n >= p['min_cases'])
    small = sum(n for s, n in svc_n.items() if s not in services)
    if small: notes.append(f'{small} cases in services with fewer than {p["min_cases"]} cases are left out of the allocation.')
    dem = np.zeros((len(services), 5)); cnt = np.zeros((len(services), 5)); sidx = {s: i for i, s in enumerate(services)}
    for c in cs:
        if c['service'] in sidx:
            i, d_ = sidx[c['service']], H.d(c['day']).weekday()
            dem[i, d_] += (c['minutes'] + p['turnover_minutes']) / weeks; cnt[i, d_] += 1 / weeks
    S = len(services)
    rooms = p['rooms'] or max(int(math.ceil(dem.sum(axis=0).max() / cap)), 1)
    # variables: x[S*5] block counts (int), y[S*5] covered minutes (continuous)
    n = S * 5; c_vec = np.concatenate([np.full(n, 0.01), -np.ones(n)])
    A, lb, ub = [], [], []
    for j in range(5):
        row = np.zeros(2 * n); row[[i * 5 + j for i in range(S)]] = 1; A.append(row); lb.append(0); ub.append(rooms)
    for k in range(n):
        row = np.zeros(2 * n); row[n + k] = 1; row[k] = -cap; A.append(row); lb.append(-np.inf); ub.append(0)
    res = milp(c_vec, constraints=LinearConstraint(np.array(A), lb, ub), integrality=np.concatenate([np.ones(n), np.zeros(n)]),
               bounds=Bounds(np.zeros(2 * n), np.concatenate([np.full(n, rooms), dem.ravel()])))
    x = np.rint(res.x[:n]).reshape(S, 5) if res.success else np.zeros((S, 5))
    # flat baseline: rooms*5 blocks shared by total demand, same count every weekday
    share = dem.sum(axis=1) / dem.sum(); tot = rooms * 5
    flat_week = np.floor(share * tot).astype(int)
    for i in np.argsort(-(share * tot - flat_week))[: tot - flat_week.sum()]: flat_week[i] += 1
    flat = np.zeros((S, 5))
    for i in range(S):
        base_d, extra = divmod(int(flat_week[i]), 5)
        flat[i, :] = base_d; flat[i, :extra] += 1
    def score(alloc):
        cov = np.minimum(dem, alloc * cap); return cov, float(cov.sum()), float(np.maximum(dem - cov, 0).sum())
    cov_o, tot_o, un_o = score(x); cov_f, tot_f, un_f = score(flat)
    table = []
    for i, s in enumerate(services):
        for label, alloc, cov in (('optimised', x, cov_o), ('flat', flat, cov_f)):
            blocks = alloc[i]; used = cov[i].sum(); avail = blocks.sum() * block_min
            table.append({'service': s, 'schedule': label, **{d_: int(b) for d_, b in zip(DAYS, blocks)}, 'blocks_per_week': int(blocks.sum()),
                          'weekly_demand_minutes': round(float(dem[i].sum())), 'cases_per_week': round(float(cnt[i].sum()), 1),
                          'utilisation': None if not avail else round(float(used / avail), 3), 'unmet_minutes': round(float(np.maximum(dem[i] - cov[i], 0).sum())),
                          'blocks_to_release': int(sum(1 for j in range(5) if flat[i, j] and label == 'flat' and dem[i, j] / (flat[i, j] * block_min) < p['release_threshold']))})
    chap = Counter(c['chapter'] for c in cs)
    receipt = {'count': len(cs), 'cases': len(cs), 'services': S, 'weeks_in_window': round(weeks, 1), 'rooms': rooms, 'rooms_source': 'stated' if p['rooms'] else 'estimated to cover the busiest weekday',
               'block_hours': p['block_hours'], 'optimised_covered_minutes': round(tot_o), 'flat_covered_minutes': round(tot_f), 'optimised_unmet_minutes': round(un_o),
               'flat_unmet_minutes': round(un_f), 'extra_covered_hours_per_week': round((tot_o - tot_f) / 60, 1),
               'releasable_blocks_flat': sum(t['blocks_to_release'] for t in table), 'cases_by_chapter': dict(chap.most_common())}
    return {**base, 'status': 'computed', 'table': table, 'receipt': receipt, 'detail': [{'chapter': n_, 'assumed_case_minutes': m} for lo, hi, n_, m in CHAPTER_MINUTES],
            'method': ('Weekday surgical cases from procedure_line (CPT 10000-69999, POS 21/22/24) joined to payer_claim and clinician; one case per patient, day and rendering clinician '
                       '(longest procedure + 20% of the others); minutes = CPT-chapter assumption + turnover; mean weekly demand per service-weekday; integer program (HiGHS) maximises '
                       'covered minutes (block capacity = block hours x target utilisation) subject to rooms per day; flat schedule = the same block total split by service demand, equal every day.'),
            'assumptions': ['OR case-time and block-schedule feeds are not received: case minutes are modelled from the CPT chapter (table in detail) and the current block schedule is '
                            'represented by a flat allocation, not a recorded one.',
                            'Service = the rendering clinician\'s specialty; claims without a rendering clinician are grouped as unassigned.',
                            'Demand is the average week; it does not model add-on cases, cancellations or surgeon vacations.']}


def headline(r):
    rc = r['receipt']
    top = sorted([t for t in r['table'] if t['schedule'] == 'optimised'], key=lambda t: -t['blocks_per_week'])[:3]
    return (f"{rc['cases']} weekday surgical cases over {rc['weeks_in_window']} weeks need {sum(t['weekly_demand_minutes'] for t in r['table'] if t['schedule'] == 'optimised') / 60:.0f} OR hours a week "
            f"across {rc['services']} services with {rc['rooms']} rooms; optimised blocks cover {rc['extra_covered_hours_per_week']:+.1f} h/week more demand than a flat schedule "
            f"(unmet {rc['optimised_unmet_minutes'] / 60:.1f} vs {rc['flat_unmet_minutes'] / 60:.1f} h); {rc['releasable_blocks_flat']} flat blocks run under the release threshold; "
            + '; '.join(f"{t['service']} {t['blocks_per_week']} blocks/week" for t in top))


def answer(report, plan, question):
    if report['status'] != 'computed':
        return {'status': 'unknown', 'answer_type': 'text', 'value': ' '.join(report['notes']), 'method': report['method'], 'limitations': []}
    return H.generic_answer(OP, report, plan, headline(report) + '.')


ORB = (r'\b(?:or blocks?|block (?:time|schedule|allocation|utili[sz]ation|release)|operating room (?:blocks?|schedule|time|utili[sz]ation|allocation)|or (?:schedule|utili[sz]ation|time)|'
       r'surgical (?:block|schedule|capacity)|release (?:unused )?blocks?|block release|surgeon (?:block|time)|block utili[sz]ation|ors? (?:are|is) (?:under|over)utili[sz]ed)\b')


def route(question, profile, as_of):
    from .engine_suite import plan
    low = ' '.join(str(question).lower().split())
    if not re.search(ORB, low) or H.is_definition(low): return None
    params = H.common_params(low)
    m = re.search(r'\b(\d{1,2})\s+(?:operating rooms?|ors?|rooms)\b', low)
    if m: params['rooms'] = int(m.group(1))
    if re.search(r'\basc\b|ambulatory surg', low): params['setting'] = 'asc'
    elif re.search(r'\bhospital (?:ors?|operating)\b|\binpatient (?:ors?|surg)', low): params['setting'] = 'hospital'
    return plan(OP, params, 'OR block allocation and release from surgical claim demand by service and weekday', question, ['or block optimisation'])


TEMPLATES = ['how should we allocate OR blocks across surgical services by weekday',
             'which OR blocks should we release because they run under 50% utilisation',
             'optimise block schedule for 4 operating rooms using the last 12 months of surgical cases',
             'OR block utilisation at our ASCs']
