"""Outreach worklist: who should care managers contact, within a stated capacity, and why.

Per candidate patient the value of one outreach contact is built from explained
components (reason codes), each with its evidence:
  - avoidable utilization: expected admissions/ED days next year (the patient's
    last-12-month events shrunk toward the internal risk-score expectation from
    vbc.risk_scores) x an assumed relative reduction x an assumed unit cost;
  - open care gaps (internal definitions, not certified HEDIS): A1c > 9 or no A1c,
    BP >= 140/90 or no BP, statin therapy, kidney health evaluation (eGFR + uACR),
    heart-failure GDMT pillars (evidence-based beta blocker, ACEi/ARB/ARNI, MRA,
    SGLT2 inhibitor) from medication classes;
  - rising risk: local-linear-trend Kalman forecast (trajectory.forecast) of eGFR,
    A1c and systolic BP crossing a threshold within a year;
  - social needs: positive latest screen for food, transport or cost of care.
Components for one patient overlap (one contact cannot deliver every intervention
in full), so they are combined with a geometric overlap discount. Expected value =
engagement probability (reachability from the patient's own appointment history,
Beta-shrunk) x value if engaged.

Selection maximises total expected value under a capacity of N patients and/or
nurse hours (per-task minute estimates), with optional per-practice floors/caps
and optional cross-patient saturation per (practice, reason category). Without
saturation the objective is additive: cardinality problems are solved exactly by
sorting (with practice floors/caps: floors first, then fill - exact for additive
weights on a partition constraint), hour budgets exactly by 0/1 knapsack DP over
minutes. With saturation (monotone submodular) or mixed constraints, lazy greedy
runs and a data-dependent upper bound on the optimum (F(S) + best fractional
packing of marginal gains, Leskovec et al. 2007) certifies how close it is.

All dollar figures are planning assumptions, stated in `assumptions`, not
measured effects. Nothing here authorizes clinical action. The learning loop
(outreach_bandit) needs logged outreach outcomes that do not exist yet.
"""
from collections import defaultdict
from datetime import date
import heapq, math

import numpy as np

from . import aggregate, conformal, semantic_layer, trajectory, vbc

VERSION = 'outreach/1'
CATEGORIES = ('utilization', 'care_gap', 'rising_risk', 'social_need')
GROUP_BY = ('none', 'practice', 'pcp')
METHODS = ('auto', 'exact', 'greedy')

# code -> (category, label, assumed value if engaged (USD), suggested action, task minutes)
REASONS = {
    'UTIL_AVOIDABLE': ('utilization', 'Expected avoidable admissions/ED use', None, 'Care-management enrollment call', 30),
    'GAP_A1C_GT9': ('care_gap', 'Diabetes: latest A1c > 9%', 450, 'Diabetes care-gap call; schedule PCP visit', 15),
    'GAP_A1C_MISSING': ('care_gap', 'Diabetes: no A1c in 12 months', 250, 'Order A1c; schedule PCP visit', 10),
    'GAP_BP_UNCONTROLLED': ('care_gap', 'Hypertension: latest BP >= 140/90', 300, 'BP recheck / home BP cuff; PCP follow-up', 15),
    'GAP_BP_MISSING': ('care_gap', 'Hypertension: no BP in 12 months', 150, 'Schedule BP check', 10),
    'GAP_STATIN': ('care_gap', 'No statin observed (diabetes 40-75 or ASCVD)', 350, 'Statin discussion with PCP', 10),
    'GAP_KED': ('care_gap', 'Diabetes: kidney health evaluation incomplete (eGFR + uACR)', 150, 'Order eGFR and uACR', 10),
    'GAP_GDMT_BB': ('care_gap', 'HF: no evidence-based beta blocker observed', 600, 'GDMT review (nurse/pharmacist with cardiology/PCP)', 25),
    'GAP_GDMT_RAAS': ('care_gap', 'HF: no ACEi/ARB/ARNI observed', 600, 'GDMT review (nurse/pharmacist with cardiology/PCP)', 25),
    'GAP_GDMT_MRA': ('care_gap', 'HF: no MRA observed', 600, 'GDMT review (nurse/pharmacist with cardiology/PCP)', 25),
    'GAP_GDMT_SGLT2': ('care_gap', 'HF: no SGLT2 inhibitor observed', 600, 'GDMT review (nurse/pharmacist with cardiology/PCP)', 25),
    'RISING_EGFR': ('rising_risk', 'eGFR forecast to fall below 30 within a year', 800, 'Early renal labs recheck; PCP/nephrology review', 15),
    'RISING_A1C': ('rising_risk', 'A1c forecast to exceed 9% within a year', 300, 'Early A1c recheck; diabetes education', 15),
    'RISING_SBP': ('rising_risk', 'Systolic BP forecast to exceed 160 within a year', 250, 'BP recheck; medication review', 15),
    'SDOH_FOOD': ('social_need', 'Positive food-insecurity screen', 150, 'Community resource referral (food)', 20),
    'SDOH_TRANSPORT': ('social_need', 'Positive transport screen', 150, 'Arrange transport benefit / ride', 20),
    'SDOH_COST': ('social_need', 'Positive cost-of-care screen', 150, 'Financial counseling / assistance referral', 20),
}
# Rising-risk forecasts: concept -> (reason, threshold, below?)
RISING = {'egfr': ('RISING_EGFR', 30.0, True), 'hba1c': ('RISING_A1C', 9.0, False), 'systolic_bp': ('RISING_SBP', 160.0, False)}
ASSUMPTIONS = {
    'admission_cost_usd': 15000.0, 'ed_visit_cost_usd': 1500.0,
    'admission_reduction_if_engaged': 0.15, 'ed_reduction_if_engaged': 0.10,
    'utilization_prior_weight_years': 1.0, 'utilization_floor_usd': 200.0,
    'gdmt_weight_ef_unconfirmed': 0.5, 'rising_min_probability': 0.25, 'rising_max_days_since_result': 730,
    'base_reach_probability': 0.55, 'reach_prior': [6.0, 4.0], 'reach_bounds': [0.2, 0.9],
    'value_cv_if_engaged': 0.5, 'extra_reason_minutes': 5, 'lookback_days': 365,
}
NEGATIVE_MED_STATUS = ('returned_to_stock', 'reversed_adjudication', 'does_not_recognize_as_current', 'cancelled', 'stopped', 'discontinued')
DIABETES = ('diabetes', 'type_2_diabetes', 'type_1_diabetes')
ASCVD = ('coronary_artery_disease', 'myocardial_infarction', 'stroke')
HF = ('heart_failure', 'heart_failure_reduced_ef')


def validate(p):
    allowed = {'capacity_patients', 'capacity_hours', 'require', 'group_by', 'min_per_practice', 'max_per_practice',
               'method', 'overlap_discount', 'saturation', 'top', 'measures'}
    if not isinstance(p, dict) or set(p) - allowed:
        raise ValueError('outreach parameters: ' + ', '.join(sorted(allowed)))
    out = {'capacity_patients': p.get('capacity_patients'), 'capacity_hours': p.get('capacity_hours'),
           'require': sorted(set(p.get('require') or [])), 'group_by': p.get('group_by', 'none'),
           'min_per_practice': p.get('min_per_practice', 0), 'max_per_practice': p.get('max_per_practice'),
           'method': p.get('method', 'auto'), 'overlap_discount': p.get('overlap_discount', 0.5),
           'saturation': p.get('saturation', 0), 'top': p.get('top', 200)}
    if out['capacity_patients'] is None and out['capacity_hours'] is None: out['capacity_patients'] = 50
    if out['capacity_patients'] is not None and (type(out['capacity_patients']) is not int or not 1 <= out['capacity_patients'] <= 5000):
        raise ValueError('capacity_patients 1..5000')
    h = out['capacity_hours']
    if h is not None and (type(h) not in (int, float) or not math.isfinite(h) or not 0.25 <= h <= 2000):
        raise ValueError('capacity_hours 0.25..2000')
    if any(r not in CATEGORIES for r in out['require']): raise ValueError(f'require: subset of {CATEGORIES}')
    if out['group_by'] not in GROUP_BY: raise ValueError(f'group_by: {GROUP_BY}')
    if out['method'] not in METHODS: raise ValueError(f'method: {METHODS}')
    if type(out['min_per_practice']) is not int or not 0 <= out['min_per_practice'] <= 1000: raise ValueError('min_per_practice 0..1000')
    m = out['max_per_practice']
    if m is not None and (type(m) is not int or not 1 <= m <= 5000 or m < out['min_per_practice']): raise ValueError('max_per_practice >= min, 1..5000')
    d = out['overlap_discount']
    if type(d) not in (int, float) or not 0 <= d <= 1: raise ValueError('overlap_discount 0..1')
    s = out['saturation']
    if type(s) not in (int, float) or not 0 <= s <= 1000: raise ValueError('saturation 0 (off) or patients per (practice, category) scale')
    if type(out['top']) is not int or not 1 <= out['top'] <= 5000: raise ValueError('top 1..5000')
    if p.get('measures'):  # care gaps from the measure library (config/agere/measures), in addition to the internal gaps
        from . import measures
        out['measures'] = measures.validate({'measures': p['measures']})['measures']
    return out


def _register_measure_reasons(measure_ids):
    """Outreach reason codes for library measures: GAP_M_<ID> with the measure file's outreach value/minutes/action."""
    from . import measures
    lib = measures.library()
    for mid in measure_ids:
        o = lib[mid].get('outreach') or {}
        REASONS.setdefault(f'GAP_M_{mid.upper()}', ('care_gap', lib[mid].get('gap_label', lib[mid]['name']), o.get('value_usd', 150),
                                                    o.get('action', 'Close the care gap with the PCP team'), o.get('minutes', 10)))


# ---- candidate values --------------------------------------------------------------
def _ago(as_of, days):
    return date.fromordinal(date.fromisoformat(as_of).toordinal() - days).isoformat()


def combine(values, discount):
    """Value of one contact addressing several reasons: largest first, each further one x discount."""
    return sum(v * discount ** k for k, v in enumerate(sorted(values, reverse=True)))


def minutes_for(codes):
    if not codes: return 0
    return max(REASONS[c][4] for c in codes) + ASSUMPTIONS['extra_reason_minutes'] * (len(codes) - 1)


def _utilization_counts(db, start, end):
    ed, adm = defaultdict(set), defaultdict(int)
    for pid, d in db.execute("SELECT patient_id, event_date FROM encounter WHERE type_concept='emergency_visit' AND event_date BETWEEN ? AND ?", (start, end)):
        ed[pid].add(d[:10])
    for pid, in db.execute("SELECT patient_id FROM stay WHERE admit_date BETWEEN ? AND ?", (start, end)):
        adm[pid] += 1
    return ed, adm


def utilization_model(db, as_of):
    """Expected next-year admissions and ED days per patient as of `as_of`, from the 12 months before it only."""
    A = ASSUMPTIONS
    start, end = _ago(as_of, A['lookback_days']), as_of
    people = vbc.eligibility(db, as_of, start, end)
    scores, fit = vbc.risk_scores(db, people, start, end) if any(i['months'] for i in people.values()) else ({}, {})
    ed, adm = _utilization_counts(db, start, end)
    member_years = sum(len(i['months']) for i in people.values()) / 12 or 1.0
    enrolled = [p for p, i in people.items() if i['months']]
    rate_adm = sum(adm[p] for p in enrolled) / member_years
    rate_ed = sum(len(ed[p]) for p in enrolled) / member_years
    return {'start': start, 'end': end, 'people': people, 'scores': scores, 'fit': fit, 'ed': ed, 'adm': adm,
            'enrolled': enrolled, 'rate_adm': rate_adm, 'rate_ed': rate_ed}


def expected_utilization(m, pid):
    """(score used, expected admissions, expected ED days) next year: last-12-month events shrunk toward the risk-model rate."""
    A = ASSUMPTIONS
    score = m['scores'].get(pid)
    s = score if score is not None else 1.0
    k = A['utilization_prior_weight_years']
    return s, (m['adm'][pid] + k * m['rate_adm'] * s) / (1 + k), (len(m['ed'][pid]) + k * m['rate_ed'] * s) / (1 + k)


def utilization_value(adm, ed):
    """Avoidable-utilization value if engaged for a given number of admissions and ED days (stated assumptions)."""
    A = ASSUMPTIONS
    return A['admission_reduction_if_engaged'] * adm * A['admission_cost_usd'] + A['ed_reduction_if_engaged'] * ed * A['ed_visit_cost_usd']


def _utilization_pairs(db, origin):
    """Utilization value predicted at `origin` vs the value implied by the admissions/ED days observed in the next year."""
    m = utilization_model(db, origin)
    after = date.fromordinal(date.fromisoformat(origin).toordinal() + 1).isoformat()
    ed, adm = _utilization_counts(db, after, _ago(origin, -ASSUMPTIONS['lookback_days']))
    out = []
    for pid in m['enrolled']:
        _, pa, pe = expected_utilization(m, pid)
        out.append({'patient_id': pid, 'pred': utilization_value(pa, pe), 'y': utilization_value(adm[pid], len(ed[pid])),
                    'group': aggregate.age_band(m['people'][pid]['age'])})
    return out


def _monthly_events(db):
    months = defaultdict(int)
    for m, n in db.execute("SELECT substr(admit_date, 1, 7), count(*) FROM stay WHERE admit_date IS NOT NULL GROUP BY 1"): months[m] += n
    for m, n in db.execute("SELECT substr(event_date, 1, 7), count(*) FROM encounter WHERE type_concept='emergency_visit' "
                           "AND event_date IS NOT NULL GROUP BY 1"): months[m] += n
    return months


def _calibration_origins(db, as_of, max_years=4, max_empty_run=2):
    """Most recent origin t1 <= as_of - 1y whose year before and year after are both continuously recorded, and the most
    recent such origin t2 <= t1 - 1y (so backtest calibration outcomes end before the evaluation origin). Origins step back
    monthly. A run of more than `max_empty_run` months with no admission or ED visit anywhere in the roster is a feed gap,
    not months of perfect health, so a window containing one cannot calibrate anything."""
    counts, skipped = _monthly_events(db), []
    def ok(t):
        run = 0
        for m in vbc._months(_ago(t, 365), _ago(t, -365))[1:-1]:  # whole months inside the 24-month span
            run = run + 1 if not counts.get(m) else 0
            if run > max_empty_run: return False
        people = vbc.eligibility(db, t, _ago(t, 365), t)
        return sum(1 for i in people.values() if i['months']) >= 20
    def find(latest):
        for k in range(0, 12 * max_years):
            t = _ago(latest, round(k * 30.4375))
            if ok(t): return t
            if k == 0: skipped.append(f'{_ago(t, 365)}..{_ago(t, -365)}')
        return None
    t1 = find(_ago(as_of, 365))
    t2 = find(_ago(t1, 365)) if t1 else None
    return t1, t2, skipped


def calibrate_utilization(db, as_of, alpha=0.1, min_group=30):
    """Mondrian (age band) split conformal on the avoidable-utilization value, time-split by year.
    Score = |observed - predicted| / (predicted + s0), s0 = median prediction. Live: calibrated on predictions issued
    one year before `as_of` scored on the following year. Backtest: calibrated two years back, evaluated one year back."""
    import numpy as np
    level = 1 - alpha
    t1, t2, skipped = _calibration_origins(db, as_of)
    out = {'method': ('Mondrian (age band) split conformal on the avoidable-utilization value (assumed reductions x unit costs x '
                      'admissions/ED days), normalized by predicted value + median prediction; time split by year: live intervals are '
                      f'calibrated on predictions issued {t1} scored on the next 12 months; the backtest calibrates on predictions issued {t2} '
                      f'and evaluates those issued {t1}. Other value components are stated assumptions and are held fixed.'
                      + (f' Windows skipped because they contain a run of months with no admission or ED visit recorded anywhere (feed gap): {"; ".join(skipped[:3])}.' if skipped else '')),
           'level': level, 'target': 'avoidable-utilization value next 12 months (USD)', 'available': False,
           'calibration_origin': t1, 'backtest_origin': t2, 'skipped_windows': skipped[:6]}
    live = _utilization_pairs(db, t1) if t1 else []
    if len(live) < 20:
        out['reason'] = (f'only {len(live)} enrolled members at the calibration origin {t1} (need 20)' if t1 else
                         'no origin with 24 continuously recorded months of admissions/ED visits around it and >= 20 enrolled members')
        out['statement'] = 'Conformal calibration unavailable: ' + out['reason'] + '.'
        return out
    arr = lambda pairs, k: np.array([q[k] for q in pairs], dtype=float)
    s0 = float(np.median(arr(live, 'pred'))) or 1.0
    norm = lambda pairs: conformal.scores(arr(pairs, 'y'), arr(pairs, 'pred'), arr(pairs, 'pred') + s0)
    table = conformal.mondrian(norm(live), [q['group'] for q in live], alpha, min_group)
    out.update(available=True, calibration_pairs=len(live), scale_offset=round(s0, 2), _table=table, _s0=s0,
               groups={str(g): {'q': round(v['q'], 4) if math.isfinite(v['q']) else None, 'calibration_cases': v['n'], 'pooled': v['pooled']}
                       for g, v in table['groups'].items()})
    earlier = _utilization_pairs(db, t2) if t2 else []
    if len(earlier) >= 20:
        s0b = float(np.median(arr(earlier, 'pred'))) or 1.0
        sb = conformal.scores(arr(earlier, 'y'), arr(earlier, 'pred'), arr(earlier, 'pred') + s0b)
        bt = conformal.mondrian(sb, [q['group'] for q in earlier], alpha, min_group)
        pred, y = arr(live, 'pred'), arr(live, 'y')
        qs = np.array([conformal.mondrian_q(bt, q['group']) for q in live])
        lo, hi = conformal.interval(pred, qs, pred + s0b, lower=0.0)
        hit = (y >= lo) & (y <= hi)
        out['backtest_coverage'] = round(float(hit.mean()), 4)
        out['backtest_evaluation_pairs'] = len(live)
        out['backtest_calibration_pairs'] = len(earlier)
        groups = np.array([q['group'] for q in live], dtype=object)
        out['backtest_by_group'] = {str(g): {'cases': int((groups == g).sum()), 'coverage': round(float(hit[groups == g].mean()), 4)}
                                    for g in sorted(set(groups.tolist()), key=str)}
        out['statement'] = conformal.describe_coverage(level, out['backtest_coverage'], len(live)) + ' in the time-split backtest'
    else:
        out['statement'] = f'Conformal {level:.0%} intervals calibrated on {len(live)} members; too little earlier history to backtest their coverage'
    out['statement'] += ' (avoidable-utilization component; Mondrian by age band).'
    return out


def utilization_interval(cal, pred, age):
    q = conformal.mondrian_q(cal['_table'], aggregate.age_band(age))
    if not math.isfinite(q): return None
    lo, hi = conformal.interval([pred], q, [pred + cal['_s0']], lower=0.0)
    return float(lo[0]), float(hi[0])


def candidates(db, as_of, keep=None, discount=0.5, measure_gaps=None):
    """Every in-scope patient with explained value components. Returns (rows, context).
    measure_gaps: {patient_id: [(measure id, label, evidence)]} from the measure library (optional)."""
    A = ASSUMPTIONS
    # Risk model, base rates and attendance are fitted on the whole roster; only candidates are restricted to `keep`,
    # so a cohort's scores stay on the population scale (a heart-failure cohort is not re-centred to mean 1.0).
    model = utilization_model(db, as_of)
    start, end, people, scores, fit = model['start'], model['end'], model['people'], model['scores'], model['fit']
    ed, adm, enrolled, rate_adm, rate_ed = model['ed'], model['adm'], model['enrolled'], model['rate_adm'], model['rate_ed']
    attr = vbc.attribution(db, people, start, end, 'hybrid')
    has = defaultdict(set)
    for c, pid in db.execute('SELECT concept, patient_id FROM concept_patient'):
        has[pid].add(c)
    # latest measurements within the lookback
    latest = {}
    for pid, c, d, v in db.execute("SELECT patient_id, concept, event_date, value_canonical FROM measurement_latest "
                                   "WHERE concept IN ('hba1c','systolic_bp','diastolic_bp','egfr','uacr')"):
        latest[(pid, c)] = (d[:10] if d else None, v)
    recent = lambda pid, c: latest.get((pid, c)) if latest.get((pid, c)) and latest[(pid, c)][0] and latest[(pid, c)][0] >= start else None
    # medication classes with positive evidence in the lookback (or undated active/reported use)
    meds = defaultdict(set)
    for pid, cls, status, d in db.execute('SELECT patient_id, drug_class, status, event_date FROM medication WHERE drug_class IS NOT NULL'):
        if status in NEGATIVE_MED_STATUS: continue
        if (d and d[:10] >= start) or (not d and status in ('active', 'reports_taking')): meds[pid].add(cls)
    # social needs: latest screen per need
    needs = {}
    for pid, need, pos, d in db.execute('SELECT patient_id, need, positive, event_date FROM social_need ORDER BY event_date'):
        needs[(pid, need)] = (pos, d)
    # appointment attendance -> reachability
    appts = defaultdict(lambda: [0, 0])
    for pid, kept, noshow in db.execute("SELECT patient_id, kept, ever_no_show FROM appointment WHERE scheduled_date BETWEEN ? AND ?",
                                        (_ago(as_of, 730), end)):
        appts[pid][0] += bool(kept); appts[pid][1] += bool(noshow) and not kept
    a0, b0 = A['reach_prior']
    pop_attend = (a0 + sum(k for k, _ in appts.values())) / (a0 + b0 + sum(k + n for k, n in appts.values()))
    # measurement series for rising-risk forecasts
    series = defaultdict(lambda: defaultdict(list))
    for pid, c, d, v in db.execute("SELECT patient_id, concept, event_date, value_canonical FROM measurement WHERE concept IN ('egfr','hba1c','systolic_bp') "
                                   "AND value_canonical IS NOT NULL AND event_date IS NOT NULL"):
        if keep is None or pid in keep: series[(pid, c)][d[:10]].append(v)
    snapshot = date.fromisoformat(as_of).toordinal()

    rows = []
    for pid, info in people.items():
        if keep is not None and pid not in keep: continue
        conds, age = has[pid], info['age']
        reasons = []
        add = lambda code, value, evidence: reasons.append({'code': code, 'category': REASONS[code][0], 'label': REASONS[code][1],
                                                            'value_if_engaged': round(float(value), 2), 'evidence': evidence})
        # utilization
        score = scores.get(pid)
        s, pred_adm, pred_ed = expected_utilization(model, pid)
        util = utilization_value(pred_adm, pred_ed)
        if util >= A['utilization_floor_usd']:
            add('UTIL_AVOIDABLE', util, f"risk score {s:.2f}{'' if score is not None else ' (not enrolled; population mean used)'}; "
                                        f"{adm[pid]} admissions, {len(ed[pid])} ED days in 12 months; expected next year "
                                        f"{pred_adm:.2f} admissions, {pred_ed:.2f} ED days")
        diabetic = bool(conds & set(DIABETES))
        if diabetic and age is not None and 18 <= age <= 75:
            a1c = recent(pid, 'hba1c')
            if a1c is None: add('GAP_A1C_MISSING', REASONS['GAP_A1C_MISSING'][2], f'no A1c since {start}')
            elif a1c[1] > 9: add('GAP_A1C_GT9', REASONS['GAP_A1C_GT9'][2], f'A1c {a1c[1]:.1f}% on {a1c[0]}')
        if 'hypertension' in conds and age is not None and 18 <= age <= 85:
            sbp, dbp = recent(pid, 'systolic_bp'), recent(pid, 'diastolic_bp')
            if sbp is None and dbp is None: add('GAP_BP_MISSING', REASONS['GAP_BP_MISSING'][2], f'no BP since {start}')
            elif (sbp and sbp[1] >= 140) or (dbp and dbp[1] >= 90):
                shown = '/'.join(f'{x[1]:.0f}' if x else '?' for x in (sbp, dbp))
                add('GAP_BP_UNCONTROLLED', REASONS['GAP_BP_UNCONTROLLED'][2], f'BP {shown} on {(sbp or dbp)[0]}')
        statin_eligible = (diabetic and age is not None and 40 <= age <= 75) or (conds & set(ASCVD) and (age is None or 21 <= age <= 75))
        if statin_eligible and 'statin' not in meds[pid]:
            add('GAP_STATIN', REASONS['GAP_STATIN'][2], ('diabetes' if diabetic else 'ASCVD') + f'; no statin evidence since {start}')
        if diabetic and age is not None and 18 <= age <= 85 and (recent(pid, 'egfr') is None or recent(pid, 'uacr') is None):
            missing = [c for c in ('egfr', 'uacr') if recent(pid, c) is None]
            add('GAP_KED', REASONS['GAP_KED'][2], 'missing ' + ' and '.join(missing) + f' since {start}')
        if conds & set(HF):
            w = 1.0 if 'heart_failure_reduced_ef' in conds else A['gdmt_weight_ef_unconfirmed']
            ef = 'HFrEF documented' if w == 1.0 else 'HF; reduced EF not documented (value x0.5)'
            for code, classes in (('GAP_GDMT_BB', ('hf_evidence_based_beta_blocker',)), ('GAP_GDMT_RAAS', ('ace_inhibitor', 'arb', 'arni')),
                                  ('GAP_GDMT_MRA', ('mra',)), ('GAP_GDMT_SGLT2', ('sglt2_inhibitor',))):
                if not meds[pid] & set(classes):
                    add(code, REASONS[code][2] * w, f"{ef}; no {'/'.join(classes)} evidence since {start}")
        for concept, (code, threshold, below) in RISING.items():
            days = series.get((pid, concept))
            if not days or len(days) < 3: continue
            pts = sorted((date.fromisoformat(d).toordinal(), float(np.mean(v))) for d, v in days.items())
            last = pts[-1][1]
            if (last < threshold) if below else (last > threshold): continue  # already past: a gap/control issue, not rising risk
            gap = max(0, snapshot - pts[-1][0])
            if gap > A['rising_max_days_since_result']: continue
            rel, floor = trajectory.NOISE[concept]
            f = trajectory.forecast(pts, rel, floor, 365 + gap, threshold, below)
            if f['p_cross'] >= A['rising_min_probability']:
                add(code, REASONS[code][2] * f['p_cross'], f"{concept} {last:.1f} -> forecast {f['forecast']} (sd {f['forecast_sd']}) in 1 year; "
                                                           f"slope {f['slope_per_year']}/yr; P(cross {threshold:g}) {f['p_cross']:.2f}")
                reasons[-1].update(p_cross=f['p_cross'], _concept=concept)
                reasons[-1]['_mean'], reasons[-1]['_sd'] = trajectory._predict(pts, rel, floor, 365 + gap)
        for need, code in (('food', 'SDOH_FOOD'), ('transport', 'SDOH_TRANSPORT'), ('cost_of_care', 'SDOH_COST')):
            got = needs.get((pid, need))
            if got and got[0]: add(code, REASONS[code][2], f'positive {need} screen on {(got[1] or "undated")[:10]}')
        for mid, _label, evidence in (measure_gaps or {}).get(pid, ()):
            add(f'GAP_M_{mid.upper()}', REASONS[f'GAP_M_{mid.upper()}'][2], f'{mid}: {evidence}')
        if not reasons: continue
        reasons.sort(key=lambda r: -r['value_if_engaged'])
        kept, noshow = appts[pid]
        attend = (a0 + kept) / (a0 + b0 + kept + noshow)
        lo, hi = A['reach_bounds']
        reach = min(hi, max(lo, A['base_reach_probability'] * attend / pop_attend))
        w_value = combine([r['value_if_engaged'] for r in reasons], discount)
        codes = [r['code'] for r in reasons]
        a = attr.get(pid, {})
        rows.append({'patient_id': pid, 'practice': a.get('practice') or 'unattributed', 'pcp': a.get('pcp') or 'unattributed',
                     'risk_score': round(score, 3) if score is not None else None, 'reasons': reasons,
                     'categories': sorted({r['category'] for r in reasons}), 'engagement_probability': round(reach, 3),
                     'engagement_evidence': f'{kept} kept, {noshow} no-show appointments in 24 months',
                     'value_if_engaged': round(w_value, 2), 'expected_value': round(reach * w_value, 2),
                     'minutes': minutes_for(codes), 'suggested_action': _action(reasons), '_util': util, '_age': age})
    context = {'people_in_scope': len(people) if keep is None else len(keep & set(people)),'enrolled': len(enrolled), 'base_admissions_per_member_year': round(rate_adm, 4),
               'base_ed_days_per_member_year': round(rate_ed, 4), 'population_attendance': round(pop_attend, 3),
               'risk_model_calibration_by_decile': fit.get('calibration_by_decile', [])[:10], 'lookback': {'start': start, 'end': end}}
    return rows, context


def _action(reasons):
    seen, actions = set(), []
    for r in reasons:
        a = REASONS[r['code']][3]
        if a not in seen: seen.add(a); actions.append(a)
    return actions[0] + ('; also: ' + '; '.join(actions[1:3]) if len(actions) > 1 else '')


# ---- objective -----------------------------------------------------------------------
class Objective:
    """F(S) = sum over groups g of phi_g(sum_{i in S} x_ig).

    x_ig = patient i's expected value attributed to group g (practice x reason category, split pro rata
    from its discounted components). phi_g = identity when saturation is off (additive, modular), else
    C_g(1 - exp(-x / C_g)) with C_g = saturation x mean candidate contribution in g: concave, so F is
    monotone submodular."""
    def __init__(self, rows, saturation=0.0):
        self.rows, self.saturation = rows, saturation
        self.parts = []
        for r in rows:
            total = sum(c['value_if_engaged'] for c in r['reasons']) or 1.0
            share = defaultdict(float)
            for c in r['reasons']: share[(r['practice'], c['category'])] += c['value_if_engaged'] / total
            self.parts.append({g: r['expected_value'] * s for g, s in share.items()})
        self.cap = {}
        if saturation:
            sums, counts = defaultdict(float), defaultdict(int)
            for part in self.parts:
                for g, x in part.items(): sums[g] += x; counts[g] += 1
            self.cap = {g: saturation * sums[g] / counts[g] for g in sums}

    @property
    def modular(self): return not self.saturation

    def phi(self, g, x):
        if not self.saturation: return x
        c = self.cap[g]
        return c * (1 - math.exp(-x / c)) if c > 0 else 0.0

    def value(self, chosen):
        load = defaultdict(float)
        for i in chosen:
            for g, x in self.parts[i].items(): load[g] += x
        return sum(self.phi(g, x) for g, x in load.items())

    def gain(self, i, load):
        return sum(self.phi(g, load[g] + x) - self.phi(g, load[g]) for g, x in self.parts[i].items())


# ---- solvers -------------------------------------------------------------------------
def _feasible_add(i, rows, count_by, p, n_chosen, used_minutes, cap_n, cap_min):
    if cap_n is not None and n_chosen >= cap_n: return False
    if cap_min is not None and used_minutes + rows[i]['minutes'] > cap_min: return False
    if p['max_per_practice'] is not None and count_by[rows[i]['practice']] >= p['max_per_practice']: return False
    return True


def exact_cardinality(rows, k, floor=0, cap=None):
    """Exact for additive values: floors per practice from each practice's best, then best remaining under caps.
    Exchange argument: any optimum can be rearranged to contain each practice's top `floor` and to be
    value-sorted within the remaining slots."""
    order = sorted(range(len(rows)), key=lambda i: (-rows[i]['expected_value'], rows[i]['patient_id']))
    chosen, count = [], defaultdict(int)
    if floor:
        for i in order:
            if count[rows[i]['practice']] < floor and len(chosen) < k:
                chosen.append(i); count[rows[i]['practice']] += 1
    taken = set(chosen)
    for i in order:
        if len(chosen) >= k: break
        if i in taken or (cap is not None and count[rows[i]['practice']] >= cap): continue
        chosen.append(i); count[rows[i]['practice']] += 1; taken.add(i)
    return chosen


def knapsack(values, weights, capacity):
    """Exact 0/1 knapsack by DP over integer weights. Returns (chosen indices, best value per capacity 0..C)."""
    n, C = len(values), int(capacity)
    best = np.zeros(C + 1)
    keep = np.zeros((n, C + 1), dtype=bool)
    for i, (v, w) in enumerate(zip(values, weights)):
        w = int(w)
        if w > C or v <= 0: continue
        cand = np.full(C + 1, -np.inf)
        cand[w:] = best[:C + 1 - w] + v
        take = cand > best
        keep[i] = take
        best = np.where(take, cand, best)
    chosen, c = [], C
    for i in range(n - 1, -1, -1):
        if keep[i, c]: chosen.append(i); c -= int(weights[i])
    return sorted(chosen), best


def greedy(obj, rows, p, cap_n, cap_min):
    """Lazy (CELF) greedy on marginal gain, or gain per minute under an hour budget, with practice floors served first.
    Under an hour budget the better of the ratio-greedy set and the best single feasible patient is kept."""
    count_by, load, chosen, minutes = defaultdict(int), defaultdict(float), [], 0
    def take(i):
        nonlocal minutes
        chosen.append(i); count_by[rows[i]['practice']] += 1; minutes += rows[i]['minutes']
        for g, x in obj.parts[i].items(): load[g] += x
    ratio = cap_min is not None
    key = lambda i, gain: gain / max(rows[i]['minutes'], 1) if ratio else gain
    if p['min_per_practice']:
        for practice in sorted({r['practice'] for r in rows}):
            members = [i for i, r in enumerate(rows) if r['practice'] == practice]
            for _ in range(p['min_per_practice']):
                options = [i for i in members if i not in chosen and _feasible_add(i, rows, count_by, p, len(chosen), minutes, cap_n, cap_min)]
                if not options: break
                take(max(options, key=lambda i: key(i, obj.gain(i, load))))
    heap = [(-key(i, obj.gain(i, load)), i) for i in range(len(rows)) if i not in chosen]
    heapq.heapify(heap)
    picked = set(chosen)
    while heap:
        neg, i = heapq.heappop(heap)
        if i in picked: continue
        if not _feasible_add(i, rows, count_by, p, len(chosen), minutes, cap_n, cap_min):
            if cap_n is not None and len(chosen) >= cap_n: break
            continue
        fresh = key(i, obj.gain(i, load))
        if heap and fresh < -heap[0][0] - 1e-12:
            heapq.heappush(heap, (-fresh, i)); continue
        if fresh <= 0: break
        take(i); picked.add(i)
    if ratio and not p['min_per_practice']:
        single = max((i for i in range(len(rows)) if rows[i]['minutes'] <= cap_min), key=lambda i: obj.value([i]), default=None)
        if single is not None and obj.value([single]) > obj.value(chosen): chosen = [single]
    return chosen


def upper_bound(obj, rows, chosen, cap_n, cap_min):
    """F(OPT) <= F(S) + max over feasible T of sum_{j in T} gain_j(S) (submodularity + monotonicity);
    the inner max is relaxed to the LP (fractional) packing under slots and minutes, ignoring practice
    floors/caps - still a valid upper bound."""
    load = defaultdict(float)
    for i in chosen:
        for g, x in obj.parts[i].items(): load[g] += x
    sel = set(chosen)
    gains = [(obj.gain(j, load), rows[j]['minutes']) for j in range(len(rows)) if j not in sel]
    gains = [(g, m) for g, m in gains if g > 0]
    if cap_min is None:
        extra = sum(sorted((g for g, _ in gains), reverse=True)[:cap_n])
    else:
        gains.sort(key=lambda gm: -gm[0] / max(gm[1], 1))
        extra, room, slots = 0.0, float(cap_min), cap_n if cap_n is not None else len(gains)
        for g, m in gains:
            if room <= 0 or slots <= 0: break
            frac = min(1.0, room / max(m, 1), slots)
            extra += g * frac; room -= m * frac; slots -= frac
    return obj.value(chosen) + extra


def solve(rows, p, cap_n=None, cap_min=None):
    """Choose patients. Returns (chosen indices, optimization receipt)."""
    obj = Objective(rows, p['saturation'])
    constrained = bool(p['min_per_practice'] or p['max_per_practice'] is not None)
    mode = p['method']
    if mode != 'greedy' and obj.modular and cap_min is None:
        chosen = exact_cardinality(rows, cap_n, p['min_per_practice'], p['max_per_practice'])
        receipt = {'method': 'exact_sort', 'guarantee': 'optimal (additive values; floors/caps per practice by exchange argument)'}
    elif mode != 'greedy' and obj.modular and cap_n is None and not constrained and len(rows) * (cap_min + 1) <= 60_000_000:
        chosen, _ = knapsack([r['expected_value'] for r in rows], [r['minutes'] for r in rows], cap_min)
        receipt = {'method': 'exact_knapsack_dp', 'guarantee': 'optimal 0/1 knapsack over task minutes'}
    else:
        if mode == 'exact':
            raise ValueError('exact method needs additive values (saturation 0) and one capacity type'
                             + ('' if cap_min is None or not constrained else ' without practice floors/caps under an hour budget'))
        chosen = greedy(obj, rows, p, cap_n, cap_min)
        prior = ('1 - 1/e ~ 0.632 (monotone submodular, cardinality; Nemhauser-Wolsey-Fisher 1978)' if cap_min is None and not constrained else
                 '1/2 (partition matroid; Fisher-Nemhauser-Wolsey 1978)' if cap_min is None else
                 '(1 - 1/e)/2 ~ 0.316 (knapsack: better of ratio-greedy and best singleton; Leskovec et al. 2007)')
        receipt = {'method': 'lazy_greedy', 'guarantee': 'a priori ' + prior + ('; floors served first, so the a-priori bound is not claimed' if p['min_per_practice'] else '')}
    achieved = obj.value(chosen)
    exact = receipt['method'].startswith('exact')
    ub = achieved if exact else upper_bound(obj, rows, chosen, cap_n, cap_min)
    receipt.update(objective='additive' if obj.modular else f'submodular (saturation {p["saturation"]} patients per practice x category)',
                   achieved_value=round(achieved, 2), certified_upper_bound=round(ub, 2),
                   certified_ratio=round(achieved / ub, 4) if ub > 0 else 1.0,
                   certificate='exact solver: the selection is optimal for the stated objective' if exact else
                               'F(OPT) <= F(S) + best fractional packing of marginal gains at S (relaxes practice floors/caps)')
    return chosen, receipt, obj


def portfolio(rows, chosen, obj):
    """Expected value and spread of the realised value of the chosen worklist (patients independent)."""
    cv = ASSUMPTIONS['value_cv_if_engaged']
    total = obj.value(chosen)
    standalone = sum(rows[i]['expected_value'] for i in chosen) or 1.0
    scale = total / standalone  # saturation shrinkage applied proportionally
    var = 0.0
    for i in chosen:
        e, w = rows[i]['engagement_probability'], rows[i]['value_if_engaged'] * scale
        var += e * w * w * (1 + cv * cv) - (e * w) ** 2
    sd = math.sqrt(max(var, 0.0))
    return {'expected_value': round(total, 2), 'sd': round(sd, 2), 'interval_80': [round(max(0.0, total - 1.2816 * sd), 2), round(total + 1.2816 * sd, 2)],
            'expected_engaged_patients': round(sum(rows[i]['engagement_probability'] for i in chosen), 1),
            'variance_model': f'engagement ~ Bernoulli(reach); value if engaged has CV {cv}; patients independent; normal approximation'}


def _conformal_fields(r, cal, discount):
    """Conformal 90% interval on the avoidable-utilization component and the expected value it implies
    (engagement probability x overlap-combined value, other components held at their stated values)."""
    if not cal.get('available'): return {}
    got = utilization_interval(cal, r['_util'], r['_age'])
    if got is None: return {}
    lo_u, hi_u = got
    others = [c['value_if_engaged'] for c in r['reasons'] if c['code'] != 'UTIL_AVOIDABLE']
    floor = ASSUMPTIONS['utilization_floor_usd']
    ev = lambda u: r['engagement_probability'] * combine(others + ([u] if u >= floor else []), discount)
    return {'utilization_value_interval_90': [round(lo_u, 2), round(hi_u, 2)],
            'expected_value_interval_90': [round(min(ev(lo_u), r['expected_value']), 2), round(max(ev(hi_u), r['expected_value']), 2)]}


def calibrate_rising(db, as_of):
    """Time-split conformal calibration of each rising-risk forecast (trajectory.calibrate on the whole roster's series)."""
    series = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for pid, c, d, v in db.execute("SELECT patient_id, concept, event_date, value_canonical FROM measurement WHERE concept IN ('egfr','hba1c','systolic_bp') "
                                   "AND value_canonical IS NOT NULL AND event_date IS NOT NULL"):
        series[c][pid][d[:10]].append(v)
    snapshot = date.fromisoformat(as_of).toordinal()
    return {c: trajectory.calibrate(series[c], c, snapshot, 365, threshold, below) for c, (_, threshold, below) in RISING.items()}


def _apply_rising(rows, rising):
    """Rising-risk reasons gain a conformal forecast interval and a Venn-Abers calibrated crossing probability."""
    for r in rows:
        for c in r['reasons']:
            if '_concept' not in c: continue
            tmp = trajectory.apply_calibration({'_mean': c.pop('_mean'), '_sd': c.pop('_sd'), 'p_cross': c['p_cross']}, rising[c.pop('_concept')])
            if 'conformal_low' in tmp: c['forecast_interval_90'] = [tmp['conformal_low'], tmp['conformal_high']]
            for k in ('p_cross_calibrated', 'p_cross_calibrated_range'):
                if k in tmp: c[k] = tmp[k]
        r.pop('_util', None); r.pop('_age', None)


def execute(path, patient_ids, params, as_of=None):
    p = validate(params)
    db = sqlite3_connect(path)
    try:
        as_of = as_of or dict(db.execute('SELECT key, value FROM meta')).get('as_of')
        measure_gaps, skipped_measures = None, []
        if p.get('measures'):
            from . import measures
            _register_measure_reasons(p['measures'])
            measure_gaps, skipped_measures = measures.care_gaps(db, p['measures'], as_of, set(patient_ids) if patient_ids is not None else None)
        rows, context = candidates(db, as_of, set(patient_ids) if patient_ids is not None else None, p['overlap_discount'], measure_gaps)
        if p.get('measures'): context['measure_library'] = {'measures': p['measures'], 'declined': skipped_measures}
        cal = calibrate_utilization(db, as_of)
        rising = calibrate_rising(db, as_of)
    finally:
        db.close()
    for r in rows: r['_conformal'] = _conformal_fields(r, cal, p['overlap_discount'])
    _apply_rising(rows, rising)
    cal['rising_risk'] = {c: trajectory.public(x) for c, x in rising.items()}
    rising_text = '; '.join(f"{c}: {x['statement'].rstrip('.')}" for c, x in rising.items() if x.get('available'))
    cal['statement'] = cal['statement'] + (f' Rising-risk forecasts - {rising_text}.' if rising_text else '')
    eligible = [r for r in rows if not p['require'] or set(p['require']) & set(r['categories'])]
    cap_n, cap_min = p['capacity_patients'], (int(round(p['capacity_hours'] * 60)) if p['capacity_hours'] is not None else None)
    chosen, receipt, obj = solve(eligible, p, cap_n, cap_min) if eligible else ([], {'method': 'none', 'achieved_value': 0.0}, Objective([], 0))
    # Rank: the order of selection value (expected value, ties by id).
    chosen = sorted(chosen, key=lambda i: (-eligible[i]['expected_value'], eligible[i]['patient_id']))
    worklist = []
    for rank, i in enumerate(chosen, 1):
        r = eligible[i]
        worklist.append({'priority': rank, 'patient_id': r['patient_id'], 'expected_value': r['expected_value'],
                         'value_if_engaged': r['value_if_engaged'], 'engagement_probability': r['engagement_probability'],
                         'top_reasons': [c['code'] for c in r['reasons'][:3]],
                         'reason_detail': '; '.join(f"{c['code']} ${c['value_if_engaged']:,.0f}: {c['evidence']}" for c in r['reasons'][:3]),
                         'suggested_action': r['suggested_action'], 'minutes': r['minutes'], 'practice': r['practice'], 'pcp': r['pcp'],
                         'risk_score': r['risk_score'], 'reasons': r['reasons'], **r['_conformal']})
    port = portfolio(eligible, chosen, obj) if chosen else {'expected_value': 0.0, 'sd': 0.0}
    # Capacity sensitivity: re-solve at scaled capacity; loss per unit cut at the margin.
    curve = []
    if chosen:
        for frac in (0.5, 0.75, 0.9, 1.0, 1.1):
            n2 = max(1, int(round(cap_n * frac))) if cap_n is not None else None
            m2 = max(1, int(round(cap_min * frac))) if cap_min is not None else None
            c2, r2, _ = solve(eligible, {**p, 'method': 'auto' if p['method'] == 'exact' else p['method']}, n2, m2)
            curve.append({'capacity_fraction': frac, 'patients': n2, 'hours': round(m2 / 60, 2) if m2 else None,
                          'selected': len(c2), 'expected_value': r2['achieved_value']})
        full = next(c for c in curve if c['capacity_fraction'] == 1.0)
        cut = next(c for c in curve if c['capacity_fraction'] == 0.9)
        unit, units = ('hour', (cap_min - max(1, round(cap_min * 0.9))) / 60) if cap_min is not None else ('slot', cap_n - max(1, round(cap_n * 0.9)))
        port['capacity_curve'] = curve
        port['value_lost_per_unit_cut'] = {'unit': unit, 'usd_per_unit': round((full['expected_value'] - cut['expected_value']) / units, 2) if units else None,
                                           'basis': f're-solved value at 100% vs 90% capacity ({units:g} {unit}s)'}
        last = worklist[-1]
        port['marginal_patient'] = {'patient_id': last['patient_id'], 'expected_value': last['expected_value']}
    groups = defaultdict(lambda: {'patients': 0, 'expected_value': 0.0, 'minutes': 0, 'candidates': 0})
    key = (lambda r: r[p['group_by']]) if p['group_by'] != 'none' else (lambda r: r['practice'])
    for r in eligible: groups[key(r)]['candidates'] += 1
    for w in worklist:
        g = groups[key(w)]; g['patients'] += 1; g['expected_value'] = round(g['expected_value'] + w['expected_value'], 2); g['minutes'] += w['minutes']
    by_group = sorted(({'group': k, **v} for k, v in groups.items()), key=lambda x: -x['expected_value'])
    reason_counts = defaultdict(int)
    for w in worklist:
        for c in w['reasons']: reason_counts[c['code']] += 1
    cap_text = ' and '.join(x for x in ((f'{cap_n} patients' if cap_n is not None else ''),
                                        (f'{p["capacity_hours"]:g} nurse hours' if cap_min is not None else '')) if x)
    top_reasons = ', '.join(f'{k} ({v})' for k, v in sorted(reason_counts.items(), key=lambda kv: -kv[1])[:4])
    headline = (f"{len(worklist)} of {len(eligible)} candidates selected within {cap_text}: expected value ${port['expected_value']:,.0f} "
                f"(SD ${port['sd']:,.0f}), {port.get('expected_engaged_patients', 0)} expected to engage"
                + (f"; cutting capacity 10% loses ${port['value_lost_per_unit_cut']['usd_per_unit']:,.0f} per {port['value_lost_per_unit_cut']['unit']}"
                   if port.get('value_lost_per_unit_cut', {}).get('usd_per_unit') is not None else '')
                + (f". Most common reasons: {top_reasons}." if top_reasons else '.')) if worklist else \
               f'No candidates with an outreach reason among {context["people_in_scope"]} patients in scope.'
    method = ('Candidate value = engagement probability x value if engaged; value if engaged combines reason components largest-first '
              f'with overlap discount {p["overlap_discount"]}. Components: avoidable utilization (12-month admissions/ED days shrunk toward '
              'the internal Poisson-GLM risk-score expectation, x assumed reduction x unit cost), internal care-gap definitions (A1c, BP, statin, '
              'kidney health evaluation, HF GDMT pillars from medication classes), Kalman-forecast rising risk (eGFR<30, A1c>9, SBP>160 within a year, '
              'value x probability), positive social-needs screens. Engagement = base reach x the patient\'s Beta-shrunk appointment attendance '
              f'relative to the population. Optimiser: {receipt.get("method")} ({receipt.get("guarantee", "")}); certified ratio '
              f'{receipt.get("certified_ratio", 1.0)} of a computed upper bound. Dollar values are planning assumptions, not measured effects. '
              f'Intervals: {cal["method"]} {cal["statement"]}')
    covered = ', '.join(f"{c} {x['backtest_coverage']:.1%}" for c, x in rising.items() if x.get('backtest_coverage') is not None)
    if worklist and cal.get('backtest_coverage') is not None:
        headline += (f" Conformal 90% utilization-value intervals covered {cal['backtest_coverage']:.1%} of "
                     f"{cal['backtest_evaluation_pairs']} held-out members (time-split backtest).")
    elif worklist and cal.get('available'):
        headline += ' ' + cal['statement'].split(' Rising-risk')[0]
    if worklist and covered:
        headline += f' Rising-risk forecast 90% conformal intervals covered {covered} of held-out values (time-split backtest).'
    from .outreach_bandit import learning_status
    return {'schema': VERSION, 'parameters': p, 'as_of': as_of, 'candidates': len(eligible), 'patients_in_scope': context['people_in_scope'],
            'selected': len(worklist), 'capacity': {'patients': cap_n, 'minutes': cap_min,
                                                     'minutes_used': sum(w['minutes'] for w in worklist)},
            'worklist': worklist[:p['top']], 'by_group': by_group, 'group_by': p['group_by'] if p['group_by'] != 'none' else 'practice',
            'portfolio': port, 'optimization': receipt, 'headline': headline, 'method': method,
            'assumptions': {**ASSUMPTIONS, 'reason_values_usd': {k: v[2] for k, v in REASONS.items() if v[2] is not None},
                            'task_minutes': {k: v[4] for k, v in REASONS.items()}},
            'context': context, 'learning_loop': learning_status(),
            'conformal': {k: v for k, v in cal.items() if not k.startswith('_')},
            'limitations': ['Reason values, effect sizes and unit costs are stated planning assumptions, not effects measured in this population.',
                            'Care-gap definitions are internal (no certified HEDIS value sets); absence of a medication or lab means not observed in received data.',
                            'GDMT gaps assume HF; reduced ejection fraction is rarely documented, so those gaps carry half weight.',
                            'Engagement probability is a reachability proxy from appointment attendance, not a measured outreach response rate.',
                            'Worklist ranks candidates for review by care teams; it does not authorize clinical action.']}


def sqlite3_connect(path):
    import sqlite3
    return sqlite3.connect(f'file:{semantic_layer.ensure(path)}?mode=ro', uri=True)
