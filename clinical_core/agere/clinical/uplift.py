"""Uplift: who BENEFITS from outreach / care management (heterogeneous treatment effect), not who is at risk.

Cohort (from the semantic layer, same exposure rules as program_effect): treated = members whose first qualifying
record of the program stream (care-management contact the patient agreed to, ...) falls on the index date, or an explicit
`treated_patient_ids` list with one `index_date`. Comparison = members with no record of that stream at all (selected
non-participants are dropped, as in program_effect); each inherits a sampled treated index date. Covariates are measured
before the index date: age, sex, Medicare coverage, ED visits and admissions in the pre window, chronic-condition count
and an internal baseline-risk score (cross-fitted Poisson ridge on comparison members). Outcome = events in
(index, index + post_days], binary (any event) or count.

Estimation
  1. Propensity P(treated | X): cross-fitted ridge logistic (IRLS in numpy). Members outside [trim, 1 - trim] are dropped
     from fitting and evaluation (overlap trimming) and never recommended.
  2. Learners with K-fold cross-fitting; every member's prediction comes from models that never saw that member:
       T-learner  tau(x) = mu1(x) - mu0(x), separate ridge logistic/Poisson outcome models per arm;
       X-learner  (Kunzel et al. 2019) impute effects D1 = Y - mu0(x) on treated, D0 = mu1(x) - Y on controls, regress each
                  on x (ridge), blend tau = e(x) tau0(x) + (1 - e(x)) tau1(x).
  3. Evaluation on the held-out predictions. The ATE uses the doubly robust score
       Gamma = mu1 - mu0 + T (Y - mu1) / e - (1 - T)(Y - mu0) / (1 - e)     (out-of-fold mu, e);
     ranking metrics use the contrast-free score (T - e)(Y - m) / (e (1 - e)) (out-of-fold pooled m, e), whose
     conditional mean is tau(x) but which does not contain the learner's own mu1 - mu0, so noise cannot score well:
       RATE / AUTOC (Yadlowsky et al.): area under the TOC curve, mean over treated fractions q of
         [mean benefit of the top-q members ranked by predicted benefit - overall mean benefit];
       Qini: area between the cumulative incremental-benefit curve (per member) and the random-targeting line;
       both with nonparametric bootstrap CIs over members, plus calibration of predicted benefit by decile.
  A RATE CI that excludes 0 means ranking by predicted benefit concentrates benefit; a CI covering 0 means no
  heterogeneity was detected, and targeting by uplift is not supported over random targeting.
Benefit = reduction in the outcome (direction 'decrease'); positive uplift = the program helps that member.

Targeting: among members not yet treated (and inside overlap), value = predicted events averted x value per event
(planning assumption from outreach.ASSUMPTIONS), minus an optional cost per contact; take the highest positive net
values up to the patient / nurse-hour capacity (exact for additive values, as in outreach).

Nothing here is randomised. Effects are estimated under no-unmeasured-confounding given the covariates and stated overlap;
they do not authorize clinical action.
"""
from collections import defaultdict
from datetime import date
import json, math, random, sqlite3

import numpy as np

from . import aggregate, outreach, program_effect, semantic_layer

VERSION = 'uplift/2'
OUTCOMES = ('ed_visits', 'admissions', 'ed_or_admission')
OUTCOME_TYPES = ('binary', 'count')
LEARNERS = ('x', 't', 'dml', 'causal_forest')  # dml = cross-fitted double ML R-learner, causal_forest = honest GRF-style forest (causal_hte.py)
LEARNER_SETS = {'both': ('x', 't'), 'all': LEARNERS}
MIN_ARM = 30
FEATURES = ('age', 'female', 'medicare', 'prior_ed', 'prior_admissions', 'chronic_conditions', 'baseline_risk')
ASSUMPTIONS = [
    'No unmeasured confounding of program receipt given age, sex, coverage, pre-period ED visits/admissions, chronic-condition count '
    'and baseline risk (receipt was not randomised; care-team selection is not fully observed).',
    'Overlap: members with an estimated treatment probability outside the trimming bounds are excluded from fitting and evaluation.',
    'Exposure = first qualifying received record; contacts recorded elsewhere are invisible. Outcomes are complete in the follow-up window.',
    'No spillover between members. Value per event is a stated planning assumption (outreach.ASSUMPTIONS), not a measured cost.',
]


# ---- parameters --------------------------------------------------------------------
def _num(p, key, default, lo, hi, integer=False):
    v = p.get(key, default)
    ok = type(v) is int if integer else (type(v) in (int, float) and math.isfinite(v))
    if not ok or not lo <= v <= hi: raise ValueError(f'{key} must be {"an integer" if integer else "a number"} {lo}..{hi}')
    return v if integer else float(v)


def validate(p):
    allowed = {'program', 'treated_patient_ids', 'index_date', 'outcome', 'outcome_type', 'pre_days', 'post_days', 'folds', 'l2', 'trim',
               'learners', 'primary_learner', 'bootstrap', 'seed', 'capacity_patients', 'capacity_hours', 'minutes_per_contact',
               'value_per_event_usd', 'cost_per_contact_usd', 'target_population', 'top', 'interactions', 'population_conditions'}
    if not isinstance(p, dict) or set(p) - allowed:
        raise ValueError('uplift parameters: ' + ', '.join(sorted(allowed)))
    out = {'program': p.get('program', 'care_management'), 'outcome': p.get('outcome', 'ed_or_admission'),
           'outcome_type': p.get('outcome_type', 'binary'), 'learners': p.get('learners', 'all'),
           'primary_learner': p.get('primary_learner', 'x'), 'target_population': p.get('target_population', 'untreated'),
           'interactions': p.get('interactions', False)}
    ids = p.get('treated_patient_ids')
    if ids is not None:
        if not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids): raise ValueError('treated_patient_ids: non-empty list of patient ids')
        try: date.fromisoformat(str(p.get('index_date', ''))[:10])
        except ValueError: raise ValueError('treated_patient_ids needs index_date (YYYY-MM-DD)')
        out['treated_patient_ids'] = sorted(set(ids)); out['index_date'] = str(p['index_date'])[:10]
        out['program'] = 'provided_cohort'
    elif p.get('index_date'): raise ValueError('index_date applies with treated_patient_ids only')
    elif out['program'] not in program_effect.PROGRAMS: raise ValueError(f'program must be one of {sorted(program_effect.PROGRAMS)} or pass treated_patient_ids')
    if out['outcome'] not in OUTCOMES: raise ValueError(f'outcome must be one of {OUTCOMES}')
    if out['outcome_type'] not in OUTCOME_TYPES: raise ValueError(f'outcome_type must be one of {OUTCOME_TYPES}')
    if out['learners'] not in (*LEARNER_SETS, *LEARNERS): raise ValueError(f"learners must be one of {(*LEARNER_SETS, *LEARNERS)}")
    if out['primary_learner'] not in LEARNERS: raise ValueError(f'primary_learner must be one of {LEARNERS}')
    if out['primary_learner'] not in LEARNER_SETS.get(out['learners'], (out['learners'],)): out['primary_learner'] = LEARNER_SETS.get(out['learners'], (out['learners'],))[0]
    if out['target_population'] not in ('untreated', 'all'): raise ValueError("target_population must be 'untreated' or 'all'")
    if type(out['interactions']) is not bool: raise ValueError('interactions must be true or false')
    out['pre_days'] = _num(p, 'pre_days', 180, 30, 730, True); out['post_days'] = _num(p, 'post_days', 90, 14, 365, True)
    out['folds'] = _num(p, 'folds', 5, 2, 10, True); out['l2'] = _num(p, 'l2', 1.0, 1e-3, 1e3)
    out['trim'] = _num(p, 'trim', 0.05, 0.0, 0.25); out['bootstrap'] = _num(p, 'bootstrap', 200, 50, 2000, True)
    out['seed'] = _num(p, 'seed', 7, 0, 2 ** 31 - 1, True); out['top'] = _num(p, 'top', 200, 1, 5000, True)
    out['capacity_patients'] = _num(p, 'capacity_patients', 50, 1, 5000, True) if (p.get('capacity_patients') is not None or p.get('capacity_hours') is None) else None
    out['capacity_hours'] = _num(p, 'capacity_hours', None, 0.25, 2000) if p.get('capacity_hours') is not None else None
    out['minutes_per_contact'] = _num(p, 'minutes_per_contact', 30, 1, 480, True)
    v = p.get('value_per_event_usd')
    out['value_per_event_usd'] = None if v is None else _num(p, 'value_per_event_usd', None, 0, 1e7)
    out['cost_per_contact_usd'] = _num(p, 'cost_per_contact_usd', 0.0, 0, 1e6)
    conds = p.get('population_conditions', [])
    if not isinstance(conds, list) or len(conds) > 5: raise ValueError('population_conditions: up to five condition concepts')
    if conds:
        from . import concepts
        if any(c not in concepts.CONDITIONS for c in conds): raise ValueError('population_conditions: unknown condition concept')
        out['population_conditions'] = conds
    return out


# ---- regularised GLMs (IRLS ridge, numpy only) ---------------------------------------
def fit_glm(X, y, family, l2=1.0, iters=60):
    """Ridge GLM; intercept unpenalised. family: gaussian | logistic | poisson. Returns (b0, beta)."""
    n, d = X.shape
    A = np.hstack([np.ones((n, 1)), X])
    pen = l2 * np.eye(d + 1); pen[0, 0] = 1e-8
    if family == 'gaussian':
        w = np.linalg.solve(A.T @ A + pen, A.T @ y)
        return float(w[0]), w[1:]
    w = np.zeros(d + 1)
    ybar = float(np.clip(np.mean(y), 1e-4, 1 - 1e-4)) if family == 'logistic' else max(float(np.mean(y)), 1e-4)
    w[0] = math.log(ybar / (1 - ybar)) if family == 'logistic' else math.log(ybar)
    for _ in range(iters):
        eta = np.clip(A @ w, -30, 30)
        if family == 'logistic':
            mu = 1 / (1 + np.exp(-eta)); W = np.maximum(mu * (1 - mu), 1e-6)
        else:
            mu = np.exp(eta); W = np.maximum(mu, 1e-6)
        z = eta + (y - mu) / W
        new = np.linalg.solve(A.T @ (A * W[:, None]) + pen, A.T @ (W * z))
        done = np.max(np.abs(new - w)) < 1e-8
        w = new
        if done: break
    return float(w[0]), w[1:]


def predict_glm(model, X, family):
    eta = model[0] + X @ model[1]
    if family == 'gaussian': return eta
    eta = np.clip(eta, -30, 30)
    return 1 / (1 + np.exp(-eta)) if family == 'logistic' else np.exp(eta)


def _expand(X, interactions):
    if not interactions: return X
    d = X.shape[1]
    cols = [X] + [(X[:, i] * X[:, j])[:, None] for i in range(d) for j in range(i + 1, d)]
    return np.hstack(cols)


def _folds(t, k, rng):
    """Stratified fold assignment by treatment arm."""
    f = np.zeros(len(t), dtype=int)
    for arm in (0, 1):
        idx = np.flatnonzero(t == arm); rng.shuffle(idx)
        f[idx] = np.arange(len(idx)) % k
    return f


# ---- evaluation: RATE (AUTOC) and Qini -----------------------------------------------
def _curves(order_score, G):
    """TOC / Qini ingredients for benefit scores G ranked by order_score (descending). Returns (autoc, qini, toc, cum)."""
    n = len(G)
    o = np.argsort(-order_score, kind='stable')
    g = G[o]
    cum = np.cumsum(g)
    k = np.arange(1, n + 1)
    toc = cum / k - g.mean()
    autoc = float(toc.mean())
    qini = float(np.mean(cum / n - k / n * g.mean()))
    return autoc, qini, toc, cum


def _boot(order_score, G, B, rng):
    n = len(G)
    a, q = np.empty(B), np.empty(B)
    for b in range(B):
        i = rng.integers(0, n, n)
        a[b], q[b], _, _ = _curves(order_score[i], G[i])
    return a, q


def _ci(draws): return [float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5))]


def _within_fold_rank(pred, fold):
    """Percentile rank of the prediction inside its own fold. Cross-fitted predictions carry a fold-level offset that moves
    opposite to the held-out noise of that fold (the training folds hold the rest of the sample); ranking inside folds removes it."""
    out = np.zeros(len(pred))
    for f in np.unique(fold):
        idx = np.flatnonzero(fold == f)
        out[idx] = (np.argsort(np.argsort(pred[idx], kind='stable'), kind='stable') + 0.5) / len(idx)
    return out


def _calibration(pred, G, groups=10, order=None):
    o = np.argsort(-(pred if order is None else order), kind='stable')
    rows = []
    for j, idx in enumerate(np.array_split(o, groups), 1):
        if not len(idx): continue
        g = G[idx]
        rows.append({'decile': j, 'n': int(len(idx)), 'predicted_benefit': float(pred[idx].mean()), 'observed_benefit': float(g.mean()),
                     'observed_se': float(g.std(ddof=1) / math.sqrt(len(idx))) if len(idx) > 1 else None})
    return rows


def _evaluate(pred, G, B, rng, fold):
    """pred: predicted benefit; G: benefit scores (same units); fold: cross-fit fold of each member. Held-out members only."""
    order = _within_fold_rank(pred, fold)
    autoc, qini, toc, cum = _curves(order, G)
    a, q = _boot(order, G, B, rng)
    cal = _calibration(pred, G, order=order)
    px = np.array([c['predicted_benefit'] for c in cal]); ob = np.array([c['observed_benefit'] for c in cal])
    slope = float(np.polyfit(px, ob, 1)[0]) if len(cal) >= 3 and np.ptp(px) > 1e-12 else None
    top, bot = cal[0], cal[-1]
    se = math.sqrt((top['observed_se'] or 0) ** 2 + (bot['observed_se'] or 0) ** 2)
    n = len(G)
    grid = sorted({max(1, int(round(n * q_))) for q_ in (0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0)})
    ci_a, ci_q = _ci(a), _ci(q)
    return {'rate_autoc': {'estimate': autoc, 'ci95': ci_a, 'excludes_zero': bool(ci_a[0] > 0 or ci_a[1] < 0)},
            'qini': {'coefficient': qini, 'ci95': ci_q, 'excludes_zero': bool(ci_q[0] > 0 or ci_q[1] < 0)},
            'toc_curve': [{'fraction': k / n, 'toc': float(toc[k - 1]), 'qini_cumulative_per_member': float(cum[k - 1] / n)} for k in grid],
            'calibration': cal, 'calibration_slope': slope,
            'top_minus_bottom_decile': {'estimate': float(top['observed_benefit'] - bot['observed_benefit']),
                                        'se': se, 'ci95': [float(top['observed_benefit'] - bot['observed_benefit'] - 1.96 * se),
                                                           float(top['observed_benefit'] - bot['observed_benefit'] + 1.96 * se)]}}


# ---- core estimator on arrays ---------------------------------------------------------
def analyze(X, t, y, family='logistic', folds=5, l2=1.0, trim=0.05, bootstrap=200, seed=7, learners=('x', 't'),
            primary='x', interactions=False, direction='decrease', names=None):
    """Cross-fitted T/X-learner uplift on arrays. X (n,d) raw covariates, t in {0,1}, y outcome (binary or count).
    Returns dict with per-member arrays ('members') and evaluation per learner. Uplift is BENEFIT: positive = outcome reduced
    (direction 'decrease') or increased (direction 'increase') by treatment."""
    X = np.asarray(X, float); t = np.asarray(t, int); y = np.asarray(y, float)
    n, d = X.shape
    names = list(names or [f'x{j}' for j in range(d)])
    sgn = -1.0 if direction == 'decrease' else 1.0
    rng = np.random.default_rng(seed)
    mu_, sd_ = X.mean(0), X.std(0); sd_[sd_ < 1e-12] = 1.0
    Z = _expand((X - mu_) / sd_, interactions)
    fold = _folds(t, folds, rng)
    # propensity, cross-fitted
    e = np.zeros(n)
    for f in range(folds):
        tr, te = fold != f, fold == f
        e[te] = predict_glm(fit_glm(Z[tr], t[tr].astype(float), 'logistic', l2), Z[te], 'logistic')
    e_full = predict_glm(fit_glm(Z, t.astype(float), 'logistic', l2), Z, 'logistic')
    lo, hi = trim, 1 - trim
    keep = (e >= lo) & (e <= hi) if trim > 0 else np.ones(n, bool)
    ec = np.clip(e, 1e-3, 1 - 1e-3)
    out = {'n': n, 'n_treated': int(t.sum()), 'n_control': int(n - t.sum()), 'kept': int(keep.sum()),
           'kept_treated': int(t[keep].sum()), 'kept_control': int(keep.sum() - t[keep].sum()),
           'trimmed_treated': int(t[~keep].sum()), 'trimmed_control': int((1 - t[~keep]).sum()), 'trim_bounds': [lo, hi],
           'propensity': {'mean_treated': float(e[t == 1].mean()), 'mean_control': float(e[t == 0].mean()),
                          'min': float(e.min()), 'max': float(e.max()), 'auc': _auc(e, t)}}
    if out['kept_treated'] < MIN_ARM or out['kept_control'] < MIN_ARM:
        out['status'] = 'too_few_after_trimming'
        return out
    tau = {l: np.full(n, np.nan) for l in learners}
    mu1h, mu0h, mh = np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
    for f in range(folds):
        te = fold == f
        trk = (fold != f) & keep
        t1, t0 = trk & (t == 1), trk & (t == 0)
        m1, m0 = fit_glm(Z[t1], y[t1], family, l2), fit_glm(Z[t0], y[t0], family, l2)
        mu1, mu0 = predict_glm(m1, Z[te], family), predict_glm(m0, Z[te], family)
        mu1h[te], mu0h[te] = mu1, mu0
        mh[te] = predict_glm(fit_glm(Z[trk], y[trk], family, l2), Z[te], family)  # pooled E[Y|x], contrast-free
        if 't' in learners: tau['t'][te] = mu1 - mu0
        if 'x' in learners:
            D1 = y[t1] - predict_glm(m0, Z[t1], family)
            D0 = predict_glm(m1, Z[t0], family) - y[t0]
            g1, g0 = fit_glm(Z[t1], D1, 'gaussian', l2), fit_glm(Z[t0], D0, 'gaussian', l2)
            tau['x'][te] = ec[te] * predict_glm(g0, Z[te], 'gaussian') + (1 - ec[te]) * predict_glm(g1, Z[te], 'gaussian')
    ml = [l for l in learners if l in ('dml', 'causal_forest')]
    if ml:  # causal ML learners: gradient-boosted nuisances, same folds and overlap set (causal_hte.py)
        from . import causal_hte
        cm = causal_hte.fit((X - mu_) / sd_, t, y, fold, keep, ml, seed)
        for l in ml: tau[l] = cm['tau'][l]
        out['plr_projection_benefit'] = {**cm['plr_projection'], 'estimate': sgn * cm['plr_projection']['estimate'],
                                  'ci95': sorted(sgn * v for v in cm['plr_projection']['ci95'])}
        if cm['importance'] is not None: out['causal_forest_importance'] = {names[j]: float(cm['importance'][j]) for j in range(d)}
    gamma = mu1h - mu0h + t * (y - mu1h) / ec - (1 - t) * (y - mu0h) / (1 - ec)
    # Ranking-evaluation scores must not contain the learners' own contrast mu1 - mu0 (that would reward any ranking by
    # construction): use the residual-on-residual transform (T - e)(Y - m) / (e (1 - e)), whose conditional mean is tau(x)
    # for out-of-fold pooled m and e. The ATE keeps the doubly robust score.
    rscore = (t - ec) * (y - mh) / (ec * (1 - ec))
    G = sgn * rscore[keep]
    Gate = sgn * gamma[keep]
    ate = float(Gate.mean()); ate_se = float(Gate.std(ddof=1) / math.sqrt(len(Gate)))
    out['ate_benefit'] = {'estimand': 'average_treatment_effect',
                          'population': 'members retained under the declared propensity-overlap bounds',
                          'n': int(keep.sum()), 'method': 'cross-fitted augmented inverse probability weighting',
                          'estimate': ate, 'se': ate_se, 'ci95': [ate - 1.96 * ate_se, ate + 1.96 * ate_se],
                          'observed_rate_treated': float(y[keep & (t == 1)].mean()), 'observed_rate_control': float(y[keep & (t == 0)].mean())}
    evals = {}
    for l in learners:
        evals[l] = _evaluate(sgn * tau[l][keep], G, bootstrap, np.random.default_rng(seed + 1 + LEARNERS.index(l)), fold[keep])
    out['learners'] = evals
    pb = sgn * tau[primary]
    # linear surrogate for per-member reasons: contribution_j = coef_j * (x_j - mean_j) toward predicted benefit
    Zs = (X - mu_) / sd_
    ok = keep & np.isfinite(pb)
    coef = np.linalg.lstsq(np.hstack([np.ones((ok.sum(), 1)), Zs[ok]]), pb[ok], rcond=None)[0]
    fitted = np.hstack([np.ones((ok.sum(), 1)), Zs[ok]]) @ coef
    ss = float(np.sum((pb[ok] - pb[ok].mean()) ** 2))
    out['surrogate'] = {'r2': float(1 - np.sum((pb[ok] - fitted) ** 2) / ss) if ss > 1e-18 else None,
                        'coefficients': {names[j]: float(coef[j + 1]) for j in range(d)}}
    out['members'] = {'benefit': pb, 'tau_x': tau.get('x'), 'tau_t': tau.get('t'), 'baseline_rate': mu0h, 'propensity': e,
                      'propensity_full': e_full, 'in_overlap': keep, 'contrib': Zs * coef[1:], 'z': Zs}
    out['status'] = 'estimated'
    out['primary'] = primary
    out['feature_names'] = names
    return out


def _auc(score, t):
    r = np.argsort(np.argsort(score)) + 1.0
    n1 = int(t.sum()); n0 = len(t) - n1
    return float((r[t == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)) if n1 and n0 else None


# ---- cohort from the semantic layer --------------------------------------------------
def _day(s): return date.fromisoformat(s[:10]).toordinal()


def _cohort(db, p, as_of, keep_ids):
    as_of_day = _day(as_of)
    pe = {'outcome': 'ed_visits', 'population_conditions': p.get('population_conditions', []), 'program': p['program'], 'pre_days': p['pre_days']}
    people, _, ed, adm = program_effect._load(db, pe, as_of_day, keep_ids)
    raw = {pid: (age, sex) for pid, age, sex in db.execute('SELECT patient_id, age_reported, sex_reported FROM patient') if pid in people}
    notes = {}
    if p['program'] == 'provided_cohort':
        d0 = _day(p['index_date'])
        first = {pid: d0 for pid in p['treated_patient_ids'] if pid in people}
        touched = set(first)
        definition = f'Provided cohort of {len(p["treated_patient_ids"])} patient ids, all indexed at {p["index_date"]}'
        notes = {'provided': len(p['treated_patient_ids']), 'found_in_scope': len(first)}
    else:
        first, touched, definition, notes = program_effect._exposure(db, pe, people, as_of_day)
    excluded = defaultdict(int)
    treated = {}
    for pid, t0 in sorted(first.items()):
        if t0 + p['post_days'] > as_of_day: excluded['follow_up_incomplete'] += 1; continue
        if pid not in raw or raw[pid][0] is None: excluded['age_missing'] += 1; continue
        treated[pid] = t0
    pool = sorted(set(people) - touched)
    pool = [pid for pid in pool if pid in raw and raw[pid][0] is not None]
    rng = random.Random(p['seed'])
    t_days = sorted(treated.values())
    index = dict(treated)
    for pid in pool:
        index[pid] = rng.choice(t_days) if t_days else as_of_day
    rows = []
    for pid, t0 in sorted(index.items()):
        lo = t0 - p['pre_days']
        pe_n, pa_n = ed.count(pid, lo, t0), adm.count(pid, lo, t0)
        post_ed, post_adm = ed.count(pid, t0 + 1, t0 + 1 + p['post_days']), adm.count(pid, t0 + 1, t0 + 1 + p['post_days'])
        y = {'ed_visits': post_ed, 'admissions': post_adm, 'ed_or_admission': post_ed + post_adm}[p['outcome']]
        age, sex = raw[pid]
        rows.append({'pid': pid, 'treated': int(pid in treated), 'age': float(age), 'female': float(str(sex).upper().startswith('F')),
                     'medicare': float('medicare' in str(people[pid]['payer']).lower()), 'prior_ed': float(pe_n), 'prior_admissions': float(pa_n),
                     'chronic_conditions': float(people[pid]['chronic_conditions']), 'y': float(y), 'post_ed': post_ed, 'post_adm': post_adm,
                     'practice': people[pid]['practice']})
    return rows, definition, notes, dict(excluded), len(pool)


def _baseline_risk(rows, folds, l2, seed, binary):
    """Prognostic score: ridge GLM of the outcome on the other covariates, fitted on comparison members only; comparison
    members get out-of-fold values (their own outcome never enters their score), treated members get the full fit."""
    cols = ('age', 'female', 'medicare', 'prior_ed', 'prior_admissions', 'chronic_conditions')
    X = np.array([[r[c] for c in cols] for r in rows]); y = np.array([r['y'] for r in rows]); t = np.array([r['treated'] for r in rows])
    m, s = X.mean(0), X.std(0); s[s < 1e-12] = 1.0
    Z = (X - m) / s
    fam = 'logistic' if binary else 'poisson'
    ctl = np.flatnonzero(t == 0)
    score = np.zeros(len(rows))
    if len(ctl) < 2 * folds: return np.zeros(len(rows))
    full = fit_glm(Z[ctl], y[ctl], fam, l2)
    score[:] = predict_glm(full, Z, fam)
    rng = np.random.default_rng(seed); perm = rng.permutation(ctl); fid = np.arange(len(perm)) % folds
    for f in range(folds):
        te, tr = perm[fid == f], perm[fid != f]
        score[te] = predict_glm(fit_glm(Z[tr], y[tr], fam, l2), Z[te], fam)
    return score


# ---- execute ---------------------------------------------------------------------------
def _round(o, nd=5):
    if isinstance(o, float): return round(o, nd) if math.isfinite(o) else None
    if isinstance(o, dict): return {k: _round(v, nd) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [_round(v, nd) for v in o]
    return o


def execute(path, patient_ids, params, as_of=None):
    p = validate(params)
    db = sqlite3.connect(f'file:{semantic_layer.ensure(path)}?mode=ro', uri=True)
    try:
        as_of = as_of or dict(db.execute('SELECT key, value FROM meta')).get('as_of')
        rows, definition, notes, excluded, pool_n = _cohort(db, p, as_of, set(patient_ids) if patient_ids is not None else None)
    finally:
        db.close()
    binary = p['outcome_type'] == 'binary'
    for r in rows:
        r['y_raw'] = r['y']
        if binary: r['y'] = float(r['y'] > 0)
    warnings, family = [], 'logistic' if binary else 'poisson'
    n_t = sum(r['treated'] for r in rows)
    base = {'schema': VERSION, 'parameters': p, 'as_of': as_of, 'assumptions': ASSUMPTIONS,
            'exposure': {'program': p['program'], 'definition': definition, **notes, 'treated_eligible': n_t, 'comparison_pool': pool_n, 'excluded': excluded},
            'outcome': {'measure': p['outcome'], 'type': p['outcome_type'], 'pre_days': p['pre_days'], 'post_days': p['post_days'],
                        'unit': 'probability of any event' if binary else 'events'}}
    if n_t < MIN_ARM or pool_n < MIN_ARM:
        w = f'Too few members for an uplift estimate (treated {n_t}, comparison {pool_n}; need >= {MIN_ARM} each).'
        return {**base, 'answer': {'headline': w, 'recommended_targeting': [], 'heterogeneity': 'not_assessable'}, 'warnings': [w],
                'inference_status': 'not_supported', 'inference': 'not_supported'}
    score = _baseline_risk(rows, p['folds'], p['l2'], p['seed'], binary)
    for r, s in zip(rows, score): r['baseline_risk'] = float(s)
    X = np.array([[r[c] for c in FEATURES] for r in rows]); t = np.array([r['treated'] for r in rows]); y = np.array([r['y'] for r in rows])
    learners = LEARNER_SETS.get(p['learners'], (p['learners'],))
    res = analyze(X, t, y, family, p['folds'], p['l2'], p['trim'], p['bootstrap'], p['seed'], learners, p['primary_learner'],
                  p['interactions'], 'decrease', FEATURES)
    sample = {k: res[k] for k in ('n', 'n_treated', 'n_control', 'kept', 'kept_treated', 'kept_control', 'trimmed_treated', 'trimmed_control')}
    if res['status'] != 'estimated':
        w = 'Too few treated or comparison members remain after overlap trimming for an uplift estimate.'
        return {**base, 'sample': sample, 'overlap': {'bounds': res['trim_bounds'], **res['propensity']},
                'answer': {'headline': w, 'recommended_targeting': [], 'heterogeneity': 'not_assessable'}, 'warnings': [w],
                'inference_status': 'not_supported', 'inference': 'not_supported'}
    if res['trimmed_treated'] + res['trimmed_control'] > 0.2 * res['n']:
        warnings.append(f"{res['trimmed_treated'] + res['trimmed_control']} of {res['n']} members fall outside the overlap bounds "
                        f"{res['trim_bounds']} and were trimmed; results describe the overlapping population only.")
    prim = res['primary']; ev = res['learners'][prim]
    het = 'detected' if ev['rate_autoc']['ci95'][0] > 0 else ('reversed' if ev['rate_autoc']['ci95'][1] < 0 else 'not_detected')
    if het != 'detected':
        warnings.append('RATE (AUTOC) interval covers 0 or is negative: no treatment-effect heterogeneity was detected; targeting by '
                        'predicted uplift is not shown to beat random targeting.')
    if int(res['kept_treated']) < 100: warnings.append(f"Only {res['kept_treated']} treated members after trimming: uplift estimates are noisy.")
    m = res['members']
    names = res['feature_names']
    # per-member output
    pb, ino = m['benefit'], m['in_overlap']
    value_event = p['value_per_event_usd']
    if value_event is None:
        a = outreach.ASSUMPTIONS
        n_ed = sum(r['post_ed'] for r in rows); n_ad = sum(r['post_adm'] for r in rows)
        if p['outcome'] == 'ed_visits': value_event = a['ed_visit_cost_usd']
        elif p['outcome'] == 'admissions': value_event = a['admission_cost_usd']
        else:
            value_event = (n_ed * a['ed_visit_cost_usd'] + n_ad * a['admission_cost_usd']) / max(n_ed + n_ad, 1) if (n_ed + n_ad) else a['ed_visit_cost_usd']
    value_event = float(value_event)

    def reasons(i):
        c = m['contrib'][i]
        out = []
        for j in np.argsort(-np.abs(c))[:5]:
            if abs(c[j]) < 1e-9: continue
            raw = rows[i][names[j]]
            out.append({'feature': names[j], 'value': raw, 'contribution': float(c[j]),
                        'direction': 'raises predicted benefit' if c[j] > 0 else 'lowers predicted benefit'})
            if len(out) == 3: break
        return out
    members = []
    for i, r in enumerate(rows):
        if not np.isfinite(pb[i]): continue
        members.append({'i': i, 'patient_id': r['pid'], 'treated': bool(r['treated']), 'predicted_uplift': float(pb[i]),
                        'baseline_rate': float(m['baseline_rate'][i]) if np.isfinite(m['baseline_rate'][i]) else None,
                        'propensity': float(m['propensity'][i]), 'in_overlap': bool(ino[i]), 'practice': r['practice']})
    # deciles (overlap members, primary learner)
    ordered = sorted((x for x in members if x['in_overlap']), key=lambda x: (-x['predicted_uplift'], x['patient_id']))
    segments = []
    for j, chunk in enumerate(np.array_split(np.arange(len(ordered)), 10), 1):
        grp = [ordered[k] for k in chunk]
        if not grp: continue
        gi = [x['i'] for x in grp]
        yy, tt = y[gi], t[gi]
        segments.append({'decile': j, 'members': len(grp), 'mean_predicted_uplift': float(np.mean([x['predicted_uplift'] for x in grp])),
                         'observed_rate_treated': float(yy[tt == 1].mean()) if (tt == 1).any() else None,
                         'observed_rate_control': float(yy[tt == 0].mean()) if (tt == 0).any() else None,
                         'mean_age': float(np.mean([rows[k]['age'] for k in gi])), 'mean_prior_utilization': float(np.mean([rows[k]['prior_ed'] + rows[k]['prior_admissions'] for k in gi])),
                         'mean_chronic_conditions': float(np.mean([rows[k]['chronic_conditions'] for k in gi]))})
    # targeting
    cap_n = p['capacity_patients']
    cap_min = int(round(p['capacity_hours'] * 60)) if p['capacity_hours'] is not None else None
    slots = min(x for x in (cap_n, (cap_min // p['minutes_per_contact']) if cap_min is not None else None) if x is not None)
    elig = [x for x in ordered if (p['target_population'] == 'all' or not x['treated'])]
    for x in elig: x['expected_value_usd'] = x['predicted_uplift'] * value_event - p['cost_per_contact_usd']
    pos = sorted((x for x in elig if x['expected_value_usd'] > 0), key=lambda x: (-x['expected_value_usd'], x['patient_id']))
    chosen = pos[:slots]
    worklist = []
    for rank, x in enumerate(chosen, 1):
        rs = reasons(x['i'])
        worklist.append({'priority': rank, 'patient_id': x['patient_id'], 'predicted_uplift': x['predicted_uplift'],
                         'baseline_rate': x['baseline_rate'], 'expected_value_usd': x['expected_value_usd'],
                         'minutes': p['minutes_per_contact'], 'practice': x['practice'], 'reasons': rs,
                         'reason_detail': '; '.join(f"{r_['feature']}={r_['value']:g} {r_['direction']}" for r_ in rs)})
    tot_events = sum(w['predicted_uplift'] for w in worklist); tot_val = sum(w['expected_value_usd'] for w in worklist)
    rand_events = float(np.mean([x['predicted_uplift'] for x in elig])) * len(chosen) if elig else 0.0
    unit_ev = 'fewer members with an event' if binary else 'fewer events'
    ra = ev['rate_autoc']
    headline = (f"Among {res['kept']} overlap members ({res['kept_treated']} treated), average benefit of {p['program'].replace('_', ' ')} on {p['outcome'].replace('_', ' ')} "
                f"in {p['post_days']} days is {res['ate_benefit']['estimate']:+.4f} (95% CI {res['ate_benefit']['ci95'][0]:+.4f} to {res['ate_benefit']['ci95'][1]:+.4f}) per member. "
                + (f"{prim.upper()}-learner ranking concentrates benefit: RATE AUTOC {ra['estimate']:+.4f} (95% CI {ra['ci95'][0]:+.4f} to {ra['ci95'][1]:+.4f}); "
                   f"top decile observed benefit {ev['calibration'][0]['observed_benefit']:+.4f} vs bottom {ev['calibration'][-1]['observed_benefit']:+.4f}."
                   if het == 'detected' else
                   f"No reliable heterogeneity: {prim.upper()}-learner RATE AUTOC {ra['estimate']:+.4f} (95% CI {ra['ci95'][0]:+.4f} to {ra['ci95'][1]:+.4f}) does not exclude 0.")
                + (f" Recommended {len(worklist)} of {len(elig)} eligible members: {tot_events:.2f} {unit_ev} expected (about ${tot_val:,.0f} at ${value_event:,.0f} per event) "
                   f"vs {rand_events:.2f} for random selection of the same size." if worklist else ' No eligible member has a positive expected net value.'))
    method = (f"Cohort: {definition}. Pre window {p['pre_days']}d, outcome window {p['post_days']}d after the index date. Propensity: cross-fitted ridge logistic "
              f"(l2 {p['l2']:g}), overlap bounds [{p['trim']:g}, {1 - p['trim']:g}]. Learners ({', '.join(l.upper() for l in learners)}) with {p['folds']}-fold cross-fitting, "
              f"ridge {'logistic' if binary else 'Poisson'} base models (IRLS, numpy) for T/X; DML (R-learner on gradient-boosted residuals) and an honest causal forest (GRF-style, leaf effects re-estimated on held-out halves) for DML/CAUSAL_FOREST. Evaluation on held-out predictions (residual-on-residual scores for ranking metrics, doubly robust ATE): RATE AUTOC and Qini with "
              f"{p['bootstrap']}-draw bootstrap CIs, decile calibration. Reasons = largest contributions of a linear surrogate of the predicted uplift "
              f"(R2 {res['surrogate']['r2'] if res['surrogate']['r2'] is None else round(res['surrogate']['r2'], 3)}). Targeting = positive uplift x value per event, top by net value within capacity.")
    result = {**base, 'sample': sample,
              'overlap': {'bounds': res['trim_bounds'], **res['propensity']},
              'ate': res['ate_benefit'],
              'learners': {l: {k: v for k, v in e.items()} for l, e in res['learners'].items()},
              'primary_learner': prim,
              **({'plr_projection': res['plr_projection_benefit']} if 'plr_projection_benefit' in res else {}),
              **({'causal_forest_importance': res['causal_forest_importance']} if 'causal_forest_importance' in res else {}),
              'segments': segments,
              'members': [{k: v for k, v in x.items() if k != 'i'} for x in sorted(ordered, key=lambda x: (-x['predicted_uplift'], x['patient_id']))][:p['top']],
              'targeting': {'capacity': {'patients': cap_n, 'minutes': cap_min, 'minutes_per_contact': p['minutes_per_contact'], 'slots': slots},
                            'target_population': p['target_population'], 'eligible': len(elig), 'selected': len(worklist),
                            'expected_events_averted': tot_events, 'expected_value_usd': tot_val,
                            'random_selection_expected_events_averted': rand_events, 'value_per_event_usd': value_event,
                            'cost_per_contact_usd': p['cost_per_contact_usd'], 'optimality': 'exact (additive values, cardinality/hours capacity)'},
              'surrogate': res['surrogate'],
              'answer': {'headline': headline, 'heterogeneity': het, 'rate_autoc': ra, 'qini': ev['qini'], 'recommended_targeting': worklist},
              'warnings': warnings, 'inference_status': 'estimated_under_assumptions' if het == 'detected' else 'limited',
              'inference': 'estimated_under_assumptions' if het == 'detected' else 'limited',
              'method': method, 'headline': headline,
              'limitations': ['Observational design: uplift is an estimated effect under stated assumptions, not an identified causal effect.',
                              'Value per event is a planning assumption; the worklist ranks members for review and does not authorize clinical action.',
                              'Linear base models: heterogeneity not captured by the covariates (or by their interactions, if enabled) cannot be found.']}
    return _round(result)
