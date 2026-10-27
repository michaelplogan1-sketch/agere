"""Target trial emulation: comparative effectiveness of two treatment strategies from received fills, labs and encounters.

The protocol of the hypothetical randomised trial is written down first (Hernan & Robins target-trial framework) and
then emulated with the received data:
  eligibility       adults (age bounds stated), the stated population conditions documented by time zero, no stated
                    exclusion condition before time zero, NEW users of both strategies: no fill of either strategy's
                    drug class during the washout before time zero, and time zero at least one washout after the fill
                    feed starts (earlier use cannot be ruled out before that); a baseline result for lab outcomes.
  strategies        initiate strategy A vs initiate the active comparator B (drug classes, or statin intensity by
                    ingredient and strength, ACC/AHA 2018).
  assignment        the strategy whose drug is filled at time zero (patients initiating both on one day are excluded).
  time zero         the first qualifying fill: eligibility, assignment and the start of follow-up coincide, so no
                    immortal time enters either arm and nothing after time zero decides eligibility or arm.
  follow-up         to the outcome, the horizon or the snapshot; per-protocol additionally censors at deviation
                    (switching to the other strategy, or the initiated supply ending for more than grace_days).
  outcome           change in a lab at the horizon (closest result within +/- window), or time to an admission, ED
                    visit, incident condition, lab threshold or a percentage lab decline.
  causal contrasts  intention-to-treat (as initiated) and per-protocol (censoring at deviation).
  analysis          propensity score (ridge logistic regression on baseline age, sex, payer, documented conditions,
                    prior admissions / ED visits, baseline labs with missing indicators, calendar year), overlap
                    weights (default; ATO), stabilised trimmed IPTW (ATE) or 1:1 caliper matching (ATT); balance by
                    standardised mean differences; weighted mean difference of the change (continuous) or weighted
                    Kaplan-Meier risks and a weighted Cox hazard ratio (time to event); 95% intervals by bootstrap that
                    re-estimates the propensity model in every draw; outcome-ascertainment / censoring weights from
                    baseline covariates; E-value for unmeasured confounding; a negative-control outcome (assumed null)
                    run through the identical pipeline.
Every result is an estimate under the emulated protocol and its assumptions (no unmeasured confounding, positivity,
consistency, correct outcome capture), never a randomised or identified causal effect. Data are synthetic.
"""
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from datetime import date
import math, re, sqlite3

import numpy as np

from . import aggregate, concepts, semantic_layer
from .drug_classes import CLASSES
from .line_of_therapy import episodes, fill_rows

VERSION = 'target-trial/1'
OUTCOME_TYPES = ('lab_change', 'lab_threshold', 'lab_decline_pct', 'admission', 'ed_visit', 'condition', 'encounter')
ADJUSTMENTS = ('overlap', 'iptw', 'matching')
OPS = {'lt': lambda v, t: v < t, 'le': lambda v, t: v <= t, 'gt': lambda v, t: v > t, 'ge': lambda v, t: v >= t}
OP_WORDS = {'lt': 'below', 'le': 'at or below', 'gt': 'above', 'ge': 'at or above'}
MIN_ARM = 10        # patients per arm below which no estimate is reported
SMALL_ARM = 50      # below this the estimate carries a small-sample warning
SMD_LIMIT = 0.1
BASELINE_CONDITIONS = ('diabetes', 'type_2_diabetes', 'hypertension', 'heart_failure', 'chronic_kidney_disease', 'coronary_artery_disease',
                       'atrial_fibrillation', 'obesity', 'hyperlipidemia', 'copd', 'depression')
BASELINE_LABS = ('egfr', 'hba1c', 'ldl', 'systolic_bp')
# Statin intensity by ingredient and daily strength (mg), ACC/AHA 2018 cholesterol guideline, Table 3.
STATIN_INTENSITY = {
    'high': {'atorvastatin': (40, 80), 'rosuvastatin': (20, 40)},
    'moderate': {'atorvastatin': (10, 20), 'rosuvastatin': (5, 10), 'simvastatin': (20, 40), 'pravastatin': (40, 80), 'lovastatin': (40, 80),
                 'fluvastatin': (80, 80), 'pitavastatin': (1, 4)},
    'low': {'simvastatin': (10, 10), 'pravastatin': (10, 20), 'lovastatin': (20, 20), 'fluvastatin': (20, 40)},
}
INTENSITY_STRATEGIES = {'high_intensity_statin': ('high',), 'moderate_intensity_statin': ('moderate',), 'low_intensity_statin': ('low',),
                        'lower_intensity_statin': ('moderate', 'low')}
STRATEGIES = (*CLASSES, *INTENSITY_STRATEGIES)
ASSUMPTIONS = [
    'No unmeasured confounding: given the baseline covariates listed, the strategy initiated is as good as randomised '
    '(prescriber preference, frailty, adherence expectations and cost are not observed).',
    'Positivity: every eligible patient could have initiated either strategy (checked through propensity-score overlap).',
    'Consistency: "initiating" a strategy means the first received fill; fills outside the received pharmacy feeds are invisible.',
    'Outcome capture is equal across arms (labs ordered and encounters recorded alike); missing outcomes are handled by '
    'baseline-covariate ascertainment weights only.',
    'Per-protocol: censoring at deviation is adjusted for baseline covariates only (no time-varying weights); if deviation '
    'depends on post-baseline prognosis the per-protocol estimate is biased.',
]


# ---- parameters ------------------------------------------------------------------------------------------------
def _int(p, key, default, lo, hi):
    v = p.get(key, default)
    if type(v) is not int or not lo <= v <= hi: raise ValueError(f'{key} must be an integer {lo}..{hi}')
    return v


def _outcome(o, where='outcome'):
    if not isinstance(o, dict) or o.get('type') not in OUTCOME_TYPES:
        raise ValueError(f'{where}.type must be one of {OUTCOME_TYPES}')
    if set(o) - {'type', 'lab', 'op', 'value', 'pct', 'concept', 'horizon_days', 'window_days'}:
        raise ValueError(f'{where} takes type, lab, op, value, pct, concept, horizon_days, window_days')
    t = o['type']
    out = {'type': t, 'horizon_days': _int(o, 'horizon_days', 365, 30, 1825)}
    if t.startswith('lab'):
        if o.get('lab') not in concepts.MEASUREMENTS: raise ValueError(f'{where}: {t} needs a measurement concept')
        out['lab'] = o['lab']
    if t == 'lab_change': out['window_days'] = _int(o, 'window_days', 120, 14, 365)
    if t == 'lab_threshold':
        if o.get('op') not in OPS or type(o.get('value')) not in (int, float) or not math.isfinite(o['value']):
            raise ValueError(f'{where}: lab_threshold needs op lt/le/gt/ge and a numeric value')
        out.update(op=o['op'], value=float(o['value']))
    if t == 'lab_decline_pct':
        v = o.get('pct', 0.4)
        if type(v) not in (int, float) or not 0.05 <= v <= 0.9: raise ValueError(f'{where}: pct 0.05..0.9')
        out['pct'] = float(v)
    if t in ('condition', 'encounter'):
        pool = concepts.CONDITIONS if t == 'condition' else concepts.ENCOUNTERS
        if o.get('concept') not in pool: raise ValueError(f'{where}: {t} needs a library {t} concept')
        out['concept'] = o['concept']
    return out


def validate(p):
    allowed = {'arms', 'outcome', 'population_conditions', 'exclude_conditions', 'min_age', 'max_age', 'washout_days', 'baseline_days',
               'grace_days', 'adjustment', 'caliper', 'bootstrap', 'seed', 'negative_control', 'period'}
    if not isinstance(p, dict) or set(p) - allowed: raise ValueError('target_trial parameters: ' + ', '.join(sorted(allowed)))
    arms = p.get('arms')
    if not isinstance(arms, list) or len(arms) != 2 or arms[0] == arms[1] or any(a not in STRATEGIES for a in arms):
        raise ValueError('target_trial needs arms: two distinct treatment strategies (registered drug classes or statin intensities)')
    if (arms[0] in INTENSITY_STRATEGIES) != (arms[1] in INTENSITY_STRATEGIES):
        raise ValueError('compare statin intensities with statin intensities, drug classes with drug classes')
    if set(INTENSITY_STRATEGIES[arms[0]] if arms[0] in INTENSITY_STRATEGIES else ()) & set(INTENSITY_STRATEGIES.get(arms[1], ())):
        raise ValueError('statin intensity arms overlap')
    out = {'arms': list(arms), 'outcome': _outcome(p.get('outcome'))}
    for key in ('population_conditions', 'exclude_conditions'):
        v = p.get(key, [])
        if not isinstance(v, list) or len(v) > 6 or any(c not in concepts.CONDITIONS for c in v): raise ValueError(f'{key}: up to six condition concepts')
        out[key] = list(v)
    out['min_age'] = _int(p, 'min_age', 18, 0, 120)
    out['max_age'] = _int(p, 'max_age', 120, out['min_age'], 120)
    out['washout_days'] = _int(p, 'washout_days', 365, 90, 1095)
    out['baseline_days'] = _int(p, 'baseline_days', 365, 30, 1095)
    out['grace_days'] = _int(p, 'grace_days', 60, 7, 180)
    adj = p.get('adjustment', 'overlap')
    if adj not in ADJUSTMENTS: raise ValueError(f'adjustment must be one of {ADJUSTMENTS}')
    out['adjustment'] = adj
    cal = p.get('caliper', 0.2)
    if type(cal) not in (int, float) or not 0.01 <= cal <= 1: raise ValueError('caliper 0.01..1 SD of the logit propensity score')
    out['caliper'] = float(cal)
    out['bootstrap'] = _int(p, 'bootstrap', 400, 50, 3000)
    out['seed'] = _int(p, 'seed', 11, 0, 2 ** 31 - 1)
    nc = p.get('negative_control', 'auto')
    if nc not in ('auto', 'none'): nc = _outcome(nc, 'negative_control')
    out['negative_control'] = nc
    period = p.get('period') or {}
    if not isinstance(period, dict) or set(period) - {'start', 'end'} or any(v and (not isinstance(v, str) or len(v) != 10) for v in period.values()):
        raise ValueError('period takes ISO start/end (time-zero dates)')
    for v in period.values():
        if v: date.fromisoformat(v)
    out['period'] = {'start': period.get('start', ''), 'end': period.get('end', '')}
    return out


# ---- strategies ------------------------------------------------------------------------------------------------
_MG = re.compile(r'(\d+(?:\.\d+)?)\s*mg\b', re.I)


def statin_intensity(name):
    """'high' | 'moderate' | 'low' | None from a received statin name with its strength ('atorvastatin 40 mg tablet' -> high)."""
    low = str(name or '').lower()
    ing = next((x for x in ('atorvastatin', 'rosuvastatin', 'simvastatin', 'pravastatin', 'lovastatin', 'fluvastatin', 'pitavastatin') if x in low), None)
    m = _MG.search(low)
    if not ing or not m: return None
    mg = float(m.group(1))
    for level in ('high', 'moderate', 'low'):
        rng = STATIN_INTENSITY[level].get(ing)
        if rng and rng[0] <= mg <= rng[1]: return level
    return None


def _strategy_class(arm): return 'statin' if arm in INTENSITY_STRATEGIES else arm


def strategy_fills(rows, arms):
    """{pid: {'any': {day: supply} (either strategy's class: washout), arm: {day: supply} (fills that are this strategy)}}"""
    classes = {_strategy_class(a) for a in arms}
    rx = {c: re.compile(CLASSES[c], re.I) for c in classes}
    out = defaultdict(lambda: defaultdict(dict))
    for pid, day, supply, name, cls in rows:
        hit = {c for c in classes if cls == c or rx[c].search(name)}
        if not hit: continue
        s = supply or 30
        out[pid]['any'][day] = max(out[pid]['any'].get(day, 0), s)
        for a in arms:
            if _strategy_class(a) not in hit: continue
            if a in INTENSITY_STRATEGIES and statin_intensity(name) not in INTENSITY_STRATEGIES[a]: continue
            out[pid][a][day] = max(out[pid][a].get(day, 0), s)
    return out


def label(arm):
    return arm.replace('_', ' ')


# ---- data ------------------------------------------------------------------------------------------------------
def _day(s): return date.fromisoformat(s[:10]).toordinal()
def _iso(d): return date.fromordinal(int(d)).isoformat()


def _cols(db, table): return {r[1] for r in db.execute(f'PRAGMA table_info({table})')}


def _labs(db, concept_ids, keep):
    from .safety_data import usable_measurement_sql
    extra = usable_measurement_sql(db)
    out = defaultdict(lambda: defaultdict(list))
    q = ','.join('?' * len(concept_ids))
    for pid, c, d, v in db.execute(f'SELECT patient_id, concept, event_date, value_canonical FROM measurement WHERE concept IN ({q}) '
                                   f'AND value_canonical IS NOT NULL AND event_date IS NOT NULL{extra}', list(concept_ids)):
        if keep is None or pid in keep: out[c][pid].append((_day(d), float(v)))
    for by in out.values():
        for v in by.values(): v.sort()
    return out


def _events(db, kind, concept, keep):
    out = defaultdict(list)
    if kind == 'admission':
        sql, args = 'SELECT patient_id, admit_date FROM stay WHERE admit_date IS NOT NULL', ()
    elif kind == 'ed_visit':
        from .ed_definition import metric_visits_sql
        sql,args=metric_visits_sql(db),()
    elif kind == 'encounter':
        sql = "SELECT DISTINCT patient_id, substr(event_date,1,10) FROM encounter WHERE type_concept = ? AND event_date IS NOT NULL"
        args = ('emergency_visit' if kind == 'ed_visit' else concept,)
    else:
        sql = 'SELECT patient_id, coalesce(substr(event_date,1,10), substr(recorded_date,1,10)) FROM condition WHERE concept = ?'
        args = (concept,)
    for pid, d in db.execute(sql, args):
        if d and (keep is None or pid in keep): out[pid].append(_day(d))
    for v in out.values(): v.sort()
    return out


def _last_before(series, day, lo):
    """Last (day, value) in [lo, day]."""
    if not series: return None
    i = bisect_right(series, (day, math.inf)) - 1
    return series[i] if i >= 0 and series[i][0] >= lo else None


def _closest(series, target, window, lo, hi):
    """Result closest to target within +/- window, restricted to (lo, hi]."""
    best = None
    for d, v in series[bisect_left(series, (max(lo + 1, target - window), -math.inf)):]:
        if d > min(hi, target + window): break
        if best is None or abs(d - target) < abs(best[0] - target): best = (d, v)
    return best


# ---- estimators ------------------------------------------------------------------------------------------------
def logistic(X, y, ridge=0.1, w=None, iters=50):
    """Ridge logistic regression by Newton-Raphson on standardised columns (intercept unpenalised). Returns fitted p."""
    X = np.asarray(X, float); y = np.asarray(y, float)
    w = np.ones(len(y)) if w is None else np.asarray(w, float)
    mu, sd = X.mean(0), X.std(0); sd[sd == 0] = 1
    Z = np.hstack([np.ones((len(y), 1)), (X - mu) / sd])
    beta = np.zeros(Z.shape[1]); pen = np.full(Z.shape[1], ridge); pen[0] = 0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(Z @ beta, -30, 30)))
        g = Z.T @ (w * (y - p)) - pen * beta
        H = (Z * (w * p * (1 - p))[:, None]).T @ Z + np.diag(pen) + 1e-9 * np.eye(Z.shape[1])
        step = np.linalg.solve(H, g)
        beta += step
        if np.max(np.abs(step)) < 1e-8: break
    return 1 / (1 + np.exp(-np.clip(Z @ beta, -30, 30)))


def ps_weights(ps, a, method, caliper=0.2, rng=None):
    """Weights per patient for the chosen adjustment; matching returns 0/1 weights for 1:1 nearest-neighbour pairs on the
    logit propensity score within caliper x SD (greedy, without replacement, random order of treated)."""
    a = np.asarray(a, int); ps = np.clip(np.asarray(ps, float), 1e-6, 1 - 1e-6)
    if method == 'overlap':
        return np.where(a == 1, 1 - ps, ps)
    if method == 'iptw':
        pa = a.mean()
        w = np.where(a == 1, pa / ps, (1 - pa) / (1 - ps))
        lo, hi = np.quantile(w, [0.01, 0.99])
        return np.clip(w, lo, hi)
    lg = np.log(ps / (1 - ps)); cal = caliper * lg.std() if lg.std() > 0 else 0.0
    t_idx = np.flatnonzero(a == 1); c_idx = list(np.flatnonzero(a == 0))
    order = t_idx.copy()
    (rng or np.random.default_rng(0)).shuffle(order)
    w = np.zeros(len(a)); used = set()
    for i in order:
        best, bd = None, None
        for j in c_idx:
            if j in used: continue
            d = abs(lg[i] - lg[j])
            if d <= cal + 1e-12 and (bd is None or d < bd): best, bd = j, d
        if best is not None: used.add(best); w[i] = w[best] = 1
    return w


def smd(x, a, w=None):
    """Standardised mean difference (weighted means, unweighted pooled SD)."""
    x = np.asarray(x, float); a = np.asarray(a, int)
    w = np.ones(len(x)) if w is None else np.asarray(w, float)
    x1, x0 = x[a == 1], x[a == 0]
    if not len(x1) or not len(x0) or w[a == 1].sum() <= 0 or w[a == 0].sum() <= 0: return None
    m1 = np.average(x1, weights=w[a == 1]); m0 = np.average(x0, weights=w[a == 0])
    s = math.sqrt((x1.var(ddof=1 if len(x1) > 1 else 0) + x0.var(ddof=1 if len(x0) > 1 else 0)) / 2)
    if s == 0: return 0.0 if abs(m1 - m0) < 1e-12 else None
    return float((m1 - m0) / s)


def weighted_km(t, e, w, horizon):
    """Weighted Kaplan-Meier risk (1 - S) at horizon, plus the curve [(day, survival)]."""
    t = np.asarray(t, float); e = np.asarray(e, int); w = np.asarray(w, float)
    s, curve = 1.0, [(0, 1.0)]
    for u in np.unique(t[(e == 1) & (t <= horizon)]):
        n = w[t >= u].sum(); d = w[(t == u) & (e == 1)].sum()
        if n > 0: s *= 1 - d / n
        curve.append((int(u), round(s, 5)))
    return 1 - s, curve


def weighted_cox(t, e, x, w, iters=50):
    """Weighted Cox model with one binary covariate (Breslow ties): log hazard ratio by Newton-Raphson."""
    t = np.asarray(t, float); e = np.asarray(e, int); x = np.asarray(x, float); w = np.asarray(w, float)
    times = np.unique(t[e == 1])
    if not len(times) or x.min() == x.max(): return None
    b = 0.0
    for _ in range(iters):
        g = h = 0.0
        for u in times:
            R = t >= u; D = (t == u) & (e == 1)
            r = w[R] * np.exp(b * x[R]); s0 = r.sum(); s1 = (r * x[R]).sum()
            if s0 <= 0: continue
            dw = w[D].sum(); xbar = s1 / s0
            g += (w[D] * x[D]).sum() - dw * xbar
            h += dw * (xbar - xbar * xbar)  # x binary: s2/s0 = s1/s0
        if h <= 1e-12: return None
        step = g / h
        b += step
        if abs(b) > 10: return None
        if abs(step) < 1e-9: break
    return b


def e_value(rr):
    """E-value for a risk ratio (VanderWeele & Ding 2017)."""
    if rr is None or not math.isfinite(rr) or rr <= 0: return None
    rr = 1 / rr if rr < 1 else rr
    return rr + math.sqrt(rr * (rr - 1))


def e_value_ci(lo, hi):
    if lo is None or hi is None: return None
    if lo <= 1 <= hi: return 1.0
    return e_value(lo if lo > 1 else hi)


# ---- execute ---------------------------------------------------------------------------------------------------
def execute(path, patient_ids, params, as_of=None):
    db = sqlite3.connect(f'file:{semantic_layer.ensure(path)}?mode=ro', uri=True)
    try:
        as_of = as_of or dict(db.execute('SELECT key, value FROM meta')).get('as_of')
        return run(db, patient_ids, params, as_of)
    finally:
        db.close()


def describe_outcome(o):
    t = o['type']
    if t == 'lab_change': return f"change in {o['lab']} at {o['horizon_days']} days"
    if t == 'lab_threshold': return f"{o['lab']} {OP_WORDS[o['op']]} {o['value']:g} within {o['horizon_days']} days"
    if t == 'lab_decline_pct': return f"{o['pct']:.0%} decline in {o['lab']} from baseline within {o['horizon_days']} days"
    if t == 'admission': return f"hospital admission (all-cause) within {o['horizon_days']} days"
    if t == 'ed_visit': return f"ED visit within {o['horizon_days']} days"
    if t == 'condition': return f"incident {o['concept'].replace('_', ' ')} (first documented record) within {o['horizon_days']} days"
    return f"first {o['concept'].replace('_', ' ')} within {o['horizon_days']} days"


def _auto_negative_control(o):
    """Assumed-null negative-control outcome. Lab outcomes: the change in the same lab over the year BEFORE time zero (no
    drug started at time zero can change it; the pre-period change is not a propensity covariate). Event outcomes: time to
    the first urgent-care visit (no compared class is expected to change urgent-care use)."""
    if o['type'] == 'lab_change' or o['type'].startswith('lab'):
        return {'type': 'pre_period_change', 'lab': o['lab'], 'horizon_days': 365}
    return {'type': 'encounter', 'concept': 'urgent_care_visit', 'horizon_days': o['horizon_days']}


def run(db, patient_ids, params, as_of):
    p = validate(params)
    as_of_day = _day(as_of)
    keep = set(patient_ids) if patient_ids is not None else None
    arms, o = p['arms'], p['outcome']
    rows, n_reversed = fill_rows(db, keep)
    feed = min((r[1] for r in rows), default=None)
    fills = strategy_fills(rows, arms)
    people = {pid: (age, sex, cov, birth) for pid, age, sex, cov, birth in db.execute(
        'SELECT patient_id, age_reported, sex_reported, coverage_type, ' + ('birth_date' if 'birth_date' in _cols(db, 'patient') else 'NULL') + ' FROM patient')}
    first_cond = defaultdict(dict)
    want = set(BASELINE_CONDITIONS) | set(p['population_conditions']) | set(p['exclude_conditions'])
    q = ','.join('?' * len(want))
    for pid, c, d in db.execute(f'SELECT patient_id, concept, min(coalesce(substr(event_date,1,10), substr(recorded_date,1,10))) FROM condition '
                                f'WHERE concept IN ({q}) GROUP BY patient_id, concept', sorted(want)):
        if d: first_cond[pid][c] = _day(d)
    lab_ids = set(BASELINE_LABS) | ({o['lab']} if 'lab' in o else set())
    nc = _auto_negative_control(o) if p['negative_control'] == 'auto' else (None if p['negative_control'] == 'none' else p['negative_control'])
    if nc and 'lab' in nc: lab_ids.add(nc['lab'])
    labs = _labs(db, sorted(lab_ids), keep)
    adm = _events(db, 'admission', None, keep)
    ed = _events(db, 'ed_visit', None, keep)
    out_events = None
    if o['type'] in ('admission', 'ed_visit', 'condition', 'encounter'):
        out_events = adm if o['type'] == 'admission' else ed if o['type'] == 'ed_visit' else _events(db, o['type'], o['concept'], keep)
    nc_events = _events(db, nc['type'], nc.get('concept'), keep) if nc and nc['type'] in ('encounter', 'condition') else None

    # --- eligibility, assignment at time zero (one row per eligible new user) ---
    funnel = Counter()
    cohort = []
    lo_p = _day(p['period']['start']) if p['period']['start'] else None
    hi_p = _day(p['period']['end']) if p['period']['end'] else None
    for pid in sorted(fills):
        f = fills[pid]
        starts = {a: min(f[a]) for a in arms if f.get(a)}
        if not starts: continue
        funnel['initiators_of_either_strategy'] += 1
        t0 = min(starts.values())
        first_any = min(f['any'])
        if first_any < t0: funnel['excluded_prior_use_of_class_not_in_either_strategy'] += 1; continue  # e.g. a statin of another intensity first
        if len([a for a in starts if starts[a] == t0]) > 1: funnel['excluded_initiated_both_strategies_same_day'] += 1; continue
        if feed is None or t0 < feed + p['washout_days']: funnel['excluded_washout_not_observable'] += 1; continue
        if t0 > as_of_day: continue
        if (lo_p and t0 < lo_p) or (hi_p and t0 > hi_p): funnel['excluded_time_zero_outside_period'] += 1; continue
        age, sex, cov, birth = people.get(pid, (None, None, None, None))
        if birth:
            try: age0 = (t0 - _day(birth)) / 365.25
            except ValueError: age0 = None
        else:
            age0 = age - (as_of_day - t0) / 365.25 if isinstance(age, (int, float)) else None
        if age0 is None: funnel['excluded_age_unknown'] += 1; continue
        if not p['min_age'] <= age0 <= p['max_age'] + 1: funnel['excluded_age'] += 1; continue
        conds = first_cond.get(pid, {})
        if any(c not in conds or conds[c] > t0 for c in p['population_conditions']): funnel['excluded_population_condition_not_documented_by_time_zero'] += 1; continue
        if any(c in conds and conds[c] < t0 for c in p['exclude_conditions']): funnel['excluded_exclusion_condition'] += 1; continue
        arm = next(a for a in arms if starts.get(a) == t0)
        base_out = None
        if 'lab' in o:
            base_out = _last_before(labs.get(o['lab'], {}).get(pid), t0, t0 - p['baseline_days'])
            if base_out is None: funnel['excluded_no_baseline_outcome_lab'] += 1; continue
            if o['type'] == 'lab_threshold' and OPS[o['op']](base_out[1], o['value']):
                funnel['excluded_already_past_threshold_at_time_zero'] += 1; continue
        # per-protocol deviation day: switch to the other strategy or the initiated supply lapses beyond grace
        other = arms[1] if arm == arms[0] else arms[0]
        ep = episodes(f[arm], p['grace_days'])[0]
        lapse = ep[1] + p['grace_days'] + 1
        sw = min((d for d in f.get(other, {}) if d > t0), default=None)
        deviation = min(lapse, sw) if sw is not None else lapse
        cohort.append({'pid': pid, 't0': t0, 'arm': arm, 'a': 1 if arm == arms[0] else 0, 'age': age0, 'sex': sex, 'cov': cov,
                       'conds': {c for c, d in conds.items() if d <= t0}, 'base_out': base_out, 'deviation': deviation,
                       'deviation_kind': 'switch' if sw is not None and sw <= lapse else 'discontinued'})
    funnel['eligible'] = len(cohort)
    n1 = sum(c['a'] for c in cohort); n0 = len(cohort) - n1

    # --- baseline covariates ---
    names, X = covariate_matrix(cohort, labs, adm, ed, p, o)
    a = np.array([c['a'] for c in cohort], int)

    # --- outcomes per patient: ITT and per-protocol ---
    def outcome_for(c, oc, pp):
        t0 = c['t0']
        end = min(t0 + oc['horizon_days'], as_of_day)
        if pp: end = min(end, c['deviation'] - 1)
        if oc['type'] == 'pre_period_change':
            s = labs.get(oc['lab'], {}).get(c['pid'], [])
            b = _last_before(s, t0, t0 - p['baseline_days'])
            prior = _closest(s, t0 - 365, 120, -math.inf, t0 - 180)
            return ('value', b[1] - prior[1]) if b and prior else None
        if oc['type'] == 'lab_change':
            s = labs.get(oc['lab'], {}).get(c['pid'], [])
            b = _last_before(s, t0, t0 - p['baseline_days'])
            if b is None: return None
            hit = _closest(s, t0 + oc['horizon_days'], oc['window_days'], t0, min(as_of_day, c['deviation'] - 1) if pp else as_of_day)
            return ('value', hit[1] - b[1]) if hit else None
        if oc['type'] in ('lab_threshold', 'lab_decline_pct'):
            s = labs.get(oc['lab'], {}).get(c['pid'], [])
            b = _last_before(s, t0, t0 - p['baseline_days'])
            if b is None: return None
            after = [(d, v) for d, v in s if t0 < d <= end]
            if not after: return None
            hit = next((d for d, v in after if (OPS[oc['op']](v, oc['value']) if oc['type'] == 'lab_threshold' else v <= b[1] * (1 - oc['pct']))), None)
            return ('time', hit - t0, 1) if hit else ('time', after[-1][0] - t0, 0)
        ev = out_events if oc is o else nc_events
        days = ev.get(c['pid'], []) if ev else []
        if oc['type'] == 'condition' and days and days[0] <= t0: return None  # prevalent at time zero: not at risk of incidence
        hit = next((d for d in days if d > t0), None)
        if end <= t0: return None
        return ('time', hit - t0, 1) if hit is not None and hit <= end else ('time', end - t0, 0)

    rng = np.random.default_rng(p['seed'])
    # blocking: no estimate; cautions: the estimate is reported but inference is limited; notes: always-true context
    blocking, cautions, notes = [], [], ['Synthetic data: every number describes a generated warehouse, not real patients or real drug effects.']
    analyses, balance, ps_summary = {}, [], None
    if n1 < MIN_ARM or n0 < MIN_ARM:
        blocking.append(f'Too few eligible new users per arm ({label(arms[0])} {n1}, {label(arms[1])} {n0}; need >= {MIN_ARM} each): no effect is estimated.')
    else:
        if n1 < SMALL_ARM or n0 < SMALL_ARM:
            cautions.append(f'Small arms ({label(arms[0])} {n1}, {label(arms[1])} {n0} new users): wide, unstable intervals; covariate adjustment is fragile.')
        ps = logistic(X, a)
        w = ps_weights(ps, a, p['adjustment'], p['caliper'], np.random.default_rng(p['seed']))
        ext = float(np.mean((ps < 0.05) | (ps > 0.95)))
        ps_summary = {'treated': _quant(ps[a == 1]), 'comparator': _quant(ps[a == 0]), 'share_outside_0.05_0.95': round(ext, 4),
                      'histogram': _hist(ps, a), 'effective_sample_size': {arms[0]: _ess(w[a == 1]), arms[1]: _ess(w[a == 0])}}
        if ext > 0.1: cautions.append(f'Limited overlap: {ext:.0%} of patients have a propensity score outside 0.05-0.95 (positivity is doubtful for them).')
        if p['adjustment'] == 'matching':
            m1 = int((w[a == 1] > 0).sum())
            if m1 < 0.8 * n1: cautions.append(f'Only {m1} of {n1} {label(arms[0])} initiators found a match within the caliper: the estimate describes the matched subset.')
        for j, nm in enumerate(names):
            balance.append({'covariate': nm, 'mean_treated': _r(float(X[a == 1, j].mean())), 'mean_comparator': _r(float(X[a == 0, j].mean())),
                            'smd_before': _r(smd(X[:, j], a)), 'smd_after': _r(smd(X[:, j], a, w))})
        bad = [b['covariate'] for b in balance if b['smd_after'] is not None and abs(b['smd_after']) > SMD_LIMIT]
        if bad: cautions.append(f'Residual imbalance after {p["adjustment"]} weighting (|SMD| > {SMD_LIMIT}): ' + ', '.join(bad[:6]) + '.')
        for contrast, pp in (('intention_to_treat', False), ('per_protocol', True)):
            analyses[contrast] = estimate(cohort, X, a, [outcome_for(c, o, pp) for c in cohort], o, p, rng)
        if nc:
            analyses['negative_control'] = estimate(cohort, X, a, [outcome_for(c, nc, False) for c in cohort], nc, p, rng)
            r = analyses['negative_control']
            if r.get('status') == 'estimated' and r['ci95'][0] is not None:
                lo, hi = r['ci95']
                null = 1.0 if r['scale'] == 'hazard_ratio' else 0.0
                r['assumed_null_consistent'] = lo <= null <= hi
                if not r['assumed_null_consistent']:
                    cautions.append(f"Negative-control outcome ({r['outcome']}) is not null ({r['estimate']:+.3g}, 95% CI {lo:.3g} to {hi:.3g}): "
                                    'residual confounding or differential outcome capture is likely; treat the main estimate as biased.')
        itt = analyses['intention_to_treat']
        if itt.get('status') != 'estimated':
            blocking.append('Intention-to-treat estimate not available: ' + itt.get('reason', 'no analysable outcomes'))
        else:
            if itt.get('events') is not None and itt['events'] < 10:
                cautions.append(f"Only {itt['events']} outcome events in follow-up: the risk ratio and hazard ratio are unstable.")
            if itt['analysed'] < 0.7 * len(cohort):
                cautions.append(f"Outcome measured for {itt['analysed']} of {len(cohort)} eligible patients; the rest are weighted for "
                                'ascertainment on baseline covariates only.')
        pp = analyses['per_protocol']
        dev = Counter(c['deviation_kind'] for c in cohort if c['deviation'] <= c['t0'] + o['horizon_days'])
        if dev: notes.append(f"Per-protocol censoring before the horizon: {dev.get('switch', 0)} switched to the other strategy, "
                                f"{dev.get('discontinued', 0)} let the supply lapse > {p['grace_days']} days.")
    if o['type'] in ('admission', 'ed_visit', 'condition', 'encounter', 'lab_threshold', 'lab_decline_pct'):
        notes.append('No death records are received: death cannot be a competing event or a censoring reason, so risks are conditional on survival being invisible.')
    if o['type'] == 'admission':
        notes.append('Admissions are all-cause: stays are not linked to a discharge diagnosis, so a cause-specific (e.g. heart-failure) admission outcome is approximated.')
    if n_reversed: notes.append(f'{n_reversed} reversed / returned-to-stock fills were excluded (counts_as_fill).')
    itt = analyses.get('intention_to_treat') or {}
    inference = ('not_supported' if blocking or itt.get('status') != 'estimated' else
                 'limited' if cautions else 'supported_under_assumptions')
    report = {'schema': VERSION, 'parameters': p, 'as_of': as_of, 'arms': arms, 'arm_labels': [label(x) for x in arms],
              'outcome_label': describe_outcome(o), 'fill_feed_start': _iso(feed) if feed else None,
              'eligibility_funnel': dict(funnel), 'sample': {arms[0]: n1, arms[1]: n0},
              'protocol': protocol(p, feed), 'propensity': ps_summary, 'balance': balance, 'analyses': analyses,
              'negative_control_definition': nc, 'warnings': blocking + cautions + notes, 'blocking_warnings': blocking, 'cautions': cautions,
              'assumptions': ASSUMPTIONS, 'inference': inference}
    report['reading'] = reading(report)
    report['rows'] = result_rows(report)
    report['method'] = method_text(p)
    return report


def covariate_matrix(cohort, labs, adm, ed, p, o):
    names = ['age_at_time_zero', 'female', 'payer_medicare', 'payer_medicaid', 'time_zero_year']
    present = [c for c in BASELINE_CONDITIONS if 0 < sum(c in x['conds'] for x in cohort) < len(cohort)]
    names += [f'has_{c}' for c in present] + ['admissions_prior_year', 'ed_visits_prior_year']
    lab_list = list(BASELINE_LABS) + ([o['lab']] if 'lab' in o and o['lab'] not in BASELINE_LABS else [])
    lab_vals = {}
    for lab in lab_list:
        vals = []
        for c in cohort:
            r = _last_before(labs.get(lab, {}).get(c['pid']), c['t0'], c['t0'] - p['baseline_days'])
            vals.append(r[1] if r else None)
        if sum(v is not None for v in vals) >= 2: lab_vals[lab] = vals
    for lab in lab_vals: names += [f'baseline_{lab}', f'missing_{lab}']
    X = []
    for i, c in enumerate(cohort):
        row = [c['age'], 1.0 if str(c['sex'] or '').lower().startswith('f') else 0.0, 1.0 if (c['cov'] or '') == 'medicare' else 0.0,
               1.0 if (c['cov'] or '') == 'medicaid' else 0.0, date.fromordinal(c['t0']).year + date.fromordinal(c['t0']).timetuple().tm_yday / 366]
        row += [1.0 if cc in c['conds'] else 0.0 for cc in present]
        t0 = c['t0']
        row += [float(sum(1 for d in adm.get(c['pid'], []) if t0 - 365 <= d < t0)), float(sum(1 for d in ed.get(c['pid'], []) if t0 - 365 <= d < t0))]
        for lab, vals in lab_vals.items():
            obs = [v for v in vals if v is not None]
            mean = float(np.mean(obs))
            row += [vals[i] if vals[i] is not None else mean, 0.0 if vals[i] is not None else 1.0]
        X.append(row)
    X = np.array(X, float) if X else np.zeros((0, len(names)))
    keepcols = [j for j in range(X.shape[1]) if X.shape[0] and X[:, j].std() > 0]
    return [names[j] for j in keepcols], X[:, keepcols] if X.shape[0] else X


def estimate(cohort, X, a, outcomes, o, p, rng):
    """Weighted estimate with bootstrap CI (propensity model re-estimated in every draw)."""
    idx = [i for i, y in enumerate(outcomes) if y is not None]
    label_ = describe_outcome(o) if o['type'] != 'pre_period_change' else f"pre-period change in {o['lab']} (year before time zero)"
    if not idx: return {'status': 'not_estimable', 'reason': 'no patient has a measurable outcome', 'outcome': label_}
    kind = outcomes[idx[0]][0]
    obs = np.array([y is not None for y in outcomes], float)
    n1 = sum(1 for i in idx if a[i] == 1); n0 = len(idx) - n1
    if n1 < 5 or n0 < 5:
        return {'status': 'not_estimable', 'reason': f'outcome measured for only {n1} / {n0} patients per arm', 'outcome': label_, 'analysed': len(idx)}
    horizon = o.get('horizon_days', 365)

    def point(rows_idx, Xb, ab, obsb, ys, seed):
        ps = logistic(Xb, ab)
        w = ps_weights(ps, ab, p['adjustment'], p['caliper'], np.random.default_rng(seed))
        # ascertainment weights: P(outcome observed | baseline covariates, arm)
        if obsb.min() < 1:
            pr = logistic(np.hstack([Xb, ab[:, None]]), obsb)
            w = w / np.clip(pr, 0.05, 1)
        sel = obsb == 1
        wa, aa = w[sel], ab[sel]
        if wa[aa == 1].sum() <= 0 or wa[aa == 0].sum() <= 0: return None
        if kind == 'value':
            y = np.array([v[1] for v in ys])
            m1 = np.average(y[aa == 1], weights=wa[aa == 1]); m0 = np.average(y[aa == 0], weights=wa[aa == 0])
            return {'difference': m1 - m0, 'mean_treated': m1, 'mean_comparator': m0, 'sd': float(y.std(ddof=1)) if len(y) > 1 else 0.0}
        tt = np.array([v[1] for v in ys], float); ee = np.array([v[2] for v in ys], int)
        tt = np.maximum(tt, 0.5)
        r1, _ = weighted_km(tt[aa == 1], ee[aa == 1], wa[aa == 1], horizon)
        r0, _ = weighted_km(tt[aa == 0], ee[aa == 0], wa[aa == 0], horizon)
        b = weighted_cox(tt, ee, aa, wa)
        return {'risk_treated': r1, 'risk_comparator': r0, 'risk_difference': r1 - r0, 'risk_ratio': r1 / r0 if r0 > 0 else None,
                'hazard_ratio': math.exp(b) if b is not None else None}
    ys = [outcomes[i] for i in idx]
    base = point(idx, X, a, obs, ys, p['seed'])
    if base is None: return {'status': 'not_estimable', 'reason': 'weights collapse in one arm', 'outcome': label_, 'analysed': len(idx)}
    n = len(cohort); draws = []
    arm1 = np.flatnonzero(a == 1); arm0 = np.flatnonzero(a == 0)
    for b in range(p['bootstrap']):
        pick = np.concatenate([rng.choice(arm1, len(arm1)), rng.choice(arm0, len(arm0))])
        ob = obs[pick]
        if ob[a[pick] == 1].sum() < 2 or ob[a[pick] == 0].sum() < 2: continue
        r = point(pick, X[pick], a[pick], ob, [outcomes[i] for i in pick if outcomes[i] is not None], p['seed'] + b + 1)
        if r: draws.append(r)
    out = {'status': 'estimated', 'outcome': label_, 'analysed': len(idx), 'analysed_by_arm': [n1, n0], 'bootstrap_draws': len(draws)}
    q = lambda key: [float(x) for x in np.quantile([d[key] for d in draws if d.get(key) is not None], [0.025, 0.975])] \
        if sum(d.get(key) is not None for d in draws) >= 20 else [None, None]
    if kind == 'value':
        y = np.array([v[1] for v in ys])
        out.update(scale='mean_difference', estimate=_r(base['difference']), ci95=[_r(x) for x in q('difference')],
                   mean_change_treated=_r(base['mean_treated']), mean_change_comparator=_r(base['mean_comparator']),
                   unadjusted_difference=_r(float(y[[a[i] == 1 for i in idx]].mean() - y[[a[i] == 0 for i in idx]].mean())))
        sd = base['sd'] or 1.0
        d = base['difference'] / sd
        rr = math.exp(0.91 * d)
        lo, hi = out['ci95']
        out['e_value'] = _r(e_value(rr))
        out['e_value_ci'] = _r(e_value_ci(math.exp(0.91 * lo / sd), math.exp(0.91 * hi / sd))) if lo is not None else None
        out['e_value_note'] = 'Standardised difference d = estimate / SD of the outcome converted to an approximate risk ratio exp(0.91 d) (VanderWeele 2017).'
        return out
    ev = int(sum(v[2] for v in ys))
    rd_ci, rr_ci, hr_ci = q('risk_difference'), q('risk_ratio'), q('hazard_ratio')
    out.update(scale='hazard_ratio', estimate=_r(base['hazard_ratio']), ci95=[_r(x) for x in hr_ci], events=ev,
               events_by_arm=[int(sum(v[2] for v, i in zip(ys, idx) if a[i] == 1)), int(sum(v[2] for v, i in zip(ys, idx) if a[i] == 0))],
               risk_treated=_r(base['risk_treated']), risk_comparator=_r(base['risk_comparator']),
               risk_difference=_r(base['risk_difference']), risk_difference_ci95=[_r(x) for x in rd_ci],
               risk_ratio=_r(base['risk_ratio']), risk_ratio_ci95=[_r(x) for x in rr_ci],
               e_value=_r(e_value(base['risk_ratio'])), e_value_ci=_r(e_value_ci(*rr_ci)) if rr_ci[0] is not None else None,
               e_value_note='E-value on the risk ratio at the horizon (VanderWeele & Ding 2017).')
    if out['estimate'] is None: out['ci95'] = [None, None]
    tt = np.maximum(np.array([v[1] for v in ys], float), 0.5); ee = np.array([v[2] for v in ys], int); aa = np.array([a[i] for i in idx])
    ps = logistic(X, a); w = ps_weights(ps, a, p['adjustment'], p['caliper'], np.random.default_rng(p['seed']))[idx]
    out['curves'] = {'treated': weighted_km(tt[aa == 1], ee[aa == 1], w[aa == 1], horizon)[1][:300],
                     'comparator': weighted_km(tt[aa == 0], ee[aa == 0], w[aa == 0], horizon)[1][:300]}
    return out


def protocol(p, feed):
    a, b = (label(x) for x in p['arms'])
    o = p['outcome']
    elig = [f"age {p['min_age']}-{p['max_age']} at time zero"]
    if p['population_conditions']: elig.append('documented ' + ', '.join(c.replace('_', ' ') for c in p['population_conditions']) + ' by time zero')
    if p['exclude_conditions']: elig.append('no ' + ', '.join(c.replace('_', ' ') for c in p['exclude_conditions']) + ' before time zero')
    elig.append(f"no fill of {_strategy_class(p['arms'][0]).replace('_', ' ')}" + (f" or {_strategy_class(p['arms'][1]).replace('_', ' ')}"
                if _strategy_class(p['arms'][1]) != _strategy_class(p['arms'][0]) else '') + f" in the {p['washout_days']} days before time zero (new users)")
    if 'lab' in o: elig.append(f"a baseline {o['lab']} result within {p['baseline_days']} days before time zero")
    return [
        {'component': 'Eligibility', 'target_trial': '; '.join(elig),
         'emulation': f"Same criteria from received records; time zero at least {p['washout_days']} days after the fill feed starts "
                      f"({_iso(feed) if feed else 'no fills'}) so prior use is observable."},
        {'component': 'Treatment strategies', 'target_trial': f'Initiate {a} vs initiate {b} (active comparator)',
         'emulation': 'First received fill (dispense, paid pharmacy or payer claim; reversals excluded)' +
                      ('; statin intensity from ingredient and strength (ACC/AHA 2018)' if p['arms'][0] in INTENSITY_STRATEGIES else '')},
        {'component': 'Assignment', 'target_trial': 'Randomised at time zero',
         'emulation': f"Strategy filled at time zero; confounding addressed by {p['adjustment']} propensity-score weighting"
                      + (' (1:1 caliper matching)' if p['adjustment'] == 'matching' else '') + ' on baseline covariates; same-day dual initiators excluded'},
        {'component': 'Time zero', 'target_trial': 'Randomisation = treatment start = start of follow-up',
         'emulation': 'First qualifying fill: eligibility, assignment and follow-up start on the same day (immortal-time protection: no '
                      'post-time-zero information decides eligibility or arm)'},
        {'component': 'Follow-up', 'target_trial': f"From time zero to the outcome or {o['horizon_days']} days",
         'emulation': f"Same, also ended by the snapshot; per-protocol censors at switching to the other strategy or a supply lapse > {p['grace_days']} days"},
        {'component': 'Outcome', 'target_trial': describe_outcome(o),
         'emulation': {'lab_change': f"Result closest to day {o['horizon_days']} within +/- {o.get('window_days')} days minus the last result before time zero",
                       'admission': 'Hospital stays (all-cause; stays carry no linked diagnosis)',
                       'ed_visit': 'Governed all-source ED event rows; older snapshots without that relation use the all-source patient-day definition',
                       'condition': 'First documented record after time zero; patients with an earlier record are not at risk',
                       'encounter': 'First encounter of the concept after time zero'}.get(o['type'], 'First result past the threshold after time zero '
                                                                                                   '(detected only when tested)')},
        {'component': 'Causal contrasts', 'target_trial': 'Intention-to-treat and per-protocol effects',
         'emulation': 'Observational analogues: as initiated (ITT) and censoring at deviation (per-protocol, baseline-covariate weights only)'},
        {'component': 'Analysis', 'target_trial': 'Compare outcomes between randomised arms',
         'emulation': ('Weighted mean difference of the change' if o['type'] == 'lab_change' else 'Weighted Kaplan-Meier risks at the horizon and weighted Cox hazard ratio')
                      + f"; 95% CI from {p['bootstrap']} bootstrap draws re-fitting the propensity model; E-value; negative-control outcome"},
    ]


def result_rows(r):
    rows = []
    for key in ('intention_to_treat', 'per_protocol', 'negative_control'):
        x = r['analyses'].get(key)
        if not x: continue
        rows.append({'analysis': key, 'outcome': x.get('outcome'), 'scale': x.get('scale'), 'estimate': x.get('estimate'),
                     'ci95': x.get('ci95'), 'analysed': x.get('analysed'), 'events': x.get('events'),
                     'risk_treated': x.get('risk_treated'), 'risk_comparator': x.get('risk_comparator'),
                     'risk_difference': x.get('risk_difference'), 'e_value': x.get('e_value'), 'status': x.get('status'),
                     **({'reason': x['reason']} if x.get('reason') else {})})
    return rows


def reading(r):
    a, b = r['arm_labels']
    itt = r['analyses'].get('intention_to_treat') or {}
    n = r['sample']
    head = f"Target trial emulation, initiating {a} vs {b} ({n[r['arms'][0]]} vs {n[r['arms'][1]]} eligible new users) on {r['outcome_label']}: "
    if r['inference'] == 'not_supported' or itt.get('status') != 'estimated':
        return head + 'the received data cannot support an estimate: ' + (r['blocking_warnings'][0] if r['blocking_warnings'] else itt.get('reason', 'no analysable outcome')) + \
            ' Synthetic data; no causal claim.'
    lo, hi = itt['ci95']
    ci = f"95% CI {lo:.3g} to {hi:.3g}" if lo is not None else 'interval not estimable'
    if itt['scale'] == 'mean_difference':
        main = f"intention-to-treat difference in mean change {itt['estimate']:+.3g} ({ci})"
    else:
        main = (f"intention-to-treat hazard ratio {itt['estimate']:.3g} ({ci}); {itt['risk_treated']:.1%} vs {itt['risk_comparator']:.1%} "
                f"risk at the horizon" if itt['estimate'] is not None else
                f"risk {itt['risk_treated']:.1%} vs {itt['risk_comparator']:.1%} at the horizon (hazard ratio not estimable)")
    pp = r['analyses'].get('per_protocol') or {}
    if pp.get('status') == 'estimated' and pp.get('estimate') is not None:
        main += f"; per-protocol {pp['estimate']:+.3g}" if pp['scale'] == 'mean_difference' else f"; per-protocol hazard ratio {pp['estimate']:.3g}"
    if itt.get('e_value') is not None: main += f"; E-value {itt['e_value']:.2f}"
    nc = r['analyses'].get('negative_control') or {}
    if nc.get('status') == 'estimated':
        main += '; negative control ' + ('consistent with null' if nc.get('assumed_null_consistent') else 'NOT null (bias likely)')
    k = len(r['cautions'])
    return (head + main + f". Estimate under the emulated protocol and its assumptions ({r['parameters']['adjustment']} propensity weighting), "
            f"not a randomised or identified causal effect; synthetic data." + (f' {k} caution(s) limit this estimate.' if k > 0 else ''))


def method_text(p):
    return ('Target trial emulation (Hernan & Robins): explicit protocol (eligibility, strategies, assignment, time zero, follow-up, '
            'outcome, contrasts) emulated with a new-user active-comparator design; time zero = first qualifying fill so eligibility, '
            f"assignment and follow-up start coincide (no immortal time). Ridge logistic propensity score on baseline covariates, {p['adjustment']} "
            f"weighting, SMD balance; ITT and per-protocol (censoring at switch or supply lapse > {p['grace_days']} days); outcome ascertainment "
            f"weights; bootstrap ({p['bootstrap']} draws, propensity re-fitted per draw); E-value; assumed-null negative-control outcome. "
            'Observational estimate under stated assumptions on synthetic data, never a randomised or identified causal effect.')


def summarize(report):
    return report['reading'], report['rows']


def _quant(v):
    if not len(v): return None
    return {k: round(float(x), 4) for k, x in zip(('min', 'p25', 'median', 'p75', 'max'), np.quantile(v, [0, 0.25, 0.5, 0.75, 1]))}


def _hist(ps, a, bins=10):
    edges = np.linspace(0, 1, bins + 1)
    return {'edges': [round(float(x), 2) for x in edges], 'treated': np.histogram(ps[a == 1], edges)[0].tolist(),
            'comparator': np.histogram(ps[a == 0], edges)[0].tolist()}


def _ess(w):
    w = np.asarray(w, float)
    return round(float(w.sum() ** 2 / (w ** 2).sum()), 1) if w.sum() > 0 else 0.0


def _r(v):
    if v is None: return None
    if isinstance(v, (float, np.floating)): return round(float(v), 4) if math.isfinite(v) else None
    return v
