"""Baseline (do-nothing) model: per-patient monthly admission and ED rates and expected monthly spending, fit on the
workspace's own history, time-split calibrated.

Design. A person is a row at an origin date o: features from the lookback [o - L months, o) and outcomes over the window
[o, o + W months), with the enrolled member-months in the window as exposure. Three log-linear models with an exposure
offset: admissions (Poisson), ED visits (Poisson) and other spending (quasi-Poisson on dollars not attached to an admission
or ED visit). Spending of an admission or ED visit comes from the workspace's own event costs (data.History), so total
expected cost = other + rate_adm x cost per admission + rate_ed x cost per ED visit. Fitting is ridge-penalized IRLS (the
same estimator as clinical/risk_outcomes.fit_glm, here with an exposure offset) and the Laplace covariance (times the
Pearson dispersion) gives coefficient draws for the Monte Carlo.

Calibration (nothing uses data after the moment it would have been issued): the model fit on windows ending by
o2 = as_of - W is scored on the held-out window starting at o2 (observed / expected, decile reliability, C-statistic, Brier score and
expected calibration error from clinical/conformal.py, conformal coverage of per-patient spending intervals, and whether
the observed population and cohort totals fall inside the simulated do-nothing 90% interval). The live model is then refit on
windows ending by as_of and predicts from features at as_of. The population's held-out O/E becomes the drift term of the
Monte Carlo (a mean-one calendar shift whose size is the shift seen out of sample, never extrapolated as a level), the
cohort's O/E relative to the population's is its group-conditional recalibration (shrunk toward 1), and the held-out
spending ratios y / yhat are the residual distribution it samples.
"""
from __future__ import annotations

import math

import numpy as np

from ..clinical import conformal
from ..clinical.risk_outcomes import c_statistic
from .data import add_months, day

STEP_MONTHS = 3       # spacing of stacked fit origins
MAX_ORIGINS = 4
PRIOR = 10.0          # Gamma(PRIOR, PRIOR) prior on a cohort's held-out O/E for event counts
PRIOR_COST_SD = 0.25  # normal prior sd on a cohort's log O/E for other spending
FEATURES = ('intercept', 'age_per_10', 'age_75_plus', 'male', 'medicare', 'medicaid', 'dual', 'log_prior_adm_rate',
            'log_prior_ed_rate', 'log_prior_other_pm', 'log_prior_event_pm', 'chronic_categories', 'heart_failure',
            'admitted_last_90d')


def windows(h, as_of, horizon, lookback):
    """(L, W): lookback and fit-window months that fit in the history before as_of (L + 2W <= available months)."""
    T = max(0, int((as_of - (h.data_start or as_of)) / 30.44))
    L, W = int(lookback), 12  # monthly rates are fit on 12-month windows whatever the horizon (seasonality averages out)
    if L + 2 * W > T:
        W = (T - L) // 2
        if W < 6:
            L = max(3, min(L, T - 12))
            W = max(1, (T - L) // 2)
    if W < 1 or L < 1:
        raise ValueError(f'Not enough history before {as_of_iso(as_of)} to fit and calibrate the baseline '
                         f'({T} months of data; need at least 5)')
    return L, W


def as_of_iso(d):
    from datetime import date
    return date.fromordinal(int(d)).isoformat()


def features(h, pid, o, L):
    a = add_months(o, -L)
    adm, ed, ecost, ocost = h.window(pid, a, o)
    e_pre = max(1, h.enrolled_months(pid, a, o))
    age = (o - h.birth[pid]) / 365.25 if pid in h.birth else 60.0
    lob = h.lob_at(pid, o)
    chronic = len({c for d, c in h.chronic.get(pid, ()) if d < o})
    recent = any(kind == 'admission' and o - 90 <= d < o for d, kind, _, _ in h.events.get(pid, ()))
    return [1.0, age / 10.0, float(age >= 75), float(h.sex.get(pid) == 'M'), float(lob == 'medicare'), float(lob == 'medicaid'),
            float(lob == 'dual'), math.log1p(adm * 12.0 / e_pre), math.log1p(ed * 12.0 / e_pre), math.log1p(max(ocost, 0.0) / e_pre / 100.0),
            math.log1p(max(ecost, 0.0) / e_pre / 100.0), min(chronic, 12) / 4.0, float(pid in h.hf_dx and h.hf_dx[pid] < o), float(recent)]


def eligible(h, pid, o):
    return h.enrolled_at(pid, o) and not (pid in h.death and h.death[pid] < o)


def panel(h, o, L, W, ids=None):
    """Rows at origin o: (pids, X, exposure months, admissions, ed, other cost, event cost)."""
    end = add_months(o, W)
    pids, X, ex, ya, ye, yc, ycost = [], [], [], [], [], [], []
    for pid in sorted(ids if ids is not None else h.months):
        if not eligible(h, pid, o):
            continue
        e = h.enrolled_months(pid, o, end)
        if e <= 0:
            continue
        adm, ed, ecost, ocost = h.window(pid, o, end)
        pids.append(pid)
        X.append(features(h, pid, o, L))
        ex.append(float(e)); ya.append(adm); ye.append(ed); yc.append(max(ocost, 0.0)); ycost.append(max(ecost, 0.0))
    return pids, np.array(X, float).reshape(-1, len(FEATURES)), np.array(ex), np.array(ya, float), np.array(ye, float), \
        np.array(yc, float), np.array(ycost, float)


def fit_poisson(X, y, offset, ridge=1.0, max_iter=80, tol=1e-8):
    """Ridge IRLS for a log-linear model with an offset (risk_outcomes.fit_glm plus offset). -> (beta, cov, dispersion)."""
    n, k = X.shape
    beta = np.zeros(k)
    beta[0] = math.log(max(y.sum(), 1e-6) / max(np.exp(offset).sum(), 1e-9))
    pen = np.full(k, ridge)
    pen[0] = 0.0
    for _ in range(max_iter):
        mu = np.exp(np.clip(X @ beta + offset, -30, 30))
        grad = X.T @ (y - mu) - pen * beta
        H = (X * np.maximum(mu, 1e-10)[:, None]).T @ X + np.diag(pen) + 1e-9 * np.eye(k)
        step = np.linalg.solve(H, grad)
        beta = beta + step
        if np.max(np.abs(step)) < tol:
            break
    mu = np.exp(np.clip(X @ beta + offset, -30, 30))
    H = (X * np.maximum(mu, 1e-10)[:, None]).T @ X + np.diag(pen) + 1e-9 * np.eye(k)
    phi = max(1.0, float(np.sum((y - mu) ** 2 / np.maximum(mu, 1e-9)) / max(n - k, 1)))
    return beta, np.linalg.inv(H) * phi, phi


def origins(h, cutoff, L, W, step=STEP_MONTHS, most=MAX_ORIGINS):
    """Fit origins whose window ends by cutoff and whose lookback starts at or after the data start (latest first)."""
    out, o = [], add_months(cutoff, -W)
    first = add_months(h.data_start, L) if h.data_start else o
    while o >= first - 1 and len(out) < most:
        out.append(o)
        o = add_months(o, -step)
    return out


class Model:
    """Three fitted models on person-windows stacked over several origins (every STEP_MONTHS, windows ending by
    cutoff), so one unusual calendar window does not set the level. The Laplace covariance is inflated by the number
    of origins (the same person appears once per origin)."""

    def __init__(self, h, cutoff, L, W, ids=None):
        self.cutoff, self.L, self.W = cutoff, L, W
        self.origins = origins(h, cutoff, L, W) or [add_months(cutoff, -W)]
        parts = [panel(h, o, L, W, ids) for o in self.origins]
        pids = [p for part in parts for p in part[0]]
        if len(pids) < 30:
            raise ValueError(f'Only {len(pids)} enrolled person-windows before {as_of_iso(cutoff)}; the baseline needs at least 30')
        X, ex, ya, ye, yc = (np.concatenate([part[j] for part in parts]) for j in (1, 2, 3, 4, 5))
        off = np.log(ex)
        self.n = len(set(pids))
        self.rows = len(pids)
        k = float(len(self.origins))
        self.adm, self.ed, self.cost = (_inflate(fit_poisson(X, y, off), k) for y in (ya, ye, yc / 100.0))  # cost in $100 units
        self.events = {'admissions': float(ya.sum()), 'ed_visits': float(ye.sum()), 'member_months': float(ex.sum())}

    def rates(self, X):
        """monthly admission rate, ED rate, other spending ($/month)."""
        return (np.exp(X @ self.adm[0]), np.exp(X @ self.ed[0]), np.exp(X @ self.cost[0]) * 100.0)

    def draws(self, X, n, rng):
        """n coefficient draws -> arrays (n, patients) of monthly rates."""
        out = []
        for beta, cov, _ in (self.adm, self.ed, self.cost):
            try:
                B = rng.multivariate_normal(beta, cov, size=n, method='cholesky')
            except np.linalg.LinAlgError:
                B = rng.multivariate_normal(beta, cov, size=n, method='eigh')
            out.append(np.exp(np.clip(B @ X.T, -30, 30)))
        out[2] = out[2] * 100.0
        return out

    def coefficients(self):
        return {name: {f: round(float(b), 4) for f, b in zip(FEATURES, m[0])} | {'dispersion': round(m[2], 3)}
                for name, m in (('admissions', self.adm), ('ed_visits', self.ed), ('other_spending', self.cost))}


def _inflate(fit, k):
    beta, cov, phi = fit
    return beta, cov * k, phi


def event_costs(h, ids, a, b):
    """{'admission': [...], 'ed': [...]} costs of events dated [a, b) for ids."""
    out = {'admission': [], 'ed': []}
    for pid in ids:
        for d, kind, cost, _ in h.events.get(pid, ()):
            if a <= d < b:
                out[kind].append(cost)
    return out


def _r(v, n=4):
    return None if v is None or (isinstance(v, float) and not math.isfinite(v)) else round(float(v), n)


def calibrate(h, as_of, L, W, cohort=None, rng=None, cost_tables=None, anchors=None):
    """Fit on windows ending by o2, score on the held-out window from o2 (see module doc). -> (report, drift sds, spending residual ratios)."""
    rng = rng or np.random.default_rng(0)
    o2 = add_months(as_of, -W)
    m1 = Model(h, o2, L, W)
    pids, X, ex, ya, ye, yc, ycost = panel(h, o2, L, W)
    if anchors:  # the events that put a patient in the cohort (e.g. the index ED visit) are selection, not outcome
        ya, ye, ycost = ya.copy(), ye.copy(), ycost.copy()
        pos = {p: i for i, p in enumerate(pids)}
        for pid, days in anchors.items():
            i = pos.get(pid)
            if i is None:
                continue
            for d, kind, cost, _ in h.events.get(pid, ()):
                if d in days and o2 <= d < as_of:
                    if kind == 'admission':
                        ya[i] -= 1
                    else:
                        ye[i] -= 1
                    ycost[i] -= cost
        ycost = np.maximum(ycost, 0.0)
    la, le, lc = m1.rates(X)
    ea, ee, ec = la * ex, le * ex, lc * ex
    cost_tables = cost_tables or {}
    ca = float(np.mean(cost_tables.get('admission') or [0.0]))
    ce = float(np.mean(cost_tables.get('ed') or [0.0]))
    total_pred = ec + ea * ca + ee * ce
    total_obs = yc + ycost
    rep = {'fit_origins': [as_of_iso(o) for o in m1.origins], 'heldout_origin': as_of_iso(o2), 'heldout_end': as_of_iso(as_of), 'lookback_months': L,
           'window_months': W, 'fit_patients': m1.n, 'heldout_patients': len(pids), 'outcomes': {}}
    drift = {}
    for name, y, e in (('admissions', ya, ea), ('ed_visits', ye, ee)):
        O, E = float(y.sum()), float(e.sum())
        p_any = 1 - np.exp(-e)
        any_ = (y > 0).astype(int)
        order = np.argsort(e)
        dec = []
        for part in np.array_split(order, 10):
            if len(part):
                dec.append({'predicted': _r(e[part].sum(), 2), 'observed': float(y[part].sum()), 'patients': int(len(part))})
        rep['outcomes'][name] = {'observed': O, 'expected': _r(E, 2), 'o_e': _r(O / E if E else None),
                                 'c_statistic_any': _r(c_statistic(any_, p_any)), 'brier_any': _r(conformal.brier(p_any, any_)),
                                 'ece_any': _r(conformal.ece(p_any, any_)), 'deciles': dec}
        drift[name] = math.sqrt(math.log(max(O, 0.5) / max(E, 1e-9)) ** 2 + 1.0 / max(O, 1.0))
    ratios = np.where(total_pred > 0, total_obs / np.maximum(total_pred, 1e-9), 1.0)
    other_ratios = np.where(ec > 0, yc / np.maximum(ec, 1e-9), 1.0)
    O, E = float(total_obs.sum()), float(total_pred.sum())
    cv = float(np.std(ratios) / max(np.mean(ratios), 1e-9))
    drift['cost'] = math.sqrt(math.log(max(O, 1.0) / max(E, 1e-9)) ** 2 + (cv ** 2) / max(len(ratios), 1))
    # Split-conformal per-patient spending intervals: calibrate on half the held-out patients, score the other half.
    idx = rng.permutation(len(pids))
    half = len(idx) // 2
    q = conformal.split_conformal(total_obs[idx[:half]], total_pred[idx[:half]], alpha=0.1,
                                  scale_cal=np.maximum(total_pred[idx[:half]], 1.0)) if half >= 20 else None
    cov = None
    if q is not None and math.isfinite(q['q']):
        lo, hi = conformal.interval(total_pred[idx[half:]], q['q'], scale=np.maximum(total_pred[idx[half:]], 1.0), lower=0.0)
        cov = conformal.coverage(total_obs[idx[half:]], lo, hi)
    rep['outcomes']['total_cost'] = {'observed': _r(O, 2), 'expected': _r(E, 2), 'o_e': _r(O / E if E else None),
                                     'per_patient_90pct_conformal_coverage': _r(cov), 'dispersion_cv_of_ratio': _r(cv)}
    rep['drift_sd'] = {k: _r(v) for k, v in drift.items()}
    # Group-conditional (Mondrian) recalibration of the cohort: held-out O/E with a Gamma(10, 10) prior centred on 1
    # (worth ten expected events), so a small cohort is pulled toward the population model.
    recal = None
    if cohort:
        cs = set(cohort)
        m = np.array([p in cs for p in pids])
        if m.any():
            recal = {}
            for name, y, e in (('admissions', ya, ea), ('ed_visits', ye, ee)):
                O, E = float(y[m].sum()), float(e[m].sum())
                pop = float(y.sum()) / max(float(e.sum()), 1e-9)   # calendar shift of the whole population: drift, not cohort
                Er = E * pop
                recal[name] = {'observed': O, 'expected': _r(E, 2), 'expected_at_population_o_e': _r(Er, 2), 'shape': PRIOR + O,
                               'rate': PRIOR + Er, 'factor': _r((PRIOR + O) / (PRIOR + Er))}
            O, E = float(yc[m].sum()), float(ec[m].sum()) * float(yc.sum()) / max(float(ec.sum()), 1e-9)
            r_c = other_ratios[m]
            cv_c = float(np.std(r_c) / max(np.mean(r_c), 1e-9)) if len(r_c) > 1 else 1.0
            se = cv_c / math.sqrt(max(m.sum(), 1))
            w = (1 / se ** 2) / (1 / se ** 2 + 1 / PRIOR_COST_SD ** 2)  # precision-weighted shrinkage of log O/E toward 0
            mu = w * math.log(max(O, 1.0) / max(E, 1e-9))
            recal['other_cost'] = {'observed': _r(O, 2), 'expected': _r(E, 2), 'log_mean': _r(mu, 5),
                                   'log_sd': _r(math.sqrt(w) * se, 5), 'factor': _r(math.exp(mu))}
            recal['patients'] = int(m.sum())
    rep['cohort_recalibration'] = recal
    # Do-nothing interval backtest on the held-out window (the same Monte Carlo machinery, no drift term).
    rep['interval_backtest'] = _interval_backtest(m1, X, ex, ya, ye, total_obs, other_ratios, cost_tables, pids, cohort, rng)
    return rep, drift, other_ratios, recal


def _interval_backtest(m1, X, ex, ya, ye, total_obs, ratios, cost_tables, pids, cohort, rng, n=400):
    ra, re_, rc = m1.draws(X, n, rng)
    na = rng.poisson(ra * ex)
    ne = rng.poisson(re_ * ex)
    ca = np.asarray(cost_tables.get('admission') or [0.0])
    ce = np.asarray(cost_tables.get('ed') or [0.0])
    other = rc * ex * rng.choice(ratios, size=rc.shape)
    out = {}
    groups = {'population': np.ones(len(pids), bool)}
    if cohort:
        cs = set(cohort)
        groups['cohort'] = np.array([p in cs for p in pids])
    for g, mask in groups.items():
        if mask.sum() == 0:
            continue
        A, E = na[:, mask].sum(1), ne[:, mask].sum(1)
        C = other[:, mask].sum(1) + np.array([rng.choice(ca, size=int(a)).sum() if a else 0.0 for a in A]) + \
            np.array([rng.choice(ce, size=int(e)).sum() if e else 0.0 for e in E])
        row = {'patients': int(mask.sum())}
        for name, sim, obs in (('admissions', A, ya[mask].sum()), ('ed_visits', E, ye[mask].sum()), ('total_cost', C, total_obs[mask].sum())):
            lo, hi = np.quantile(sim, [0.05, 0.95])
            row[name] = {'observed': _r(obs, 2), 'interval_90': [_r(lo, 2), _r(hi, 2)], 'inside': bool(lo <= obs <= hi)}
        out[g] = row
    return out


def related_share(h, ids, a, b, kind, prefixes, exclude=None):
    """(events with a related principal diagnosis, events) for kind in [a, b), excluding {pid: {day}}."""
    k = n = 0
    pre = tuple(prefixes)
    for pid in ids:
        skip = (exclude or {}).get(pid) or ()
        for d, kd, _, dx in h.events.get(pid, ()):
            if kd == kind and a <= d < b and d not in skip:
                n += 1
                k += any(x.startswith(pre) for x in dx)
    return k, n


def related_other_share(h, ids, a, b, prefixes):
    tot = rel = 0.0
    pre = tuple(prefixes)
    for pid in ids:
        for d, p, dx in h.other.get(pid, ()):
            if a <= d < b:
                tot += p
                if dx.startswith(pre):
                    rel += p
    return rel, tot


def iso_to_day(s):
    return day(s)
