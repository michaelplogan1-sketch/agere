"""The markets view of a population ('market' operation): every practice, PCP or payer is a ticker.

Answers "what moved this month?", "give me the market view of our practices", "which practices are trending worse
on ED use and cost?" and "what is the most surprising change across the network?".

Panel: unit x metric monthly series over a 36-month window ending at the last complete month, built from the semantic
layer with the monitor/vbc building blocks (enrolment, attribution, ED/admission events, completion-adjusted claims):
  ed_rate      ED visits per 1,000 member-months            count / exposure  (higher is worse)
  admit_rate   hospital admissions per 1,000 member-months  count / exposure  (higher is worse)
  pmpm         completion-adjusted allowed PMPM ($)         mean per member-month (higher is worse)
  a1c_poor     share of diabetics (18-75) tested in the month whose latest A1c > 9%  (higher is worse)
  bp_control   share of hypertensives (18-85) with a BP in the month whose latest is < 140/90 (higher is better)
  denial_rate  share of claims (by service month) first adjudicated as denied (higher is worse)
  no_show_rate share of appointments with a final disposition that were no-shows (higher is worse)

Data before statistics: months the feed did not deliver are found on the network series and removed for every unit
(zero runs while the stream keeps flowing, the monitor's event-type gaps, single months >= 5x or <= 0.2x the typical
rate or volume). The analysis regime is the run of valid months after the last gap, so an empty regime never enters a
baseline and a feed gap is never reported as a clinical move. Claim months still running out are excluded.

Small panels: each unit's monthly rate is shrunk toward the network rate of that month (empirical Bayes; prior
strength from the between-unit variance by method of moments, worth at most one typical unit-month). Low-volume
tickers are marked.

Per ticker: last value, change vs the prior valid month and vs the 12-month baseline, Theil-Sen trend, volatility (robust
SD of month-over-month changes, 1.4826 x MAD), z of the latest move against that volatility, beta and R^2 of the
unit's log changes on the network's (does it move with the network or on its own), drawdown and run-up.

Surprise: -log2 p of the latest month's raw count under the unit's own predictive distribution - its baseline rate
moved by the network's change that month (a market model, as in an event study's abnormal return), negative-binomial
for counts with exposure (overdispersion pooled per metric), overdispersed normal for shares and dollars; plus the KL
information of the latest month vs that expectation. The network ticker is judged against its own baseline, so a
network-wide drift appears once, as the network, and not as N unit moves. Ranked by information x impact (excess bits
after the look-elsewhere cost of the tickers examined, times log2(2 + members + excess dollars in member-years of
spend)), the same rule as monitor and briefing. Sectors: average-linkage clustering of units on 1 - correlation of
their monthly log changes, and the share of variance carried by the first principal component (the network factor).
"""
from collections import defaultdict
import math, re

import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform
from scipy.stats import nbinom, norm, poisson, theilslopes

from . import monitor, vbc

VERSION = 'market/1'
UNITS = ('practice', 'pcp', 'payer')
METRICS = ('ed_rate', 'admit_rate', 'pmpm', 'a1c_poor', 'bp_control', 'denial_rate', 'no_show_rate')
DIRECTIONS = ('any', 'worse', 'better')
FIELDS = {'unit', 'metrics', 'direction', 'period', 'top'}
KIND = {'ed_rate': 'count', 'admit_rate': 'count', 'pmpm': 'mean', 'a1c_poor': 'share', 'bp_control': 'share',
        'denial_rate': 'share', 'no_show_rate': 'share'}
SCALE = {'ed_rate': 1000.0, 'admit_rate': 1000.0}
LABELS = {'ed_rate': 'ED visits per 1,000 member-months', 'admit_rate': 'admissions per 1,000 member-months',
          'pmpm': 'allowed PMPM ($, completion-adjusted)', 'a1c_poor': 'diabetics tested with A1c > 9%',
          'bp_control': 'hypertensives with BP < 140/90', 'denial_rate': 'claim first-pass denial rate',
          'no_show_rate': 'appointment no-show rate'}
SHORT = {'ed_rate': 'ED rate', 'admit_rate': 'admissions', 'pmpm': 'PMPM', 'a1c_poor': 'A1c poor control', 'bp_control': 'BP control',
         'denial_rate': 'denials', 'no_show_rate': 'no-shows'}
HIGHER_IS_WORSE = {m: m != 'bp_control' for m in METRICS}
NETWORK = 'NETWORK'
BASELINE_MONTHS = 12
MIN_REGIME = 4
MIN_MEMBERS = 30
SPIKE = 5.0
TERMINAL_APPT = ('arrived', 'fulfilled', 'no_show')
LIMITATIONS = [
    'Moves are statistical surprises against each unit\'s own history and the network that month, not judgements of care quality; '
    'look-elsewhere costs are reported because many tickers are examined at once.',
    'Months the data feed did not deliver are removed for every unit; a metric whose last regime is shorter than four months is not ranked.',
    'Monthly rates are empirical-Bayes shrunk toward the network; low-volume tickers stay marked and are not headlined.',
    'Claim-based months still running out (completion factor > 1.25) are excluded; PMPM is allowed cost, completion-adjusted.',
    'Quality shares are internal monthly definitions over patients tested that month (not certified HEDIS measures).',
    'Payer = the member\'s coverage type for member-based metrics and the adjudicating payer (claims source system) for denials.',
]


# ---- parameters ------------------------------------------------------------------------

def _share(x):
    return f'{x:.0%}' if isinstance(x, (int, float)) else 'an unmeasured share'

def validate(p):
    if p is None: p = {}
    if not isinstance(p, dict) or set(p) - FIELDS:
        raise ValueError(f'market parameters must be a subset of {sorted(FIELDS)}')
    unit = p.get('unit', 'practice')
    if unit not in UNITS: raise ValueError(f'market unit must be one of {UNITS}')
    metrics = p.get('metrics') or []
    metrics = [metrics] if isinstance(metrics, str) else metrics
    if not isinstance(metrics, list) or any(m not in METRICS for m in metrics) or len(set(metrics)) != len(metrics):
        raise ValueError(f'market metrics must be distinct values of {METRICS}')
    direction = p.get('direction', 'any')
    if direction not in DIRECTIONS: raise ValueError(f'market direction must be one of {DIRECTIONS}')
    period = p.get('period') or {}
    if not isinstance(period, dict) or set(period) - {'start', 'end'} or \
            any(not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(v)) for v in period.values() if v):
        raise ValueError('market period needs YYYY-MM-DD start/end')
    top = p.get('top', 25)
    if not isinstance(top, int) or isinstance(top, bool) or not 1 <= top <= 500:
        raise ValueError('market top must be an integer 1..500')
    return {'unit': unit, 'metrics': metrics, 'direction': direction, 'period': {k: v for k, v in period.items() if v}, 'top': top}


# ---- panel --------------------------------------------------------------------------------
def _key_fn(unit, pop):
    if unit == 'payer': return lambda pid: (pop.people.get(pid) or {}).get('coverage_type') or 'unknown'
    return lambda pid: (pop.attr.get(pid) or {}).get(unit) or 'unattributed'


def _latest_in_month(rows):
    """(pid, month) -> value of the latest dated result in that month."""
    out = {}
    for pid, d, v in rows:
        if v is None or not d: continue
        k = (pid, d[:7])
        if k not in out or d >= out[k][0]: out[k] = (d, v)
    return {k: v for k, (_, v) in out.items()}


def panel(db, unit, months, keep, as_of, metrics):
    """metric -> {'num': unit -> array, 'den': unit -> array, 'sd': per-unit-of-exposure SD (mean metrics)} plus members per unit."""
    idx = {m: i for i, m in enumerate(months)}
    T = len(months)
    pop = monitor.Population(db, months, keep)
    key = _key_fn(unit, pop)
    exposure = defaultdict(lambda: np.zeros(T))
    members = defaultdict(int)
    for pid, ms in pop.months.items():
        members[key(pid)] += 1
        for m in ms:
            if m in idx: exposure[key(pid)][idx[m]] += 1
    lo, hi = f'{months[0]}-01', monitor._month_end(months[-1])
    out = {}

    def zeros(): return defaultdict(lambda: np.zeros(T))
    for s in ('ed_rate', 'admit_rate'):
        if s not in metrics: continue
        num = zeros()
        for pid, m, _, v in monitor._series_events(db, s, months, pop, keep):
            num[key(pid)][idx[m]] += v
        out[s] = {'num': {u: num[u] for u in exposure}, 'den': dict(exposure)}
    if 'pmpm' in metrics:
        paid = vbc._claims(db, lo, hi)
        completion, _ = vbc._completion(paid, hi)
        cost = defaultdict(float)
        for pid, m, allowed, *_ in paid:
            if m in pop.months.get(pid, ()) and (keep is None or pid in keep): cost[(pid, m)] += allowed / 100 * completion.get(m, 1.0)
        mature = np.array([completion.get(m, 1.0) <= 1.25 for m in months])
        num, den, allx = zeros(), zeros(), []
        for pid, ms in pop.months.items():
            u = key(pid)
            for m in ms:
                c = cost.get((pid, m), 0.0)
                num[u][idx[m]] += c; den[u][idx[m]] += 1; allx.append(c)
        has_claims = {m for (_, m) in cost}
        ok = np.array([bool(mature[i]) and months[i] in has_claims for i in range(T)])
        out['pmpm'] = {'num': {u: np.where(ok, num[u], 0.0) for u in den}, 'den': {u: np.where(ok, den[u], 0.0) for u in den},
                       'sd': float(np.std(allx)) if allx else 1.0}
    if 'a1c_poor' in metrics or 'bp_control' in metrics:
        has = defaultdict(set)
        for c, pid in db.execute("SELECT concept, patient_id FROM concept_patient WHERE concept IN ('diabetes', 'hypertension')"):
            has[c].add(pid)
        age = {pid: (i.get('age')) for pid, i in pop.people.items()}

        def rows(concept):
            return _latest_in_month(db.execute('SELECT patient_id, event_date, value_canonical FROM measurement WHERE concept = ? '
                                               'AND event_date BETWEEN ? AND ?', (concept, lo, hi)))
        if 'a1c_poor' in metrics:
            num, den = zeros(), zeros()
            for (pid, m), v in rows('hba1c').items():
                if pid in has['diabetes'] and age.get(pid) is not None and 18 <= age[pid] <= 75 and m in pop.months.get(pid, ()):
                    den[key(pid)][idx[m]] += 1; num[key(pid)][idx[m]] += v > 9
            out['a1c_poor'] = {'num': {u: num[u] for u in den}, 'den': dict(den)}
        if 'bp_control' in metrics:
            num, den = zeros(), zeros()
            sbp, dbp = rows('systolic_bp'), rows('diastolic_bp')
            for (pid, m), s in sbp.items():
                d = dbp.get((pid, m))
                if d is None: continue
                if pid in has['hypertension'] and age.get(pid) is not None and 18 <= age[pid] <= 85 and m in pop.months.get(pid, ()):
                    den[key(pid)][idx[m]] += 1; num[key(pid)][idx[m]] += (s < 140 and d < 90)
            out['bp_control'] = {'num': {u: num[u] for u in den}, 'den': dict(den)}
    if 'denial_rate' in metrics:
        num, den = zeros(), zeros()
        for r in monitor._claims_rows(db, months, keep, as_of):
            m = r[3][:7]
            if m not in idx: continue
            u = (r[2] or 'unknown') if unit == 'payer' else key(r[1])
            den[u][idx[m]] += 1; num[u][idx[m]] += r[4] == 'denied'
        out['denial_rate'] = {'num': {u: num[u] for u in den}, 'den': dict(den)}
    if 'no_show_rate' in metrics:
        num, den = zeros(), zeros()
        for pid, d, status in db.execute('SELECT patient_id, scheduled_date, final_status FROM appointment WHERE scheduled_date BETWEEN ? AND ?',
                                          (lo, min(hi, as_of))):
            if status not in TERMINAL_APPT or (keep is not None and pid not in keep) or not d or d[:7] not in idx: continue
            u = key(pid)
            den[u][idx[d[:7]]] += 1; num[u][idx[d[:7]]] += status == 'no_show'
        out['no_show_rate'] = {'num': {u: num[u] for u in den}, 'den': dict(den)}
    return out, dict(members), pop


# ---- data gaps -----------------------------------------------------------------------------
def data_gaps(num, den, kind, months, type_gaps=()):
    """Months the network series cannot be read as care: (excluded index -> reason, regime indices).
    Gaps (no data, zero runs while exposure flows, event-type gaps) split regimes; single-month spikes/dips are excluded."""
    T = len(months)
    reason = {}
    for i in range(T):
        if den[i] <= 0: reason[i] = 'no_data'
    for g in type_gaps:
        for i in range(g['start'], g['end'] + 1): reason[i] = 'event_type_gap'
    ok = [i for i in range(T) if i not in reason and num[i] > 0]
    if kind in ('count', 'share') and ok:
        typical = float(np.median([num[i] / den[i] for i in ok]))
        run = []
        for i in list(range(T)) + [None]:
            if i is not None and i not in reason and num[i] == 0 and typical * den[i] >= 1.0:
                run.append(i); continue
            if len(run) >= 2 or (run and sum(typical * den[j] for j in run) >= 5):
                for j in run: reason[j] = 'zero_run'
            run = []
    ok = [i for i in range(T) if i not in reason]
    if ok:
        rate = {i: num[i] / den[i] for i in ok}
        typical = float(np.median([rate[i] for i in ok]))
        typical_den = float(np.median([den[i] for i in ok]))
        for i in ok:
            expected = typical * den[i] if kind == 'count' else den[i]
            if typical > 0 and expected >= 5 and (rate[i] >= SPIKE * typical or (kind != 'share' and rate[i] <= typical / SPIKE)):
                reason[i] = 'volume_spike' if rate[i] > typical else 'volume_dip'
            elif kind != 'count' and den[i] <= typical_den / SPIKE:
                reason[i] = 'thin_month'
    splits = [i for i, r in reason.items() if r in ('no_data', 'event_type_gap', 'zero_run')]
    last_split = max([i for i in splits if any(j > i and j not in reason for j in range(T))] or [-1])
    regime = [i for i in range(last_split + 1, T) if i not in reason]
    # leading no-data months inside the regime would not split it; trailing no-data months simply end it
    return reason, regime


def _ranges(months, idxs):
    out, run = [], []
    for i in sorted(idxs) + [None]:
        if i is not None and (not run or i == run[-1] + 1): run.append(i); continue
        if run: out.append(months[run[0]] + ('..' + months[run[-1]] if len(run) > 1 else ''))
        run = [i] if i is not None else []
    return out


# ---- shrinkage -------------------------------------------------------------------------------
def prior_strength(num, den, kind, sd=None, regime=None):
    """Empirical-Bayes prior strength (in exposure units) toward the network: sampling variance per unit of exposure over
    the between-unit variance of long-run rates (method of moments), capped at one typical unit-month."""
    units = [u for u in den if den[u][regime].sum() > 0]
    if len(units) < 2: return 0.0
    X = np.array([den[u][regime].sum() for u in units]); E = np.array([num[u][regime].sum() for u in units])
    R = E / X
    m = E.sum() / X.sum()
    per = m if kind == 'count' else m * (1 - m) if kind == 'share' else (sd or 1.0) ** 2
    tau2 = max(float(np.var(R)) - float(np.mean(per / X)), 1e-12)
    cap = float(np.median([d for u in units for d in den[u][regime] if d > 0]))
    return min(per / tau2, cap)


def shrunk(num, den, net_rate, k):
    with np.errstate(invalid='ignore', divide='ignore'):
        return (num + k * net_rate) / (den + k)


# ---- predictive surprise -------------------------------------------------------------------------
def _logit(p): p = min(max(p, 1e-6), 1 - 1e-6); return math.log(p / (1 - p))


def _expit(x): return 1 / (1 + math.exp(-x))


def expected_rate(kind, base_unit, base_net, latest_net, market=True):
    """The unit's baseline moved by the network's change (multiplicative; additive on the logit for shares)."""
    if not market or base_net <= 0: return base_unit
    if kind == 'share':
        return _expit(_logit(base_unit) + _logit(latest_net) - _logit(base_net))
    return base_unit * latest_net / base_net


def predictive(kind, x, n, e, phi, sd=None):
    """(two-sided p, KL information in bits, expected numerator) of the observed numerator x on exposure n."""
    if n <= 0: return 1.0, 0.0, 0.0
    if kind == 'count':
        mu = max(e * n, 1e-9)
        if phi > 1.0 + 1e-9:
            size = mu / (phi - 1); q = size / (size + mu)
            lo, hi = nbinom.cdf(x, size, q), nbinom.sf(x - 1, size, q)
        else:
            lo, hi = poisson.cdf(x, mu), poisson.sf(x - 1, mu)
        kl = ((x * math.log(x / mu) if x > 0 else 0.0) - x + mu) / math.log(2) / max(phi, 1.0)
        return min(1.0, 2 * min(lo, hi)), kl, mu
    if kind == 'share':
        e = min(max(e, 1e-6), 1 - 1e-6)
        z = (x - e * n) / math.sqrt(phi * n * e * (1 - e))
        f = x / n
        kl = n * sum(a * math.log(a / b) for a, b in ((f, e), (1 - f, 1 - e)) if a > 0) / math.log(2) / max(phi, 1.0)
        return float(2 * norm.sf(abs(z))), kl, e * n
    z = (x / n - e) / math.sqrt(phi * (sd or 1.0) ** 2 / n)
    return float(2 * norm.sf(abs(z))), z * z / 2 / math.log(2), e * n


def _var(kind, mu_rate, n, sd):
    if kind == 'count': return max(mu_rate * n, 1e-9)
    if kind == 'share': return max(n * mu_rate * (1 - mu_rate), 1e-9)
    return max(n * (sd or 1.0) ** 2, 1e-9)


def dispersion(num, den, kind, net_rate, base_idx, sd=None):
    """Pooled Pearson overdispersion of unit-months against the market model, floored at 1."""
    r2 = []
    for u in den:
        n_b = den[u][base_idx].sum()
        if n_b <= 0: continue
        base = num[u][base_idx].sum() / n_b
        net_b = sum(num[v][base_idx].sum() for v in den) / max(sum(den[v][base_idx].sum() for v in den), 1e-9)
        for t in base_idx:
            n = den[u][t]
            if n <= 0: continue
            e = expected_rate(kind, base, net_b, net_rate[t])
            if kind == 'share': e = min(max(e, 1e-6), 1 - 1e-6)
            r2.append((num[u][t] - e * n) ** 2 / _var(kind, e, n, sd))
    return float(min(max(np.mean(r2) if r2 else 1.0, 1.0), 50.0))


def own_dispersion(num, den, kind, base_rate, base_idx, sd=None):
    """Pearson dispersion of one series' baseline months around its own baseline rate, floored at 1."""
    mu = min(max(base_rate, 1e-9), 1 - 1e-9) if kind == 'share' else base_rate
    r2 = [(num[t] - mu * den[t]) ** 2 / _var(kind, mu, den[t], sd) for t in base_idx if den[t] > 0]
    return float(min(max(np.mean(r2) if r2 else 1.0, 1.0), 1e6))


# ---- per-ticker statistics --------------------------------------------------------------------
def robust_sd(x):
    x = np.asarray(x, float)
    if len(x) < 2: return None
    return float(1.4826 * np.median(np.abs(x - np.median(x))))


def drawdown(y):
    y = np.asarray(y, float)
    if len(y) < 2: return 0.0, 0.0
    peak, trough, dd, ru = y[0], y[0], 0.0, 0.0
    for v in y[1:]:
        if peak > 0: dd = max(dd, (peak - v) / peak)
        if trough > 0: ru = max(ru, (v - trough) / trough)
        peak, trough = max(peak, v), min(trough, v)
    return float(dd), float(ru)


def beta_to(unit_y, net_y):
    a, b = np.diff(np.log(np.maximum(unit_y, 1e-9))), np.diff(np.log(np.maximum(net_y, 1e-9)))
    if len(a) < 4 or np.var(b) <= 1e-12: return None, None
    beta = float(np.cov(a, b, ddof=0)[0, 1] / np.var(b))
    r = np.corrcoef(a, b)[0, 1] if np.var(a) > 1e-12 else 0.0
    return beta, float(r * r) if math.isfinite(r) else 0.0


def sectors(series, threshold=0.5):
    """Average-linkage clusters of units on 1 - correlation of monthly log changes; PC1 share = network factor."""
    names = [u for u, y in series.items() if len(y) >= 6]
    if len(names) < 3: return [], None
    D = np.array([np.diff(np.log(np.maximum(series[u], 1e-9))) for u in names])
    keep = [i for i in range(len(names)) if np.std(D[i]) > 1e-9]
    names, D = [names[i] for i in keep], D[keep]
    if len(names) < 3: return [], None
    C = np.clip(np.nan_to_num(np.corrcoef(D)), -1, 1)
    Z = (D - D.mean(axis=1, keepdims=True)) / D.std(axis=1, keepdims=True)
    ev = np.linalg.eigvalsh(np.cov(Z))
    pc1 = float(ev[-1] / ev.sum()) if ev.sum() > 0 else None
    dist = squareform(np.clip(1 - C, 0, 2), checks=False)
    labels = fcluster(linkage(dist, 'average'), t=threshold, criterion='distance')
    out = []
    for lab in sorted(set(labels)):
        ix = [i for i in range(len(names)) if labels[i] == lab]
        if len(ix) < 2: continue
        sub = C[np.ix_(ix, ix)]
        out.append({'units': [names[i] for i in ix], 'mean_correlation': round(float((sub.sum() - len(ix)) / (len(ix) * (len(ix) - 1))), 3)})
    return sorted(out, key=lambda s: -len(s['units'])), (round(pc1, 3) if pc1 is not None else None)


def _r(v, d=4):
    if v is None or not isinstance(v, (int, float)) or not math.isfinite(v): return None
    return round(float(v), d if abs(v) < 1 else 2)


def follow_up(t, unit):
    noun = {'practice': 'practice', 'pcp': 'PCP', 'payer': 'payer'}[unit]
    return f"Show me {noun} tickers for {SHORT[t['metric']]}"


# ---- the operator ----------------------------------------------------------------------------
def execute(path, patient_ids, params, as_of=None):
    p = validate(params)
    db = monitor._connect(path)
    try:
        as_of = as_of or dict(db.execute('SELECT key, value FROM meta')).get('as_of')
        months, _ = monitor.windows({'period': p['period']}, as_of)
        if len(months) < MIN_REGIME + 1: raise ValueError('market needs at least five complete months of data in the window')
        keep = set(patient_ids) if patient_ids is not None else None
        metrics = p['metrics'] or list(METRICS)
        data, members, pop = panel(db, p['unit'], months, keep, as_of, metrics)
        type_gaps = {s: (monitor.event_type_gaps(db, months, s, keep) or {}).get('gaps', []) for s in ('ed_rate', 'admit_rate') if s in metrics}
    finally:
        db.close()
    annual = None
    if 'pmpm' in data:
        d = data['pmpm']; mm = sum(v.sum() for v in d['den'].values())
        annual = 12 * sum(v.sum() for v in d['num'].values()) / mm if mm else None
    tickers, network, gaps, skipped, sector_rows, series_out = [], {}, {}, {}, [], {}
    for metric in metrics:
        d = data[metric]; kind, scale = KIND[metric], SCALE.get(metric, 1.0)
        num, den, sd = d['num'], d['den'], d.get('sd')
        units = sorted(u for u in den if den[u].sum() > 0)
        if not units:
            skipped[metric] = 'no data in the window'; continue
        net_num = sum(num[u] for u in units); net_den = sum(den[u] for u in units)
        reason, regime = data_gaps(net_num, net_den, kind, months, type_gaps.get(metric, ()))
        gaps[metric] = [{'months': r, 'reason': why} for why in sorted(set(reason.values()))
                        for r in _ranges(months, [i for i, w in reason.items() if w == why])]
        if len(regime) < MIN_REGIME:
            skipped[metric] = f'only {len(regime)} valid month(s) after the last data gap'; continue
        with np.errstate(invalid='ignore', divide='ignore'):
            net_rate = np.where(net_den > 0, net_num / np.maximum(net_den, 1e-12), 0.0)
        L, prev = regime[-1], regime[-2]
        base = regime[:-1][-BASELINE_MONTHS:]
        k = prior_strength(num, den, kind, sd, regime)
        phi = dispersion(num, den, kind, net_rate, base, sd)
        base_net = net_num[base].sum() / max(net_den[base].sum(), 1e-12)
        n_tests_before = len(tickers)
        net_series = net_rate[regime] * scale
        unit_series = {}
        for u in [NETWORK] + units:
            if u == NETWORK:
                un, ud, y = net_num, net_den, net_series
            else:
                un, ud = num[u], den[u]
                y = shrunk(un[regime], ud[regime], net_rate[regime], k) * scale
                unit_series[u] = y
            if ud[L] <= 0 or ud[base].sum() <= 0: continue
            bn, bd = un[base].sum(), ud[base].sum()
            base_rate = (bn + (0 if u == NETWORK else k * base_net)) / (bd + (0 if u == NETWORK else k))
            e = expected_rate(kind, base_rate, base_net, net_rate[L], market=u != NETWORK)
            # The network has no market to net out: its predictive spread is its own month-to-month dispersion.
            own_phi = own_dispersion(un, ud, kind, base_rate, base, sd)
            phi_u = own_phi if u == NETWORK else phi
            pval, kl, mu = predictive(kind, un[L], ud[L], e, phi_u, sd)
            own_p, _, _ = predictive(kind, un[L], ud[L], base_rate, max(phi_u, own_phi), sd)
            last, prior_v = float(y[-1]), float(y[-2])
            base_v = float(np.mean(y[:-1][-BASELINE_MONTHS:]))
            diffs = np.diff(y)
            vol = robust_sd(diffs[:-1]) if len(diffs) >= 3 else None
            sampling = math.sqrt(_var(kind, max(base_rate, 1e-9), ud[L], sd)) / max(ud[L] + (0 if u == NETWORK else k), 1e-9) * scale
            vol = max(vol or 0.0, sampling)
            xs = np.array(regime[-(BASELINE_MONTHS + 1):], float)
            slope = float(theilslopes(y[-len(xs):], xs)[0]) if len(xs) >= 3 else None
            beta, r2 = (None, None) if u == NETWORK else beta_to(y, net_series)
            dd, ru = drawdown(y)
            observed_rate = un[L] / ud[L]
            up = observed_rate > e
            worse = up == HIGHER_IS_WORSE[metric]
            excess_dollars = abs(un[L] - e * ud[L]) if metric == 'pmpm' else 0.0
            low = u != NETWORK and (members.get(u, 0) < MIN_MEMBERS or
                                    (kind == 'count' and float(np.median(ud[regime])) * base_net < 2) or
                                    (kind == 'share' and float(np.median(ud[regime])) < 10) or
                                    (kind == 'mean' and float(np.median(ud[regime])) < 30))
            tickers.append({'unit': u, 'metric': metric, 'label': LABELS[metric], 'month': months[L], 'prior_month': months[prev],
                            'last': _r(last), 'prior': _r(prior_v), 'change_prior': _r(last - prior_v),
                            'change_prior_pct': _r((last - prior_v) / prior_v) if prior_v else None,
                            'baseline': _r(base_v), 'change_baseline_pct': _r((last - base_v) / base_v) if base_v else None,
                            'trend_per_month': _r(slope), 'volatility': _r(vol), 'z': _r((last - prior_v) / vol if vol else None, 2),
                            'beta': _r(beta, 2), 'r2_with_network': _r(r2, 3), 'drawdown': _r(dd, 3), 'run_up': _r(ru, 3),
                            'observed_raw': _r(observed_rate * scale), 'baseline_raw': _r(bn / bd * scale), 'expected': _r(e * scale), 'numerator': _r(float(un[L])),
                            'exposure': _r(float(ud[L])), 'direction': 'worse' if worse else 'better',
                            'surprise_bits': monitor.bits(pval), 'own_bits': monitor.bits(own_p), 'kl_bits': _r(kl, 3),
                            'members': len(pop.months) if u == NETWORK else members.get(u, 0), 'excess_dollars': _r(excess_dollars, 2),
                            'low_volume': bool(low), 'spark': [_r(float(v)) for v in y[-24:]], 'spark_months': [months[i] for i in regime[-24:]],
                            'prior_strength': _r(k), 'overdispersion': _r(phi_u, 3)})
        series_out[metric] = unit_series
        net = next((t for t in tickers[n_tests_before:] if t['unit'] == NETWORK), None)
        if net:
            # Breadth on raw rates (shrunk series lean on the network and would echo one unit's shock); low-volume units abstain.
            moved = [t for t in tickers[n_tests_before:] if t['unit'] != NETWORK and not t['low_volume'] and t['baseline_raw'] is not None]
            same = [t for t in moved if (t['observed_raw'] > t['baseline_raw']) == ((net['change_baseline_pct'] or 0) > 0)]
            network[metric] = {'breadth': _r(len(same) / len(moved), 3) if moved else None, 'units': len(moved)}
        groups, pc1 = sectors({u: y for u, y in unit_series.items()})
        if groups or pc1 is not None:
            sector_rows.append({'metric': metric, 'network_factor_share': pc1, 'sectors': groups})
    tests = max(len(tickers), 1)
    look = round(math.log2(tests), 2)
    for t in tickers:
        t['excess_bits'] = round(t['surprise_bits'] - look, 2)
        t['salient'] = t['excess_bits'] >= 0 and not t['low_volume']
        t['impact'] = round(float(t['members'] + (t['excess_dollars'] / annual if annual else 0)), 1)
        t['score'] = round(monitor.score(t['excess_bits'], t['impact']) * (0.25 if t['low_volume'] else 1.0), 2)
        if t['unit'] == NETWORK:
            info = network.get(t['metric']) or {}
            t['move_type'] = 'network-wide' if t['salient'] and (info.get('breadth') or 0) >= 0.6 else 'network' if t['salient'] else 'quiet'
        else:
            t['move_type'] = 'idiosyncratic' if t['salient'] else \
                'with the network' if t['own_bits'] - look >= 0 and not t['low_volume'] else 'quiet'
        t['follow_up'] = follow_up(t, p['unit'])
    for metric, info in network.items():
        t = next(x for x in tickers if x['metric'] == metric and x['unit'] == NETWORK)
        solo = [x['unit'] for x in tickers if x['metric'] == metric and x['move_type'] == 'idiosyncratic']
        info.update(salient=t['salient'], change_baseline_pct=t['change_baseline_pct'], direction=t['direction'], month=t['month'],
                    idiosyncratic_units=solo,
                    statement=(f"{SHORT[metric]}: network-wide move in {t['month']} ({_pct(t['change_baseline_pct'])} vs baseline, "
                               f"{_share(info.get('breadth'))} of {info['units']} units moved the same way)" if t['move_type'] == 'network-wide' else
                               f"{SHORT[metric]}: the network moved in {t['month']} ({_pct(t['change_baseline_pct'])} vs baseline) but only "
                                                  f"{_share(info.get('breadth'))} of units moved with it" if t['salient'] else
                               f"{SHORT[metric]}: " + (f"no network move; {', '.join(solo[:4])} moved on {'its' if len(solo) == 1 else 'their'} own"
                                                       if solo else 'no network move and no idiosyncratic mover')))
    # Low-volume tickers rank after every adequately sized one: shrunk and marked, never headlined over real panels.
    ranked = sorted(tickers, key=lambda t: (t['low_volume'], -t['score'], -t['surprise_bits']))
    if p['direction'] != 'any':
        ranked = [t for t in ranked if t['direction'] == p['direction']]
    return {'schema': VERSION, 'parameters': p, 'unit': p['unit'], 'metrics': metrics, 'as_of': as_of,
            'period': {'window': [months[0], months[-1]]}, 'tickers': ranked, 'network': network, 'sectors': sector_rows,
            'data_gaps': gaps, 'skipped_metrics': skipped, 'tests_examined': tests, 'look_elsewhere_bits': look,
            'annual_allowed_per_member': _r(annual), 'members_in_scope': len(pop.months), 'method': METHOD, 'limitations': LIMITATIONS}


def _pct(v): return '—' if v is None else f'{v:+.0%}' if abs(v) < 10 else f'{v:+.1f}x'


METHOD = ('Unit x metric monthly panel (36-month window to the last complete month) from enrolment, attribution, ED/admission events, '
          'completion-adjusted allowed claims, A1c/BP results, claim first-pass status and appointment dispositions. Feed gaps found on '
          'the network series (no data, zero runs while exposure flows, event-type gaps, single months >= 5x / <= 0.2x typical, thin '
          'months) are excluded for every unit; the analysis regime starts after the last gap. Monthly unit rates are empirical-Bayes '
          'shrunk toward the network (method-of-moments prior, capped at one typical unit-month). Per ticker: change vs prior month and '
          '12-month baseline, Theil-Sen trend, robust volatility (1.4826 x MAD of monthly changes), z of the latest move, beta / R^2 '
          'of log changes on the network, drawdown and run-up. Surprise = -log2 p of the latest raw month under the unit\'s baseline '
          'moved by the network\'s change (negative binomial for counts with exposure, overdispersed normal for shares and dollars; '
          'overdispersion pooled per metric by Pearson chi-square); the network ticker is judged against its own baseline. Excess = '
          'surprise - log2(tickers examined); score = excess bits x log2(2 + members + excess dollars in member-years of spend). '
          'Sectors: average-linkage clustering on 1 - correlation of monthly log changes; network factor = first principal component share.')


def summarize(report, limit=5):
    """(one-paragraph value, ticker table) for the shared answer contract."""
    t = report['tickers']
    lead = [x for x in t if x['salient']][:limit]
    unit = {'practice': 'practices', 'pcp': 'PCPs', 'payer': 'payers'}[report['unit']]
    parts = []
    if lead:
        parts.append(f"{len([x for x in t if x['salient']])} salient move(s) among {report['tests_examined']} tickers ({unit} x "
                     f"{len(report['metrics']) - len(report['skipped_metrics'])} metrics, look-elsewhere cost {report['look_elsewhere_bits']} bits)")
        for x in lead:
            parts.append(f"{'Network' if x['unit'] == NETWORK else x['unit']} {SHORT[x['metric']]} {x['month']}: {_fmt(x['observed_raw'], x['metric'])} "
                         f"vs {_fmt(x['expected'], x['metric'])} expected ({x['direction']}, {x['surprise_bits']} bits, {x['move_type']})")
    else:
        parts.append(f"No salient moves among {report['tests_examined']} tickers after a look-elsewhere cost of {report['look_elsewhere_bits']} bits"
                     + (f"; the largest was {t[0]['unit']} {SHORT[t[0]['metric']]} ({t[0]['surprise_bits']} bits)" if t else ''))
    parts += [v['statement'] for v in report['network'].values() if v.get('statement')][:4]
    gap_metrics = [m for m, g in report['data_gaps'].items() if any(x['reason'] != 'no_data' for x in g)]
    if gap_metrics:
        parts.append('data gaps excluded, not read as moves: ' + '; '.join(
            f"{SHORT[m]} " + ', '.join(f"{x['months']} ({x['reason'].replace('_', ' ')})" for x in report['data_gaps'][m] if x['reason'] != 'no_data')[:160]
            for m in gap_metrics[:4]))
    if report['skipped_metrics']:
        parts.append('not ranked: ' + '; '.join(f'{SHORT[m]} ({why})' for m, why in report['skipped_metrics'].items()))
    table = [{k: v for k, v in x.items() if k not in ('label', 'spark_months')} for x in t[:report['parameters']['top']]]
    return '. '.join(s.rstrip('.') for s in parts) + '.', table


def _fmt(v, metric):
    if v is None: return '—'
    if KIND[metric] == 'share': return f'{v:.1%}'
    if metric == 'pmpm': return f'${v:,.0f}'
    return f'{v:.1f}'


def market_cards(report, limit=3):
    """Top salient movers as briefing cards (low-volume tickers never headline)."""
    cards = []
    for t in report['tickers']:
        if not t['salient'] or t['low_volume']: continue
        who = 'Network' if t['unit'] == NETWORK else f"{report['unit'].upper() if report['unit'] == 'pcp' else report['unit'].capitalize()} {t['unit']}"
        cards.append({'kind': 'market move', 'topic': f"{SHORT[t['metric']]} ({t['unit']})",
                      'headline': (f"{who} {SHORT[t['metric']]} moved {t['direction']} in {t['month']}: {_fmt(t['observed_raw'], t['metric'])} vs "
                                   f"{_fmt(t['expected'], t['metric'])} expected "
                                   + (f"from its own baseline ({t['move_type']}; z {t['z']})" if t['unit'] == NETWORK else
                                      f"given the network ({t['move_type']}; z {t['z']}, beta {t['beta']})")),
                      'patients': t['members'], 'dollars': t['excess_dollars'] or None, 'strength': t['excess_bits'],
                      'surprise_bits': max(t['excess_bits'], 0.0), 'follow_up': t['follow_up'],
                      'evidence': {k: t.get(k) for k in ('unit', 'metric', 'month', 'last', 'prior', 'baseline', 'expected', 'surprise_bits',
                                                         'excess_bits', 'kl_bits', 'z', 'beta', 'move_type', 'spark')}})
        if len(cards) >= limit: break
    return cards
