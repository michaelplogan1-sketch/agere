"""Bayesian Blocks vs CUSUM / Page-Hinkley / EWMA change-point qualification under feed gaps and claim lag.

AGERE_RESEARCH_REPORT 2026-10-18 item 10: "qualify the existing change-point engine against delayed/missing feeds". Genuine
utilization changes are simulated separately from missing batches, zero-filled feed gaps and claim runout lag; each detector
is calibrated to the SAME false-alert budget on clean null series and then scored on:
  false_alarm_rate        share of held-out null series with any alarm in the monitoring window
  gap_false_alarm_rate    share of no-change series with a feed gap / missing batch that alarm (a gap mistaken for change)
  lag_false_alarm_rate    share of no-change series whose latest weeks are under-reported by claim runout that alarm
  detection_rate          share of genuine level shifts detected after the change (an alarm before it is a false alarm)
  median_delay_weeks      median weeks from the true change to the first alarm, over detected shifts
Detectors (one statistic per monitoring week, threshold calibrated on the null set):
  bayesian_blocks   the existing uncertainty-weighted block program (signal_math.gaussian_change_points, the trajectory engine's
                    method) re-run on every prefix; statistic = |last block - previous block| / combined SE when the last block
                    started within the recent window
  cusum             two-sided standardized CUSUM (k = 0.5 sd)
  page_hinkley      two-sided Page-Hinkley (delta = 0.5 sd)
  ewma              EWMA of the standardized series (lambda 0.2), the monitor engine's rate rule
Each runs naive and gap_aware: gap_aware drops weeks the feed-gap rule marks (counts implausibly low under the baseline Poisson,
the same signature monitor.zero_runs / feed_gaps.py use) and completes the latest weeks for runout with chain-ladder factors.
Keep rule (report line 92): a method qualifies over CUSUM/Page-Hinkley only if its median delay is >= 20% shorter at the fixed
false-alert budget AND its gap false-alarm rate is no worse than the budget. The verdict is written by
tools/changepoint_qualification.py to config/agere/changepoint_qualification.json and read back by the trajectory and monitor
engines (verdict(), note()). Synthetic simulation only; this is bounded engineering evidence, not clinical validation.
"""
import json
import math
import os
import random
import statistics
from pathlib import Path

VERSION = 'changepoint-qualification/1'
ROOT = Path(__file__).resolve().parents[3]
RESULT_FILE = ROOT / 'config' / 'agere' / 'changepoint_qualification.json'
METHODS = ('bayesian_blocks', 'cusum', 'page_hinkley', 'ewma')
VARIANTS = ('naive', 'gap_aware')
BASELINES = ('cusum', 'page_hinkley')
WEEKS, WARMUP, RECENT = 78, 26, 8
LAG_FACTORS = (0.55, 0.75, 0.9)  # share of final counts already received 1, 2, 3 weeks after the week (claim runout)
GAP_P = 1e-3


# ---- simulation -------------------------------------------------------------------------------------------------------
def _poisson(rng, lam):
    if lam <= 0: return 0
    if lam > 30: return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))
    l, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= l: return k
        k += 1


def simulate(kind, rng, weeks=WEEKS, warmup=WARMUP):
    """One weekly utilization series. kind: null | shift | gap | batch | lag. -> {'counts', 'change'(index|None), 'lagged', 'kind'}"""
    base = rng.uniform(25, 80)
    season = rng.uniform(0.0, 0.06)
    phase = rng.uniform(0, 2 * math.pi)
    mean = [base * (1 + season * math.sin(2 * math.pi * t / 52 + phase)) for t in range(weeks)]
    change = None
    if kind == 'shift':
        change = rng.randint(warmup + 6, weeks - 14)
        factor = rng.choice((0.7, 0.75, 1.25, 1.3))
        mean = [m * (factor if t >= change else 1.0) for t, m in enumerate(mean)]
    counts = [_poisson(rng, m) for m in mean]
    if kind == 'gap':  # feed outage: weeks arrive empty (zero-filled)
        s = rng.randint(warmup + 4, weeks - 6)
        for t in range(s, min(weeks, s + rng.randint(1, 4))): counts[t] = 0
    elif kind == 'batch':  # a missing batch: one site's file never arrived, 40-70% of the week missing
        s = rng.randint(warmup + 4, weeks - 4)
        for t in range(s, min(weeks, s + rng.randint(1, 3))): counts[t] = int(counts[t] * rng.uniform(0.3, 0.6))
    return {'kind': kind, 'counts': counts, 'change': change, 'lagged': kind == 'lag'}


def observed(series, t):
    """Counts as received at the end of week t: under 'lag' the latest weeks are incomplete (runout)."""
    out = list(series['counts'][:t + 1])
    if series['lagged']:
        for back, f in enumerate(LAG_FACTORS):
            i = t - back
            if i >= 0: out[i] = int(out[i] * f)
    return out


# ---- feed-gap awareness -----------------------------------------------------------------------------------------------
def _poisson_cdf(k, lam):
    if lam <= 0: return 1.0
    if lam > 50:
        return 0.5 * (1 + math.erf((k + 0.5 - lam) / math.sqrt(2 * lam)))
    term = total = math.exp(-lam)
    for i in range(1, int(k) + 1):
        term *= lam / i; total += term
    return min(1.0, total)


def gap_weeks(counts, baseline):
    """Weeks whose count is implausibly low under the baseline Poisson rate (P(X <= x) < GAP_P and below 60% of it): the
    zero-run / missing-batch signature of a feed problem, not a utilization change."""
    return {i for i, c in enumerate(counts) if c < 0.6 * baseline and _poisson_cdf(c, baseline) < GAP_P}


def prepare(counts, warm, aware, lagged_ok=True):
    """-> (times, values, baseline) for the detectors; gap_aware drops gap weeks and completes runout."""
    baseline = statistics.mean(counts[:warm]) if warm else statistics.mean(counts)
    vals = list(counts)
    if aware and lagged_ok:
        n = len(vals)
        for back, f in enumerate(LAG_FACTORS):
            i = n - 1 - back
            if i >= warm: vals[i] = vals[i] / f
    drop = gap_weeks(counts, baseline) if aware else set()
    keep = [i for i in range(len(vals)) if i not in drop or i < warm]
    return keep, [vals[i] for i in keep], baseline


# ---- detector statistics (one value per monitoring week) --------------------------------------------------------------
def _z(values, baseline):
    sd = math.sqrt(max(baseline, 1.0))
    return [(v - baseline) / sd for v in values]


def cusum_stat(z, k=0.5):
    hi = lo = 0.0; out = []
    for x in z:
        hi = max(0.0, hi + x - k); lo = max(0.0, lo - x - k); out.append(max(hi, lo))
    return out


def page_hinkley_stat(z, delta=0.5):
    """Two-sided Page-Hinkley on the standardized series (running mean from the warm-up level 0)."""
    mu, n, m_up, m_dn, min_up, max_dn, out = 0.0, 0, 0.0, 0.0, 0.0, 0.0, []
    for x in z:
        n += 1; mu += (x - mu) / n
        m_up += x - mu - delta; min_up = min(min_up, m_up)
        m_dn += x - mu + delta; max_dn = max(max_dn, m_dn)
        out.append(max(m_up - min_up, max_dn - m_dn))
    return out


def ewma_stat(z, lam=0.2):
    e, t, out = 0.0, 0, []
    for x in z:
        t += 1; e = lam * x + (1 - lam) * e
        out.append(abs(e) / math.sqrt(lam / (2 - lam) * (1 - (1 - lam) ** (2 * t))))
    return out


def bb_stat(times, values, baseline, recent=RECENT, penalty=6.0, unit_sd=None):
    """Existing Bayesian-blocks program (signal_math.gaussian_change_points, as in the trajectory engine) on the prefix:
    z of the newest block vs the one before when the newest block began within `recent` weeks, else 0."""
    from ...signal_math import gaussian_change_points
    errors = [unit_sd] * len(values) if unit_sd else [math.sqrt(max(v, baseline * 0.25, 1.0)) for v in values]
    seg = gaussian_change_points([float(t) for t in times], [float(v) for v in values], errors, penalty=penalty)
    blocks = seg['blocks']
    if len(blocks) < 2 or blocks[-1]['start_time'] < times[-1] - recent: return 0.0
    a, b = blocks[-2], blocks[-1]
    return abs(b['weighted_mean'] - a['weighted_mean']) / math.hypot(a['standard_error'], b['standard_error'])


def paths(series, method, variant, warm=WARMUP):
    """{monitoring week t: statistic} for t in [warm, len)."""
    n = len(series['counts'])
    out = {}
    if method == 'bayesian_blocks':
        for t in range(warm, n):
            keep, vals, base = prepare(observed(series, t), warm, variant == 'gap_aware', lagged_ok=series['lagged'])
            out[t] = bb_stat(keep, vals, base)
        return out
    fn = {'cusum': cusum_stat, 'page_hinkley': page_hinkley_stat, 'ewma': ewma_stat}[method]
    if not series['lagged']:  # complete data: one pass gives every prefix's statistic
        keep, vals, base = prepare(series['counts'], warm, variant == 'gap_aware', lagged_ok=False)
        mon = [(i, v) for i, v in zip(keep, vals) if i >= warm]
        stat = fn(_z([v for _, v in mon], base))
        last = 0.0
        pos = {i: s for (i, _), s in zip(mon, stat)}
        for t in range(warm, n):
            last = pos.get(t, last); out[t] = last
        return out
    for t in range(warm, n):  # runout: the latest weeks change as claims arrive, so each week re-reads its prefix
        keep, vals, base = prepare(observed(series, t), warm, variant == 'gap_aware')
        mon = [v for i, v in zip(keep, vals) if i >= warm]
        out[t] = fn(_z(mon, base))[-1] if mon else 0.0
    return out


def first_alarm(path, h):
    return next((t for t in sorted(path) if path[t] > h), None)


# ---- calibration and scoring ------------------------------------------------------------------------------------------
def calibrate(null_paths, budget):
    """Smallest threshold whose any-alarm rate on the calibration null series is <= budget."""
    maxima = sorted(max(p.values(), default=0.0) for p in null_paths)
    if not maxima: return math.inf
    k = int(math.floor(budget * len(maxima)))  # at most k series may exceed the threshold
    return maxima[len(maxima) - 1 - k] + 1e-9 if k < len(maxima) else 0.0


def score(method, variant, sets, budget):
    P = {key: [paths(s, method, variant) for s in group] for key, group in sets.items()}
    h = calibrate(P['null_cal'], budget)
    rate = lambda key: round(sum(first_alarm(p, h) is not None for p in P[key]) / len(P[key]), 4)
    delays, detected, early = [], 0, 0
    for s, p in zip(sets['shift'], P['shift']):
        t = first_alarm(p, h)
        if t is None: continue
        if t < s['change']: early += 1; continue
        detected += 1; delays.append(t - s['change'])
    return {'method': method, 'variant': variant, 'threshold': round(h, 4), 'false_alarm_rate': rate('null_test'),
            'gap_false_alarm_rate': round((rate('gap') * len(sets['gap']) + rate('batch') * len(sets['batch'])) / (len(sets['gap']) + len(sets['batch'])), 4),
            'zero_gap_false_alarm_rate': rate('gap'), 'missing_batch_false_alarm_rate': rate('batch'),
            'lag_false_alarm_rate': rate('lag'), 'detection_rate': round(detected / len(sets['shift']), 4),
            'alarm_before_change_rate': round(early / len(sets['shift']), 4),
            'median_delay_weeks': statistics.median(delays) if delays else None}


def qualify(seed=20261018, n=150, budget=0.1):
    """Run the whole experiment. -> the qualification document (also the JSON written by the CLI)."""
    rng = random.Random(seed)
    sets = {'null_cal': [simulate('null', rng) for _ in range(n)], 'null_test': [simulate('null', rng) for _ in range(n)],
            'shift': [simulate('shift', rng) for _ in range(n)], 'gap': [simulate('gap', rng) for _ in range(n)],
            'batch': [simulate('batch', rng) for _ in range(n)], 'lag': [simulate('lag', rng) for _ in range(n)]}
    results = [score(m, v, sets, budget) for m in METHODS for v in VARIANTS]
    return {'version': VERSION, 'seed': seed, 'series_per_scenario': n, 'weeks': WEEKS, 'warmup_weeks': WARMUP,
            'false_alert_budget': budget, 'results': results, **decide(results, budget),
            'design': ('Weekly Poisson utilization (25-80/week, up to 6% seasonality); genuine level shifts of x0.70-0.75 or '
                       'x1.25-1.30; zero-filled feed outages of 1-4 weeks; missing batches removing 40-70% of 1-3 weeks; claim '
                       f'runout leaving {", ".join(f"{f:.0%}" for f in LAG_FACTORS)} of the latest three weeks received. Thresholds '
                       f'calibrated so at most {budget:.0%} of clean null series alarm in {WEEKS - WARMUP} monitoring weeks.'),
            'keep_rule': ('A method qualifies over CUSUM/Page-Hinkley only if its median detection delay is >= 20% shorter at the '
                          'same false-alert budget and its gap false-alarm rate does not exceed the budget.'),
            'scope': 'Synthetic simulation; bounded engineering evidence about detector behaviour, not clinical validation.'}


def decide(results, budget):
    by = {(r['method'], r['variant']): r for r in results}
    def ok(r): return r['median_delay_weeks'] is not None and r['gap_false_alarm_rate'] <= budget + 1e-9
    base = [by[(m, v)] for m in BASELINES for v in VARIANTS if ok(by[(m, v)])]
    if not base:
        base = [by[(m, v)] for m in BASELINES for v in VARIANTS if by[(m, v)]['median_delay_weeks'] is not None]
    ref = min(base, key=lambda r: (r['median_delay_weeks'], r['gap_false_alarm_rate'])) if base else None
    rows = []
    for r in results:
        if r['method'] in BASELINES or ref is None: continue
        improves = ok(r) and r['median_delay_weeks'] <= 0.8 * ref['median_delay_weeks']
        rows.append({'method': r['method'], 'variant': r['variant'], 'qualifies': bool(improves),
                     'delay_vs_reference': (round(1 - r['median_delay_weeks'] / ref['median_delay_weeks'], 3)
                                            if r['median_delay_weeks'] is not None and ref['median_delay_weeks'] else None)})
    qualified = [x for x in rows if x['qualifies']]
    if qualified:
        best = min(qualified, key=lambda x: by[(x['method'], x['variant'])]['median_delay_weeks'])
        winner = by[(best['method'], best['variant'])]
    else:
        winner = ref
    return {'reference': {k: ref[k] for k in ('method', 'variant', 'median_delay_weeks', 'gap_false_alarm_rate')} if ref else None,
            'challengers': rows, 'qualified_method': winner['method'] if winner else None,
            'qualified_variant': winner['variant'] if winner else None,
            'qualified': {k: winner[k] for k in ('method', 'variant', 'threshold', 'false_alarm_rate', 'gap_false_alarm_rate',
                                                 'lag_false_alarm_rate', 'detection_rate', 'median_delay_weeks')} if winner else None}


# ---- consumption by the engines ---------------------------------------------------------------------------------------
def verdict(path=None):
    """The saved qualification (config/agere/changepoint_qualification.json) or None. Never raises."""
    p = Path(path or os.environ.get('AGERE_CHANGEPOINT_QUALIFICATION') or RESULT_FILE)
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def summary(doc=None):
    doc = doc if doc is not None else verdict()
    if not doc or not doc.get('qualified'): return None
    by = {(r['method'], r['variant']): r for r in doc['results']}
    bb = by.get(('bayesian_blocks', 'gap_aware')) or {}
    return {'version': doc['version'], 'qualified_method': doc['qualified_method'], 'qualified_variant': doc['qualified_variant'],
            'qualified': doc['qualified'], 'reference': doc.get('reference'), 'false_alert_budget': doc['false_alert_budget'],
            'bayesian_blocks_gap_aware': {k: bb.get(k) for k in ('median_delay_weeks', 'gap_false_alarm_rate', 'false_alarm_rate')},
            'bayesian_blocks_qualifies': any(c['qualifies'] for c in doc.get('challengers') or [] if c['method'] == 'bayesian_blocks'),
            'source': 'config/agere/changepoint_qualification.json'}


def note(doc=None):
    """One plain sentence for an engine's method / limitations. None when no qualification has been run."""
    s = summary(doc)
    if not s: return None
    q, bb = s['qualified'], s['bayesian_blocks_gap_aware']
    role = 'reference detector (no challenger met the keep rule)' if q['method'] in BASELINES else 'qualified over CUSUM/Page-Hinkley'
    return (f"Change-point qualification (synthetic, {s['false_alert_budget']:.0%} false-alert budget): {q['method'].replace('_', ' ')} "
            f"({q['variant'].replace('_', '-')}) is the {role}, median delay {q['median_delay_weeks']} weeks, gap false alarms "
            f"{q['gap_false_alarm_rate']:.0%}, detection {q['detection_rate']:.0%}; Bayesian Blocks (gap-aware) delay "
            f"{bb['median_delay_weeks']} weeks, gap false alarms {bb['gap_false_alarm_rate']:.0%}"
            + (' (qualifies over CUSUM/Page-Hinkley).' if s['bayesian_blocks_qualifies'] else
               ' (did not meet the 20% faster-than-CUSUM/Page-Hinkley keep rule).'))


def chart_signals(points, excluded=(), doc=None):
    """The qualified detector run on one monitor chart's standardized points ([{'month', 'z'}]), gap months excluded.
    -> {'method', 'variant', 'threshold', 'signal_months'} or None."""
    s = summary(doc)
    if not s: return None
    q = s['qualified']
    pts = [p for p in points if p.get('z') is not None and (q['variant'] != 'gap_aware' or p.get('month') not in set(excluded))]
    z = [float(p['z']) for p in pts]
    if q['method'] == 'bayesian_blocks':
        stat = [bb_stat(list(range(i + 1)), z[:i + 1], 0.0, unit_sd=1.0) if i >= 3 else 0.0 for i in range(len(z))]
    else:
        stat = {'cusum': cusum_stat, 'page_hinkley': page_hinkley_stat, 'ewma': ewma_stat}[q['method']](z)
    return {'method': q['method'], 'variant': q['variant'], 'threshold': q['threshold'],
            'signal_months': [p['month'] for p, v in zip(pts, stat) if v > q['threshold']][:12]}
