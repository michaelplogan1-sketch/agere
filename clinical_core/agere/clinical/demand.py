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
import calendar, json, math, sqlite3

import numpy as np
from scipy.stats import poisson

from . import monitor, semantic_layer
from ...signal_math import gaussian_change_points

VERSION = 'demand/1'
# measure -> (source, visit type concept, label)
MEASURES = {
    'ed_visits': ('visit', 'emergency_visit', 'ED visits'),
    'inpatient_admissions': ('visit', 'inpatient_admission', 'inpatient admissions'),
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
               'minutes_per_unit', 'fte_hours_per_week'}
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
    out = {'measure': p['measure'], 'frequency': freq, 'horizon': horizon, 'group_by': group_by, 'top': top,
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
    if source == 'visit':
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
    best = min(mase, key=lambda k: (round(mase[k], 6), list(mase).index(k)))
    recs = records[best]
    floor = 0.5
    scores = [_score(r, floor) for r in recs]
    factory = dict(candidates(len(yr), freq, seasonal))[best]
    model = factory(yr)
    rng = np.random.default_rng(seed + 1)
    pt = np.maximum(model.point(steps), 0); sims = np.maximum(model.simulate(steps, SIMS, rng), 0); sd = sims.std(axis=0)
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
    out.update(model=best, model_parameters=getattr(model, 'params', None), forecast=fc, interval_method=methods, seasonal_models_considered=seasonal,
               backtest={'origins': len(origins), 'errors': len(recs), 'horizon': steps, 'baseline': baseline,
                         'mase': {k: round(v, 3) for k, v in sorted(mase.items(), key=lambda kv: kv[1])},
                         'selected_mase': round(mase[best], 3), 'baseline_mase': round(mase[baseline], 3),
                         'skill_vs_baseline': round(1 - mase[best] / mase[baseline], 3) if mase[baseline] > 0 else None,
                         'coverage': coverage})
    out['_sims'] = sims
    return out


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
        attrs = attributes(db) if p['group_by'] in ('practice', 'payer') else {}
    finally:
        db.close()
    freq, label = p['frequency'], MEASURES[p['measure']][2]
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
        s.pop('_sims', None)
        s['group'] = g
        if s['status'] == 'forecast':
            for r in s['forecast']: table.append({'group': g, **r})
            cap = capacity_rows(s['forecast'], freq, p, p['measure'])
            for r in cap: capacity.append({'group': g, **r})
            s['capacity'] = cap
        series.append(s)
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
               'The model with the lowest rolling-origin backtest MASE is used; the naive baseline MASE is always reported (selection '
               'on the same backtest is mildly optimistic). Intervals: normalised split-conformal on backtest errors (score = |error| / '
               'model predictive SD) when enough errors exist, else model simulation; empirical coverage is measured sequentially, each '
               'origin calibrated only on errors observed before it. ')
    if p['measure'] == 'bed_days':
        method += (f"Beds at target occupancy = bed-days / days / {p['target_occupancy']:g}; peak-day beds = the {p['peak_quantile']:.0%} "
                   'quantile of a Poisson daily census (M/G/infinity occupancy) at the forecast mean. ')
    if p['minutes_per_unit'] and p['fte_hours_per_week']:
        method += (f"Staffing = forecast units x {p['minutes_per_unit']:g} min / 60 / ({p['fte_hours_per_week']:g} productive h per FTE-week "
                   'x days/7), using the ratios supplied in the question. ')
    method += 'Groups forecast independently; group forecasts need not sum to the total.' if p['group_by'] != 'none' else ''
    return {'schema': VERSION, 'parameters': p, 'as_of': as_of, 'measure_label': label, 'frequency': freq, 'series': series,
            'table': table, 'capacity': capacity, 'flags': {k: v for k, v in flags.items() if not k.startswith('open_stay_admit_')},
            'missing': missing, 'notes': notes, 'method': method}


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
    text += (f"Model {main['model'].replace('_', ' ')} chosen by rolling-origin backtest: MASE {bt['selected_mase']:.2f} vs "
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
    groups = [s for s in report['series'][1:] if s['status'] == 'forecast']
    if groups:
        text += 'By group, first period: ' + '; '.join(f"{s['group']} {s['forecast'][0]['point']:,.1f}" for s in groups[:5]) + '. '
    return text.strip()
