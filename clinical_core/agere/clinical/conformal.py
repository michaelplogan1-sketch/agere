"""Distribution-free calibration for Agere's predictions (pure numpy).

Model-based intervals (Kalman forecast SDs, GLM expectations) assume the model is right. Conformal
prediction instead uses held-out residuals: if calibration and test cases are exchangeable, a split
conformal interval at level 1 - alpha covers the truth with probability >= 1 - alpha in finite samples
(Vovk et al. 2005; Lei et al. 2018). Implemented here:

  - split conformal on absolute or normalized residuals (score = |y - yhat| / scale);
  - jackknife+ and CV+ for small samples (Barber et al. 2021; coverage >= 1 - 2 alpha);
  - conformalized quantile regression correction (Romano, Patterson, Candes 2019);
  - Mondrian (group-conditional) conformal: one quantile per group, pooled fallback for small groups
    (Vovk 2012) - coverage holds within each group with enough calibration cases;
  - adaptive conformal inference for sequential forecasts (Gibbs & Candes 2021): alpha_t is nudged
    after every miss/hit so long-run coverage tracks the target under drift;
  - probability calibration: isotonic regression (PAV) and inductive Venn-Abers (Vovk & Petej 2014),
    which outputs a [p0, p1] range and a merged probability;
  - conformal risk control (Angelopoulos et al. 2022) for a flag threshold bounding the expected miss rate;
  - diagnostics: coverage, Brier score, expected calibration error, reliability table.

Agere calibrates on its own data by time-splitting (predict at a past origin, observe the next window):
the interval shipped with a live prediction is calibrated on the most recent complete window, and the
reported backtested coverage comes from calibrating one window earlier and scoring the next one, so
no reported number uses data from after the moment it would have been issued.
"""
import math

import numpy as np

VERSION = 'conformal/1'


# ---- regression intervals --------------------------------------------------------------------
def conformal_quantile(scores, alpha):
    """The ceil((n+1)(1-alpha))-th smallest score; inf when n is too small for that level."""
    s = np.sort(np.asarray(scores, dtype=float))
    n = len(s)
    if n == 0: return math.inf
    k = math.ceil((n + 1) * (1 - alpha))
    return float(s[k - 1]) if k <= n else math.inf


def scores(y, pred, scale=None):
    y, pred = np.asarray(y, dtype=float), np.asarray(pred, dtype=float)
    s = np.abs(y - pred)
    return s / np.maximum(np.asarray(scale, dtype=float), 1e-12) if scale is not None else s


def split_conformal(y_cal, pred_cal, alpha=0.1, scale_cal=None):
    """Calibrate once; returns {'q', 'n', 'alpha'}. Interval for a new case: pred +- q x scale."""
    return {'q': conformal_quantile(scores(y_cal, pred_cal, scale_cal), alpha), 'n': len(y_cal), 'alpha': alpha}


def interval(pred, q, scale=None, lower=None, upper=None):
    pred = np.asarray(pred, dtype=float)
    half = q * (np.asarray(scale, dtype=float) if scale is not None else 1.0)
    lo, hi = pred - half, pred + half
    if lower is not None: lo = np.maximum(lo, lower)
    if upper is not None: hi = np.minimum(hi, upper)
    return lo, hi


def coverage(y, lo, hi):
    y, lo, hi = (np.asarray(a, dtype=float) for a in (y, lo, hi))
    return float(np.mean((y >= lo) & (y <= hi))) if len(y) else None


def _plus_interval(loo_pred_new, loo_resid, alpha):
    """Jackknife+/CV+ bounds from per-calibration-point leave-out predictions at the new point."""
    lo_scores = loo_pred_new - loo_resid
    hi_scores = loo_pred_new + loo_resid
    n = len(loo_resid)
    k = math.ceil((1 - alpha) * (n + 1))
    if k > n: return -math.inf, math.inf
    return float(-np.sort(-lo_scores)[k - 1]), float(np.sort(hi_scores)[k - 1])


def cv_plus(X, y, fit, predict, X_new, alpha=0.1, folds=10, seed=0):
    """CV+ (folds < n) or jackknife+ (folds = n). fit(X, y) -> model; predict(model, X) -> array.
    Coverage >= 1 - 2 alpha in finite samples for any symmetric fitting algorithm."""
    X, y, X_new = np.asarray(X, dtype=float), np.asarray(y, dtype=float), np.asarray(X_new, dtype=float)
    n = len(y)
    folds = min(folds, n)
    idx = np.random.default_rng(seed).permutation(n)
    assign = np.empty(n, dtype=int)
    for f, chunk in enumerate(np.array_split(idx, folds)): assign[chunk] = f
    resid, pred_new = np.empty(n), np.empty((n, len(X_new)))
    for f in range(folds):
        test = assign == f
        model = fit(X[~test], y[~test])
        resid[test] = np.abs(y[test] - predict(model, X[test]))
        pred_new[test] = predict(model, X_new)
    bounds = [_plus_interval(pred_new[:, j], resid, alpha) for j in range(len(X_new))]
    return np.array([b[0] for b in bounds]), np.array([b[1] for b in bounds])


def jackknife_plus(X, y, fit, predict, X_new, alpha=0.1):
    return cv_plus(X, y, fit, predict, X_new, alpha, folds=len(y))


def cqr(y_cal, lo_cal, hi_cal, alpha=0.1):
    """Conformalized quantile regression: correction q added to both quantile-regression bounds."""
    s = np.maximum(np.asarray(lo_cal, dtype=float) - y_cal, np.asarray(y_cal, dtype=float) - hi_cal)
    return conformal_quantile(s, alpha)


def mondrian(score_values, groups, alpha=0.1, min_group=30):
    """Group-conditional quantiles. Groups with fewer than min_group calibration cases (or too few for
    the level) use the pooled quantile and are marked pooled."""
    score_values, groups = np.asarray(score_values, dtype=float), np.asarray(groups, dtype=object)
    pooled = conformal_quantile(score_values, alpha)
    out = {}
    for g in sorted(set(groups.tolist()), key=str):
        s = score_values[groups == g]
        q = conformal_quantile(s, alpha)
        own = len(s) >= min_group and math.isfinite(q)
        out[g] = {'q': q if own else pooled, 'n': int(len(s)), 'pooled': not own}
    return {'groups': out, 'pooled_q': pooled, 'n': int(len(score_values)), 'alpha': alpha}


def mondrian_q(table, group):
    g = table['groups'].get(group)
    return g['q'] if g else table['pooled_q']


def aci(y, pred, scale=None, alpha=0.1, gamma=0.01, warmup=30, window=None):
    """Adaptive conformal inference over a time-ordered stream. At step t the interval uses the
    (1 - alpha_t) quantile of past scores; alpha_{t+1} = alpha_t + gamma (alpha - miss_t).
    Returns per-step bounds (nan during warm-up), the alpha_t path and realised coverage."""
    s = scores(y, pred, scale)
    sc = np.ones(len(s)) if scale is None else np.asarray(scale, dtype=float)
    pred = np.asarray(pred, dtype=float)
    a_t, lo, hi, alphas, hits = alpha, np.full(len(s), np.nan), np.full(len(s), np.nan), [], []
    for t in range(len(s)):
        past = s[max(0, t - window):t] if window else s[:t]
        if t >= warmup:
            level = min(max(1 - a_t, 0.0), 1.0)
            q = math.inf if level >= 1 else (0.0 if level <= 0 else float(np.quantile(past, level, method='higher')))
            lo[t], hi[t] = pred[t] - q * sc[t], pred[t] + q * sc[t]
            miss = float(not (s[t] <= q))
            hits.append(1 - miss)
            a_t = a_t + gamma * (alpha - miss)
        alphas.append(a_t)
    return {'lo': lo, 'hi': hi, 'alpha_path': np.array(alphas), 'coverage': float(np.mean(hits)) if hits else None,
            'steps': len(hits), 'target': 1 - alpha}


# ---- probability calibration -------------------------------------------------------------------
def _pav(x, y, w=None):
    """Isotonic (non-decreasing) least-squares fit. Returns (sorted unique x, fitted values)."""
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    w = np.ones(len(x)) if w is None else np.asarray(w, dtype=float)
    order = np.argsort(x, kind='mergesort')
    x, y, w = x[order], y[order], w[order]
    ux, inv = np.unique(x, return_inverse=True)
    wy, ww = np.bincount(inv, weights=w * y), np.bincount(inv, weights=w)
    vals, wts, sizes = [], [], []
    for v, wt in zip(wy / ww, ww):
        vals.append(v); wts.append(wt); sizes.append(1)
        while len(vals) > 1 and vals[-2] > vals[-1]:
            v2, w2, s2 = vals.pop(), wts.pop(), sizes.pop()
            vals[-1] = (vals[-1] * wts[-1] + v2 * w2) / (wts[-1] + w2); wts[-1] += w2; sizes[-1] += s2
    return ux, np.repeat(vals, sizes)


class Isotonic:
    """Isotonic calibration map fitted on (score, label); step interpolation, clipped at the ends."""
    def __init__(self, p, labels):
        self.x, self.f = _pav(p, labels)

    def __call__(self, p):
        p = np.asarray(p, dtype=float)
        if not len(self.x): return p
        i = np.clip(np.searchsorted(self.x, p, side='right') - 1, 0, len(self.x) - 1)
        return self.f[i]


def venn_abers(cal_p, cal_y, test_p):
    """Inductive Venn-Abers: for each test score fit isotonic twice, with the test point labelled 0 and 1.
    Returns (p0, p1, merged = p1 / (1 - p0 + p1)). [p0, p1] is a multiprobability range that is
    calibrated in the Venn sense under exchangeability."""
    cal_p, cal_y = np.asarray(cal_p, dtype=float), np.asarray(cal_y, dtype=float)
    test_p = np.asarray(test_p, dtype=float)
    cache, p0, p1 = {}, np.empty(len(test_p)), np.empty(len(test_p))
    for j, t in enumerate(test_p):
        key = round(float(t), 4)
        if key not in cache:
            vals = []
            for label in (0.0, 1.0):
                ux, f = _pav(np.append(cal_p, key), np.append(cal_y, label))
                vals.append(float(f[np.searchsorted(ux, key)]))
            cache[key] = vals
        p0[j], p1[j] = cache[key]
    merged = p1 / np.maximum(1 - p0 + p1, 1e-12)
    return p0, p1, merged


def risk_control_threshold(cal_p, cal_y, alpha=0.1, grid=None):
    """Conformal risk control for 'flag when p >= lambda': the largest lambda whose adjusted miss rate
    (n/(n+1)) FNR_n(lambda) + 1/(n+1) is <= alpha, so E[share of true crossers not flagged] <= alpha.
    Returns None when there are no positives to control."""
    cal_p, cal_y = np.asarray(cal_p, dtype=float), np.asarray(cal_y, dtype=bool)
    pos = cal_p[cal_y]
    n = len(pos)
    if n == 0: return None
    grid = np.unique(np.concatenate([[0.0], pos])) if grid is None else np.asarray(grid)
    best = 0.0
    for lam in np.sort(grid):
        fnr = float(np.mean(pos < lam))
        if n / (n + 1) * fnr + 1 / (n + 1) <= alpha: best = float(lam)
        else: break
    return best


def binomial_cdf(k, n, p):
    """P(Bin(n, p) <= k), exact (log-space sum; n is a calibration-set size, so this is cheap)."""
    if n <= 0 or k >= n: return 1.0
    if k < 0: return 0.0
    if p <= 0: return 1.0
    if p >= 1: return 0.0
    lp, lq = math.log(p), math.log1p(-p)
    terms = [math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * lp + (n - i) * lq for i in range(k + 1)]
    m = max(terms)
    return min(1.0, math.exp(m) * sum(math.exp(t - m) for t in terms))


def ltt_threshold(score_values, errors, alpha=0.02, delta=0.1, grid=None):
    """Learn-then-Test (Angelopoulos, Bates, Candes, Jordan & Lei 2021) for an 'accept when score >= lambda' gate.

    For each candidate lambda, H0(lambda): the error rate among accepted cases exceeds alpha. Its p-value is the exact
    binomial tail P(Bin(n_lambda, alpha) <= k_lambda), with n_lambda accepted calibration cases of which k_lambda were wrong
    (valid conditionally on the calibration scores, given exchangeability).
    Hypotheses are tested in a fixed sequence from the strictest testable lambda down (fixed-sequence testing controls the
    family-wise error at delta), stopping at the first that is not rejected. The returned lambda is the most permissive
    certified one: with probability >= 1 - delta over the calibration draw, accepted cases at that lambda are wrong at a
    rate <= alpha, provided future cases are exchangeable with the calibration cases.
    Returns a dict; 'threshold' is None when no lambda can be certified (too little data or too many errors)."""
    s = np.asarray(score_values, dtype=float)
    e = np.asarray(errors, dtype=bool)
    if len(s) != len(e): raise ValueError('scores and errors must align')
    grid = np.unique(s) if grid is None else np.unique(np.asarray(grid, dtype=float))
    out = {'alpha': alpha, 'delta': delta, 'n_calibration': int(len(s)), 'threshold': None, 'tested': 0}
    for lam in sorted(grid, reverse=True):
        accepted = s >= lam
        n, k = int(accepted.sum()), int(e[accepted].sum())
        # A lambda that accepts too few cases to reject even with zero errors is not tested. Which lambdas those are depends
        # only on the scores, not the labels, so (conditioning on the scores) the test sequence stays fixed in advance.
        if n == 0 or binomial_cdf(0, n, alpha) > delta: continue
        p_value = binomial_cdf(k, n, alpha)
        out['tested'] += 1
        if p_value > delta: break
        out.update(threshold=float(lam), n_accepted=n, errors_accepted=k, coverage=round(n / len(s), 4), p_value=p_value,
                   empirical_error=round(k / n, 4))
    if out['threshold'] is None:
        out['min_accepted_for_zero_errors'] = math.ceil(math.log(delta) / math.log1p(-alpha))
    return out


def brier(p, y):
    p, y = np.asarray(p, dtype=float), np.asarray(y, dtype=float)
    return float(np.mean((p - y) ** 2)) if len(p) else None


def reliability(p, y, bins=10):
    """Equal-width reliability table: per bin, cases, mean predicted, observed rate."""
    p, y = np.asarray(p, dtype=float), np.asarray(y, dtype=float)
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    rows = []
    for b in range(bins):
        m = idx == b
        if m.any():
            rows.append({'bin': f'{edges[b]:.1f}-{edges[b + 1]:.1f}', 'cases': int(m.sum()),
                         'mean_predicted': round(float(p[m].mean()), 4), 'observed_rate': round(float(y[m].mean()), 4)})
    return rows


def ece(p, y, bins=10):
    p = np.asarray(p, dtype=float)
    n = len(p)
    if not n: return None
    return float(sum(r['cases'] / n * abs(r['mean_predicted'] - r['observed_rate']) for r in reliability(p, y, bins)))


def describe_coverage(level, achieved, n):
    if achieved is None or not n: return f'{level:.0%} intervals: no held-out values to backtest'
    return f'{level:.0%} intervals covered {achieved:.1%} of {n} held-out values'
