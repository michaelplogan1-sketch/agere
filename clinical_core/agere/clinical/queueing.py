"""`queueing` operation: queueing-theory staffing and bed-capacity sizing (Erlang C / A / B, M/M/c/K, square-root
staffing, SIPP with lag).

Answers "how many nurses/providers per hour block keep P(wait > 30 min) <= 10%?", "how many beds keep blocking <= 2%?",
"what does staffing look like if arrivals run 10% above forecast?".

Inputs
  * Arrivals by hour-of-week (168 slots, Monday 00:00 = slot 0) from the semantic layer: ED visits (visit,
    type_concept 'emergency_visit'), clinic visits, appointments (scheduled_date, kept) or hospital stays (admissions);
    or from explicit `arrivals` timestamps; or from an explicit `hourly_rates` vector. Optionally scaled to a
    demand-engine forecast (`demand_forecast: true`) or a supplied weekly volume (`forecast_weekly_arrivals`).
  * Service time from visit start_time/end_time (or stay admit/discharge times, else LOS days), or the user's
    `mean_service_minutes` (stated as an assumption).
  * Optional selected patient IDs and an inclusive arrival `window`, bounded by the snapshot date.
    A selected cohort models a dedicated queue; it does not estimate competing facility demand.
    Automatic forecasting currently requires matching ED events, cohort and snapshot without an additional
    organization/window restriction. A mismatched forecast remains an explicit unmet requirement.

Models
  * M/M/c Erlang C: P(wait), mean wait, utilization, P(wait > t).        * Erlang A (M/M/c+M) when an abandonment rate is given.
  * Square-root staffing c = a + beta*sqrt(a), beta from the Halfin-Whitt P(wait) relation (or supplied).
  * M/M/c/K bounded capacity (K = c is Erlang B: blocking for beds).      * SIPP: independent stationary M/M/c per hour block
    with an optional lag adjustment (offered load a(t) = E[lambda(t - S_e)], S_e the equilibrium residual service).

The minimum servers per block is the exact smallest c meeting the target under the stationary approximation; it is
a planning calculation, not an optimal roster (optimality_claimed False). Poisson arrivals and exponential service are
ASSUMPTIONS and are checked (index of dispersion, service-time CV) with warnings when violated.
"""
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
import json, math, sqlite3

import numpy as np

VERSION = 'queueing/2'
SOURCES = ('ed', 'clinic', 'appointment', 'beds', 'manual')
OBJECTIVES = ('wait', 'blocking')
DAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
CLINIC_TYPES = ('primary_care_visit', 'specialist_visit', 'urgent_care_visit')
DEFAULTS = {
    'source': None, 'organization_id': '', 'arrivals': None, 'service_minutes': None, 'hourly_rates': None,
    'mean_service_minutes': None, 'objective': None, 'wait_threshold_minutes': 30.0, 'target_wait_probability': 0.10,
    'target_blocking': 0.02, 'abandonment_rate_per_hour': None, 'servers': None, 'capacity_K': None,
    'arrival_rate_per_hour': None, 'qos_beta': None, 'lag_adjustment': True, 'arrival_scale': 1.0,
    'forecast_weekly_arrivals': None, 'demand_forecast': False, 'sensitivity_pct': 10.0, 'max_servers': 1000,
    'dispersion_warn': 1.25, 'cv_tolerance': 0.30, 'visit_types': None, 'window': None,
}
MIN_DISPERSION_Z = 3.0


# ---- validation --------------------------------------------------------------------------------
def _num(v, lo, hi, name, integer=False):
    if (type(v) is not int) if integer else (type(v) not in (int, float) or not math.isfinite(v)):
        raise ValueError(f'queueing {name} must be {"an integer" if integer else "a number"}')
    if not lo <= v <= hi: raise ValueError(f'queueing {name} {lo}..{hi}')
    return v if integer else float(v)


def validate(p):
    if p is None: p = {}
    if not isinstance(p, dict) or set(p) - set(DEFAULTS):
        raise ValueError('queueing parameters: ' + ', '.join(sorted(DEFAULTS)))
    out = {**DEFAULTS, **{k: v for k, v in p.items() if v is not None}}
    if out['source'] is not None and out['source'] not in SOURCES: raise ValueError(f'queueing source: {SOURCES}')
    if out['objective'] is not None and out['objective'] not in OBJECTIVES: raise ValueError(f'queueing objective: {OBJECTIVES}')
    if not isinstance(out['organization_id'], str) or len(out['organization_id']) > 80: raise ValueError('queueing organization_id: a string')
    if out['arrivals'] is not None and (not isinstance(out['arrivals'], (list, tuple)) or len(out['arrivals']) > 5_000_000):
        raise ValueError('queueing arrivals: a list of ISO timestamps')
    if out['service_minutes'] is not None:
        if not isinstance(out['service_minutes'], (list, tuple)): raise ValueError('queueing service_minutes: a list of minutes')
        out['service_minutes'] = [_num(x, 0.0, 1e7, 'service_minutes item') for x in out['service_minutes']]
    if out['hourly_rates'] is not None:
        hr = out['hourly_rates']
        if not isinstance(hr, (list, tuple)) or len(hr) not in (24, 168): raise ValueError('queueing hourly_rates: 24 (hour of day) or 168 (hour of week) arrivals per hour')
        hr = [_num(x, 0.0, 1e6, 'hourly_rates item') for x in hr]
        out['hourly_rates'] = hr * 7 if len(hr) == 24 else hr
    for k, lo, hi in (('mean_service_minutes', 0.01, 1e6), ('arrival_rate_per_hour', 0.0, 1e6), ('qos_beta', -5.0, 20.0),
                      ('abandonment_rate_per_hour', 0.0, 1e4)):
        if out[k] is not None: out[k] = _num(out[k], lo, hi, k)
    out['wait_threshold_minutes'] = _num(out['wait_threshold_minutes'], 0.0, 1e5, 'wait_threshold_minutes')
    out['target_wait_probability'] = _num(out['target_wait_probability'], 1e-6, 1.0, 'target_wait_probability')
    out['target_blocking'] = _num(out['target_blocking'], 1e-9, 1.0, 'target_blocking')
    out['arrival_scale'] = _num(out['arrival_scale'], 0.0, 100.0, 'arrival_scale')
    out['sensitivity_pct'] = _num(out['sensitivity_pct'], 0.0, 100.0, 'sensitivity_pct')
    out['dispersion_warn'] = _num(out['dispersion_warn'], 1.0, 100.0, 'dispersion_warn')
    out['cv_tolerance'] = _num(out['cv_tolerance'], 0.0, 5.0, 'cv_tolerance')
    out['max_servers'] = _num(out['max_servers'], 1, 5000, 'max_servers', True)
    if out['servers'] is not None: out['servers'] = _num(out['servers'], 1, 5000, 'servers', True)
    if out['capacity_K'] is not None: out['capacity_K'] = _num(out['capacity_K'], 1, 100000, 'capacity_K', True)
    if not isinstance(out['lag_adjustment'], bool) or not isinstance(out['demand_forecast'], bool):
        raise ValueError('queueing lag_adjustment and demand_forecast are true/false')
    f = out['forecast_weekly_arrivals']
    if f is not None:
        if isinstance(f, (int, float)): f = {'point': f}
        if not isinstance(f, dict) or 'point' not in f or set(f) - {'point', 'lo80', 'hi80'}:
            raise ValueError('queueing forecast_weekly_arrivals: a number or {point, lo80, hi80} arrivals per week')
        out['forecast_weekly_arrivals'] = {k: _num(v, 0.0, 1e9, f'forecast_weekly_arrivals {k}') for k, v in f.items()}
    if out['visit_types'] is not None and (not isinstance(out['visit_types'], (list, tuple)) or not all(isinstance(x, str) for x in out['visit_types'])):
        raise ValueError('queueing visit_types: a list of visit type_concept strings')
    if out['source'] is None:
        out['source'] = 'manual' if (out['arrivals'] is not None or out['hourly_rates'] is not None) else 'ed'
    if out['objective'] is None: out['objective'] = 'blocking' if out['source'] == 'beds' else 'wait'
    window = {'start':'', 'end':''} if out['window'] is None else out['window']
    if not isinstance(window, dict) or set(window) != {'start', 'end'}:
        raise ValueError('queueing window: inclusive start and end dates, or empty strings')
    for value in window.values():
        if not isinstance(value, str) or (value and date.fromisoformat(value).isoformat() != value):
            raise ValueError('queueing window dates must be YYYY-MM-DD or empty')
    if window['start'] and window['end'] and window['start'] > window['end']:
        raise ValueError('queueing window start must not follow end')
    out['window'] = dict(window)
    return out


# ---- Erlang formulas ---------------------------------------------------------------------------
def erlang_b(c, a):
    """Blocking probability of M/M/c/c with offered load a (stable recursion)."""
    if c < 0 or a < 0: raise ValueError('erlang_b needs c >= 0 and a >= 0')
    b = 1.0
    for k in range(1, c + 1): b = a * b / (k + a * b)
    return b


def erlang_c(c, a):
    """P(an arrival must wait) in M/M/c with offered load a = lambda/mu (1.0 when unstable)."""
    if a <= 0: return 0.0
    if c <= a: return 1.0
    b = erlang_b(c, a)
    rho = a / c
    return b / (1.0 - rho * (1.0 - b))


def mmc(lam, mu, c, t_hours=0.0):
    """Erlang C metrics. Times in hours. Unstable systems return stable False and no waits."""
    a = lam / mu
    out = {'model': 'M/M/c (Erlang C)', 'arrival_rate_per_hour': lam, 'service_rate_per_hour': mu, 'servers': c, 'offered_load': a,
           'utilization': (a / c) if c else None, 'stable': bool(c > a)}
    if not out['stable']:
        out.update(p_wait=1.0, mean_wait_hours=None, p_wait_gt_t=None, mean_queue=None)
        return out
    pw = erlang_c(c, a)
    gap = c * mu - lam
    out.update(p_wait=pw, mean_wait_hours=pw / gap, p_wait_gt_t=pw * math.exp(-gap * t_hours), mean_queue=pw * lam / gap)
    return out


def _erlang_a_dist(lam, mu, theta, c):
    """Stationary distribution of M/M/c+M (states c.. truncated) as (p vector over all states, N)."""
    a = lam / mu
    logs = [0.0]
    for n in range(1, c + 1): logs.append(logs[-1] + math.log(a) - math.log(n))
    n = c
    while True:
        n += 1
        logs.append(logs[-1] + math.log(lam) - math.log(c * mu + (n - c) * theta))
        if n > c + 20 and logs[-1] < max(logs) - 40: break
        if n > c + 20000: break
    arr = np.exp(np.array(logs) - max(logs))
    return arr / arr.sum()


def erlang_a(lam, mu, theta, c, t_hours=0.0):
    """M/M/c+M: exponential patience with rate theta. P(wait) = P(all servers busy), abandonment probability and
    P(virtual wait > t) (time an infinitely patient arrival would wait)."""
    if theta <= 0: return {**mmc(lam, mu, c, t_hours), 'model': 'M/M/c+M (Erlang A)', 'abandonment_rate_per_hour': 0.0,
                           'abandonment_probability': 0.0}
    p = _erlang_a_dist(lam, mu, theta, c)
    n = np.arange(len(p))
    q = np.maximum(n - c, 0)
    p_wait = float(p[c:].sum())
    e_q = float((q * p).sum())
    p_ab = theta * e_q / lam if lam > 0 else 0.0
    eff = lam * (1 - p_ab)
    out = {'model': 'M/M/c+M (Erlang A)', 'arrival_rate_per_hour': lam, 'service_rate_per_hour': mu, 'abandonment_rate_per_hour': theta,
           'servers': c, 'offered_load': lam / mu, 'utilization': eff / (c * mu), 'stable': True, 'p_wait': p_wait,
           'abandonment_probability': p_ab, 'mean_queue': e_q, 'mean_wait_hours': e_q / lam if lam > 0 else 0.0}
    out['p_wait_gt_t'] = _erlang_a_tail(p, c, mu, theta, t_hours, p_wait)
    return out


def _erlang_a_tail(p, c, mu, theta, t, p_wait):
    if p_wait <= 0: return 0.0
    if t <= 0: return p_wait
    w = p[c:] / p_wait  # weights over m = number ahead of an arrival that finds all servers busy
    M = len(w) - 1
    m = np.arange(M + 1)
    rate = c * mu + m * theta
    big = float(rate.max())
    stay, down = 1 - rate / big, rate / big
    lt = big * t
    # Poisson(lt) weights by uniformization
    jmax = int(lt + 12 * math.sqrt(lt + 1) + 30)
    surv = 0.0
    v = w.copy()
    logp = -lt
    for j in range(jmax + 1):
        if j > 0: logp += math.log(lt) - math.log(j)
        surv += math.exp(logp) * float(v.sum())
        nv = v * stay
        nv[:-1] += (v * down)[1:]
        v = nv
    return p_wait * min(max(surv, 0.0), 1.0)


def mmck(lam, mu, c, K):
    """M/M/c/K finite-capacity queue; K = c reduces to Erlang B (blocking for beds)."""
    if K < c: raise ValueError('capacity K must be >= servers')
    a = lam / mu
    if a <= 0: return {'model': 'M/M/c/K', 'servers': c, 'capacity': K, 'blocking_probability': 0.0, 'utilization': 0.0,
                       'mean_in_system': 0.0, 'mean_queue': 0.0, 'mean_wait_hours': 0.0, 'throughput_per_hour': 0.0,
                       'offered_load': 0.0, 'stable': True, 'arrival_rate_per_hour': lam, 'service_rate_per_hour': mu}
    logs = [0.0]
    for n in range(1, K + 1):
        logs.append(logs[-1] + math.log(a) - math.log(min(n, c)))
    arr = np.exp(np.array(logs) - max(logs))
    p = arr / arr.sum()
    n = np.arange(K + 1)
    block = float(p[K])
    thr = lam * (1 - block)
    lq = float((np.maximum(n - c, 0) * p).sum())
    return {'model': 'M/M/c/K' + (' (Erlang B)' if K == c else ''), 'arrival_rate_per_hour': lam, 'service_rate_per_hour': mu,
            'servers': c, 'capacity': K, 'offered_load': a, 'blocking_probability': block, 'throughput_per_hour': thr,
            'utilization': thr / (c * mu), 'mean_in_system': float((n * p).sum()), 'mean_queue': lq,
            'mean_wait_hours': lq / thr if thr > 0 else 0.0, 'stable': True}


# ---- square-root staffing (Halfin-Whitt) -------------------------------------------------------
def _phi(x): return math.exp(-x * x / 2) / math.sqrt(2 * math.pi)
def _Phi(x): return 0.5 * math.erfc(-x / math.sqrt(2))


def hw_wait_probability(beta):
    """Halfin-Whitt limit P(wait) = [1 + beta Phi(beta)/phi(beta)]^-1 (beta > 0)."""
    return 1.0 / (1.0 + beta * _Phi(beta) / _phi(beta))


def hw_beta(alpha):
    """QoS parameter beta giving limiting P(wait) = alpha (bisection)."""
    if not 0 < alpha < 1: raise ValueError('alpha must be in (0, 1)')
    lo, hi = 1e-9, 40.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if hw_wait_probability(mid) > alpha: lo = mid
        else: hi = mid
    return (lo + hi) / 2


def sqrt_staffing(a, beta):
    """c = ceil(a + beta sqrt(a)); the rule needs a > 0 and beta > 0 to be stable."""
    if a <= 0: return 0
    return max(math.ceil(a + beta * math.sqrt(a)), math.floor(a) + 1)


# ---- minimum servers ---------------------------------------------------------------------------
def meets(lam, mu, c, p):
    """Does c servers meet the objective at arrival rate lam? Returns (ok, metrics)."""
    t = p['wait_threshold_minutes'] / 60.0
    if p['objective'] == 'blocking':
        b = erlang_b(c, lam / mu)
        return b <= p['target_blocking'], {'blocking_probability': b, 'p_wait_gt_t': None, 'p_wait': None, 'utilization': (lam / mu / c) * (1 - b) if c else None, 'mean_wait_minutes': None}
    theta = p['abandonment_rate_per_hour']
    if theta:
        m = erlang_a(lam, mu, theta, c, t)
    else:
        m = mmc(lam, mu, c, t)
        if not m['stable']: return False, {}
    return m['p_wait_gt_t'] <= p['target_wait_probability'], {
        'p_wait': m['p_wait'], 'p_wait_gt_t': m['p_wait_gt_t'], 'utilization': m['utilization'], 'blocking_probability': None,
        'mean_wait_minutes': None if m['mean_wait_hours'] is None else m['mean_wait_hours'] * 60.0,
        **({'abandonment_probability': m['abandonment_probability']} if theta else {})}


def min_servers(lam, mu, p):
    """Smallest c meeting the objective, or (None, None) when the max_servers bound is reached."""
    if lam <= 0: return 0, {'p_wait': 0.0, 'p_wait_gt_t': 0.0, 'blocking_probability': 0.0, 'utilization': 0.0, 'mean_wait_minutes': 0.0}
    a = lam / mu
    c = 1 if p['objective'] == 'blocking' else max(int(math.floor(a)) + 1, 1)
    while c <= p['max_servers']:
        ok, met = meets(lam, mu, c, p)
        if ok: return c, met
        c += 1
    return None, None


# ---- arrival and service data ------------------------------------------------------------------
def _parse(ts, day=None):
    """Datetime from an ISO timestamp (wall clock; zone dropped) or HH:MM[:SS] combined with `day`."""
    if ts is None or ts == '': return None
    if isinstance(ts, datetime): return ts.replace(tzinfo=None)
    s = str(ts).strip()
    try:
        if len(s) <= 8 and ':' in s and day:
            return datetime.fromisoformat(f'{str(day)[:10]}T{s}')
        return datetime.fromisoformat(s.replace('Z', '+00:00')).replace(tzinfo=None)
    except ValueError:
        return None


def _is_date_only(ts):
    return ts is not None and len(str(ts).strip()) <= 10 and ':' not in str(ts)


def _load_semantic(db, p, patient_ids=None, as_of=None):
    """Read only the selected population's arrivals and completed service evidence."""
    flags = Counter()
    arrivals, service = [], []
    untimed = []
    org, src = p['organization_id'], p['source']
    window = p['window']
    def rows(table, columns, where='', bindings=()):
        clauses, args = ([where] if where else []), list(bindings)
        if patient_ids is not None:
            clauses.append('patient_id IN (SELECT value FROM json_each(?))')
            args.append(json.dumps(patient_ids))
        if org:
            clauses.append('organization_id = ?'); args.append(org)
        return db.execute(f'SELECT {columns} FROM {table}' + (' WHERE ' + ' AND '.join(clauses) if clauses else ''), args)

    def in_window(arrival):
        if arrival is None:
            flags['undated'] += 1; return False
        d = arrival.date().isoformat()
        return (not window['start'] or d >= window['start']) and (not window['end'] or d <= window['end'])

    def completed(end):
        return end is not None and (not as_of or end.date().isoformat() <= as_of)

    if src in ('ed', 'clinic'):
        types = ('emergency_visit',) if src == 'ed' else tuple(p['visit_types'] or CLINIC_TYPES)
        for st, en, ed, end_d in rows('visit', 'start_time, end_time, event_date, end_date',
                                    'type_concept IN (%s)' % ','.join('?' * len(types)), types):
            a = _parse(st, ed) or _parse(ed)
            if not in_window(a): continue
            if a.date().isoformat() != str(ed or '')[:10]: flags['event_clock_mismatch'] += 1
            if ':' not in str(st or ''): flags['date_only'] += 1; untimed.append(a); continue
            arrivals.append(a)
            b = _parse(en, end_d or ed)
            if completed(b) and ':' in str(en or ''): service.append((b - a).total_seconds() / 60.0)
    elif src == 'appointment':
        for d, kept in rows('appointment', 'scheduled_date, kept'):
            if not kept: continue
            a = _parse(d)
            if not in_window(a): continue
            if _is_date_only(d): flags['date_only'] += 1; untimed.append(a); continue
            arrivals.append(a)
    elif src == 'beds':
        for ad, dd, known, at, dt, los in rows('stay', 'admit_date, discharge_date, discharge_known, admit_time_utc, discharge_time_utc, los_days'):
            a = _parse(at) or _parse(ad)
            if not in_window(a): continue
            if not at: flags['date_only'] += 1; untimed.append(a)
            else: arrivals.append(a)
            b = _parse(dt)
            # A later discharge/LOS must not reveal service duration at an earlier snapshot.
            if as_of and (not known or not completed(b or _parse(dd))): continue
            if completed(b) and at: service.append((b - a).total_seconds() / 60.0)
            elif known and dd and ad:
                x, y = _parse(ad), _parse(dd)
                if x and y: service.append(max((y - x).days, 0) * 1440.0); flags['los_in_whole_days'] += 1
            elif los not in (None, ''): service.append(float(los) * 1440.0); flags['los_in_whole_days'] += 1
    return arrivals, service, flags, untimed


def hourly_profile(arrivals, window=None):
    """Arrival rate per hour for each of 168 hour-of-week slots, from datetimes; also dispersion pieces.

    rate_slot = arrivals in the slot / calendar occurrences of that weekday in the observed range. Counts per
    (date, hour) are zero-filled so variance across occurrences of a slot is measured honestly."""
    if not arrivals: return None
    days = [a.date() for a in arrivals]
    window = window or {}
    first = date.fromisoformat(window['start']) if window.get('start') else min(days)
    last = date.fromisoformat(window['end']) if window.get('end') else max(days)
    n_days = (last - first).days + 1
    occ = Counter((first + timedelta(days=i)).weekday() for i in range(n_days))
    counts = Counter((a.date(), a.hour) for a in arrivals)
    per_slot = defaultdict(list)  # slot -> counts for each calendar occurrence
    for i in range(n_days):
        d = first + timedelta(days=i)
        for h in range(24):
            per_slot[d.weekday() * 24 + h].append(counts.get((d, h), 0))
    rates = [sum(per_slot[s]) / len(per_slot[s]) if per_slot[s] else 0.0 for s in range(168)]
    return {'rates': rates, 'first': first, 'last': last, 'days': n_days, 'weeks': n_days / 7.0, 'per_slot': per_slot,
            'occurrences_min': min(occ.get(d, 0) for d in range(7))}


def dispersion(per_slot, min_occ=2):
    """Pooled index of dispersion (variance/mean of counts across occurrences of the same hour-of-week slot) with a
    chi-square z-score; Poisson arrivals give 1."""
    num = den = df = chi = 0.0
    used = 0
    for counts in per_slot.values():
        n = len(counts)
        m = sum(counts) / n if n else 0.0
        if n < min_occ or m <= 0: continue
        ss = sum((x - m) ** 2 for x in counts)
        num += ss; den += (n - 1) * m; df += n - 1; chi += ss / m; used += 1
    if df < 30 or den <= 0: return None
    return {'index': num / den, 'z': (chi - df) / math.sqrt(2 * df), 'slots_used': used, 'degrees_of_freedom': int(df)}


def service_cv(minutes):
    xs = [x for x in minutes if x is not None and 0 < x <= 20160]
    if not xs: return None
    m = sum(xs) / len(xs)
    sd = (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5 if len(xs) > 1 else None
    return {'n': len(xs), 'mean_minutes': m, 'sd_minutes': sd, 'cv': sd / m if sd is not None else None, 'median_minutes': float(np.median(xs))}


# ---- SIPP with lag -----------------------------------------------------------------------------
def lagged_offered_load(rates, mu):
    """Offered load per hour block with the exponential lag: a_h = integral lambda(T - s) exp(-mu s) ds at the block
    midpoint T = h + 0.5, treating the weekly rate vector as piecewise constant and periodic. For constant lambda
    this is lambda/mu."""
    n = len(rates)
    horizon = int(min(max(math.ceil(30.0 / mu), 2), 500 * n)) + 1  # exp(-30) of the lag mass is beyond this
    out = []
    for h in range(n):
        tot = rates[h] * (1 - math.exp(-0.5 * mu)) / mu
        for k in range(1, horizon + 1):
            tot += rates[(h - k) % n] * (math.exp(-mu * (k - 0.5)) - math.exp(-mu * (k + 0.5))) / mu
        out.append(tot)
    return out


def sipp(rates, mu, p, lag=True, scale=1.0):
    """Stationary independent period-by-period staffing for each hour block."""
    r = [x * scale for x in rates]
    loads = lagged_offered_load(r, mu) if lag else [x / mu for x in r]
    rows, total = [], 0
    for s, (rate, a) in enumerate(zip(r, loads)):
        lam_eff = a * mu
        c, met = min_servers(lam_eff, mu, p)
        rows.append({'day': DAYS[s // 24], 'hour': s % 24, 'slot': s, 'arrival_rate_per_hour': round(rate, 6), 'offered_load': round(a, 6),
                     'servers': c, **({k: (None if v is None else round(v, 6)) for k, v in (met or {}).items()})})
        total += c if c is not None else 0
    return rows, total


def _staffing_summary(rows, total):
    ok = [r['servers'] for r in rows if r['servers'] is not None]
    return {'total_server_hours_per_week': total, 'peak_servers': max(ok) if ok else None,
            'peak_hour': next((f"{r['day']} {r['hour']:02d}:00" for r in rows if r['servers'] == max(ok)), None) if ok else None,
            'blocks_unmet': sum(1 for r in rows if r['servers'] is None)}


# ---- demand engine integration -----------------------------------------------------------------
def _demand_forecast(path, p, patient_ids=None, as_of=None):
    from . import demand
    # Other mappings are not equivalent: kept vs scheduled appointments, stay vs
    # visit admissions, or combined outpatient types vs primary-care visits.
    if p['source'] != 'ed' or p['organization_id'] or any(p['window'].values()) or p['arrivals'] is not None or p['hourly_rates'] is not None:
        return None, 'Automatic demand forecast cannot match this source, organization or event window; provide an explicitly scoped weekly volume.'
    measure = 'ed_visits'
    try:
        rep = demand.execute(path, patient_ids, {'measure': measure, 'frequency': 'week', 'horizon': 1}, as_of)
        s = rep['series'][0]
        if s.get('status') != 'forecast': return None, f"demand engine returned no forecast: {s.get('reason')}"
        fc = next((r for r in s['forecast'] if not r.get('in_progress')), s['forecast'][0])
        return {'point': fc['point'], 'lo80': fc.get('lo80'), 'hi80': fc.get('hi80'), 'period': fc.get('period'), 'measure': measure}, None
    except Exception as e:  # the demand engine is optional input; never fail the staffing answer on it
        return None, f'demand forecast unavailable ({type(e).__name__}: {e}).'


# ---- execute -----------------------------------------------------------------------------------
def _connect(obj):
    """Semantic layer path / stack object / open sqlite connection -> (connection, path, should_close)."""
    if obj is None: return None, None, False
    if isinstance(obj, sqlite3.Connection): return obj, None, False
    path = getattr(obj, 'semantic_path', None) or (obj.get('semantic_path') if isinstance(obj, dict) else None) or obj
    from . import semantic_layer
    return sqlite3.connect(f'file:{semantic_layer.ensure(Path(path))}?mode=ro', uri=True), Path(path), True


def execute(stack_or_semantic, params=None, *, patient_ids=None, as_of=None):
    p = validate(params)
    forecast_params = dict(p)
    if as_of:
        if date.fromisoformat(as_of).isoformat() != as_of: raise ValueError('queueing as_of must be YYYY-MM-DD')
        if any(v > as_of for v in p['window'].values() if v): raise ValueError('queueing window exceeds snapshot as_of')
        p['window'] = {**p['window'], 'end': p['window']['end'] or as_of}
    if patient_ids is not None:
        if not isinstance(patient_ids, (list, tuple, set, frozenset)) or any(not isinstance(x, str) for x in patient_ids):
            raise ValueError('queueing patient_ids must be a sequence of patient IDs')
        patient_ids = sorted(set(patient_ids))
    manual = p['arrivals'] is not None or p['hourly_rates'] is not None
    scope = {'basis':'user_supplied_scenario' if manual else 'received_records',
             'population':'selected' if patient_ids is not None else 'all_received',
             'patient_count':len(patient_ids) if patient_ids is not None else None,
             'patient_linkage_verified':not manual, 'organization_id':p['organization_id'],
             'window':dict(p['window']), 'as_of':as_of}
    warnings, assumptions, notes = [], [], []
    if patient_ids is not None:
        assumptions.append('Capacity models a dedicated queue for the selected population; shared facility demand from other patients is excluded.')
    if manual:
        assumptions.append('User-supplied arrivals/rates and service durations are scenario assumptions; patient linkage is not verified.')
    def finish(report):
        report['input_scope'] = scope
        return report
    src, objective = p['source'], p['objective']
    if patient_ids == []:
        return finish(_insufficient(p, src, warnings, assumptions, 'selected population is empty'))
    db, path, close = _connect(stack_or_semantic)
    arrivals, service, flags, untimed = [], [], Counter(), []
    supplied_service = False
    try:
        if p['arrivals'] is not None:
            arrivals = [d for d in (_parse(x) for x in p['arrivals']) if d is not None]
            if len(arrivals) < len(p['arrivals']): warnings.append(f"{len(p['arrivals']) - len(arrivals)} arrival timestamps could not be parsed and were dropped.")
            arrivals = [d for d in arrivals if (not p['window']['start'] or d.date().isoformat() >= p['window']['start'])
                        and (not p['window']['end'] or d.date().isoformat() <= p['window']['end'])]
            service = list(p['service_minutes'] or [])
            supplied_service = bool(service)
        elif p['hourly_rates'] is None:
            if db is None: raise ValueError('queueing needs a semantic layer, explicit arrivals or hourly_rates')
            try:
                arrivals, service, flags, untimed = _load_semantic(db, p, patient_ids, as_of)
            except sqlite3.Error as e:
                raise ValueError(f'queueing could not read the semantic layer for source {src}: {e}')
    finally:
        if close and db is not None: db.close()
    if p['service_minutes'] is not None and not service:
        service = list(p['service_minutes']); supplied_service = bool(service)

    # arrival rates by hour of week
    profile = hourly_profile(arrivals + untimed if (untimed and len(arrivals) < len(untimed)) else arrivals, p['window']) if (arrivals or untimed) else None
    rate_source = None
    date_only_mode = bool(untimed) and len(arrivals) < len(untimed)
    if p['hourly_rates'] is not None:
        rates, rate_source = list(p['hourly_rates']), 'user-supplied hourly_rates'
    elif profile:
        n_used = len(arrivals) + (len(untimed) if date_only_mode else 0)
        rates, rate_source = list(profile['rates']), f"{n_used} arrivals over {profile['days']} days ({profile['first']} to {profile['last']})"
        if date_only_mode:
            mean_daily = [sum(profile['rates'][d * 24:(d + 1) * 24]) for d in range(7)]
            rates = [mean_daily[s // 24] / 24.0 for s in range(168)]
            warnings.append('Most arrival timestamps carry dates only, so the hour-of-day pattern is unknown: day-of-week volume is spread evenly over 24 hours. Peak-hour staffing is understated.')
            assumptions.append('Hour-of-day arrivals are uniform within each day (received data are date-only).')
        elif untimed:
            # arrivals without a time of day follow the hour-of-week shape of the timed ones
            factor = (len(arrivals) + len(untimed)) / len(arrivals)
            rates = [x * factor for x in rates]
            warnings.append(f"{len(untimed)} of {len(arrivals) + len(untimed)} arrivals have no time of day; their volume is allocated in proportion to the hour-of-week pattern of the timed arrivals (rates scaled by {factor:.3f}). The Poisson check uses timed arrivals only.")
            assumptions.append('Arrivals without a time of day follow the same hour-of-week distribution as timed arrivals.')
        if profile['weeks'] < 4:
            warnings.append(f"Only {profile['weeks']:.1f} weeks of arrivals: hour-of-week rates are noisy.")
    else:
        return finish(_insufficient(p, src, warnings, assumptions, 'no arrivals found in the requested population/source/window' if db is not None or p['arrivals'] is not None else 'no data'))

    # service time
    cv = service_cv(service) if service else None
    if cv:
        mean_min, service_source = cv['mean_minutes'], f"{cv['n']} " + ('user-provided durations' if supplied_service else 'observed durations')
    elif p['mean_service_minutes']:
        mean_min, service_source = p['mean_service_minutes'], 'user-provided mean_service_minutes'
        assumptions.append(f"Mean service time {mean_min:g} minutes is user-provided; no start/end durations were available to estimate it.")
    else:
        # Missing service duration prevents staffing, not observation of demand.
        # Keep the actual arrival scope and scalar arithmetic available to the
        # shared analyst without substituting another event source or duration.
        observations = {
            'source': src, 'observation_kind': 'arrival_baseline_without_service_time',
            'arrivals_used': len(arrivals) + len(untimed) if profile else None,
            'timed_arrivals': len(arrivals) if profile else None,
            'date_only_arrivals': len(untimed) if profile else None,
            'first_arrival_date': min(d.date().isoformat() for d in arrivals + untimed) if profile else None,
            'last_arrival_date': max(d.date().isoformat() for d in arrivals + untimed) if profile else None,
            'rate_window_start_date': profile['first'].isoformat() if profile else None,
            'rate_window_end_date': profile['last'].isoformat() if profile else None,
            'window_start': p['window']['start'] or None, 'window_end': p['window']['end'] or None,
            'weeks_observed': round(profile['weeks'], 4) if profile else None,
            'arrival_rate_source': rate_source,
            'weekly_arrivals_base': round(sum(rates), 4),
            'arrival_scale_applied': p['arrival_scale'],
            'weekly_arrivals_scaled': round(sum(rates) * p['arrival_scale'], 4),
            'scaling_basis': 'Arrival-scale arithmetic only; no demand forecast or staffing estimate.',
            'all_arrivals_timed': not bool(untimed) if profile else None,
            'undated_arrivals': flags['undated'] if profile else None,
            'usable_service_observations': 0, 'mean_service_minutes': None,
            'supplied_servers': p['servers'], 'staffing_established': False}
        return finish(_insufficient(p, src, warnings, assumptions,
            'no service times observed and no mean_service_minutes provided', observations=observations))
    if cv and p['mean_service_minutes'] and abs(p['mean_service_minutes'] - cv['mean_minutes']) > 1e-9:
        mean_min, service_source = p['mean_service_minutes'], 'user-provided mean_service_minutes (overrides observed)'
        assumptions.append(f"Mean service time {mean_min:g} minutes is user-provided and overrides the observed mean of {cv['mean_minutes']:.1f}.")
    mu = 60.0 / mean_min

    # forecast / scaling
    scale = p['arrival_scale']
    weekly_hist = sum(rates)
    forecast = p['forecast_weekly_arrivals']
    unmet = []
    if p['demand_forecast'] and forecast is None and path is not None:
        if flags['event_clock_mismatch']:
            forecast, err = None, 'Automatic forecast uses event dates that disagree with the observed arrival timestamps.'
        else:
            forecast, err = _demand_forecast(path, forecast_params, patient_ids, as_of)
        if err: warnings.append(err); unmet.append(err)
    elif p['demand_forecast'] and forecast is None:
        err = 'Automatic demand forecast needs a semantic-layer path; historical capacity only.'
        warnings.append(err); unmet.append(err)
    forecast_info = None
    if forecast and weekly_hist > 0:
        f_scale = forecast['point'] / weekly_hist
        scale *= f_scale
        forecast_info = {'weekly_arrivals_forecast': forecast['point'], 'weekly_arrivals_history': round(weekly_hist, 4), 'scale_applied': round(f_scale, 6),
                         **({'lo80': forecast['lo80'], 'hi80': forecast['hi80'], 'scale_lo80': round(forecast['lo80'] / weekly_hist, 6) if forecast.get('lo80') is not None else None,
                             'scale_hi80': round(forecast['hi80'] / weekly_hist, 6) if forecast.get('hi80') is not None else None} if 'lo80' in forecast else {})}
        assumptions.append('Arrival rates by hour-of-week are rescaled so the weekly total equals the supplied/forecast weekly volume; the within-week shape is the historical shape.')
    elif forecast: warnings.append('Weekly arrival history is zero; the forecast could not be applied as a scale.')
    rates_scaled = [x * scale for x in rates]

    # assumption checks
    checks = {'poisson_arrivals': {'status': 'not_testable', 'reason': 'needs timestamped arrivals over several weeks'},
              'exponential_service': {'status': 'not_testable', 'reason': 'needs observed durations'}}
    if profile:
        d = dispersion(profile['per_slot'])
        if d:
            bad = d['index'] > p['dispersion_warn'] and d['z'] > MIN_DISPERSION_Z
            checks['poisson_arrivals'] = {'status': 'violated' if bad else 'consistent', 'index_of_dispersion': round(d['index'], 4), 'z': round(d['z'], 2),
                                          'slots_used': d['slots_used'], 'degrees_of_freedom': d['degrees_of_freedom'], 'poisson_value': 1.0}
            if bad: warnings.append(f"Arrivals are overdispersed (index of dispersion {d['index']:.2f} vs 1.0 for Poisson, z={d['z']:.1f}): Erlang results understate waiting and the staffing needed; treat them as optimistic.")
            elif d['index'] < 1 / p['dispersion_warn'] and d['z'] < -MIN_DISPERSION_Z:
                warnings.append(f"Arrivals are underdispersed (index {d['index']:.2f}); scheduled demand is smoother than Poisson so Erlang results are conservative.")
        else:
            checks['poisson_arrivals']['reason'] = 'fewer than 30 degrees of freedom across hour-of-week slots (too little history)'
            warnings.append('Too little history to test the Poisson-arrival assumption (index of dispersion).')
    if cv and cv['cv'] is not None:
        bad = cv['n'] >= 30 and abs(cv['cv'] - 1.0) > p['cv_tolerance']
        checks['exponential_service'] = {'status': 'violated' if bad else ('consistent' if cv['n'] >= 30 else 'thin_data'), 'cv': round(cv['cv'], 4), 'n': cv['n'], 'exponential_value': 1.0,
                                         'median_minutes': round(cv['median_minutes'], 3)}
        if bad: warnings.append(f"Service times are not exponential (CV {cv['cv']:.2f} vs 1.0, n={cv['n']}): "
                                + ('lower variability than exponential makes Erlang waits conservative' if cv['cv'] < 1 else 'higher variability makes Erlang waits optimistic') + '. See allen_cunneen_mean_wait_minutes for an M/G/c adjustment of the mean wait.')
    else: warnings.append('Service-time shape (CV) could not be checked; exponential service is assumed.')

    # lag
    lag = p['lag_adjustment']
    rows, total = sipp(rates, mu, p, lag=lag, scale=scale)
    summary = _staffing_summary(rows, total)
    unlag_rows, unlag_total = (sipp(rates, mu, p, lag=False, scale=scale) if lag else (rows, total))
    peak_rate_slot = max(range(168), key=lambda s: rates_scaled[s])
    peak_staff = [s for s, r in enumerate(rows) if r['servers'] == summary['peak_servers']] if summary['peak_servers'] is not None else []

    # sensitivity
    sens = []
    pct = p['sensitivity_pct'] / 100.0
    for f in (1 - pct, 1.0, 1 + pct):
        r2, t2 = sipp(rates, mu, p, lag=lag, scale=scale * f)
        s2 = _staffing_summary(r2, t2)
        sens.append({'arrival_scale_factor': round(f, 6), 'total_server_hours_per_week': t2, 'peak_servers': s2['peak_servers'], 'blocks_unmet': s2['blocks_unmet'],
                     'change_vs_base_pct': None if not total else round(100.0 * (t2 - total) / total, 3)})

    # single-point models
    peak_lam = p['arrival_rate_per_hour'] if p['arrival_rate_per_hour'] is not None else (
        (sum(rates_scaled) / 168.0) if objective == 'blocking' else max(rates_scaled))
    lam_basis = 'user-provided arrival_rate_per_hour' if p['arrival_rate_per_hour'] is not None else ('weekly mean arrival rate' if objective == 'blocking' else 'peak hour-of-week arrival rate')
    point = _single_point(peak_lam, mu, p, cv, lam_basis)

    ac = None
    if cv and cv['cv'] is not None and point.get('erlang_c') and point['erlang_c'].get('mean_wait_minutes') is not None:
        ac = round(point['erlang_c']['mean_wait_minutes'] * (1.0 + cv['cv'] ** 2) / 2.0, 4)
    point['allen_cunneen_mean_wait_minutes'] = ac

    assumptions += [
        'Arrivals are Poisson within each hour block (rate = historical mean for that hour of week) and service times are exponential; both are checked in assumption_checks.',
        f"Service rate mu = {mu:.4g}/h (mean {mean_min:.2f} min; {service_source}). Servers are identical and work the whole block; breaks, handoffs, acuity mix and priority classes are not modelled.",
        ('SIPP: each hour block is solved as an independent stationary system' + (' with the offered load lagged by the exponential service time (a(t) = E[lambda(t - S_e)]), so staffing peaks after arrivals peak.' if lag else '; no lag adjustment, so staffing peaks with arrivals and understates load just after a surge.')),
        ('Objective: ' + (f"blocking probability (Erlang B, K = c) <= {p['target_blocking']:.2%}." if objective == 'blocking' else
                         f"P(wait > {p['wait_threshold_minutes']:g} min) <= {p['target_wait_probability']:.1%}" + (f" with exponential patience (abandonment rate {p['abandonment_rate_per_hour']:g}/h, Erlang A virtual wait)." if p['abandonment_rate_per_hour'] else ' (Erlang C).'))),
        'Targets, abandonment rate and capacity K are the user\'s or stated defaults, not clinical standards.',
    ]
    if src == 'beds': assumptions.append('Beds: arrival = admission, service = length of stay, each patient occupies one bed, blocked patients are lost (no boarding queue).')
    if flags['los_in_whole_days']: warnings.append(f"{flags['los_in_whole_days']} stays only have whole-day length of stay; hourly occupancy is approximate.")
    if flags['undated']: notes.append(f"{flags['undated']} records without a usable date were excluded.")
    if summary['blocks_unmet']: warnings.append(f"{summary['blocks_unmet']} hour blocks could not meet the target within max_servers={p['max_servers']}.")
    if lag and peak_staff and peak_rate_slot not in peak_staff:
        notes.append('Peak staffing falls after the peak arrival hour because of the service-time lag.')

    inferred = 'descriptive_model' if (cv and checks['poisson_arrivals']['status'] != 'not_testable') else 'assumption_based'
    answer = {
        'objective': objective, 'source': src,
        'inputs': {'arrival_rate_source': rate_source, 'arrivals_used': len(arrivals) + len(untimed), 'date_only_arrivals': len(untimed), 'weeks_observed': round(profile['weeks'], 3) if profile else None,
                   'arrival_scale_applied': round(scale, 6), 'weekly_arrivals_base': round(weekly_hist, 4), 'weekly_arrivals_planned': round(sum(rates_scaled), 4),
                   'mean_service_minutes': round(mean_min, 4), 'service_source': service_source, 'service_rate_per_hour': round(mu, 6),
                   'peak_arrival_hour': f"{DAYS[peak_rate_slot // 24]} {peak_rate_slot % 24:02d}:00", 'peak_arrival_rate_per_hour': round(rates_scaled[peak_rate_slot], 6),
                   'arrival_rates_per_hour': [round(x, 6) for x in rates_scaled], 'forecast': forecast_info},
        'single_point': point,
        'staffing': {'lag_adjustment': lag, 'by_hour_of_week': rows, **summary,
                     'by_hour_of_day_peak': _by_hour_of_day(rows),
                     'without_lag': None if not lag else {'total_server_hours_per_week': unlag_total, **{k: v for k, v in _staffing_summary(unlag_rows, unlag_total).items() if k != 'total_server_hours_per_week'}}},
        'sensitivity': sens,
        'assumption_checks': checks,
    }
    return finish({'schema': VERSION, 'answer': answer, 'warnings': warnings, 'assumptions': assumptions, 'notes': notes, 'inference_status': inferred,
            'unmet_requirements': unmet,
            'optimality_claimed': False, 'parameters': {k: v for k, v in p.items() if k not in ('arrivals', 'service_minutes')},
            'method': ('Hour-of-week arrival rates from timestamped arrivals; Erlang C (stable Erlang B recursion), Erlang A (birth-death with truncation, virtual wait by uniformization), '
                       'Halfin-Whitt square-root staffing, M/M/c/K, SIPP with exponential lag; exact minimum servers by search; index of dispersion and service CV checks.'),
            'limitations': ['Planning approximation under Poisson/exponential assumptions; not an optimal roster and not a validated clinical standard.',
                            'Synthetic data only; no operational validation.']})


def _by_hour_of_day(rows):
    """Peak servers over the week for each hour of day."""
    best = defaultdict(int)
    for r in rows:
        if r['servers'] is not None: best[r['hour']] = max(best[r['hour']], r['servers'])
    return [best[h] for h in range(24)]


def _single_point(lam, mu, p, cv, basis):
    t = p['wait_threshold_minutes'] / 60.0
    a = lam / mu
    out = {'arrival_rate_per_hour': round(lam, 6), 'arrival_rate_basis': basis, 'offered_load': round(a, 6), 'service_rate_per_hour': round(mu, 6)}
    c_opt, _ = min_servers(lam, mu, p)
    c = p['servers'] if p['servers'] is not None else c_opt
    out['servers'] = c
    out['servers_basis'] = 'user-provided servers' if p['servers'] is not None else 'minimum meeting the objective'
    out['min_servers_for_objective'] = c_opt
    if c is None or lam <= 0:
        out.update(erlang_c=None, erlang_a=None, mmck=None, square_root=None)
        return out
    r = lambda d: None if d is None else {k: (round(v, 6) if isinstance(v, float) else v) for k, v in d.items()}
    ec = mmc(lam, mu, c, t)
    if ec['mean_wait_hours'] is not None: ec['mean_wait_minutes'] = ec['mean_wait_hours'] * 60.0
    out['erlang_c'] = r(ec)
    th = p['abandonment_rate_per_hour']
    if th:
        ea = erlang_a(lam, mu, th, c, t)
        ea['mean_wait_minutes'] = ea['mean_wait_hours'] * 60.0
        out['erlang_a'] = r(ea)
    else: out['erlang_a'] = None
    K = p['capacity_K'] if p['capacity_K'] is not None else c
    if K < c: K = c
    out['mmck'] = r(mmck(lam, mu, c, K))
    out['erlang_b_blocking_probability'] = round(erlang_b(c, a), 8)
    # square-root staffing
    alpha = p['target_wait_probability']
    beta = p['qos_beta'] if p['qos_beta'] is not None else hw_beta(alpha)
    c_sr = sqrt_staffing(a, beta) if beta > 0 else None
    out['square_root'] = {'beta': round(beta, 6), 'beta_basis': 'user-provided qos_beta' if p['qos_beta'] is not None else f'Halfin-Whitt limit P(wait)={alpha:g}',
                          'halfin_whitt_p_wait': round(hw_wait_probability(beta), 6) if beta > 0 else None, 'servers': c_sr,
                          'exact_p_wait': round(erlang_c(c_sr, a), 6) if c_sr else None,
                          'exact_min_servers_for_p_wait_target': _min_c_for_pwait(a, alpha, p['max_servers']),
                          'note': 'Targets P(wait > 0); the exact search in `servers`/min_servers_for_objective targets P(wait > t).'}
    return out


def _min_c_for_pwait(a, alpha, cap):
    c = max(int(math.floor(a)) + 1, 1)
    while c <= cap:
        if erlang_c(c, a) <= alpha: return c
        c += 1
    return None


def _insufficient(p, src, warnings, assumptions, reason, *, observations=None):
    return {'schema': VERSION, 'answer': {'objective': p['objective'], 'source': src, 'status': 'insufficient_data', 'reason': reason,
                **({'inputs': observations} if observations is not None else {})},
            'warnings': warnings + [f'No staffing answer: {reason}.'], 'assumptions': assumptions, 'notes': [], 'inference_status': 'insufficient_data',
            'optimality_claimed': False, 'parameters': {k: v for k, v in p.items() if k not in ('arrivals', 'service_minutes')},
            'method': '', 'limitations': []}


# ---- Agere graph integration -------------------------------------------------------------------
def engine_execute(path, patient_ids, params, as_of=None):
    """Preserve the graph's cohort and cutoff; None and an empty cohort differ."""
    return execute(path, params, patient_ids=patient_ids, as_of=as_of)


def table(report):
    """Staffing blocks, or retained demand observations with staffing unknown."""
    answer = report.get('answer') or {}
    if answer.get('status') == 'insufficient_data' and answer.get('inputs'):
        return [{**answer['inputs'], 'required_servers': None, 'capacity_gap_servers': None,
                 'configured_parameters_json': json.dumps(report['parameters'], sort_keys=True, allow_nan=False),
                 'parameter_authority': 'Configured engine inputs, not approved service targets. No staffing objective was evaluated.'}]
    return list(((report.get('answer') or {}).get('staffing') or {}).get('by_hour_of_week') or [])


def summary(report):
    a = report['answer']
    if a.get('status') == 'insufficient_data':
        return f"No staffing answer: {a['reason']}."
    st, sp = a['staffing'], a['single_point']
    blocking = a['objective'] == 'blocking'
    p = report['parameters']
    unit = 'beds' if a['source'] == 'beds' else 'staff on duty'
    target = (f"blocking probability <= {p['target_blocking']:.1%}" if blocking else
              f"P(wait > {p['wait_threshold_minutes']:g} min) <= {p['target_wait_probability']:.0%}")
    if st['peak_servers'] is None:
        return f"The target ({target}) cannot be met within {p['max_servers']} {unit}."
    text = (f"To keep {target}, the model needs up to {st['peak_servers']} {unit} at the busiest block ({st['peak_hour']}) and "
            f"{st['total_server_hours_per_week']:,} {'bed-hours' if a['source'] == 'beds' else 'server-hours'} per week across the 168 hour blocks. ")
    ec = sp.get('erlang_c')
    if blocking:
        if sp.get('servers'):
            text += f"At the mean load of {sp['offered_load']:.3g} erlangs, {sp['servers']} beds block {sp['erlang_b_blocking_probability']:.2%} of arrivals (Erlang B). "
    elif ec and ec.get('stable') and sp.get('servers'):
        text += (f"At the {sp['arrival_rate_basis']} ({sp['arrival_rate_per_hour']:.3g}/h, offered load {sp['offered_load']:.3g}) with {sp['servers']} servers, "
                 f"P(wait) = {ec['p_wait']:.0%}, P(wait > {p['wait_threshold_minutes']:g} min) = {ec['p_wait_gt_t']:.1%}, mean wait {ec['mean_wait_minutes']:.1f} min, "
                 f"utilization {ec['utilization']:.0%}. ")
    s = a['sensitivity']
    if len(s) == 3 and s[0]['peak_servers'] is not None and s[2]['peak_servers'] is not None:
        text += (f"Arrivals {p['sensitivity_pct']:g}% lower/higher change the peak to {s[0]['peak_servers']}/{s[2]['peak_servers']} "
                 f"and the weekly total to {s[0]['total_server_hours_per_week']:,}/{s[2]['total_server_hours_per_week']:,}. ")
    if report['warnings']: text += f"Warnings ({len(report['warnings'])}): " + report['warnings'][0]
    if report.get('unmet_requirements'):
        text = 'Historical capacity estimate only; the requested automatic forecast is unavailable. ' + text
    return text.strip()
