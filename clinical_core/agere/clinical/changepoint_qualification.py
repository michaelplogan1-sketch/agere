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
Current qualification uses simultaneous bounds for clean-null, gap, missing-batch, runout and early alarms; comparable
paired sensitivity and at least 20% lower miss-adjusted delay loss with simultaneous uncertainty. Conditional median delay
is descriptive. Older metric-only verdicts are unqualified under this version. The verdict is written by
tools/changepoint_qualification.py to config/agere/changepoint_qualification.json and read back by the trajectory and monitor
engines (verdict(), note()). Synthetic simulation only; this is bounded engineering evidence, not clinical validation.
"""
import json
import math
import os
import random
import statistics
from pathlib import Path

VERSION = 'changepoint-qualification/2'
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
    alarms = {key: sum(first_alarm(path, h) is not None for path in group) for key,group in P.items() if key != 'shift'}
    rate = lambda key: alarms[key] / len(P[key])
    delays, detected, early, losses, detections = [], 0, 0, [], []
    for item, path in zip(sets['shift'], P['shift']):
        t = first_alarm(path, h); duration = len(item['counts']) - item['change']
        found = t is not None and t >= item['change']
        detections.append(int(found)); losses.append((t-item['change']) / duration if found else 1.)
        if t is not None and t < item['change']: early += 1
        if found: detected += 1; delays.append(t-item['change'])
    n = len(sets['shift']); gap_n = len(sets['gap']) + len(sets['batch'])
    counts = {field: {'events':alarms[key], 'n':len(P[key])} for field,key in [
        ('false_alarm_rate','null_test'), ('zero_gap_false_alarm_rate','gap'),
        ('missing_batch_false_alarm_rate','batch'), ('lag_false_alarm_rate','lag')]}
    counts.update(alarm_before_change_rate={'events':early,'n':n}, detection_rate={'events':detected,'n':n},
                  gap_false_alarm_rate={'events':alarms['gap']+alarms['batch'],'n':gap_n})
    import hashlib
    identity = hashlib.sha256(json.dumps(sets,sort_keys=True).encode()).hexdigest()
    return {'method':method, 'variant':variant, 'threshold':float(h),
            **{field: round(v['events']/v['n'],4) for field,v in counts.items()}, 'scenario_counts':counts,
            'median_delay_weeks':statistics.median(delays) if delays else None,
            'miss_adjusted_delay_loss':statistics.mean(losses), 'shift_loss_samples':losses,
            'shift_detection_samples':detections, 'evaluation_identity':identity}


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
            'keep_rule': ('Joint simultaneous clean-null, gap, missing-batch, runout and early-alarm bounds; comparable sensitivity on paired trials and >=20% lower miss-adjusted loss with simultaneous uncertainty. No implicit qualified fallback.'),
            'scope': 'Synthetic simulation; bounded engineering evidence about detector behaviour, not clinical validation.'}


FALSE_ALERT_FIELDS = ('false_alarm_rate','zero_gap_false_alarm_rate','missing_batch_false_alarm_rate',
                      'lag_false_alarm_rate','alarm_before_change_rate')


def decide(results, budget, alpha=0.05):
    """Joint scenario bounds and paired, miss-adjusted loss; no implicit qualified fallback.

    Bonferroni allocates half alpha to all scenario bounds and half to all possible challenger/reference loss
    comparisons, so selecting a comparator does not avoid multiplicity. Assumes independent synthetic series
    and exact paired evaluation identity. Conditional median delay is descriptive, not a miss-adjusted objective.
    """
    from .cascade import upper_bound
    if not 0 < budget < 1 or not 0 < alpha < 1: raise ValueError('budget and alpha must lie in (0,1)')
    rows = list(results); bases = [r for r in rows if r.get('method') in BASELINES]
    challengers = [r for r in rows if r.get('method') not in BASELINES]
    delta = alpha / (2 * max(len(rows),1) * len(FALSE_ALERT_FIELDS))
    loss_delta = alpha / (2 * max(len(bases)*len(challengers),1))
    def evidence(r):
        reasons=[]; bounds={}; counts=r.get('scenario_counts') or {}
        if r.get('method') not in METHODS or r.get('variant') not in VARIANTS:
            reasons.append('unsupported detector method or variant')
        if not isinstance(r.get('threshold'),(int,float)) or not math.isfinite(r['threshold']) or r['threshold']<0:
            reasons.append('missing finite nonnegative detector threshold')
        for field in [*FALSE_ALERT_FIELDS,'gap_false_alarm_rate','detection_rate']:
            c=counts.get(field) or {}; k,n=c.get('events'),c.get('n')
            if type(k) is not int or type(n) is not int or not 0<=k<=n or n<=0:
                reasons.append(field+': missing settled scenario counts');continue
            value=r.get(field)
            if not isinstance(value,(int,float)) or not math.isfinite(value) or abs(value-k/n)>5.01e-5:
                reasons.append(field+': reported metric disagrees with counts');continue
            if field in FALSE_ALERT_FIELDS:
                bounds[field]=upper_bound(k,n,delta)
                if bounds[field]>budget:reasons.append(field+': upper bound exceeds declared budget')
        loss=r.get('shift_loss_samples') or []; detections=r.get('shift_detection_samples') or []
        n=(counts.get('detection_rate') or {}).get('n',0)
        n=n if type(n) is int and n>0 else 0
        valid_loss=isinstance(loss,(list,tuple)) and bool(loss) and len(loss)==n and all(isinstance(x,(int,float)) and math.isfinite(x) and 0<=x<=1 for x in loss)
        valid_detections=isinstance(detections,(list,tuple)) and len(detections)==n and all(type(x) in (int,bool) and x in (0,1) for x in detections) and sum(detections)==(counts.get('detection_rate') or {}).get('events')
        if not isinstance(r.get('evaluation_identity'),str) or not r['evaluation_identity']:
            reasons.append('missing paired evaluation identity')
        if not valid_loss:
            reasons.append('missing bounded miss-adjusted delay samples')
        if not valid_detections:
            reasons.append('detection samples do not reconcile')
        parts=[counts.get(k) or {} for k in ('zero_gap_false_alarm_rate','missing_batch_false_alarm_rate','gap_false_alarm_rate')]
        if all(type(c.get('n')) is int and type(c.get('events')) is int for c in parts):
            if parts[2]['n']!=parts[0]['n']+parts[1]['n'] or parts[2]['events']!=parts[0]['events']+parts[1]['events']:
                reasons.append('aggregate gap counts do not reconcile with the two gap scenarios')
        if valid_loss and (not isinstance(r.get('miss_adjusted_delay_loss'),(int,float)) or not math.isfinite(r['miss_adjusted_delay_loss']) or abs(r['miss_adjusted_delay_loss']-statistics.mean(loss))>1e-8):
            reasons.append('reported miss-adjusted loss disagrees with samples')
        if valid_loss and valid_detections and any((not flag and loss!=1) or (flag and loss>=1) for loss,flag in zip(loss,detections)):
            reasons.append('miss/detection outcomes disagree with delay loss')
        if not reasons and not any(detections):reasons.append('no genuine shift detected')
        return {'eligible':not reasons,'reasons':reasons,'false_alert_upper_bounds':bounds,
                'mean_miss_adjusted_delay_loss':statistics.mean(loss) if valid_loss else None,
                'detection_interval':([1-upper_bound(n-sum(detections),n,alpha/2),upper_bound(sum(detections),n,alpha/2)] if n and valid_detections else [None,None])}
    assessments=[evidence(r) for r in rows]; lookup={id(r):a for r,a in zip(rows,assessments)}
    eligible=[r for r in bases if lookup[id(r)]['eligible']]
    ref=min(eligible,key=lambda r:lookup[id(r)]['mean_miss_adjusted_delay_loss']) if eligible else None
    diagnostic=ref or next((r for r in bases if r.get('median_delay_weeks') is not None),None)
    compact=lambda r: ({k:r.get(k) for k in ('method','variant','threshold','false_alarm_rate','gap_false_alarm_rate',
                      'zero_gap_false_alarm_rate','missing_batch_false_alarm_rate','lag_false_alarm_rate',
                      'detection_rate','median_delay_weeks','miss_adjusted_delay_loss')} if r else None)
    outcomes=[]
    for r in challengers:
        a=lookup[id(r)]; reasons=list(a['reasons']); upper=None
        if ref is None:reasons.append('no reference passes the joint evidence constraints')
        elif a['eligible']:
            if r['evaluation_identity']!=ref['evaluation_identity'] or len(r['shift_loss_samples'])!=len(ref['shift_loss_samples']):
                reasons.append('incomparable paired shift populations')
            elif sum(r['shift_detection_samples'])<sum(ref['shift_detection_samples']):
                reasons.append('sensitivity fell below reference on the same shift trials')
            else:
                differences=[x-.8*y for x,y in zip(r['shift_loss_samples'],ref['shift_loss_samples'])]
                upper=statistics.mean(differences)+1.8*math.sqrt(math.log(1/loss_delta)/(2*len(differences)))
                if upper>0:reasons.append('20% miss-adjusted loss improvement not established by simultaneous Hoeffding bound')
        outcomes.append({'method':r.get('method'),'variant':r.get('variant'),'qualifies':not reasons,'reasons':reasons,
                         'loss_difference_upper_bound':upper,'evidence':a,
                         'sensitivity_comparison': {'observed_rate':r.get('detection_rate'),
                            'reference_rate':ref.get('detection_rate') if ref else None,
                            'marginal_interval':a['detection_interval'], 'formal_noninferiority_certified':False,
                            'meaning':'Observed detection cannot fall below the paired reference; the simultaneous certificate concerns joint false-alert risk and miss-adjusted loss, not a separate sensitivity noninferiority guarantee.'}})
    winners=[r for r,o in zip(challengers,outcomes) if o['qualifies']]
    winner=min(winners,key=lambda r:lookup[id(r)]['mean_miss_adjusted_delay_loss']) if winners else None
    return {'reference':compact(ref),'diagnostic_reference':{**compact(diagnostic),'qualified':False} if diagnostic else None,
            'challengers':outcomes,'qualified_method':winner['method'] if winner else None,
            'qualified_variant':winner['variant'] if winner else None,'qualified':compact(winner),
            'assessments':[{'method':r.get('method'),'variant':r.get('variant'),**a} for r,a in zip(rows,assessments)],
            'joint_alpha':alpha,'false_alert_fields':list(FALSE_ALERT_FIELDS),'qualification_version':VERSION,
            'assumptions':'Independent synthetic series; same paired evaluation population; binomial one-sided bounds and bounded paired-loss Hoeffding bounds. No clinical qualification.'}


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
    if not doc: return None
    if doc.get('version')!=VERSION or doc.get('qualification_version')!=VERSION:
        return {'version':VERSION,'qualified_method':None,'qualified_variant':None,'qualified':None,'reference':None,
                'diagnostic_reference':None,'reason':'Saved qualification lacks current joint-scenario/count/paired-loss evidence; requalification is required.',
                'false_alert_budget':doc.get('false_alert_budget'),'clinical_qualification':False}
    try: current=decide(doc.get('results') or [],doc['false_alert_budget'],doc.get('joint_alpha',.05))
    except (ValueError,TypeError,KeyError,OverflowError):
        return {'version':VERSION,'qualified_method':None,'qualified':None,'reason':'Qualification evidence is malformed; no method selected.'}
    return {'version':VERSION,'false_alert_budget':doc['false_alert_budget'],
            **{k:current.get(k) for k in ('qualified_method','qualified_variant','qualified','reference','diagnostic_reference',
                                        'joint_alpha','false_alert_fields','assumptions')}}



def note(doc=None):
    s=summary(doc)
    if not s:return None
    q=s.get('qualified')
    if not q:return 'Change-point qualification: no method currently qualifies under the joint evidence rule; any reference is diagnostic only. '+s.get('reason','Synthetic engineering evidence, not clinical validation.')
    return (f"Change-point qualification (synthetic, {s['false_alert_budget']:.0%} joint scenario false-alert budget): "
            f"{q['method']} ({q['variant']}) passed joint scenario bounds and paired miss-adjusted delay loss against its reference. "
            'Not clinical validation; conditional median delay alone is not the qualification target.')


def chart_signals(points, excluded=(), doc=None):
    """The qualified detector run on one monitor chart's standardized points ([{'month', 'z'}]), gap months excluded.
    -> {'method', 'variant', 'threshold', 'signal_months'} or None."""
    s = summary(doc)
    if not s or not s.get('qualified'): return None
    q = s['qualified']
    pts = [p for p in points if p.get('z') is not None and (q['variant'] != 'gap_aware' or p.get('month') not in set(excluded))]
    z = [float(p['z']) for p in pts]
    if q['method'] == 'bayesian_blocks':
        stat = [bb_stat(list(range(i + 1)), z[:i + 1], 0.0, unit_sd=1.0) if i >= 3 else 0.0 for i in range(len(z))]
    else:
        stat = {'cusum': cusum_stat, 'page_hinkley': page_hinkley_stat, 'ewma': ewma_stat}[q['method']](z)
    return {'method': q['method'], 'variant': q['variant'], 'threshold': q['threshold'],
            'qualification_scope': 'synthetic weekly scenarios', 'live_chart_transfer_qualified': False,
            'signal_months': [p['month'] for p, v in zip(pts, stat) if v > q['threshold']][:12]}
