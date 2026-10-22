"""overbooking_fairness: who bears the burden of a no-show-driven overbooking policy, by age band and payer (wave 2).

Replays a no-show-driven overbooking policy on resolved appointments: the scheduling engine's no-show model (scheduling.Design / logistic fit, trained before the
audit window) scores every booking; each organisation-day session gets capacity K (stated, else bookings minus expected no-shows) and
the yield-managed number of double-booked slots (round of the session's expected no-shows), placed on the highest predicted-risk bookings. Replaying
real outcomes marks which patients were bumped (showed up beyond capacity). Exposure = expected double-booking per 100 appointments (mean predicted
no-show) compared across age bands and payers with Welch tests; the realised burden rate and its two-proportion test are reported beside it.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import timedelta

import numpy as np
from scipy import stats

from . import hospital_ops as H, scheduling as S

OP = 'overbooking_fairness'
VERSION = 'overbooking-fairness/1'
DIMS = ('both', 'age', 'payer')
AGE_BANDS = (('0-17', 0, 17), ('18-39', 18, 39), ('40-64', 40, 64), ('65+', 65, 200))
DEFAULTS = {'by': 'both', 'organization': None, 'months': 12, 'start': None, 'end': None, 'capacity': None, 'max_overflow_probability': 0.25,
            'value_per_visit': 1.0, 'overflow_cost': 2.0, 'alpha': 0.05, 'disparity_threshold': 1.25}
SPEC = {'by': ('enum', DIMS), 'organization': ('str',), 'months': ('int', 1, 120), 'start': ('date',), 'end': ('date',), 'capacity': ('int', 1, 500),
        'max_overflow_probability': ('num', 0.01, 1.0), 'value_per_visit': ('num', 0.01, 1000), 'overflow_cost': ('num', 0.01, 1000),
        'alpha': ('num', 0.001, 0.5), 'disparity_threshold': ('num', 1.0, 5.0)}
PRODUCT = {'population': 'optional', 'view': 'overbooking_fairness',
           'use': 'OVERBOOKING FAIRNESS AUDIT: replay the no-show overbooking policy on real appointment outcomes and show who gets overbooked, bumped past capacity or '
                  'deferred - by age band and payer - with disparity ratios, tests and the no-show model\'s calibration for each group.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'by': '|'.join(DIMS), 'organization': None, 'months': 12, 'capacity': None, 'max_overflow_probability': 0.25, 'value_per_visit': 1.0,
                           'overflow_cost': 2.0, 'alpha': 0.05, 'disparity_threshold': 1.25, 'start': None, 'end': None},
            'meaning': 'Counterfactual replay of the newsvendor overbooking policy on resolved appointments; per age band / payer: appointments, overbooked-slot, bumped and '
                       'deferred rates, burden rate, disparity ratio vs everyone else, two-proportion p-value, mean predicted vs observed no-show.'}
NATIVE = ('count', 'value')


def schema(obj, string, array): return H.spec_schema(SPEC, obj, string, array)
def validate(p): return H.spec_validate(OP, DEFAULTS, SPEC, p)


def band(age):
    if age is None: return 'age unknown'
    for name, lo, hi in AGE_BANDS:
        if lo <= age <= hi: return name
    return 'age unknown'


def z_test(k1, n1, k2, n2):
    if not n1 or not n2: return None
    p = (k1 + k2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0: return 1.0
    z = (k1 / n1 - k2 / n2) / se
    return math.erfc(abs(z) / math.sqrt(2))


def execute(path, patient_ids, params, as_of):
    p = validate(params)
    db, snap = H.connect(path)
    try:
        as_of = str(as_of or snap)[:10]
        rows, needs = S._load(db, patient_ids, as_of)
    finally:
        db.close()
    S._history(rows)
    notes = []
    start, end = H.window(as_of, p['months'], p['start'], p['end'])
    orgs = sorted({r['organization_id'] for r in rows})
    org = S._resolve_practice(p['organization'], orgs) if p['organization'] else None
    if p['organization'] and not org: notes.append(f"No scheduling organisation matches '{p['organization']}'; every organisation is audited.")
    resolved = [r for r in rows if (r['kept'] or r['no_show'])]
    win = [r for r in resolved if r['scheduled_date'] >= (start or '0000') and r['scheduled_date'] <= end and (not org or r['organization_id'] == org)]
    train = [r for r in resolved if r['scheduled_date'] < (start or '0000')]
    base = {'schema': VERSION, 'as_of': as_of, 'parameters': p, 'window': [start, end], 'basis': 'appointment + patient tables of the semantic layer', 'notes': notes, 'patient_ids': []}
    if len(train) < 200 or len(win) < 50 or len({r['no_show'] for r in train}) < 2:
        return {**base, 'status': 'unknown', 'table': [], 'receipt': {'count': 0, 'train': len(train), 'audited': len(win)}, 'method': 'replay of the overbooking policy',
                'assumptions': [], 'notes': notes + [f'Not enough resolved appointments ({len(train)} before the window, {len(win)} inside) to train the no-show model and audit.']}
    l2, _ = S.choose_l2(train, needs, None)
    design = S.Design(train, needs, float(np.mean([r['no_show'] for r in train])))
    w = S.fit_logistic(design.matrix(train), [r['no_show'] for r in train], l2)
    pn = S.predict(w, design.matrix(win))
    for r, x in zip(win, pn): r['p_noshow'] = float(x)
    sessions = defaultdict(list)
    for r in win: sessions[(r['organization_id'], r['scheduled_date'])].append(r)
    for (o, day), rs in sessions.items():
        n = len(rs)
        rs.sort(key=lambda r: (r['booked'] if r['booked'] is not None else -1, r['appointment_id']))
        m = int(round(sum(r['p_noshow'] for r in rs)))  # yield-managed: expected shows equal capacity
        K = p['capacity'] if p['capacity'] else max(n - m, 1)
        m = max(n - K, 0)
        risky = sorted(range(n), key=lambda i: (-rs[i]['p_noshow'], -i))[:m]  # double-booked slots go to the highest predicted no-show risk
        dbl = set(risky)
        for i, r in enumerate(rs): r['slot'] = 'double_booked' if i in dbl else 'regular'; r['bumped'] = 0
        shows = [i for i in range(n) if rs[i]['kept']]
        over = max(len(shows) - K, 0)
        waiters = sorted([i for i in shows if i in dbl], key=lambda i: -rs[i]['p_noshow'])[:over]
        for i in waiters: rs[i]['bumped'] = 1
    for r in win: r['burden'] = int(r['slot'] != 'regular' or r['bumped'])
    n_all, k_all = len(win), sum(r['burden'] for r in win)
    def table_for(dim):
        key = (lambda r: band(r['age'])) if dim == 'age' else (lambda r: r['coverage'] or 'unknown')
        g = defaultdict(list)
        for r in win: g[key(r)].append(r)
        out = []
        for name, rs in sorted(g.items(), key=lambda kv: -len(kv[1])):
            n, k = len(rs), sum(r['burden'] for r in rs); rest_n, rest_k = n_all - n, k_all - k
            rate, rrate = k / n, (rest_k / rest_n if rest_n else None)
            pv_real = z_test(k, n, rest_k, rest_n)
            others = [r['p_noshow'] for r in win if key(r) != name]
            mine = [r['p_noshow'] for r in rs]
            pv = None
            if len(mine) > 1 and len(others) > 1:
                pv = float(stats.ttest_ind(mine, others, equal_var=False).pvalue)
            pred, obs = float(np.mean(mine)), float(np.mean([r['no_show'] for r in rs]))
            ratio = round(pred / float(np.mean(others)), 2) if others and np.mean(others) else None
            flag = 'higher exposure' if ratio and ratio >= p['disparity_threshold'] and pv is not None and pv <= p['alpha'] else (
                   'lower exposure' if ratio and ratio <= 1 / p['disparity_threshold'] and pv is not None and pv <= p['alpha'] else 'no significant difference')
            out.append({'dimension': dim, 'group': name, 'appointments': n, 'double_booked_slots': sum(r['slot'] == 'double_booked' for r in rs),
                        'bumped': sum(r['bumped'] for r in rs), 'expected_double_booked_per_100': round(pred * 100, 2), 'replayed_burden_rate': round(rate, 4),
                        'replayed_rate_everyone_else': None if rrate is None else round(rrate, 4), 'replayed_p_value': None if pv_real is None else round(pv_real, 4),
                        'exposure_ratio_vs_everyone_else': ratio, 'p_value': None if pv is None else round(pv, 4), 'finding': flag, 'mean_predicted_no_show': round(pred, 4), 'observed_no_show': round(obs, 4),
                        'calibration_ratio': round(obs / pred, 2) if pred else None})
        return out
    table = (table_for('age') if p['by'] in ('age', 'both') else []) + (table_for('payer') if p['by'] in ('payer', 'both') else [])
    flagged = [t for t in table if t['finding'] != 'no significant difference' and t['appointments'] >= 30]
    receipt = {'count': n_all, 'appointments_audited': n_all, 'sessions': len(sessions), 'burdened': k_all, 'burden_rate': round(k_all / n_all, 4),
               'double_booked_slots': sum(r['slot'] == 'double_booked' for r in win), 'bumped': sum(r['bumped'] for r in win),
               'expected_double_booked': round(sum(r['p_noshow'] for r in win), 1),
               'groups_flagged': len(flagged), 'model_l2': l2, 'trained_on': len(train)}
    return {**base, 'status': 'computed', 'table': table, 'receipt': receipt, 'patient_ids': [],
            'method': ('No-show model (scheduling.Design logistic fit) trained on appointments before the window; per organisation-day session, double-booked slots = round(sum of predicted no-show '
                       'probabilities) given to the highest-risk bookings, capacity K = bookings minus those slots (or stated); replay with real outcomes: shows beyond K bump the '
                       'highest-risk double-booked patients who showed; exposure ratio and Welch test of each group vs everyone else, realised-burden two-proportion test beside it.'),
            'assumptions': ['Slot templates are not received: capacity K is stated or inferred as bookings minus expected no-shows; booking order is by booked date.',
                            'Which patients get the double-booked slots (highest predicted risk) is the audited policy choice, not something the clinics recorded.',
                            'This is a counterfactual audit of the policy, not a record of what the clinics did.',
                            'Cancellation outcomes are not usable in the generator (see scheduling data defects), so only kept and no-show appointments are replayed.',
                            'Age band and payer come from the patient table (coverage_type); the policy itself never uses age or payer except as model features.']}


def headline(r):
    rc = r['receipt']
    fl = [t for t in r['table'] if t['finding'] != 'no significant difference' and t['appointments'] >= 30]
    txt = (f"Replayed {rc['appointments_audited']:,} appointments in {rc['sessions']:,} sessions: {rc['double_booked_slots']} double-booked slots and {rc['bumped']} bumped patients "
           f"(model expects {rc['expected_double_booked']} double-bookings)")
    if fl: txt += '; ' + '; '.join(f"{t['dimension']} {t['group']}: {t['expected_double_booked_per_100']} per 100 appointments, x{t['exposure_ratio_vs_everyone_else']} the rest (p={t['p_value']})" for t in fl[:4])
    else: txt += '; no age band or payer carries significantly more or less of the overbooking'
    return txt


def answer(report, plan, question):
    if report['status'] != 'computed':
        return {'status': 'unknown', 'answer_type': 'text', 'value': ' '.join(report['notes']), 'method': report['method'], 'limitations': []}
    return H.generic_answer(OP, report, plan, headline(report) + '.')


FAIR = (r'\b(?:overbooking|over[- ]booking|overbook(?:ed)?)\b')
EQ = r'\b(?:bumped|deferred|double[- ]booked|age bands?|fair(?:ness)?|equit\w+|disparit\w+|bias(?:ed)?|burden|disproportionate\w*|who (?:gets|bears|is)|by (?:age|payer|insurance|coverage)|across (?:age|payers?)|audit)\b'


def route(question, profile, as_of):
    from .engine_suite import plan
    low = ' '.join(str(question).lower().split())
    if not (re.search(FAIR, low) and re.search(EQ, low)) or H.is_definition(low): return None
    params = {}
    if re.search(r'\bage\b', low) and not re.search(r'\bpayer|insurance|coverage', low): params['by'] = 'age'
    elif re.search(r'\bpayer|insurance|coverage', low) and not re.search(r'\bage\b', low): params['by'] = 'payer'
    m = re.search(r'\b(?:last|past) (\d{1,3}) months?\b', low)
    if m: params['months'] = int(m.group(1))
    org = re.search(r'\b(practice[\s-]*\d+|wh-practice-\d+)\b', low)
    if org: params['organization'] = org.group(1)
    return plan(OP, params, 'Fairness audit of the no-show overbooking policy by age band and payer', question, ['overbooking fairness'])


TEMPLATES = ['audit who bears the burden of overbooking by age and payer',
             'is our overbooking policy fair across payers',
             'which age bands get bumped or deferred by no-show overbooking in the last 12 months',
             'overbooking disparity by insurance type at practice 3']
