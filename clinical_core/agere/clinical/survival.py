"""Time-to-event analysis: Kaplan-Meier curves, log-rank comparisons and Cox hazard ratios.

Answers questions such as "readmission curve for heart failure discharges by payer",
"time to first ED visit after diagnosis for diabetics" or "how long until discharged
patients get a follow-up visit". Every analysed unit has a time zero (index), an
event definition and an explicit censoring rule; units whose time zero or outcome
cannot be observed are excluded and counted, never imputed.

Index (time zero):
  discharge        each hospital stay of a population patient with a received discharge date
  condition_first_record  first documented record of a library condition (cohort entry, not clinical onset)
  date             one fixed calendar date
Events:
  readmission      next hospital stay admitted after the index discharge (discharge index only)
  followup         first clinic/specialist/telehealth contact after discharge (discharge index only;
                   a readmission before any follow-up censors the stay)
  encounter        first encounter of a library encounter concept after time zero
  measurement      first dated result of a measurement concept past a threshold after time zero;
                   censored at the last result of that measurement (crossing is only observable when tested)
Methods: product-limit (Kaplan-Meier) estimator, Greenwood variance with log(-log) 95% intervals,
k-group log-rank test, Cox proportional hazards by Newton-Raphson on the Efron partial likelihood.
Conformalized survival lower bounds (Candes, Lei & Ren, JRSS-B 2023; default on, alpha = 0.1): for each group a
distribution-free lower predictive bound L with P(T >= L) >= 1 - alpha under censoring. Units are split by patient into
train / calibration / held-out test; the KM alpha-quantile of the training split is the predictor, calibration units
with censoring time C >= c0 are scored V = q(x) - min(T, c0) and weighted by 1 / P(C >= c0 | group) (Kaplan-Meier of
censoring), and L = min(q(x) - eta, c0) with eta the weighted (1 - alpha) quantile; empirical IPCW coverage is
reported on the held-out split.
"""
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
import json, math, sqlite3

import numpy as np
from scipy.stats import chi2, norm

from . import aggregate, concepts, semantic_layer

VERSION = 'survival/4'
INDEX_TYPES = ('discharge', 'condition_first_record', 'date')
INDEX_SELECTIONS = ('all', 'first_recorded', 'first_in_window')
STAY_KINDS = ('hospital', 'inpatient')
FOLLOWUP_COVERAGE = ('received_records', 'contiguous_enrollment')
OUTPUTS = ('estimates', 'observed_units')
EVENT_TYPES = ('readmission', 'followup', 'encounter', 'measurement')
GROUP_BY = ('none', 'coverage_type', 'practice', 'pcp', 'sex', 'age_band', 'condition')
COVARIATES = ('age_band', 'sex', 'coverage_type')
FOLLOWUP_CONCEPTS = ('primary_care_visit', 'specialist_visit', 'telehealth_contact')  # same set as vbc 7-day follow-up
OPS = {'lt': lambda v, t: v < t, 'le': lambda v, t: v <= t, 'gt': lambda v, t: v > t, 'ge': lambda v, t: v >= t}
OP_WORDS = {'lt': 'below', 'le': 'at or below', 'gt': 'above', 'ge': 'at or above'}
SMALL_GROUP = 11
Z = float(norm.ppf(0.975))


# ---- estimators --------------------------------------------------------------------
def kaplan_meier(times, events):
    """Product-limit estimate at each distinct event/censoring time.
    Returns rows {time, at_risk, events, censored, survival, variance (Greenwood), ci_low, ci_high}.
    At tied times events precede censorings. Intervals use the log(-log) transform of S."""
    t = np.asarray(times, float); e = np.asarray(events, int)
    rows, s, g = [], 1.0, 0.0
    for u in np.unique(t):
        n = int((t >= u).sum()); d = int(((t == u) & (e == 1)).sum()); c = int(((t == u) & (e == 0)).sum())
        if d:
            s *= 1 - d / n
            g = g + d / (n * (n - d)) if n > d else math.inf
        rows.append({'time': float(u), 'at_risk': n, 'events': d, 'censored': c, 'survival': s,
                     'variance': (s * s * g) if math.isfinite(g) else 0.0, **dict(zip(('ci_low', 'ci_high'), _loglog_ci(s, g)))})
    return rows


def _loglog_ci(s, g):
    """95% interval for S from the Greenwood sum g = sum d/(n(n-d)), on the log(-log S) scale."""
    if s >= 1.0: return 1.0, 1.0
    if s <= 0.0 or not math.isfinite(g): return 0.0, 0.0
    se = math.sqrt(g) / abs(math.log(s))
    return s ** math.exp(Z * se), s ** math.exp(-Z * se)


def endpoint_support(horizon, last_followup, at_risk, *, all_events_observed=False):
    """Observation support for a requested endpoint; a flat censored tail is not observed survival."""
    supported = last_followup is not None and (last_followup >= horizon or all_events_observed)
    return {'requested_horizon_days': float(horizon), 'last_followup_days': last_followup,
            'at_risk': int(at_risk), 'all_events_observed': bool(all_events_observed), 'supported': bool(supported),
            'reason': None if supported else 'Requested horizon extends beyond observed follow-up; the surviving tail is unknown.'}


def horizon_support(times, events, horizon, weights=None):
    times, events = np.asarray(times, float), np.asarray(events, int)
    weights = np.ones(len(times)) if weights is None else np.asarray(weights, float)
    positive = np.isfinite(times) & np.isfinite(weights) & (weights > 0)
    times, events, weights = times[positive], events[positive], weights[positive]
    at_risk = times >= horizon
    result = endpoint_support(horizon, float(times.max()) if len(times) else None, at_risk.sum(),
                              all_events_observed=bool(len(times) and np.all((events == 1) & (times <= horizon))))
    result['positive_weight_units'] = len(times)
    result['weight_at_risk'] = float(weights[at_risk].sum())
    return result


def km_support(rows, t):
    at_risk = next((r['at_risk'] for r in rows if r['time'] >= t), 0)
    absorbed = bool(rows and all(not r['censored'] for r in rows) and rows[-1]['survival'] == 0 and rows[-1]['time'] <= t)
    return endpoint_support(t, rows[-1]['time'] if rows else None, at_risk, all_events_observed=absorbed)


def km_at(rows, t):
    """Step-function value of a KM curve at time t (with its interval and number still at risk)."""
    s, lo, hi = 1.0, 1.0, 1.0
    for r in rows:
        if r['time'] > t: break
        if r['events']: s, lo, hi = r['survival'], r['ci_low'], r['ci_high']
    at_risk = next((r['at_risk'] for r in rows if r['time'] >= t), 0)  # still under observation at t
    return (s, lo, hi, at_risk) if km_support(rows, t)['supported'] else (None, None, None, at_risk)


def median_time(rows):
    return next((r['time'] for r in rows if r['events'] and r['survival'] <= 0.5), None)


def logrank(times, events, groups):
    """k-group log-rank test. Returns {statistic, df, p_value, observed, expected} (per group)."""
    t = np.asarray(times, float); e = np.asarray(events, int); labels = sorted(set(groups), key=str)
    gi = np.array([labels.index(x) for x in groups]); k = len(labels)
    if k < 2: return None
    O = np.zeros(k); E = np.zeros(k); V = np.zeros((k, k))
    for u in np.unique(t[e == 1]):
        risk = t >= u; n = risk.sum(); d = ((t == u) & (e == 1)).sum()
        nj = np.array([(risk & (gi == j)).sum() for j in range(k)], float)
        dj = np.array([((t == u) & (e == 1) & (gi == j)).sum() for j in range(k)], float)
        O += dj; E += d * nj / n
        if n > 1:
            f = d * (n - d) / (n - 1) / n
            V += f * (np.diag(nj) - np.outer(nj, nj) / n)
    diff = (O - E)[:-1]; Vr = V[:-1, :-1]
    df = int(np.linalg.matrix_rank(Vr)) if Vr.size else 0
    if df == 0: return None
    stat = float(diff @ np.linalg.pinv(Vr) @ diff)
    return {'statistic': round(stat, 4), 'df': df, 'p_value': float(chi2.sf(stat, df)),
            'observed': {str(l): int(O[i]) for i, l in enumerate(labels)},
            'expected': {str(l): round(float(E[i]), 3) for i, l in enumerate(labels)}}


def cox(times, events, X, names, max_iter=50, tol=1e-9):
    """Cox proportional hazards, Efron ties, Newton-Raphson with step halving.
    Returns {coefficients: [{term, coef, se, hazard_ratio, ci_low, ci_high, p_value}], log_likelihood, iterations, converged}."""
    t = np.asarray(times, float); e = np.asarray(events, int); X = np.asarray(X, float)
    n, p = X.shape
    order = np.argsort(-t, kind='stable'); t, e, X = t[order], e[order], X[order]
    event_times = np.unique(t[e == 1])

    def pieces(beta):
        eta = X @ beta; w = np.exp(eta - eta.max()); shift = eta.max()
        ll, grad, info = 0.0, np.zeros(p), np.zeros((p, p))
        for u in event_times:
            R = t >= u; D = (t == u) & (e == 1); d = int(D.sum())
            sR, sD = w[R].sum(), w[D].sum()
            zR, zD = w[R] @ X[R], w[D] @ X[D]
            qR = (X[R] * w[R, None]).T @ X[R]; qD = (X[D] * w[D, None]).T @ X[D]
            ll += eta[D].sum(); grad += X[D].sum(0)
            for l in range(d):
                f = l / d
                den = sR - f * sD; zbar = (zR - f * zD) / den
                ll -= math.log(den) + shift
                grad -= zbar
                info += (qR - f * qD) / den - np.outer(zbar, zbar)
        return ll, grad, info

    beta = np.zeros(p); ll, grad, info = pieces(beta); converged = False
    for it in range(1, max_iter + 1):
        try: step = np.linalg.solve(info, grad)
        except np.linalg.LinAlgError: step = np.linalg.pinv(info) @ grad
        h = 1.0
        while True:
            nb = beta + h * step; nll, ng, ni = pieces(nb)
            if nll >= ll - 1e-12 or h < 1e-6: break
            h /= 2
        done = abs(nll - ll) < tol
        beta, ll, grad, info = nb, nll, ng, ni
        if done: converged = True; break
    cov = np.linalg.pinv(info)
    out = []
    for j, name in enumerate(names):
        se = math.sqrt(max(cov[j, j], 0.0)); b = float(beta[j])
        if abs(b) > 10 or se > 5 or not math.isfinite(se):  # (quasi-)separation: the likelihood has no finite maximum
            out.append({'term': name, 'coef': None, 'se': None, 'hazard_ratio': None, 'ci_low': None, 'ci_high': None, 'p_value': None,
                        'estimable': False})
            continue
        out.append({'term': name, 'coef': round(b, 4), 'se': round(se, 4), 'hazard_ratio': round(math.exp(b), 3),
                    'ci_low': round(math.exp(b - Z * se), 3), 'ci_high': round(math.exp(b + Z * se), 3),
                    'p_value': float(2 * norm.sf(abs(b) / se)) if se > 0 else None, 'estimable': True})
    return {'coefficients': out, 'log_likelihood': round(float(ll), 4), 'iterations': it, 'converged': converged}


# ---- parameters ----------------------------------------------------------------------
def validate(p):
    allowed = {'index', 'event', 'horizon_days', 'time_points', 'group_by', 'group_concept', 'cox', 'covariates', 'top', 'alpha', 'lower_bound', 'coverage', 'output'}
    if not isinstance(p, dict) or set(p) - allowed: raise ValueError(f'survival parameters: {sorted(allowed)}')
    index = p.get('index')
    if not isinstance(index, dict) or index.get('type') not in INDEX_TYPES or set(index) - {'type', 'concept', 'date', 'window', 'selection', 'window_basis', 'stay_kind'}:
        raise ValueError(f'index.type must be one of {INDEX_TYPES}')
    ix = {'type': index['type']}
    if ix['type'] == 'discharge':
        for name, choices in (('selection', INDEX_SELECTIONS), ('window_basis', ('discharge', 'admission')), ('stay_kind', STAY_KINDS)):
            value = index.get(name, choices[0])
            if value not in choices: raise ValueError(f'survival index.{name} must be one of {choices}')
            if name in index: ix[name] = value
    elif set(index) & {'selection', 'window_basis', 'stay_kind'}:
        raise ValueError('survival stay selection, window basis and kind require a discharge index')
    if ix['type'] == 'condition_first_record':
        if index.get('concept') not in concepts.CONDITIONS: raise ValueError('condition_first_record index needs a library condition concept')
        ix['concept'] = index['concept']
    if ix['type'] == 'date':
        d = index.get('date')
        if not isinstance(d, str) or len(d) != 10: raise ValueError('date index needs an ISO date')
        date.fromisoformat(d); ix['date'] = d
    if index.get('window') and any((index['window'] or {}).values()):
        w = index['window']
        if ix['type'] != 'discharge' or not isinstance(w, dict) or set(w) - {'start', 'end'} or \
                any(v and (not isinstance(v, str) or len(v) != 10) for v in w.values()):
            raise ValueError('index.window (discharge dates) takes ISO start/end')
        ix['window'] = {'start': w.get('start', ''), 'end': w.get('end', '')}
    event = p.get('event')
    if not isinstance(event, dict) or event.get('type') not in EVENT_TYPES or set(event) - {'type', 'concept', 'op', 'value'}:
        raise ValueError(f'event.type must be one of {EVENT_TYPES}')
    ev = {'type': event['type']}
    if ev['type'] in ('readmission', 'followup') and ix['type'] != 'discharge':
        raise ValueError(f'{ev["type"]} events need a discharge index')
    if ev['type'] == 'encounter':
        if event.get('concept') not in concepts.ENCOUNTERS: raise ValueError('encounter events need a library encounter concept')
        ev['concept'] = event['concept']
    if ev['type'] == 'measurement':
        if event.get('concept') not in concepts.MEASUREMENTS: raise ValueError('measurement events need a library measurement concept')
        if event.get('op') not in OPS: raise ValueError('measurement events need op lt/le/gt/ge')
        if type(event.get('value')) not in (int, float) or not math.isfinite(event['value']): raise ValueError('measurement events need a numeric threshold')
        ev.update(concept=event['concept'], op=event['op'], value=float(event['value']))
    discharge = ix['type'] == 'discharge'
    output = p.get('output', 'estimates')
    coverage = p.get('coverage', 'received_records')
    if output not in OUTPUTS or coverage not in FOLLOWUP_COVERAGE: raise ValueError('Invalid survival output or coverage policy')
    observed = output == 'observed_units'
    if observed and (p.get('cox') or p.get('lower_bound')): raise ValueError('observed_units does not fit Cox or predictive bounds')
    horizon = p.get('horizon_days', None if observed else 90 if discharge else 730)
    if not (observed and horizon is None) and (type(horizon) is not int or not 1 <= horizon <= 3650): raise ValueError('horizon_days 1..3650, or null for observed_units through the snapshot')
    points = [] if observed else p.get('time_points') or [x for x in ((7, 14, 30, 60, 90) if discharge else (30, 90, 180, 365, 730)) if x <= horizon] or [horizon]
    if not isinstance(points, list) or not observed and not 1 <= len(points) <= 12 or any(type(x) is not int or not 1 <= x <= horizon for x in points):
        raise ValueError('time_points: 1..12 integer days within the horizon')
    group_by = p.get('group_by', 'none')
    if group_by not in GROUP_BY: raise ValueError(f'group_by must be one of {GROUP_BY}')
    out = {'index': ix, 'event': ev, 'horizon_days': horizon, 'time_points': sorted(set(points)), 'group_by': group_by,
           'cox': bool(p.get('cox', False)), 'top': p.get('top', 25), 'coverage': coverage, 'output': output}
    if group_by == 'condition':
        if p.get('group_concept') not in concepts.CONDITIONS: raise ValueError('condition groups need group_concept')
        out['group_concept'] = p['group_concept']
    covs = p.get('covariates') or None
    if covs is not None:
        if not out['cox'] or not isinstance(covs, list) or not 1 <= len(covs) <= 8 or \
                any(c not in COVARIATES and c not in concepts.CONDITIONS for c in covs) or len(set(covs)) != len(covs):
            raise ValueError(f'covariates (Cox only): {COVARIATES} or condition concepts, 1..8')
        out['covariates'] = covs
    elif out['cox']:
        out['covariates'] = list(COVARIATES)
    if type(out['top']) is not int or not 1 <= out['top'] <= 200: raise ValueError('top 1..200')
    alpha = p.get('alpha', 0.1)
    if isinstance(alpha, bool) or not isinstance(alpha, (int, float)) or not 0.01 <= alpha <= 0.5: raise ValueError('alpha 0.01..0.5')
    out['alpha'] = float(alpha)
    out['lower_bound'] = False if observed else bool(p.get('lower_bound', True))
    return out


# ---- conformalized survival lower bounds (Candes, Lei & Ren 2023) ---------------------------
def _split(pid):
    """Deterministic patient-level split: 'train' 50%, 'calibration' 30%, 'test' 20% (no patient spans splits)."""
    import hashlib
    h = int(hashlib.sha256(str(pid).encode()).hexdigest()[:8], 16) % 10
    return 'train' if h < 5 else 'calibration' if h < 8 else 'test'


def km_quantile(times, events, q, cap):
    """Smallest time with KM survival <= 1 - q (the q-quantile of T); cap when the curve never falls that far."""
    for r in kaplan_meier(times, events):
        if r['events'] and r['survival'] <= 1 - q: return float(r['time'])
    return float(cap)


def _censor_survival(times, events, c0):
    """P(C >= c0) from the Kaplan-Meier estimate of the censoring distribution (censorings are its events)."""
    s = 1.0
    for r in kaplan_meier(times, [1 - e for e in events]):
        if r['time'] >= c0: break
        s = r['survival']
    return s


def _weighted_quantile(values, weights, level, test_weight):
    """Weighted (level) quantile of the calibration scores with the test point's weight placed at +infinity."""
    order = sorted(zip(values, weights))
    total = sum(weights) + test_weight
    acc = 0.0
    for v, w in order:
        acc += w
        if acc / total >= level: return v
    return math.inf


def conformal_lower_bounds(units_rows, groups_of, admin_of, alpha=0.1, c0=None, min_calibration=20):
    """units_rows: [(pid, zero, time, event, reason)]; groups_of(row) -> group key; admin_of(row) -> administrative censoring
    time (the follow-up the unit could have had). Censoring time C: the observed time for censored units, the administrative
    limit for units with an event (known for every unit; non-administrative censoring of event units is unobserved).
    -> {'alpha', 'c0', 'eta', 'groups': {g: {...}}, 'coverage': {...}} or {'status': 'insufficient', ...}."""
    rows = list(units_rows)
    C = [r[2] if not r[3] else admin_of(r) for r in rows]
    if c0 is None:
        c0 = float(np.median(C)) if C else 0.0
    split = [_split(r[0]) for r in rows]
    train = [i for i, s in enumerate(split) if s == 'train']
    calib = [i for i, s in enumerate(split) if s == 'calibration']
    test = [i for i, s in enumerate(split) if s == 'test']
    cap = c0
    q_all = km_quantile([rows[i][2] for i in train], [rows[i][3] for i in train], alpha, cap) if train else cap
    by_group = defaultdict(list)
    for i in train: by_group[groups_of(rows[i])].append(i)
    qhat = {g: (km_quantile([rows[i][2] for i in ix], [rows[i][3] for i in ix], alpha, cap) if len(ix) >= SMALL_GROUP else q_all)
            for g, ix in by_group.items()}
    q = lambda g: min(qhat.get(g, q_all), c0)
    # censoring survival P(C >= c0 | group): KM of censoring on train + calibration, pooled for small groups
    pool = train + calib
    c_all = _censor_survival([rows[i][2] for i in pool], [rows[i][3] for i in pool], c0)
    cg = defaultdict(list)
    for i in pool: cg[groups_of(rows[i])].append(i)
    chat = {g: (_censor_survival([rows[i][2] for i in ix], [rows[i][3] for i in ix], c0) if len(ix) >= SMALL_GROUP else c_all)
            for g, ix in cg.items()}
    ch = lambda g: max(chat.get(g, c_all), 0.05)  # weights bounded at 20
    usable = [i for i in calib if C[i] >= c0]
    if len(usable) < min_calibration:
        return {'status': 'insufficient', 'alpha': alpha, 'c0': c0, 'calibration_units': len(usable),
                'reason': f'{len(usable)} calibration units observed to day {c0:g} (need {min_calibration})'}
    scores = [q(groups_of(rows[i])) - min(rows[i][2], c0) for i in usable]
    weights = [1.0 / ch(groups_of(rows[i])) for i in usable]
    test_weight = max(weights)  # conservative: the largest weight a test point can carry
    eta = _weighted_quantile(scores, weights, 1 - alpha, test_weight)
    lower = lambda g: max(0.0, min(q(g) - eta, c0)) if math.isfinite(eta) else 0.0
    covered = [(1.0 / ch(groups_of(rows[i])), min(rows[i][2], c0) >= lower(groups_of(rows[i]))) for i in test if C[i] >= c0]
    cov = (sum(w for w, ok in covered if ok) / sum(w for w, _ in covered)) if covered else None
    groups = {}
    for g in set(groups_of(r) for r in rows):
        groups[g] = {'lower_bound_days': round(lower(g), 1), 'predicted_quantile_days': round(q(g), 1),
                     'censoring_survival_at_c0': round(ch(g), 4), 'train_units': len(by_group.get(g, []))}
    return {'status': 'ok', 'alpha': alpha, 'c0': round(c0, 1), 'eta': round(eta, 3) if math.isfinite(eta) else None,
            'calibration_units': len(usable), 'groups': groups,
            'coverage': {'target': round(1 - alpha, 3), 'empirical_ipcw': round(cov, 4) if cov is not None else None,
                         'test_units': len(covered)},
            'method': (f'Conformalized lower predictive bound (Candes, Lei & Ren 2023): KM {alpha:g}-quantile of the training split as '
                       f'predictor, calibration units with censoring time >= c0 = {c0:g} days scored q(x) - min(T, c0), weighted by '
                       f'1/P(C >= c0 | group) (KM of censoring); L = min(q(x) - eta, c0); P(T >= L) >= {1 - alpha:.0%} marginally. '
                       f'Censoring times of units with an event are taken as their administrative limit (snapshot/horizon).')}


# ---- data -----------------------------------------------------------------------------
def _days(a, b):
    return date.fromisoformat(b[:10]).toordinal() - date.fromisoformat(a[:10]).toordinal()


def describe_event(p):
    ev, ix = p['event'], p['index']
    what = {'readmission': 'readmission', 'followup': 'follow-up visit'}.get(ev['type'])
    if ev['type'] == 'encounter': what = f"first {ev['concept'].replace('_', ' ')}"
    if ev['type'] == 'measurement': what = f"{ev['concept']} {OP_WORDS[ev['op']]} {ev['value']:g}"
    zero = {'discharge': 'discharge', 'condition_first_record': f"first recorded {ix.get('concept', '').replace('_', ' ')}",
            'date': ix.get('date')}[ix['type']]
    return what, zero


def _has_relation(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE name=? AND type IN ('table','view')", (name,)).fetchone() is not None


def _discharge_starts(db, members, ix, excluded, accounts):
    """Select received index episodes before timing; never break a date tie with a nullable record ID."""
    by_patient = defaultdict(list)
    inpatient = ix.get('stay_kind', 'hospital') == 'inpatient'
    query = ("SELECT patient_id,event_date,end_date,discharge_known,record_id,sources FROM utilization_event WHERE kind='inpatient'"
             if inpatient else 'SELECT patient_id,admit_date,discharge_date,discharge_known,NULL,NULL FROM stay')
    for pid, admit, discharge, known, rid, sources in db.execute(query):
        if pid in members:
            by_patient[pid].append({'admission_date': admit, 'discharge_date': discharge if known else None,
                                   'discharge_known': known, 'record_id': rid, 'sources': sources})
    starts, stays = [], defaultdict(list)
    w, basis, selection = ix.get('window') or {}, ix.get('window_basis', 'discharge'), ix.get('selection', 'all')
    def in_window(row):
        if not any(w.values()): return True
        d = row['admission_date' if basis == 'admission' else 'discharge_date']
        return bool(d and (not w.get('start') or d >= w['start']) and (not w.get('end') or d <= w['end']))
    for pid, received in sorted(by_patient.items()):
        dated = sorted((r for r in received if r['admission_date']), key=lambda r: r['admission_date'])
        stays[pid] = [(r['admission_date'], r['discharge_date']) for r in dated]
        candidates = [r for r in dated if in_window(r)] if selection == 'first_in_window' else dated
        if selection != 'all' and candidates:
            candidates = [r for r in candidates if r['admission_date'] == candidates[0]['admission_date']]
            # Membership is checked after selecting first across history.
            if selection == 'first_recorded' and not any(in_window(r) for r in candidates): continue
            groups = [candidates]
        else:
            groups = [[r] for r in candidates if in_window(r)]
        for group in groups:
            known = [r['discharge_date'] for r in group if r['discharge_date']]
            detail = {'patient_id': pid, 'index_admission_date': group[0]['admission_date'],
                      'index_candidates': len(group), 'index_source_rows_json': json.dumps(group, sort_keys=True),
                      'index_discharge_min': min(known) if known else None, 'index_discharge_max': max(known) if known else None,
                      'index_selection': selection, 'index_window_basis': basis, 'index_stay_kind': ix.get('stay_kind', 'hospital')}
            if selection == 'first_recorded' and len(dated) != len(received):
                detail.update(index_date=None, status='undated_admission_prevents_first_recorded_order')
                detail['undated_admission_source_rows_json'] = json.dumps([r for r in received if not r['admission_date']], sort_keys=True)
                excluded[detail['status']] += 1; accounts.append(detail); continue
            if len(known) != len(group) or len(set(known)) != 1:
                detail.update(index_date=None, status='index_discharge_unknown_or_ambiguous')
                excluded['stay_without_received_discharge' if selection == 'all' else detail['status']] += 1
                accounts.append(detail); continue
            starts.append((pid, known[0], detail))
    return starts, stays


def _coverage_windows(db, members, as_of):
    """Union overlapping/adjacent received spans. A later open span cannot bridge a recorded gap."""
    if not _has_relation(db, 'enrollment_span'): return {}
    cols = {r[1] for r in db.execute('PRAGMA table_info(enrollment_span)')}
    open_flag = 'open_ended' if 'open_ended' in cols else 'NULL'
    raw = defaultdict(list)
    for pid, start, end, opened in db.execute(f'SELECT patient_id,coverage_start,coverage_end,{open_flag} FROM enrollment_span'):
        if pid not in members or not start: continue
        if end is None:
            if opened != 1: continue  # unknown end is not an asserted open interval
            end = as_of
        if start <= min(end, as_of): raw[pid].append((start, min(end, as_of)))
    merged = {}
    for pid, spans in raw.items():
        blocks = []
        for start, end in sorted(set(spans)):
            if blocks and date.fromisoformat(start) <= date.fromisoformat(blocks[-1][1]) + timedelta(days=1):
                blocks[-1] = (blocks[-1][0], max(end, blocks[-1][1]))
            else: blocks.append((start, end))
        merged[pid] = blocks
    return merged


def units(db, patient_ids, p, as_of, *, uncertainties=None, accounts=None):
    """One row per analysed unit: (patient_id, index_date, time_days, event 0/1, censor_reason); plus exclusion counts."""
    ix, ev, horizon = p['index'], p['event'], p['horizon_days']
    members = set(patient_ids) if patient_ids is not None else {r[0] for r in db.execute('SELECT patient_id FROM patient')}
    excluded = Counter()
    accounts = accounts if accounts is not None else []
    # time zero
    starts = []
    stays = defaultdict(list)
    if ix['type'] == 'discharge':
        starts, stays = _discharge_starts(db, members, ix, excluded, accounts)
    elif ix['type'] == 'condition_first_record':
        # Documentation date (event date, else recorded date); reported onset is often only a year and predates observation.
        first = {pid: d for pid, d in db.execute("SELECT patient_id, min(coalesce(substr(event_date, 1, 10), substr(recorded_date, 1, 10))) "
                                                 "FROM condition WHERE concept = ? GROUP BY patient_id", (ix['concept'],))}
        for pid in members:
            if pid not in first: excluded['no_index_condition_record'] += 1
            elif not first[pid]: excluded['index_condition_undated'] += 1
            else: starts.append((pid, first[pid][:10], {'patient_id': pid, 'index_selection': 'condition_first_record'}))
    else:
        starts = [(pid, ix['date'], {'patient_id': pid, 'index_selection': 'fixed_date'}) for pid in sorted(members)]
    deaths = {}
    if _has_relation(db, 'death'):
        cols = {r[1] for r in db.execute('PRAGMA table_info(death)')}
        disagreement = 'sources_disagree' if 'sources_disagree' in cols else 'NULL'
        deaths = {pid: (d, conflict) for pid, d, conflict in db.execute(f'SELECT patient_id,death_date,{disagreement} FROM death') if pid in members}
    for detail in accounts:  # An unknown index origin must not hide a received death observation.
        detail.update(snapshot_date=as_of, death_date=deaths.get(detail['patient_id'], (None, None))[0],
                      death_sources_disagree=deaths.get(detail['patient_id'], (None, None))[1],
                      coverage_policy=p.get('coverage', 'received_records'))
    coverage = _coverage_windows(db, members, as_of) if p.get('coverage') == 'contiguous_enrollment' else {}
    # event sources
    dated = defaultdict(list)
    threshold_evidence = defaultdict(list)
    if ev['type'] in ('encounter', 'followup'):
        types = FOLLOWUP_CONCEPTS if ev['type'] == 'followup' else (ev['concept'],)
        query = ('SELECT DISTINCT patient_id, substr(event_date, 1, 10) FROM encounter WHERE type_concept IN (%s) AND event_date IS NOT NULL'
                 % ','.join('?' * len(types)))
        if ev.get('concept') == 'hospital_stay':
            query = "SELECT DISTINCT patient_id, event_date FROM utilization_event WHERE kind IN ('inpatient', 'observation') AND event_date IS NOT NULL"
            types = ()
        for pid, d in db.execute(query, types):
            if pid in members: dated[pid].append((d, None))
    elif ev['type'] == 'measurement':
        from .safety_data import usable_measurement_sql
        from .cohorts import _compare_measurement, CENSOR_OPS
        columns = {r[1] for r in db.execute('PRAGMA table_info(measurement)')}
        censor = 'value_censor' if 'value_censor' in columns else 'NULL'
        rid = 'record_id' if 'record_id' in columns else 'NULL'
        raw = 'value' if 'value' in columns else 'NULL'
        for pid, d, v, comparator, record_id, received in db.execute(
                                    f'SELECT patient_id, substr(event_date, 1, 10), value_canonical,{censor},{rid},{raw} FROM measurement WHERE concept = ? '
                                    'AND value_canonical IS NOT NULL AND event_date IS NOT NULL'+usable_measurement_sql(db), (ev['concept'],)):
            if pid not in members: continue
            value = {'op': CENSOR_OPS[comparator], 'bound': v} if comparator in CENSOR_OPS else v
            finite = type(v) in (int,float) and math.isfinite(v)
            truth = (None if not finite or
                     (comparator and comparator not in CENSOR_OPS) else _compare_measurement(value, ev['op'], ev['value']))
            dated[pid].append((d, truth))
            threshold_evidence[pid,d].append({'record_id': record_id, 'date': d, 'value_canonical': v if finite else None,
                                             'comparator': comparator, 'received_value': str(received), 'threshold_result': truth})
    for v in dated.values(): v.sort(key=lambda item:item[0])
    out = []
    for pid, zero, detail in starts:
        detail.update(index_date=zero, snapshot_date=as_of, death_date=deaths.get(pid, (None, None))[0],
                      death_sources_disagree=deaths.get(pid, (None, None))[1], coverage_policy=p.get('coverage', 'received_records'))
        nxt = next((a for a, _ in stays.get(pid, []) if zero < a <= as_of), None) if ev['type'] in ('readmission', 'followup') else None
        detail['next_recorded_admission_date'] = nxt
        accounts.append(detail)
        if zero > as_of:
            excluded['time_zero_after_snapshot'] += 1; detail['status'] = 'time_zero_after_snapshot'; continue
        limit = min(horizon, _days(zero, as_of)) if horizon is not None else _days(zero, as_of)
        reason = 'snapshot' if horizon is None or _days(zero, as_of) < horizon else 'horizon'
        death_date = detail['death_date']
        if death_date and death_date <= as_of and _days(zero, death_date) <= limit:
            limit, reason = _days(zero, death_date), 'death'
        if p.get('coverage') == 'contiguous_enrollment':
            block = next(((a, b) for a, b in coverage.get(pid, []) if a <= zero <= b), None)
            detail.update(coverage_at_index='recorded_covering_span' if block else 'no_recorded_covering_span',
                          contiguous_coverage_start=block[0] if block else None, contiguous_coverage_end=block[1] if block else None)
            if block is None:
                excluded['coverage_at_index_not_established'] += 1; detail['status'] = 'coverage_at_index_not_established'; continue
            if _days(zero, block[1]) < limit: limit, reason = _days(zero, block[1]), 'enrollment_boundary'
        detail.update(followup_boundary_date=(date.fromisoformat(zero) + timedelta(days=limit)).isoformat(),
                      followup_boundary_days=limit, followup_boundary_reason=reason)
        if limit <= 0:
            excluded['no_follow_up_time'] += 1; detail['status'] = 'no_follow_up_time'; continue
        if nxt and reason == 'death' and _days(zero, nxt) == limit:
            excluded['event_censor_same_day_order_unknown'] += 1; detail['status'] = 'event_censor_same_day_order_unknown'; continue
        out_before = len(out)
        if ev['type'] == 'readmission':
            gap = _days(zero, nxt) if nxt else None
            out.append((pid, zero, gap, 1, None) if gap is not None and gap <= limit else (pid, zero, limit, 0, reason))
        elif ev['type'] in ('followup', 'encounter'):
            hit = next((d for d, _ in dated.get(pid, []) if d > zero), None)
            gap = _days(zero, hit) if hit else None
            stop = _days(zero, nxt) if nxt else None  # follow-up: a readmission first ends observation
            if gap is not None and gap <= limit and (stop is None or gap < stop):
                out.append((pid, zero, gap, 1, None))
            elif stop is not None and stop <= limit:
                out.append((pid, zero, stop, 0, 'readmitted_before_follow_up'))
            else:
                out.append((pid, zero, limit, 0, reason))
        else:  # measurement threshold
            series = dated.get(pid, [])
            before = [(d, v) for d, v in series if d <= zero]
            after = [(d, v) for d, v in series if d > zero and _days(zero, d) <= limit]
            def uncertain(reason, day):
                excluded[reason] += 1
                if uncertainties is not None:
                    uncertainties.append({'patient_id':pid,'index_date':zero,'reason':reason,
                                          'measurement_evidence':threshold_evidence[pid,day]})
            if before:
                latest = {v for d, v in before if d == before[-1][0]}
                if None in latest or len(latest) > 1:
                    uncertain('unresolved_threshold_at_time_zero',before[-1][0]); continue
                if latest == {True}: excluded['already_past_threshold_at_time_zero'] += 1; continue
            if not after: excluded['no_result_after_time_zero'] += 1; continue
            hit, unresolved = None, None
            daily = defaultdict(set)
            for day, truth in after: daily[day].add(truth)
            for day, values in sorted(daily.items()):
                if True in values: hit = day; break  # An observed positive result establishes an event that day.
                if None in values: unresolved = day; break
            if unresolved:
                uncertain('unresolved_threshold_after_time_zero',unresolved); continue
            if hit: out.append((pid, zero, _days(zero, hit), 1, None))
            else: out.append((pid, zero, _days(zero, after[-1][0]), 0, 'last_result'))
            if not before: excluded['included_without_baseline_result'] += 1
        if len(out) > out_before:
            timed = out[-1]
            detail.update(time_at_risk_days=timed[2], event_observed=timed[3], censor_reason=timed[4],
                          status='next_event_observed' if timed[3] else 'right_censored',
                          timed_endpoint_date=(date.fromisoformat(zero) + timedelta(days=timed[2])).isoformat())
    return out, excluded


def attributes(db, pids, p):
    want = {p.get('group_concept')} | set(c for c in p.get('covariates', []) if c in concepts.CONDITIONS)
    want.discard(None)
    flags = defaultdict(set)
    if want:
        for c, pid in db.execute('SELECT concept, patient_id FROM concept_patient WHERE concept IN (%s)' % ','.join('?' * len(want)), sorted(want)):
            flags[pid].add(c)
    attrs = {}
    for pid, age, sex, cov, clin, org in db.execute('SELECT patient_id, age_reported, sex_reported, coverage_type, pcp_clinician_id, pcp_practice_id FROM patient'):
        if pid not in pids: continue
        attrs[pid] = {'age_band': aggregate.age_band(age), 'sex': sex or 'unknown', 'coverage_type': cov or 'unknown',
                      'pcp': clin or 'unattributed', 'practice': org or 'unattributed', 'flags': flags[pid]}
    return attrs


def _group(a, p):
    g = p['group_by']
    if g == 'none': return 'all'
    if g == 'condition': return f"{p['group_concept']}" if p['group_concept'] in a['flags'] else f"no {p['group_concept']}"
    return a[g]


def _design(rows, attrs, covariates):
    """Indicator design matrix; reference = most common level; sparse levels pooled; non-estimable terms dropped."""
    cols, names, dropped = [], [], []
    events = np.array([r[3] for r in rows])
    for cov in covariates:
        if cov in concepts.CONDITIONS:
            x = np.array([1.0 if cov in attrs[r[0]]['flags'] else 0.0 for r in rows])
            if 0 < x.sum() < len(x) and events[x == 1].sum() > 0 and events[x == 0].sum() > 0: cols.append(x); names.append(cov)
            else: dropped.append(cov)
            continue
        vals = [attrs[r[0]][cov] for r in rows]
        counts = Counter(vals)
        ref = counts.most_common(1)[0][0]
        for level in sorted(counts, key=str):
            if level == ref: continue
            x = np.array([1.0 if v == level else 0.0 for v in vals])
            if counts[level] < 5 or events[x == 1].sum() == 0 or events[x == 1].sum() == x.sum():
                dropped.append(f'{cov}={level}'); continue
            cols.append(x); names.append(f'{cov}={level} (vs {ref})')
    return (np.column_stack(cols) if cols else np.zeros((len(rows), 0))), names, dropped


# ---- operator ---------------------------------------------------------------------------
def execute(path, patient_ids, params):
    p = validate(params)
    path = Path(path)
    as_of = json.loads((path / 'run.json').read_text())['snapshot']['as_of']
    db = sqlite3.connect(f'file:{semantic_layer.ensure(path)}?mode=ro', uri=True)
    try:
        uncertainties, accounts = [], []
        rows, excluded = units(db, patient_ids, p, as_of, uncertainties=uncertainties, accounts=accounts)
        death_source = {'received_relation': _has_relation(db, 'death')}
        death_source['received_rows'] = db.execute('SELECT COUNT(*) FROM death').fetchone()[0] if death_source['received_relation'] else None
        if p['output'] == 'observed_units':
            for account in accounts:
                account.setdefault('status', 'unresolved_event_definition')
                for field in ('index_date', 'next_recorded_admission_date', 'time_at_risk_days', 'event_observed', 'censor_reason',
                              'followup_boundary_date', 'followup_boundary_days', 'death_date', 'coverage_at_index'):
                    account.setdefault(field, None)
                account['parameters_json'] = json.dumps(p, sort_keys=True)
            fields = sorted(set().union(*(a.keys() for a in accounts))) if accounts else []
            complete_accounts = [{k: a.get(k) for k in fields} for a in accounts]
            unresolved = [a for a in complete_accounts if a.get('status') not in ('next_event_observed', 'right_censored')]
            method = ('Observed received-source follow-up only; no Kaplan-Meier, Cox, log-rank or predictive bound is fitted. '
                      'Index selection, event kind, window basis and coverage policy are declared in parameters. '
                      'First-recorded means first across dated received history, not a clinically first-ever admission. '
                      'Ambiguous/unknown index origins remain visible and are not timed. Later recorded admissions are selected by date, '
                      'without a record-ID inequality; same-day/overlapping admissions do not establish a later-day event. '
                      'Received death dates and, when requested, the end of the contiguous received enrollment block at time zero bound follow-up. '
                      'A later reenrollment does not bridge a recorded gap; missing coverage does not establish ineligibility. '
                      'Event/death date ties have unknown within-day order and are not timed as resolved events; enrollment end dates are inclusive. '
                      'Time at risk ends at the first recorded event or censoring boundary, while raw later admissions remain visible. '
                      'Right censoring and absent records do not establish that unobserved care did not occur. Enrollment is a declared observation proxy, '
                      'not proof of complete event capture. No care-causal, population-risk or clinical qualification follows.')
            return {'schema': VERSION, 'parameters': p, 'event_label': describe_event(p)[0], 'time_zero': describe_event(p)[1], 'as_of': as_of,
                    'status': 'partial' if unresolved or uncertainties else 'complete', 'unresolved_units': unresolved + uncertainties,
                    'unsupported_horizons': [], 'missing': ['Some index, eligibility or date-order units remain unresolved; complete accounts are retained.'] if unresolved or uncertainties else [],
                    'units': len(rows), 'patients': len({r[0] for r in rows}), 'events': sum(r[3] for r in rows),
                    'selected_index_accounts': len(accounts), 'excluded': dict(excluded), 'included_without_baseline_result': excluded.get('included_without_baseline_result', 0),
                    'groups': [], 'groups_total': 0, 'table': complete_accounts, 'curves': {}, 'logrank': None, 'cox': None,
                    'notes': [], 'method': method, 'conformal_lower_bounds': None, 'death_source': death_source,
                    'observed_status_counts': dict(Counter(a.get('status', 'unresolved') for a in accounts))}
        attrs = attributes(db, {r[0] for r in rows}, p)
    finally:
        db.close()
    what, zero = describe_event(p)
    groups = defaultdict(list)
    for r in rows: groups[_group(attrs[r[0]], p)].append(r)
    bounds = None
    source_censoring = any(r[4] in ('death', 'enrollment_boundary') for r in rows)
    if p.get('lower_bound', True) and source_censoring:
        bounds = {'status': 'insufficient', 'reason': 'Received death/enrollment censoring is not qualified for this predictive-bound contract; no bound fitted.'}
    elif p.get('lower_bound', True) and rows:
        try:
            bounds = conformal_lower_bounds(rows, lambda r: _group(attrs[r[0]], p),
                                            lambda r: min(p['horizon_days'], _days(r[1], as_of)), p.get('alpha', 0.1))
        except (ValueError, ZeroDivisionError) as exc:  # a bound that cannot be computed never takes the curve down
            bounds = {'status': 'error', 'reason': f'{type(exc).__name__}: {exc}'[:200]}
    summaries, table, unsupported_horizons = [], [], []
    for g, rs in groups.items():
        km = kaplan_meier([r[2] for r in rs], [r[3] for r in rs])
        s = {'group': g, 'units': len(rs), 'patients': len({r[0] for r in rs}), 'events': sum(r[3] for r in rs),
             'censored': sum(1 - r[3] for r in rs), 'median_days': median_time(km), 'small_group': len(rs) < SMALL_GROUP,
             'censoring': dict(Counter(r[4] for r in rs if not r[3]))}
        s['_km'] = km
        summaries.append(s)
    summaries.sort(key=lambda s: (s['small_group'], -s['units'], str(s['group'])))
    shown = summaries[:p['top']]
    for s in shown:
        for t in p['time_points']:
            surv, lo, hi, at_risk = km_at(s['_km'], t)
            support = km_support(s['_km'], t)
            if not support['supported']:
                unsupported_horizons.append({'group': s['group'], 'day': t, **support})
            table.append({'group': s['group'], 'day': t, 'at_risk': at_risk,
                          'events_by_day': sum(r['events'] for r in s['_km'] if r['time'] <= t),
                          'survival': round(surv, 4) if surv is not None else None,
                          'cumulative_incidence': round(1 - surv, 4) if surv is not None else None,
                          'ci95_low': round(1 - hi, 4) if hi is not None else None,
                          'ci95_high': round(1 - lo, 4) if lo is not None else None,
                          'support': support, 'small_group': s['small_group']})
            if source_censoring:
                # 1-KM with deaths censored is not a competing-risk cumulative incidence.
                table[-1]['net_failure_probability'] = table[-1]['cumulative_incidence']
                table[-1]['cumulative_incidence'] = None
    curves = {str(s['group']): [{'day': int(r['time']), 'at_risk': r['at_risk'], 'events': r['events'], 'censored': r['censored'],
                                 'survival': round(r['survival'], 4)} for r in s['_km']][:400] for s in shown}
    for s in summaries:
        s.pop('_km')
        if bounds and bounds.get('status') == 'ok' and s['group'] in bounds['groups']:
            s['conformal_lower_bound_days'] = bounds['groups'][s['group']]['lower_bound_days']
    test = logrank([r[2] for r in rows], [r[3] for r in rows], [_group(attrs[r[0]], p) for r in rows]) if len(groups) > 1 else None
    model, cox_note = None, None
    if p['cox'] and rows and sum(r[3] for r in rows) >= 5:
        X, names, dropped = _design(rows, attrs, p['covariates'])
        if names:
            model = cox([r[2] for r in rows], [r[3] for r in rows], X, names)
            model.update(covariates=p['covariates'], not_estimable=dropped, units=len(rows), events=sum(r[3] for r in rows))
            if any(not c['estimable'] for c in model['coefficients']):
                model['note'] = ('Some terms diverged (sparse covariate-by-event cells, quasi-separation) and are reported as not estimable; '
                                 'fit fewer covariates or a larger population for those terms.')
        else:
            cox_note = 'No covariate term was estimable (levels without events or under 5 units).'
    elif p['cox']:
        cox_note = 'Fewer than 5 events: Cox model not fitted.'
    multi = p['index']['type'] == 'discharge'
    probability_label = 'net failure probability = 1 - S(t)' if source_censoring else 'cumulative incidence = 1 - S(t)'
    method = (f"Kaplan-Meier (product-limit) estimate of time from {zero} (day 0) to {what}; {probability_label}. "
              f"95% intervals from Greenwood's variance on the log(-log) scale; at risk = units still observed at the day shown. ")
    method += {'readmission': 'Index units = hospital stays (inpatient/observation encounters collapsed by admit day and overlap) of population patients '
                              'with a received discharge date; event = the next stay admitted after that discharge. Stays without a received discharge '
                              'are excluded, not counted as 0 days. Stays are not linked to a discharge diagnosis: a condition restricts which patients\' '
                              'stays are indexed. ',
               'followup': 'Index units = stays with a received discharge date; event = first primary-care, specialist or telehealth contact on a later '
                           'day; a readmission before any follow-up censors the stay at the readmission day. ',
               'encounter': f"Event = first {p['event'].get('concept', '').replace('_', ' ')} (encounter or ADT record) on a day after time zero. ",
               'measurement': 'Event = first dated result past the threshold on a day after time zero (crossing is detected at testing, so the true '
                              'crossing may be earlier). Patients already past the threshold at their last result on or before time zero are excluded; '
                              'patients with no result after time zero are excluded; others are censored at their last result. '}[p['event']['type']]
    if p['index']['type'] == 'condition_first_record':
        method += (f"Time zero = first documented record of {p['index']['concept'].replace('_', ' ')} (event date, else recorded date: cohort entry, "
                   f"not clinical onset); patients without a dated record are excluded. ")
    method += (f"Censoring: snapshot date {as_of} or {p['horizon_days']} days, whichever is first; known received death dates also end observation. "
               f"Death source availability: {death_source}. Missing/undated death records do not establish survival. ")
    if p['coverage'] == 'contiguous_enrollment':
        method += 'Eligibility at time zero requires a recorded covering enrollment block; follow-up stops at its first end, without bridging later reenrollment gaps. '
    if source_censoring:
        method += ('With deaths/enrollment boundaries censored, 1 minus Kaplan-Meier survival is shown only as net_failure_probability; '
                   'cumulative_incidence remains unavailable. This is a net failure estimate under censoring assumptions, '
                   'not an absolute competing-risk cumulative incidence. ')
    if multi: method += 'A patient can contribute several index stays; intervals and tests treat stays as independent. '
    if test: method += f"Groups compared with the {len(groups)}-group log-rank test. "
    if model: method += ('Cox proportional hazards (Efron ties, Newton-Raphson; reference = most common level; levels with under 5 units or '
                         'no events are not estimated); hazard ratios are associations, not causal effects, and proportional hazards is not tested. ')
    method += f'Groups under {SMALL_GROUP} units are flagged.'
    if unsupported_horizons:
        method += ' Requested endpoint risks beyond observed follow-up remain unknown; observed earlier curves are retained without shortening the requested horizon.'
    if uncertainties:
        method += (' Thresholds use the full interval allowed by a reported comparator. Units whose baseline eligibility or '
                   'first observed event cannot be resolved are excluded with source evidence; curves describe the resolvable '
                   'subset only. Selection and censoring assumptions for general-population inference remain unvalidated.')
    if bounds and bounds.get('status') == 'ok': method += ' ' + bounds['method']
    return {'schema': VERSION, 'parameters': p, 'event_label': what, 'time_zero': zero, 'as_of': as_of,
            'status': 'partial' if uncertainties or unsupported_horizons else 'complete', 'unresolved_units': uncertainties,
            'unsupported_horizons': unsupported_horizons,
            'missing': (['Resolve bounded/ambiguous measurement evidence before estimating this outcome for the full requested population.'] if uncertainties else [])
                       + (['Requested horizon risk is unknown for groups with no remaining follow-up support.'] if unsupported_horizons else []),
            'units': len(rows), 'patients': len({r[0] for r in rows}), 'events': sum(r[3] for r in rows),
            'excluded': {k: v for k, v in excluded.items() if k != 'included_without_baseline_result'},
            'included_without_baseline_result': excluded.get('included_without_baseline_result', 0), 'groups': summaries, 'groups_total': len(summaries), 'table': table, 'curves': curves,
            'logrank': test, 'cox': model, 'notes': [cox_note] if cox_note else [], 'method': method, 'conformal_lower_bounds': bounds,
            'death_source': death_source, 'estimand': 'net_failure_under_censoring' if source_censoring else 'product_limit_failure_under_censoring'}


def summary(report):
    """One-sentence answer: incidence at the key day per group, log-rank and hazard ratios."""
    p = report['parameters']
    if p.get('output') == 'observed_units':
        return (f"Observed follow-up: {report['selected_index_accounts']} selected index accounts, {report['units']} timed units, "
                f"{report['events']} next events recorded before censoring; {len(report['unresolved_units'])} unresolved units. "
                'Complete unit accounts are shown; no modeled risk or unobserved-event absence is established.')
    key = 30 if 30 in p['time_points'] and p['index']['type'] == 'discharge' else p['time_points'][-1]
    unit = 'index discharges' if p['index']['type'] == 'discharge' else 'patients'
    parts = []
    for row in [r for r in report['table'] if r['day'] == key][:6]:
        g = next(s for s in report['groups'] if s['group'] == row['group'])
        failure = row.get('net_failure_probability', row['cumulative_incidence'])
        if failure is None:
            support = row.get('support') or {}
            parts.append(f"{row['group']} risk unknown (last follow-up {support.get('last_followup_days')} days; {row['at_risk']} at risk at day {key})")
            continue
        med = f", median {g['median_days']:g} days" if g['median_days'] is not None else ''
        parts.append(f"{row['group']} {failure:.1%} (95% CI {row['ci95_low']:.1%}-{row['ci95_high']:.1%}; "
                     f"{g['units']} {unit}, {g['events']} events{med})")
    if not parts: return f"No analysable {unit}: " + ', '.join(f'{k} {v}' for k, v in report['excluded'].items()) + '.'
    quantity = 'net failure probability under censoring' if report.get('estimand') == 'net_failure_under_censoring' else 'cumulative incidence'
    text = ('Resolvable subset only. ' if report.get('unresolved_units') else '') + f"Kaplan-Meier {quantity} of {report['event_label']} by day {key} after {report['time_zero']}: " + '; '.join(parts)
    if report['groups_total'] > 6: text += f" (+{report['groups_total'] - 6} more groups)"
    text += '.'
    if report['logrank']:
        lr = report['logrank']
        text += f" Log-rank chi-square {lr['statistic']:.2f} on {lr['df']} df, p = {lr['p_value']:.3g}."
    if report['cox']:
        hrs = [f"{c['term']} HR {c['hazard_ratio']:g} ({c['ci_low']:g}-{c['ci_high']:g})" if c['estimable'] else f"{c['term']} not estimable"
               for c in report['cox']['coefficients'][:4]]
        text += ' Cox: ' + '; '.join(hrs) + '.'
        if report['cox'].get('note'): text += ' ' + report['cox']['note']
    b = report.get('conformal_lower_bounds') or {}
    if b.get('status') == 'ok':
        shown = [f"{g} {v['lower_bound_days']:g} days" for g, v in list(b['groups'].items())[:6]]
        cov = b['coverage']['empirical_ipcw']
        text += (f" Conformal {1 - b['alpha']:.0%} lower bound on time to {report['event_label']} (distribution-free, censoring-weighted, "
                 f"c0 {b['c0']:g} days): " + '; '.join(shown)
                 + (f" (held-out coverage {cov:.1%} on {b['coverage']['test_units']} units)" if cov is not None else '') + '.')
    elif b.get('status') in ('insufficient', 'error'):
        text += f" Conformal lower bound not computed: {b.get('reason')}."
    for note in report.get('notes') or []: text += ' ' + note
    return text
