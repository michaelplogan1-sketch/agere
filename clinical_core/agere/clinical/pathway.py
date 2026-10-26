"""Care pathways: Markov transition chains and process mining over per-patient visit sequences.

Answers "how do heart failure patients move through care after an ED visit?", "where do patients leak out of network
after discharge?", "after an ED visit, how many get primary care within 7 days vs return to the ED?", "most common care
paths for new diabetics" and "probability a CHF patient is admitted within 90 days starting from a PCP visit".

Events (one per patient, setting and day; the semantic `visit` table is already one row per encounter, latest version):
  emergency, inpatient, observation, urgent_care, primary_care, specialty, telehealth, post_acute  visit settings
  outside       a visit at an outside organisation (organisation id marked OUTSIDE; optionally primary care at a practice
                other than the patient's attributed practice) = leakage
  discharge     hospital stay discharge with a received discharge date (optional)
  referral      referral record (optional)
  diagnosis     first documented record of a library condition (only when it is the start state)
  end           no further recorded contact within the window (absorbing)
Methods: first-order (and second-order) Markov transition counts with Wilson and Dirichlet-posterior intervals and
median days between steps; absorbing-chain analysis (step-limited and eventual absorption, fundamental matrix, expected
steps/days) with Dirichlet posterior draws for intervals; empirical reach within N days; top-k path variants;
conformance to an expected step; bottlenecks; leakage; group comparison of transition rows (chi-square with a
patient-level permutation test, Benjamini-Hochberg across rows); Anderson-Goodman test of second vs first order.
"""
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
import json, math, re, sqlite3

import numpy as np
from scipy.stats import beta as beta_dist, chi2, norm

from . import concepts, semantic_layer

VERSION = 'pathway/1'
CARE_STATES = ('emergency', 'inpatient', 'observation', 'hospital_stay', 'urgent_care', 'primary_care', 'specialty', 'telehealth', 'post_acute')
EVENT_STATES = CARE_STATES + ('outside', 'other', 'discharge', 'referral', 'diagnosis')
STATES = EVENT_STATES + ('end',)
ACUTE = ('emergency', 'inpatient', 'observation', 'hospital_stay')
GROUP_BY = ('none', 'practice', 'coverage_type')
LEAKAGE = ('organization', 'organization_or_practice')
ANCHORS = ('each', 'first')
LABELS = {'emergency': 'ED', 'inpatient': 'inpatient', 'observation': 'observation', 'hospital_stay': 'hospital stay (inpatient or observation)', 'urgent_care': 'urgent care',
          'primary_care': 'primary care', 'specialty': 'specialty', 'telehealth': 'telehealth', 'post_acute': 'post-acute',
          'outside': 'outside organisation', 'other': 'other', 'discharge': 'discharge', 'referral': 'referral',
          'diagnosis': 'diagnosis', 'end': 'no further contact'}
CONCEPT_STATE = {'emergency_visit': 'emergency', 'inpatient_admission': 'inpatient', 'observation_stay': 'observation',
                 'urgent_care_visit': 'urgent_care', 'primary_care_visit': 'primary_care', 'specialist_visit': 'specialty',
                 'telehealth_contact': 'telehealth', 'skilled_nursing_or_post_acute': 'post_acute'}
SETTING_STATE = {'emergency': 'emergency', 'inpatient': 'inpatient', 'observation': 'observation', 'urgent_care': 'urgent_care',
                 'primary_care': 'primary_care', 'preventive': 'primary_care', 'specialty': 'specialty', 'behavioral_health': 'specialty',
                 'telehealth': 'telehealth', 'home_health': 'post_acute'}
RANK = {'diagnosis': -1, 'emergency': 0, 'urgent_care': 1, 'observation': 2, 'inpatient': 3, 'discharge': 4, 'referral': 6}
SMALL_GROUP = 11
Z = float(norm.ppf(0.975))
PRIOR_MASS = 1.0  # Dirichlet prior: total mass 1 spread evenly over the destinations observed anywhere (Perks)


def outside_organization(org):
    """Leakage definition shared with network.py: an organisation id marked OUTSIDE is outside the network."""
    return bool(org) and 'OUTSIDE' in str(org).upper()


# ---- parameters ----------------------------------------------------------------------
def _iso(v, what):
    if not isinstance(v, str) or (v and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', v)): raise ValueError(f'{what}: ISO date or ""')
    if v: date.fromisoformat(v)
    return v


def validate(p):
    allowed = {'start', 'index_concept', 'anchor', 'window_days', 'period', 'targets', 'max_steps', 'horizons_days', 'expected',
               'steps', 'top', 'group_by', 'include_other', 'discharge_events', 'referral_events', 'leakage', 'order',
               'gap_policy', 'permutations'}
    if not isinstance(p, dict) or set(p) - allowed: raise ValueError(f'pathway parameters: {sorted(allowed)}')
    out = {}
    start = p.get('start') or ''
    if start and start not in EVENT_STATES: raise ValueError(f'start must be one of {EVENT_STATES} or empty')
    out['start'] = start
    ic = p.get('index_concept') or ''
    if start == 'diagnosis':
        if ic not in concepts.CONDITIONS: raise ValueError('start diagnosis needs index_concept: a library condition')
        out['index_concept'] = ic
    elif ic:
        raise ValueError('index_concept applies only to start = diagnosis')
    out['anchor'] = p.get('anchor') or 'each'
    if out['anchor'] not in ANCHORS: raise ValueError(f'anchor must be one of {ANCHORS}')
    w = p.get('window_days', 90)
    if type(w) is not int or not 1 <= w <= 730: raise ValueError('window_days 1..730')
    out['window_days'] = w
    per = p.get('period') or {}
    if not isinstance(per, dict) or set(per) - {'start', 'end'}: raise ValueError('period takes start/end')
    out['period'] = {'start': _iso(per.get('start', ''), 'period.start'), 'end': _iso(per.get('end', ''), 'period.end')}
    if all(out['period'].values()) and out['period']['start'] > out['period']['end']: raise ValueError('period start after end')
    targets = p.get('targets') or ['emergency', start if start in ('inpatient', 'observation') else 'hospital_stay']
    if not isinstance(targets, list) or not 1 <= len(targets) <= 4 or len(set(targets)) != len(targets) or \
            any(t not in EVENT_STATES or t == 'diagnosis' for t in targets):
        raise ValueError('targets: 1..4 distinct event states')
    out['targets'] = targets
    named = {start, *targets, *((p.get('expected') or {}).get('to') or [])}
    if 'hospital_stay' in named and named & {'inpatient', 'observation'}:
        raise ValueError('Overlapping hospital-stay and inpatient/observation pathway states need separate analyses')
    ms = p.get('max_steps', 5)
    if type(ms) is not int or not 1 <= ms <= 20: raise ValueError('max_steps 1..20')
    out['max_steps'] = ms
    hz = p.get('horizons_days') or sorted({x for x in (7, 30) if x < w} | {w})
    if not isinstance(hz, list) or not 1 <= len(hz) <= 8 or any(type(x) is not int or not 1 <= x <= w for x in hz):
        raise ValueError('horizons_days: 1..8 integer days within window_days')
    out['horizons_days'] = sorted(set(hz))
    exp = p.get('expected') or None
    if isinstance(exp, dict) and not exp.get('from') and not exp.get('to'): exp = None  # schema-shaped empty payload
    if exp is not None:
        if not isinstance(exp, dict) or set(exp) - {'from', 'to', 'within_days'}: raise ValueError('expected takes from/to/within_days')
        to = exp.get('to') if isinstance(exp.get('to'), list) else [exp.get('to')]
        if exp.get('from') not in EVENT_STATES or not to or any(t not in EVENT_STATES for t in to) or len(to) > 6:
            raise ValueError('expected.from / expected.to must be event states')
        wd = exp.get('within_days', 7)
        if type(wd) is not int or not 1 <= wd <= w: raise ValueError('expected.within_days 1..window_days')
        if exp['from'] == 'diagnosis' and start != 'diagnosis': raise ValueError('expected.from diagnosis needs start diagnosis')
        out['expected'] = {'from': exp['from'], 'to': sorted(set(to)), 'within_days': wd}
    else:
        out['expected'] = None
    for k, lo, hi, d in (('steps', 1, 6, 3), ('top', 1, 50, 10), ('order', 1, 2, 1), ('permutations', 0, 5000, 999)):
        v = p.get(k, d)
        if type(v) is not int or not lo <= v <= hi: raise ValueError(f'{k} {lo}..{hi}')
        out[k] = v
    out['group_by'] = p.get('group_by') or 'none'
    if out['group_by'] not in GROUP_BY: raise ValueError(f'group_by must be one of {GROUP_BY}')
    for k, d in (('include_other', False), ('discharge_events', True), ('referral_events', False)):
        v = p.get(k, d)
        if not isinstance(v, bool): raise ValueError(f'{k} must be boolean')
        out[k] = v
    out['leakage'] = p.get('leakage') or 'organization'
    if out['leakage'] not in LEAKAGE: raise ValueError(f'leakage must be one of {LEAKAGE}')
    out['gap_policy'] = p.get('gap_policy') or 'exclude'
    if out['gap_policy'] not in ('exclude', 'include'): raise ValueError('gap_policy exclude|include')
    if start == 'referral' or 'referral' in targets or (out['expected'] and 'referral' in [out['expected']['from'], *out['expected']['to']]):
        out['referral_events'] = True
    if start == 'discharge' or 'discharge' in targets or (out['expected'] and 'discharge' in [out['expected']['from'], *out['expected']['to']]):
        out['discharge_events'] = True
    if start == 'other' or 'other' in targets: out['include_other'] = True
    return out


# ---- small statistics ------------------------------------------------------------------
def wilson(k, n):
    if n <= 0: return None, None
    ph = k / n; den = 1 + Z * Z / n
    c = (ph + Z * Z / (2 * n)) / den; h = Z * math.sqrt(ph * (1 - ph) / n + Z * Z / (4 * n * n)) / den
    return max(0.0, c - h), min(1.0, c + h)


def dirichlet_interval(k, n, support):
    """Marginal 95% interval of one cell under a Dirichlet(counts + 1/support) posterior over `support` destinations."""
    if n <= 0 or support < 1: return None, None
    a = PRIOR_MASS / support
    a1, b1 = k + a, n - k + (support - 1) * a
    return float(beta_dist.ppf(0.025, a1, b1)), float(beta_dist.ppf(0.975, a1, b1))


def bh(pvals):
    p = np.asarray(pvals, float); n = len(p)
    if not n: return []
    order = np.argsort(p); q = np.empty(n); prev = 1.0
    for rank, i in reversed(list(enumerate(order, 1))):
        prev = min(prev, p[i] * n / rank); q[i] = prev
    return q.tolist()


def _chi2(table):
    t = np.asarray(table, float)
    t = t[t.sum(1) > 0][:, t.sum(0) > 0]
    if t.shape[0] < 2 or t.shape[1] < 2: return 0.0, 0, t
    e = t.sum(1, keepdims=True) * t.sum(0, keepdims=True) / t.sum()
    return float(((t - e) ** 2 / e).sum()), (t.shape[0] - 1) * (t.shape[1] - 1), t


# ---- Markov chain --------------------------------------------------------------------------
def transition_matrix(counts):
    """Row-normalised probabilities; rows without observations become absorbing (self loop)."""
    C = np.asarray(counts, float); P = np.zeros_like(C)
    for i, n in enumerate(C.sum(1)):
        if n > 0: P[i] = C[i] / n
        else: P[i, i] = 1.0
    return P


def absorption(P, states, start, targets, max_steps):
    """From `start`, the first step follows P[start] (so the start may itself be a target, e.g. ED return); targets and
    'end' are absorbing thereafter. Returns step-limited and eventual absorption probabilities per target, expected
    steps to absorption, and the fundamental matrix rows used."""
    idx = {s: i for i, s in enumerate(states)}
    absorbing = [idx[t] for t in targets if t in idx] + ([idx['end']] if 'end' in idx and 'end' not in targets else [])
    A = np.array(P, float)
    for j in absorbing: A[j] = 0; A[j, j] = 1
    v = np.array(P[idx[start]], float)
    by_step, cur = [], v.copy()
    for _ in range(max_steps):
        by_step.append({t: float(cur[idx[t]]) if t in idx else 0.0 for t in targets})
        cur = cur @ A
    trans = [i for i in range(len(states)) if i not in absorbing]
    Q = A[np.ix_(trans, trans)]; R = A[np.ix_(trans, absorbing)]
    try:
        N = np.linalg.inv(np.eye(len(trans)) - Q)
    except np.linalg.LinAlgError:
        N = np.linalg.pinv(np.eye(len(trans)) - Q)
    vt = v[trans]
    eventual_abs = v[absorbing] + vt @ N @ R
    eventual = {states[j]: float(eventual_abs[k]) for k, j in enumerate(absorbing)}
    steps = 1.0 + float(vt @ N @ np.ones(len(trans)))
    return {'by_step': by_step, 'eventual': {t: eventual.get(t, 0.0) for t in targets}, 'eventual_end': eventual.get('end'),
            'expected_steps': steps, '_N': N, '_trans': trans, '_v': v}


def second_order_test(triples):
    """Anderson-Goodman likelihood-ratio test of a second-order chain against first order. triples: Counter((a,b,c))."""
    n_ab, n_bc, n_b = Counter(), Counter(), Counter()
    for (a, b, c), n in triples.items(): n_ab[a, b] += n; n_bc[b, c] += n; n_b[b] += n
    g = 0.0
    for (a, b, c), n in triples.items():
        g += 2 * n * math.log(n * n_b[b] / (n_ab[a, b] * n_bc[b, c]))
    df = 0
    for b in n_b:
        A = len({a for (a, bb) in n_ab if bb == b}); C = len({c for (bb, c) in n_bc if bb == b})
        df += (A - 1) * (C - 1)
    return {'g_statistic': round(g, 3), 'df': df, 'p_value': float(chi2.sf(g, df)) if df > 0 else None}


# ---- sequences ---------------------------------------------------------------------------
def _d(s): return date.fromisoformat(s[:10])


def _days(a, b): return (_d(b) - _d(a)).days


def _months(d0, d1):
    y, m = d0.year, d0.month
    while (y, m) <= (d1.year, d1.month):
        yield f'{y:04d}-{m:02d}'
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def acute_gaps(monthly, min_run=2, floor=20):
    """Runs of >= min_run calendar months with zero acute (ED/inpatient/observation) visits while overall visit volume is
    normal (>= floor and >= 10% of the median month), inside the span of months that do have acute visits.
    monthly: {YYYY-MM: (acute, total)}. A data-feed defect, not an absence of acute care."""
    acute_months = sorted(m for m, (a, _) in monthly.items() if a)
    if not acute_months: return []
    totals = sorted(t for _, t in monthly.values())
    bar = max(floor, 0.1 * totals[len(totals) // 2])
    first, last = acute_months[0], acute_months[-1]
    span = list(_months(_d(first + '-01'), _d(last + '-01')))
    runs, cur = [], []
    for m in span:
        a, t = monthly.get(m, (0, 0))
        if a == 0 and t >= bar: cur.append(m)
        else:
            if len(cur) >= min_run: runs.append(cur)
            cur = []
    if len(cur) >= min_run: runs.append(cur)
    return [{'start': r[0], 'end': r[-1], 'months': len(r), 'visits_in_gap': sum(monthly[m][1] for m in r)} for r in runs]


def load(db, patient_ids, p):
    """Per-patient sorted event sequences [(date, rank, time, state, org)], patient attributes and monthly acute volume."""
    members = set(patient_ids) if patient_ids is not None else {r[0] for r in db.execute('SELECT patient_id FROM patient')}
    attrs = {pid: {'practice': org or 'unattributed', 'coverage_type': cov or 'unknown'}
             for pid, org, cov in db.execute('SELECT patient_id, pcp_practice_id, coverage_type FROM patient') if pid in members}
    practices = {r[0] for r in db.execute('SELECT DISTINCT pcp_practice_id FROM patient WHERE pcp_practice_id IS NOT NULL')}
    cols = {r[1] for r in db.execute('PRAGMA table_info(visit)')}
    setting = 'setting' if 'setting' in cols else 'NULL'
    start_time = 'start_time' if 'start_time' in cols else 'NULL'
    events = defaultdict(list); monthly = defaultdict(lambda: [0, 0]); dropped = Counter()
    for pid, d, t, concept, sett, org in db.execute(
            f"SELECT patient_id, substr(event_date, 1, 10), coalesce({start_time}, ''), type_concept, {setting}, organization_id "
            "FROM visit WHERE event_date IS NOT NULL"):
        state = CONCEPT_STATE.get(concept) or SETTING_STATE.get(sett) or 'other'
        monthly[d[:7]][1] += 1
        if state in ACUTE: monthly[d[:7]][0] += 1
        if pid not in members: continue
        if outside_organization(org):
            state = 'outside'
        elif p['leakage'] == 'organization_or_practice' and state == 'primary_care' and org in practices and \
                attrs.get(pid, {}).get('practice') not in (None, 'unattributed', org):
            state = 'outside'
        if state == 'other' and not p['include_other']: dropped['other_setting_visits'] += 1; continue
        events[pid].append((d, RANK.get(state, 5), t, state, org))
    if p['discharge_events']:
        for pid, dis, org in db.execute('SELECT patient_id, discharge_date, organization_id FROM stay WHERE discharge_known = 1 AND discharge_date IS NOT NULL'):
            if pid in members: events[pid].append((dis[:10], RANK['discharge'], '', 'discharge', org))
    if p['referral_events']:
        for pid, d, org in db.execute("SELECT patient_id, substr(event_date, 1, 10), organization_id FROM record WHERE stream = 'referrals' AND event_date IS NOT NULL"):
            if pid in members: events[pid].append((d, RANK['referral'], '', 'referral', org))
    if p['start'] == 'diagnosis':
        for pid, d in db.execute("SELECT patient_id, min(coalesce(substr(event_date, 1, 10), substr(recorded_date, 1, 10))) FROM condition "
                                 "WHERE concept = ? GROUP BY patient_id", (p['index_concept'],)):
            if pid in members and d: events[pid].append((d, RANK['diagnosis'], '', 'diagnosis', None))
    seqs = {}
    for pid, ev in events.items():
        ev.sort(key=lambda e: (e[0], e[1], e[2]))
        out = []
        for e in ev:
            if out and out[-1][0] == e[0] and out[-1][1] == e[3]: dropped['same_day_repeat'] += 1; continue
            out.append((e[0], e[3]))
        seqs[pid] = out
    for pid in members: attrs.setdefault(pid, {'practice': 'unattributed', 'coverage_type': 'unknown'})
    return seqs, attrs, {m: tuple(v) for m, v in monthly.items()}, dropped


# ---- analysis ------------------------------------------------------------------------------
def analyse(seqs, attrs, p, as_of, gaps=(), dropped=None, seed=0):
    """Pure computation over {patient: [(date, state), ...]} sorted sequences."""
    # Mike 2026-10-25: the unqualified hospital state pools inpatient and observation transitions.
    if p['start'] and 'hospital_stay' in [p['start'], *p['targets'], *((p.get('expected') or {}).get('to') or [])]:
        seqs = {pid: [(d, 'hospital_stay' if s in ('inpatient', 'observation') else s) for d, s in seq] for pid, seq in seqs.items()}
    W, per = p['window_days'], p['period']
    gap_months = {m for g in gaps for m in _months(_d(g['start'] + '-01'), _d(g['end'] + '-01'))}
    exclude_gaps = p['gap_policy'] == 'exclude' and gap_months

    def in_gap(d0, d1):
        return bool(exclude_gaps) and any(m in gap_months for m in _months(_d(d0), min(_d(d1), _d(as_of))))

    def in_period(d):
        return (not per['start'] or d >= per['start']) and (not per['end'] or d <= per['end'])

    plus = lambda d, n: (_d(d) + timedelta(days=n)).isoformat()
    excluded = Counter(dropped or {})
    trans = []  # (pid, from, to, days or None, prev or None, date)
    for pid, seq in seqs.items():
        for i, (d, s) in enumerate(seq):
            if not in_period(d) or d > as_of: continue
            nxt = seq[i + 1] if i + 1 < len(seq) and seq[i + 1][0] <= as_of else None
            if nxt and _days(d, nxt[0]) <= W:
                to, gap, stop = nxt[1], _days(d, nxt[0]), nxt[0]
            elif nxt or _days(d, as_of) >= W:
                to, gap, stop = 'end', None, plus(d, W)
            else:
                excluded['transition_follow_up_incomplete'] += 1; continue
            if in_gap(d, stop): excluded['transition_window_overlaps_acute_data_gap'] += 1; continue
            prev = seq[i - 1][1] if i > 0 and _days(seq[i - 1][0], d) <= W else None
            trans.append((pid, s, to, gap, prev, d))
    used = [s for s in STATES if any(t[1] == s or t[2] == s for t in trans)]
    if p['start'] and p['start'] not in used: used = sorted(set(used) | {p['start']}, key=STATES.index)
    for t in p['targets']:
        if t not in used: used = sorted(set(used) | {t}, key=STATES.index)
    if 'end' not in used: used.append('end')
    idx = {s: i for i, s in enumerate(used)}
    K = len(used)
    C = np.zeros((K, K)); gaps_by = defaultdict(list)
    for _, a, b, g, _, _ in trans:
        C[idx[a], idx[b]] += 1
        if g is not None: gaps_by[a, b].append(g)
    P = transition_matrix(C)
    support = int((C.sum(0) > 0).sum())
    rows = []
    for a in used:
        n = int(C[idx[a]].sum())
        if not n: continue
        for b in used:
            k = int(C[idx[a], idx[b]])
            if not k: continue
            lo, hi = wilson(k, n); dlo, dhi = dirichlet_interval(k, n, support)
            g = gaps_by.get((a, b))
            rows.append({'from': a, 'to': b, 'count': k, 'row_total': n, 'probability': round(k / n, 4),
                         'ci95_low': round(lo, 4), 'ci95_high': round(hi, 4), 'dirichlet_low': round(dlo, 4), 'dirichlet_high': round(dhi, 4),
                         'median_days': float(np.median(g)) if g else None,
                         'p25_days': float(np.percentile(g, 25)) if g else None, 'p75_days': float(np.percentile(g, 75)) if g else None})
    start = p['start']
    rows.sort(key=lambda r: (r['from'] != start, used.index(r['from']), -r['count']))
    # absorbing chain from the start state, with Dirichlet posterior draws for intervals
    chain = None
    if start and C[idx[start]].sum() > 0:
        hold = np.array([np.mean([g for (a, b), gs in gaps_by.items() if a == s for g in gs]) if any(a == s for a, _ in gaps_by) else 0.0
                         for s in used])
        res = absorption(P, used, start, p['targets'], p['max_steps'])
        N, tr, v = res.pop('_N'), res.pop('_trans'), res.pop('_v')
        days = float(hold[idx[start]] + v[tr] @ N @ hold[tr])
        rng = np.random.default_rng(seed); draws = defaultdict(list); step_draws = defaultdict(list); sup = C.sum(0) > 0
        for _ in range(400):
            Pd = np.zeros_like(P)
            for i in range(K):
                if C[i].sum() > 0:
                    alpha = np.where(sup, C[i] + PRIOR_MASS / sup.sum(), 0.0); Pd[i] = rng.dirichlet(alpha[alpha > 0]) @ np.eye(K)[alpha > 0]
                else: Pd[i, i] = 1.0
            r = absorption(Pd, used, start, p['targets'], p['max_steps'])
            for t in p['targets']:
                draws[t].append(r['eventual'][t]); step_draws[t].append(r['by_step'][-1][t])  # absorbed mass is cumulative
        chain = {'start': start, 'targets': p['targets'], 'max_steps': p['max_steps'],
                 'within_steps': {t: {'probability': round(res['by_step'][-1][t], 4),
                                      'ci95_low': round(float(np.percentile(step_draws[t], 2.5)), 4),
                                      'ci95_high': round(float(np.percentile(step_draws[t], 97.5)), 4)} for t in p['targets']},
                 'by_step': [{t: round(v_, 4) for t, v_ in s.items()} for s in res['by_step']],
                 'eventual': {t: {'probability': round(res['eventual'][t], 4), 'ci95_low': round(float(np.percentile(draws[t], 2.5)), 4),
                                  'ci95_high': round(float(np.percentile(draws[t], 97.5)), 4)} for t in p['targets']},
                 'eventual_end': round(res['eventual_end'], 4) if res['eventual_end'] is not None else None,
                 'expected_steps': round(res['expected_steps'], 3), 'expected_days_in_care': round(days, 1)}
    # anchors: empirical reach within N days and path variants
    anchors, variants, reach = [], Counter(), {}
    for pid, seq in seqs.items():
        cands = [i for i, (d, s) in enumerate(seq) if in_period(d) and d <= as_of and (s == start if start else True)]
        if not start or p['anchor'] == 'first': cands = cands[:1]
        for i in cands:
            d = seq[i][0]
            if _days(d, as_of) < W: excluded['anchor_follow_up_incomplete'] += 1; continue
            if in_gap(d, plus(d, W)): excluded['anchor_window_overlaps_acute_data_gap'] += 1; continue
            follow = [(_days(d, dd), s) for dd, s in seq[i + 1:] if _days(d, dd) <= W]
            anchors.append((pid, d, seq[i][1], follow))
            path = [seq[i][1]] + [s for _, s in follow[:p['steps']]]
            if len(follow) < p['steps']: path.append('end')
            variants[tuple(path)] += 1
    na = len(anchors)
    if start:
        for t in p['targets']:
            first = [next((dd for dd, s in f if s == t), None) for _, _, _, f in anchors]
            reach[t] = []
            for h in p['horizons_days']:
                k = sum(1 for x in first if x is not None and x <= h); lo, hi = wilson(k, na)
                reach[t].append({'days': h, 'reached': k, 'anchors': na, 'share': round(k / na, 4) if na else None,
                                 'ci95_low': round(lo, 4) if na else None, 'ci95_high': round(hi, 4) if na else None})
    top_variants = []
    for path, k in variants.most_common(p['top']):
        lo, hi = wilson(k, na)
        top_variants.append({'path': ' > '.join(path), 'states': list(path), 'count': k, 'share': round(k / na, 4),
                             'ci95_low': round(lo, 4), 'ci95_high': round(hi, 4)})
    # conformance to an expected step
    conformance = None
    if p['expected']:
        e = p['expected']; hits, n, instead, competing = 0, 0, Counter(), 0
        conf_by_pid = defaultdict(lambda: [0, 0])
        for pid, seq in seqs.items():
            occ = [i for i, (d, s) in enumerate(seq) if s == e['from'] and in_period(d) and d <= as_of]
            if e['from'] == start and p['anchor'] == 'first': occ = occ[:1]
            for i in occ:
                d = seq[i][0]
                if _days(d, as_of) < e['within_days']: excluded['conformance_follow_up_incomplete'] += 1; continue
                if in_gap(d, plus(d, e['within_days'])): excluded['conformance_window_overlaps_acute_data_gap'] += 1; continue
                follow = [(dd, s) for dd, s in seq[i + 1:] if _days(d, dd) <= e['within_days']]
                ok = next((j for j, (_, s) in enumerate(follow) if s in e['to']), None)
                n += 1; conf_by_pid[pid][1] += 1
                if ok is not None:
                    hits += 1; conf_by_pid[pid][0] += 1
                else:
                    instead[follow[0][1] if follow else 'end'] += 1
                if any(s in ACUTE for _, s in follow[:ok if ok is not None else len(follow)]): competing += 1
        lo, hi = wilson(hits, n)
        conformance = {**e, 'occurrences': n, 'conforming': hits, 'share': round(hits / n, 4) if n else None,
                       'ci95_low': round(lo, 4) if n else None, 'ci95_high': round(hi, 4) if n else None,
                       'acute_before_expected': competing,
                       'instead_next': [{'state': s, 'count': k, 'share': round(k / (n - hits), 4)} for s, k in instead.most_common(6)]}
    # bottlenecks and leakage
    bottlenecks = sorted([r for r in rows if r['to'] != 'end' and r['count'] >= 5 and r['median_days'] is not None],
                         key=lambda r: (-r['median_days'], -r['count']))[:5]
    moves = [t for t in trans if t[2] != 'end']
    out_moves = [t for t in moves if t[2] == 'outside']
    lo, hi = wilson(len(out_moves), len(moves))
    by_source = Counter(t[1] for t in out_moves); from_tot = Counter(t[1] for t in moves)
    leakage = {'definition': p['leakage'], 'transitions': len(moves), 'to_outside': len(out_moves),
               'share': round(len(out_moves) / len(moves), 4) if moves else None,
               'ci95_low': round(lo, 4) if moves else None, 'ci95_high': round(hi, 4) if moves else None,
               'patients_with_outside_visit': len({t[0] for t in trans if t[1] == 'outside' or t[2] == 'outside'}),
               'by_source': [{'from': s, 'to_outside': k, 'transitions': from_tot[s], 'share': round(k / from_tot[s], 4)}
                             for s, k in by_source.most_common(6)]}
    # second order
    second = None
    if p['order'] == 2:
        triples = Counter((t[4], t[1], t[2]) for t in trans if t[4] is not None)
        second = second_order_test(triples) if triples else None
        if second is not None:
            pair_tot = Counter()
            for (a, b, _), n in triples.items(): pair_tot[a, b] += n
            second['rows'] = [{'previous': a, 'from': b, 'to': c, 'count': n, 'probability': round(n / pair_tot[a, b], 4),
                               'first_order_probability': round(float(P[idx[b], idx[c]]), 4)}
                              for (a, b, c), n in triples.most_common(p['top'] * 3) if pair_tot[a, b] >= 5][:p['top'] * 2]
    # group comparison
    groups = None
    if p['group_by'] != 'none':
        g_of = {pid: attrs.get(pid, {}).get(p['group_by'], 'unknown') for pid in seqs}
        labels = sorted(set(g_of.values()), key=str); gi = {g: i for i, g in enumerate(labels)}
        pids = sorted(seqs); pix = {pid: i for i, pid in enumerate(pids)}
        pg = np.array([gi[g_of[pid]] for pid in pids])
        rng = np.random.default_rng(seed + 1)
        perms = [rng.permutation(pg) for _ in range(p['permutations'])]
        tests = []
        for a in used:
            sel = [t for t in trans if t[1] == a]
            if len(sel) < 20: continue
            pi = np.array([pix[t[0]] for t in sel]); ci = np.array([idx[t[2]] for t in sel])
            tab = np.zeros((len(labels), K)); np.add.at(tab, (pg[pi], ci), 1)
            stat, df, kept = _chi2(tab)
            if df == 0: continue
            ge = 0
            for pr in perms:
                tb = np.zeros((len(labels), K)); np.add.at(tb, (pr[pi], ci), 1)
                ge += _chi2(tb)[0] >= stat - 1e-9
            e_small = float(((kept.sum(1, keepdims=True) * kept.sum(0, keepdims=True) / kept.sum()) < 5).mean())
            tests.append({'from': a, 'transitions': len(sel), 'chi_square': round(stat, 3), 'df': df, 'p_value': float(chi2.sf(stat, df)),
                          'permutation_p': (ge + 1) / (len(perms) + 1) if perms else None, 'share_expected_below_5': round(e_small, 3)})
        q = bh([t['permutation_p'] if t['permutation_p'] is not None else t['p_value'] for t in tests])
        for t, qq in zip(tests, q): t['q_value'] = round(qq, 4)
        tests.sort(key=lambda t: t['q_value'])
        per_group = []
        for g in labels:
            gt = [t for t in trans if g_of[t[0]] == g]; gm = [t for t in gt if t[2] != 'end']
            row = {'group': g, 'patients': sum(1 for pid in seqs if g_of[pid] == g), 'transitions': len(gt),
                   'leakage_share': round(sum(t[2] == 'outside' for t in gm) / len(gm), 4) if gm else None,
                   'small_group': len(gt) < SMALL_GROUP}
            if start:
                ga = [a for a in anchors if g_of[a[0]] == g]; row['anchors'] = len(ga)
                for t in p['targets']:
                    h = p['horizons_days'][-1]
                    k = sum(1 for a in ga if any(s == t and dd <= h for dd, s in a[3]))
                    row[f'{t}_within_{h}d'] = round(k / len(ga), 4) if ga else None
                srow = [x for x in gt if x[1] == start]
                if srow:
                    nxt = Counter(x[2] for x in srow).most_common(1)[0]
                    row['top_next'] = nxt[0]; row['top_next_share'] = round(nxt[1] / len(srow), 4)
            per_group.append(row)
        groups = {'by': p['group_by'], 'tests': tests, 'groups': per_group,
                  'method': 'Chi-square of next-state counts by group for each source state; p from a patient-level permutation of group labels '
                            f'({p["permutations"]} permutations; patients keep all their transitions); BH q across source states.'}
    report = {'schema': VERSION, 'parameters': p, 'as_of': as_of, 'states': used,
            'patients': len({t[0] for t in trans}), 'patients_with_events': sum(1 for s in seqs.values() if s),
            'transitions': len(trans), 'events': sum(len(s) for s in seqs.values()),
            'matrix': {'states': used, 'counts': C.astype(int).tolist(), 'probabilities': np.round(P, 4).tolist()},
            'table': rows, 'chain': chain, 'reach': reach, 'anchors': na, 'variants': top_variants, 'variants_total': len(variants),
            'conformance': conformance, 'bottlenecks': bottlenecks, 'leakage': leakage, 'second_order': second, 'groups': groups,
            'acute_data_gaps': list(gaps), 'excluded': dict(excluded)}
    report['method'] = method(report)
    report['notes'] = notes(report)
    return report


def execute(path, patient_ids, params):
    p = validate(params)
    path = Path(path)
    as_of = json.loads((path / 'run.json').read_text())['snapshot']['as_of']
    db = sqlite3.connect(f'file:{semantic_layer.ensure(path)}?mode=ro', uri=True)
    try:
        seqs, attrs, monthly, dropped = load(db, patient_ids, p)
    finally:
        db.close()
    gaps = acute_gaps({m: v for m, v in monthly.items() if m <= as_of[:7]})
    report = analyse(seqs, attrs, p, as_of, gaps, dropped)
    return report


def L(s): return LABELS.get(s, s)


def method(report):
    p = report['parameters']; W = p['window_days']
    text = ('Per-patient event sequences from the semantic visit table (one row per encounter, latest version), mapped to care settings; '
            'repeat events of the same setting on the same day are collapsed; same-day events are ordered ED, urgent care, observation, '
            'inpatient, discharge, other visits, referral. ')
    text += ('Visits at organisations marked OUTSIDE are the "outside organisation" state (leakage)' +
             (', as are primary-care visits at a practice other than the patient\'s attributed practice. ' if p['leakage'] == 'organization_or_practice' else '. '))
    if p['discharge_events']: text += 'Hospital stays with a received discharge date add a discharge event. '
    if p['referral_events']: text += 'Referral records add a referral event. '
    if p['start'] == 'diagnosis': text += f"Diagnosis = first documented record of {p['index_concept'].replace('_', ' ')} (cohort entry, not onset). "
    text += (f'First-order Markov transition counts between consecutive events; a next event more than {W} days later (or none, with '
             f'{W} days observed before the snapshot {report["as_of"]}) is "no further contact" (absorbing). Events with under {W} days of '
             'follow-up and no later event are censored, not counted. Cell intervals: Wilson 95% and Dirichlet posterior 95% (prior mass 1 spread over observed destinations). ')
    if report['chain']:
        text += (f"Absorbing-chain analysis from {L(p['start'])}: first step from its observed row, then {', '.join(L(t) for t in p['targets'])} "
                 'and no further contact absorbing; probabilities within ' + str(p['max_steps']) + ' steps and eventually (fundamental matrix), '
                 'with 95% intervals from 400 Dirichlet posterior draws of the transition matrix; expected days = expected visits to each '
                 'state x its mean days to the next event. Empirical reach = share of anchors with the target within N days. ')
    text += 'Path variants: the first ' + str(p['steps']) + ' steps after each anchor within the window. '
    if p['expected']: text += f"Conformance: share of {L(p['expected']['from'])} events followed by {', '.join(L(t) for t in p['expected']['to'])} within {p['expected']['within_days']} days. "
    if p['order'] == 2: text += 'Second order tested against first order by the Anderson-Goodman likelihood-ratio statistic. '
    if report['groups']: text += report['groups']['method'] + ' '
    text += ('A patient contributes every transition and anchor; intervals treat them as independent except the permutation test. '
             'Settings are received encounter types, not adjudicated care; no causal reading.')
    if report['acute_data_gaps']:
        text += (' Acute-data gaps (months with normal visit volume but no ED/inpatient/observation records, a known feed defect) are '
                 + ('excluded: any window overlapping them is dropped and counted.' if p['gap_policy'] == 'exclude' else 'INCLUDED (gap_policy=include): ED/admission rates in them read as zero.'))
    return text


def notes(report):
    out = []
    for g in report['acute_data_gaps']:
        out.append(f"No ED/inpatient/observation records {g['start']}..{g['end']} ({g['months']} months, {g['visits_in_gap']} other visits): "
                   'a data-feed gap, not absent acute care.')
    if report['leakage']['transitions'] and not report['leakage']['to_outside']:
        out.append('No transitions to outside organisations observed: leakage beyond the received feeds cannot be seen.')
    return out


def _pc(x): return f'{x:.1%}'


def summary(report):
    p = report['parameters']; parts = []
    start = p['start']
    if not report['transitions']:
        return 'No analysable transitions: ' + (', '.join(f'{k.replace("_", " ")} {v}' for k, v in report['excluded'].items()) or 'no events') + '.'
    if start:
        row = [r for r in report['table'] if r['from'] == start][:4]
        if row:
            parts.append(f"After {L(start)} ({row[0]['row_total']} transitions, {p['window_days']}-day window) the next step is " +
                         ', '.join(f"{L(r['to'])} {_pc(r['probability'])} ({_pc(r['ci95_low'])}-{_pc(r['ci95_high'])})" for r in row))
        else:
            parts.append(f'No observed transitions out of {L(start)}')
    else:
        top = sorted(report['table'], key=lambda r: -r['count'])[:3]
        parts.append(f"{report['transitions']} transitions among {report['patients']} patients; most frequent: " +
                     ', '.join(f"{L(r['from'])} > {L(r['to'])} {_pc(r['probability'])}" for r in top))
    c = report['conformance']
    if c and c['occurrences']:
        parts.append(f"{L(c['from'])} followed by {'/'.join(L(t) for t in c['to'])} within {c['within_days']} days: {_pc(c['share'])} "
                     f"({_pc(c['ci95_low'])}-{_pc(c['ci95_high'])}; {c['conforming']} of {c['occurrences']})")
    if report['reach']:
        bits = []
        for t, rs in report['reach'].items():
            r = rs[0] if c else rs[-1]
            if r['anchors']: bits.append(f"{L(t)} within {r['days']} days {_pc(r['share'])} ({_pc(r['ci95_low'])}-{_pc(r['ci95_high'])})")
        if bits: parts.append(f"of {report['anchors']} {L(start)} anchors, " + '; '.join(bits))
        else: parts.append(f'no {L(start)} anchor has a complete, analysable {p["window_days"]}-day window, so reach within N days is not estimated')
    ch = report['chain']
    if ch:
        parts.append('Markov chain, within ' + str(ch['max_steps']) + ' steps: ' +
                     '; '.join(f"{L(t)} {_pc(v['probability'])} ({_pc(v['ci95_low'])}-{_pc(v['ci95_high'])})" for t, v in ch['within_steps'].items()))
    lk = report['leakage']
    if lk['transitions']: parts.append(f"leakage to outside organisations {_pc(lk['share'])} of {lk['transitions']} transitions")
    if report['variants']:
        v = report['variants'][0]; parts.append(f"most common path {' > '.join(L(s) for s in v['states'])} ({_pc(v['share'])})")
    g = report['groups']
    if g and g['tests']:
        t = g['tests'][0]; parts.append(f"groups by {g['by']} differ most after {L(t['from'])} (permutation p {t['permutation_p']:.3g}, q {t['q_value']:.3g})")
    text = '. '.join(parts[:1]) + ('. ' + '; '.join(parts[1:]) if parts[1:] else '') + '.'
    ex = report['excluded']
    gap_excl = ex.get('anchor_window_overlaps_acute_data_gap', 0) if start else ex.get('transition_window_overlaps_acute_data_gap', 0)
    for gp in report['acute_data_gaps']:
        text += (f" ED/inpatient data absent {gp['start']}..{gp['end']} (feed defect); " +
                 (f"{gap_excl} {'anchors' if start else 'transitions'} whose window overlaps it excluded." if gap_excl else 'no analysed window overlaps it.'))
    return text
