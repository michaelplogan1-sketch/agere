"""Demand forecasting: visit, admission, appointment, bed-day and discharge volumes with uncertainty and capacity.

Answers "forecast ED visits for the next 6 months", "how many beds will we need next quarter?",
"weekly admissions forecast with uncertainty" or "how many nurses do we need for next month's
discharges?" (staffing only from user-supplied ratios).

Series (semantic layer, read only): visits of one setting counted on their event date (visit table, one row
per encounter), scheduled appointments on their scheduled date, discharges on the discharge date and bed-days
spread over the nights of each stay. Stays without a received discharge are counted and flagged; they never
contribute 0 days. Weekly (Monday-start) or monthly periods; only complete periods up to the snapshot are used.

Data checks before fitting: structural zero runs (feed gaps: a run of empty periods the surrounding level makes
implausible, via monitor.zero_runs), single-period spikes (Poisson tail versus the local median) and level breaks
(exact penalised change points on variance-stabilised counts, signal_math.gaussian_change_points). Models are fitted
only on the usable recent regime: after the last gap, after the last level break when enough periods remain, with
in-regime spikes replaced by the local median.

Models: naive / seasonal naive, drift, regime mean, ETS (simple, damped Holt, damped Holt-Winters additive and
multiplicative; grid-search least squares) and a negative-binomial log-linear GLM (level, trend, Fourier
seasonality). Selection by rolling-origin backtest MASE; the naive baseline is always reported. Intervals are
normalised split-conformal from backtest residuals (model simulation when too few residuals), with empirical
coverage measured sequentially (each origin calibrated only on errors observed before it).
"""
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
import calendar, json, math, os, sqlite3

import numpy as np
from scipy.stats import poisson

from . import monitor, semantic_layer
from ...signal_math import gaussian_change_points

VERSION = 'demand/1'
# measure -> (source, visit type concept, label)
MEASURES = {
    'ed_visits': ('visit', 'emergency_visit', 'ED visits'),
    'inpatient_admissions': ('visit', 'inpatient_admission', 'inpatient admissions'),
    'hospital_admissions': ('utilization_event', ('inpatient', 'observation'), 'hospital admissions'),
    'observation_stays': ('visit', 'observation_stay', 'observation stays'),
    'urgent_care_visits': ('visit', 'urgent_care_visit', 'urgent care visits'),
    'primary_care_visits': ('visit', 'primary_care_visit', 'primary care visits'),
    'specialist_visits': ('visit', 'specialist_visit', 'specialist visits'),
    'appointments': ('appointment', None, 'scheduled appointments'),
    'discharges': ('stay', None, 'hospital discharges'),
    'bed_days': ('stay', None, 'bed-days'),
}
FREQUENCIES = ('week', 'month')
GROUP_BY = ('none', 'practice', 'facility', 'payer')
SEASON = {'week': 52, 'month': 12}
MIN_REGIME = {'week': 12, 'month': 8}       # shortest usable regime
MIN_FIT = {'week': 8, 'month': 6}           # first backtest origin
MAX_ORIGINS = 40
LEVELS = (0.8, 0.95)
SIMS = 2000
BACKTEST_SIMS = 300


# ---- parameters -------------------------------------------------------------------------
def validate(p):
    allowed = {'measure', 'frequency', 'horizon', 'group_by', 'top', 'target_occupancy', 'peak_quantile', 'staffing',
               'minutes_per_unit', 'fte_hours_per_week', 'reconcile', 'hierarchy'}
    if not isinstance(p, dict) or set(p) - allowed: raise ValueError(f'demand parameters: {sorted(allowed)}')
    if p.get('measure') not in MEASURES: raise ValueError(f'measure must be one of {sorted(MEASURES)}')
    freq = p.get('frequency') or 'month'
    if freq not in FREQUENCIES: raise ValueError(f'frequency must be one of {FREQUENCIES}')
    horizon = p.get('horizon') or (6 if freq == 'month' else 13)
    limit = 24 if freq == 'month' else 104
    if type(horizon) is not int or not 1 <= horizon <= limit: raise ValueError(f'horizon 1..{limit} {freq}s')
    group_by = p.get('group_by') or 'none'
    if group_by not in GROUP_BY: raise ValueError(f'group_by must be one of {GROUP_BY}')
    top = p.get('top') or 8
    if type(top) is not int or not 1 <= top <= 30: raise ValueError('top 1..30 groups')
    occ = p.get('target_occupancy') or 0.85
    if type(occ) not in (int, float) or not 0.3 <= occ <= 1.0: raise ValueError('target_occupancy 0.3..1.0')
    peak = p.get('peak_quantile') or 0.95
    if type(peak) not in (int, float) or not 0.5 <= peak < 1.0: raise ValueError('peak_quantile 0.5..0.999')
    hierarchy = p.get('hierarchy') or []
    if (not isinstance(hierarchy, (list, tuple)) or len(hierarchy) > 3 or len(set(hierarchy)) != len(hierarchy)
            or any(h not in GROUP_BY or h == 'none' for h in hierarchy)):
        raise ValueError(f'hierarchy: ordered list (outermost first) of up to 3 distinct levels from {[g for g in GROUP_BY if g != "none"]}')
    reconcile = p.get('reconcile')
    if reconcile is not None and reconcile not in RECONCILE: raise ValueError(f'reconcile must be one of {RECONCILE}')
    if not hierarchy and reconcile not in (None, 'none'):
        if group_by == 'none': raise ValueError('reconcile needs a hierarchy (or a group_by to use as a one-level hierarchy)')
        hierarchy = [group_by]
    out = {'measure': p['measure'], 'frequency': freq, 'horizon': horizon, 'group_by': group_by, 'top': top,
           'hierarchy': list(hierarchy), 'reconcile': reconcile or ('mint_shrink' if hierarchy else 'none'),
           'target_occupancy': float(occ), 'peak_quantile': float(peak), 'staffing': bool(p.get('staffing', False))}
    for k, lo, hi in (('minutes_per_unit', 0.1, 10000.0), ('fte_hours_per_week', 1.0, 80.0)):
        v = p.get(k)
        if v in (None, 0, 0.0): out[k] = None; continue
        if type(v) not in (int, float) or not math.isfinite(v) or not lo <= v <= hi: raise ValueError(f'{k} {lo:g}..{hi:g} (user-supplied)')
        out[k] = float(v)
    if out['minutes_per_unit'] or out['fte_hours_per_week']: out['staffing'] = True
    return out


# ---- periods ----------------------------------------------------------------------------
def period_of(d, freq):
    d = date.fromisoformat(d[:10])
    return (d - timedelta(days=d.weekday())).isoformat() if freq == 'week' else d.isoformat()[:7]


def period_start(k, freq):
    return date.fromisoformat(k) if freq == 'week' else date.fromisoformat(k + '-01')


def period_days(k, freq):
    if freq == 'week': return 7
    y, m = map(int, k.split('-')); return calendar.monthrange(y, m)[1]


def next_period(k, freq, n=1):
    if freq == 'week': return (date.fromisoformat(k) + timedelta(days=7 * n)).isoformat()
    y, m = map(int, k.split('-')); m += n
    y += (m - 1) // 12; m = (m - 1) % 12 + 1
    return f'{y:04d}-{m:02d}'


def period_end(k, freq):
    return period_start(k, freq) + timedelta(days=period_days(k, freq) - 1)


# ---- data -------------------------------------------------------------------------------
def events(db, measure, patient_ids, as_of):
    """Dated units of the measure: list of (date, patient_id, organization_id, weight); plus counts of flagged rows."""
    source, concept, _ = MEASURES[measure]
    members = set(patient_ids) if patient_ids is not None else None
    keep = lambda pid: members is None or pid in members
    rows, flags = [], Counter()
    if source == 'utilization_event':
        for d, pid, org in db.execute("SELECT event_date, patient_id, organization_id FROM utilization_event WHERE kind IN ('inpatient', 'observation')"):
            if keep(pid) and d and d[:10] <= as_of: rows.append((d[:10], pid, org, 1.0))
    elif source == 'visit':
        for d, pid, org in db.execute('SELECT substr(event_date, 1, 10), patient_id, organization_id FROM visit WHERE type_concept = ?', (concept,)):
            if not keep(pid): continue
            if not d: flags['undated_visits'] += 1; continue
            rows.append((d, pid, org, 1.0))
    elif source == 'appointment':
        for d, pid, org in db.execute('SELECT substr(scheduled_date, 1, 10), patient_id, organization_id FROM appointment'):
            if not keep(pid): continue
            if not d: flags['undated_appointments'] += 1; continue
            rows.append((d, pid, org, 1.0))
    else:
        for pid, admit, dis, known, org in db.execute('SELECT patient_id, admit_date, discharge_date, discharge_known, organization_id FROM stay'):
            if not keep(pid) or not admit: continue
            if not known or not dis:
                flags['stays_without_received_discharge'] += 1
                if measure == 'bed_days': flags[f'open_stay_admit_{admit[:7]}'] += 1
                continue
            if measure == 'discharges':
                rows.append((dis[:10], pid, org, 1.0)); continue
            a, b = date.fromisoformat(admit[:10]), date.fromisoformat(dis[:10])
            nights = max((b - a).days, 1)  # a same-day stay occupies a bed for one day
            for i in range(nights):
                d = (a + timedelta(days=i)).isoformat()
                if d <= as_of: rows.append((d, pid, org, 1.0))
    return rows, flags


def attributes(db):
    return {pid: {'practice': prac or 'unattributed', 'payer': cov or 'unknown'}
            for pid, prac, cov in db.execute('SELECT patient_id, pcp_practice_id, coverage_type FROM patient')}


def group_key(group_by, measure, pid, org, attrs):
    if group_by == 'none': return 'all'
    if group_by == 'facility': return org or 'unknown'
    if group_by == 'practice' and measure == 'appointments': return org or 'unknown'  # where the appointment is scheduled
    return (attrs.get(pid) or {}).get(group_by, 'unattributed' if group_by == 'practice' else 'unknown')


def build_series(rows, freq, as_of):
    """Counts per period from the first event's period through the last complete period; plus future (booked) counts."""
    counts, future = Counter(), Counter()
    for d, *_, w in rows:
        if d > as_of: future[period_of(d, freq)] += w
        else: counts[period_of(d, freq)] += w
    if not counts: return [], [], future, None
    current = period_of(as_of, freq)
    last = current if period_end(current, freq).isoformat() <= as_of else next_period(current, freq, -1)
    first = min(counts)
    periods = []
    k = first
    while k <= last:
        periods.append(k); k = next_period(k, freq)
    partial = None if last == current else {'period': current, 'observed_so_far': counts.get(current, 0.0)}
    return periods, [counts.get(k, 0.0) for k in periods], future, partial


# ---- data checks: gaps, spikes, level breaks ---------------------------------------------
def _local_median(y, i, bad, width=6):
    nb = [y[j] for j in range(max(0, i - width), min(len(y), i + width + 1)) if j != i and j not in bad and y[j] > 0]
    return float(np.median(nb)) if nb else 0.0


def screen(y, freq):
    """Classify periods: structural gaps (zero runs), spikes, level breaks; choose the usable recent regime.
    Returns {start, gaps, spikes, breaks, cleaned, notes, usable}."""
    n = len(y); y = [float(v) for v in y]
    # 1. structural zero runs (feed gaps): expected count from the nearest non-zero periods
    expected = [y[i] for i in range(n)]
    i = 0
    while i < n:  # each zero run expects the mean level of the 6 periods on either side of it
        if y[i] != 0: i += 1; continue
        j = i
        while j + 1 < n and y[j + 1] == 0: j += 1
        around = y[max(0, i - 6):i] + y[j + 1:j + 7]
        for k in range(i, j + 1): expected[k] = float(np.mean(around)) if around else 0.0
        i = j + 1
    points = [{'index': i, 'count': int(round(y[i])), 'expected': expected[i]} for i in range(n)]
    runs = monitor.zero_runs(points, min_run=2 if freq == 'month' else 3, min_expected=0.5)
    gaps = [(r['start'], r['end']) for r in runs if r['rule'] == 'zero_run' and r['p'] < 1e-3]
    in_gap = {i for s, e in gaps for i in range(s, e + 1)}
    # 2. single-period spikes: implausible versus the local median (Poisson tail, and at least 3x)
    spikes = []
    for i in range(n):
        if i in in_gap or y[i] < 5: continue
        med = _local_median(y, i, in_gap)
        if med > 0 and y[i] >= 3 * med and poisson.sf(y[i] - 1, med * 1.5) < 1e-6:
            spikes.append(i)
    # 3. regime start: after the last gap (or a spike that sits at a gap edge)
    start = max([e + 1 for s, e in gaps] + [0])
    while start < n and (start in spikes or y[start] == 0): start += 1
    notes, cleaned = [], list(y)
    if gaps and start < n:
        seg = [v for v in y[start:] if v > 0]
        if seg and y[start] < 0.5 * float(np.median(seg)) and n - start > MIN_REGIME[freq]:
            notes.append('first period after the gap trimmed as a partial resumption'); start += 1
    for i in spikes:
        if i >= start: cleaned[i] = _local_median(y, i, in_gap | set(spikes))
    # 4. level breaks inside the regime (variance-stabilised counts, exact penalised segmentation)
    breaks = []
    seg = cleaned[start:]
    if len(seg) >= 6:
        z = np.array([2 * math.sqrt(v + 0.375) for v in seg])
        m = SEASON[freq]
        if len(z) >= 2 * m:  # remove a seasonal profile so seasonality is not read as level changes
            pos = np.arange(len(z)) % m
            prof = np.array([z[pos == j].mean() for j in range(m)])
            z = z - prof[pos] + prof.mean()
        dz = np.diff(z)
        s = max(1.0, 1.4826 * float(np.median(np.abs(dz - np.median(dz)))) / math.sqrt(2)) if len(dz) else 1.0
        cp = gaussian_change_points(list(range(len(z))), [float(v) for v in z], [s] * len(z), penalty=3.0 * math.log(len(z)))
        blocks = cp['blocks']
        for a, b in zip(blocks, blocks[1:]):  # report only material shifts (>= 30% in the count level)
            la, lb = (max(a['weighted_mean'] / 2, 0) ** 2 - 0.375), (max(b['weighted_mean'] / 2, 0) ** 2 - 0.375)
            if abs(lb - la) >= 0.3 * max(la, lb, 1.0): breaks.append(start + b['start_index'])
    regime_start = start
    if breaks:
        last = breaks[-1]
        if n - last >= MIN_REGIME[freq]:
            regime_start = last
            notes.append('fitted after the latest level break')
        else:
            notes.append('a level break within the last few periods leaves too little data; fitted across it')
    usable = n - regime_start
    return {'start': regime_start, 'gaps': gaps, 'spikes': spikes, 'breaks': breaks, 'cleaned': cleaned, 'notes': notes,
            'usable': usable, 'trailing_gap': bool(gaps and gaps[-1][1] == n - 1)}


# ---- models ------------------------------------------------------------------------------
class Model:
    """point(h) -> mean path; simulate(h, n, rng) -> (n, h) sample paths."""
    name = ''
    def point(self, h): raise NotImplementedError
    def simulate(self, h, n, rng): raise NotImplementedError


class Naive(Model):
    def __init__(self, y, m=1):
        y = np.asarray(y, float); self.name = 'seasonal_naive' if m > 1 else 'naive'; self.y, self.m = y, m
        d = y[m:] - y[:-m]
        self.sigma = float(np.sqrt(np.mean(d ** 2))) if len(d) else 1.0
    def point(self, h):
        m, y = self.m, self.y
        return np.array([y[len(y) - m + (k % m)] for k in range(h)])
    def simulate(self, h, n, rng):
        steps = np.array([k // self.m + 1 for k in range(h)])
        if self.m == 1: return self.point(h) + np.cumsum(rng.normal(0, self.sigma, (n, h)), axis=1)
        return self.point(h) + rng.normal(0, 1, (n, h)) * self.sigma * np.sqrt(steps)


class Drift(Model):
    name = 'drift'
    def __init__(self, y):
        y = np.asarray(y, float); self.y = y; T = len(y)
        self.c = (y[-1] - y[0]) / (T - 1) if T > 1 else 0.0
        r = np.diff(y) - self.c
        self.sigma = float(np.sqrt(np.sum(r ** 2) / max(T - 2, 1))) if T > 2 else 1.0
    def point(self, h): return self.y[-1] + self.c * np.arange(1, h + 1)
    def simulate(self, h, n, rng):
        T = len(self.y); k = np.arange(1, h + 1)
        slope_sd = self.sigma / math.sqrt(max(T - 1, 1))
        return self.point(h) + np.cumsum(rng.normal(0, self.sigma, (n, h)), axis=1) + rng.normal(0, slope_sd, (n, 1)) * k


class Mean(Model):
    name = 'mean'
    def __init__(self, y):
        y = np.asarray(y, float); self.mu = float(y.mean()); self.sd = float(y.std(ddof=1)) if len(y) > 1 else 1.0; self.n = len(y)
    def point(self, h): return np.full(h, self.mu)
    def simulate(self, h, n, rng): return self.mu + rng.normal(0, self.sd * math.sqrt(1 + 1 / self.n), (n, h))


def _ets_pass(y, alpha, beta, phi, gamma, season, m, l, b, s, noise=None):
    """One vectorised pass of the additive-error ETS recursion over y (or over simulated steps when y is None).
    Arrays share a leading dimension (parameter grid or simulation draws). Returns (sse or path, l, b, s)."""
    T = len(y) if y is not None else noise.shape[1]
    out = np.zeros(l.shape[0]) if y is not None else np.zeros((l.shape[0], T))
    for t in range(T):
        base = l + phi * b
        j = t % m if season else 0
        si = s[:, j] if season else 0.0
        yhat = base + si if season == 'add' else base * si if season == 'mul' else base
        if y is not None:
            e = y[t] - yhat; out += e * e
        else:
            e = noise[:, t]; out[:, t] = yhat + e
        if season == 'mul':
            l_new = base + alpha * e / si; b = phi * b + beta * e / si
            s[:, j] = si + gamma * e / np.maximum(base, 1e-9)
        else:
            l_new = base + alpha * e; b = phi * b + beta * e
            if season == 'add': s[:, j] = si + gamma * e
        l = l_new
    return out, l, b, s


class ETS(Model):
    """Additive-error exponential smoothing: trend in {None, 'damped'}, season in {None, 'add', 'mul'}; grid least squares."""
    def __init__(self, y, trend=None, season=None, m=1):
        y = np.asarray(y, float); n = len(y); self.season, self.m, self.trend = season, (m if season else 1), trend
        if season == 'mul' and y.min() <= 0: raise ValueError('multiplicative seasonality needs positive counts')
        if season and n < 2 * m: raise ValueError('seasonal ETS needs two full seasons')
        self.name = {(None, None): 'ets_simple', ('damped', None): 'ets_damped_trend', ('damped', 'add'): 'holt_winters_additive_damped',
                     ('damped', 'mul'): 'holt_winters_multiplicative_damped'}[(trend, season)]
        A = [0.05, 0.1, 0.2, 0.3, 0.5, 0.7, 0.9]
        B = [0.01, 0.05, 0.1, 0.2] if trend else [0.0]
        P = [0.8, 0.9, 0.95, 0.98] if trend else [0.0]
        G = [0.01, 0.05, 0.1, 0.3] if season else [0.0]
        grid = np.array([(a, bb, p, g) for a in A for bb in B for p in P for g in G if bb <= a and (not season or g <= 1 - a)])
        al, be, ph, ga = grid.T
        if season:
            first = y[:m]; l0 = first.mean()
            b0 = (y[m:2 * m].mean() - l0) / m if trend else 0.0
            s0 = first - l0 if season == 'add' else first / max(l0, 1e-9)
        else:
            l0, b0, s0 = y[0], (float(np.mean(np.diff(y[:4]))) if trend and n > 1 else 0.0), None
        mk = lambda k: (np.full(k, l0), np.full(k, b0), np.tile(s0, (k, 1)) if season else None)
        sse, *_ = _ets_pass(y, al, be, ph, ga, season, self.m, *mk(len(grid)))
        sse[~np.isfinite(sse)] = np.inf
        i = int(np.argmin(sse)); self.params = tuple(float(v) for v in grid[i])
        a, bb, p, g = self.params
        _, l, b, s = _ets_pass(y, np.array([a]), np.array([bb]), np.array([p]), np.array([g]), season, self.m, *mk(1))
        self.state, self.n = (float(l[0]), float(b[0]), s[0].copy() if season else None), n
        k = 1 + (2 if trend else 0) + (self.m if season else 0)
        self.sigma = float(math.sqrt(sse[i] / max(n - k, 1)))
        if not math.isfinite(self.sigma): raise ValueError('ETS fit diverged')

    def _run(self, h, noise):
        a, bb, p, g = self.params; k = noise.shape[0]
        l0, b0, s0 = self.state
        s = np.tile(np.roll(s0, -(self.n % self.m)), (k, 1)) if self.season else None  # re-index seasons to the next step
        path, *_ = _ets_pass(None, np.full(k, a), np.full(k, bb), np.full(k, p), np.full(k, g), self.season, self.m,
                             np.full(k, l0), np.full(k, b0), s, noise=noise)
        return path

    def point(self, h): return self._run(h, np.zeros((1, h)))[0]
    def simulate(self, h, n, rng): return self._run(h, rng.normal(0, self.sigma, (n, h)))


class NegBinGLM(Model):
    """Log-linear count model: log mu = a [+ b t] [+ Fourier seasonal terms]; Poisson IRLS, NB2 dispersion by moments."""
    def __init__(self, y, trend=True, m=1, harmonics=0):
        y = np.asarray(y, float); n = len(y); self.n, self.trend, self.m, self.K = n, trend, m, harmonics
        self.name = 'negbin_glm' + ('_trend' if trend else '_level') + ('_seasonal' if harmonics else '')
        X = self._X(np.arange(n))
        beta = np.zeros(X.shape[1]); beta[0] = math.log(max(y.mean(), 0.1))
        for _ in range(50):
            mu = np.exp(np.clip(X @ beta, -20, 20))
            W = mu; z = X @ beta + (y - mu) / mu
            XtW = X.T * W
            new = np.linalg.solve(XtW @ X + 1e-8 * np.eye(len(beta)), XtW @ z)
            if np.max(np.abs(new - beta)) < 1e-8: beta = new; break
            beta = new
        mu = np.exp(np.clip(X @ beta, -20, 20)); p = X.shape[1]
        self.alpha = max(0.0, float(np.sum(((y - mu) ** 2 - mu) / mu ** 2) / max(n - p, 1)))
        var = mu + self.alpha * mu ** 2
        info = (X.T * (mu ** 2 / var)) @ X
        self.beta, self.cov = beta, np.linalg.pinv(info)
        if not np.all(np.isfinite(beta)): raise ValueError('GLM did not converge')

    def _X(self, t):
        cols = [np.ones(len(t))]
        if self.trend: cols.append(t / max(self.n, 1))
        for k in range(1, self.K + 1):
            cols += [np.sin(2 * math.pi * k * t / self.m), np.cos(2 * math.pi * k * t / self.m)]
        return np.column_stack(cols)

    def point(self, h): return np.exp(np.clip(self._X(np.arange(self.n, self.n + h)) @ self.beta, -20, 20))

    def simulate(self, h, n, rng):
        X = self._X(np.arange(self.n, self.n + h))
        betas = rng.multivariate_normal(self.beta, self.cov, size=n, check_valid='ignore')
        mu = np.exp(np.clip(betas @ X.T, -20, 20))
        if self.alpha > 1e-6: mu = mu * rng.gamma(1 / self.alpha, self.alpha, size=mu.shape)
        return rng.poisson(mu).astype(float)


_TIMESTRAL = {}


def _timestral_forecaster(size):
    if size not in _TIMESTRAL:
        from timestral import TimestralForecaster  # optional dependency, imported only when the candidate is enabled
        _TIMESTRAL[size] = TimestralForecaster.from_pretrained(f'mistralai/Timestral-{size}')
    return _TIMESTRAL[size]


class Timestral(Model):
    """Zero-shot Timestral foundation model (Mistral). point = forecast median; simulate draws per-step values from the
    21 forecast quantiles by inverse-CDF interpolation (steps independent; the conformal step calibrates the spread)."""
    def __init__(self, y, size):
        self.name = f'timestral_{size.lower()}'; self.y, self.size = np.asarray(y, float), size
        self._h, self._q = 0, None

    def _quantiles(self, h):
        if self._h < h:
            try:
                fc = _timestral_forecaster(self.size).forecast(self.y, prediction_length=h)
            except Exception as e:  # missing package/weights or a runtime failure: the classic candidates take over
                raise ValueError(f'Timestral-{self.size} unavailable: {e}') from e
            self._q = np.asarray(fc.quantiles, float).reshape(h, -1); self._levels = np.asarray(fc.quantile_levels, float); self._h = h
        return self._q[:h]

    def point(self, h): return self._quantiles(h)[:, list(self._levels).index(0.5)]

    def simulate(self, h, n, rng):
        q = self._quantiles(h); u = rng.uniform(self._levels[0], self._levels[-1], (n, h))
        return np.stack([np.interp(u[:, k], self._levels, q[k]) for k in range(h)], axis=1)


def timestral_sizes():
    """Timestral sizes from AGERE_DEMAND_TIMESTRAL (default '123M'; e.g. '123M,28M'); 'off' = classic models only.
    The first size is the default demand model (2026-10-17 build test: ~11% lower out-of-sample MASE than the
    classic backtest pick at equal conformal coverage, docs/agere/BUILD_LOG.md)."""
    raw = os.environ.get('AGERE_DEMAND_TIMESTRAL', '123M')
    return [] if raw.strip().lower() in ('', 'off', '0', 'false') else [s.strip() for s in raw.split(',') if s.strip()]


def default_model():
    sizes = timestral_sizes()
    return f'timestral_{sizes[0].lower()}' if sizes else None


def candidates(n, freq, seasonal):
    """(name, factory) pairs fit-able on a series of length n."""
    m = SEASON[freq]
    out = [('naive', lambda y: Naive(y, 1)), ('drift', Drift), ('mean', Mean),
           ('ets_simple', lambda y: ETS(y)), ('ets_damped_trend', lambda y: ETS(y, 'damped')),
           ('negbin_glm_level', lambda y: NegBinGLM(y, trend=False)), ('negbin_glm_trend', lambda y: NegBinGLM(y, trend=True))]
    if seasonal:
        out.insert(0, ('seasonal_naive', lambda y: Naive(y, m)))
        out.append(('negbin_glm_trend_seasonal', lambda y: NegBinGLM(y, trend=True, m=m, harmonics=2 if freq == 'month' else 3)))
        if m <= 12:
            out.append(('holt_winters_additive_damped', lambda y: ETS(y, 'damped', 'add', m)))
            out.append(('holt_winters_multiplicative_damped', lambda y: ETS(y, 'damped', 'mul', m)))
    for size in timestral_sizes():
        out.append((f'timestral_{size.lower()}', lambda y, size=size: Timestral(y, size)))
    from . import foundation_forecasters as ff  # Chronos-2 / TimesFM-2.5 challengers: used only where the backtest says they win
    for name in ff.enabled():
        out.append((name, lambda y, name=name: ff.Challenger(y, name)))
    return out


def _mase_scale(y, lag):
    d = np.abs(np.asarray(y[lag:], float) - np.asarray(y[:-lag], float))
    s = float(d.mean()) if len(d) else 0.0
    return s if s > 0 else max(float(np.mean(np.abs(y))), 1.0)


def backtest(y, freq, horizon, seed=0):
    """Rolling-origin evaluation of every candidate. Returns (records per model, origins, seasonal flag, baseline name)."""
    n, m = len(y), SEASON[freq]
    seasonal = n >= 2 * m + MIN_FIT[freq]
    first = max(MIN_FIT[freq], 2 * m if seasonal else 0)
    origins = list(range(max(first, n - MAX_ORIGINS), n))
    lag = m if seasonal else 1
    rng = np.random.default_rng(seed)
    records = defaultdict(list)
    for name, factory in candidates(n, freq, seasonal):
        ok = True; recs = []
        for o in origins:
            h = min(horizon, n - o)
            try:
                model = factory(y[:o])
                pt = model.point(h); sims = model.simulate(h, BACKTEST_SIMS, rng)
            except (ValueError, np.linalg.LinAlgError, FloatingPointError):
                ok = False; break
            if not np.all(np.isfinite(pt)): ok = False; break
            sd = sims.std(axis=0); scale = _mase_scale(y[:o], lag)
            q = {lv: np.quantile(np.maximum(sims, 0), [(1 - lv) / 2, (1 + lv) / 2], axis=0) for lv in LEVELS}
            for k in range(h):
                a = float(y[o + k])
                recs.append({'origin': o, 'step': k + 1, 'target': o + k, 'actual': a, 'point': float(pt[k]), 'sd': float(sd[k]),
                             'scaled_error': abs(a - float(pt[k])) / scale,
                             'model_cover': {lv: bool(q[lv][0][k] <= a <= q[lv][1][k]) for lv in LEVELS}})
        if ok and recs: records[name] = recs
    return records, origins, seasonal, 'seasonal_naive' if seasonal else 'naive'


def _conformal_q(scores, level):
    s = sorted(scores); N = len(s)
    k = math.ceil((N + 1) * level)
    return s[k - 1] if 1 <= k <= N else None


def _score(r, floor):
    return abs(r['actual'] - r['point']) / max(r['sd'], floor)


def sequential_coverage(recs, level, floor):
    """Each origin's intervals calibrated only on errors whose targets were observed before that origin."""
    hits = total = 0
    for o in sorted({r['origin'] for r in recs}):
        prior = [_score(r, floor) for r in recs if r['target'] < o]
        q = _conformal_q(prior, level)
        if q is None: continue
        for r in recs:
            if r['origin'] == o:
                total += 1; hits += abs(r['actual'] - r['point']) <= q * max(r['sd'], floor)
    return (hits / total if total else None), total


# ---- one series end to end -----------------------------------------------------------------
def forecast_series(periods, y, freq, horizon, partial=None, booked=None, seed=0):
    """Screen, select by backtest, forecast with intervals. Pure function of the series (testable on synthetic data)."""
    out = {'history': [], 'status': 'forecast', 'data_issues': []}
    n = len(y)
    if n == 0:
        out.update(status='insufficient', reason='no dated records of this measure'); return out
    sc = screen(y, freq)
    gaps, spikes = set(i for s, e in sc['gaps'] for i in range(s, e + 1)), set(sc['spikes'])
    for i, k in enumerate(periods):
        st = 'gap' if i in gaps else 'spike' if i in spikes else 'before_regime' if i < sc['start'] else 'used'
        out['history'].append({'period': k, 'value': y[i], 'status': st, **({'fitted_value': round(sc['cleaned'][i], 2)} if st == 'spike' and i >= sc['start'] else {})})
    for s, e in sc['gaps']:
        out['data_issues'].append({'kind': 'gap', 'periods': f'{periods[s]}..{periods[e]}', 'detail': 'structural zero run (feed gap): '
                                   f'{e - s + 1} empty {freq}s where the surrounding level makes zero implausible; excluded'})
    for i in sc['spikes']:
        out['data_issues'].append({'kind': 'spike', 'periods': periods[i], 'detail': f'{y[i]:g} is >= 3x the local median: implausible '
                                   'single-period magnitude (batch load or duplicate feed); ' + ('replaced by the local median' if i >= sc['start'] else 'excluded')})
    for i in sc['breaks']:
        out['data_issues'].append({'kind': 'level_break', 'periods': periods[i], 'detail': 'level change detected (penalised change point)'})
    out['regime'] = {'start': periods[sc['start']] if sc['start'] < n else None, 'end': periods[-1], 'periods': sc['usable'], 'notes': sc['notes']}
    if sc['trailing_gap']:
        out.update(status='insufficient', reason=f'the most recent {freq}s are a feed gap ({out["data_issues"][-1]["periods"] if out["data_issues"] else ""}); '
                   'no current regime to forecast from'); return out
    if sc['usable'] < MIN_REGIME[freq]:
        out.update(status='insufficient', reason=f'only {sc["usable"]} usable {freq}s in the current regime (need {MIN_REGIME[freq]})'); return out
    yr = np.asarray(sc['cleaned'][sc['start']:], float)
    steps = horizon + (1 if partial else 0)
    records, origins, seasonal, baseline = backtest(yr, freq, steps, seed)
    if baseline not in records: baseline = 'naive'
    mase = {name: float(np.mean([r['scaled_error'] for r in recs])) for name, recs in records.items()}
    from . import foundation_forecasters as ff
    classic = {k: v for k, v in mase.items() if not ff.is_foundation(k)}
    picked = min(classic, key=lambda k: (round(classic[k], 6), list(classic).index(k)))
    factories = dict(candidates(len(yr), freq, seasonal))
    rng = np.random.default_rng(seed + 1)
    best, selection = picked, 'backtest_mase'
    preferred = default_model()
    if preferred in mase:
        try:
            model = factories[preferred](yr)
            pt = np.maximum(model.point(steps), 0); sims = np.maximum(model.simulate(steps, SIMS, rng), 0)
            best, selection = preferred, 'default'
        except ValueError:
            pass
    if selection != 'default':
        model = factories[picked](yr)
        pt = np.maximum(model.point(steps), 0); sims = np.maximum(model.simulate(steps, SIMS, rng), 0)
    challengers = {k: mase[k] for k in ff.CHALLENGERS if k in mase}
    if challengers:  # a challenger replaces the incumbent only by beating its backtest MASE by the margin
        c = min(challengers, key=challengers.get)
        if challengers[c] < mase[best] * (1 - ff.CHALLENGER_MARGIN):
            try:
                cm = factories[c](yr)
                cpt = np.maximum(cm.point(steps), 0); csims = np.maximum(cm.simulate(steps, SIMS, rng), 0)
                model, pt, sims, best, selection = cm, cpt, csims, c, 'challenger_backtest'
            except ValueError:
                pass
    sd = sims.std(axis=0)
    recs = records[best]
    floor = 0.5
    scores = [_score(r, floor) for r in recs]
    intervals, methods = {}, {}
    for lv in LEVELS:
        q = _conformal_q(scores, lv)
        if q is not None:
            intervals[lv] = (np.maximum(pt - q * np.maximum(sd, floor), 0), pt + q * np.maximum(sd, floor)); methods[lv] = 'split_conformal'
        else:
            lo, hi = np.quantile(sims, [(1 - lv) / 2, (1 + lv) / 2], axis=0)
            intervals[lv] = (lo, hi); methods[lv] = 'model_simulation'
    coverage = {}
    for lv in LEVELS:
        c, k = sequential_coverage(recs, lv, floor)
        coverage[f'{int(lv * 100)}'] = {'conformal_sequential': None if c is None else round(c, 3), 'evaluated': k,
                                         'model_simulation': round(float(np.mean([r['model_cover'][lv] for r in recs])), 3), 'errors': len(recs)}
    fc = []
    k = periods[-1]
    for i in range(steps):
        k = next_period(k, freq)
        row = {'period': k, 'point': round(float(pt[i]), 2),
               'lo80': round(float(intervals[0.8][0][i]), 2), 'hi80': round(float(intervals[0.8][1][i]), 2),
               'lo95': round(float(intervals[0.95][0][i]), 2), 'hi95': round(float(intervals[0.95][1][i]), 2)}
        if partial and k == partial['period']: row.update(in_progress=True, observed_so_far=partial['observed_so_far'])
        if booked and booked.get(k): row['already_booked'] = booked[k]
        fc.append(row)
    out.update(model=best, model_selection=selection, backtest_pick=picked, model_parameters=getattr(model, 'params', None), forecast=fc, interval_method=methods, seasonal_models_considered=seasonal,
               backtest={'origins': len(origins), 'errors': len(recs), 'horizon': steps, 'baseline': baseline,
                         'mase': {k: round(v, 3) for k, v in sorted(mase.items(), key=lambda kv: kv[1])},
                         'selected_mase': round(mase[best], 3), 'baseline_mase': round(mase[baseline], 3),
                         'skill_vs_baseline': round(1 - mase[best] / mase[baseline], 3) if mase[baseline] > 0 else None,
                         'coverage': coverage})
    out.update(_sims=sims, _pt=pt, _sd=sd, _recs=recs, _offset=sc['start'], _yr=yr, _lag=SEASON[freq] if seasonal else 1, _floor=floor)
    return out


# ---- hierarchical reconciliation (MinT) -----------------------------------------------------
RECONCILE = ('mint_shrink', 'ols', 'wls', 'none')
MIN_RESID = 6          # fewest common one-step residuals for MinT-shrink; below it WLS-structural is used
MAX_RESID = MAX_ORIGINS


def hierarchy_structure(paths):
    """paths: node paths (tuples of keys; () = network). Returns (S, parent index per node, level per node, bottom indices).
    S is (nodes x bottoms): S[i, j] = 1 when bottom j lies under node i."""
    idx = {p: i for i, p in enumerate(paths)}
    bottoms = [i for i, p in enumerate(paths) if not any(q[:len(p)] == p and len(q) > len(p) for q in paths)]
    S = np.zeros((len(paths), len(bottoms)))
    for j, b in enumerate(bottoms):
        for d in range(len(paths[b]) + 1): S[idx[paths[b][:d]], j] = 1.0
    parent = [idx.get(p[:-1]) if p else None for p in paths]
    return S, parent, [len(p) for p in paths], bottoms


def shrink_covariance(E):
    """Schafer-Strimmer shrinkage of the (uncentred, as in MinT) residual covariance towards its diagonal.
    E is (T x n). Returns (W, lambda). Positive definite whenever lambda > 0, so n may exceed T."""
    E = np.asarray(E, float); T, n = E.shape
    sd = np.sqrt((E ** 2).mean(axis=0))
    sd = np.maximum(sd, max(1e-8, 1e-6 * float(sd.max()) if sd.size else 1e-8))
    Z = E / sd
    if n == 1 or T < 2: return np.diag(sd ** 2), 1.0
    R = Z.T @ Z / T
    w = Z[:, :, None] * Z[:, None, :]
    var_r = T / (T - 1.0) ** 3 * ((w - w.mean(axis=0)) ** 2).sum(axis=0)
    off = ~np.eye(n, dtype=bool)
    den = float((R[off] ** 2).sum())
    lam = 1.0 if den <= 1e-12 else float(min(1.0, max(0.0, var_r[off].sum() / den)))
    Rs = (1 - lam) * R + lam * np.eye(n)
    return sd[:, None] * Rs * sd[None, :], lam


def reconcile_gain(S, method, E=None):
    """Matrix G (bottoms x nodes) with reconciled = S @ G @ base. G = (S'W^-1 S)^-1 S'W^-1 for the chosen W.
    method mint_shrink uses the residuals E (falls back to wls when fewer than MIN_RESID rows). Returns (G, info)."""
    n = S.shape[0]; used, lam = method, None
    if method == 'mint_shrink' and (E is None or len(E) < MIN_RESID): used = 'wls'
    if used == 'ols': W = np.eye(n)
    elif used == 'wls': W = np.diag(S.sum(axis=1))      # structural scaling: bottoms under each node
    else: W, lam = shrink_covariance(E)
    W = W + 1e-10 * float(np.trace(W)) / n * np.eye(n)
    Wi_S = np.linalg.solve(W, S)                        # W^-1 S
    G = np.linalg.solve(S.T @ Wi_S, Wi_S.T)
    return G, {'method': used, 'requested': method, 'shrinkage_lambda': None if lam is None else round(lam, 4),
               'residual_observations': 0 if E is None else int(len(E)), 'series': int(n), 'bottom_series': int(S.shape[1])}


def project(S, G, base):
    """Reconcile base forecasts (..., nodes) -> coherent non-negative (..., nodes): bottoms clipped at zero, then summed up."""
    b = np.maximum(np.asarray(base) @ G.T, 0.0)
    return b @ S.T


def coherence_gap(values, parent):
    """Largest |node - sum of its children| over all nodes with children; values is (nodes x ...)."""
    gap = 0.0
    kids = defaultdict(list)
    for i, pa in enumerate(parent):
        if pa is not None: kids[pa].append(i)
    for pa, ch in kids.items(): gap = max(gap, float(np.max(np.abs(values[pa] - values[ch].sum(axis=0)))))
    return gap


def fallback_base(y, steps, seed=0):
    """Mean-of-recent forecast for a node the engine cannot model (too short / feed gap); keeps the hierarchy complete."""
    y = np.asarray(y, float); w = min(len(y), 2 * MIN_REGIME['month']); yr = y[-w:]
    rng = np.random.default_rng(seed); recs = []
    for o in range(max(3, w // 2), w):
        m = Mean(yr[:o]); h = min(steps, w - o); pt = m.point(h); sd = m.sd * math.sqrt(1 + 1 / m.n)
        recs += [{'origin': o, 'step': k + 1, 'target': o + k, 'actual': float(yr[o + k]), 'point': float(pt[k]), 'sd': float(sd)} for k in range(h)]
    m = Mean(yr)
    return {'_pt': np.maximum(m.point(steps), 0), '_sd': np.full(steps, m.sd * math.sqrt(1 + 1 / m.n)), '_sims': np.maximum(m.simulate(steps, SIMS, rng), 0),
            '_recs': recs, '_offset': len(y) - w, '_yr': yr, '_lag': 1, '_floor': 0.5}


def _step1(s):
    return {s['_offset'] + r['target']: r for r in s['_recs'] if r['step'] == 1}


def reconcile_backtest(bases, S, parent, levels, method, steps):
    """Rolling-origin comparison on the engine's own backtest records: at each common origin the base forecasts are
    reconciled with residuals observed before that origin only; MASE (node's own in-sample scale) of base vs reconciled."""
    by = []
    for s in bases:
        d = defaultdict(dict)
        for r in s['_recs']: d[s['_offset'] + r['origin']][r['step']] = r
        by.append(d)
    origins = sorted(set.intersection(*[set(d) for d in by]))
    res = [{t: r['actual'] - r['point'] for t, r in _step1(s).items()} for s in bases]
    common = sorted(set.intersection(*[set(d) for d in res]))
    n = len(bases); base_err, rec_err, used = [[] for _ in range(n)], [[] for _ in range(n)], Counter()
    for g in origins:
        prior = [t for t in common if t < g][-MAX_RESID:]
        E = np.array([[res[i][t] for i in range(n)] for t in prior]) if prior else None
        G, info = reconcile_gain(S, method, E); used[info['method']] += 1
        h = min(steps, min(max(d[g]) for d in by))
        if h < 1: continue
        pt = np.array([[by[i][g][k]['point'] for i in range(n)] for k in range(1, h + 1)])
        act = np.array([[by[i][g][k]['actual'] for i in range(n)] for k in range(1, h + 1)])
        rec = project(S, G, np.maximum(pt, 0) if method == 'none' else pt)
        for i, s in enumerate(bases):
            sc = _mase_scale(s['_yr'][:g - s['_offset']], s['_lag'])
            base_err[i] += list(np.abs(act[:, i] - pt[:, i]) / sc); rec_err[i] += list(np.abs(act[:, i] - rec[:, i]) / sc)
    if not origins or not base_err[0]: return None
    bm = np.array([np.mean(e) for e in base_err]); rm = np.array([np.mean(e) for e in rec_err])
    lv = np.array(levels)
    return {'origins': len(origins), 'method_by_origin': dict(used), 'base_mase': round(float(bm.mean()), 4), 'reconciled_mase': round(float(rm.mean()), 4),
            'improved_or_matched': bool(rm.mean() <= bm.mean() + 1e-9),
            'by_level': {int(l): {'base_mase': round(float(bm[lv == l].mean()), 4), 'reconciled_mase': round(float(rm[lv == l].mean()), 4)} for l in sorted(set(levels))},
            'by_node': [{'base_mase': round(float(a), 4), 'reconciled_mase': round(float(b), 4)} for a, b in zip(bm, rm)]}


def forecast_hierarchy(periods, ys, paths, freq, horizon, method='mint_shrink', partials=None, booked=None, seed=0):
    """Forecast every node of a hierarchy (paths[0] must be the network, ()) with the single-series engine, then reconcile.
    ys are aligned to `periods`. Returns (series per node, reconciliation report)."""
    S, parent, levels, bottoms = hierarchy_structure(paths)
    partials = partials or [None] * len(paths); booked = booked or [None] * len(paths)
    series = [forecast_series(periods, ys[i], freq, horizon, partials[i], booked[i], seed=seed + i) for i in range(len(paths))]
    steps = horizon + (1 if partials[0] else 0)
    rec = {'method_requested': method, 'nodes': len(paths), 'levels': max(levels) + 1, 'bottom_series': len(bottoms)}
    if series[0]['status'] != 'forecast' and method != 'none':
        rec.update(applied=False, reason='the network total could not be forecast, so the hierarchy is left unreconciled: ' + series[0].get('reason', ''))
        return series, rec
    fallback = []
    for i, s in enumerate(series):
        if s['status'] == 'forecast' or method == 'none': continue
        fb = fallback_base(ys[i], steps, seed + i)
        s.update(status='forecast', model='mean_fallback', model_selection='fallback', base_status=s.get('reason'), forecast=[], interval_method={},
                 backtest={'origins': 0, 'errors': len(fb['_recs']), 'horizon': steps, 'baseline': 'naive', 'mase': {}, 'selected_mase': None,
                           'baseline_mase': None, 'skill_vs_baseline': None, 'coverage': {'80': {'conformal_sequential': None, 'evaluated': 0, 'model_simulation': None, 'errors': 0},
                                                                                            '95': {'conformal_sequential': None, 'evaluated': 0, 'model_simulation': None, 'errors': 0}}})
        s.setdefault('data_issues', []).append({'kind': 'fallback', 'periods': '', 'detail': 'too little modelable history for the series engine; '
                                                f'forecast from the mean of the recent periods so the hierarchy stays complete ({s.get("reason", "")})'})
        s.update(fb); fallback.append(i)
    live = [i for i, s in enumerate(series) if s['status'] == 'forecast']
    if method == 'none' or len(live) != len(paths):
        pt = np.array([s['_pt'] for s in series]) if len(live) == len(paths) else None
        rec.update(applied=False, reason='reconcile=none' if method == 'none' else 'some nodes have no forecast',
                   coherence={'assessable': pt is not None, 'max_abs_gap': None if pt is None else round(coherence_gap(pt, parent), 6)})
        return series, rec
    n = len(paths); PT = np.array([s['_pt'] for s in series]); SD = np.array([s['_sd'] for s in series])
    floor = 0.5
    firsts = [_step1(s) for s in series]
    common = sorted(set.intersection(*[set(d) for d in firsts]))[-MAX_RESID:]
    E = np.array([[firsts[i][t]['actual'] - firsts[i][t]['point'] for i in range(n)] for t in common]).reshape(len(common), n)
    Zs = np.array([[(firsts[i][t]['actual'] - firsts[i][t]['point']) / max(firsts[i][t]['sd'], floor) for i in range(n)] for t in common]).reshape(len(common), n)
    G, info = reconcile_gain(S, method, E)
    rng = np.random.default_rng(seed + 10_000)
    if len(common) >= MIN_RESID:   # joint bootstrap of conformal-style standardised residual rows keeps cross-node dependence
        idx = rng.integers(0, len(common), (SIMS, steps))
        B = PT.T[None] + Zs[idx] * np.maximum(SD, floor).T[None]
        interval_method = 'residual_bootstrap_projected'
    else:
        B = np.stack([s['_sims'][:SIMS] for s in series], axis=2); interval_method = 'model_simulation_projected'
    raw_pt = PT.T @ G.T                                  # (steps x bottoms) before clipping
    RP = project(S, G, PT.T).T                           # nodes x steps
    RS = project(S, G, B)                                # sims x steps x nodes
    q = {lv: np.quantile(RS, [(1 - lv) / 2, (1 + lv) / 2], axis=0) for lv in LEVELS}
    ev = reconcile_backtest(series, S, parent, levels, method, steps)
    k = periods[-1]; ks = []
    for _ in range(steps): k = next_period(k, freq); ks.append(k)
    for i, s in enumerate(series):
        s['base_forecast'] = [{'period': ks[j], 'point': round(float(PT[i, j]), 2)} for j in range(steps)]
        fc = []
        for j in range(steps):
            pt = float(RP[i, j])
            row = {'period': ks[j], 'point': round(pt, 2)}
            for lv, tag in ((0.8, '80'), (0.95, '95')):
                row['lo' + tag] = round(min(float(q[lv][0][j, i]), pt), 2); row['hi' + tag] = round(max(float(q[lv][1][j, i]), pt), 2)
            part = partials[i] or (partials[0] and {'period': partials[0]['period'], 'observed_so_far': 0.0})
            if partials[0] and ks[j] == partials[0]['period']: row.update(in_progress=True, observed_so_far=part['observed_so_far'])
            if booked[i] and booked[i].get(ks[j]): row['already_booked'] = booked[i][ks[j]]
            fc.append(row)
        s['forecast'] = fc; s['reconciled'] = True; s['interval_method'] = {lv: interval_method for lv in LEVELS}
        if ev: s['reconciliation'] = ev['by_node'][i]
    gap = coherence_gap(RP, parent)
    rounded = np.array([[r['point'] for r in s['forecast']] for s in series])
    rec.update(applied=True, reconciliation=info, interval_method=interval_method, fallback_nodes=fallback,
               coherence={'max_abs_gap': round(gap, 9), 'coherent': gap < 1e-6, 'max_abs_gap_rounded_to_2dp': round(coherence_gap(rounded, parent), 4),
                          'base_max_abs_gap': round(coherence_gap(PT, parent), 4)},
               non_negativity={'min_reconciled': round(float(RP.min()), 6), 'bottom_cells_set_to_zero': int((raw_pt < 0).sum()),
                               'rule': 'negative bottom values are set to zero and the hierarchy re-aggregated (coherence preserved)'},
               backtest=ev)
    return series, rec


# ---- capacity and staffing -------------------------------------------------------------------
def capacity_rows(fc, freq, p, measure):
    rows = []
    for i, r in enumerate(fc):
        days = period_days(r['period'], freq)
        row = {'period': r['period']}
        if measure == 'bed_days':
            occ = p['target_occupancy']
            row.update(average_census=round(r['point'] / days, 2), beds_at_target_occupancy=math.ceil(r['point'] / days / occ - 1e-9),
                       beds_at_target_occupancy_hi80=math.ceil(r['hi80'] / days / occ - 1e-9),
                       peak_day_beds=int(poisson.ppf(p['peak_quantile'], max(r['point'] / days, 1e-9))),
                       peak_day_beds_hi80=int(poisson.ppf(p['peak_quantile'], max(r['hi80'] / days, 1e-9))))
        if p['minutes_per_unit'] and p['fte_hours_per_week']:
            hours = p['fte_hours_per_week'] * days / 7
            row.update(workload_hours=round(r['point'] * p['minutes_per_unit'] / 60, 1),
                       fte=round(r['point'] * p['minutes_per_unit'] / 60 / hours, 2),
                       fte_hi80=round(r['hi80'] * p['minutes_per_unit'] / 60 / hours, 2))
        if len(row) > 1: rows.append(row)
    return rows


# ---- operator ----------------------------------------------------------------------------------
def execute(path, patient_ids, params, as_of=None):
    p = validate(params)
    path = Path(path)
    as_of = as_of or json.loads((path / 'run.json').read_text())['snapshot']['as_of']
    db = sqlite3.connect(f'file:{semantic_layer.ensure(path)}?mode=ro', uri=True)
    try:
        rows, flags = events(db, p['measure'], patient_ids, as_of)
        attrs = attributes(db) if {p['group_by'], *p['hierarchy']} & {'practice', 'payer'} else {}
    finally:
        db.close()
    freq, label = p['frequency'], MEASURES[p['measure']][2]
    recon = None
    if p['hierarchy'] and rows:
        series, table, capacity, recon = _hierarchy_series(rows, p, attrs, as_of)
        return _finish(p, path, as_of, label, freq, series, table, capacity, flags, recon)
    by_group = defaultdict(list)
    for r in rows:
        by_group['all'].append(r)
        if p['group_by'] != 'none': by_group[group_key(p['group_by'], p['measure'], r[1], r[2], attrs)].append(r)
    names = ['all'] + sorted((g for g in by_group if g != 'all'), key=lambda g: -len(by_group[g]))[:p['top']]
    series, table, capacity = [], [], []
    for gi, g in enumerate(names):
        periods, y, future, partial = build_series(by_group[g], freq, as_of)
        booked = dict(future) if p['measure'] == 'appointments' else None
        s = forecast_series(periods, y, freq, p['horizon'], partial, booked, seed=gi)
        for k in [k for k in s if k.startswith('_')]: s.pop(k)
        s['group'] = g
        if s['status'] == 'forecast':
            for r in s['forecast']: table.append({'group': g, **r})
            cap = capacity_rows(s['forecast'], freq, p, p['measure'])
            for r in cap: capacity.append({'group': g, **r})
            s['capacity'] = cap
        series.append(s)
    return _finish(p, path, as_of, label, freq, series, table, capacity, flags, None)


def _hierarchy_series(rows, p, attrs, as_of):
    """Nodes = network, then every prefix of the level keys (e.g. facility -> practice); the `top` largest bottom paths are kept
    and the rest are pooled into an 'other' bottom under their parent, so the network is always the sum of the kept bottoms."""
    levels, freq, measure = p['hierarchy'], p['frequency'], p['measure']
    pth = [tuple(group_key(l, measure, r[1], r[2], attrs) for l in levels) for r in rows]
    keep = {q for q, _ in sorted(Counter(pth).items(), key=lambda kv: (-kv[1], kv[0]))[:p['top']]}
    node_rows = defaultdict(list)
    for r, q in zip(rows, pth):
        q = q if q in keep else q[:-1] + ('other',)
        for d in range(len(q) + 1): node_rows[q[:d]].append(r)
    paths = [()]
    for d in range(1, len(levels) + 1):
        paths += sorted((q for q in node_rows if len(q) == d), key=lambda q: (-len(node_rows[q]), q))
    periods, ys, parts, books = None, [], [], []
    for q in paths:
        per, y, future, partial = build_series(node_rows[q], freq, as_of)
        if periods is None: periods = per
        cnt = dict(zip(per, y)); ys.append([cnt.get(k, 0.0) for k in periods])
        parts.append(partial); books.append(dict(future) if measure == 'appointments' else None)
    if not periods: return [], [], [], None
    series, recon = forecast_hierarchy(periods, ys, paths, freq, p['horizon'], p['reconcile'], parts, books)
    S, parent, lv, _ = hierarchy_structure(paths)
    table, capacity = [], []
    for i, (q, s) in enumerate(zip(paths, series)):
        for k in [k for k in s if k.startswith('_')]: s.pop(k)
        s.update(group='/'.join(q) or 'all', level=lv[i], parent=None if parent[i] is None else '/'.join(paths[parent[i]]) or 'all')
        if s['status'] == 'forecast':
            table += [{'group': s['group'], **r} for r in s['forecast']]
            cap = capacity_rows(s['forecast'], freq, p, measure)
            capacity += [{'group': s['group'], **r} for r in cap]; s['capacity'] = cap
    recon['hierarchy'] = list(levels); recon['nodes_by_level'] = dict(Counter(lv))
    return series, table, capacity, recon


def _finish(p, path, as_of, label, freq, series, table, capacity, flags, recon):
    missing, notes = [], []
    if p['staffing'] and not (p['minutes_per_unit'] and p['fte_hours_per_week']):
        missing.append('Staffing needs user-supplied ratios: minutes of staff time per unit and productive hours per FTE per week. '
                       'Agere does not invent staffing ratios.')
    open_stays = flags.get('stays_without_received_discharge', 0)
    if p['measure'] in ('bed_days', 'discharges') and open_stays:
        notes.append(f'{open_stays} stays have no received discharge date: they are flagged and excluded (never counted as 0 days), '
                     f'so {label} are a lower bound where they fall.')
    if flags.get('undated_visits') or flags.get('undated_appointments'):
        notes.append(f"{flags.get('undated_visits', 0) + flags.get('undated_appointments', 0)} undated records excluded.")
    method = (f"{label.capitalize()} per {freq} ({'Monday-start weeks' if freq == 'week' else 'calendar months'}) from the semantic layer; "
              f"only complete {freq}s through the snapshot ({as_of}) are modelled; an in-progress {freq} is forecast and its count so far shown. ")
    method += {'visit': 'Visits = encounter rows of that setting on their event date. ',
               'utilization_event':'Hospital admissions = governed all-source inpatient and observation events on their received event date. ',
               'appointment': 'Appointments counted on their scheduled date regardless of final status (demand, including no-shows); '
                              'appointments already booked for future periods are shown beside the forecast. ',
               'stay': ('Bed-days = nights of each hospital stay (same-day stays = 1) from admit to discharge. ' if p['measure'] == 'bed_days'
                        else 'Discharges counted on the received discharge date. ') +
                       'Stays without a received discharge are flagged and excluded, not treated as 0 days. '}[MEASURES[p['measure']][0]]
    method += ('Data checks: structural zero runs (feed gaps, monitor.zero_runs with expected counts from the local median, p < 0.001), '
               'single-period spikes (>= 3x the local median, Poisson tail p < 1e-6) and level breaks (penalised change points on '
               'Anscombe-transformed counts). Models are fitted only on the usable recent regime after the last gap (and after the latest '
               'level break when enough periods remain); spikes inside it are replaced by the local median. ')
    method += ('Candidates: naive / seasonal naive, drift, regime mean, ETS (simple, damped trend, damped Holt-Winters additive and '
               'multiplicative when two seasons are available) and negative-binomial log-linear GLMs (level, trend, Fourier seasonality). '
               + (f'Default model: Timestral-{timestral_sizes()[0]} (Mistral time-series foundation model, zero-shot; forecast median as '
                  'the point, its 21 quantiles as the predictive distribution), scored on the same backtest; the classic model with the '
                  'lowest backtest MASE is the fallback when it is unavailable. ' if timestral_sizes() else
                  'The model with the lowest rolling-origin backtest MASE is used. ')
               + 'The naive baseline MASE is always reported (selection on the same backtest is mildly optimistic). Intervals: normalised split-conformal on backtest errors (score = |error| / '
               'model predictive SD) when enough errors exist, else model simulation; empirical coverage is measured sequentially, each '
               'origin calibrated only on errors observed before it. ')
    if p['measure'] == 'bed_days':
        method += (f"Beds at target occupancy = bed-days / days / {p['target_occupancy']:g}; peak-day beds = the {p['peak_quantile']:.0%} "
                   'quantile of a Poisson daily census (M/G/infinity occupancy) at the forecast mean. ')
    if p['minutes_per_unit'] and p['fte_hours_per_week']:
        method += (f"Staffing = forecast units x {p['minutes_per_unit']:g} min / 60 / ({p['fte_hours_per_week']:g} productive h per FTE-week "
                   'x days/7), using the ratios supplied in the question. ')
    if recon and recon.get('applied'):
        r = recon['reconciliation']
        method += (f"Hierarchy {' > '.join(['network'] + p['hierarchy'])}: every node is forecast by the engine, then reconciled with "
                   f"{r['method'].replace('_', '-')} (Wickramasuriya, Athanasopoulos & Hyndman 2019: minimum-trace projection onto the coherent subspace"
                   + (f"; Schafer-Strimmer shrinkage of the one-step residual covariance, lambda {r['shrinkage_lambda']}" if r['shrinkage_lambda'] is not None else '')
                   + '), so children sum exactly to parents; negative bottom values are set to zero and re-aggregated. Intervals come from '
                   'conformal-standardised backtest residual rows resampled jointly across nodes and projected through the same map. ')
    elif recon: method += f"Hierarchy not reconciled ({recon['reason']}). "
    else: method += 'Groups forecast independently; group forecasts need not sum to the total.' if p['group_by'] != 'none' else ''
    out = {'schema': VERSION, 'parameters': p, 'as_of': as_of, 'measure_label': label, 'frequency': freq, 'series': series,
            'table': table, 'capacity': capacity, 'flags': {k: v for k, v in flags.items() if not k.startswith('open_stay_admit_')},
            'missing': missing, 'notes': notes, 'method': method}
    if recon: out['reconciliation'] = recon
    return out


def summary(report):
    p = report['parameters']; label, freq = report['measure_label'], report['frequency']
    main = report['series'][0] if report['series'] else None
    if not main or main['status'] != 'forecast':
        return f"No forecast of {label}: {main['reason'] if main else 'no data'}."
    fc = [r for r in main['forecast'] if not r.get('in_progress')] or main['forecast']
    total = sum(r['point'] for r in fc)
    bt = main['backtest']
    text = (f"{label[0].upper() + label[1:]} forecast for the next {len(fc)} {freq}s ({fc[0]['period']} to {fc[-1]['period']}): "
            f"about {fc[0]['point']:,.1f} in {fc[0]['period']} (80% interval {fc[0]['lo80']:,.1f}-{fc[0]['hi80']:,.1f})"
            + (f", {total:,.0f} in total" if len(fc) > 1 else '') + '. ')
    sk = bt['skill_vs_baseline']
    how = ('default foundation model' if main.get('model_selection') == 'default' else 'foundation-model challenger that beat the incumbent on the rolling-origin backtest' if main.get('model_selection') == 'challenger_backtest' else 'chosen by rolling-origin backtest')
    text += (f"Model {main['model'].replace('_', ' ')} ({how}): MASE {bt['selected_mase']:.2f} vs "
             f"{bt['baseline'].replace('_', ' ')} baseline {bt['baseline_mase']:.2f}"
             + (f" ({sk:+.0%} skill)" if sk is not None else '') + f" over {bt['errors']} forecast errors. ")
    cov = bt['coverage']['80']
    if cov['conformal_sequential'] is not None and cov['evaluated'] >= 10:
        text += f"80% intervals covered {cov['conformal_sequential']:.0%} of {cov['evaluated']} held-out values. "
    else:
        text += (f"Too few held-out values ({cov['evaluated']}) to measure interval coverage: the backtest is thin, "
                 "so treat the intervals as indicative. ")
    reg = main['regime']
    issues = [d for d in main['data_issues'] if d['kind'] in ('gap', 'spike')]
    if issues or reg['notes']:
        text += (f"Fitted on {reg['periods']} {freq}s from {reg['start']} only"
                 + (': ' + '; '.join(f"{d['kind']} {d['periods']}" for d in issues[:3]) + ' excluded' if issues else '') + '. ')
    cap = [c for c in main.get('capacity') or [] if c['period'] in {r['period'] for r in fc}]
    if cap and 'beds_at_target_occupancy' in cap[0]:
        c = cap[0]
        text += (f"Beds in {c['period']}: {c['beds_at_target_occupancy']} at {p['target_occupancy']:.0%} occupancy "
                 f"(average census {c['average_census']:g}; {c['peak_day_beds']} to cover the {p['peak_quantile']:.0%} peak day). ")
    if cap and 'fte' in cap[0]:
        text += f"Staffing in {cap[0]['period']}: {cap[0]['fte']:g} FTE (80% upper {cap[0]['fte_hi80']:g}). "
    for m in report['missing']: text += m + ' '
    rc = report.get('reconciliation')
    if rc and rc.get('applied'):
        bt2 = rc.get('backtest') or {}
        text += (f"Forecasts across {rc['nodes']} nodes were reconciled ({rc['reconciliation']['method'].replace('_', '-')}) so children sum to parents"
                 + (f"; rolling-origin MASE {bt2['base_mase']:.2f} unreconciled vs {bt2['reconciled_mase']:.2f} reconciled" if bt2 else '') + '. ')
    groups = [s for s in report['series'][1:] if s['status'] == 'forecast']
    if groups:
        text += 'By group, first period: ' + '; '.join(f"{s['group']} {s['forecast'][0]['point']:,.1f}" for s in groups[:5]) + '. '
    return text.strip()
