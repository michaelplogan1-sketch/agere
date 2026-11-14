"""Fair provider/practice profiling: hierarchical Bayes and actuarial credibility.

Answers "which PCPs have truly high ED use after accounting for panel size and case mix?", "rank practices on A1c
poor control fairly", "is Dr X's admission rate really an outlier?" and "credibility-weighted PMPM by practice".
Small panels produce extreme raw rates by chance; this operator separates signal from panel size.

Data (same building blocks as the VBC engine): enrolled member-months in the period, hybrid PCP attribution
(assigned PCP, else visit plurality), event counts from the semantic layer. Members are the level-1 units; the
profiled unit (PCP or practice) is level 2. Members not attributed to any unit count in the network reference
but are not profiled.

Every unit is compared on the relative scale O/E: observed numerator over the numerator expected from the
network's own stratum rates (indirect standardisation, the same age band x sex [x chronic-condition burden]
strata as aggregate.py; without adjustment E = network rate x exposure). Display values = relative x network level.

Models (empirical Bayes; hyperparameters by maximum marginal likelihood):
  rates (count per member-year) and case-mix-adjusted shares: O_j ~ Poisson(theta_j E_j), theta_j ~ Gamma(a, b),
      after scaling O and E by a member-level overdispersion factor phi (quasi-Poisson, Pearson, >= 1);
      marginal = negative binomial. Posterior Gamma(a + O, b + E).
  unadjusted shares: O_j ~ Binomial(n_j, p_j), p_j ~ Beta(alpha, beta); marginal = beta-binomial.
  PMPM: unit mean relative cost ~ N(mu_j, s2 / W_j), mu_j ~ N(mu, tau2); s2 = within-unit (process) variance.
Credibility (Buhlmann-Straub, non-parametric): member observations x_i = y_i / E_i with weights E_i;
  k = EPV / VHM, Z_j = W_j / (W_j + k), estimate = Z X_j + (1 - Z) X_collective.
Hyperparameters (network mean and between-unit CV, or tau) are integrated on a grid under flat priors using the
model's marginal likelihood, so units are never all collapsed onto the mean when the between-unit variance estimate
sits at zero. Intervals, ranks and probabilities come from 4,000 joint posterior draws (fixed seed).
"""
from collections import defaultdict
import json, math

import numpy as np
from scipy.special import betaln, gammaln

from . import aggregate, vbc

VERSION = 'profile/1'
# metric -> (kind, encounter concept, label, display unit)
METRICS = {
    'ed_visits': ('rate', 'emergency_visit', 'ED visits per 1,000 member-years', 'per 1,000'),
    'admissions': ('rate', None, 'hospital admissions per 1,000 member-years', 'per 1,000'),
    'urgent_care_visits': ('rate', 'urgent_care_visit', 'urgent-care visits per 1,000 member-years', 'per 1,000'),
    'primary_care_visits': ('rate', 'primary_care_visit', 'primary-care visits per 1,000 member-years', 'per 1,000'),
    'readmission_rate': ('share', None, '30-day readmissions per index hospital stay', 'share'),
    'a1c_poor_control': ('share', None, 'diabetes A1c poor control (latest A1c > 9% or none in period; lower is better)', 'share'),
    'bp_control': ('share', None, 'hypertension BP control (latest < 140/90; higher is better)', 'share'),
    'statin_therapy_diabetes': ('share', None, 'statin therapy in diabetes 40-75 (higher is better)', 'share'),
    'pmpm': ('cost', None, 'completion-adjusted allowed cost PMPM', '$ PMPM'),
}
UNITS = ('pcp', 'practice')
UNIT_WORDS = {'pcp': 'PCPs', 'practice': 'practices'}
ADJUSTMENTS = ('auto', 'none', 'age_sex', 'age_sex_conditions')
METHODS = ('auto', 'bayes', 'credibility')
DRAWS = 4000
SEED = 20291013
FLAGS = ('truly_high', 'truly_low', 'indistinguishable')
# Misclassification (FRONTIER #45; profiling classification accuracy, PMC6400472): the TRUE class of a unit is 'high' / 'low' when
# its relative rate lies outside the network by more than MARGIN, else 'average'. Probabilities come from the same posterior draws.
MARGIN = 0.10
HIGHER_IS_BETTER = {'bp_control', 'statin_therapy_diabetes', 'primary_care_visits'}
CLASSES = ('high', 'average', 'low')


def misclassification(sample, flag, raw, margin=MARGIN):
    """Per-unit class probabilities and misclassification probabilities from posterior draws (draws x units, relative scale).
    reported class: from the 95% flag (truly_high -> high, truly_low -> low, else average);
    bayes class: the class with the largest posterior probability (minimises the expected number misclassified);
    raw class: the unshrunken O/E outside the margin (what a naive report would say).
    -> dict of arrays: p_high, p_average, p_low, reported, p_misclassified, bayes, p_misclassified_bayes, raw_class, p_misclassified_raw"""
    sample = np.asarray(sample, float)
    p_high = (sample > 1 + margin).mean(0); p_low = (sample < 1 - margin).mean(0)
    p_avg = 1 - p_high - p_low
    probs = {'high': p_high, 'average': p_avg, 'low': p_low}
    reported = np.array(['high' if f == 'truly_high' else 'low' if f == 'truly_low' else 'average' for f in flag])
    stack = np.vstack([p_high, p_avg, p_low])
    bayes = np.array(CLASSES)[stack.argmax(0)]
    raw = np.asarray(raw, float)
    raw_class = np.where(~np.isfinite(raw), 'average', np.where(raw > 1 + margin, 'high', np.where(raw < 1 - margin, 'low', 'average')))
    pick = lambda cls: np.array([probs[c][j] for j, c in enumerate(cls)])
    return {'p_high': p_high, 'p_average': p_avg, 'p_low': p_low, 'reported': reported, 'p_misclassified': 1 - pick(reported),
            'bayes': bayes, 'p_misclassified_bayes': 1 - pick(bayes), 'raw_class': raw_class, 'p_misclassified_raw': 1 - pick(raw_class)}


def validate(p):
    allowed = {'metric', 'unit', 'adjust', 'method', 'period', 'threshold', 'focus', 'top'}
    if not isinstance(p, dict) or set(p) - allowed: raise ValueError(f'profile parameters: {sorted(allowed)}')
    if p.get('metric') not in METRICS: raise ValueError(f'profile metric must be one of {sorted(METRICS)}')
    unit = p.get('unit', 'pcp')
    if unit not in UNITS: raise ValueError(f'profile unit must be one of {UNITS}')
    adjust = p.get('adjust', 'auto')
    if adjust not in ADJUSTMENTS: raise ValueError(f'adjust must be one of {ADJUSTMENTS}')
    kind = METRICS[p['metric']][0]
    if adjust == 'auto': adjust = 'none' if kind == 'share' and p['metric'] != 'readmission_rate' else 'age_sex'
    method = p.get('method', 'auto')
    if method not in METHODS: raise ValueError(f'method must be one of {METHODS}')
    if method == 'auto': method = 'bayes'
    period = p.get('period') or {}
    if not isinstance(period, dict) or set(period) - {'start', 'end'} or \
            any(v and (not isinstance(v, str) or len(v) != 10) for v in period.values()):
        raise ValueError('period takes ISO start/end dates')
    threshold = p.get('threshold')
    if threshold is not None:
        if type(threshold) not in (int, float) or not math.isfinite(threshold) or threshold < 0: raise ValueError('threshold must be a non-negative number')
        threshold = float(threshold)
        if kind == 'share' and threshold > 1:
            if threshold > 100: raise ValueError('share thresholds are 0..1 or a percent')
            threshold /= 100
    focus = p.get('focus') or ''
    if not isinstance(focus, str) or len(focus) > 80: raise ValueError('focus: one unit id')
    top = p.get('top', 200)
    if type(top) is not int or not 1 <= top <= 500: raise ValueError('top 1..500')
    return {'metric': p['metric'], 'unit': unit, 'adjust': adjust, 'method': method,
            'period': {'start': period.get('start', ''), 'end': period.get('end', '')}, 'threshold': threshold, 'focus': focus, 'top': top}


# ---- estimators (pure functions on member-level arrays) -------------------------------------
def _group(units, *arrays):
    labels = sorted(set(units), key=str)
    idx = {u: i for i, u in enumerate(labels)}
    g = np.array([idx[u] for u in units], int)
    sums = [np.bincount(g, weights=np.asarray(a, float), minlength=len(labels)) for a in arrays]
    return labels, g, sums


def buhlmann_straub(units, x, w):
    """Non-parametric Buhlmann-Straub. units/x/w: member-level labels, observations and weights (w > 0).
    Returns {labels, W, X (unit weighted means), epv, vhm, k, Z, collective, estimate, sd (sqrt((1-Z) VHM))}."""
    x = np.asarray(x, float); w = np.asarray(w, float)
    keep = w > 0
    units = [u for u, k in zip(units, keep) if k]; x, w = x[keep], w[keep]
    labels, g, (W, WX, n) = _group(units, w, w * x, np.ones_like(w))
    X = WX / W
    J, total = len(labels), W.sum()
    dof = (n - 1).sum()
    epv = float((w * (x - X[g]) ** 2).sum() / dof) if dof > 0 else 0.0
    Xw = WX.sum() / total
    denom = total - (W ** 2).sum() / total
    vhm = float(((W * (X - Xw) ** 2).sum() - epv * (J - 1)) / denom) if J > 1 and denom > 0 else 0.0
    vhm = max(vhm, 0.0)
    if vhm <= 0:
        k, Z, collective = math.inf, np.zeros(J), float(Xw)
    else:
        k = epv / vhm; Z = W / (W + k)
        collective = float((Z * X).sum() / Z.sum())
    est = Z * X + (1 - Z) * collective
    return {'labels': labels, 'W': W, 'X': X, 'n': n, 'epv': epv, 'vhm': vhm, 'k': k, 'Z': Z, 'collective': collective,
            'estimate': est, 'sd': np.sqrt((1 - Z) * vhm)}


def overdispersion(units, y, e):
    """Quasi-Poisson dispersion of member counts around their unit's O/E (Pearson), floored at 1."""
    y = np.asarray(y, float); e = np.asarray(e, float)
    keep = e > 0
    units = [u for u, k in zip(units, keep) if k]; y, e = y[keep], e[keep]
    labels, g, (O, E) = _group(units, y, e)
    mu = (O / np.where(E > 0, E, 1))[g] * e
    dof = len(y) - len(labels)
    ok = mu > 0
    if dof <= 0 or not ok.any(): return 1.0
    return max(1.0, float(((y[ok] - mu[ok]) ** 2 / mu[ok]).sum() / dof))


CV_GRID = np.linspace(0.005, 2.0, 200)  # between-unit coefficient of variation (uniform spacing: weights are densities)


def _grid_posterior(loglik, means, cvs):
    """Hyperparameter posterior on a (mean, cv) grid: flat prior on the mean and on the between-unit CV
    (weights = grid spacing), likelihood = the model's marginal likelihood. Returns normalised weights."""
    dm = np.gradient(means) if len(means) > 1 else np.ones(1)
    dc = np.gradient(cvs)
    ll = np.array([[loglik(m, c) for c in cvs] for m in means])
    ll = np.where(np.isfinite(ll), ll, -np.inf)
    wt = np.exp(ll - ll.max()) * dm[:, None] * dc[None, :]
    return wt / wt.sum()


def _mean_grid(O, E, n=25):
    m = O.sum() / E.sum()
    se = m / math.sqrt(max(O.sum(), 1.0))
    return np.clip(np.linspace(m - 5 * se, m + 5 * se, n), m * 0.05, None)


def gamma_poisson_posterior(O, E, draws, rng):
    """theta_j ~ Gamma(shape 1/cv^2, mean mu); O_j | theta_j ~ Poisson(theta_j E_j) (O may be non-integer after
    dispersion scaling). Marginal = negative binomial; (mu, cv) integrated on a grid; theta draws from the
    conditional Gamma posteriors."""
    O = np.asarray(O, float); E = np.asarray(E, float)
    means = _mean_grid(O, E)

    def loglik(m, c):
        a = 1 / (c * c); b = a / m
        return float((gammaln(a + O) - gammaln(a) - gammaln(O + 1) + a * np.log(b / (b + E)) + O * np.log(np.where(E > 0, E, 1e-300) / (b + E))).sum())
    wt = _grid_posterior(loglik, means, CV_GRID)
    pick = rng.choice(wt.size, size=draws, p=wt.ravel())
    mi, ci = np.unravel_index(pick, wt.shape)
    a = 1 / CV_GRID[ci] ** 2; b = a / means[mi]
    sample = rng.gamma(a[:, None] + O[None, :], 1 / (b[:, None] + E[None, :]))
    i, j = np.unravel_index(wt.argmax(), wt.shape)
    return sample, {'model': 'Poisson-gamma', 'prior_mean_mode': float(means[i]), 'between_unit_cv_mode': float(CV_GRID[j]),
                    'between_unit_cv_mean': float((wt.sum(0) * CV_GRID).sum())}


def beta_binomial_posterior(O, n, draws, rng):
    """p_j ~ Beta with mean mu and SD cv x mu; O_j ~ Binomial(n_j, p_j). Marginal = beta-binomial; (mu, cv) on a grid."""
    O = np.asarray(O, float); n = np.asarray(n, float)
    means = np.clip(_mean_grid(O, n), 1e-4, 1 - 1e-4)

    def strength(m, c):
        return m * (1 - m) / (c * m) ** 2 - 1

    def loglik(m, c):
        s = strength(m, c)
        if s <= 0: return -np.inf
        a, b = m * s, (1 - m) * s
        return float((betaln(a + O, b + n - O) - betaln(a, b)).sum())
    wt = _grid_posterior(loglik, means, CV_GRID)
    pick = rng.choice(wt.size, size=draws, p=wt.ravel())
    mi, ci = np.unravel_index(pick, wt.shape)
    s = strength(means[mi], CV_GRID[ci])
    sample = rng.beta((means[mi] * s)[:, None] + O[None, :], ((1 - means[mi]) * s)[:, None] + (n - O)[None, :])
    i, j = np.unravel_index(wt.argmax(), wt.shape)
    return sample, {'model': 'beta-binomial', 'prior_mean_mode': float(means[i]), 'between_unit_cv_mode': float(CV_GRID[j]),
                    'between_unit_cv_mean': float((wt.sum(0) * CV_GRID).sum())}


def normal_posterior(X, v, draws, rng):
    """X_j ~ N(theta_j, v_j), theta_j ~ N(mu, tau^2): tau on a grid (flat prior; mu integrated analytically, flat prior),
    then mu | tau and theta | mu, tau drawn exactly."""
    X = np.asarray(X, float); v = np.asarray(v, float)
    sd = float(np.sqrt(max(np.var(X), 1e-12)))
    taus = np.concatenate([[0.0], np.linspace(sd / 200, 3 * sd, 120)])
    ll = []
    for t in taus:
        wt = 1 / (t * t + v); mu = (wt * X).sum() / wt.sum()
        ll.append(-0.5 * float((np.log(t * t + v) + wt * (X - mu) ** 2).sum()) - 0.5 * math.log(wt.sum()))
    ll = np.array(ll); p = np.exp(ll - ll.max()) * np.gradient(taus); p /= p.sum()
    t = taus[rng.choice(len(taus), size=draws, p=p)][:, None]
    wt = 1 / (t * t + v[None, :]); muhat = (wt * X).sum(1, keepdims=True) / wt.sum(1, keepdims=True)
    mu = muhat + rng.standard_normal((draws, 1)) / np.sqrt(wt.sum(1, keepdims=True))
    prec = 1 / v[None, :] + np.where(t > 0, 1 / np.maximum(t * t, 1e-300), np.inf)
    mean = np.where(t > 0, (X[None, :] / v[None, :] + mu / np.maximum(t * t, 1e-300)) / prec, mu)
    sample = mean + rng.standard_normal(mean.shape) / np.sqrt(prec)
    return sample, {'model': 'normal-normal', 'tau_mode': float(taus[p.argmax()]), 'tau_mean': float((p * taus).sum())}


def analyse(units, y, w, e, kind, method='bayes', threshold_rel=None, draws=DRAWS, seed=SEED):
    """Profile units from member-level data. units: labels; y: numerator; w: exposure (member-years, trials or
    member-months); e: expected numerator. kind: 'count' (Poisson-gamma on O/E), 'binomial' (beta-binomial, e must
    be network share x trials) or 'cost' (normal-normal on relative cost). All estimates are relative (1 = network).
    Returns per-unit arrays plus hyperparameters."""
    y = np.asarray(y, float); w = np.asarray(w, float); e = np.asarray(e, float)
    labels, g, (n, W, O, E) = _group(units, np.ones_like(y), w, y, e)
    J = len(labels)
    raw = np.where(E > 0, O / np.where(E > 0, E, 1), np.nan)
    rng = np.random.default_rng(seed)
    cred = buhlmann_straub(units, np.where(e > 0, y / np.where(e > 0, e, 1), 0), e)
    cmap = {u: i for i, u in enumerate(cred['labels'])}
    ci = [cmap.get(u) for u in labels]
    Z = np.array([cred['Z'][i] if i is not None else 0.0 for i in ci])
    cred_est = np.array([cred['estimate'][i] if i is not None else cred['collective'] for i in ci])
    cred_sd = np.array([cred['sd'][i] if i is not None else math.sqrt(cred['vhm']) for i in ci])
    hyper = {'credibility_k': None if math.isinf(cred['k']) else round(cred['k'], 4), 'epv': cred['epv'], 'vhm': cred['vhm']}
    if method == 'credibility':
        mean = cred_est; lo = cred_est - 1.959964 * cred_sd; hi = cred_est + 1.959964 * cred_sd
        sample = cred_est[None, :] + cred_sd[None, :] * rng.standard_normal((draws, J))
        hyper['model'] = 'Buhlmann-Straub credibility (normal approximation for intervals)'
    elif kind == 'binomial':
        p0 = O.sum() / W.sum()
        sample, h = beta_binomial_posterior(O, W, draws, rng)
        sample = sample / p0
        hyper.update(h)
    elif kind == 'count':
        phi = overdispersion(units, y, e)
        sample, h = gamma_poisson_posterior(O / phi, E / phi, draws, rng)
        hyper.update(h, dispersion=phi)
    else:  # cost: normal-normal on unit relative cost with process variance from the members
        s2 = cred['epv']
        ok = E > 0
        sample = np.ones((draws, J))
        sample[:, ok], h = normal_posterior(raw[ok], s2 / E[ok], draws, rng)
        hyper.update(h, process_variance=s2)
    if method != 'credibility':
        mean = sample.mean(0); lo = np.percentile(sample, 2.5, axis=0); hi = np.percentile(sample, 97.5, axis=0)
    # ranks: 1 = highest; tiny jitter breaks exact ties (complete pooling) at random
    jitter = rng.random(sample.shape) * 1e-12
    ranks = J - np.argsort(np.argsort(sample + jitter, axis=1), axis=1)
    p_above = (sample > 1.0).mean(0)
    p_thr = (sample > threshold_rel).mean(0) if threshold_rel is not None else None
    flag = np.where(p_above >= 0.975, 'truly_high', np.where(p_above <= 0.025, 'truly_low', 'indistinguishable'))
    mis = misclassification(sample, flag, raw)
    return {'misclassification': mis, 'labels': labels, 'n': n, 'exposure': W, 'observed': O, 'expected': E, 'raw': raw, 'mean': mean, 'lo': lo, 'hi': hi,
            'Z': Z, 'credibility': cred_est, 'p_above': p_above, 'p_threshold': p_thr,
            'rank_mean': ranks.mean(0), 'rank_lo': np.percentile(ranks, 2.5, axis=0, method='lower'),
            'rank_hi': np.percentile(ranks, 97.5, axis=0, method='higher'), 'flag': flag, 'hyper': hyper}


# ---- data ------------------------------------------------------------------------------------
def members(db, patient_ids, p, as_of):
    """Member-level rows: (patient_id, unit, stratum, y, w). w = member-years (rate), trials (share) or member-months (cost)."""
    start, end = vbc._period(p, as_of)
    people = vbc.eligibility(db, as_of, start, end)
    people = {k: v for k, v in people.items() if v['months'] and (patient_ids is None or k in patient_ids)}
    attr = vbc.attribution(db, people, start, end)
    burden = defaultdict(int)
    if p['adjust'] == 'age_sex_conditions':
        for pid, in db.execute('SELECT patient_id FROM concept_patient WHERE concept IN (%s)' % ','.join('?' * len(aggregate.BURDEN_CONCEPTS)),
                               aggregate.BURDEN_CONCEPTS):
            burden[pid] += 1
    metric = p['metric']; kind, concept = METRICS[metric][:2]
    y, w = defaultdict(float), {}
    if kind == 'rate':
        if concept:
            from .ed_definition import metric_visits_sql
            # Current rate concepts are ED only. Preserve event rows rather than
            # silently collapsing distinct received events to patient-days.
            query=metric_visits_sql(db) if concept=='emergency_visit' else 'SELECT DISTINCT patient_id,substr(event_date,1,10) AS event_date FROM encounter WHERE type_concept = ?'
            args=() if concept=='emergency_visit' else (concept,)
            for pid,d in db.execute('SELECT patient_id,event_date FROM ('+query+') WHERE event_date BETWEEN ? AND ?',(*args,start,end+'T99')):
                if pid in people and d and d[:7] in set(people[pid]['months']):y[pid]+=1
        else:
            for pid,d in db.execute('SELECT patient_id,admit_date FROM stay WHERE admit_date BETWEEN ? AND ?', (start, end)):
                if pid in people and d and d[:7] in set(people[pid]['months']):y[pid]+=1
        w = {pid: len(info['months']) / 12 for pid, info in people.items()}
    elif metric == 'readmission_rate':
        for pid, n, r in db.execute('SELECT patient_id, count(*), sum(readmitted_30d) FROM stay WHERE admit_date BETWEEN ? AND ? GROUP BY 1', (start, end)):
            if pid in people: w[pid] = n; y[pid] = r or 0
    elif kind == 'share':
        w, y = _quality(db, people, metric, start, end)
    else:
        claims = vbc._claims(db, start, end)
        completion, _ = vbc._completion(claims, end)
        months = {pid: set(info['months']) for pid, info in people.items()}
        for pid, month, allowed, _, _, _ in claims:
            if month in months.get(pid, ()): y[pid] += allowed / 100 * completion.get(month, 1.0)
        w = {pid: len(info['months']) for pid, info in people.items()}
    rows = []
    for pid, wi in w.items():
        info = people[pid]
        stratum = (aggregate.age_band(info['age']), info['sex'] or 'unknown') + ((min(burden[pid], 3),) if p['adjust'] == 'age_sex_conditions' else ())
        rows.append((pid, attr[pid][p['unit']] or 'unattributed', stratum if p['adjust'] != 'none' else (), y.get(pid, 0.0), float(wi)))
    return rows, {'start': start, 'end': end}


def _quality(db, people, measure, start, end):
    """Patient-level versions of vbc._quality's internal measures (same denominators and numerators)."""
    has = defaultdict(set)
    for c, pid in db.execute("SELECT concept, patient_id FROM concept_patient WHERE concept IN ('diabetes','hypertension')"): has[c].add(pid)
    latest = {}
    for concept in ('hba1c', 'systolic_bp', 'diastolic_bp'):
        for pid, v in db.execute("SELECT patient_id, value_canonical FROM (SELECT patient_id, value_canonical, row_number() OVER "
                                 "(PARTITION BY patient_id ORDER BY event_date DESC, available_date DESC) rn FROM measurement "
                                 "WHERE concept=? AND value_canonical IS NOT NULL AND event_date BETWEEN ? AND ?) WHERE rn=1", (concept, start, end)):
            latest[(concept, pid)] = v
    statin = set()
    if measure == 'statin_therapy_diabetes':
        statin = {pid for pid, payload in db.execute("SELECT patient_id, payload FROM record WHERE stream IN ('pharmacy_dispenses','pharmacy_claims') "
                                                     "AND event_date BETWEEN ? AND ?", (start, end))
                  if vbc.STATIN.search(json.loads(payload).get('medication_name') or '')}
    age = lambda i, lo, hi: i['age'] is not None and lo <= i['age'] <= hi
    den, num = {
        'a1c_poor_control': (lambda pid, i: pid in has['diabetes'] and age(i, 18, 75),
                             lambda pid: latest.get(('hba1c', pid)) is None or latest[('hba1c', pid)] > 9),
        'bp_control': (lambda pid, i: pid in has['hypertension'] and age(i, 18, 85),
                       lambda pid: (latest.get(('systolic_bp', pid)) or 999) < 140 and (latest.get(('diastolic_bp', pid)) or 999) < 90),
        'statin_therapy_diabetes': (lambda pid, i: pid in has['diabetes'] and age(i, 40, 75), lambda pid: pid in statin),
    }[measure]
    w, y = {}, {}
    for pid, info in people.items():
        if den(pid, info): w[pid] = 1; y[pid] = float(bool(num(pid)))
    return w, y


def expected(rows):
    """Indirect standardisation: each member's expected numerator at the network rate of its stratum."""
    tot = defaultdict(lambda: [0.0, 0.0])
    for _, _, s, y, w in rows: tot[s][0] += y; tot[s][1] += w
    return [tot[s][0] / tot[s][1] * w if tot[s][1] else 0.0 for _, _, s, _, w in rows]


# ---- operator --------------------------------------------------------------------------------
def execute(path, patient_ids, params, as_of=None):
    p = validate(params)
    db = vbc._connect(path)
    try:
        as_of = as_of or dict(db.execute('SELECT key, value FROM meta')).get('as_of')
        rows, period = members(db, set(patient_ids) if patient_ids is not None else None, p, as_of)
    finally:
        db.close()
    kind, _, label, display = METRICS[p['metric']]
    scale = 1000.0 if kind == 'rate' else 1.0
    total_y, total_w = sum(r[3] for r in rows), sum(r[4] for r in rows)
    level = total_y / total_w if total_w else None  # network numerator per unit of exposure
    report = {'schema': VERSION, 'parameters': p, 'period': period, 'metric_label': label, 'display_unit': display,
              'members': len(rows), 'unattributed_members': sum(r[1] == 'unattributed' for r in rows),
              'network_rate': round(level * scale, 4) if level is not None else None, 'units': [], 'notes': []}
    e = expected(rows)
    prof = [(r, ei) for r, ei in zip(rows, e) if r[1] != 'unattributed']
    if not level or len({r[1] for r, _ in prof}) < 2:
        report['notes'].append('Fewer than two attributed units with events in scope: nothing to compare.' if prof else 'No members in scope.')
        report['method'] = _method(p, report, None); return report
    model_kind = 'cost' if kind == 'cost' else 'binomial' if kind == 'share' and p['adjust'] == 'none' else 'count'
    thr_rel = (p['threshold'] / scale) / level if p['threshold'] is not None else None
    res = analyse([r[1] for r, _ in prof], [r[3] for r, _ in prof], [r[4] for r, _ in prof], [ei for _, ei in prof],
                  model_kind, p['method'], thr_rel)
    disp = lambda v: None if v is None or not math.isfinite(v) else round(float(v) * level * scale, 4 if kind == 'share' else 2)
    units = []
    for j, u in enumerate(res['labels']):
        units.append({'group': u, 'members': int(res['n'][j]), 'exposure': round(float(res['exposure'][j]), 2),
                      'observed': round(float(res['observed'][j]), 2), 'expected': round(float(res['expected'][j]), 2),
                      'raw_rate': round(float(res['observed'][j] / res['exposure'][j] * scale), 4 if kind == 'share' else 2) if res['exposure'][j] else None,
                      'oe_ratio': round(float(res['raw'][j]), 3) if math.isfinite(res['raw'][j]) else None,
                      'adjusted_raw_rate': disp(res['raw'][j]),
                      'posterior_rate': disp(res['mean'][j]), 'ci95_low': disp(max(res['lo'][j], 0.0)), 'ci95_high': disp(res['hi'][j]),
                      'credibility_z': round(float(res['Z'][j]), 3), 'credibility_rate': disp(res['credibility'][j]),
                      'p_above_network': round(float(res['p_above'][j]), 4),
                      **({'p_above_threshold': round(float(res['p_threshold'][j]), 4)} if res['p_threshold'] is not None else {}),
                      'rank': round(float(res['rank_mean'][j]), 1), 'rank_ci95': [int(res['rank_lo'][j]), int(res['rank_hi'][j])],
                      'flag': str(res['flag'][j]), **_mis_row(res['misclassification'], j, p['metric'])})
    units.sort(key=lambda r: (-(r['posterior_rate'] if r['posterior_rate'] is not None else -1e18), str(r['group'])))
    hyper = {k: (round(v, 6) if isinstance(v, float) else v) for k, v in res['hyper'].items()}
    report.update(units=units[:p['top']], units_total=len(units), hyper=hyper,
                  counts={f: sum(u['flag'] == f for u in units) for f in FLAGS},
                  misclassification=_mis_summary(units),
                  shrinkage_median=_median_shrink(units, level * scale))
    if p['focus']:
        hit = next((u for u in units if str(u['group']).lower() == p['focus'].lower()), None)
        report['focus'] = hit
        if hit is None: report['notes'].append(f"Unit {p['focus']} is not among the {len(units)} attributed {UNIT_WORDS[p['unit']]} in scope.")
    report['method'] = _method(p, report, hyper)
    return report


def _verdict(cls, metric):
    if cls == 'average': return 'no different'
    better = (cls == 'high') == (metric in HIGHER_IS_BETTER)
    return 'better' if better else 'worse'


def _mis_row(m, j, metric):
    r = lambda v: round(float(v), 4)
    return {'p_below_network': None, 'p_true_high': r(m['p_high'][j]), 'p_true_average': r(m['p_average'][j]), 'p_true_low': r(m['p_low'][j]),
            'classification': _verdict(str(m['reported'][j]), metric), 'p_misclassified': r(m['p_misclassified'][j]),
            'bayes_classification': _verdict(str(m['bayes'][j]), metric), 'p_misclassified_bayes': r(m['p_misclassified_bayes'][j]),
            'raw_classification': _verdict(str(m['raw_class'][j]), metric), 'p_misclassified_raw': r(m['p_misclassified_raw'][j]),
            'credible_outlier': str(m['reported'][j]) != 'average'}


def _mis_summary(units):
    """Expected number of units misclassified under each classification rule (sum of per-unit misclassification probabilities)."""
    for u in units:
        if u.get('p_below_network') is None: u['p_below_network'] = round(1 - u['p_above_network'], 4)
    n = len(units)
    exp = lambda k: round(sum(u[k] for u in units), 2)
    return {'margin': MARGIN, 'units': n, 'expected_misclassified': exp('p_misclassified'),
            'expected_misclassified_bayes': exp('p_misclassified_bayes'), 'expected_misclassified_raw': exp('p_misclassified_raw'),
            'credible_outliers': sum(u['credible_outlier'] for u in units),
            'units_with_misclassification_over_20pct': sum(u['p_misclassified'] > 0.2 for u in units),
            'definition': f'true class high/low = relative rate more than {MARGIN:.0%} above/below the network, else no different; '
                          'P(misclassified) = posterior probability the true class differs from the reported one'}


def _median_shrink(units, net):
    """Median share of the raw (adjusted) deviation from the network that the estimate removed (0 = none, 1 = all)."""
    out = []
    for u in units:
        if u['adjusted_raw_rate'] is None or u['posterior_rate'] is None: continue
        if abs(u['adjusted_raw_rate'] - net) > 1e-9 * max(1.0, abs(net)):
            out.append(1 - (u['posterior_rate'] - net) / (u['adjusted_raw_rate'] - net))
    return round(float(np.median(out)), 3) if out else None


def _method(p, report, hyper):
    kind = METRICS[p['metric']][0]
    adj = {'none': 'no case-mix adjustment (expected = network rate x exposure)',
           'age_sex': 'indirect standardisation by age band x sex', 'age_sex_conditions': 'indirect standardisation by age band x sex x chronic-condition burden (0-3+)'}[p['adjust']]
    text = (f"Fair comparison of {UNIT_WORDS[p['unit']]} on {METRICS[p['metric']][2]}, {report['period']['start']} to {report['period']['end']}. "
            f"Members with enrolled months in the period; hybrid attribution (assigned PCP, else plurality of primary-care visits); unattributed members "
            f"stay in the network reference but are not profiled. {adj[0].upper() + adj[1:]}; each unit is compared on O/E and shown on the network scale. ")
    text += {'rate': 'Exposure = enrolled member-years; only events in enrolled months count. ED uses the governed all-source event rows '
                    '(older snapshots without that relation use the all-source patient-day definition); other visits use distinct event days and admissions use hospital stays. '
                     'Poisson-gamma random effects (negative-binomial marginal likelihood) after quasi-Poisson overdispersion '
                     'scaling of member counts. ',
             'share': ('Denominator = index hospital stays; numerator = 30-day readmissions. ' if p['metric'] == 'readmission_rate' else
                       'Internal measure definitions (as in the VBC quality metric; not certified HEDIS). ') +
                      ('Beta-binomial random effects (beta-binomial marginal likelihood). ' if p['adjust'] == 'none' else
                       'Adjusted shares use the Poisson-gamma O/E model (Poisson approximation to the binomial). '),
             'cost': 'Paid medical + pharmacy claims in enrolled months, chain-ladder completion; exposure = member-months. '
                     'Normal-normal random effects on relative cost (process variance from members). '}[kind]
    text += ('Buhlmann-Straub credibility (actuarial companion): Z = W/(W + k) with k = EPV/VHM estimated non-parametrically from members '
             '(W = expected numerator); credibility rate = Z x unit + (1 - Z) x collective mean. '
             f"Flags, probabilities and ranks use the {'credibility (normal approximation)' if p['method'] == 'credibility' else 'Bayesian posterior'}: "
             f"{DRAWS:,} joint draws; truly high/low = P(above network average) >= 97.5% / <= 2.5% (95% interval excludes the network); "
             'rank 1 = highest value, with a 95% rank interval. Hyperparameters (network mean, between-unit CV or SD) are integrated '
             'on a grid under flat priors, so a zero between-unit variance estimate does not collapse every unit onto the mean; '
             'credibility intervals are normal approximations. '
             f'Misclassification probability per unit = posterior probability that its true class (more than {MARGIN:.0%} above / below the '
             'network, else no different) differs from the reported class; the expected number misclassified sums these, and is also shown for '
             'a raw-rate ranking and for the most-probable-class rule. '
             'Differences are associations with the unit, not proof of practice quality: attribution, coding and unmeasured case mix remain.')
    return text


def summary(report):
    p = report['parameters']; units = report['units']
    kind = METRICS[p['metric']][0]
    f = (lambda v: f'{v:.1%}') if kind == 'share' else (lambda v: f'${v:,.2f}') if kind == 'cost' else (lambda v: f'{v:,.0f}')
    if not units: return 'No fair comparison possible: ' + (' '.join(report['notes']) or 'no units in scope.')
    c = report['counts']; n = report['units_total']
    adj = '' if p['adjust'] == 'none' else ', adjusted for ' + p['adjust'].replace('age_sex_conditions', 'age, sex and chronic-condition burden').replace('age_sex', 'age and sex')
    text = ''
    focus = report.get('focus')
    if focus:
        text += (f"{focus['group']}: {f(focus['posterior_rate'])} {report['display_unit'] if kind == 'rate' else ''}".rstrip() +
                 f" after shrinkage (95% interval {f(focus['ci95_low'])}-{f(focus['ci95_high'])}; raw {f(focus['raw_rate'])} on {focus['members']} members), "
                 f"P(above network) {focus['p_above_network']:.0%}, rank {focus['rank']:g} of {n} (95% {focus['rank_ci95'][0]}-{focus['rank_ci95'][1]}): "
                 f"{'truly high' if focus['flag'] == 'truly_high' else 'truly low' if focus['flag'] == 'truly_low' else 'not distinguishable from the network average'} "
                 f"({focus['classification']}; {focus['p_misclassified']:.0%} probability that label is wrong). ")
    text += (f"Of {n} {UNIT_WORDS[p['unit']]} profiled on {report['metric_label']}{adj} (network {f(report['network_rate'])}), "
             f"{c['truly_high']} truly high, {c['truly_low']} truly low, {c['indistinguishable']} indistinguishable from average.")
    high = [u for u in units if u['flag'] == 'truly_high'][:3] or units[:1]
    text += ' Highest: ' + '; '.join(f"{u['group']} {f(u['posterior_rate'])} (95% {f(u['ci95_low'])}-{f(u['ci95_high'])}, raw {f(u['raw_rate'])}, "
                                     f"{u['members']} members, P(above) {u['p_above_network']:.0%}, rank {u['rank_ci95'][0]}-{u['rank_ci95'][1]})" for u in high) + '.'
    if p['threshold'] is not None:
        over = sum(u.get('p_above_threshold', 0) >= 0.95 for u in units)
        text += f" {over} of {n} are above {f(p['threshold'])} with at least 95% probability."
    mis = report.get('misclassification')
    if mis:
        text += (f" Misclassification: expected {mis['expected_misclassified']:g} of {n} {UNIT_WORDS[p['unit']]} misclassified under the 95% flags "
                 f"(vs {mis['expected_misclassified_raw']:g} if ranked on raw rates; {mis['expected_misclassified_bayes']:g} under the most-probable class), "
                 f"true difference = more than {mis['margin']:.0%} from the network; {mis['credible_outliers']} credible outlier(s); "
                 f"{mis['units_with_misclassification_over_20pct']} with over 20% chance their label is wrong.")
    k = report['hyper'].get('credibility_k')
    text += (f" Buhlmann-Straub credibility k = {k:,.1f} expected {'dollars' if kind == 'cost' else 'events'}"
             f" (Z = W/(W + k); median Z {np.median([u['credibility_z'] for u in units]):.2f})." if k is not None else
             ' Credibility: no between-unit variation beyond chance (Z = 0, every unit at the collective mean).')
    for note in report['notes']: text += ' ' + note
    return text
