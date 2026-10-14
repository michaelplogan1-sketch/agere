"""Risk-standardized outcome rates (CMS-style): readmission, post-discharge ED visits, ED revisits, mortality, length of stay.

Answers "risk-standardized readmission rate by hospital", "which hospitals are worse than the network on 30-day readmissions
after case mix", "excess readmission ratio", "observed-to-expected ED revisits by practice", "funnel plot of readmissions" and
"O/E length of stay". Mortality needs death data (date of death for in-hospital and post-discharge deaths); without it the
engine refuses and names that missing input.

Index events and outcomes:
  readmission        index = hospital discharges (inpatient; observation optional) with complete follow-up before the snapshot;
                     outcome = an inpatient admission 1..window days after discharge (no planned-readmission algorithm: the
                     warehouse carries no planned flag)
  post_discharge_ed  same index; outcome = an ED visit 1..window days after discharge that did not lead to an admission
  ed_revisit         index = ED treat-and-release visits; outcome = an ED visit or admission 1..window days later
  mortality          index = each patient's first admission in the period; outcome = death within window days of admission
                     (needs death records)
  length_of_stay     index = discharges with a known length of stay; outcome = days (Poisson model)
Case mix (patient level, index date): age band, sex, chronic conditions recorded on or before the index date
(aggregate.BURDEN_CONCEPTS), prior-year acute stays (0 / 1 / 2+), observation status. Expected = sum of predicted
probabilities from a patient-level logistic regression (Poisson log-linear for length of stay) without unit terms: the
CMS "expected" (average unit effect). Unit effects then come from profile.analyse, the same hierarchical machinery the fair
comparison uses: quasi-Poisson overdispersion, a Poisson-gamma random effect on O/E with hyperparameters integrated on a
grid, 4,000 joint posterior draws, Buhlmann-Straub credibility, ranks. The shrunken O/E is the standardized ratio (SRR;
for readmissions the HRRP excess readmission ratio analogue); RSRR = SRR x network observed rate, with 95% intervals.
Worse / better than the network = 95% interval excludes the network rate (P(above) >= 97.5% / <= 2.5%). Funnel plot:
O/E against expected events with Poisson 95% and 99.8% limits (Spiegelhalter 2005).
"""
from collections import defaultdict
import math

import numpy as np
from scipy.stats import poisson

from . import aggregate, profile, safety_data as sd

VERSION = 'risk-outcomes/1'
OUTCOMES = ('readmission', 'post_discharge_ed', 'ed_revisit', 'mortality', 'length_of_stay')
UNITS = ('hospital', 'practice', 'pcp')
ADJUST = ('age_sex_conditions', 'age_sex', 'none')
FIELDS = {'outcome', 'unit', 'window_days', 'period', 'include_observation', 'adjust', 'focus', 'top', 'period_days'}
LABELS = {'readmission': 'all-cause inpatient readmission', 'post_discharge_ed': 'ED visit after discharge (not admitted)',
          'ed_revisit': 'return to the ED or admission after an ED treat-and-release visit', 'mortality': 'death',
          'length_of_stay': 'length of stay (days)'}
MIN_CASES = 25  # CMS does not publicly report units with fewer cases
MIN_EVENTS = 10  # below this many outcome events network-wide, unit effects are not estimated (O/E only)
DEATH_STREAMS = ('death_records', 'deaths', 'vital_status', 'death_index', 'mortality')
DEATH_FIELDS = ('death_date', 'deceased_date', 'date_of_death')
AGE_REF = '45-64'


def validate(p):
    if not isinstance(p, dict) or set(p) - FIELDS: raise ValueError(f'risk_outcomes parameters: {sorted(FIELDS)}')
    outcome = p.get('outcome') or 'readmission'
    if outcome not in OUTCOMES: raise ValueError(f'outcome must be one of {OUTCOMES}')
    unit = p.get('unit') or 'hospital'
    if unit not in UNITS: raise ValueError(f'unit must be one of {UNITS}')
    window = p.get('window_days', 30)
    if type(window) is not int or not 1 <= window <= 365: raise ValueError('window_days 1..365')
    pd = p.get('period_days', 1095)
    if type(pd) is not int or not 90 <= pd <= 3650: raise ValueError('period_days 90..3650 (index window when no period is given)')
    period = p.get('period') or {}
    if not isinstance(period, dict) or set(period) - {'start', 'end'} or any(v and sd.day(v) != v for v in period.values()):
        raise ValueError('period takes ISO start/end dates')
    obs = p.get('include_observation', False)
    if type(obs) is not bool: raise ValueError('include_observation: true/false')
    adjust = p.get('adjust') or 'age_sex_conditions'
    if adjust not in ADJUST: raise ValueError(f'adjust must be one of {ADJUST}')
    focus = p.get('focus') or ''
    if not isinstance(focus, str) or len(focus) > 80: raise ValueError('focus: one unit id')
    top = p.get('top', 100)
    if type(top) is not int or not 1 <= top <= 500: raise ValueError('top 1..500')
    return {'outcome': outcome, 'unit': unit, 'window_days': window, 'period': {'start': period.get('start', ''), 'end': period.get('end', '')},
            'period_days': pd, 'include_observation': obs, 'adjust': adjust, 'focus': focus, 'top': top}


# ---- models (pure) -------------------------------------------------------------------------------------------------------------
def fit_glm(X, y, family='binomial', ridge=1.0, max_iter=60, tol=1e-9):
    """IRLS for logistic (binomial) or log-linear Poisson regression with a weak N(0, 1/ridge) prior (ridge) on non-intercept
    terms, so rare outcomes and empty cells cannot separate; negligible once events are plentiful.
    Returns (beta, fitted mean, converged)."""
    X = np.asarray(X, float); y = np.asarray(y, float)
    n, k = X.shape
    beta = np.zeros(k)
    m0 = y.mean()
    beta[0] = math.log(max(m0, 1e-6) / max(1 - m0, 1e-6)) if family == 'binomial' else math.log(max(m0, 1e-6))
    pen = np.full(k, ridge); pen[0] = 0.0
    ok = False
    for _ in range(max_iter):
        eta = np.clip(X @ beta, -30, 30)
        if family == 'binomial':
            mu = 1 / (1 + np.exp(-eta)); w = np.maximum(mu * (1 - mu), 1e-10)
        else:
            mu = np.exp(eta); w = np.maximum(mu, 1e-10)
        grad = X.T @ (y - mu) - pen * beta
        H = (X * w[:, None]).T @ X + np.diag(pen) + 1e-12 * np.eye(k)
        step = np.linalg.solve(H, grad)
        beta = beta + step
        if np.max(np.abs(step)) < tol: ok = True; break
    eta = np.clip(X @ beta, -30, 30)
    mu = 1 / (1 + np.exp(-eta)) if family == 'binomial' else np.exp(eta)
    return beta, mu, ok


def c_statistic(y, p):
    """Area under the ROC curve (Mann-Whitney with average ranks for ties)."""
    y = np.asarray(y, int); p = np.asarray(p, float)
    pos, neg = y.sum(), len(y) - y.sum()
    if not pos or not neg: return None
    order = np.argsort(p, kind='mergesort'); ranks = np.empty(len(p)); ranks[order] = np.arange(1, len(p) + 1)
    for v in np.unique(p):  # average ranks for ties
        m = p == v
        if m.sum() > 1: ranks[m] = ranks[m].mean()
    return float((ranks[y == 1].sum() - pos * (pos + 1) / 2) / (pos * neg))


def design(rows, adjust):
    """rows: dicts with age_band, sex, conditions (set), prior (0/1/2), observation (bool) -> (X, column names). Constant columns dropped."""
    names, cols = ['intercept'], [np.ones(len(rows))]
    if adjust != 'none':
        for band in ('0-17', '18-44', '65-74', '75+', 'unknown'):
            cols.append(np.array([r['age_band'] == band for r in rows], float)); names.append(f'age {band}')
        cols.append(np.array([r['sex'] == 'M' for r in rows], float)); names.append('male')
    if adjust == 'age_sex_conditions':
        for c in aggregate.BURDEN_CONCEPTS:
            cols.append(np.array([c in r['conditions'] for r in rows], float)); names.append(c)
        cols.append(np.array([r['prior'] == 1 for r in rows], float)); names.append('1 prior-year stay')
        cols.append(np.array([r['prior'] >= 2 for r in rows], float)); names.append('2+ prior-year stays')
        cols.append(np.array([bool(r.get('observation')) for r in rows], float)); names.append('observation stay')
    keep = [0] + [i for i in range(1, len(cols)) if 0 < cols[i].sum() < len(rows)]
    return np.column_stack([cols[i] for i in keep]), [names[i] for i in keep]


def standardise(units, y, X, names, family='binomial', seed=profile.SEED, draws=profile.DRAWS):
    """Patient-level case-mix model for E, then profile.analyse (hierarchical O/E) per unit. Returns (unit rows, model summary).
    With fewer than MIN_EVENTS outcome events network-wide the unit effects are not estimable: O, E and O/E only."""
    y = np.asarray(y, float)
    beta, mu, ok = fit_glm(X, y, family)
    rate = y.sum() / len(y)
    estimable = bool(family != 'binomial' or y.sum() >= MIN_EVENTS)
    out = []
    if estimable:
        res = profile.analyse(list(units), y, np.ones(len(y)), mu, 'count', 'bayes', draws=draws, seed=seed)
        for j, u in enumerate(res['labels']):
            n, O, E = int(res['n'][j]), float(res['observed'][j]), float(res['expected'][j])
            flag = {'truly_high': 'worse_than_network', 'truly_low': 'better_than_network'}.get(str(res['flag'][j]), 'no_different')
            out.append({'unit': u, 'index_cases': n, 'observed': round(O, 2), 'expected': round(E, 3),
                        'observed_rate': round(O / n, 5) if n else None, 'expected_rate': round(E / n, 5) if n else None,
                        'oe_ratio': round(O / E, 4) if E > 0 else None, 'srr': round(float(res['mean'][j]), 4),
                        'srr_ci95': [round(float(max(res['lo'][j], 0)), 4), round(float(res['hi'][j]), 4)],
                        'rsr': round(float(res['mean'][j]) * rate, 5), 'rsr_ci95': [round(float(max(res['lo'][j], 0)) * rate, 5), round(float(res['hi'][j]) * rate, 5)],
                        'p_worse_than_network': round(float(res['p_above'][j]), 4), 'rank': round(float(res['rank_mean'][j]), 1),
                        'rank_ci95': [int(res['rank_lo'][j]), int(res['rank_hi'][j])], 'credibility_z': round(float(res['Z'][j]), 3),
                        'flag': flag, 'small_unit': bool(n < MIN_CASES)})
        hyper = {k: (round(v, 6) if isinstance(v, float) else v) for k, v in res['hyper'].items()}
    else:
        agg = defaultdict(lambda: [0, 0.0, 0.0])
        for u, yi, mi in zip(units, y, mu): a = agg[u]; a[0] += 1; a[1] += yi; a[2] += mi
        for u, (n, O, E) in agg.items():
            out.append({'unit': u, 'index_cases': n, 'observed': round(O, 2), 'expected': round(E, 3), 'observed_rate': round(O / n, 5),
                        'expected_rate': round(E / n, 5), 'oe_ratio': round(O / E, 4) if E > 0 else None, 'srr': None, 'srr_ci95': None,
                        'rsr': None, 'rsr_ci95': None, 'p_worse_than_network': None, 'rank': None, 'rank_ci95': None, 'credibility_z': None,
                        'flag': 'insufficient_events', 'small_unit': bool(n < MIN_CASES)})
        hyper = {}
    out.sort(key=lambda r: (-(r['srr'] if r['srr'] is not None else r['oe_ratio'] or 0), str(r['unit'])))
    pred = np.asarray(mu)
    cstat = c_statistic(y, pred) if family == 'binomial' else None
    ratio = lambda b: round(math.exp(min(max(float(b), -50), 50)), 3)
    model = {'family': 'logistic' if family == 'binomial' else 'Poisson log-linear', 'converged': bool(ok), 'cases': int(len(y)),
             'events': round(float(y.sum()), 2), 'network_rate': round(float(rate), 5), 'unit_effects_estimable': estimable,
             'coefficients': [{'term': nm, 'estimate': round(float(b), 4), **({} if nm == 'intercept' else
                                                                              {'odds_ratio' if family == 'binomial' else 'rate_ratio': ratio(b)})}
                              for nm, b in zip(names, beta)],
             'c_statistic': round(cstat, 4) if cstat is not None else None,
             'calibration_in_the_large': round(float(y.sum() / pred.sum()), 4) if pred.sum() > 0 else None, 'hyper': hyper}
    return out, model


def funnel(units, levels=(0.95, 0.998)):
    """Poisson control limits for O/E around 1 over the range of expected counts, plus each unit's point."""
    es = [u['expected'] for u in units if u['expected'] and u['expected'] > 0]
    if not es: return {'limits': [], 'points': []}
    grid = np.unique(np.round(np.geomspace(max(min(es) * 0.8, 0.05), max(es) * 1.2, 40), 4))
    limits = []
    for e in grid:
        row = {'expected': float(e)}
        for lv in levels:
            a = (1 - lv) / 2
            row[f'lower_{lv}'] = round(float(poisson.ppf(a, e)) / e, 4); row[f'upper_{lv}'] = round(float(poisson.ppf(1 - a, e)) / e, 4)
        limits.append(row)
    pts = []
    for u in units:
        e, o = u['expected'], u['observed']
        if not e or e <= 0: continue
        out95 = o > poisson.ppf(0.975, e) or o < poisson.ppf(0.025, e)
        out998 = o > poisson.ppf(0.999, e) or o < poisson.ppf(0.001, e)
        pts.append({'unit': u['unit'], 'expected': e, 'oe_ratio': u['oe_ratio'], 'outside_95': bool(out95), 'outside_998': bool(out998)})
    return {'limits': limits, 'points': pts, 'levels': list(levels)}


# ---- data ------------------------------------------------------------------------------------------------------------------
def death_source(db):
    """Where death dates live in this build, or None."""
    t = sd.tables(db)
    pc = sd.columns(db, 'patient') if 'patient' in t else set()
    col = next((c for c in DEATH_FIELDS if c in pc), None)
    if col: return ('patient', col)
    st = sd.streams(db)
    s = next((s for s in DEATH_STREAMS if s in st), None)
    return ('stream', s) if s else None


def death_dates(db, src):
    out = {}
    if src[0] == 'patient':
        for pid, d in db.execute(f'SELECT patient_id, {src[1]} FROM patient'):
            if sd.day(d): out[pid] = sd.day(d)
    else:
        import json
        for pid, d, payload in db.execute('SELECT patient_id, event_date, payload FROM record WHERE stream = ?', (src[1],)):
            r = json.loads(payload)
            dd = next((sd.day(r.get(k)) for k in DEATH_FIELDS if sd.day(r.get(k))), None) or sd.day(d)
            if dd and (pid not in out or dd < out[pid]): out[pid] = dd
    return out


def capability(db):
    t = sd.tables(db)
    cap = {'version': sd.meta(db).get('version'), 'stays': 'stay' in t, 'visits': 'visit' in t, 'death': death_source(db),
           'discharge_known': 'stay' in t and 'discharge_known' in sd.columns(db, 'stay'),
           'concept_dates': 'concept_patient' in t and 'first_date' in sd.columns(db, 'concept_patient')}
    gaps = []
    if not cap['death']:
        gaps.append('No death records (date of death in hospital or after discharge, e.g. a linked death index): mortality is not measurable.')
    gaps.append('No planned-readmission flag or principal procedure on stays: readmissions are all-cause (CMS removes planned readmissions).')
    gaps.append('No condition-specific index cohorts (stays carry no principal diagnosis): condition cohorts use the patient population as an approximation.')
    gaps.append('HRRP payment-reduction simulation needs DRG base payments by condition and the peer-group median ERR (CMS files, not received).')
    if not cap['concept_dates']: gaps.append('No first dates on chronic conditions: comorbidities are taken as recorded at any time.')
    cap['data_gaps'] = gaps
    return cap


def _conditions(db, cap, pids):
    out = defaultdict(dict)
    q = 'SELECT patient_id, concept, first_date FROM concept_patient WHERE concept IN (%s)' % ','.join('?' * len(aggregate.BURDEN_CONCEPTS))
    for pid, c, first in db.execute(q if cap['concept_dates'] else q.replace('first_date', 'NULL'), aggregate.BURDEN_CONCEPTS):
        if pids is None or pid in pids: out[pid][c] = sd.day(first) or '0000-01-01'
    return out


def _stays(db, cap):
    cols = sd.columns(db, 'stay')
    known = 'discharge_known' if 'discharge_known' in cols else '1'
    los = 'los_days' if 'los_days' in cols else 'NULL'
    out = defaultdict(list)
    for pid, a, d, typ, org, k, l in db.execute(f'SELECT patient_id, admit_date, discharge_date, type, organization_id, {known}, {los} FROM stay'):
        a = sd.day(a)
        if not a: continue
        kind = 'observation' if 'observation' in (typ or '').lower() else 'inpatient' if 'inpatient' in (typ or '').lower() else None
        if kind is None: continue
        dd = sd.day(d) if k else None
        out[pid].append({'admit': a, 'discharge': dd, 'kind': kind, 'organization': org,
                         'los': l if isinstance(l, (int, float)) else (sd.days_between(a, dd) if dd else None)})
    for v in out.values(): v.sort(key=lambda s: s['admit'])
    return out


def index_cases(db, cap, patient_ids, p, as_of):
    """[(unit, y, covariate row, detail)] for the outcome."""
    pids = set(patient_ids) if patient_ids is not None else None
    people = sd.patients(db, pids)
    end = p['period']['end'] or sd.shift(as_of, -(p['window_days'] if p['outcome'] != 'length_of_stay' else 0))
    end = min(end, sd.shift(as_of, -p['window_days'])) if p['outcome'] != 'length_of_stay' else min(end, as_of)
    start = p['period']['start'] or sd.shift(end, -p['period_days'] + 1)
    stays = _stays(db, cap) if cap['stays'] else {}
    conds = _conditions(db, cap, pids) if p['adjust'] == 'age_sex_conditions' else {}
    visits = sd.visits(db, pids) if p['outcome'] in ('post_discharge_ed', 'ed_revisit') else {}
    deaths = death_dates(db, cap['death']) if p['outcome'] == 'mortality' and cap['death'] else {}
    rows = []

    def covariates(pid, when, observation=False):
        pat = people.get(pid) or {}
        prior = sum(1 for s in stays.get(pid, ()) if sd.shift(when, -365) <= s['admit'] < when)
        return {'age_band': aggregate.age_band(pat.get('age')), 'sex': pat.get('sex') or 'unknown',
                'conditions': {c for c, d in conds.get(pid, {}).items() if d <= when}, 'prior': min(prior, 2), 'observation': observation}

    def unit_of(pid, org):
        pat = people.get(pid) or {}
        return org if p['unit'] == 'hospital' else pat.get('practice') if p['unit'] == 'practice' else pat.get('pcp')

    if p['outcome'] == 'ed_revisit':
        for pid, vs in visits.items():
            if pid not in people: continue
            eds = [v for v in vs if v[4] == 'emergency_visit']
            admits = sorted(s['admit'] for s in stays.get(pid, ()))
            ed_days = sorted({v[0] for v in eds})
            for v in eds:
                if not start <= v[0] <= end: continue
                if sd.first_in(admits, v[0], sd.shift(v[0], 1)): continue  # admitted: not treat-and-release
                lo, hi = sd.shift(v[0], 1), sd.shift(v[0], p['window_days'])
                hit = bool(sd.first_in(ed_days, lo, hi) or sd.first_in(admits, lo, hi))
                u = unit_of(pid, v[2])
                if u: rows.append((u, float(hit), covariates(pid, v[0]), {'patient_id': pid, 'index_date': v[0]}))
        return rows, {'start': start, 'end': end}
    for pid, ss in stays.items():
        if pid not in people: continue
        admits = [s['admit'] for s in ss if s['kind'] == 'inpatient' or p['include_observation']]
        for s in ss:
            if s['kind'] == 'observation' and not p['include_observation']: continue
            u = unit_of(pid, s['organization'])
            if not u: continue
            if p['outcome'] == 'mortality':  # one index admission per patient (the first in the period), as CMS mortality measures do
                if not start <= s['admit'] <= end or any(start <= x['admit'] < s['admit'] and (x['kind'] == 'inpatient' or p['include_observation'])
                                                         for x in ss): continue
                d = deaths.get(pid)
                y = float(bool(d and s['admit'] <= d <= sd.shift(s['admit'], p['window_days'])))
                rows.append((u, y, covariates(pid, s['admit'], s['kind'] == 'observation'), {'patient_id': pid, 'index_date': s['admit']}))
                continue
            if not s['discharge'] or not start <= s['discharge'] <= end: continue
            if deaths.get(pid) and deaths[pid] <= s['discharge']: continue  # died in hospital: no readmission risk
            if p['outcome'] == 'length_of_stay':
                if s['los'] is None: continue
                y = float(max(0, s['los']))
            elif p['outcome'] == 'readmission':
                y = float(bool(sd.first_in(admits, sd.shift(s['discharge'], 1), sd.shift(s['discharge'], p['window_days']))))
            else:  # post_discharge_ed: ED visit 1..window days after discharge not followed by an admission within a day
                ed = [v[0] for v in visits.get(pid, ()) if v[4] == 'emergency_visit' and sd.shift(s['discharge'], 1) <= v[0] <= sd.shift(s['discharge'], p['window_days'])]
                all_admits = [x['admit'] for x in ss]
                y = float(any(not sd.first_in(sorted(all_admits), d, sd.shift(d, 1)) for d in ed))
            rows.append((u, y, covariates(pid, s['admit'], s['kind'] == 'observation'), {'patient_id': pid, 'index_date': s['discharge']}))
    return rows, {'start': start, 'end': end}


# ---- entry points ----------------------------------------------------------------------------------------------------------
def run(db, patient_ids, params, as_of=None):
    p = validate(params)
    as_of = sd.as_of_of(db, as_of)
    cap = capability(db)
    report = {'schema': VERSION, 'parameters': p, 'as_of': as_of, 'outcome_label': LABELS[p['outcome']], 'data_gaps': cap['data_gaps'],
              'capability': {k: v for k, v in cap.items() if k != 'data_gaps'}, 'limitations': LIMITATIONS, 'units': [], 'notes': []}
    if p['outcome'] == 'mortality' and not cap['death']:
        report.update(status='unsupported', missing=['death records (date of death for in-hospital and post-discharge deaths, e.g. a linked death index)'],
                      period=None, reason='Mortality needs death records, which this warehouse does not carry.')
        report['method'] = method(report); return report
    if p['outcome'] == 'ed_revisit' and not cap['visits'] or p['outcome'] != 'ed_revisit' and not cap['stays']:
        report.update(status='unsupported', missing=['hospital stays' if p['outcome'] != 'ed_revisit' else 'ED visits'], period=None,
                      reason='The index events are not in this warehouse.')
        report['method'] = method(report); return report
    rows, period = index_cases(db, cap, patient_ids, p, as_of)
    report['period'] = period
    units = {r[0] for r in rows}
    if len(rows) < 10 or len(units) < 2:
        report.update(status='empty', reason=f'{len(rows)} index cases in {len(units)} units: nothing to standardise.')
        report['method'] = method(report); return report
    X, names = design([r[2] for r in rows], p['adjust'])
    y = [r[1] for r in rows]
    if p['outcome'] != 'length_of_stay' and sum(y) == 0:
        report.update(status='empty', reason=f'No {LABELS[p["outcome"]]} events among {len(rows)} index cases: rates cannot be compared.')
        report['method'] = method(report); return report
    out, model = standardise([r[0] for r in rows], y, X, names, 'poisson' if p['outcome'] == 'length_of_stay' else 'binomial')
    rate = model['network_rate']
    report.update(status='answered', units=out[:p['top']], units_total=len(out), model=model, network_rate=rate,
                  counts={f: sum(u['flag'] == f for u in out) for f in ('worse_than_network', 'better_than_network', 'no_different')},
                  small_units=sum(u['small_unit'] for u in out), funnel=funnel(out))
    if p['focus']:
        hit = next((u for u in out if str(u['unit']).lower() == p['focus'].lower()), None)
        report['focus'] = hit
        if hit is None: report['notes'].append(f"{p['focus']} is not among the {len(out)} units with index cases.")
    if report['small_units']:
        report['notes'].append(f"{report['small_units']} of {len(out)} units have fewer than {MIN_CASES} index cases (CMS would not publicly report them); "
                               'their estimates are shrunk strongly toward the network.')
    if not model['unit_effects_estimable']:
        report['notes'].append(f"Only {model['events']:g} outcome events network-wide (fewer than {MIN_EVENTS}): unit effects are not estimable, "
                               'so observed, expected and O/E are shown without standardized rates or flags.')
    elif model['events'] < 30 and p['outcome'] != 'length_of_stay':
        report['notes'].append(f"Only {model['events']:g} outcome events network-wide: estimates are shrunk strongly toward the network; intervals are wide.")
    report['method'] = method(report)
    return report


def execute(path, patient_ids, params, as_of=None):
    db = sd.connect(path)
    try:
        return run(db, patient_ids, params, as_of)
    finally:
        db.close()


LIMITATIONS = ['Standardised rates compare units with the network\'s own case mix (indirect standardisation), not with a national benchmark: '
               'no CMS Care Compare benchmark file is received.',
               'Case mix covers age, sex, recorded chronic conditions, prior-year stays and observation status; unmeasured severity remains.',
               'Units are where the index stay or ED visit happened (hospital) or the attributed practice / PCP; attribution is not causal responsibility.',
               'Synthetic data.']


def method(report):
    p = report['parameters']
    unit = {'hospital': 'the facility of the index stay / ED visit', 'practice': 'the attributed practice', 'pcp': 'the attributed PCP'}[p['unit']]
    t = (f"Risk-standardized {LABELS[p['outcome']]} within {p['window_days']} days, by {unit}. ")
    if report.get('status') == 'unsupported':
        return t + 'Not computed: ' + report.get('reason', '')
    per = report.get('period') or {}
    t += (f"Index events {per.get('start')}..{per.get('end')} (complete {p['window_days']}-day follow-up before the snapshot). "
          f"Expected = sum of patient-level predicted {'days' if p['outcome'] == 'length_of_stay' else 'probabilities'} from a "
          f"{'Poisson log-linear' if p['outcome'] == 'length_of_stay' else 'logistic'} case-mix model ("
          + {'age_sex_conditions': 'age band, sex, chronic conditions recorded by the index date, prior-year stays, observation status',
             'age_sex': 'age band and sex', 'none': 'intercept only'}[p['adjust']] +
          ') without unit terms; unit effects from profile.analyse: Poisson-gamma random effects on O/E after quasi-Poisson overdispersion '
          'scaling, hyperparameters integrated on a grid, 4,000 posterior draws (fixed seed), Buhlmann-Straub credibility. SRR = shrunken O/E '
          '(HRRP ERR analogue for readmissions); RSR = SRR x network observed rate; worse/better = 95% interval excludes the network. '
          'Funnel: Poisson 95%/99.8% limits on O/E by expected events.')
    return t


def summarize(report, limit=None):
    p = report['parameters']
    if report.get('status') == 'unsupported':
        return f"Risk-standardized {LABELS[p['outcome']]} cannot be computed: {report['reason']} Missing input: {', '.join(report.get('missing') or [])}.", []
    if report.get('status') != 'answered':
        return report.get('reason') or 'Nothing to standardise.', []
    m, c = report['model'], report['counts']
    pct = (lambda v: f'{v:.1%}') if p['outcome'] != 'length_of_stay' else (lambda v: f'{v:.2f} days')
    unit_word = {'hospital': 'hospitals', 'practice': 'practices', 'pcp': 'PCPs'}[p['unit']]
    window = '' if p['outcome'] == 'length_of_stay' else f" ({p['window_days']}-day)"
    txt = (f"Risk-standardized {LABELS[p['outcome']]}{window} across {report['units_total']} {unit_word}: network rate {pct(report['network_rate'])} "
           f"({m['events']:g} {'days' if p['outcome'] == 'length_of_stay' else 'events'} in {m['cases']} index cases)")
    focus = report.get('focus')
    if m['unit_effects_estimable']:
        txt += f"; {c['worse_than_network']} worse than the network, {c['better_than_network']} better, {c['no_different']} not distinguishable."
        rows = [focus] if focus else [u for u in report['units'] if u['flag'] != 'no_different'][:3] or report['units'][:2]
        txt += ' ' + '; '.join(f"{u['unit']}: RSR {pct(u['rsr'])} (95% {pct(u['rsr_ci95'][0])}-{pct(u['rsr_ci95'][1])}), SRR {u['srr']:.2f}, "
                               f"O/E {u['oe_ratio'] if u['oe_ratio'] is not None else '-'} on {u['index_cases']} cases" for u in rows) + '.'
    else:
        rows = [focus] if focus else report['units'][:3]
        txt += '. ' + '; '.join(f"{u['unit']}: observed {u['observed']:g} vs expected {u['expected']:.2f} on {u['index_cases']} cases" for u in rows) + '.'
    if m.get('c_statistic') is not None: txt += f" Case-mix model c-statistic {m['c_statistic']:.2f}."
    for n in report['notes']: txt += ' ' + n
    return txt, report['units'][:limit or p['top']]


def follow_up():
    return ['What is the risk-standardized readmission rate by hospital?', 'Show the funnel plot of 30-day readmissions by practice',
            'Risk-adjusted ED revisit rates by hospital', 'Observed-to-expected length of stay by hospital']
