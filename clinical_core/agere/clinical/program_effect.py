"""Program effect: did a received program (or medication start) change an outcome, under stated assumptions?

Exposure is defined only by received records: the first qualifying record per patient
(care-management contact the patient agreed to, completed transition-of-care call,
delivered community service, care-coordination contact, or first dated dispense/claim/
order of a drug class after a washout). Nothing here is randomised, so every estimate is
an *estimated effect under stated assumptions*, never an identified causal effect.

Design
  1. Matched difference-in-differences. Each exposed patient's index date is their first
     qualifying record; unexposed patients are exact-matched on age band, sex and payer,
     then nearest-neighbour matched (without replacement, caliper in score SDs) on a
     baseline risk/utilization score: the comparison pool's expected post-window outcome
     given pre-period outcome, ED + 2 x admissions and chronic-condition count measured in
     the same pre window at the exposed patient's index date (prognostic score). Controls inherit
     the exposed patient's index date. Effect = mean over matched sets of
     (post - pre)_exposed - mean(post - pre)_controls; 95% CI by cluster bootstrap over sets.
     Covariate balance is reported as standardised mean differences before/after matching.
  2. Synthetic-control style group comparison in calendar months when adoption is
     concentrated in time: the exposed group's monthly series is reproduced by a convex
     weighting of practice-level unexposed series over the pre months; inference by
     in-space placebo (each donor treated in turn; RMSPE ratio rank).
  3. Diagnostics: pre-period parallel-trends check (early vs late half of the pre window,
     bootstrap CI), an in-space placebo (unexposed patients given exposed index dates and
     run through the same pipeline; should be ~0), event-study bins, outcome feed-gap and
     spike checks over every analysed window, and explicit warnings when the data cannot
     support inference.
"""
from bisect import bisect_left
from collections import defaultdict
from datetime import date
import json, math, random, sqlite3

import numpy as np

from . import aggregate, concepts, semantic_layer

VERSION = 'program-effect/1'
OUTCOMES = ('ed_visits', 'admissions', 'pmpm', 'lab')
# program -> (stream, qualifying rule, definition). Patients with any record of the stream that does not qualify are
# excluded from the comparison pool (they were selected for the program; their non-participation is itself selected).
PROGRAMS = {
    'care_management': ('care_management_contacts', lambda r: r.get('status') == 'agrees',
                        'First care-management contact the patient agreed to (status agrees)'),
    'transitions_of_care': ('transition_of_care_contacts', lambda r: r.get('communication_result') == 'conversation_completed',
                            'First completed transition-of-care conversation (communication_result conversation_completed)'),
    'community_services': ('community_service_outcomes', lambda r: r.get('service_delivered') is True,
                           'First community service recorded as delivered (community_service_outcomes.service_delivered)'),
    'care_coordination': ('care_coordination_contacts', lambda r: bool(r.get('respondent')),
                          'First care-coordination contact with a respondent'),
}
MED_EVIDENCE = ('dispensed', 'claim', 'ordered')
MIN_ESTIMATE = 10     # matched exposed patients below which no estimate is reported
MIN_RELIABLE = 30     # below this the estimate is reported with a small-sample warning
SMD_LIMIT = 0.1
COVARIATES = ('pre_outcome', 'pre_utilization', 'chronic_conditions')
METHODS = ('matched_did', 'sdid', 'callaway_santanna', 'auto')
CONTROL_GROUPS = ('never_treated', 'not_yet_treated')
CS_ESTIMATORS = ('outcome_regression', 'ipw', 'doubly_robust')
PANEL_COVARIATES = ('age_band', 'sex', 'payer', 'chronic_conditions')
MIN_COHORT = 3        # exposed patients per adoption-month cohort below which the cohort is dropped (Callaway-Sant'Anna)
MIN_DONORS = 5        # practice-level donor series needed for synthetic DiD
SDID_PLACEBO_REPS = 200
ASSUMPTIONS = [
    'No unmeasured confounding of program receipt beyond age band, sex, payer, pre-period outcome, pre-period utilization '
    'and chronic-condition count (receipt was not randomised; selection by care teams is not observed).',
    'Parallel trends: absent the program, exposed and matched comparison patients would have changed alike from pre to post.',
    'No anticipation and no spillover: outcomes before the index date and outcomes of comparison patients are unaffected by the program.',
    'Outcomes are captured equally in both groups and across windows (received feeds; gaps are checked, not assumed away).',
    'Exposure = first qualifying received record; contacts or fills recorded elsewhere are invisible and bias toward no effect.',
]


def _int(p, key, default, lo, hi):
    v = p.get(key, default)
    if type(v) is not int or not lo <= v <= hi: raise ValueError(f'{key} must be an integer {lo}..{hi}')
    return v


def validate(p):
    allowed = {'program', 'drug_class', 'outcome', 'lab', 'pre_days', 'post_days', 'controls_per_exposed', 'min_followup_days',
               'bootstrap', 'seed', 'caliper', 'population_conditions', 'method', 'control_group', 'covariates', 'estimator'}
    if not isinstance(p, dict) or set(p) - allowed:
        raise ValueError('program_effect parameters: ' + ', '.join(sorted(allowed)))
    program = p.get('program')
    if program not in (*PROGRAMS, 'medication_start'):
        raise ValueError(f'program must be one of {sorted(PROGRAMS) + ["medication_start"]}')
    drug = p.get('drug_class', '')
    if program == 'medication_start':
        from .drug_classes import CLASSES
        if drug not in CLASSES: raise ValueError('medication_start needs a registered drug_class')
    elif drug: raise ValueError('drug_class applies to medication_start only')
    outcome = p.get('outcome')
    if outcome not in OUTCOMES: raise ValueError(f'outcome must be one of {OUTCOMES}')
    lab = p.get('lab', '')
    if outcome == 'lab' and lab not in concepts.MEASUREMENTS: raise ValueError('lab outcome needs a measurement concept')
    if outcome != 'lab' and lab: raise ValueError('lab applies to the lab outcome only')
    caliper = p.get('caliper', 0.25)
    if not isinstance(caliper, (int, float)) or isinstance(caliper, bool) or not 0.05 <= caliper <= 10:
        raise ValueError('caliper must be 0.05..10 (SDs of the baseline score)')
    conds = p.get('population_conditions', [])
    if not isinstance(conds, list) or len(conds) > 5 or any(c not in concepts.CONDITIONS for c in conds):
        raise ValueError('population_conditions: up to five condition concepts')
    out = {'program': program, 'outcome': outcome, 'pre_days': _int(p, 'pre_days', 180, 30, 730),
           'post_days': _int(p, 'post_days', 90, 14, 365), 'controls_per_exposed': _int(p, 'controls_per_exposed', 3, 1, 5),
           'min_followup_days': _int(p, 'min_followup_days', 14, 1, 180), 'bootstrap': _int(p, 'bootstrap', 1000, 200, 5000),
           'seed': _int(p, 'seed', 7, 0, 2 ** 31 - 1), 'caliper': float(caliper)}
    if drug: out['drug_class'] = drug
    if lab: out['lab'] = lab
    if conds: out['population_conditions'] = conds
    if out['min_followup_days'] > out['post_days']: raise ValueError('min_followup_days cannot exceed post_days')
    method = p.get('method', 'matched_did')
    if method not in METHODS: raise ValueError(f'method must be one of {METHODS}')
    if method != 'matched_did': out['method'] = method
    if 'control_group' in p:
        if p['control_group'] not in CONTROL_GROUPS: raise ValueError(f'control_group must be one of {CONTROL_GROUPS}')
        out['control_group'] = p['control_group']
    if 'estimator' in p:
        if p['estimator'] not in CS_ESTIMATORS: raise ValueError(f'estimator must be one of {CS_ESTIMATORS}')
        out['estimator'] = p['estimator']
    if 'covariates' in p:
        cv = p['covariates']
        if not isinstance(cv, list) or len(set(cv)) != len(cv) or any(c not in PANEL_COVARIATES for c in cv):
            raise ValueError(f'covariates: a list drawn from {PANEL_COVARIATES}')
        if cv: out['covariates'] = list(cv)
    if method == 'matched_did' and {'control_group', 'estimator', 'covariates'} & set(p):
        raise ValueError("control_group, estimator and covariates apply to method 'callaway_santanna' (or 'auto')")
    return out


def _day(s): return date.fromisoformat(s[:10]).toordinal()
def _iso(d): return date.fromordinal(d).isoformat()
def _month_start(d): x = date.fromordinal(d); return date(x.year, x.month, 1).toordinal()
def _next_month(d): x = date.fromordinal(d); return date(x.year + (x.month == 12), x.month % 12 + 1, 1).toordinal()


# ---- data --------------------------------------------------------------------------
class Outcome:
    """Per-patient outcome series with O(log n) window queries on ordinal days, half-open [lo, hi)."""

    def __init__(self, kind, as_of):
        self.kind, self.as_of = kind, as_of
        self.days = defaultdict(list)       # count outcomes: sorted event days
        self.values = defaultdict(list)     # lab: sorted (day, value)
        self.months = defaultdict(dict)     # pmpm: month-start ordinal -> completed allowed dollars
        self.observable_from = {}           # pmpm: first enrolled day

    def finalize(self):
        for d in (self.days, self.values):
            for v in d.values(): v.sort()
        self._prefix = {pid: np.concatenate([[0.0], np.cumsum([x for _, x in v])]) for pid, v in self.values.items()}

    def count(self, pid, lo, hi):
        a = self.days.get(pid, ())
        return bisect_left(a, hi) - bisect_left(a, lo)

    def rate(self, pid, lo, hi):
        """Outcome in the window: events per 30 days, allowed $ per member-month, or mean lab value; None if unmeasurable."""
        hi = min(hi, self.as_of + 1)
        if hi <= lo: return None
        if self.kind in ('ed_visits', 'admissions'):
            return self.count(pid, lo, hi) * 30.0 / (hi - lo)
        if self.kind == 'pmpm':
            start = self.observable_from.get(pid)
            if start is None or lo < start: return None
            total, m = 0.0, _month_start(lo)
            while m < hi:
                nxt = _next_month(m)
                overlap = min(hi, nxt) - max(lo, m)
                if overlap > 0: total += self.months[pid].get(m, 0.0) * overlap / (nxt - m)
                m = nxt
            return total / ((hi - lo) / (365.25 / 12))
        v = self.values.get(pid)
        if not v: return None
        i, j = bisect_left(v, (lo, -math.inf)), bisect_left(v, (hi, -math.inf))
        if j <= i: return None
        pre = self._prefix[pid]
        return float((pre[j] - pre[i]) / (j - i))


def _load(db, p, as_of_day, keep):
    people = {pid: {'age_band': aggregate.age_band(age), 'sex': sex or 'unknown', 'payer': cov or 'unknown', 'practice': prac or 'unattributed'}
              for pid, age, sex, cov, prac in db.execute('SELECT patient_id, age_reported, sex_reported, coverage_type, pcp_practice_id FROM patient')
              if keep is None or pid in keep}
    if p.get('population_conditions'):
        for c in p['population_conditions']:
            have = {pid for (pid,) in db.execute('SELECT patient_id FROM concept_patient WHERE concept = ?', (c,))}
            people = {k: v for k, v in people.items() if k in have}
    chronic = defaultdict(int)
    for c, pid in db.execute('SELECT concept, patient_id FROM concept_patient WHERE kind = ?', ('condition',)):
        if c in aggregate.BURDEN_CONCEPTS: chronic[pid] += 1
    for pid in people: people[pid]['chronic_conditions'] = chronic[pid]
    ed, adm = Outcome('ed_visits', as_of_day), Outcome('admissions', as_of_day)
    for pid, d in db.execute("SELECT DISTINCT patient_id, substr(event_date,1,10) FROM encounter WHERE type_concept='emergency_visit' AND event_date IS NOT NULL"):
        if pid in people: ed.days[pid].append(_day(d))
    for pid, d in db.execute('SELECT patient_id, admit_date FROM stay WHERE admit_date IS NOT NULL'):
        if pid in people: adm.days[pid].append(_day(d))
    ed.finalize(); adm.finalize()
    if p['outcome'] == 'ed_visits': outcome = ed
    elif p['outcome'] == 'admissions': outcome = adm
    else:
        outcome = Outcome(p['outcome'], as_of_day)
        if p['outcome'] == 'lab':
            for pid, d, v in db.execute('SELECT patient_id, event_date, value_canonical FROM measurement WHERE concept = ? AND '
                                        'value_canonical IS NOT NULL AND event_date IS NOT NULL', (p['lab'],)):
                if pid in people: outcome.values[pid].append((_day(d), float(v)))
        else:
            from . import vbc
            as_of = _iso(as_of_day)
            claims = vbc._claims(db, '1900-01-01', as_of)
            completion, _ = vbc._completion(claims, as_of)
            for pid, month, allowed, *_ in claims:
                if pid in people:
                    m = _day(month + '-01')
                    outcome.months[pid][m] = outcome.months[pid].get(m, 0.0) + allowed / 100 * completion.get(month, 1.0)
            elig = vbc.eligibility(db, as_of, '2000-01', as_of)
            for pid, info in elig.items():
                if pid in people and info['months']: outcome.observable_from[pid] = _day(info['months'][0] + '-01')
        outcome.finalize()
    return people, outcome, ed, adm


def _exposure(db, p, people, as_of_day):
    """(first qualifying day per patient, patients excluded from the comparison pool, definition, counts)."""
    first, touched, notes = {}, set(), {}
    if p['program'] == 'medication_start':
        cls = p['drug_class']
        ev = ','.join('?' * len(MED_EVIDENCE))
        feed = db.execute(f'SELECT min(event_date) FROM medication WHERE evidence IN ({ev}) AND event_date IS NOT NULL', MED_EVIDENCE).fetchone()[0]
        washout_from = _day(feed) + p['pre_days'] if feed else None
        prevalent, undated = set(), set()
        for pid, d, evidence in db.execute('SELECT patient_id, event_date, evidence FROM medication WHERE drug_class = ?', (cls,)):
            if pid not in people: continue
            touched.add(pid)
            if evidence not in MED_EVIDENCE or not d:
                if not d: undated.add(pid)
                continue
            day = _day(d)
            if pid not in first or day < first[pid]: first[pid] = day
        for pid in list(first):
            if pid in undated: first.pop(pid); continue
            if washout_from is None or first[pid] < washout_from: prevalent.add(pid); first.pop(pid)
        notes = {'medication_feed_start': feed, 'new_user_washout_days': p['pre_days'],
                 'excluded_possible_prevalent_users': len(prevalent), 'excluded_undated_listing': len(undated & touched)}
        definition = (f'First dated {cls} record ({", ".join(MED_EVIDENCE)}) at least {p["pre_days"]} days after the medication feed '
                      f'starts (new-user design); patients with undated listings of {cls} are excluded.')
        stream = 'medication'
        records = sum(1 for _ in db.execute('SELECT 1 FROM medication WHERE drug_class = ?', (cls,)))
    else:
        stream, rule, definition = PROGRAMS[p['program']]
        records = 0
        for pid, d, payload in db.execute('SELECT patient_id, event_date, payload FROM record WHERE stream = ?', (stream,)):
            records += 1
            if pid not in people: continue
            touched.add(pid)
            if not d or not rule(json.loads(payload)): continue
            day = _day(d)
            if pid not in first or day < first[pid]: first[pid] = day
    first = {k: v for k, v in first.items() if v <= as_of_day}
    return first, touched, definition, {'stream': stream, 'records': records, 'patients_with_records': len(touched),
                                        'patients_qualifying': len(first), **notes}


# ---- matching ----------------------------------------------------------------------
def _covariates(pid, t0, p, outcome, ed, adm, people):
    lo = t0 - p['pre_days']
    pre = outcome.rate(pid, lo, t0)
    if pre is None: return None
    return (pre, ed.count(pid, lo, t0) + 2 * adm.count(pid, lo, t0), float(people[pid]['chronic_conditions']))


def _post(pid, t0, p, outcome):
    return outcome.rate(pid, t0 + 1, t0 + 1 + p['post_days'])


def _prognostic(treated, pool, p, outcome, ed, adm, people):
    """Baseline risk/utilization score: the comparison pool's expected post-window outcome given pre-period outcome,
    pre-period ED + 2 x admissions and chronic-condition count, fitted by least squares at the median exposed index date
    (prognostic score; fitted on unexposed patients only, so it never sees exposed outcomes). Returns (score fn, caliper, fit)."""
    t_ref = sorted(t[1] for t in treated)[len(treated) // 2] if treated else None
    X, y = [], []
    for c in sorted(pool):
        x = _covariates(c, t_ref, p, outcome, ed, adm, people) if t_ref else None
        v = _post(c, t_ref, p, outcome) if x is not None else None
        if v is not None: X.append([1.0, *x]); y.append(v)
    beta = None
    if len(y) >= 20 and float(np.std(y)) > 0:
        A = np.array(X); b = np.array(y)
        beta = np.linalg.solve(A.T @ A + 1e-6 * np.eye(A.shape[1]), A.T @ b)
    if beta is None:  # no outcome variation in the pool: fall back to the pre-period outcome itself
        score = lambda cov: float(cov[0])
    else:
        score = lambda cov: float(beta[0] + np.dot(beta[1:], cov))
    values = [score(x[1:]) for x in X] + [score(t[2]) for t in treated]
    sd = float(np.std(values)) if values else 0.0
    caliper = p['caliper'] * sd if sd > 0 else 0.0
    fit = {'kind': 'prognostic_least_squares' if beta is not None else 'pre_outcome_only', 'reference_index_date': _iso(t_ref) if t_ref else None,
           'pool_patients': len(y), 'coefficients': dict(zip(['intercept', *COVARIATES], [round(float(b), 6) for b in beta])) if beta is not None else None,
           'caliper_absolute': round(caliper, 6), 'caliper_sd': p['caliper']}
    return score, caliper, fit


def _match(treated, pool, p, outcome, ed, adm, people, rng, score, caliper):
    """Greedy nearest-neighbour matching on the baseline score, without replacement, inside exact age band/sex/payer strata.
    treated: [(pid, t0, cov, post)]; ties broken by the closest covariate vector, then patient id."""
    strata = defaultdict(list)
    for c in sorted(pool):  # deterministic strata order: seeded draws must not depend on string-hash order
        a = people[c]
        strata[(a['age_band'], a['sex'], a['payer'])].append(c)
    used, sets, unmatched = set(), [], 0
    order = list(range(len(treated))); rng.shuffle(order)
    for i in order:
        pid, t0, cov, post = treated[i]
        a = people[pid]
        s0, cands = score(cov), []
        for c in strata.get((a['age_band'], a['sex'], a['payer']), ()):
            if c in used: continue
            x = _covariates(c, t0, p, outcome, ed, adm, people)
            if x is None: continue
            dist = abs(score(x) - s0)
            if dist > caliper + 1e-12: continue
            y = _post(c, t0, p, outcome)
            if y is None: continue
            cands.append((dist, sum(abs(u - v) for u, v in zip(x, cov)), c, x, y))
        cands.sort(key=lambda z: (z[0], z[1], z[2]))
        chosen = cands[:p['controls_per_exposed']]
        if not chosen: unmatched += 1; continue
        used.update(z[2] for z in chosen)
        sets.append({'exposed': pid, 't0': t0, 'cov': cov, 'pre': cov[0], 'post': post,
                     'controls': [{'pid': c, 'cov': x, 'pre': x[0], 'post': y} for _, _, c, x, y in chosen]})
    sets.sort(key=lambda s: (s['t0'], s['exposed']))
    return sets, unmatched, used


def _did(sets, draws, rng_np):
    if not sets: return None
    d = np.array([(s['post'] - s['pre']) - np.mean([c['post'] - c['pre'] for c in s['controls']]) for s in sets])
    est = float(d.mean())
    boot = d[rng_np.integers(0, len(d), size=(draws, len(d)))].mean(axis=1)
    lo, hi = np.quantile(boot, [0.025, 0.975])
    return {'effect': est, 'ci95': [float(lo), float(hi)], 'se': float(boot.std(ddof=1)), 'sets': len(d)}


def _smd(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if not len(a) or not len(b): return None
    s = math.sqrt((a.var(ddof=1 if len(a) > 1 else 0) + b.var(ddof=1 if len(b) > 1 else 0)) / 2)
    return 0.0 if s == 0 and a.mean() == b.mean() else (float((a.mean() - b.mean()) / s) if s else math.copysign(math.inf, a.mean() - b.mean()))


# ---- diagnostics -------------------------------------------------------------------
def _pretrend(sets, p, outcome, draws, rng_np):
    half = p['pre_days'] // 2
    rows = []
    for s in sets:
        def change(pid):
            early = outcome.rate(pid, s['t0'] - p['pre_days'], s['t0'] - half)
            late = outcome.rate(pid, s['t0'] - half, s['t0'])
            return None if early is None or late is None else late - early
        e = change(s['exposed'])
        cs = [x for x in (change(c['pid']) for c in s['controls']) if x is not None]
        if e is not None and cs: rows.append(e - float(np.mean(cs)))
    if len(rows) < MIN_ESTIMATE:
        return {'status': 'not_assessable', 'sets': len(rows), 'detail': 'Too few matched sets with measurable outcomes in both pre-window halves.'}
    d = np.array(rows)
    if not np.any(d):
        return {'status': 'not_assessable', 'sets': len(rows), 'detail': 'No outcome change in either pre-window half for any matched set: nothing to test.'}
    boot = d[rng_np.integers(0, len(d), size=(draws, len(d)))].mean(axis=1)
    lo, hi = (float(x) for x in np.quantile(boot, [0.025, 0.975]))
    return {'status': 'failed' if lo > 0 or hi < 0 else 'passed', 'difference_in_pre_trend': float(d.mean()), 'ci95': [lo, hi], 'sets': len(rows),
            'detail': f'Late-minus-early pre-window change, exposed minus matched controls (halves of {p["pre_days"]} days).'}


def _event_study(sets, p, outcome):
    rows = []
    for k in range(-math.ceil(p['pre_days'] / 30), math.ceil(p['post_days'] / 30)):
        e, c = [], []
        for s in sets:
            lo = s['t0'] + 30 * k + (1 if k >= 0 else 0)
            hi = min(lo + 30, s['t0'] if k < 0 else s['t0'] + 1 + p['post_days'])
            lo = max(lo, s['t0'] - p['pre_days'])
            x = outcome.rate(s['exposed'], lo, hi)
            ys = [y for y in (outcome.rate(cc['pid'], lo, hi) for cc in s['controls']) if y is not None]
            if x is not None and ys: e.append(x); c.append(float(np.mean(ys)))
        if e: rows.append({'relative_30d_bin': k, 'sets': len(e), 'exposed': round(float(np.mean(e)), 4),
                           'comparison': round(float(np.mean(c)), 4), 'difference': round(float(np.mean(e) - np.mean(c)), 4)})
    return rows


def _feed_checks(db, p, outcome, sets, as_of_day):
    """Months where the outcome event vanishes (or spikes) while its reference stream keeps flowing, overlapping analysed windows."""
    if not sets: return []
    start = _month_start(min(s['t0'] for s in sets) - p['pre_days'])
    ref_stream = {'ed_visits': 'encounters', 'admissions': 'encounters', 'pmpm': 'encounters', 'lab': 'labs'}[p['outcome']]
    ref = dict(db.execute('SELECT substr(event_date,1,7), count(*) FROM record WHERE stream = ? AND event_date IS NOT NULL GROUP BY 1', (ref_stream,)))
    events = defaultdict(float)
    if p['outcome'] in ('ed_visits', 'admissions'):
        sql = ("SELECT substr(event_date,1,7), count(DISTINCT patient_id || event_date) FROM encounter WHERE type_concept='emergency_visit' GROUP BY 1"
               if p['outcome'] == 'ed_visits' else 'SELECT substr(admit_date,1,7), count(*) FROM stay GROUP BY 1')
        events.update({k: v for k, v in db.execute(sql) if k})
    elif p['outcome'] == 'lab':
        events.update({k: v for k, v in db.execute('SELECT substr(event_date,1,7), count(*) FROM measurement WHERE concept = ? AND event_date IS NOT NULL GROUP BY 1', (p['lab'],)) if k})
    else:
        for months in outcome.months.values():
            for m, v in months.items(): events[_iso(m)[:7]] += 1 if v else 0
    months, m = [], start
    while m <= as_of_day: months.append(_iso(m)[:7]); m = _next_month(m)
    nonzero = sorted(events[x] for x in months if events[x] > 0)
    med = float(np.median(nonzero)) if nonzero else 0.0
    ref_med = float(np.median([ref.get(x, 0) for x in months])) if months else 0.0
    zero = [x for x in months if events[x] == 0 and ref.get(x, 0) >= max(1, 0.5 * ref_med)]
    gaps, run = [], []
    for x in months:  # runs of silent months
        if x in zero: run.append(x); continue
        if run: gaps.append(run); run = []
    if run: gaps.append(run)
    gap_months = {x for g in gaps for x in g if (len(g) >= 2 and med >= 3) or med >= 8}
    spikes = {x for x in months if med and events[x] > 5 * med and events[x] > med + 20}
    out = []

    def overlapping(flag):
        hit = 0
        for s in sets:
            lo, hi = s['t0'] - p['pre_days'], min(s['t0'] + p['post_days'], as_of_day)
            m = _month_start(lo)
            while m <= hi:
                if _iso(m)[:7] in flag: hit += 1; break
                m = _next_month(m)
        return hit
    if gap_months:
        n = overlapping(gap_months)
        out.append({'kind': 'outcome_feed_gap', 'months': sorted(gap_months), 'sets_affected': n, 'share_affected': round(n / len(sets), 3),
                    'detail': f'{len(gap_months)} months record {ref_stream} volume but none of the outcome events ({min(gap_months)}..{max(gap_months)}); '
                              f'{n} of {len(sets)} matched sets have a pre or post window inside the gap.'})
    if spikes:
        n = overlapping(spikes)
        out.append({'kind': 'outcome_feed_spike', 'months': sorted(spikes), 'sets_affected': n, 'share_affected': round(n / len(sets), 3),
                    'detail': f'Outcome volume in {", ".join(sorted(spikes))} exceeds 5x the monthly median ({med:g}); {n} matched sets overlap it.'})
    return out


def _placebo(pool, treated, p, outcome, ed, adm, people, rng, rng_np, score, caliper):
    """In-space placebo: unexposed patients given exposed index dates, matched and estimated by the same pipeline."""
    candidates = sorted(pool)  # an independent analysis: the whole unexposed pool, regardless of main-analysis control use
    rng.shuffle(candidates)
    size = min(len(treated), len(candidates) // (p['controls_per_exposed'] + 1))
    fake = []
    for pid in candidates:
        if len(fake) >= size: break
        t0 = treated[rng.randrange(len(treated))][1]
        cov = _covariates(pid, t0, p, outcome, ed, adm, people)
        post = _post(pid, t0, p, outcome) if cov else None
        if cov is not None and post is not None: fake.append((pid, t0, cov, post))
    sets, _, _ = _match(fake, set(candidates) - {f[0] for f in fake}, p, outcome, ed, adm, people, rng, score, caliper)
    if len(sets) < MIN_ESTIMATE:
        return {'status': 'not_assessable', 'sets': len(sets), 'detail': 'Too few unexposed patients to form placebo matched sets.'}
    r = _did(sets, p['bootstrap'], rng_np)
    return {'status': 'nonzero' if r['ci95'][0] > 0 or r['ci95'][1] < 0 else 'consistent_with_zero', 'placebo_effect': r['effect'],
            'ci95': r['ci95'], 'sets': r['sets'], 'detail': 'Unexposed patients assigned exposed index dates; same matching and DiD. Expected ~0.'}


def _synthetic_control(exposed, pool, p, outcome, people, as_of_day):
    """Calendar-month convex donor weighting (practice-level unexposed series) when adoption is concentrated in time."""
    from scipy.optimize import minimize
    if len(exposed) < MIN_ESTIMATE: return {'status': 'not_feasible', 'reason': 'too few exposed patients'}
    days = sorted(exposed.values())
    q1, q3 = days[len(days) // 4], days[(3 * len(days)) // 4]
    if q3 - q1 > 92: return {'status': 'not_feasible', 'reason': f'staggered adoption (index-date IQR {q3 - q1} days > 92); patient-level DiD is the primary design'}
    first_month = _month_start(days[0]); median_month = _month_start(days[len(days) // 2])
    m, months = _month_start(days[0] - p['pre_days']), []
    while m <= as_of_day: months.append(m); m = _next_month(m)
    pre = [m for m in months if m < first_month]; post = [m for m in months if m >= median_month]
    if len(pre) < 3 or not post: return {'status': 'not_feasible', 'reason': 'needs >= 3 pre months and >= 1 post month'}

    def series(pids):
        out = []
        for m in months:
            vals = [v for v in (outcome.rate(x, m, _next_month(m)) for x in pids) if v is not None]
            out.append(float(np.mean(vals)) if vals else None)
        return out
    donors_by = defaultdict(list)
    for c in pool: donors_by[people[c]['practice']].append(c)
    donors = {k: series(v) for k, v in donors_by.items() if len(v) >= 10}
    donors = {k: v for k, v in donors.items() if all(x is not None for x in v)}
    treated = series(list(exposed))
    if len(donors) < 5 or any(x is None for x in treated):
        return {'status': 'not_feasible', 'reason': f'{len(donors)} complete practice donor series (need 5) or treated series has empty months'}
    names = sorted(donors)
    Y0 = np.array([donors[k] for k in names]).T
    ipre = [months.index(m) for m in pre]; ipost = [months.index(m) for m in post]

    def fit(y, X):
        n = X.shape[1]
        r = minimize(lambda w: float(np.sum((y[ipre] - X[ipre] @ w) ** 2)), np.full(n, 1 / n), method='SLSQP',
                     bounds=[(0, 1)] * n, constraints=[{'type': 'eq', 'fun': lambda w: w.sum() - 1}])
        w = np.clip(r.x, 0, None); w = w / w.sum()
        gap = y - X @ w
        pre_rmspe = float(np.sqrt(np.mean(gap[ipre] ** 2))); post_rmspe = float(np.sqrt(np.mean(gap[ipost] ** 2)))
        return w, gap, pre_rmspe, post_rmspe
    y = np.array(treated)
    w, gap, pre_r, post_r = fit(y, Y0)
    ratios = []
    for j in range(len(names)):
        others = [i for i in range(len(names)) if i != j]
        _, _, a, b = fit(Y0[:, j], Y0[:, others])
        ratios.append(b / a if a > 0 else math.inf)
    ratio = post_r / pre_r if pre_r > 0 else math.inf
    rank = 1 + sum(r >= ratio for r in ratios)
    return {'status': 'estimated', 'effect': float(np.mean(gap[ipost])), 'pre_rmspe': pre_r, 'post_rmspe': post_r,
            'placebo_p_value': round(rank / (len(names) + 1), 3), 'donors': len(names),
            'weights': {names[i]: round(float(w[i]), 3) for i in np.argsort(-w)[:5] if w[i] > 0.005},
            'pre_months': [_iso(m)[:7] for m in pre], 'post_months': [_iso(m)[:7] for m in post],
            'series': [{'month': _iso(m)[:7], 'exposed': round(y[i], 4), 'synthetic': round(float((Y0 @ w)[i]), 4)} for i, m in enumerate(months)]}


# ---- execute -----------------------------------------------------------------------
UNITS = {'ed_visits': 'ED visit-days per patient per 30 days', 'admissions': 'admissions per patient per 30 days',
         'pmpm': 'completed allowed $ per member-month', 'lab': 'mean value in window'}


def execute(path, patient_ids, params, as_of=None):
    p = validate(params)
    db = sqlite3.connect(f'file:{semantic_layer.ensure(path)}?mode=ro', uri=True)
    try:
        as_of = as_of or dict(db.execute('SELECT key, value FROM meta')).get('as_of')
        as_of_day = _day(as_of)
        keep = set(patient_ids) if patient_ids is not None else None
        people, outcome, ed, adm = _load(db, p, as_of_day, keep)
        first, touched, definition, exposure_counts = _exposure(db, p, people, as_of_day)
        rng, rng_np = random.Random(p['seed']), np.random.default_rng(p['seed'])
        if p.get('method', 'matched_did') != 'matched_did':
            return _execute_panel(p, people, outcome, first, touched, definition, exposure_counts, as_of_day, as_of)
        excluded = defaultdict(int)
        treated = []
        for pid, t0 in sorted(first.items()):
            if as_of_day - t0 < p['min_followup_days']: excluded['follow_up_shorter_than_minimum'] += 1; continue
            cov = _covariates(pid, t0, p, outcome, ed, adm, people)
            if cov is None: excluded['pre_window_outcome_unmeasurable'] += 1; continue
            post = _post(pid, t0, p, outcome)
            if post is None: excluded['post_window_outcome_unmeasurable'] += 1; continue
            treated.append((pid, t0, cov, post))
        pool = set(people) - touched
        score, caliper, score_fit = _prognostic(treated, pool, p, outcome, ed, adm, people)
        sets, unmatched, used = _match(treated, pool, p, outcome, ed, adm, people, rng, score, caliper)
        unit = (f"{p['lab']} " if p['outcome'] == 'lab' else '') + UNITS[p['outcome']]
        warnings, blocking = [], []
        # Balance: exposed vs pool before matching (pool measured at the median exposed index date), and vs matched controls.
        balance = []
        if treated:
            t_med = sorted(t[1] for t in treated)[len(treated) // 2]
            pool_cov = [x for x in (_covariates(c, t_med, p, outcome, ed, adm, people) for c in sorted(pool)) if x is not None]
            exp_cov = [t[2] for t in treated]
            m_exp = [s['cov'] for s in sets]; m_ctl = [c['cov'] for s in sets for c in s['controls']]
            def col(rows, j): return [r[j] for r in rows]
            for j, name in enumerate(COVARIATES):
                balance.append({'covariate': name, 'mean_exposed': round(float(np.mean(col(exp_cov, j))), 4),
                                'mean_unmatched_pool': round(float(np.mean(col(pool_cov, j))), 4) if pool_cov else None,
                                'mean_matched_controls': round(float(np.mean(col(m_ctl, j))), 4) if m_ctl else None,
                                'smd_before': _r(_smd(col(exp_cov, j), col(pool_cov, j))) if pool_cov else None,
                                'smd_after': _r(_smd(col(m_exp, j), col(m_ctl, j))) if m_ctl else None})
            # Age band, sex and payer are exact-matched: their SMD after matching is 0 by construction.
        n = len(sets)
        did = _did(sets, p['bootstrap'], rng_np) if n >= MIN_ESTIMATE else None
        if exposure_counts['patients_qualifying'] == 0:
            blocking.append(f"No patient has a qualifying {exposure_counts['stream']} record: {definition}")
        if n < MIN_ESTIMATE:
            blocking.append(f'Too few matched exposed patients ({n}; need >= {MIN_ESTIMATE}): no effect is estimated.')
        elif n < MIN_RELIABLE:
            warnings.append(f'Only {n} matched exposed patients (< {MIN_RELIABLE}): the interval is wide and unstable.')
        if treated and unmatched / len(treated) > 0.2:
            key = lambda x: (people[x]['age_band'], people[x]['sex'], people[x]['payer'])
            ex_n, pool_n = defaultdict(int), defaultdict(int)
            for t in treated: ex_n[key(t[0])] += 1
            for c in pool: pool_n[key(c)] += 1
            thin = sorted(((k, v, pool_n[k]) for k, v in ex_n.items() if pool_n[k] < v * p['controls_per_exposed']), key=lambda z: -z[1])
            warnings.append(f'{unmatched} of {len(treated)} eligible exposed patients had no comparison patient within the caliper; '
                            'the estimate describes the matched subset only.'
                            + (' Limited overlap (positivity): ' + '; '.join(f'{"/".join(k)} {v} exposed vs {c} unexposed' for k, v, c in thin[:4])
                               + ' - most such patients received the program, so few comparable unexposed patients exist.' if thin else ''))
        bad = [b['covariate'] for b in balance if b['smd_after'] is not None and abs(b['smd_after']) > SMD_LIMIT]
        if bad: warnings.append(f'Residual imbalance after matching (|SMD| > {SMD_LIMIT}): ' + ', '.join(bad) + '.')
        pretrend = _pretrend(sets, p, outcome, p['bootstrap'], rng_np) if n >= MIN_ESTIMATE else {'status': 'not_assessable', 'sets': n}
        if pretrend['status'] == 'failed':
            blocking.append('Parallel-trends check failed: exposed and comparison outcomes were already diverging before the index date '
                            f"(pre-trend difference {pretrend['difference_in_pre_trend']:+.4g}, 95% CI {pretrend['ci95'][0]:+.4g}..{pretrend['ci95'][1]:+.4g}).")
        elif pretrend['status'] == 'not_assessable' and n >= MIN_ESTIMATE:
            warnings.append('Parallel trends could not be assessed: ' + pretrend.get('detail', 'too few matched sets.'))
        placebo = _placebo(pool, treated, p, outcome, ed, adm, people, rng, rng_np, score, caliper) if n >= MIN_ESTIMATE else {'status': 'not_assessable'}
        if placebo['status'] == 'not_assessable' and n >= MIN_ESTIMATE:
            warnings.append('Placebo test could not be run (too few unexposed patients to form placebo matched sets).')
        if placebo['status'] == 'nonzero':
            warnings.append(f"Placebo test is not ~0 ({placebo['placebo_effect']:+.4g}, 95% CI {placebo['ci95'][0]:+.4g}..{placebo['ci95'][1]:+.4g}): "
                            'the pipeline produces an effect where none exists; treat the main estimate as biased.')
        feeds = _feed_checks(db, p, outcome, sets, as_of_day)
        for f in feeds:
            if f['kind'] == 'outcome_feed_gap' and f['share_affected'] >= 0.2: blocking.append('Outcome feed gap inside analysed windows: ' + f['detail'])
            elif f['sets_affected']: warnings.append(('Outcome feed gap: ' if f['kind'] == 'outcome_feed_gap' else 'Outcome feed spike: ') + f['detail'])
        follow = [min(p['post_days'], as_of_day - s['t0']) for s in sets]
        if follow and float(np.median(follow)) < p['post_days']:
            warnings.append(f'Follow-up truncated by the snapshot: median {float(np.median(follow)):.0f} of {p["post_days"]} requested post-window days '
                            '(rates use observed days only; later effects are unobserved).')
        if sets and p['outcome'] in ('ed_visits', 'admissions'):
            ev_e = sum(outcome.count(s['exposed'], s['t0'] - p['pre_days'], s['t0'] + p['post_days'] + 1) for s in sets)
            ev_c = sum(outcome.count(c['pid'], s['t0'] - p['pre_days'], s['t0'] + p['post_days'] + 1) for s in sets for c in s['controls'])
            if ev_e + ev_c < 10: blocking.append(f'Only {ev_e + ev_c} outcome events in all analysed windows: the outcome is too rare to estimate a change.')
        if p['outcome'] == 'pmpm':
            warnings.append('Recent months are completion-adjusted (chain-ladder IBNR); post-window cost near the snapshot is partly estimated.')
        if p['program'] == 'transitions_of_care' or (sets and p['outcome'] in ('ed_visits', 'admissions') and np.mean([s['pre'] for s in sets]) >
                                                      2 * max(np.mean([c['pre'] for s in sets for c in s['controls']]), 1e-9)):
            warnings.append('Exposure tends to follow acute events; regression to the mean can mimic a reduction even after matching on pre-period use.')
        sc = _synthetic_control(dict((t[0], t[1]) for t in treated), pool, p, outcome, people, as_of_day)
        gap_months = {m for f in feeds if f['kind'] == 'outcome_feed_gap' for m in f['months']}
        if sc['status'] == 'estimated' and gap_months & set(sc['pre_months'] + sc['post_months']):
            sc.update(status='not_reliable', reason='outcome feed gap inside the fitted months: ' + ', '.join(sorted(gap_months & set(sc['pre_months'] + sc['post_months']))))
        if sc['status'] == 'estimated' and did and (did['ci95'][0] > 0 or did['ci95'][1] < 0) and \
                (sc['placebo_p_value'] > 0.1 or math.copysign(1, sc['effect']) != math.copysign(1, did['effect'])):
            warnings.append(f"Methods disagree: the matched DiD interval excludes zero ({did['effect']:+.4g}) but the practice-level synthetic control "
                            f"estimates {sc['effect']:+.4g} (placebo p = {sc['placebo_p_value']}); the patient-level result is not corroborated.")
        if p['program'] == 'medication_start' and exposure_counts.get('excluded_possible_prevalent_users'):
            warnings.append(f"{exposure_counts['excluded_possible_prevalent_users']} patients with {p['drug_class']} records inside the washout were "
                            'excluded as possible prevalent users (start date not identifiable).')
        if did and not blocking:
            inference = 'limited' if warnings else 'supported_under_assumptions'
        else:
            inference = 'not_supported'
        summary = {'pre_exposed': _mean([s['pre'] for s in sets]), 'post_exposed': _mean([s['post'] for s in sets]),
                   'pre_comparison': _mean([float(np.mean([c['pre'] for c in s['controls']])) for s in sets]),
                   'post_comparison': _mean([float(np.mean([c['post'] for c in s['controls']])) for s in sets])}
        if did:
            did = {k: (_r(v) if not isinstance(v, list) else [_r(x) for x in v]) for k, v in did.items()}
            base = summary['post_exposed'] - did['effect'] if summary['post_exposed'] is not None else None
            did['relative_effect'] = _r(did['effect'] / base) if base and base > 0 else None  # vs the counterfactual post level
        label = p['drug_class'] + ' start' if p['program'] == 'medication_start' else p['program'].replace('_', ' ')
        outcome_label = f"{p['lab']} (lab)" if p['outcome'] == 'lab' else p['outcome'].replace('_', ' ')
        if did and inference != 'not_supported':
            reading = (f"Estimated effect of {label} on {outcome_label} under stated assumptions: {did['effect']:+.4g} {unit} "
                       f"(95% CI {did['ci95'][0]:+.4g} to {did['ci95'][1]:+.4g}; {n} exposed vs {sum(len(s['controls']) for s in sets)} matched comparison patients)"
                       + ('; the interval includes no change.' if did['ci95'][0] <= 0 <= did['ci95'][1] else '.')
                       + (f' {len(warnings)} warning(s) limit this estimate.' if warnings else '') + ' Observational design; not an identified causal effect.')
        elif did:
            reading = (f"The received data cannot support an effect estimate for {label} on {outcome_label}: {blocking[0]} "
                       f"(the unreliable matched DiD would be {did['effect']:+.4g} {unit}, 95% CI {did['ci95'][0]:+.4g} to {did['ci95'][1]:+.4g}).")
        else:
            reading = f"The received data cannot support an effect estimate for {label} on {outcome_label}: {blocking[0] if blocking else 'no matched sets'}"
        rows = [{'analysis': 'matched_difference_in_differences', 'estimate': did['effect'] if did else None, 'ci95': did['ci95'] if did else None,
                 'unit': unit, 'exposed': n, 'comparison': sum(len(s['controls']) for s in sets), **summary, 'status': inference},
                {'analysis': 'parallel_trends_pre_period', 'estimate': _r(pretrend.get('difference_in_pre_trend')), 'ci95': _rl(pretrend.get('ci95')),
                 'unit': unit, 'status': pretrend['status']},
                {'analysis': 'placebo_unexposed', 'estimate': _r(placebo.get('placebo_effect')), 'ci95': _rl(placebo.get('ci95')),
                 'unit': unit, 'status': placebo['status']},
                {'analysis': 'synthetic_control_practices', 'estimate': _r(sc.get('effect')), 'ci95': None, 'unit': unit, 'status': sc['status'],
                 **({'placebo_p_value': sc['placebo_p_value'], 'pre_rmspe': _r(sc['pre_rmspe'])} if sc['status'] == 'estimated' else {'reason': sc.get('reason')})}]
    finally:
        db.close()
    return {'schema': VERSION, 'parameters': p, 'as_of': as_of,
            'exposure': {'program': p['program'], 'definition': definition, **exposure_counts,
                         'index_dates': [_iso(min(first.values())), _iso(max(first.values()))] if first else None},
            'outcome': {'measure': p['outcome'], 'unit': unit, 'pre_days': p['pre_days'], 'post_days': p['post_days']},
            'sample': {'qualifying': len(first), 'eligible_exposed': len(treated), 'excluded': dict(excluded), 'matched_exposed': n,
                       'unmatched_exposed': unmatched, 'comparison_patients': sum(len(s['controls']) for s in sets),
                       'comparison_pool': len(pool), 'excluded_from_pool_program_records': len(touched - set(first))},
            'did': did, 'baseline_score': score_fit, 'balance': balance, 'pre_trend': pretrend, 'placebo': placebo, 'event_study': _event_study(sets, p, outcome),
            'synthetic_control': sc, 'feed_checks': feeds, 'warnings': blocking + warnings, 'blocking_warnings': blocking,
            'assumptions': ASSUMPTIONS, 'inference': inference, 'reading': reading, 'rows': rows,
            'method': (f"Exposure: {definition}. Index date = first qualifying record. Matched difference-in-differences: exact match on age band, "
                       f"sex and payer, then nearest neighbour (1:{p['controls_per_exposed']}, without replacement, caliper {p['caliper']} SD) on a "
                       f"baseline risk/utilization score ({score_fit['kind']}: pool-fitted expected outcome from pre-period outcome, pre-period "
                       f"ED + 2 x admissions and chronic-condition count over {p['pre_days']} days before the index date); controls inherit the index date. Outcome {outcome_label} in [index-{p['pre_days']}d, index) vs "
                       f"(index, index+{p['post_days']}d], truncated at the snapshot. Effect = mean over matched sets of exposed change minus "
                       f"comparison change; 95% CI from {p['bootstrap']} cluster-bootstrap draws over matched sets. Diagnostics: SMD balance, "
                       'pre-period parallel-trends test, in-space placebo, event-study bins, outcome feed gap/spike checks; synthetic control '
                       'over practice donors when adoption is concentrated. Estimated effect under stated assumptions, not an identified causal effect.')}


def _r(v): return None if v is None else (round(v, 4) if isinstance(v, float) and math.isfinite(v) else v)
def _rl(v): return None if v is None else [_r(x) for x in v]
def _mean(v): return _r(float(np.mean(v))) if v else None


# ======================================================================================
# Panel causal methods: synthetic difference-in-differences and Callaway-Sant'Anna staggered DiD.
# The cores take plain arrays (units x periods) so they can be validated on simulated panels;
# _execute_panel builds the panel from the semantic layer (calendar months) the way the other methods do.
# ======================================================================================
Z95 = 1.959963984540054


def _simplex_ls(A, b, reg):
    """min_{c, w in simplex} ||b - c - A w||^2 + reg ||w||^2 (c free, not penalised). Returns (w, c)."""
    from scipy.optimize import minimize
    n = A.shape[1]
    if n == 1: return np.ones(1), float(b.mean() - A.mean())
    Ac, bc = A - A.mean(0), b - b.mean()
    Q = Ac.T @ Ac + reg * np.eye(n); q = Ac.T @ bc
    scale = max(float(np.trace(Q)) / n, 1e-12)
    Qs, qs = Q / scale, q / scale
    r = minimize(lambda w: float(w @ Qs @ w - 2 * qs @ w), np.full(n, 1.0 / n), jac=lambda w: 2 * (Qs @ w - qs), method='SLSQP',
                 bounds=[(0, 1)] * n, constraints=[{'type': 'eq', 'fun': lambda w: w.sum() - 1, 'jac': lambda w: np.ones(n)}],
                 options={'maxiter': 500, 'ftol': 1e-13})
    w = np.clip(r.x, 0, None); w = w / w.sum()
    return w, float(b.mean() - A.mean(0) @ w)


def _sdid_weights(Yco, ytr, T0, n_tr):
    """Unit weights omega (ridge-regularised, intercept-shifted) and time weights lambda, both on the simplex (Arkhangelsky et al. 2021)."""
    N0, T = Yco.shape
    diffs = np.diff(Yco[:, :T0], axis=1)
    sigma = float(np.std(diffs, ddof=1)) if diffs.size > 1 else 0.0
    zeta = (n_tr * (T - T0)) ** 0.25 * sigma
    omega, omega0 = _simplex_ls(Yco[:, :T0].T, ytr[:T0], zeta ** 2 * T0)
    lam, lam0 = _simplex_ls(Yco[:, :T0], Yco[:, T0:].mean(1), (1e-6 * sigma) ** 2 * N0)
    return omega, omega0, lam, lam0, sigma, zeta


def _sdid_tau(Yco, ytr, T0, omega, lam):
    """Weighted double difference; identical to the omega x lambda-weighted two-way fixed-effects coefficient on the treated x post indicator."""
    tr = ytr[T0:].mean() - lam @ ytr[:T0]
    co = Yco[:, T0:].mean(1) - Yco[:, :T0] @ lam
    return float(tr - omega @ co)


def sdid(Y, treated, T0, draws=SDID_PLACEBO_REPS, seed=7, names=None, labels=None):
    """Synthetic difference-in-differences. Y: units x periods; treated: boolean mask over units; periods 0..T0-1 are pre, T0.. post.
    Variance: placebo (pseudo-treated drawn from controls; weights re-estimated) when one unit is treated, jackknife over units otherwise."""
    Y = np.asarray(Y, float); treated = np.asarray(treated, bool)
    n_tr, N0, T = int(treated.sum()), int((~treated).sum()), Y.shape[1]
    if n_tr < 1 or N0 < 2 or T0 < 2 or T - T0 < 1: raise ValueError('sdid needs >= 1 treated unit, >= 2 controls, >= 2 pre and >= 1 post periods')
    Yco, ytr = Y[~treated], Y[treated].mean(0)
    omega, omega0, lam, lam0, sigma, zeta = _sdid_weights(Yco, ytr, T0, n_tr)
    tau = _sdid_tau(Yco, ytr, T0, omega, lam)
    did = float(ytr[T0:].mean() - ytr[:T0].mean() - (Yco[:, T0:].mean() - Yco[:, :T0].mean()))
    # variance
    taus, se_method = [], None
    if n_tr == 1:
        if N0 < 3: raise ValueError('placebo variance needs >= 3 control units')
        rng = np.random.default_rng(seed)
        for _ in range(draws):
            perm = rng.permutation(N0); pt, pc = perm[:1], perm[1:]
            if len(pc) < 2: continue
            yp = Yco[pt].mean(0); Yc = Yco[pc]
            o, _, l, _, _, _ = _sdid_weights(Yc, yp, T0, 1)
            taus.append(_sdid_tau(Yc, yp, T0, o, l))
        se_method = 'placebo'
        se = float(np.std(taus, ddof=1)) if len(taus) > 1 else float('nan')
    else:
        idx_tr, idx_co = np.flatnonzero(treated), np.flatnonzero(~treated)
        for i in range(len(Y)):
            keep_tr = [j for j in idx_tr if j != i]; keep_co = [k for k, j in enumerate(idx_co) if j != i]
            if not keep_tr or len(keep_co) < 1: continue
            w = omega[keep_co]; w = w / w.sum() if w.sum() > 0 else np.full(len(keep_co), 1 / len(keep_co))
            taus.append(_sdid_tau(Yco[keep_co], Y[keep_tr].mean(0), T0, w, lam))
        se_method = 'jackknife'
        n = len(taus)
        se = float(math.sqrt((n - 1) / n * np.sum((np.array(taus) - np.mean(taus)) ** 2))) if n > 1 else float('nan')
    pre_fit = ytr[:T0] - (omega0 + Yco[:, :T0].T @ omega)
    plain = (ytr[:T0] - ytr[:T0].mean()) - (Yco[:, :T0].mean(0) - Yco[:, :T0].mean())
    names = list(names) if names is not None else [str(i) for i in range(N0)]
    labels = list(labels) if labels is not None else [str(t) for t in range(T)]
    ci = [tau - Z95 * se, tau + Z95 * se] if math.isfinite(se) else None
    return {'effect': tau, 'se': se, 'ci95': ci, 'se_method': se_method, 'variance_draws': len(taus), 'did_unweighted': did,
            'treated_units': n_tr, 'control_units': N0, 'pre_periods': T0, 'post_periods': T - T0,
            'unit_weights': {names[i]: float(omega[i]) for i in np.argsort(-omega)[:5] if omega[i] > 0.005},
            'unit_weights_nonzero': int((omega > 0.005).sum()), 'unit_weight_intercept': omega0,
            'time_weights': {labels[t]: float(lam[t]) for t in np.argsort(-lam)[:5] if lam[t] > 0.005},
            'pre_fit_rmse': float(np.sqrt(np.mean(pre_fit ** 2))), 'pre_fit_rmse_unweighted_did': float(np.sqrt(np.mean(plain ** 2))),
            'treated_pre_sd': float(np.std(ytr[:T0], ddof=1)), 'sigma_hat': sigma, 'zeta': zeta,
            'unit_weight_vector': omega, 'time_weight_vector': lam}


# ---- Callaway & Sant'Anna ----------------------------------------------------------
def _logit(X, d, iters=60):
    k = X.shape[1]; b = np.zeros(k)
    for _ in range(iters):
        eta = np.clip(X @ b, -30, 30); pi = 1 / (1 + np.exp(-eta))
        W = pi * (1 - pi)
        H = (X.T * W) @ X + 1e-8 * np.eye(k)
        step = np.linalg.solve(H, X.T @ (d - pi) - 1e-8 * b)
        b = b + step
        if np.max(np.abs(step)) < 1e-9: break
    pi = 1 / (1 + np.exp(-np.clip(X @ b, -30, 30)))
    return b, pi


def _prep_X(X, mask):
    """Add intercept, standardise non-constant columns using the cell's subsample, drop constants."""
    sub = X[mask]; mu, sd = sub.mean(0), sub.std(0)
    keep = sd > 1e-12
    Z = (X[:, keep] - mu[keep]) / sd[keep]
    return np.hstack([np.ones((len(X), 1)), Z])


def _cs_cell(dy, D, X, estimator):
    """2x2 cell estimator. dy: outcome change over the cell (m,), D: bool treated, X: (m,k) incl. intercept or None.
    Returns (att, psi) with att_hat - att ~ mean(psi); psi includes the nuisance-estimation term for outcome regression and IPW;
    for doubly robust the (Neyman-orthogonal) plug-in influence function is used."""
    m = len(dy); Df = D.astype(float); p = Df.mean(); C = 1 - Df
    if X is None or X.shape[1] <= 1:
        a, b = dy[D].mean(), dy[~D].mean()
        return float(a - b), Df * (dy - a) / p - C * (dy - b) / (1 - p), {}
    info = {}
    beta = None
    if estimator in ('outcome_regression', 'doubly_robust'):
        Xc = X[~D]
        beta = np.linalg.lstsq(Xc, dy[~D], rcond=None)[0]
        e = dy - X @ beta
    if estimator in ('ipw', 'doubly_robust'):
        g, pi = _logit(X, Df)
        pi = np.clip(pi, 1e-4, 1 - 1e-4)
        info['max_pscore_control'] = float(pi[~D].max()); info['max_pscore'] = float(pi.max())
        w = pi / (1 - pi); wc = C * w; Ew = wc.sum() / m
    if estimator == 'outcome_regression':
        att = float(e[D].mean())
        G = np.linalg.pinv(Xc.T @ Xc)
        corr = m * C * ((X[D].mean(0) @ G) @ X.T) * e
        return att, Df / p * (e - att) - corr, info
    if estimator == 'ipw':
        a = dy[D].mean(); b = (wc * dy).sum() / wc.sum(); att = float(a - b)
        M = ((wc * (dy - b)) @ X) / m / Ew
        H = (X.T * (pi * (1 - pi))) @ X / m
        corr = ((M @ np.linalg.pinv(H)) @ X.T) * (Df - pi)
        return att, Df * (dy - a) / p - wc * (dy - b) / Ew - corr, info
    att = float((Df * e).mean() / p - (wc * e).mean() / Ew)
    return att, (Df / p - wc / Ew) * e - att * Df / p, info


def callaway_santanna(Y, G, X=None, control_group='never_treated', estimator='doubly_robust', draws=1000, seed=7,
                      min_cohort=MIN_COHORT, max_pre=None, max_post=None, labels=None):
    """Callaway & Sant'Anna (2021) group-time ATTs. Y: units x periods (balanced); G: first treated period index per unit (np.inf = never);
    X: unit covariates (time-invariant) or None. Base period for every (g, t) is g-1. Inference from influence functions;
    uniform bands for the event-study aggregation by multiplier bootstrap (Mammen weights, sup-t)."""
    Y = np.asarray(Y, float); G = np.asarray(G, float); n, T = Y.shape
    X = None if X is None or np.asarray(X).size == 0 else np.asarray(X, float).reshape(n, -1)
    labels = list(labels) if labels is not None else [str(t) for t in range(T)]
    never = np.isinf(G)
    if control_group == 'never_treated' and never.sum() < 2:
        raise ValueError('never_treated controls need >= 2 never-treated units; use control_group not_yet_treated')
    dropped = {}
    cohorts = []
    for g in sorted(set(G[~never])):
        cnt = int((G == g).sum())
        if g < 1: dropped[labels[int(g)]] = 'treated in the first period: no pre-period'; continue
        if g > T - 1: continue
        if cnt < min_cohort: dropped[labels[int(g)]] = f'cohort of {cnt} (< {min_cohort})'; continue
        cohorts.append(int(g))
    cells, psis, infos = [], [], []
    for g in cohorts:
        for t in range(T):
            e = t - g
            if t == g - 1 or (max_pre is not None and e < -max_pre) or (max_post is not None and e > max_post): continue
            ctrl = never | ((G > max(t, g)) if control_group == 'not_yet_treated' else False)
            D_all = G == g
            mask = D_all | ctrl
            nt, nc = int(D_all.sum()), int((ctrl & ~D_all).sum())
            if nt < 2 or nc < 2: continue
            Xm = _prep_X(X, mask)[mask] if X is not None else None
            if Xm is not None and nc <= Xm.shape[1] + 1: continue
            dy = Y[mask, t] - Y[mask, g - 1]
            att, psi, info = _cs_cell(dy, D_all[mask], Xm, estimator)
            full = np.zeros(n); full[mask] = psi * (n / mask.sum())
            se = float(np.sqrt(np.sum(full ** 2)) / n)
            cells.append({'group': g, 'time': t, 'event_time': e, 'att': att, 'se': se, 'n_treated': nt, 'n_control': nc})
            psis.append(full); infos.append(info)
    if not cells: raise ValueError('no estimable group-time cells (cohorts too small or no valid controls)')
    Psi = np.array(psis)
    for c in cells:
        c['ci95'] = [c['att'] - Z95 * c['se'], c['att'] + Z95 * c['se']]
        c['group_label'], c['time_label'] = labels[c['group']], labels[c['time']]
    att = np.array([c['att'] for c in cells]); ng = np.array([float((G == c['group']).sum()) for c in cells])
    ev = np.array([c['event_time'] for c in cells])

    def agg(w):
        th = float(w @ att); psi = w @ Psi
        se = float(np.sqrt(np.sum(psi ** 2)) / n)
        return th, se, psi
    out = {'cells': len(cells), 'cohorts_used': [labels[g] for g in cohorts], 'cohorts_dropped': dropped, 'units': n,
           'never_treated_units': int(never.sum()), 'control_group': control_group, 'estimator': estimator if X is not None else 'unconditional',
           'max_pscore': max([i['max_pscore'] for i in infos if 'max_pscore' in i], default=None)}
    post = ev >= 0
    if post.any():
        w = np.where(post, ng, 0.0); w = w / w.sum()
        th, se, _ = agg(w)
        out['simple'] = {'att': th, 'se': se, 'ci95': [th - Z95 * se, th + Z95 * se], 'cells': int(post.sum())}
    dyn, dpsi = [], []
    for e in sorted(set(ev)):
        sel = ev == e; w = np.where(sel, ng, 0.0); w = w / w.sum()
        th, se, psi = agg(w)
        dyn.append({'event_time': int(e), 'att': th, 'se': se, 'ci95': [th - Z95 * se, th + Z95 * se], 'cells': int(sel.sum())}); dpsi.append(psi)
    rng = np.random.default_rng(seed)
    pm = (math.sqrt(5) + 1) / (2 * math.sqrt(5))
    V = np.where(rng.random((draws, n)) < pm, -(math.sqrt(5) - 1) / 2, (math.sqrt(5) + 1) / 2)
    P = np.array(dpsi); ses = np.array([d['se'] for d in dyn])
    ok = ses > 0
    crit = Z95
    if ok.any():
        draw = (V @ P[ok].T) / n
        crit = max(float(np.quantile(np.max(np.abs(draw) / ses[ok], axis=1), 0.95)), Z95)
    for d in dyn: d['uniform_ci95'] = [d['att'] - crit * d['se'], d['att'] + crit * d['se']]
    out['dynamic'], out['uniform_critical_value'] = dyn, float(crit)
    pre = [d for d in dyn if d['event_time'] < 0 and d['se'] > 0]
    if pre:
        bad = [d['event_time'] for d in pre if not d['uniform_ci95'][0] <= 0 <= d['uniform_ci95'][1]]
        out['pretrend'] = {'status': 'failed' if bad else 'passed', 'event_times_outside_band': bad, 'pre_event_times': len(pre),
                           'detail': 'Pre-treatment ATT(g,t) aggregated by event time; uniform (sup-t) 95% band should cover zero.'}
    else:
        out['pretrend'] = {'status': 'not_assessable', 'detail': 'no pre-treatment periods beyond the base period.'}
    byg = []
    for g in cohorts:
        sel = (np.array([c['group'] for c in cells]) == g) & post
        if not sel.any(): continue
        w = np.where(sel, 1.0, 0.0); w = w / w.sum()
        th, se, _ = agg(w)
        byg.append({'group': g, 'group_label': labels[g], 'att': th, 'se': se, 'ci95': [th - Z95 * se, th + Z95 * se],
                    'units': int((G == g).sum()), 'post_periods': int(sel.sum())})
    out['by_cohort'], out['group_time'] = byg, cells
    return out


# ---- panel construction + execute path ----------------------------------------------
def _month_rates(pids, months, outcome):
    M = np.full((len(pids), len(months)), np.nan)
    for i, pid in enumerate(pids):
        for j, m in enumerate(months):
            v = outcome.rate(pid, m, _next_month(m))
            if v is not None: M[i, j] = v
    return M


def _covariate_matrix(pids, people, names):
    cols = []
    for nm in names:
        if nm == 'chronic_conditions': cols.append([float(people[x]['chronic_conditions']) for x in pids])
        else:
            key = {'age_band': 'age_band', 'sex': 'sex', 'payer': 'payer'}[nm]
            vals = sorted({people[x][key] for x in people})
            for v in vals[1:]: cols.append([1.0 if people[x][key] == v else 0.0 for x in pids])
    return np.array(cols, float).T if cols else None


def _panel_shell(p, first, as_of_day, definition, exposure_counts, outcome_label, unit):
    return {'schema': VERSION, 'parameters': p, 'exposure': {'program': p['program'], 'definition': definition, **exposure_counts,
            'index_dates': [_iso(min(first.values())), _iso(max(first.values()))] if first else None},
            'outcome': {'measure': p['outcome'], 'unit': unit, 'pre_days': p['pre_days'], 'post_days': p['post_days']}}


def _execute_panel(p, people, outcome, first, touched, definition, exposure_counts, as_of_day, as_of):
    unit = (f"{p['lab']} " if p['outcome'] == 'lab' else '') + UNITS[p['outcome']]
    label = p['drug_class'] + ' start' if p['program'] == 'medication_start' else p['program'].replace('_', ' ')
    outcome_label = f"{p['lab']} (lab)" if p['outcome'] == 'lab' else p['outcome'].replace('_', ' ')
    excluded = defaultdict(int)
    exposed = {}
    for pid, t0 in sorted(first.items()):
        if as_of_day - t0 < p['min_followup_days']: excluded['follow_up_shorter_than_minimum'] += 1; continue
        exposed[pid] = t0
    pool = sorted(set(people) - touched)
    warnings, blocking = [], []
    method, reason = p['method'], None
    days = sorted(exposed.values())
    if method == 'auto':
        if len(days) >= 4:
            iqr = days[(3 * len(days)) // 4] - days[len(days) // 4]
            if iqr > 92: method, reason = 'callaway_santanna', f'staggered adoption (index-date IQR {iqr} days > 92)'
            else: method, reason = 'sdid', f'adoption concentrated in time (index-date IQR {iqr} days <= 92)'
        else:
            method, reason = 'sdid', 'too few exposed patients to see staggering'
    out = _panel_shell(p, first, as_of_day, definition, exposure_counts, outcome_label, unit)
    out.update(as_of=as_of, estimation_method=method, method_selection=reason or 'requested',
               sample={'qualifying': len(first), 'eligible_exposed': len(exposed), 'excluded': dict(excluded), 'comparison_pool': len(pool),
                       'excluded_from_pool_program_records': len(touched - set(first))})
    months = []
    if days:
        m = _month_start(days[0] - p['pre_days'])
        while m <= as_of_day: months.append(m); m = _next_month(m)
    res = None
    if len(exposed) < MIN_ESTIMATE:
        blocking.append(f'Too few exposed patients ({len(exposed)}; need >= {MIN_ESTIMATE}): no effect is estimated.')
    elif method == 'sdid':
        res = _panel_sdid(p, people, outcome, exposed, pool, months, blocking, warnings)
        out['sdid'] = res
    else:
        res = _panel_cs(p, people, outcome, exposed, pool, months, blocking, warnings, as_of_day)
        out['callaway_santanna'] = res
    ok = bool(res and res.get('status') == 'estimated')
    inference = ('limited' if warnings else 'supported_under_assumptions') if ok and not blocking else 'not_supported'
    rows, name = [], {'sdid': 'synthetic_difference_in_differences', 'callaway_santanna': 'callaway_santanna_staggered_did'}[method]
    if ok and method == 'sdid':
        e = res['effect']; rows.append({'analysis': name, 'estimate': _r(e), 'ci95': _rl(res['ci95']), 'se': _r(res['se']), 'unit': unit,
                                        'exposed': res['treated_patients'], 'comparison': res['control_units'], 'status': inference})
        reading = (f"Estimated effect of {label} on {outcome_label} under stated assumptions (synthetic difference-in-differences): "
                   f"{_r(e):+.4g} {unit} (95% CI {_r(res['ci95'][0]):+.4g} to {_r(res['ci95'][1]):+.4g}; {res['treated_patients']} exposed patients vs "
                   f"{res['control_units']} unexposed practice series)" + ('; the interval includes no change.' if res['ci95'][0] <= 0 <= res['ci95'][1] else '.')
                   + (f' {len(warnings)} warning(s) limit this estimate.' if warnings else '') + ' Observational design; not an identified causal effect.')
        rows.append({'analysis': 'sdid_pre_period_fit', 'estimate': _r(res['pre_fit_rmse']), 'ci95': None, 'unit': unit,
                     'status': res['pre_fit']['status']})
    elif ok:
        s = res.get('simple')
        if s: rows.append({'analysis': name, 'estimate': _r(s['att']), 'ci95': _rl(s['ci95']), 'se': _r(s['se']), 'unit': unit,
                           'exposed': res['exposed_units'], 'comparison': res['control_units'], 'status': inference})
        rows += [{'analysis': 'cs_cohort', 'cohort': c['group_label'], 'estimate': _r(c['att']), 'ci95': _rl(c['ci95']), 'unit': unit, 'status': inference} for c in res['by_cohort']]
        rows.append({'analysis': 'cs_pretrend_uniform_band', 'estimate': None, 'ci95': None, 'unit': unit, 'status': res['pretrend']['status']})
        s = res['simple']
        reading = (f"Estimated effect of {label} on {outcome_label} under stated assumptions (Callaway-Sant'Anna staggered difference-in-differences, "
                   f"{res['control_group'].replace('_', '-')} controls): overall ATT {_r(s['att']):+.4g} {unit} (95% CI {_r(s['ci95'][0]):+.4g} to "
                   f"{_r(s['ci95'][1]):+.4g}; {res['exposed_units']} exposed in {len(res['cohorts_used'])} adoption-month cohorts vs {res['never_treated_units']} never-treated)"
                   + ('; the interval includes no change.' if s['ci95'][0] <= 0 <= s['ci95'][1] else '.')
                   + (f' {len(warnings)} warning(s) limit this estimate.' if warnings else '') + ' Observational design; not an identified causal effect.')
    else:
        reading = f"The received data cannot support an effect estimate for {label} on {outcome_label}: {blocking[0] if blocking else 'no estimable panel'}"
    desc = {'sdid': ('Synthetic difference-in-differences (Arkhangelsky et al. 2021) on calendar months: the exposed patients\' mean series against unexposed '
                     'practice-level series; unit weights (ridge-regularised, intercept-shifted) and time weights on the simplex; effect = omega x lambda-weighted '
                     'double difference (= weighted two-way fixed effects); standard error by placebo reassignment of donor units.'),
            'callaway_santanna': ("Callaway & Sant'Anna (2021) group-time ATT(g,t) on a patient x calendar-month panel; cohort g = month of first qualifying record, "
                                  f"base period g-1, {p.get('control_group', 'never_treated').replace('_', '-')} controls, "
                                  f"{(p.get('estimator', 'doubly_robust').replace('_', ' ') + ' with ' + ', '.join(p['covariates'])) if p.get('covariates') else 'unconditional parallel trends (no covariates)'}; "
                                  'aggregations: simple (cohort-size weighted post-treatment), event-time and by cohort; influence-function standard errors and '
                                  'multiplier-bootstrap uniform bands.')}[method]
    out.update(warnings=blocking + warnings, blocking_warnings=blocking, assumptions=_panel_assumptions(method, p), inference=inference,
               inference_status=inference, reading=reading, rows=rows,
               method=f"Exposure: {definition}. {desc} Outcome {outcome_label}, {unit}. Estimated effect under stated assumptions, not an identified causal effect.")
    return out


def _panel_assumptions(method, p):
    common = ['No anticipation and no spillover: outcomes before the index month and outcomes of comparison patients are unaffected by the program.',
              'Outcomes are captured equally in all units and months (received feeds); exposure = first qualifying received record, so contacts recorded elsewhere bias toward no effect.',
              'Receipt was not randomised; the first exposed month is partly treated, so the first post effect is attenuated.']
    if method == 'sdid':
        return ['Weighted parallel trends: a weighted combination of unexposed practice series (unit weights) with a weighted pre-period reference (time weights) '
                'tracks what the exposed series would have done without the program; pre-period fit is reported, not assumed.',
                'The exposed series is the mean of exposed patients; adoption is treated as one date (post window starts at the median exposed month, '
                'earlier-exposed patients\' months fall in the dropped gap).'] + common
    cov = bool(p.get('covariates'))
    return [('Conditional parallel trends given ' + ', '.join(p['covariates']) if cov else 'Unconditional parallel trends')
            + (': among units with the same covariates, untreated outcome changes are alike for each cohort and its controls.' if cov else
               ': untreated outcome changes are alike for each adoption cohort and its controls.'),
            ('Never-treated' if p.get('control_group', 'never_treated') == 'never_treated' else 'Not-yet-treated')
            + ' patients are valid comparisons (never-treated: unexposed patients with no program record; not-yet-treated also includes patients exposed later than the period analysed).',
            'Treatment is irreversible: exposed from the index month onward. Effects may differ by cohort and over time; the aggregation weights are stated, not averaged by two-way fixed effects.'] + common


def _panel_sdid(p, people, outcome, exposed, pool, months, blocking, warnings):
    days = sorted(exposed.values())
    first_month, median_month = _month_start(days[0]), _month_start(days[len(days) // 2])
    pre = [m for m in months if m < first_month]; post = [m for m in months if m >= median_month]
    if len(pre) < 3 or not post:
        blocking.append('Synthetic DiD needs >= 3 pre months before the first exposure and >= 1 post month.'); return {'status': 'not_feasible'}
    cols = pre + post
    donors_by = defaultdict(list)
    for c in pool: donors_by[people[c]['practice']].append(c)
    donors = {}
    for k, v in sorted(donors_by.items()):
        if len(v) < 10: continue
        M = _month_rates(v, cols, outcome)
        with np.errstate(all='ignore'): s = np.nanmean(M, axis=0)
        if np.all(np.isfinite(s)): donors[k] = s
    E = _month_rates(sorted(exposed), cols, outcome)
    with np.errstate(all='ignore'): ytr = np.nanmean(E, axis=0)
    if len(donors) < MIN_DONORS + 0 or not np.all(np.isfinite(ytr)):
        blocking.append(f'{len(donors)} complete unexposed practice series (need {MIN_DONORS}) or the exposed series has empty months: synthetic DiD is not feasible.')
        return {'status': 'not_feasible', 'donors': len(donors)}
    names = sorted(donors)
    Y = np.vstack([np.array([donors[k] for k in names]), ytr[None, :]])
    treated = np.zeros(len(Y), bool); treated[-1] = True
    r = sdid(Y, treated, len(pre), draws=min(p['bootstrap'], SDID_PLACEBO_REPS), seed=p['seed'], names=names, labels=[_iso(m)[:7] for m in cols])
    late = sum(1 for t in days if _month_start(t) > median_month)
    if late / len(days) > 0.25:
        warnings.append(f'{late} of {len(days)} exposed patients start after the post window opens ({_iso(median_month)[:7]}): adoption is not a single date, '
                        'so post months mix treated and not-yet-treated patients and the effect is attenuated (callaway_santanna handles staggering).')
    fit_ratio = r['pre_fit_rmse'] / r['treated_pre_sd'] if r['treated_pre_sd'] > 0 else 0.0
    status = 'good' if fit_ratio <= 0.5 else 'poor'
    if status == 'poor':
        warnings.append(f"Synthetic pre-period fit is poor (RMSE {r['pre_fit_rmse']:.4g} vs the exposed pre-period SD {r['treated_pre_sd']:.4g}); the weighted comparison does not track the exposed series.")
    if not r['ci95']: blocking.append('Placebo variance could not be computed.')
    if len(donors) < 10: warnings.append(f'Only {len(donors)} unexposed practice series: placebo standard error rests on few donors.')
    r.update(status='estimated' if r['ci95'] else 'not_feasible', treated_patients=len(exposed), donors=len(donors),
             pre_months=[_iso(m)[:7] for m in pre], post_months=[_iso(m)[:7] for m in post],
             pre_fit={'status': status, 'rmse': r['pre_fit_rmse'], 'rmse_unweighted_did': r['pre_fit_rmse_unweighted_did'], 'rmse_over_pre_sd': fit_ratio})
    del r['unit_weight_vector'], r['time_weight_vector']
    for k in ('effect', 'se', 'did_unweighted', 'pre_fit_rmse', 'pre_fit_rmse_unweighted_did', 'treated_pre_sd', 'sigma_hat', 'zeta', 'unit_weight_intercept'): r[k] = _r(r[k])
    r['ci95'] = _rl(r['ci95'])
    r['unit_weights'] = {k: _r(v) for k, v in r['unit_weights'].items()}; r['time_weights'] = {k: _r(v) for k, v in r['time_weights'].items()}
    return r


def _panel_cs(p, people, outcome, exposed, pool, months, blocking, warnings, as_of_day):
    cov_names = p.get('covariates', [])
    ids = sorted(exposed) + list(pool)
    M = _month_rates(ids, months, outcome)
    good = np.all(np.isfinite(M), axis=1)
    dropped = int((~good).sum())
    if dropped: warnings.append(f'{dropped} of {len(ids)} patients dropped for an unmeasurable outcome in at least one panel month (balanced panel required).')
    idx = np.flatnonzero(good)
    ids = [ids[i] for i in idx]; M = M[idx]
    G = np.array([float(bisect_left(months, _month_start(exposed[x]) + 1) - 1) if x in exposed else np.inf for x in ids])
    X = _covariate_matrix(ids, people, cov_names) if cov_names else None
    labels = [_iso(m)[:7] for m in months]
    cg = p.get('control_group', 'never_treated')
    if (G < np.inf).sum() < MIN_ESTIMATE:
        blocking.append(f'Too few exposed patients with a complete panel ({int((G < np.inf).sum())}; need >= {MIN_ESTIMATE}).'); return {'status': 'not_feasible'}
    try:
        r = callaway_santanna(M, G, X, cg, p.get('estimator', 'doubly_robust'), draws=p['bootstrap'], seed=p['seed'], min_cohort=MIN_COHORT,
                              max_pre=math.ceil(p['pre_days'] / 30), max_post=math.ceil(p['post_days'] / 30), labels=labels)
    except ValueError as ex:
        blocking.append(str(ex)); return {'status': 'not_feasible', 'reason': str(ex)}
    for g, why in r['cohorts_dropped'].items(): warnings.append(f'Adoption cohort {g} dropped: {why}.')
    if 'simple' not in r: blocking.append('No post-treatment group-time effect is estimable.')
    if r['pretrend']['status'] == 'failed':
        warnings.append('Pre-treatment event-time effects fall outside the uniform 95% band at event times '
                        + ', '.join(map(str, r['pretrend']['event_times_outside_band'])) + ': parallel trends is doubtful for this design.')
    elif r['pretrend']['status'] == 'not_assessable': warnings.append('Parallel trends could not be assessed: no pre-treatment periods beyond the base period.')
    if r.get('max_pscore') and r['max_pscore'] > 0.99: warnings.append(f"Weak overlap: estimated propensity score reaches {r['max_pscore']:.3f} in some cells (positivity); weights are trimmed at 0.9999.")
    if cg == 'not_yet_treated' and not int((G == np.inf).sum()): warnings.append('No never-treated patients: identification rests on later-treated cohorts staying untreated until their start.')
    if p['outcome'] == 'pmpm': warnings.append('Recent months are completion-adjusted (chain-ladder IBNR); post-window cost near the snapshot is partly estimated.')
    r['exposed_units'] = int((G < np.inf).sum()); r['control_units'] = int(np.isinf(G).sum())
    r['panel'] = {'periods': len(months), 'first_month': labels[0], 'last_month': labels[-1], 'units': len(ids), 'dropped_units': dropped,
                  'covariates': cov_names}
    r['status'] = 'estimated' if 'simple' in r else 'not_feasible'
    def rd(o):
        if isinstance(o, dict): return {k: rd(v) for k, v in o.items()}
        if isinstance(o, list): return [rd(v) for v in o]
        return _r(o) if isinstance(o, float) else o
    return rd(r)
