"""Value-based-care engine: eligibility, attribution, utilization, cost, risk, quality, shared savings.

Computed from the received data via the semantic layer. Every metric states its
population, period, denominator and method. Definitions are Agere's internal,
documented versions (not certified CMS/NCQA specifications): the warehouse has
no CPT/HCPCS, claim diagnoses or official value sets, and contract terms must be
supplied. Small groups are stabilised with empirical-Bayes shrinkage and carry
95% intervals, so rankings reflect evidence rather than panel size.
"""
from collections import defaultdict
from datetime import date
import json, math, re, sqlite3

import numpy as np

from . import aggregate, semantic_layer

VERSION = 'vbc-engine/1'
METRICS = ('eligibility', 'attribution', 'utilization', 'cost', 'risk', 'quality', 'shared_savings', 'change')
CHANGE_MEASURES = ('pmpm', 'ed_rate', 'admit_rate')
GROUP_BY = ('none', 'pcp', 'practice', 'coverage_type', 'age_band', 'sex')
PCP_VISIT_TYPES = ('primary_care_visit',)
STATIN = re.compile(r'statin\b|atorvastatin|simvastatin|rosuvastatin|pravastatin|lovastatin|pitavastatin|fluvastatin', re.I)
CHRONIC = aggregate.BURDEN_CONCEPTS


def _connect(path):
    return sqlite3.connect(f'file:{semantic_layer.ensure(path)}?mode=ro', uri=True)


def _months(start, end):
    y, m = int(start[:4]), int(start[5:7])
    out = []
    while (y, m) <= (int(end[:4]), int(end[5:7])):
        out.append(f'{y:04d}-{m:02d}'); m += 1
        if m == 13: y, m = y + 1, 1
    return out


def _period(p, as_of):
    end = (p.get('period') or {}).get('end') or as_of
    start = (p.get('period') or {}).get('start') or f'{int(end[:4]) - 1}{end[4:7]}-01'
    return start[:10], end[:10]


def validate(p):
    if not isinstance(p, dict) or p.get('metric') not in METRICS:
        raise ValueError(f'vbc metric must be one of {METRICS}')
    if p.get('group_by', 'none') not in GROUP_BY: raise ValueError(f'group_by must be one of {GROUP_BY}')
    if p['metric'] == 'shared_savings':
        terms = p.get('terms') or {}
        missing = [k for k in ('benchmark_pmpm', 'sharing_rate') if not isinstance(terms.get(k), (int, float))]
        if missing: raise ValueError('Shared savings needs supplied contract terms: ' + ', '.join(missing))
    if p['metric'] == 'change':
        if p.get('measure') not in CHANGE_MEASURES: raise ValueError(f'change measure must be one of {CHANGE_MEASURES}')
        cmp = p.get('compare') or {}
        if cmp and not all(isinstance(cmp.get(k), dict) and cmp[k].get('start') and cmp[k].get('end') for k in ('a', 'b')):
            raise ValueError('compare needs periods a and b with start/end dates')
    return {'metric': p['metric'], 'group_by': p.get('group_by', 'none'), 'period': p.get('period') or {}, 'compare': p.get('compare') or {},
            'measure': p.get('measure', ''), 'terms': p.get('terms') or {}, 'attribution': p.get('attribution', 'hybrid')}


# ---- building blocks -------------------------------------------------------------
def eligibility(db, as_of, start, end):
    """Enrolled months per patient: active coverage from its effective start, open-ended to the snapshot
    (the warehouse records no termination dates), confirmed by any benefit verification in the period."""
    people = {pid: {'age': a, 'sex': s, 'coverage_type': ct, 'status': st}
              for pid, a, s, ct, st in db.execute('SELECT patient_id, age_reported, sex_reported, coverage_type, coverage_status FROM patient')}
    starts = {pid: d for pid, d in db.execute("SELECT patient_id, min(coalesce(json_extract(payload,'$.effective_start'), event_date)) "
                                              "FROM record WHERE stream='coverage' GROUP BY 1")}
    window = _months(start, end)
    for pid, info in people.items():
        eff = starts.get(pid)
        enrolled = info['status'] == 'active' and info['coverage_type'] not in (None, 'uninsured') and eff
        info['months'] = [m for m in window if enrolled and m >= eff[:7]] if enrolled else []
    return people


def attribution(db, people, start, end, method='hybrid'):
    """PCP attribution. prospective = latest assigned primary-care relationship; plurality = clinician with
    most primary-care visits in the period (ties -> most recent visit); hybrid = prospective, else plurality."""
    assigned = {}
    for pid, payload in db.execute("SELECT patient_id, payload FROM record WHERE stream='care_team_relationships'"):
        r = json.loads(payload)
        if r.get('role') in aggregate.PCP_ROLES and r.get('status') in (None, 'assigned'):
            key = r.get('effective_start') or r.get('event_date') or ''
            if pid not in assigned or key >= assigned[pid][0]:
                assigned[pid] = (key, r.get('clinician_id'), r.get('organization_id'))
    visits = defaultdict(lambda: defaultdict(lambda: [0, '', None]))
    for pid, clin, org, d in db.execute(
            "SELECT patient_id, attending_clinician_id, organization_id, event_date FROM visit "
            "WHERE type_concept IN ('primary_care_visit') AND attending_clinician_id IS NOT NULL AND event_date BETWEEN ? AND ?",
            (start, end)):
        v = visits[pid][clin]; v[0] += 1; v[1] = max(v[1], d or ''); v[2] = org
    out = {}
    for pid in people:
        plural = max(visits[pid].items(), key=lambda kv: (kv[1][0], kv[1][1]))[0] if visits[pid] else None
        pros = assigned.get(pid)
        if method == 'prospective' or (method == 'hybrid' and pros):
            out[pid] = {'pcp': pros[1], 'practice': pros[2], 'method': 'assigned_pcp', 'visits_with_pcp': visits[pid].get(pros[1], [0])[0]} if pros else \
                       {'pcp': 'unattributed', 'practice': 'unattributed', 'method': 'none'}
        elif plural:
            out[pid] = {'pcp': plural, 'practice': visits[pid][plural][2], 'method': 'visit_plurality', 'visits_with_pcp': visits[pid][plural][0]}
        else:
            out[pid] = {'pcp': 'unattributed', 'practice': 'unattributed', 'method': 'none', 'visits_with_pcp': 0}
        out[pid]['plurality_pcp'] = plural
        out[pid]['agrees_with_plurality'] = bool(pros and plural and pros[1] == plural)
    return out


def _group_key(group_by, pid, people, attr):
    if group_by == 'none': return 'all'
    if group_by in ('pcp', 'practice'): return attr[pid][group_by] or 'unattributed'
    if group_by == 'age_band': return aggregate.age_band(people[pid]['age'])
    if group_by == 'sex': return people[pid]['sex'] or 'unknown'
    return people[pid]['coverage_type'] or 'unknown'


def _gamma_poisson(rows, events_key, exposure_key):
    """Empirical-Bayes rate shrinkage (method of moments) with 95% posterior intervals."""
    rates = [r[events_key] / r[exposure_key] for r in rows if r[exposure_key] > 0]
    if not rates: return
    total_e = sum(r[events_key] for r in rows); total_x = sum(r[exposure_key] for r in rows)
    mean = total_e / total_x if total_x else 0
    var = max(np.var(rates) - mean / max(np.mean([r[exposure_key] for r in rows if r[exposure_key] > 0]), 1e-9), 1e-12)
    # Prior worth at most one typical group's exposure, so intervals keep honest width
    # even when between-group variation is indistinguishable from noise.
    beta = min(mean / var if var > 0 else 1e6, float(np.median([r[exposure_key] for r in rows if r[exposure_key] > 0])))
    alpha = mean * beta
    from scipy.stats import gamma
    for r in rows:
        a, b = alpha + r[events_key], beta + r[exposure_key]
        r['shrunk_rate'] = a / b
        r['rate_ci95'] = [float(gamma.ppf(0.025, a, scale=1 / b)), float(gamma.ppf(0.975, a, scale=1 / b))]


def _beta_binomial(rows, num_key, den_key):
    p = [r[num_key] / r[den_key] for r in rows if r[den_key] > 0]
    if not p: return
    m = sum(r[num_key] for r in rows) / max(sum(r[den_key] for r in rows), 1)
    v = max(np.var(p), 1e-9)
    k = min(max(m * (1 - m) / v - 1, 1.0), float(np.median([r[den_key] for r in rows if r[den_key] > 0])))
    from scipy.stats import beta as beta_dist
    for r in rows:
        a, b = m * k + r[num_key], (1 - m) * k + r[den_key] - r[num_key]
        r['shrunk_rate'] = a / (a + b)
        r['rate_ci95'] = [float(beta_dist.ppf(0.025, a, b)), float(beta_dist.ppf(0.975, a, b))]


# ---- metrics ---------------------------------------------------------------------
def _utilization(db, people, attr, p, start, end):
    groups = defaultdict(lambda: {'members': 0, 'member_years': 0.0, 'ed_visits': 0, 'admissions': 0, 'index_stays': 0,
                                  'readmissions': 0, 'discharges_7d_eligible': 0, 'followup_7d': 0})
    ed = defaultdict(set)
    for pid, d in db.execute("SELECT patient_id, event_date FROM encounter WHERE type_concept='emergency_visit' AND event_date BETWEEN ? AND ?", (start, end)):
        ed[pid].add(d)
    stays = defaultdict(list)
    for pid, a, dis, rd in db.execute("SELECT patient_id, admit_date, discharge_date, readmitted_30d FROM stay WHERE admit_date BETWEEN ? AND ?", (start, end)):
        stays[pid].append((a, dis, rd))
    visits = defaultdict(list)
    for pid, d in db.execute("SELECT patient_id, event_date FROM encounter WHERE type_concept IN ('primary_care_visit','specialist_visit','telehealth_contact') "
                             "AND event_date IS NOT NULL"):
        visits[pid].append(d)
    for pid, info in people.items():
        if not info['months']: continue
        g = groups[_group_key(p['group_by'], pid, people, attr)]
        g['members'] += 1; g['member_years'] = round(g['member_years'] + len(info['months']) / 12, 4)
        g['ed_visits'] += len(ed[pid])
        for a, dis, rd in stays[pid]:
            g['admissions'] += 1; g['index_stays'] += 1; g['readmissions'] += rd
            if dis and dis <= end:
                g['discharges_7d_eligible'] += 1
                lo, hi = date.fromisoformat(dis), date.fromisoformat(dis).toordinal() + 7
                g['followup_7d'] += any(lo.toordinal() < date.fromisoformat(v).toordinal() <= hi for v in visits[pid])
    rows = [{'group': k, **v} for k, v in groups.items()]
    for r in rows:
        r['ed_per_1000'] = round(1000 * r['ed_visits'] / r['member_years'], 1) if r['member_years'] else None
        r['admits_per_1000'] = round(1000 * r['admissions'] / r['member_years'], 1) if r['member_years'] else None
        r['readmission_rate'] = round(r['readmissions'] / r['index_stays'], 4) if r['index_stays'] else None
        r['followup_7d_rate'] = round(r['followup_7d'] / r['discharges_7d_eligible'], 4) if r['discharges_7d_eligible'] else None
    _gamma_poisson(rows, 'ed_visits', 'member_years')
    for r in rows:
        if 'shrunk_rate' in r:
            r['ed_per_1000_shrunk'] = round(1000 * r.pop('shrunk_rate'), 1)
            r['ed_per_1000_ci95'] = [round(1000 * x, 1) for x in r.pop('rate_ci95')]
    rows.sort(key=lambda r: -(r.get('ed_per_1000_shrunk') or 0))
    return rows, ('Members with enrolled months in the period; ED visits = distinct ED days; admissions = distinct hospital stays; '
                  '7-day follow-up = clinic/specialist/telehealth contact within 7 days after discharge. '
                  'ED rates shrunk with empirical-Bayes gamma-Poisson; rank on the shrunk rate and its interval.')


def _claims(db, start, end):
    """Paid medical and pharmacy claims by patient and service month (cents), with arrival lag in months."""
    rows = []
    for pid, payload, available in db.execute("SELECT patient_id, payload, available_date FROM record WHERE stream='medical_claims'"):
        r = json.loads(payload)
        if r.get('status') != 'paid' or not r.get('service_date') or not (start <= r['service_date'][:10] <= end): continue
        rows.append((pid, r['service_date'][:7], r.get('allowed_cents') or 0, r.get('payer_paid_cents') or 0, 'medical', available))
    for pid, payload, available in db.execute("SELECT patient_id, payload, available_date FROM record WHERE stream='pharmacy_claims'"):
        r = json.loads(payload)
        d = (r.get('event_date') or '')[:10]
        if r.get('transaction_status') != 'paid' or not d or not (start <= d <= end): continue
        paid = (r.get('payer_paid_cents') or 0)
        rows.append((pid, d[:7], paid + (r.get('patient_paid_cents') or 0), paid, 'pharmacy', available))
    return rows


def _completion(claims, end):
    """Chain-ladder completion factors from arrival lag (months), estimated on months with full runout."""
    lag = lambda svc, arr: (int(arr[:4]) - int(svc[:4])) * 12 + int(arr[5:7]) - int(svc[5:7]) if arr else 0
    by_month = defaultdict(lambda: defaultdict(float))
    for _, svc, allowed, _, _, arr in claims: by_month[svc][max(lag(svc, arr or svc), 0)] += allowed
    end_m = end[:7]
    mature = [m for m in by_month if lag(m, end_m) >= 6]
    factors = {}
    for k in range(0, 7):
        tot = sum(sum(v.values()) for m, v in by_month.items() if m in mature)
        within = sum(sum(x for l, x in v.items() if l <= k) for m, v in by_month.items() if m in mature)
        factors[k] = (tot / within) if within else 1.0
    return {m: factors.get(min(lag(m, end_m), 6), 1.0) for m in by_month}, factors


def _cost(db, people, attr, p, start, end):
    claims = _claims(db, start, end)
    completion, factors = _completion(claims, end)
    groups = defaultdict(lambda: {'members': 0, 'member_months': 0, 'allowed': 0.0, 'paid': 0.0, 'allowed_completed': 0.0, 'pharmacy_allowed': 0.0})
    member_months = {pid: set(info['months']) for pid, info in people.items()}
    for pid, info in people.items():
        if not info['months']: continue
        g = groups[_group_key(p['group_by'], pid, people, attr)]
        g['members'] += 1; g['member_months'] += len(info['months'])
    for pid, month, allowed, paid, kind, _ in claims:
        if month not in member_months.get(pid, ()): continue
        g = groups[_group_key(p['group_by'], pid, people, attr)]
        g['allowed'] += allowed / 100; g['paid'] += paid / 100
        g['allowed_completed'] += allowed / 100 * completion.get(month, 1.0)
        if kind == 'pharmacy': g['pharmacy_allowed'] += allowed / 100
    rows = []
    for k, g in groups.items():
        mm = g['member_months'] or 1
        rows.append({'group': k, 'members': g['members'], 'member_months': g['member_months'],
                     'allowed_pmpm': round(g['allowed'] / mm, 2), 'paid_pmpm': round(g['paid'] / mm, 2),
                     'allowed_pmpm_completed': round(g['allowed_completed'] / mm, 2),
                     'ibnr_estimate': round(g['allowed_completed'] - g['allowed'], 2),
                     'pharmacy_share': round(g['pharmacy_allowed'] / g['allowed'], 4) if g['allowed'] else None})
    rows.sort(key=lambda r: -r['allowed_pmpm_completed'])
    return rows, ('Paid medical claims by service month plus paid pharmacy claims, for enrolled member-months. '
                  f'Completion via chain-ladder on arrival lag (factors by months of runout: { {k: round(v, 3) for k, v in factors.items()} }).')


def risk_scores(db, people, start, end):
    """Internal risk score: Poisson GLM of allowed cost on age band, sex and chronic concepts, mean 1.0.
    Fitted on this population (not CMS-HCC); calibration by decile is reported."""
    concepts_by = defaultdict(set)
    for c, pid in db.execute('SELECT concept, patient_id FROM concept_patient WHERE kind = ?', ('condition',)):
        if c in CHRONIC: concepts_by[pid].add(c)
    claims = _claims(db, start, end)
    cost = defaultdict(float)
    for pid, month, allowed, _, _, _ in claims:
        if month in people.get(pid, {}).get('months', ()): cost[pid] += allowed / 100
    pids = [pid for pid, info in people.items() if info['months']]
    bands = ['0-17', '18-44', '45-64', '65-74', '75+']
    cols = [f'age_{b}' for b in bands[1:]] + ['female'] + [f'has_{c}' for c in CHRONIC]
    X = np.array([[aggregate.age_band(people[p]['age']) == b for b in bands[1:]] + [people[p]['sex'] == 'F'] +
                  [c in concepts_by[p] for c in CHRONIC] for p in pids], dtype=float)
    X = np.hstack([np.ones((len(pids), 1)), X])
    exposure = np.array([len(people[p]['months']) / 12 for p in pids])
    y = np.array([cost[p] for p in pids]) / 1000  # thousands per year of exposure
    beta = np.zeros(X.shape[1]); beta[0] = math.log(max(y.sum() / exposure.sum(), 1e-6))
    for _ in range(50):  # IRLS with a small ridge for rare indicators
        mu = exposure * np.exp(np.clip(X @ beta, -20, 20))
        W = mu
        z = X @ beta + (y - mu) / np.maximum(mu, 1e-9)
        A = X.T @ (X * W[:, None]) + 1e-3 * np.eye(X.shape[1])
        new = np.linalg.solve(A, X.T @ (W * z))
        if np.max(np.abs(new - beta)) < 1e-8: beta = new; break
        beta = new
    pred = np.exp(X @ beta)
    score = pred / pred.mean()
    observed = y / np.maximum(exposure, 1e-9)
    order = np.argsort(score)
    deciles = []
    for i, chunk in enumerate(np.array_split(order, 10)):
        deciles.append({'decile': i + 1, 'mean_score': round(float(score[chunk].mean()), 3),
                        'observed_to_expected': round(float((y[chunk].sum()) / max((exposure[chunk] * pred[chunk]).sum(), 1e-9)), 3)})
    return ({p: float(s) for p, s in zip(pids, score)},
            {'coefficients': {name: round(float(b), 4) for name, b in zip(['intercept'] + cols, beta)}, 'calibration_by_decile': deciles,
             'members': len(pids)})


def _quality(db, people, attr, p, start, end):
    """Internal measures (not certified HEDIS): A1c poor control, BP control, statin therapy, 7-day follow-up."""
    has = defaultdict(set)
    for c, pid in db.execute("SELECT concept, patient_id FROM concept_patient WHERE concept IN ('diabetes','hypertension','coronary_artery_disease')"):
        has[c].add(pid)
    latest = {}
    for concept in ('hba1c', 'systolic_bp', 'diastolic_bp'):
        for pid, v in db.execute("SELECT patient_id, value_canonical FROM (SELECT patient_id, value_canonical, row_number() OVER "
                                 "(PARTITION BY patient_id ORDER BY event_date DESC, available_date DESC) rn FROM measurement "
                                 "WHERE concept=? AND value_canonical IS NOT NULL AND event_date BETWEEN ? AND ?) WHERE rn=1", (concept, start, end)):
            latest[(concept, pid)] = v
    statin = {pid for pid, payload in db.execute("SELECT patient_id, payload FROM record WHERE stream IN ('pharmacy_dispenses','pharmacy_claims') "
                                                 "AND event_date BETWEEN ? AND ?", (start, end))
              if STATIN.search(json.loads(payload).get('medication_name') or '')}
    measures = {
        'diabetes_a1c_poor_control': ('Diabetes 18-75: latest A1c > 9% or no A1c in period (lower is better)',
                                      lambda pid, i: pid in has['diabetes'] and i['age'] is not None and 18 <= i['age'] <= 75,
                                      lambda pid: latest.get(('hba1c', pid)) is None or latest[('hba1c', pid)] > 9),
        'bp_control': ('Hypertension 18-85: latest BP < 140/90 (higher is better)',
                       lambda pid, i: pid in has['hypertension'] and i['age'] is not None and 18 <= i['age'] <= 85,
                       lambda pid: (latest.get(('systolic_bp', pid)) or 999) < 140 and (latest.get(('diastolic_bp', pid)) or 999) < 90),
        'statin_therapy_diabetes': ('Diabetes 40-75: statin dispensed in period (higher is better)',
                                    lambda pid, i: pid in has['diabetes'] and i['age'] is not None and 40 <= i['age'] <= 75,
                                    lambda pid: pid in statin),
    }
    wanted = [p['measure']] if p.get('measure') in measures else list(measures)
    rows = []
    for name in wanted:
        label, den, num = measures[name]
        groups = defaultdict(lambda: {'denominator': 0, 'numerator': 0})
        for pid, info in people.items():
            if not info['months'] or not den(pid, info): continue
            g = groups[_group_key(p['group_by'], pid, people, attr)]
            g['denominator'] += 1; g['numerator'] += bool(num(pid))
        part = [{'measure': name, 'definition': label, 'group': k, **v,
                 'rate': round(v['numerator'] / v['denominator'], 4) if v['denominator'] else None} for k, v in groups.items()]
        _beta_binomial(part, 'numerator', 'denominator')
        for r in part:
            if 'shrunk_rate' in r: r['shrunk_rate'] = round(r['shrunk_rate'], 4); r['rate_ci95'] = [round(x, 4) for x in r['rate_ci95']]
        rows += sorted(part, key=lambda r: str(r['group']))
    return rows, 'Internal measure definitions on enrolled members (no official value sets in the received data); rates shrunk with beta-binomial.'


def _shared_savings(db, people, attr, p, start, end):
    """Exact settlement arithmetic on supplied terms. Benchmark is risk-adjusted by the population's
    mean internal risk score relative to 1.0 when terms ask for it."""
    t = p['terms']
    cost_rows, cost_method = _cost(db, people, attr, {**p, 'group_by': 'none'}, start, end)
    c = cost_rows[0] if cost_rows else {'member_months': 0, 'allowed_pmpm_completed': 0}
    mm = c['member_months']
    actual = c['allowed_pmpm_completed']
    benchmark = float(t['benchmark_pmpm'])
    if t.get('risk_adjust'):
        scores, _ = risk_scores(db, people, start, end)
        benchmark *= float(np.mean(list(scores.values()))) if scores else 1.0
    savings_pct = (benchmark - actual) / benchmark if benchmark else 0
    total = (benchmark - actual) * mm
    msr, mlr = float(t.get('msr', 0.02)), float(t.get('mlr', t.get('msr', 0.02)))
    quality = float(t.get('quality_score', 1.0))
    result = {'member_months': mm, 'benchmark_pmpm': round(benchmark, 2), 'actual_pmpm_completed': actual,
              'savings_pct': round(savings_pct, 4), 'gross_savings_or_loss': round(total, 2), 'terms': t}
    if savings_pct >= msr:
        shared = total * float(t['sharing_rate']) * quality
        cap = t.get('savings_cap_pct')
        if cap is not None: shared = min(shared, benchmark * mm * float(cap))
        result.update(outcome='shared_savings_earned', payment_to_aco=round(shared, 2))
    elif -savings_pct >= mlr and t.get('two_sided'):
        owed = -total * float(t.get('loss_rate', 1 - float(t['sharing_rate'])))
        cap = t.get('loss_cap_pct')
        if cap is not None: owed = min(owed, benchmark * mm * float(cap))
        result.update(outcome='shared_loss_owed', payment_from_aco=round(owed, 2))
    else:
        result.update(outcome='within_corridor', payment_to_aco=0.0)
    return [result], ('Settlement on supplied terms: savings % vs MSR/MLR corridor, sharing/loss rates, quality factor and caps; '
                      'actual cost = completion-adjusted allowed PMPM for enrolled member-months. ' + cost_method)


def _measure_by_segment(db, measure, group_by, start, end, keep):
    """(segment -> (exposure, numerator)) for one period. Exposure = member-months (pmpm) or member-years."""
    people = eligibility(db, end, start, end)
    if keep is not None: people = {k: v for k, v in people.items() if k in keep}
    attr = attribution(db, people, start, end)
    out = defaultdict(lambda: [0.0, 0.0])
    for pid, info in people.items():
        if info['months']:
            out[_group_key(group_by, pid, people, attr)][0] += len(info['months']) / (1 if measure == 'pmpm' else 12)
    months = {pid: set(i['months']) for pid, i in people.items()}
    if measure == 'pmpm':
        claims = _claims(db, start, end)
        completion, _ = _completion(claims, end)
        for pid, month, allowed, *_ in claims:
            if month in months.get(pid, ()): out[_group_key(group_by, pid, people, attr)][1] += allowed / 100 * completion.get(month, 1.0)
    else:
        if measure == 'ed_rate':
            rows = db.execute("SELECT DISTINCT patient_id, event_date FROM encounter WHERE type_concept='emergency_visit' AND event_date BETWEEN ? AND ?", (start, end))
        else:
            rows = db.execute("SELECT patient_id, admit_date FROM stay WHERE admit_date BETWEEN ? AND ?", (start, end))
        for pid, d in rows:
            if d and d[:7] in months.get(pid, ()): out[_group_key(group_by, pid, people, attr)][1] += 1
    return out


def _feed_checks(db, measure, a, b):
    """Did the data feeding the measure change? Monthly record volume per source, CUSUM vs period A."""
    from ...process_monitoring import monitor
    stream = {'pmpm': 'medical_claims', 'ed_rate': 'encounters', 'admit_rate': 'encounters'}[measure]
    counts = defaultdict(lambda: defaultdict(int))
    for org, month, n in db.execute("SELECT organization_id, substr(event_date,1,7), count(*) FROM record WHERE stream=? AND event_date BETWEEN ? AND ? GROUP BY 1,2",
                                    (stream, a['start'], b['end'])):
        counts[org][month] = n
    months_a, months_b = _months(a['start'], a['end']), _months(b['start'], b['end'])
    alerts = []
    for org, series in counts.items():
        ref = [series.get(m, 0) for m in months_a]
        if len(ref) < 3 or sum(ref) < 30: continue
        mean, sd = float(np.mean(ref)), float(max(np.std(ref), math.sqrt(max(np.mean(ref), 1))))
        res = monitor([float(series.get(m, 0)) for m in months_b], reference_mean=mean, reference_sd=sd)
        flagged = [months_b[pt['index']] for pt in res['points'] if pt['investigate_source_change']]
        if flagged:
            alerts.append({'source': org, 'stream': stream, 'reference_monthly_mean': round(mean, 1),
                           'period_b_monthly_mean': round(float(np.mean([series.get(m, 0) for m in months_b])), 1), 'first_flagged_month': flagged[0]})
    # Event-type gaps: the measured event vanishes while its stream keeps flowing (coding/feed change, not care).
    if measure in ('ed_rate', 'admit_rate'):
        sql = ("SELECT substr(event_date,1,7), count(DISTINCT patient_id || event_date) FROM encounter WHERE type_concept='emergency_visit' "
               "AND event_date BETWEEN ? AND ? GROUP BY 1" if measure == 'ed_rate' else
               "SELECT substr(admit_date,1,7), count(*) FROM stay WHERE admit_date BETWEEN ? AND ? GROUP BY 1")
        events = dict(db.execute(sql, (a['start'], b['end'])))
        totals = dict(db.execute("SELECT substr(event_date,1,7), count(*) FROM record WHERE stream=? AND event_date BETWEEN ? AND ? GROUP BY 1",
                                 (stream, a['start'], b['end'])))
        for label, months in (('period_a', months_a), ('period_b', months_b)):
            silent = [m for m in months if totals.get(m, 0) > 100 and events.get(m, 0) == 0]
            if len(silent) >= 3:
                alerts.append({'source': 'all', 'stream': stream, 'kind': 'event_type_gap', 'period': label,
                               'months_without_events': len(silent), 'of_months': len(months),
                               'detail': f'{len(silent)} of {len(months)} months in {label} record {stream} volume but none of this event type: '
                                         'likely a coding or feed change, not a change in care.'})
    return alerts


def _change(db, p, as_of, keep):
    """Kitagawa decomposition of a rate change into segment mix and within-segment rate effects, plus feed checks."""
    end_b = (p['compare'].get('b') or {}).get('end') or as_of
    b = p['compare'].get('b') or {'start': f'{int(end_b[:4]) - 1}{end_b[4:7]}-01', 'end': end_b}
    a = p['compare'].get('a') or {'start': f'{int(b["start"][:4]) - 1}{b["start"][4:]}', 'end': f'{int(b["start"][:4]) - 1}{b["start"][4:7]}-01'}
    if not p['compare'].get('a'):
        a_end = date.fromordinal(date.fromisoformat(b['start']).toordinal() - 1).isoformat()
        a = {'start': f'{int(b["start"][:4]) - 1}{b["start"][4:]}', 'end': a_end}
    group_by = p['group_by'] if p['group_by'] != 'none' else 'coverage_type'
    A = _measure_by_segment(db, p['measure'], group_by, a['start'], a['end'], keep)
    B = _measure_by_segment(db, p['measure'], group_by, b['start'], b['end'], keep)
    scale = 1.0 if p['measure'] == 'pmpm' else 1000.0
    ea, eb = sum(x[0] for x in A.values()), sum(x[0] for x in B.values())
    total_a = scale * sum(x[1] for x in A.values()) / ea if ea else 0
    total_b = scale * sum(x[1] for x in B.values()) / eb if eb else 0
    rows, rate_eff, mix_eff = [], 0.0, 0.0
    for seg in sorted(set(A) | set(B)):
        xa, xb = A.get(seg, [0, 0]), B.get(seg, [0, 0])
        wa, wb = (xa[0] / ea if ea else 0), (xb[0] / eb if eb else 0)
        ra, rb = (scale * xa[1] / xa[0] if xa[0] else 0), (scale * xb[1] / xb[0] if xb[0] else 0)
        r_eff, m_eff = (rb - ra) * (wa + wb) / 2, (wb - wa) * (ra + rb) / 2
        rate_eff += r_eff; mix_eff += m_eff
        rows.append({'segment': seg, 'weight_a': round(wa, 4), 'weight_b': round(wb, 4), 'rate_a': round(ra, 2), 'rate_b': round(rb, 2),
                     'rate_effect': round(r_eff, 2), 'mix_effect': round(m_eff, 2)})
    rows.sort(key=lambda r: -abs(r['rate_effect']) - abs(r['mix_effect']))
    feeds = _feed_checks(db, p['measure'], a, b)
    summary = {'measure': p['measure'], 'period_a': a, 'period_b': b, 'value_a': round(total_a, 2), 'value_b': round(total_b, 2),
               'change': round(total_b - total_a, 2), 'rate_effect': round(rate_eff, 2), 'mix_effect': round(mix_eff, 2),
               'segments_by': group_by, 'feed_alerts': feeds,
               'reading': (next((f['detail'] for f in feeds if f.get('kind') == 'event_type_gap'), None) or
                           ('Feed volume changed in ' + ', '.join(f["source"] for f in feeds[:3]) + '; part of the change may be data, not care.')
                           if feeds else 'No source-volume change detected in the feeding stream.')}
    return [summary] + rows, ('Kitagawa decomposition: change = sum over segments of rate change x average weight (rate effect) + '
                              'weight change x average rate (mix effect). Feed check: CUSUM on monthly record volume per source vs period A.')


def execute(path, patient_ids, params, as_of=None):
    p = validate(params)
    db = _connect(path)
    try:
        as_of = as_of or dict(db.execute('SELECT key, value FROM meta')).get('as_of')
        start, end = _period(p, as_of)
        people = eligibility(db, as_of, start, end)
        if patient_ids is not None:
            keep = set(patient_ids); people = {k: v for k, v in people.items() if k in keep}
        attr = attribution(db, people, start, end, p['attribution'])
        metric = p['metric']
        if metric == 'eligibility':
            groups = defaultdict(lambda: {'members': 0, 'member_months': 0})
            for pid, info in people.items():
                if info['months']:
                    g = groups[_group_key(p['group_by'], pid, people, attr)]; g['members'] += 1; g['member_months'] += len(info['months'])
            rows = [{'group': k, **v} for k, v in sorted(groups.items())]
            method = 'Active, insured coverage from effective start through the period end (no termination dates are received).'
        elif metric == 'attribution':
            groups = defaultdict(lambda: {'members': 0, 'assigned_pcp': 0, 'visit_plurality': 0, 'unattributed': 0, 'agrees_with_plurality': 0})
            for pid, a in attr.items():
                if not people[pid]['months']: continue
                g = groups[_group_key(p['group_by'] if p['group_by'] != 'none' else 'practice', pid, people, attr)]
                g['members'] += 1; g[a['method'] if a['method'] != 'none' else 'unattributed'] += 1; g['agrees_with_plurality'] += a['agrees_with_plurality']
            rows = sorted(({'group': k, **v} for k, v in groups.items()), key=lambda r: -r['members'])
            method = f"{p['attribution']} attribution: assigned PCP relationship, else plurality of primary-care visits in the period."
        elif metric == 'utilization':
            rows, method = _utilization(db, people, attr, p, start, end)
        elif metric == 'cost':
            rows, method = _cost(db, people, attr, p, start, end)
        elif metric == 'risk':
            scores, fit = risk_scores(db, people, start, end)
            groups = defaultdict(list)
            for pid, s in scores.items(): groups[_group_key(p['group_by'], pid, people, attr)].append(s)
            rows = sorted(({'group': k, 'members': len(v), 'mean_risk_score': round(float(np.mean(v)), 3)} for k, v in groups.items()),
                          key=lambda r: -r['mean_risk_score'])
            method = 'Internal risk score (Poisson GLM on age band, sex, chronic conditions; mean 1.0; not CMS-HCC). Fit: ' + json.dumps(fit['calibration_by_decile'][:3]) + '...'
        elif metric == 'quality':
            rows, method = _quality(db, people, attr, p, start, end)
        elif metric == 'change':
            rows, method = _change(db, p, as_of, set(patient_ids) if patient_ids is not None else None)
        else:
            rows, method = _shared_savings(db, people, attr, p, start, end)
    finally:
        db.close()
    return {'schema': VERSION, 'metric': p['metric'], 'parameters': p, 'period': {'start': start, 'end': end},
            'rows': rows, 'method': method, 'members_in_scope': sum(1 for i in people.values() if i['months'])}
