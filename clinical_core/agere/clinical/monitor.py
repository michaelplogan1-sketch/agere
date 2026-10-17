"""Statistical process control and anomaly detection over the semantic layer ('monitor' operation).

Three families of checks, each stating its population, denominator and method:

1. Control charts per month for key series (ED visits and admissions per 1,000
   member-months, completed allowed PMPM, lab volume, claim denial rate, claim
   arrival lag), overall or per practice / payer / facility / source system.
   u-charts and p-charts carry per-point limits for varying denominators; PMPM and
   arrival lag use x-bar limits (pooled per-unit SD / sqrt(n)). Overdispersion is
   handled with Laney's sigma-z (robust median moving range), applied only to widen
   limits. Western Electric / Nelson rules (beyond 3 sigma, 2 of 3 beyond 2 sigma,
   4 of 5 beyond 1 sigma, 8 in a row on one side, 6 in a row trending) and an
   optional EWMA chart run on the standardised series.
2. Data-feed anomalies are separated from rate anomalies: per stream x source-system
   monthly record volume (gaps, drops, spikes), event types that vanish while their
   stream keeps flowing, and single-month magnitudes implausible for a change in care.
3. Claims integrity review flags: Benford first-digit conformity (chi-square and
   Nigrini MAD, with sample-size and applicability guards), peer outliers in
   case-mix-adjusted billing intensity (empirical-Bayes shrunk log O/E, robust z
   within peer type), and duplicate-claim heuristics. These are review flags for a
   person to check, never findings of wrongdoing.

Every finding carries a surprise in bits (-log2 p under the stated baseline), the
look-elsewhere cost of the number of tests examined, and the patients / dollars
affected, so findings can be ranked by information x impact.
"""
from collections import defaultdict
from datetime import date
import math, re, sqlite3

import numpy as np
from scipy.stats import chi2, norm

from . import semantic_layer, vbc

VERSION = 'monitor/1'
CHECKS = ('all', 'control_chart', 'feed', 'claims', 'claims_integrity', 'benford', 'peer_outliers', 'duplicates', 'ra_cusum', 'vlad')
SERIES = ('ed_rate', 'admit_rate', 'pmpm', 'lab_volume', 'denial_rate', 'arrival_lag')
CLAIM_SERIES = ('pmpm', 'denial_rate', 'arrival_lag')
GROUP_BY = ('auto', 'none', 'practice', 'payer', 'facility', 'source_system')
METHODS = ('both', 'shewhart', 'ewma')
AMOUNTS = ('billed', 'allowed', 'both')
FIELDS = {'check', 'series', 'group_by', 'method', 'amount', 'stream', 'period'} | {'chart', 'outcome', 'unit', 'or_shift', 'or_improve', 'arl0',
                                                                                    'window_days', 'adjust', 'min_cases', 'include_observation'}
NATURAL_GROUP = {'ed_rate': 'practice', 'admit_rate': 'practice', 'pmpm': 'practice', 'lab_volume': 'source_system',
                 'denial_rate': 'payer', 'arrival_lag': 'payer'}
FEED_STREAM = {'ed_rate': 'encounters', 'admit_rate': 'encounters', 'pmpm': 'medical_claims', 'lab_volume': 'labs',
               'denial_rate': 'medical_claims', 'arrival_lag': 'medical_claims'}
LABELS = {'ed_rate': 'ED visits per 1,000 member-months', 'admit_rate': 'admissions per 1,000 member-months',
          'pmpm': 'completed allowed PMPM ($)', 'lab_volume': 'lab results per 1,000 member-months',
          'denial_rate': 'claim first-pass denial rate', 'arrival_lag': 'mean claim arrival lag (days)'}
SCALE = {'ed_rate': 1000.0, 'admit_rate': 1000.0, 'lab_volume': 1000.0, 'pmpm': 1.0, 'denial_rate': 1.0, 'arrival_lag': 1.0}
MAX_BITS = 40.0
CHART_MONTHS = 36
BENFORD = [math.log10(1 + 1 / d) for d in range(1, 10)]
# Nigrini first-digit MAD conformity bands.
MAD_BANDS = [(0.006, 'close_conformity'), (0.012, 'acceptable_conformity'), (0.015, 'marginal_conformity'), (math.inf, 'nonconformity')]
LIMITATIONS = [
    'Signals mean "investigate", not "worse care": rules have false alarms, and many charts are examined at once '
    '(each finding reports its look-elsewhere cost).',
    'Control limits assume the in-control process is roughly stable; Laney widening absorbs overdispersion but not seasonality.',
    'Recent claim months are completion-adjusted (PMPM) or excluded until runout (arrival lag); denial rates for the latest '
    'months can still move as claims arrive.',
    'Claims-integrity results are review flags for a person to check. Benford conformity, billing intensity and duplicate '
    'patterns have legitimate explanations (fee schedules, service mix, repeat services) and are never evidence of wrongdoing.',
    'Payer for member-based series = the member\'s coverage type; payer for claim-based series = the adjudicating payer '
    '(claims source system).',
]


# ---- parameters ------------------------------------------------------------------------
def validate(p):
    if not isinstance(p, dict) or set(p) - FIELDS:
        raise ValueError(f'monitor parameters must be a subset of {sorted(FIELDS)}')
    check = p.get('check', 'all')
    if check not in CHECKS: raise ValueError(f'monitor check must be one of {CHECKS}')
    series = p.get('series') or []
    series = [series] if isinstance(series, str) else series
    if not isinstance(series, list) or any(s not in SERIES for s in series) or len(set(series)) != len(series):
        raise ValueError(f'monitor series must be distinct values of {SERIES}')
    group_by = p.get('group_by', 'auto')
    if group_by not in GROUP_BY: raise ValueError(f'monitor group_by must be one of {GROUP_BY}')
    method = p.get('method', 'both')
    if method not in METHODS: raise ValueError(f'monitor method must be one of {METHODS}')
    amount = p.get('amount', 'both')
    if amount not in AMOUNTS: raise ValueError(f'monitor amount must be one of {AMOUNTS}')
    stream = p.get('stream', '')
    if not isinstance(stream, str) or (stream and not re.fullmatch(r'[a-z][a-z0-9_]{0,60}', stream)):
        raise ValueError('monitor stream must be a source stream name or empty')
    period = p.get('period') or {}
    if not isinstance(period, dict) or set(period) - {'start', 'end'} or \
            any(not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(v)) for v in period.values() if v):
        raise ValueError('monitor period needs YYYY-MM-DD start/end')
    out = {'check': check, 'series': series, 'group_by': group_by, 'method': method, 'amount': amount, 'stream': stream,
           'period': {k: v for k, v in period.items() if v}}
    chart = p.get('chart') or (check if check in ('ra_cusum', 'vlad') else None)
    if chart is not None:
        if chart not in CHART_OPTIONS: raise ValueError(f'monitor chart must be one of {CHART_OPTIONS}')
        if chart != 'control': out.update(_validate_ra(p, chart, group_by))
        else: out['chart'] = 'control'
    return out


def _validate_ra(p, chart, group_by):
    def num(name, default, lo, hi, kind=float):
        v = p.get(name, default)
        if v is None: return None
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo < v < hi:
            raise ValueError(f'monitor {name} must be a number between {lo} and {hi} (exclusive)')
        return kind(v)
    outcome = p.get('outcome') or 'readmission'
    if outcome not in RA_OUTCOMES: raise ValueError(f'monitor outcome must be one of {RA_OUTCOMES}')
    unit = p.get('unit') or {'practice': 'practice', 'facility': 'hospital', 'none': 'none'}.get(group_by, 'hospital')
    if unit not in RA_UNITS: raise ValueError(f'monitor unit must be one of {RA_UNITS}')
    adjust = p.get('adjust') or 'age_sex_conditions'
    if adjust not in RA_ADJUST: raise ValueError(f'monitor adjust must be one of {RA_ADJUST}')
    window = p.get('window_days', 30)
    if type(window) is not int or not 1 <= window <= 365: raise ValueError('monitor window_days must be an integer 1..365')
    arl0 = p.get('arl0', 5000)
    if type(arl0) is not int or not 100 <= arl0 <= 100000: raise ValueError('monitor arl0 must be an integer 100..100000 (cases)')
    min_cases = p.get('min_cases', 30)
    if type(min_cases) is not int or not 10 <= min_cases <= 100000: raise ValueError('monitor min_cases must be an integer 10..100000')
    obs = p.get('include_observation', False)
    if type(obs) is not bool: raise ValueError('monitor include_observation must be true or false')
    return {'chart': chart, 'outcome': outcome, 'unit': unit, 'or_shift': num('or_shift', 2.0, 1.0, 20.0),
            'or_improve': num('or_improve', None, 0.0, 1.0), 'arl0': arl0, 'window_days': window, 'adjust': adjust,
            'min_cases': min_cases, 'include_observation': obs}


# ---- information ----------------------------------------------------------------------
def bits(p):
    """Surprise of an event of probability p, in bits (capped)."""
    if p is None or not math.isfinite(p): return 0.0
    return round(min(MAX_BITS, -math.log2(max(p, 2.0 ** -MAX_BITS))), 2)


def z_bits(z):
    """Two-sided normal-tail surprise of a standardised deviation."""
    return bits(2 * norm.sf(abs(z)))


def kl_bits(observed, expected):
    """KL divergence D(observed || expected) in bits per observation."""
    return float(sum(o * math.log2(o / e) for o, e in zip(observed, expected) if o > 0 and e > 0))


def score(surprise_bits, impact):
    """Information x impact: surprise (bits) times log2 of the patients (or member-equivalents) affected."""
    return round(max(surprise_bits, 0.0) * math.log2(2 + max(impact or 0, 0)), 2)


# ---- control charts -------------------------------------------------------------------
def _sigma(kind, center, n, sd):
    if n is None or n <= 0: return None
    if kind == 'u': return math.sqrt(max(center, 1e-12) / n)
    if kind == 'p': return math.sqrt(max(center * (1 - center), 1e-12) / n)
    if kind == 'c': return math.sqrt(max(center, 1e-12))
    return max(sd, 1e-12) / math.sqrt(n)


def _center(kind, values, ns, keep):
    idx = [i for i in keep if ns[i] and ns[i] > 0]
    if not idx: return None
    if kind == 'c': return float(np.mean([values[i] for i in idx]))
    if kind in ('u', 'p'): return sum(values[i] for i in idx) / sum(ns[i] for i in idx)
    return sum(values[i] * ns[i] for i in idx) / sum(ns[i] for i in idx)


def _weighted_median(kind, values, ns, keep):
    idx = [i for i in keep if ns[i] and ns[i] > 0]
    if not idx: return None
    pts = sorted(((values[i] / ns[i] if kind in ('u', 'p') else values[i]), ns[i] if kind != 'c' else 1) for i in idx)
    half, acc = sum(w for _, w in pts) / 2, 0.0
    for v, w in pts:
        acc += w
        if acc >= half: return v
    return pts[-1][0]


def control_chart(values, ns, kind='u', sd=None, exclude=(), laney=True, sigma_limit=3.0):
    """One control chart. values = event counts (u, p, c) or unit means (x); ns = exposure, trials or units per point.

    Center from included points (excluded = known feed gaps), Phase-I trimmed of points beyond the limits; per-point
    sigma for varying denominators; Laney sigma-z from the median moving range of z, used only to widen limits."""
    m = len(values)
    if kind == 'c': ns = [1 if v is not None else 0 for v in values]
    exclude = set(exclude)
    gaps = []
    if kind in ('u', 'p', 'c'):
        # Feed gaps first: runs of zero counts that the pooled (untrimmed) rate makes implausible. They are data
        # problems, so they never enter the baseline (a 23-month hole would otherwise become the 'normal' level).
        base = [i for i in range(m) if ns[i] and ns[i] > 0 and i not in exclude]
        pooled = _center(kind, values, ns, base)
        if pooled:
            gaps = zero_runs([{'index': i, 'count': values[i] if ns[i] else None,
                               'expected': pooled * (ns[i] if kind in ('u', 'p') else 1) if ns[i] else None} for i in range(m)],
                             min_run=2, min_expected=1.0)
            gaps = [g for g in gaps if g['p'] < 1e-3]
            exclude |= {i for g in gaps for i in range(g['start'], g['end'] + 1)}
    first = [i for i in range(m) if ns[i] and ns[i] > 0 and i not in exclude]
    keep = list(first)
    phi, center = 1.0, None
    for it in range(5):
        # Start from the exposure-weighted median (robust to start-up transients that carry most of the volume),
        # then refine with the mean of the points inside the limits.
        center = _weighted_median(kind, values, ns, keep) if it == 0 else _center(kind, values, ns, keep)
        if center is None: break
        if center == 0 and kind in ('u', 'p', 'c'): center = _center(kind, values, ns, first) or center
        z = {i: ((values[i] / ns[i] if kind in ('u', 'p') else values[i]) - center) / _sigma(kind, center, ns[i], sd) for i in first}
        mr = [abs(z[b] - z[a]) for a, b in zip(first, first[1:])]
        phi = max(1.0, float(np.median(mr)) / 0.954) if laney and len(mr) >= 3 else 1.0
        trimmed = [i for i in first if abs(z[i]) / phi <= sigma_limit]
        # Phase-I trimming never removes more than a third of the usable points.
        floor = max(3, math.ceil(2 * len(first) / 3))
        if len(trimmed) < floor: trimmed = sorted(sorted(first, key=lambda i: abs(z[i]))[:floor])
        if trimmed == keep and it > 0: break
        keep = trimmed
    if center is not None and center == 0 and kind in ('u', 'p', 'c'): center = _center(kind, values, ns, first) or center
    points = []
    for i in range(m):
        n = ns[i]
        if center is None or not n or n <= 0:
            points.append({'index': i, 'value': None, 'n': n, 'z': None}); continue
        rate = values[i] / n if kind in ('u', 'p') else values[i]
        s = _sigma(kind, center, n, sd)
        lo, hi = center - sigma_limit * s * phi, center + sigma_limit * s * phi
        if kind in ('u', 'p', 'c', 'x') and lo < 0 and center >= 0: lo = 0.0
        if kind == 'p': hi = min(hi, 1.0)
        points.append({'index': i, 'value': rate, 'n': n, 'expected': center * (n if kind in ('u', 'p') else 1) if kind != 'x' else None,
                       'count': values[i] if kind in ('u', 'p', 'c') else None,
                       'lcl': lo, 'ucl': hi, 'z': (rate - center) / (s * phi), 'z_unadjusted': (rate - center) / s})
    return {'kind': kind, 'center': center, 'phi': round(phi, 3), 'points': points, 'baseline_points': len(keep),
            'excluded_from_baseline': sorted(exclude), 'gaps': gaps}


def _runs(seq):
    """Split point indices into contiguous runs of available values."""
    run, out = [], []
    for i, v in seq:
        if v is None:
            if run: out.append(run); run = []
        else: run.append((i, v))
    if run: out.append(run)
    return out


Q1, Q2 = norm.sf(1), norm.sf(2)
P_RULE2 = 2 * (3 * Q2 ** 2 * (1 - Q2) + Q2 ** 3)
P_RULE3 = 2 * (5 * Q1 ** 4 * (1 - Q1) + Q1 ** 5)


def rule_signals(points, run_length=8, trend_length=6):
    """Western Electric / Nelson rules over the standardised series. Returns episodes:
    {rule, start, end, side, p, bits}. Consecutive hits of one rule merge into one episode."""
    zs = [(p['index'], p['z']) for p in points]
    vals = [(p['index'], p['value']) for p in points]
    hits = []
    for run in _runs(zs):
        idx = [i for i, _ in run]; z = [v for _, v in run]
        for k, v in enumerate(z):
            if abs(v) > 3:
                hits.append(('beyond_3_sigma', idx[k], idx[k], 1 if v > 0 else -1, 2 * norm.sf(abs(v))))
            for side in (1, -1):
                if k >= 2 and side * v > 2 and sum(side * x > 2 for x in z[k - 2:k + 1]) >= 2:
                    hits.append(('2_of_3_beyond_2_sigma', idx[k - 2], idx[k], side, P_RULE2))
                if k >= 4 and side * v > 1 and sum(side * x > 1 for x in z[k - 4:k + 1]) >= 4:
                    hits.append(('4_of_5_beyond_1_sigma', idx[k - 4], idx[k], side, P_RULE3))
        k = 0
        while k < len(z):  # runs on one side of the center
            side = 1 if z[k] > 0 else -1 if z[k] < 0 else 0
            j = k
            while j + 1 < len(z) and side and (z[j + 1] > 0 if side > 0 else z[j + 1] < 0): j += 1
            if side and j - k + 1 >= run_length:
                hits.append((f'{run_length}_in_a_row_one_side', idx[k], idx[j], side, 2 * 0.5 ** (j - k + 1)))
            k = j + 1
    for run in _runs(vals):
        idx = [i for i, _ in run]; v = [x for _, x in run]
        k = 0
        while k < len(v) - 1:
            d = 1 if v[k + 1] > v[k] else -1 if v[k + 1] < v[k] else 0
            j = k
            while d and j + 1 < len(v) and (v[j + 1] - v[j]) * d > 0: j += 1
            if d and j - k + 1 >= trend_length:
                hits.append((f'{trend_length}_trending', idx[k], idx[j], d, 2 / math.factorial(j - k + 1)))
            k = max(j, k + 1)
    episodes = []
    for rule, s, e, side, p in sorted(hits, key=lambda h: (h[0], h[3], h[1])):
        last = episodes[-1] if episodes else None
        if last and last['rule'] == rule and last['side'] == side and s <= last['end'] + 1:
            last['end'] = max(last['end'], e)
            last['p'] = last['p'] * p if rule == 'beyond_3_sigma' and e > last['end_hit'] else min(last['p'], p)
            last['end_hit'] = e
        else:
            episodes.append({'rule': rule, 'start': s, 'end': e, 'end_hit': e, 'side': side, 'p': p})
    for ep in episodes:
        ep.pop('end_hit'); ep['bits'] = bits(ep['p']); ep['p'] = float(ep['p'])
    return episodes


def ewma_signals(points, lam=0.2, L=2.86):
    """EWMA of the standardised series with exact time-varying limits."""
    out, e, t = [], 0.0, 0
    for p in points:
        if p['z'] is None: continue
        t += 1
        e = lam * p['z'] + (1 - lam) * e
        s = math.sqrt(lam / (2 - lam) * (1 - (1 - lam) ** (2 * t)))
        p['ewma'] = e; p['ewma_limit'] = L * s
        if abs(e) > L * s:
            if out and out[-1]['end'] == p['index'] - 1 and out[-1]['side'] == (1 if e > 0 else -1):
                out[-1]['end'] = p['index']; out[-1]['p'] = min(out[-1]['p'], 2 * norm.sf(abs(e) / s))
            else:
                out.append({'rule': 'ewma', 'start': p['index'], 'end': p['index'], 'side': 1 if e > 0 else -1,
                            'p': float(2 * norm.sf(abs(e) / s))})
    for ep in out: ep['bits'] = bits(ep['p'])
    return out


def zero_runs(points, min_run=3, min_expected=1.0):
    """Runs of zero counts where the baseline expects events: the signature of a feed gap, not a rate change.
    Low-expectation zero months extend a run without qualifying it; min_run counts months expecting >= min_expected.
    A run before the series' first event is labelled not_yet_started (a source that begins later)."""
    out, run = [], []
    seen_event = False
    for p in points + [{'index': None, 'count': None, 'expected': None}]:
        if p.get('count') == 0 and p.get('expected') is not None:
            run.append(p)
            continue
        if sum((x['expected'] or 0) >= min_expected for x in run) >= min_run:
            lam = sum(x['expected'] for x in run)
            leading = not seen_event
            out.append({'rule': 'not_yet_started' if leading else 'zero_run', 'start': run[0]['index'], 'end': run[-1]['index'],
                        'side': -1, 'expected_events': round(lam, 1), 'p': math.exp(-lam),
                        'bits': 0.0 if leading else bits(math.exp(-lam))})
        run = []
        if p.get('count'): seen_event = True
    return out


# ---- data access -----------------------------------------------------------------------
def _connect(path):
    return sqlite3.connect(f'file:{semantic_layer.ensure(path)}?mode=ro', uri=True)


def _shift(month, k):
    y, m = int(month[:4]), int(month[5:7]) + k
    y += (m - 1) // 12; m = (m - 1) % 12 + 1
    return f'{y:04d}-{m:02d}'


def _month_end(month):
    import calendar
    return f'{month}-{calendar.monthrange(int(month[:4]), int(month[5:7]))[1]:02d}'


def windows(p, as_of):
    """Chart months (baseline + reporting) and reporting months. The final month counts only when complete."""
    end = p['period'].get('end') or as_of
    end = min(end, as_of)
    last = end[:7] if end >= _month_end(end[:7]) else _shift(end[:7], -1)
    first_report = (p['period'].get('start') or f'{_shift(last, -(CHART_MONTHS - 1))}-01')[:7]
    first_chart = min(first_report, _shift(last, -(CHART_MONTHS - 1)))
    chart = vbc._months(f'{first_chart}-01', f'{last}-01') if first_chart <= last else []
    report = [m for m in chart if m >= first_report]
    if chart and not report:
        # "this week" / a period inside the incomplete current month: report the latest COMPLETE month and say so
        report = chart[-1:]
        p.setdefault('notes', []).append(f'Requested period has no complete month yet; reporting the latest complete month ({chart[-1]}).')
    return chart, report


class Population:
    """Members, enrolled months, attribution and group keys shared by all member-based series."""
    def __init__(self, db, months, keep):
        start, end = f'{months[0]}-01', _month_end(months[-1])
        self.people = vbc.eligibility(db, end, start, end)
        if keep is not None: self.people = {k: v for k, v in self.people.items() if k in keep}
        self.attr = vbc.attribution(db, self.people, start, end)
        self.months = {pid: set(i['months']) for pid, i in self.people.items() if i['months']}

    def key(self, group_by, pid):
        if group_by == 'none': return 'all'
        if group_by == 'practice': return (self.attr.get(pid) or {}).get('practice') or 'unattributed'
        if group_by == 'payer': return (self.people.get(pid) or {}).get('coverage_type') or 'unknown'
        return None

    def exposure(self, group_by):
        out = defaultdict(lambda: defaultdict(float))
        for pid, ms in self.months.items():
            g = self.key(group_by, pid) if group_by in ('none', 'practice', 'payer') else 'all'
            for m in ms: out[g][m] += 1
        return out

    def patients(self, group_by):
        out = defaultdict(int)
        for pid in self.months:
            out[self.key(group_by, pid) if group_by in ('none', 'practice', 'payer') else 'all'] += 1
        return out


def _series_events(db, series, months, pop, keep):
    """(pid, month, organization, value) rows for one series. value = 1 for count series."""
    lo, hi = f'{months[0]}-01', _month_end(months[-1])
    if series == 'ed_rate':
        rows = db.execute("SELECT patient_id, event_date, max(CASE WHEN source_table = 'encounters' THEN organization_id END), "
                          "max(organization_id) FROM encounter WHERE type_concept = 'emergency_visit' AND event_date BETWEEN ? AND ? "
                          "GROUP BY 1, 2", (lo, hi))
        return [(pid, d[:7], o1 or o2, 1.0) for pid, d, o1, o2 in rows if d[:7] in pop.months.get(pid, ())]
    if series == 'admit_rate':
        return [(pid, d[:7], org, 1.0) for pid, d, org in
                db.execute('SELECT patient_id, admit_date, organization_id FROM stay WHERE admit_date BETWEEN ? AND ?', (lo, hi))
                if d and d[:7] in pop.months.get(pid, ())]
    if series == 'lab_volume':
        return [(pid, m, org, float(n)) for pid, m, org, n in db.execute(
            "SELECT patient_id, substr(event_date, 1, 7), organization_id, count(*) FROM record "
            "WHERE stream = 'labs' AND event_date BETWEEN ? AND ? GROUP BY 1, 2, 3", (lo, hi))
                if m in pop.months.get(pid, ())]
    raise ValueError(series)


def _rate_chart_inputs(db, series, group_by, months, pop, keep):
    """group -> (counts per month, exposure per month) for u-charts."""
    events = _series_events(db, series, months, pop, keep)
    exposure = pop.exposure(group_by)
    counts = defaultdict(lambda: defaultdict(float))
    for pid, m, org, v in events:
        g = pop.key(group_by, pid) if group_by in ('none', 'practice', 'payer') else (org or 'unknown')
        counts[g][m] += v
    groups = sorted(set(counts) | (set(exposure) if group_by in ('none', 'practice', 'payer') else set()))
    out = {}
    for g in groups:
        ex = exposure[g if group_by in ('none', 'practice', 'payer') else 'all']
        out[g] = ([counts[g].get(m, 0.0) for m in months], [ex.get(m, 0.0) for m in months])
    return out


def _claims_rows(db, months, keep, as_of):
    lo, hi = f'{months[0]}-01', _month_end(months[-1])
    rows = db.execute("""SELECT c.claim_id, c.patient_id, c.payer, c.service_date, c.first_status, c.final_status, c.billed_cents,
                                c.allowed_cents, c.first_available_date, c.service_organization_id, json_extract(r.payload, '$.service_code')
                         FROM claim c LEFT JOIN current_record r ON r.stream = 'medical_claims' AND r.business_id = c.claim_id
                         WHERE c.service_date BETWEEN ? AND ?""", (lo, hi)).fetchall()
    return [r for r in rows if keep is None or r[1] in keep]


def _claims_chart_inputs(db, series, group_by, months, pop, keep, as_of, claims):
    """Claim-based charts: denial p-chart, arrival-lag x-bar, PMPM x-bar (member-months)."""
    if series == 'pmpm':
        start, end = f'{months[0]}-01', _month_end(months[-1])
        paid = vbc._claims(db, start, end)
        completion, _ = vbc._completion(paid, end)
        cost = defaultdict(float)
        for pid, m, allowed, *_ in paid:
            if m in pop.months.get(pid, ()) and (keep is None or pid in keep):
                cost[(pid, m)] += allowed / 100 * completion.get(m, 1.0)
        g_of = (lambda pid: pop.key(group_by, pid)) if group_by in ('none', 'practice', 'payer') else (lambda pid: 'all')
        tot, sq, mm = defaultdict(lambda: defaultdict(float)), defaultdict(float), defaultdict(lambda: defaultdict(float))
        allx = []
        for pid, ms in pop.months.items():
            g = g_of(pid)
            for m in ms:
                c = cost.get((pid, m), 0.0)
                tot[g][m] += c; mm[g][m] += 1; allx.append(c)
        sd = float(np.std(allx)) if allx else 1.0
        # Months still running out (completion factor > 1.25) are not charted: the projection, not the data, would move them.
        mature = {m for m in months if completion.get(m, 1.0) <= 1.25}
        return {g: ([tot[g][m] / mm[g][m] if mm[g].get(m) and m in mature else 0.0 for m in months],
                    [mm[g].get(m, 0.0) if m in mature else 0.0 for m in months], sd)
                for g in sorted(mm)}
    gkey = {'none': lambda r: 'all', 'payer': lambda r: r[2] or 'unknown', 'source_system': lambda r: r[2] or 'unknown',
            'facility': lambda r: r[9] or 'unknown',
            'practice': lambda r: (pop.attr.get(r[1]) or {}).get('practice') or 'unattributed'}[group_by]
    if series == 'denial_rate':
        num, den = defaultdict(lambda: defaultdict(float)), defaultdict(lambda: defaultdict(float))
        for r in claims:
            g, m = gkey(r), r[3][:7]
            den[g][m] += 1; num[g][m] += r[4] == 'denied'
        return {g: ([num[g].get(m, 0.0) for m in months], [den[g].get(m, 0.0) for m in months]) for g in sorted(den)}
    if series == 'arrival_lag':
        lags = [(r, (date.fromisoformat(r[8][:10]) - date.fromisoformat(r[3][:10])).days) for r in claims if r[8] and r[3]]
        if not lags: return {}
        p95 = float(np.quantile([lag for _, lag in lags], 0.95))
        mature = {m for m in months if (date.fromisoformat(as_of) - date.fromisoformat(_month_end(m))).days >= p95}
        sd = float(np.std([lag for _, lag in lags]))
        tot, n = defaultdict(lambda: defaultdict(float)), defaultdict(lambda: defaultdict(float))
        for r, lag in lags:
            m = r[3][:7]
            if m in mature: tot[gkey(r)][m] += lag; n[gkey(r)][m] += 1
        return {g: ([tot[g][m] / n[g][m] if n[g].get(m) else 0.0 for m in months], [n[g].get(m, 0.0) for m in months], sd)
                for g in sorted(n)}
    raise ValueError(series)


# ---- feeds -------------------------------------------------------------------------------
def feed_checks(db, months, stream=None, keep=None, min_active=4, min_monthly=5, min_span=9):
    """Record volume per stream (and per stream x source system) by event month (undated records have no month and
    are left out: history loads would otherwise look like spikes). Flags interior zero-runs where the source's typical month has volume (gap), sustained runs below /
    above its median (drop / surge), and isolated months far outside it (spike / dip). Robust baseline: median of active
    months, Laney-widened c-chart on the remaining months."""
    lo, hi = f'{months[0]}-01', _month_end(months[-1])
    where = 'AND stream = ?' if stream else ''
    args = (lo, hi, stream) if stream else (lo, hi)
    counts = defaultdict(lambda: defaultdict(int))
    patients = defaultdict(set)
    sql = (f"SELECT stream, coalesce(organization_id, 'unknown'), substr(event_date, 1, 7), patient_id, count(*) "
           f"FROM record WHERE event_date BETWEEN ? AND ? {where} GROUP BY 1, 2, 3, 4")
    for st, org, m, pid, n in db.execute(sql, args):
        if keep is not None and pid not in keep: continue
        counts[(st, 'all')][m] += n; counts[(st, org)][m] += n
        patients[(st, 'all')].add(pid); patients[(st, org)].add(pid)
    reports, evaluated = [], set()
    for (st, org), series in counts.items():
        values = [series.get(m, 0) for m in months]
        active = [i for i, v in enumerate(values) if v > 0]
        if len(active) < min_active: continue
        typical = float(np.median([values[i] for i in active]))
        if typical < min_monthly: continue
        span = range(active[0], active[-1] + 1)
        # Episodic sources (a one-off program or import lasting a few months) are not continuous feeds.
        if len(span) < min_span: continue
        episodes = []
        # Interior gaps (and a stopped feed: zero from some month to the end while it used to flow).
        run = []
        for i in list(span) + list(range(active[-1] + 1, len(values))) + [None]:
            if i is not None and values[i] == 0:
                run.append(i); continue
            if len(run) >= 2:
                lam = typical * len(run)
                episodes.append({'rule': 'feed_gap' if i is not None else 'feed_stopped', 'start': run[0], 'end': run[-1], 'side': -1,
                                 'expected_records': round(lam), 'observed_records': 0, 'p': math.exp(-lam), 'bits': bits(math.exp(-lam))})
            run = []
        gap = {i for e in episodes for i in range(e['start'], e['end'] + 1)}
        chart = control_chart([float(values[i]) if i in span else None for i in range(len(values))],
                              [1 if i in span else 0 for i in range(len(values))], kind='c', exclude=gap)
        chart['center'] = typical if chart['center'] is None else chart['center']
        for ep in rule_signals([p for p in chart['points'] if p['index'] not in gap]):
            if ep['rule'] in ('beyond_3_sigma', f'8_in_a_row_one_side'):
                ratio = (values[ep['end']] or 0.0) / typical if typical else None
                ep['rule'] = {('beyond_3_sigma', 1): 'volume_spike', ('beyond_3_sigma', -1): 'volume_dip',
                              ('8_in_a_row_one_side', 1): 'volume_surge', ('8_in_a_row_one_side', -1): 'volume_drop'}[(ep['rule'], ep['side'])]
                ep['ratio_to_typical'] = round(ratio, 2) if ratio is not None else None
                episodes.append(ep)
        evaluated.add((st, org))
        if episodes:
            reports.append({'stream': st, 'source_system': org, 'typical_monthly_records': typical, 'active_months': len(active),
                            'patients': len(patients[(st, org)]),
                            'months': months, 'records': values, 'phi': chart['phi'], 'episodes': episodes})
    return reports, evaluated


def _affected_records(report, episode):
    """Records a feed episode put out of line: missing in a gap, or above/below the typical month otherwise."""
    span = range(episode['start'], episode['end'] + 1)
    if episode['rule'] in ('feed_gap', 'feed_stopped'): return round(report['typical_monthly_records'] * len(span))
    return round(sum(abs(report['records'][i] - report['typical_monthly_records']) for i in span))


VOLUME_RULES = ('volume_spike', 'volume_surge', 'volume_dip', 'volume_drop')
GAP_RULES = ('feed_gap', 'feed_stopped')


def _blocks(indices):
    out, run = [], []
    for i in sorted(indices):
        if run and i != run[-1] + 1: out.append(run); run = []
        run.append(i)
    if run: out.append(run)
    return out


def systemic_feed_events(reports, evaluated, months, share=0.25, min_streams=3):
    """Common-cause feed events, so one cause is reported once instead of once per stream:
    (a) warehouse-wide volume shifts: months where >= share of the evaluated streams (all sources) show a volume
        spike/surge/dip/drop together (a go-live backload or a platform outage looks like this);
    (b) source-system silence: months where one source system has gaps in >= min_streams of its streams; source systems
        silent over the same months are reported together."""
    streams = {st for st, org in evaluated if org == 'all'}
    per_month = defaultdict(set)
    for f in reports:
        if f['source_system'] != 'all': continue
        for e in f['episodes']:
            if e['rule'] in VOLUME_RULES:
                for i in range(e['start'], e['end'] + 1): per_month[i].add(f['stream'])
    need = max(min_streams, math.ceil(share * len(streams)))
    events = []
    for block in _blocks(i for i, s in per_month.items() if len(s) >= need):
        involved = sorted(set().union(*(per_month[i] for i in block)))
        p = math.prod(max(share, 1e-3) for _ in involved)  # chance that this many streams shift together independently
        events.append({'type': 'warehouse_volume_shift', 'start': block[0], 'end': block[-1], 'streams': involved,
                       'sources': ['all'], 'months': _fmt_month_range(months, block[0], block[-1]),
                       'streams_evaluated': len(streams), 'p': p, 'bits': bits(p)})
    silent = defaultdict(lambda: defaultdict(set))   # source -> month -> streams with a gap
    for f in reports:
        if f['source_system'] == 'all': continue
        for e in f['episodes']:
            if e['rule'] in GAP_RULES:
                for i in range(e['start'], e['end'] + 1): silent[f['source_system']][i].add(f['stream'])
    per_source = {}
    for src, by_month in silent.items():
        months_hit = [i for i, s in by_month.items() if len(s) >= min_streams]
        if months_hit:
            block = max(_blocks(months_hit), key=len)
            per_source[src] = (block[0], block[-1], sorted(set().union(*(by_month[i] for i in block))))
    # Merge source systems whose silent blocks overlap by at least 80%.
    used = set()
    for src in sorted(per_source, key=lambda s: -(per_source[s][1] - per_source[s][0])):
        if src in used: continue
        s0, e0, st0 = per_source[src]
        group = [src]
        for other in sorted(per_source):
            if other in used or other == src: continue
            s1, e1, _ = per_source[other]
            overlap = max(0, min(e0, e1) - max(s0, s1) + 1)
            if overlap >= 0.8 * max(e0 - s0 + 1, e1 - s1 + 1): group.append(other)
        used |= set(group)
        streams_g = sorted(set().union(*(per_source[g][2] for g in group)))
        start, end = min(per_source[g][0] for g in group), max(per_source[g][1] for g in group)
        lam = sum(f['typical_monthly_records'] * (min(end, e['end']) - max(start, e['start']) + 1)
                  for f in reports if f['source_system'] in group for e in f['episodes']
                  if e['rule'] in GAP_RULES and e['end'] >= start and e['start'] <= end)
        events.append({'type': 'source_silence', 'start': start, 'end': end, 'streams': streams_g, 'sources': sorted(group),
                       'months': _fmt_month_range(months, start, end), 'expected_records': round(lam),
                       'p': math.exp(-lam), 'bits': bits(math.exp(-lam))})
    return events


def event_type_gaps(db, months, series, keep=None):
    """An event type vanishing while its stream keeps flowing (coding or feed change, not care)."""
    lo, hi = f'{months[0]}-01', _month_end(months[-1])
    concept = {'ed_rate': 'emergency_visit', 'admit_rate': 'inpatient_admission'}.get(series)
    if not concept: return None
    ev = defaultdict(int); flow = defaultdict(int)
    for pid, m in db.execute("SELECT patient_id, substr(event_date, 1, 7) FROM visit WHERE type_concept = ? AND event_date BETWEEN ? AND ?",
                             (concept, lo, hi)):
        if keep is None or pid in keep: ev[m] += 1
    for pid, m in db.execute("SELECT patient_id, substr(event_date, 1, 7) FROM visit WHERE event_date BETWEEN ? AND ?", (lo, hi)):
        if keep is None or pid in keep: flow[m] += 1
    num = [float(ev.get(m, 0)) for m in months]; den = [float(flow.get(m, 0)) for m in months]
    chart = control_chart(num, den, kind='p')
    runs = [g for g in chart['gaps'] if g['end'] - g['start'] >= 2]
    return {'concept': concept, 'months': months, 'events': num, 'stream_records': den, 'share_center': chart['center'],
            'gaps': runs, 'chart': chart}


# ---- claims integrity ------------------------------------------------------------------
def first_digit(x):
    try:
        v = abs(float(x))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or v <= 0: return None
    return int(f'{v:e}'[0])


def benford_test(amounts, min_n=100, chi_min_n=300, min_orders=2.0, max_repeat_share=0.5):
    """First-digit Benford test with guards. Returns counts, chi-square, Nigrini MAD band, KL (bits/obs) and whether
    the data are eligible: enough values, spanning >= min_orders orders of magnitude (p1..p99), and not dominated by a
    few repeated (priced) amounts."""
    digits = [d for d in (first_digit(a) for a in amounts) if d]
    vals = [abs(float(a)) for a in amounts if first_digit(a)]
    n = len(digits)
    out = {'n': n, 'expected_share': [round(e, 4) for e in BENFORD]}
    if n == 0:
        return {**out, 'status': 'insufficient', 'reason': 'no positive amounts'}
    counts = [digits.count(d) for d in range(1, 10)]
    share = [c / n for c in counts]
    mad = float(np.mean([abs(s - e) for s, e in zip(share, BENFORD)]))
    stat = float(sum((c - n * e) ** 2 / (n * e) for c, e in zip(counts, BENFORD)))
    p = float(chi2.sf(stat, 8))
    orders = float(np.log10(np.quantile(vals, 0.99) / max(np.quantile(vals, 0.01), 1e-9)))
    freq = defaultdict(int)
    for v in vals: freq[v] += 1
    repeat_share = sum(sorted(freq.values(), reverse=True)[:10]) / n
    band = next(name for limit, name in MAD_BANDS if mad <= limit)
    out.update(counts=counts, observed_share=[round(s, 4) for s in share], mad=round(mad, 5), conformity=band,
               chi_square=round(stat, 2), p_value=p, kl_bits_per_value=round(kl_bits(share, BENFORD), 5),
               orders_of_magnitude=round(orders, 2), top10_amount_share=round(repeat_share, 3))
    if n < min_n:
        out.update(status='insufficient', reason=f'fewer than {min_n} amounts')
    elif orders < min_orders or repeat_share >= max_repeat_share:
        out.update(status='not_applicable',
                   reason=('amounts span fewer than two orders of magnitude' if orders < min_orders else
                           'amounts are dominated by repeated priced values (fee schedule)') +
                          '; Benford\'s law is not expected to hold, so nonconformity is not a review signal')
    elif n < chi_min_n:
        out.update(status='low_power', reason=f'fewer than {chi_min_n} amounts: MAD reported, no flag raised')
    else:
        flagged = band == 'nonconformity' and p < 1e-3
        out.update(status='review_flag' if flagged else 'conforms' if band != 'nonconformity' else 'nonconformity_not_significant')
    out['bits'] = bits(p) if out['status'] == 'review_flag' else 0.0
    return out


def _peer_type(org):
    return re.sub(r'[-_]?\d+$', '', org or 'unknown')


def peer_outliers(claims, min_claims=30, z_flag=3.5, min_scale=0.05, min_effect=math.log(1.2)):
    """Billing intensity per service organization: observed billed / expected billed for its service mix (expected =
    median billed for the service code across all organizations). log(O/E) is shrunk with a normal-normal empirical-Bayes
    prior, then compared to peers of the same organization type with a robust (median/MAD) z."""
    by_code = defaultdict(list)
    for r in claims:
        if r[6] and r[6] > 0 and r[10]: by_code[r[10]].append(r[6])
    med = {c: float(np.median(v)) for c, v in by_code.items()}
    per = defaultdict(list)
    for r in claims:
        if r[6] and r[6] > 0 and r[10] in med and r[9]: per[r[9]].append((r[6], med[r[10]], r[1]))
    rows = []
    resid = []
    for org, xs in per.items():
        logs = [math.log(b / e) for b, e, _ in xs]
        resid += [x - float(np.mean(logs)) for x in logs]
        rows.append({'organization': org, 'peer_type': _peer_type(org), 'claims': len(xs), 'patients': len({p for _, _, p in xs}),
                     'billed': round(sum(b for b, _, _ in xs) / 100, 2), 'expected_billed': round(sum(e for _, e, _ in xs) / 100, 2),
                     'log_oe': float(np.mean(logs))})
    if len(rows) < 3: return {'rows': rows, 'status': 'insufficient_peers'}
    s2 = float(np.var(resid)) if resid else 0.0
    for r in rows: r['v'] = s2 / r['claims'] if r['claims'] else math.inf
    mu = float(np.average([r['log_oe'] for r in rows], weights=[r['claims'] for r in rows]))
    tau2 = max(float(np.var([r['log_oe'] for r in rows])) - float(np.mean([r['v'] for r in rows])), 1e-6)
    for r in rows:
        w = tau2 / (tau2 + r['v'])
        r['shrunk_log_oe'] = w * r['log_oe'] + (1 - w) * mu
        r['oe_ratio'] = round(math.exp(r['log_oe']), 3); r['oe_ratio_shrunk'] = round(math.exp(r['shrunk_log_oe']), 3)
    peers = defaultdict(list)
    for r in rows: peers[r['peer_type']].append(r)
    for r in rows:
        group = peers[r['peer_type']] if len(peers[r['peer_type']]) >= 5 else rows
        xs = np.array([g['shrunk_log_oe'] for g in group])
        med_x = float(np.median(xs)); mad = float(np.median(np.abs(xs - med_x))) * 1.4826
        scale = mad if mad > 1e-9 else float(np.mean(np.abs(xs - med_x))) * 1.2533
        # Floor: when peers bill almost identically (fee schedules), a 1% difference must not become a huge z.
        scale = max(scale, min_scale)
        r['robust_z'] = round((r['shrunk_log_oe'] - med_x) / scale, 2)
        r['effect_vs_peers'] = round(math.exp(r['shrunk_log_oe'] - med_x), 3)
        r['peer_group'] = r['peer_type'] if group is not rows else 'all_organizations'
        r['peer_count'] = len(group)
        r['status'] = 'review_flag' if abs(r['robust_z']) >= z_flag and r['claims'] >= min_claims \
                                        and abs(r['shrunk_log_oe'] - med_x) >= min_effect else \
                      'insufficient_claims' if r['claims'] < min_claims else 'within_peer_range'
        r['bits'] = z_bits(r['robust_z']) if r['status'] == 'review_flag' else 0.0
        r['excess_billed'] = round(r['billed'] - r['expected_billed'], 2)
        r['log_oe'], r['shrunk_log_oe'] = round(r['log_oe'], 4), round(r['shrunk_log_oe'], 4)
        r.pop('v', None)
    rows.sort(key=lambda r: -abs(r['robust_z']))
    return {'rows': rows, 'status': 'ok', 'pooled_log_oe': round(mu, 4), 'between_org_variance': round(tau2, 5)}


def duplicate_claims(claims, group_key=lambda r: r[2] or 'unknown'):
    """Exact duplicates: different claim_ids with the same patient, service organization, service date, service code and
    billed amount. Near duplicates: same patient, organization and service code within one day but not exact. Rates per
    group are compared to the pooled rate (binomial tail)."""
    exact = defaultdict(list)
    for r in claims:
        if r[6]: exact[(r[1], r[9], r[3][:10], r[10], r[6])].append(r)
    sets = [v for v in exact.values() if len({x[0] for x in v}) > 1]
    in_exact = {x[0] for v in sets for x in v}
    near = defaultdict(list)
    for r in claims:
        if r[0] not in in_exact: near[(r[1], r[9], r[10])].append(r)
    near_sets = []
    for v in near.values():
        v.sort(key=lambda x: x[3])
        for a, b in zip(v, v[1:]):
            if a[0] != b[0] and (date.fromisoformat(b[3][:10]) - date.fromisoformat(a[3][:10])).days <= 1:
                near_sets.append([a, b])
    total = defaultdict(int); dup = defaultdict(int); dollars = defaultdict(float)
    for r in claims: total[group_key(r)] += 1
    for v in sets:
        for x in sorted(v, key=lambda x: x[0])[1:]:
            dup[group_key(x)] += 1; dollars[group_key(x)] += (x[6] or 0) / 100
    n_all, d_all = sum(total.values()), sum(dup.values())
    pooled = d_all / n_all if n_all else 0.0
    from scipy.stats import binom
    groups = []
    for g in sorted(total):
        p = float(binom.sf(dup[g] - 1, total[g], pooled)) if dup[g] and pooled else 1.0
        groups.append({'group': g, 'claims': total[g], 'extra_copies': dup[g], 'rate': round(dup[g] / total[g], 5) if total[g] else None,
                       'billed_in_extra_copies': round(dollars[g], 2), 'p_above_pooled': p,
                       'bits': bits(p) if dup[g] / max(total[g], 1) > pooled else 0.0})
    return {'exact_sets': len(sets), 'extra_copies': d_all, 'pooled_rate': round(pooled, 5),
            'billed_in_extra_copies': round(sum(dollars.values()), 2),
            'near_pairs': len(near_sets), 'groups': groups,
            'examples': [{'claim_ids': sorted({x[0] for x in v}), 'patient_id': v[0][1], 'service_date': v[0][3], 'service_code': v[0][10],
                          'organization': v[0][9], 'billed': (v[0][6] or 0) / 100} for v in sets[:20]],
            'near_examples': [{'claim_ids': [a[0], b[0]], 'patient_id': a[1], 'service_dates': [a[3], b[3]], 'service_code': a[10],
                               'organization': a[9]} for a, b in near_sets[:20]]}


# ---- orchestration ----------------------------------------------------------------------
# ---- risk-adjusted monitoring: RA-CUSUM and VLAD ---------------------------------------------------------------------------
try:
    from . import risk_outcomes as _ro
except Exception:  # risk_outcomes (or its dependencies) unavailable: the in-module logistic model is used
    _ro = None

RA_CHARTS = ('ra_cusum', 'vlad')
CHART_OPTIONS = ('control',) + RA_CHARTS
RA_OUTCOMES = ('readmission', 'post_discharge_ed', 'ed_revisit', 'mortality')
RA_UNITS = ('hospital', 'practice', 'pcp', 'none')
RA_ADJUST = ('age_sex_conditions', 'age_sex', 'none')
RA_FIELDS = {'chart', 'outcome', 'unit', 'or_shift', 'or_improve', 'arl0', 'window_days', 'adjust', 'min_cases', 'include_observation'}
RA_P_FLOOR, RA_P_CEIL = 1e-4, 1 - 1e-4
VLAD_Z = (1.96, 3.09)       # 95% and 99.8% two-sided funnel limits
RA_PATH_POINTS = 120
RA_ASSUMPTIONS = [
    'Expected risk per case comes from one patient-level logistic model fitted on all cases in the window (risk_outcomes.fit_glm '
    'over age band, sex, chronic conditions, prior-year stays, observation status; otherwise an in-module logistic model on age, '
    'sex, comorbidity count and prior utilization). It has no unit terms, so expected = the network\'s average care; a shift '
    'that affects every unit equally is absorbed into expected risk.',
    'Cases are ordered by index date (discharge date, or admission / visit date for mortality and ED revisits), ties by patient id.',
    'CUSUM weights follow Steiner, Cook, Farewell & Treasure (2000) for a step change of the odds ratio to or_shift (and to '
    'or_improve for the improvement chart); each chart restarts at zero after a signal.',
    'The control limit h is one value for all units, found by simulation of in-control outcomes (Bernoulli at the expected risk, '
    'case mix resampled from the pooled cases of the charted units) so the average run length to a false signal is arl0 cases. '
    'Units with unusual case mix have a slightly different true ARL0; expected risks are treated as known (model error ignored).',
    'VLAD is the running sum of expected minus observed events; limits are +-z sqrt(sum p(1-p)) (95% and 99.8%), exact for known '
    'risks at a fixed look but not corrected for looking at every case, so crossings are prompts to review, not proof of worse care.',
    'Outcomes are what the warehouse records; follow-up must be complete before the snapshot. No planned-readmission flag exists.',
]


def _ra_weights(p, y, R):
    """Log-likelihood-ratio score of odds ratio R against 1 for each case with expected risk p and outcome y."""
    p = np.asarray(p, float)
    return np.asarray(y, float) * math.log(R) - np.log1p(p * (R - 1.0))


def ra_cusum(p, y, R, h):
    """One CUSUM over cases in order. Returns {'path': S after each case (before reset), 'signals': [case indices], 'max', 'final'}.
    R > 1 detects deterioration (odds ratio up); R < 1 detects improvement. S restarts at 0 after reaching h."""
    w = _ra_weights(p, y, R)
    s, path, signals = 0.0, [], []
    for i, wi in enumerate(w):
        s = max(0.0, s + float(wi))
        path.append(s)
        if s >= h:
            signals.append(i); s = 0.0
    return {'path': path, 'signals': signals, 'max': max(path) if path else 0.0, 'final': s}


def simulate_arl(probs, R, h, n_chains=300, length=4000, seed=11):
    """Average run length (cases) to a signal for an in-control process: chains of cases drawn from the case-mix `probs`, outcomes
    Bernoulli at the expected risk, CUSUM restarted after each signal. ARL = cases observed / signals."""
    P = np.asarray(probs, float); D = np.log1p(P * (R - 1.0)); lr = math.log(R)
    rng = np.random.default_rng(seed)
    s = np.zeros(n_chains); signals = 0
    for _ in range(length):
        idx = rng.integers(0, len(P), n_chains)
        w = (rng.random(n_chains) < P[idx]) * lr - D[idx]
        s = np.maximum(0.0, s + w)
        hit = s >= h
        signals += int(hit.sum()); s[hit] = 0.0
    return n_chains * length / signals if signals else math.inf


def simulate_control_limit(probs, R, arl0, seed=7, n_chains=None, length=None, step=0.05, h_max=20.0):
    """Control limit h giving in-control ARL ~ arl0 cases, by simulation over the case-mix distribution `probs`.
    One simulation of reflected CUSUMs (never reset) gives the first-passage time to every h on a grid at once: the run
    length to a signal is the first time the reflected path reaches h. ARL(h) is the total cases observed over signals
    seen (the censored-exponential estimate, exact for a constant signal rate), then h is interpolated on log ARL."""
    P = np.clip(np.asarray(probs, float), RA_P_FLOOR, RA_P_CEIL)
    if len(P) == 0 or not (arl0 >= 2): raise ValueError('need case risks and arl0 >= 2')
    length = int(length or min(max(4 * arl0, 2000), 400000))
    n_chains = int(n_chains or max(200, min(800, int(2.5e7 // length))))
    D = np.log1p(P * (R - 1.0)); lr = math.log(R)
    grid = np.arange(step, h_max + step / 2, step)
    first = np.full((n_chains, len(grid)), np.inf)
    rng = np.random.default_rng(seed)
    s = np.zeros(n_chains); top = np.zeros(n_chains)
    block, t0 = 500, 0
    while t0 < length:
        b = min(block, length - t0)
        idx = rng.integers(0, len(P), (n_chains, b))
        w = (rng.random((n_chains, b)) < P[idx]) * lr - D[idx]
        sb = np.empty((n_chains, b))
        for j in range(b):
            s = np.maximum(0.0, s + w[:, j]); sb[:, j] = s
        run = np.maximum.accumulate(sb, axis=1)
        new_top = np.maximum(top, run[:, -1])
        lo = np.searchsorted(grid, top, side='right'); hi = np.searchsorted(grid, new_top, side='right')
        for c in np.nonzero(hi > lo)[0]:
            first[c, lo[c]:hi[c]] = t0 + 1 + np.searchsorted(run[c], grid[lo[c]:hi[c]], side='left')
        top = new_top; t0 += b
    seen = np.isfinite(first)
    cases = np.where(seen, first, length).sum(axis=0)
    hits = seen.sum(axis=0)
    with np.errstate(divide='ignore', invalid='ignore'):
        arl = np.where(hits > 0, cases / np.maximum(hits, 1), np.inf)
    arl = np.maximum.accumulate(np.where(np.isfinite(arl), arl, 1e18))
    k = int(np.searchsorted(arl, arl0, side='left'))
    if k >= len(grid):
        h, est = float(grid[-1]), float(arl[-1])
    elif k == 0:
        h, est = float(grid[0]), float(arl[0])
    else:
        a0, a1 = math.log(arl[k - 1]), math.log(min(arl[k], 1e17))
        frac = (math.log(arl0) - a0) / (a1 - a0) if a1 > a0 else 1.0
        h, est = float(grid[k - 1] + frac * step), float(arl0)
    return {'h': round(h, 3), 'arl0_target': arl0, 'arl0_simulated': round(est, 1), 'chains': n_chains, 'chain_length': length,
            'signals_at_h': int(hits[min(k, len(grid) - 1)]), 'odds_ratio': R}


def vlad(p, y):
    """Variable life-adjusted display for cases in order: cumulative expected minus observed events, with 95% / 99.8% funnel limits
    +-z sqrt(sum p(1-p)) around zero. Below the lower limit = more events than expected."""
    p = np.asarray(p, float); y = np.asarray(y, float)
    cum = np.cumsum(p - y)
    sd_ = np.sqrt(np.maximum(np.cumsum(p * (1 - p)), 1e-12))
    z = cum / sd_
    return {'cum': cum, 'sd': sd_, 'z': z, 'limits': {str(k): (k * sd_) for k in VLAD_Z}}


def _episodes(mask):
    out, start = [], None
    for i, m in enumerate(list(mask) + [False]):
        if m and start is None: start = i
        if not m and start is not None: out.append((start, i - 1)); start = None
    return out


def _thin(values, k=RA_PATH_POINTS):
    n = len(values)
    if n <= k: return list(range(n))
    return sorted({int(round(i * (n - 1) / (k - 1))) for i in range(k)})


def _design_fallback(covs):
    """Numeric features for the in-module logistic model: age (centred, scaled, squared), male, comorbidity count, prior utilization."""
    def age(c):
        nums = [float(x) for x in re.findall(r'\d+', str(c.get('age_band') or ''))]
        if not nums: return math.nan
        return nums[0] + 5 if len(nums) == 1 else sum(nums) / 2
    a = np.array([age(c) for c in covs], float)
    a = np.where(np.isfinite(a), a, np.nanmean(a) if np.isfinite(a).any() else 50.0)
    cols = [(a - a.mean()) / (a.std() or 1.0), ((a - a.mean()) / (a.std() or 1.0)) ** 2,
            np.array([c.get('sex') == 'M' for c in covs], float),
            np.array([len(c.get('conditions') or ()) for c in covs], float),
            np.array([min(int(c.get('prior') or 0), 2) for c in covs], float)]
    X = [np.ones(len(covs))] + [c for c in cols if c.std() > 0]
    return np.column_stack(X)


def fit_logistic(X, y, ridge=1.0, max_iter=60):
    """In-module ridge-penalised IRLS logistic regression (used when risk_outcomes is unavailable). Returns fitted risks."""
    X = np.asarray(X, float); y = np.asarray(y, float)
    beta = np.zeros(X.shape[1]); m0 = min(max(y.mean(), 1e-6), 1 - 1e-6); beta[0] = math.log(m0 / (1 - m0))
    pen = np.full(X.shape[1], ridge); pen[0] = 0.0
    for _ in range(max_iter):
        mu = 1 / (1 + np.exp(-np.clip(X @ beta, -30, 30))); w = np.maximum(mu * (1 - mu), 1e-10)
        step = np.linalg.solve((X * w[:, None]).T @ X + np.diag(pen) + 1e-10 * np.eye(len(beta)), X.T @ (y - mu) - pen * beta)
        beta += step
        if np.max(np.abs(step)) < 1e-9: break
    return 1 / (1 + np.exp(-np.clip(X @ beta, -30, 30)))


def expected_risks(covs, y, adjust='age_sex_conditions'):
    """Expected event risk per case. Prefers risk_outcomes' fitted GLM (design + fit_glm); falls back to the in-module logistic
    model. Returns (risks clipped to [1e-4, 1 - 1e-4], source label)."""
    y = np.asarray(y, float)
    if _ro is not None:
        try:
            X, names = _ro.design(covs, adjust)
            _, mu, ok = _ro.fit_glm(X, y, 'binomial')
            if ok and np.all(np.isfinite(mu)):
                return np.clip(mu, RA_P_FLOOR, RA_P_CEIL), 'risk_outcomes.fit_glm'
        except Exception:
            pass
    return np.clip(fit_logistic(_design_fallback(covs), y), RA_P_FLOOR, RA_P_CEIL), 'monitor in-module logistic'


def ra_chart(unit, cases, p, y, params, limits):
    """Charts for one unit. cases = [(date, pid)] in order; limits = {'up': h, 'down': h or None}."""
    n = len(y)
    dates = [c[0] for c in cases]
    exp, obs = float(np.sum(p)), float(np.sum(y))
    out = {'unit': unit, 'cases': n, 'first_date': dates[0], 'last_date': dates[-1], 'observed': obs, 'expected': round(exp, 3),
           'o_e': round(obs / exp, 3) if exp > 0 else None}
    signals = []
    if params['chart'] == 'ra_cusum':
        up = ra_cusum(p, y, params['or_shift'], limits['up'])
        for i in up['signals']:
            signals.append({'unit': unit, 'direction': 'deterioration', 'case_number': i + 1, 'date': dates[i], 'cusum': round(up['path'][i], 3)})
        keep = _thin(up['path'])
        out['cusum_up'] = {'h': limits['up'], 'odds_ratio': params['or_shift'], 'max': round(up['max'], 3), 'final': round(up['final'], 3),
                           'signals': len(up['signals']), 'path': [[i + 1, dates[i], round(up['path'][i], 3)] for i in keep]}
        if limits.get('down') is not None:
            dn = ra_cusum(p, y, params['or_improve'], limits['down'])
            for i in dn['signals']:
                signals.append({'unit': unit, 'direction': 'improvement', 'case_number': i + 1, 'date': dates[i], 'cusum': round(dn['path'][i], 3)})
            keep = _thin(dn['path'])
            out['cusum_down'] = {'h': limits['down'], 'odds_ratio': params['or_improve'], 'max': round(dn['max'], 3), 'final': round(dn['final'], 3),
                                 'signals': len(dn['signals']), 'path': [[i + 1, dates[i], round(dn['path'][i], 3)] for i in keep]}
    else:
        v = vlad(p, y)
        lo, hi = VLAD_Z[-1], VLAD_Z[0]
        below, above = v['z'] < -lo, v['z'] > lo
        for mask, direction in ((below, 'deterioration'), (above, 'improvement')):
            for s, e in _episodes(mask):
                signals.append({'unit': unit, 'direction': direction, 'case_number': s + 1, 'date': dates[s], 'last_case_number': e + 1,
                                'last_date': dates[e], 'vlad': round(float(v['cum'][s]), 3), 'z': round(float(v['z'][s]), 3)})
        keep = _thin(v['cum'])
        out['vlad'] = {'final': round(float(v['cum'][-1]), 3), 'final_z': round(float(v['z'][-1]), 3), 'min': round(float(v['cum'].min()), 3),
                       'max': round(float(v['cum'].max()), 3), 'excess_events': round(-float(v['cum'][-1]), 3),
                       'outside_95': int((np.abs(v['z']) > VLAD_Z[0]).sum()),
                       'path': [[i + 1, dates[i], round(float(v['cum'][i]), 3), round(float(v['limits'][str(VLAD_Z[0])][i]), 3),
                                 round(float(v['limits'][str(VLAD_Z[1])][i]), 3)] for i in keep]}
    out['signals'] = signals
    return out


def _ra_cases(db, path, patient_ids, p, as_of):
    ro_p = _ro.validate({'outcome': p['outcome'], 'unit': 'hospital' if p['unit'] == 'none' else p['unit'], 'window_days': p['window_days'],
                         'period': p['period'], 'include_observation': p['include_observation'], 'adjust': p['adjust']})
    cap = _ro.capability(db)
    if p['outcome'] == 'mortality' and not cap['death']:
        return None, None, 'unsupported', 'Mortality needs death records (date of death), which this warehouse does not carry.'
    if p['outcome'] == 'ed_revisit' and not cap['visits'] or p['outcome'] != 'ed_revisit' and not cap['stays']:
        return None, None, 'unsupported', 'The index events are not in this warehouse.'
    rows, period = _ro.index_cases(db, cap, set(patient_ids) if patient_ids is not None else None, ro_p, as_of)
    return rows, period, None, None


def execute_ra(path, patient_ids, p, as_of=None):
    if _ro is None: raise RuntimeError('risk-adjusted charts need the risk_outcomes module')
    from . import safety_data as sd
    db = sd.connect(path)
    try:
        as_of = sd.as_of_of(db, as_of)
        rows, period, status, reason = _ra_cases(db, path, patient_ids, p, as_of)
    finally:
        db.close()
    chart = p['chart']
    report = {'schema': VERSION, 'check': chart, 'chart': chart, 'parameters': p, 'as_of': as_of, 'period': period, 'rows': [], 'chart_status': [],
              'feeds': [], 'event_type_gaps': {}, 'integrity': {}, 'units': [], 'signals': [], 'assumptions': RA_ASSUMPTIONS, 'notes': [],
              'tests_examined': 0, 'look_elsewhere_bits': 0.0, 'method': RA_METHOD, 'limitations': RA_ASSUMPTIONS}
    if status:
        report.update(status=status, reason=reason); return report
    yv = np.array([r[1] for r in rows], float)
    if len(rows) < max(10, p['min_cases']) or yv.sum() == 0 or yv.sum() == len(yv):
        report.update(status='empty', reason=f'{len(rows)} index cases with {int(yv.sum())} {p["outcome"]} events: too few cases, or no outcome variation, to chart.')
        return report
    pr, source = expected_risks([r[2] for r in rows], yv, p['adjust'])
    by_unit = defaultdict(list)
    for i, r in enumerate(rows):
        by_unit[r[0] if p['unit'] != 'none' else 'all'].append(i)
    kept, small = {}, 0
    for u, ix in by_unit.items():
        if len(ix) >= p['min_cases']:
            kept[u] = sorted(ix, key=lambda i: (rows[i][3]['index_date'], str(rows[i][3]['patient_id'])))
        else: small += 1
    if not kept:
        report.update(status='empty', reason=f'No unit has {p["min_cases"]} or more index cases ({len(by_unit)} units).'); return report
    pooled = np.concatenate([pr[ix] for ix in kept.values()])
    limits, sims = {'up': None, 'down': None}, {}
    if chart == 'ra_cusum':
        sims['up'] = simulate_control_limit(pooled, p['or_shift'], p['arl0']); limits['up'] = sims['up']['h']
        if p['or_improve'] is not None:
            sims['down'] = simulate_control_limit(pooled, p['or_improve'], p['arl0'], seed=8); limits['down'] = sims['down']['h']
    units = []
    for u, ix in kept.items():
        units.append(ra_chart(u, [(rows[i][3]['index_date'], rows[i][3]['patient_id']) for i in ix], pr[ix], yv[ix], p, limits))
    key = (lambda c: (-len(c['signals']), -(c.get('cusum_up') or {}).get('max', 0))) if chart == 'ra_cusum' else \
          (lambda c: (-len(c['signals']), c['vlad']['final']))
    units.sort(key=key)
    signals = sorted((s for c in units for s in c['signals']), key=lambda s: (s['date'], s['unit']))
    look = round(math.log2(max(len(units), 1)), 2)
    flagged = []
    for c in units:
        det = [s for s in c['signals'] if s['direction'] == 'deterioration']
        imp = [s for s in c['signals'] if s['direction'] == 'improvement']
        for sig, direction in ((det, 'deterioration'), (imp, 'improvement')):
            if not sig: continue
            first = sig[0]
            if chart == 'ra_cusum':
                bits_ = round(math.log2(p['arl0']), 2)
                head = (f"{c['unit']}: risk-adjusted CUSUM signals {direction} (odds ratio {p['or_shift'] if direction == 'deterioration' else p['or_improve']}) "
                        f"at case {first['case_number']} ({first['date']}); {len(sig)} signal(s) over {c['cases']} cases, O/E {c['o_e']}")
            else:
                z = abs(first['z'])
                bits_ = z_bits(z)
                head = (f"{c['unit']}: VLAD outside the 99.8% limits ({direction}) from case {first['case_number']} ({first['date']}); "
                        f"final expected minus observed {c['vlad']['final']:+.1f} events over {c['cases']} cases")
            f = {'kind': 'risk_adjusted_signal', 'category': f'{chart}_{direction}', 'series': p['outcome'], 'group_by': p['unit'], 'group': c['unit'],
                 'months': sorted({s['date'][:7] for s in sig})[:12], 'rules': [chart], 'headline': head, 'surprise_bits': bits_,
                 'patients': c['cases'], 'dollars': None, 'first_signal_date': first['date'], 'signals': len(sig),
                 'look_elsewhere_bits': look, 'excess_bits': round(bits_ - look, 2)}
            f['salient'] = f['excess_bits'] >= 0
            f['score'] = score(f['excess_bits'], c['cases'])
            f['follow_up'] = f"Is risk-adjusted {p['outcome'].replace('_', ' ')} at {c['unit']} getting worse?"
            flagged.append(f)
    flagged.sort(key=lambda f: (-f['score'], -f['surprise_bits']))
    status_rows = [{'series': p['outcome'], 'group_by': p['unit'], 'group': c['unit'], 'status': 'rate_signal' if c['signals'] else 'in_control',
                    'cases': c['cases'], 'signals': len(c['signals']), 'surprise_bits': 0.0} for c in units]
    summary = {'outcome': p['outcome'], 'unit': p['unit'], 'cases': int(sum(c['cases'] for c in units)), 'units_charted': len(units),
               'units_below_min_cases': small, 'network_rate': round(float(np.mean(np.concatenate([yv[ix] for ix in kept.values()]))), 4),
               'expected_risk_source': source, 'expected_risk_range': [round(float(pooled.min()), 4), round(float(pooled.max()), 4)],
               'units_with_signals': sum(bool(c['signals']) for c in units)}
    if small: report['notes'].append(f"{small} unit(s) have fewer than {p['min_cases']} index cases and are not charted.")
    report.update(status='answered', rows=flagged, chart_status=status_rows, units=units, signals=signals[:500], signals_total=len(signals),
                  summary=summary, tests_examined=len(units), look_elsewhere_bits=look,
                  control_limits={'arl0': p['arl0'], 'upper': sims['up'], 'lower': sims['down'], 'h': limits['up'], 'h_improvement': limits['down']}
                  if chart == 'ra_cusum' else None,
                  vlad_limits={'z': list(VLAD_Z), 'levels': ['95%', '99.8%']} if chart == 'vlad' else None)
    return report


RA_METHOD = ('Risk-adjusted monitoring over index cases in date order, using risk_outcomes\' case definitions (readmission, post-discharge ED, ED '
             'revisit, mortality) and expected risks from one logistic model without unit terms. ra_cusum: Steiner et al. (2000) CUSUM, '
             'weight log(R^y / (1 - p + R p)) for odds ratio R (or_shift for deterioration, or_improve for improvement), restart at zero after a '
             'signal, control limit h by simulation for an in-control average run length of arl0 cases over the data\'s case-mix. vlad: '
             'cumulative expected minus observed events with funnel limits +-z sqrt(sum p(1 - p)) at 95% and 99.8%.')


def _plan(p):
    check = p['check']
    charts = check in ('all', 'control_chart', 'claims', 'feed')
    series = p['series'] or (list(CLAIM_SERIES) if check == 'claims' else ['lab_volume'] if check == 'feed' else list(SERIES))
    if check == 'feed' and not p['series'] and p['stream'] and p['stream'] != 'labs':
        series = [s for s, st in FEED_STREAM.items() if st == p['stream']][:1]
    claims_checks = {'all': ('benford', 'peer_outliers', 'duplicates'), 'claims': ('benford', 'peer_outliers', 'duplicates'),
                     'claims_integrity': ('benford', 'peer_outliers', 'duplicates')}.get(check, (check,) if check in ('benford', 'peer_outliers', 'duplicates') else ())
    feeds = check in ('all', 'feed', 'claims')
    return charts, series, claims_checks, feeds


def _groupings(p, series):
    if p['group_by'] != 'auto': return [p['group_by']]
    return ['none', NATURAL_GROUP[series]]


def _fmt_month_range(months, s, e):
    return months[s] if s == e else f'{months[s]}..{months[e]}'


def _chart_findings(s, gb, g, spec, months, report_idx, method, inherited, source_flags, stream_flags):
    """Chart one (series, group), apply the rules and split signals into data-feed and rate findings.

    inherited = months the overall chart (or an event-type gap) already attributes to data; source_flags = feed anomalies of
    this group's own source system; stream_flags = feed anomalies of the feeding stream overall.
    Returns (chart status row, findings, data month indices, points tested) or None when the chart is too sparse."""
    values, ns = spec[0], spec[1]
    if sum(1 for n in ns if n) < 6: return None
    kind = 'p' if s == 'denial_rate' else 'x' if s in ('pmpm', 'arrival_lag') else 'u'
    if kind == 'u' and sum(values) < 10: return None
    chart = control_chart(values, ns, kind=kind, sd=spec[2] if len(spec) > 2 else None, exclude=inherited | source_flags)
    if chart['center'] is None: return None
    eps = rule_signals(chart['points']) if method in ('both', 'shewhart') else []
    if method in ('both', 'ewma'): eps += ewma_signals(chart['points'])
    eps += [x for x in chart['gaps'] if x['rule'] == 'zero_run']
    n_tests = sum(1 for x in chart['points'] if x['z'] is not None)
    eps = [e for e in eps if any(i in report_idx for i in range(e['start'], e['end'] + 1))]
    # Months that are data problems, not care: gaps, implausible single-month magnitudes, feed anomalies.
    gap_months = {i for x in chart['gaps'] for i in range(x['start'], x['end'] + 1)}
    leading = {i for x in chart['gaps'] if x['rule'] == 'not_yet_started' for i in range(x['start'], x['end'] + 1)}
    spikes = {}
    for x in chart['points']:
        if kind == 'u' and x['value'] is not None and chart['center'] and x['z'] is not None and abs(x['z']) > 3 \
                and x['count'] >= 5 and (x['value'] / chart['center'] >= 5 or x['value'] / chart['center'] <= 0.2):
            spikes[x['index']] = x['value'] / chart['center']
    data, rate = [], []
    for e in eps:
        span = set(range(e['start'], e['end'] + 1))
        e['months'] = _fmt_month_range(months, e['start'], e['end'])
        if e['rule'] == 'zero_run' or span & (gap_months - leading) or span & source_flags:
            e['cause'] = 'gap'
            e['reason'] = 'the series is empty or missing while its source stream keeps flowing (feed or coding gap)'
        elif span & leading:
            e['cause'] = 'gap'
            e['reason'] = 'the source had not started sending in the first months of the window (onboarding, not care)'
        elif span & set(spikes):
            k = min(span & set(spikes))
            e['cause'] = 'spike'
            e['reason'] = (f'{months[k]} at {spikes[k]:.1f}x the baseline: a single-month magnitude implausible for a change '
                           'in care (check for a batch load, duplicate feed or reclassification)')
        elif span & inherited:
            e['cause'] = 'inherited'
            e['reason'] = 'the overall series has a data gap or implausible spike in these months'
        elif span & stream_flags:
            e['cause'] = 'feed'
            e['reason'] = f'coincides with a volume anomaly in the {FEED_STREAM[s]} feed'
        else:
            rate.append(e); continue
        data.append(e)
    last = next((x for x in reversed(chart['points']) if x['value'] is not None), None)
    status = {'series': s, 'group_by': gb, 'group': g, 'status': 'rate_signal' if rate else 'data_feed_anomaly' if data else 'in_control',
              'center': _r(chart['center'] * SCALE[s]), 'latest_month': months[last['index']] if last else None,
              'latest_value': _r(last['value'] * SCALE[s]) if last else None, 'laney_phi': chart['phi'],
              'rate_rules': sorted({e['rule'] for e in rate}), 'data_rules': sorted({e['rule'] for e in data}),
              'rate_signal_months': [e['months'] for e in sorted(rate, key=lambda e: e['start'])][:8],
              'data_signal_months': [e['months'] for e in sorted(data, key=lambda e: e['start'])][:8],
              'surprise_bits': max([e['bits'] for e in rate + data], default=0.0),
              'chart': [{'month': months[x['index']], 'value': _r(x['value'] * SCALE[s]) if x['value'] is not None else None,
                         'n': _r(x['n']), 'lcl': _r(x['lcl'] * SCALE[s]) if x['value'] is not None else None,
                         'ucl': _r(x['ucl'] * SCALE[s]) if x['value'] is not None else None, 'z': _r(x['z'])} for x in chart['points']]}
    found = []
    where = 'overall' if g == 'all' else f'{gb} {g}'
    causes = defaultdict(list)
    for e in data: causes[e['cause']].append(e)
    groups = [('data_feed', causes[c]) for c in ('gap', 'spike', 'feed', 'inherited') if causes[c]] + [('rate', rate)]
    for label, eps_k in groups:
        if not eps_k: continue
        top = max(eps_k, key=lambda e: (e['bits'], e['rule'] == 'zero_run', e['end'] - e['start']))
        direction = 'above' if top['side'] > 0 else 'below'
        span = {i for e in eps_k for i in range(e['start'], e['end'] + 1)}
        # A group chart repeating the overall series' data problem adds no information: mark it as an echo.
        echo = gb != 'none' and label == 'data_feed' and bool(inherited) and len(span & inherited) >= 0.8 * len(span)
        found.append({
            'kind': label, 'echo': echo,
            'category': {'gap': 'feed_gap', 'spike': 'volume_spike', 'feed': 'feed_coincident', 'inherited': 'feed_gap'}.get(top.get('cause'), 'rate_shift'),
            'series': s, 'group_by': gb, 'group': g, 'months': [e['months'] for e in sorted(eps_k, key=lambda e: e['start'])][:8],
            'rules': sorted({e['rule'] for e in eps_k}),
            'headline': (f"{LABELS[s]} ({where}): " +
                         (f"{top['rule'].replace('_', ' ')} {direction} the center in {top['months']}" if top['rule'] != 'zero_run' else
                          f"no events in {top['months']} although about {top['expected_events']:g} were expected") +
                         f" (center {_r(chart['center'] * SCALE[s])}" + (f", Laney phi {chart['phi']}" if chart['phi'] > 1 else '') + ')' +
                         (f" - likely data, not care: {top.get('reason')}" if label == 'data_feed' else '')),
            'surprise_bits': top['bits'], 'patients': None, 'dollars': None,
            'evidence': {'episodes': eps_k[:12], 'center': chart['center'], 'phi': chart['phi'], 'chart_type': chart['kind']}})
    return status, found, gap_months | set(spikes), n_tests


def execute(path, patient_ids, params, as_of=None):
    p = validate(params)
    if p.get('chart') in RA_CHARTS: return execute_ra(path, patient_ids, p, as_of)
    db = _connect(path)
    try:
        as_of = as_of or dict(db.execute('SELECT key, value FROM meta')).get('as_of')
        months, report = windows(p, as_of)
        if len(months) < 6: raise ValueError('monitor needs at least six complete months of data in the window')
        keep = set(patient_ids) if patient_ids is not None else None
        report_idx = {i for i, m in enumerate(months) if m in report}
        charts_on, series, claims_checks, feeds_on = _plan(p)
        pop = Population(db, months, keep)
        claims = _claims_rows(db, months, keep, as_of) if claims_checks or any(s in CLAIM_SERIES for s in series) else []
        annual = None  # average annual allowed spend per member, to express dollars as member-equivalents
        findings, chart_status, tests = [], [], 0
        feed_stream = p['stream'] or ('medical_claims' if p['check'] == 'claims' else None)
        feed_reports, evaluated = feed_checks(db, months, feed_stream, keep) if feeds_on else ([], set())
        tests += len(evaluated) * len(months)
        systemic = systemic_feed_events(feed_reports, evaluated, months) if feeds_on and not feed_stream else []
        feed_months = defaultdict(set)   # (stream, source) -> month indices flagged
        for f in feed_reports:
            for ep in f['episodes']:
                feed_months[(f['stream'], f['source_system'])] |= set(range(ep['start'], ep['end'] + 1))
        type_gaps = {}
        if charts_on:
            for s in series:
                if s in ('ed_rate', 'admit_rate'):
                    type_gaps[s] = event_type_gaps(db, months, s, keep)
            series_data = {}   # series -> month indices that the overall chart attributes to data, not care
            for s in series:
                gap_idx = {i for g in (type_gaps.get(s) or {}).get('gaps', []) for i in range(g['start'], g['end'] + 1)}
                wanted = _groupings(p, s)
                plan = ['none'] + [g for g in wanted if g != 'none']
                for gb in plan:
                    silent = gb not in wanted
                    if s == 'pmpm' and gb in ('facility', 'source_system'): continue
                    if s in CLAIM_SERIES:
                        inputs = _claims_chart_inputs(db, s, gb, months, pop, keep, as_of, claims)
                    else:
                        inputs = _rate_chart_inputs(db, s, gb, months, pop, keep)
                    if s == 'pmpm' and 'all' in inputs and gb == 'none':
                        mmt = sum(inputs['all'][1])
                        annual = 12 * sum(v * n for v, n in zip(inputs['all'][0], inputs['all'][1])) / mmt if mmt else None
                    group_patients = pop.patients(gb)
                    for g, spec in inputs.items():
                        res = _chart_findings(s, gb, g, spec, months, report_idx, p['method'],
                                              gap_idx | series_data.get(s, set()),
                                              feed_months.get((FEED_STREAM[s], g), set()) if gb in ('facility', 'source_system') else set(),
                                              feed_months.get((FEED_STREAM[s], 'all'), set()))
                        if res is None: continue
                        status, found, data_idx, n_tests = res
                        if gb == 'none': series_data[s] = data_idx
                        if silent: continue
                        tests += n_tests
                        patients = group_patients.get(g, 0) if gb in ('none', 'practice', 'payer') else \
                            len({r[1] for r in claims if (r[2] if gb in ('payer', 'source_system') else r[9]) == g}) if s in CLAIM_SERIES else None
                        for f in found: f['patients'] = patients
                        chart_status.append(status); findings += found
            if annual is None and claims:
                mm = sum(len(v) for v in pop.months.values())
                annual = 12 * sum((r[7] or 0) / 100 for r in claims if r[5] == 'paid') / mm if mm else None
        # Feed findings: one per (stream, anomaly type), naming every source system it affects.
        by_rule = defaultdict(list)
        for f in feed_reports:
            for ep in f['episodes']:
                if not any(i in report_idx for i in range(ep['start'], ep['end'] + 1)): continue
                ep['months'] = _fmt_month_range(months, ep['start'], ep['end'])
                by_rule[(f['stream'], ep['rule'])].append((f, ep))
        for ev in systemic:
            if not any(i in report_idx for i in range(ev['start'], ev['end'] + 1)): continue
            pts = max([f['patients'] for f in feed_reports if f['source_system'] == 'all' and f['stream'] in ev['streams']], default=None)
            if ev['type'] == 'warehouse_volume_shift':
                head = (f"Warehouse-wide volume shift in {ev['months']}: {len(ev['streams'])} of {ev['streams_evaluated']} continuous feeds "
                        f"spike or dip together ({', '.join(ev['streams'][:6])}{'...' if len(ev['streams']) > 6 else ''}) - a common cause "
                        '(go-live backload, batch reload or outage), not care; month-level metrics in this window are unreliable')
            else:
                head = (f"Source system{'s' if len(ev['sources']) > 1 else ''} {', '.join(ev['sources'][:6])} silent in {ev['months']} across "
                        f"{len(ev['streams'])} streams ({', '.join(ev['streams'][:6])}{'...' if len(ev['streams']) > 6 else ''}); about "
                        f"{ev['expected_records']:,} records expected - a feed outage, so utilization from these sources is missing, not low")
                pts = max([sum(f['patients'] for f in feed_reports if f['source_system'] in ev['sources'] and f['stream'] == st)
                           for st in ev['streams']], default=None)
                pts = min(pts, ev['expected_records']) if pts is not None else None
            findings.append({'kind': 'data_feed', 'category': ev['type'], 'series': None, 'stream': None, 'group_by': 'source_system',
                             'group': ', '.join(ev['sources']), 'sources': ev['sources'], 'months': [ev['months']], 'rules': ['common_cause'],
                             'headline': head, 'surprise_bits': ev['bits'], 'patients': pts, 'dollars': None, 'evidence': ev})

        def covered(st, src, e):
            """The finding's lead episode falls inside a common-cause event (>= half its months)."""
            idx = set(range(e['start'], e['end'] + 1))
            return any(st in ev['streams'] and (ev['sources'] == ['all'] or src in ev['sources']) and
                       len(idx & set(range(ev['start'], ev['end'] + 1))) >= 0.5 * len(idx) for ev in systemic)

        for (st, rule), items in by_rule.items():
            top_f, top = max(items, key=lambda x: (x[1]['bits'], x[0]['source_system'] == 'all'))
            overall = next(((f, e) for f, e in items if f['source_system'] == 'all'), None)
            sources = sorted({f['source_system'] for f, _ in items if f['source_system'] != 'all'})
            lead_f, lead = overall or (top_f, top)
            detail = '; '.join(f"{f['source_system']} {e['months']}" for f, e in sorted(items, key=lambda x: x[0]['source_system'])
                               if f['source_system'] != 'all')[:400]
            findings.append({'kind': 'data_feed', 'category': rule, 'series': None, 'stream': st, 'group_by': 'source_system',
                             'echo': covered(st, lead_f['source_system'], lead),
                             'group': 'all' if overall else ', '.join(sources), 'sources': sources,
                             'months': sorted({e['months'] for _, e in items})[:8], 'rules': [rule],
                             'headline': (f"{st} feed: {rule.replace('_', ' ')} in {lead['months']} "
                                          f"({'all sources' if overall else lead_f['source_system']}; typical month "
                                          f"{lead_f['typical_monthly_records']:,.0f} records" +
                                          (f", {lead['ratio_to_typical']}x typical" if lead.get('ratio_to_typical') is not None else '') + ')' +
                                          (f" - {len(sources)} source system{'s' if len(sources) != 1 else ''} affected: {detail}" if sources else '')),
                             'surprise_bits': top['bits'], 'dollars': None,
                             # Affected = records missing (gap) or in excess of the typical month (spike), bounded by the feed's patients.
                             'patients': min(lead_f['patients'] if overall else sum(f['patients'] for f, _ in items),
                                             sum(_affected_records(f, e) for f, e in ([overall] if overall else items))),
                             'evidence': {'episodes': [{'source_system': f['source_system'], **e} for f, e in items][:20],
                                          'typical_monthly_records': lead_f['typical_monthly_records'],
                                          'records_by_month': dict(zip(lead_f['months'], lead_f['records']))}})
        for s, tg in type_gaps.items():
            if tg and tg['gaps']:
                g0 = max(tg['gaps'], key=lambda e: e['bits'])
                rng = _fmt_month_range(months, g0['start'], g0['end'])
                if not any(i in report_idx for i in range(g0['start'], g0['end'] + 1)): continue
                findings.append({'kind': 'data_feed', 'category': 'event_type_gap', 'series': s, 'stream': 'encounters', 'group_by': 'none',
                                 'group': 'all', 'months': [rng], 'rules': ['zero_run'],
                                 'headline': (f"{tg['concept'].replace('_', ' ')} events vanish for {g0['end'] - g0['start'] + 1} months ({rng}) while "
                                              f"encounters keep arriving ({int(sum(tg['stream_records'][g0['start']:g0['end'] + 1])):,} visit records): "
                                              'a feed or coding gap, not a change in care'),
                                 'surprise_bits': g0['bits'], 'patients': len(pop.months), 'dollars': None,
                                 'evidence': {'gap': g0, 'events_by_month': dict(zip(months, tg['events'])),
                                              'visits_by_month': dict(zip(months, tg['stream_records']))}})
        # Claims integrity review flags.
        integrity = {}
        if claims_checks:
            rep_claims = [r for r in claims if r[3][:7] in report]
            gb = p['group_by'] if p['group_by'] in ('payer', 'facility', 'source_system', 'none') else 'payer'
            gkey = {'payer': lambda r: r[2] or 'unknown', 'source_system': lambda r: r[2] or 'unknown',
                    'facility': lambda r: r[9] or 'unknown', 'none': lambda r: 'all'}[gb]
            if 'benford' in claims_checks:
                amounts = ['billed', 'allowed'] if p['amount'] == 'both' else [p['amount']]
                rows = []
                group_sets = [(gb, gkey)] + ([('facility', lambda r: r[9] or 'unknown')] if p['group_by'] == 'auto' else [])
                for label, key in group_sets:
                    parts = defaultdict(list)
                    for r in rep_claims: parts[key(r)].append(r)
                    if label != 'none': parts['all'] = rep_claims
                    for g, rs in sorted(parts.items()):
                        for a in amounts:
                            t = benford_test([r[6] if a == 'billed' else r[7] for r in rs])
                            tests += 1 if t['status'] not in ('insufficient',) else 0
                            rows.append({'group_by': label if g != 'all' else 'none', 'group': g, 'amount': a, **t,
                                         'patients': len({r[1] for r in rs}),
                                         'dollars': round(sum(((r[6] if a == 'billed' else r[7]) or 0) for r in rs) / 100, 2)})
                integrity['benford'] = rows
                for t in rows:
                    if t['status'] == 'review_flag':
                        worst = int(np.argmax([abs(o - e) for o, e in zip(t['observed_share'], BENFORD)])) + 1
                        findings.append({'kind': 'claims_review_flag', 'category': 'benford_nonconformity', 'series': None, 'amount': t['amount'],
                                         'group_by': t['group_by'], 'group': t['group'], 'months': [f'{report[0]}..{report[-1]}'], 'rules': ['benford_first_digit'],
                                         'headline': (f"Review flag: {t['amount']} amounts for {t['group_by']} {t['group']} do not follow Benford's first-digit "
                                                      f"pattern (MAD {t['mad']}, chi-square p={t['p_value']:.1g}, n={t['n']}; digit {worst} "
                                                      f"{t['observed_share'][worst - 1]:.1%} vs {BENFORD[worst - 1]:.1%} expected). Worth a look; not evidence of wrongdoing."),
                                         'surprise_bits': t['bits'], 'patients': t['patients'], 'dollars': t['dollars'],
                                         'evidence': {k: t[k] for k in ('n', 'counts', 'observed_share', 'expected_share', 'mad', 'conformity',
                                                                        'chi_square', 'p_value', 'kl_bits_per_value', 'orders_of_magnitude')}})
            if 'peer_outliers' in claims_checks:
                po = peer_outliers(rep_claims)
                integrity['peer_outliers'] = po
                tests += len(po['rows'])
                for r in po['rows']:
                    if r.get('status') == 'review_flag':
                        findings.append({'kind': 'claims_review_flag', 'category': 'billing_intensity_outlier', 'series': None,
                                         'group_by': 'service_organization', 'group': r['organization'], 'months': [f'{report[0]}..{report[-1]}'],
                                         'rules': ['robust_z_eb_shrunk'],
                                         'headline': (f"Review flag: {r['organization']} bills {r['effect_vs_peers']}x the peer level for the same service mix "
                                                      f"(robust z {r['robust_z']} vs {r['peer_count']} {r['peer_group']} peers, {r['claims']} claims). "
                                                      'Could reflect charge-master differences or service intensity; not evidence of wrongdoing.'),
                                         'surprise_bits': r['bits'], 'patients': r['patients'], 'dollars': abs(r['excess_billed']),
                                         'evidence': r})
            if 'duplicates' in claims_checks:
                dc = duplicate_claims(rep_claims, gkey)
                integrity['duplicates'] = dc
                tests += len(dc['groups'])
                for g in dc['groups']:
                    if g['bits'] >= 4.32:  # p < 0.05 above the pooled rate
                        findings.append({'kind': 'claims_review_flag', 'category': 'duplicate_claims', 'series': None, 'group_by': gb,
                                         'group': g['group'], 'months': [f'{report[0]}..{report[-1]}'], 'rules': ['exact_duplicate_rate'],
                                         'headline': (f"Review flag: {g['extra_copies']} possible duplicate claims for {gb} {g['group']} "
                                                      f"({g['rate']:.2%} of {g['claims']} vs {dc['pooled_rate']:.2%} pooled; ${g['billed_in_extra_copies']:,.0f} billed). "
                                                      'Could be legitimate repeat services; check before acting.'),
                                         'surprise_bits': g['bits'], 'patients': None, 'dollars': g['billed_in_extra_copies'], 'evidence': g})
                if dc['extra_copies'] and not any(f['category'] == 'duplicate_claims' for f in findings):
                    findings.append({'kind': 'claims_review_flag', 'category': 'duplicate_claims', 'series': None, 'group_by': 'none', 'group': 'all',
                                     'months': [f'{report[0]}..{report[-1]}'], 'rules': ['exact_duplicate'],
                                     'headline': (f"Review flag: {dc['extra_copies']} claims duplicate another claim exactly (same patient, organization, date, "
                                                  f"service and amount; ${dc['billed_in_extra_copies']:,.0f} billed), evenly spread across {gb}s. "
                                                  'Could be legitimate repeat services; check before acting.'),
                                     'surprise_bits': 0.0, 'patients': len({x['patient_id'] for x in dc['examples']}), 'dollars': dc['billed_in_extra_copies'],
                                     'evidence': {k: dc[k] for k in ('exact_sets', 'extra_copies', 'pooled_rate', 'examples')}})
        look = round(math.log2(max(tests, 1)), 2)
        for f in findings:
            f['look_elsewhere_bits'] = look
            f['excess_bits'] = round(f['surprise_bits'] - look, 2)
            f['salient'] = f['excess_bits'] >= 0
            impact = (f['patients'] or 0) + ((f['dollars'] or 0) / annual if annual else 0)
            f['impact'] = round(impact, 1)
            f['score'] = score(f['excess_bits'], impact) * (0.1 if f.get('echo') else 1.0)
            f['follow_up'] = follow_up(f)
        findings.sort(key=lambda f: (-f['score'], -f['surprise_bits']))
        chart_status.sort(key=lambda c: ({'rate_signal': 0, 'data_feed_anomaly': 1, 'in_control': 2}[c['status']], -c['surprise_bits']))
    finally:
        db.close()
    return {'schema': VERSION, 'check': p['check'], 'parameters': p, 'period': {'chart_months': [months[0], months[-1]], 'report_months': [report[0], report[-1]]},
            'rows': findings, 'chart_status': chart_status, 'feeds': feed_reports, 'event_type_gaps': {k: {kk: vv for kk, vv in v.items() if kk != 'chart'}
                                                                                                       for k, v in type_gaps.items() if v},
            'integrity': integrity, 'tests_examined': tests, 'look_elsewhere_bits': round(math.log2(max(tests, 1)), 2),
            'annual_allowed_per_member': _r(annual), 'members_in_scope': len(pop.months),
            'method': METHOD, 'limitations': LIMITATIONS}


METHOD = ('Monthly control charts over a 36-month window ending at the last complete month: u-charts (events per member-month, per-point '
          'limits sqrt(u/n)), p-charts (denials per claim, sqrt(p(1-p)/n)) and x-bar charts (PMPM, arrival lag; pooled SD/sqrt(n)), Phase-I '
          'trimmed centers, Laney sigma-z (median moving range) widening for overdispersion, Western Electric/Nelson rules and EWMA '
          '(lambda 0.2, L 2.86). Data-feed anomalies (zero runs while the stream flows, stream x source volume gaps/drops/spikes, '
          'single-month magnitudes >= 5x baseline) are separated from rate signals. Claims integrity: Benford first digit (chi-square '
          '+ Nigrini MAD; n >= 300, >= 2 orders of magnitude, not fee-schedule dominated), billing intensity O/E for service mix with '
          'empirical-Bayes shrinkage and robust peer z, exact/near duplicate claims. Surprise = -log2 p; excess = surprise - log2(tests '
          'examined); score = excess bits x log2(2 + patients + dollars in member-years of average spend).')


def _r(v):
    if v is None or not isinstance(v, (int, float)) or not math.isfinite(v): return None
    return round(float(v), 4 if abs(v) < 1 else 2)


def follow_up(f):
    """A question that reproduces the finding through the normal answer path (compiles to monitor)."""
    noun = {'ed_rate': 'ED use', 'admit_rate': 'admissions', 'pmpm': 'PMPM', 'lab_volume': 'lab volume', 'denial_rate': 'claim denials',
            'arrival_lag': 'claim arrival lag'}
    grp = {'practice': 'practices', 'payer': 'payers', 'facility': 'facilities', 'source_system': 'source systems'}
    if f['category'] == 'benford_nonconformity':
        return f"Run a Benford check on {f.get('amount', 'billed')} amounts by {f['group_by'] if f['group_by'] in ('payer', 'facility') else 'payer'}"
    if f['category'] == 'billing_intensity_outlier': return 'Which service organizations are billing outliers versus their peers?'
    if f['category'] == 'duplicate_claims': return 'Are there duplicate claims?'
    if f['category'] in ('warehouse_volume_shift', 'source_silence'): return 'Are any of our data feeds behaving abnormally?'
    if f['kind'] == 'data_feed' and f.get('stream') and not f.get('series'):
        return f"Is the {f['stream'].replace('_', ' ')} feed from any source system behaving abnormally?"
    s = f.get('series')
    if f['group_by'] in grp: return f"Which {grp[f['group_by']]} are out of control on {noun.get(s, s)}?"
    return f"Is {noun.get(s, s)} out of control?"


def summarize(report, limit=5):
    """Direct answer for the shared answer contract: (one-paragraph value, table)."""
    check, rows = report['check'], report['rows']
    if check in RA_CHARTS: return _summarize_ra(report, limit)
    lead = [f for f in rows if not f.get('echo')]
    if check == 'benford':
        table = [{k: t.get(k) for k in ('group_by', 'group', 'amount', 'n', 'status', 'conformity', 'mad', 'chi_square', 'p_value',
                                        'orders_of_magnitude', 'top10_amount_share', 'observed_share', 'reason')}
                 for t in report['integrity'].get('benford', [])]
        flagged = [t for t in table if t['status'] == 'review_flag']
        status = defaultdict(list)
        for t in table: status[t['status']].append(str(t['group']))
        text = (f"Benford first-digit check on {report['parameters']['amount']} amounts: "
                + (f"{len(flagged)} review flag(s) ({', '.join(str(t['group']) for t in flagged[:limit])}). " if flagged else 'no review flags. ')
                + '; '.join(f"{k.replace('_', ' ')}: {', '.join(v[:8])}" for k, v in status.items()) + '.')
        reasons = sorted({t['reason'] for t in table if t.get('reason') and t['status'] in ('not_applicable', 'low_power')})
        if reasons: text += ' ' + ' '.join(r[0].upper() + r[1:] + '.' for r in reasons[:2])
        return text + ' Review flags are prompts to look, not evidence of wrongdoing.', table
    if check == 'control_chart':
        table = [{k: v for k, v in c.items() if k != 'chart'} for c in report['chart_status']]
        rate = [c for c in table if c['status'] == 'rate_signal']
        data = [c for c in table if c['status'] == 'data_feed_anomaly']
        ok = [c for c in table if c['status'] == 'in_control']
        name = lambda c: 'overall' if c['group'] == 'all' else str(c['group'])
        text = (f"{len(rate)} of {len(table)} charts show rate signals after setting data problems aside"
                + (': ' + '; '.join(f"{name(c)} {LABELS[c['series']]} ({', '.join(c['rate_rules'])} in {', '.join(c['rate_signal_months'][:2])})"
                                    for c in rate[:limit]) if rate else '') + '. '
                + (f"{len(data)} have only data-feed anomalies ({', '.join(name(c) for c in data[:8])})" if data else 'No data-feed anomalies')
                + (f"; {len(ok)} in control" if ok else '') + '.')
        feed = next((f for f in lead if f['kind'] == 'data_feed'), None)
        if feed: text += ' Largest data issue: ' + feed['headline'] + '.'
        return text, table
    table = [{k: f.get(k) for k in ('kind', 'category', 'series', 'stream', 'group_by', 'group', 'months', 'rules', 'surprise_bits',
                                    'excess_bits', 'salient', 'patients', 'dollars', 'score', 'echo', 'headline', 'follow_up')} for f in rows]
    salient = [f for f in lead if f['salient']]
    parts = [f"{len(salient)} salient finding(s) of {len(rows)} ({sum(1 for f in salient if f['kind'] == 'data_feed')} data-feed, "
             f"{sum(1 for f in salient if f['kind'] == 'rate')} rate, {sum(1 for f in salient if f['kind'] == 'claims_review_flag')} claims review flags) "
             f"after a look-elsewhere cost of {report['look_elsewhere_bits']} bits"]
    parts += [f['headline'] for f in salient[:limit]]
    if report['chart_status']:
        st = defaultdict(int)
        for c in report['chart_status']: st[c['status']] += 1
        parts.append(f"control charts ({', '.join(sorted({c['series'] for c in report['chart_status']}))}): "
                     + ', '.join(f'{v} {k.replace("_", " ")}' for k, v in sorted(st.items())))
    integ = report.get('integrity') or {}
    if integ.get('benford') is not None:
        st = defaultdict(int)
        for t in integ['benford']: st[t['status']] += 1
        parts.append('Benford: ' + ', '.join(f'{v} {k.replace("_", " ")}' for k, v in st.items()))
    if integ.get('peer_outliers') is not None:
        parts.append(f"billing-intensity peers: {sum(r.get('status') == 'review_flag' for r in integ['peer_outliers']['rows'])} review flags among "
                     f"{len(integ['peer_outliers']['rows'])} service organizations")
    if integ.get('duplicates') is not None:
        d = integ['duplicates']
        parts.append(f"duplicates: {d['extra_copies']} exact duplicate claim(s) (${d['billed_in_extra_copies']:,.0f} billed), {d['near_pairs']} near pairs "
                     '(same patient, organization and service within a day)')
    return '. '.join(p.rstrip('.') for p in parts) + '.', table


def _summarize_ra(report, limit):
    if report.get('status') != 'answered': return report.get('reason') or 'No risk-adjusted chart could be built.', []
    s, p, rows = report['summary'], report['parameters'], report['rows']
    table = []
    for c in report['units']:
        row = {'unit': c['unit'], 'cases': c['cases'], 'observed': c['observed'], 'expected': c['expected'], 'o_e': c['o_e'], 'signals': len(c['signals']),
               'first_signal': c['signals'][0]['date'] if c['signals'] else None}
        if 'cusum_up' in c: row.update(cusum_max=c['cusum_up']['max'], cusum_final=c['cusum_up']['final'])
        if 'vlad' in c: row.update(vlad_final=c['vlad']['final'], vlad_final_z=c['vlad']['final_z'])
        table.append(row)
    lim = report.get('control_limits')
    name = 'Risk-adjusted CUSUM' if report['check'] == 'ra_cusum' else 'VLAD'
    text = (f"{name} for {p['outcome'].replace('_', ' ')} across {s['units_charted']} {p['unit']} unit(s), {s['cases']} cases "
            f"(network rate {s['network_rate']:.1%}; expected risks from {s['expected_risk_source']})"
            + (f", OR {p['or_shift']} detection, h {lim['h']}, ARL0 {p['arl0']} cases" if lim else '')
            + f": {s['units_with_signals']} unit(s) signal"
            + (': ' + '; '.join(f['headline'] for f in rows[:limit]) if rows else '') + '.')
    return text + ' Signals are prompts to review case mix and data, not evidence of worse care.', table
