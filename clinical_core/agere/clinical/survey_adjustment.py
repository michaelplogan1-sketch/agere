"""Survey weighting / adjustment: nonresponse-raked population estimates of survey items, CAHPS-style patient-mix adjusted
group comparisons and key-driver analysis over the survey streams received in the warehouse.

Survey streams (record.payload) and items (respondent-level 0/1 outcome; most recent response per patient in the window):
  health_risk_assessments  general_health (top box = excellent / very good / good), fair_poor_health, falls (>= 1 in past
                           year), falls_2plus, loneliness, pain_limits (pain limits activity), high_stress, phq2_positive
                           (PHQ-2 total >= 3, Kroenke 2003), food_access, hearing_concern, vision_concern, memory_concern,
                           adl_any / iadl_any (any assistance), inactive (little regular activity or activity limited)
  mood_questionnaires      phq9_moderate (PHQ-9 total >= 10, Kroenke 2001)
  social_screens           food_insecurity, transport_barrier, cost_of_care, resource_request, hrsn_positive (AHC HRSN any
                           positive domain)
  financial_screens        medical_bill_difficulty
Eligible population (the frame the respondents should represent): patients in scope, restricted by `eligible`:
  auto  = HRA and PHQ-9 -> adults 18+ (HRAs are received across every line of business here, not only the Medicare annual
          wellness visit of 42 CFR 410.15); social and financial screens -> everyone in scope (parents answer for children).
  adult | age65 | medicare (Medicare / dual / Medigap line of business) | all.
Weighting: base weight N_eligible / n_respondents, then raking (iterative proportional fitting, Deming-Stephan 1940) to the
eligible population's margins on age band, sex, line of business and dual status; a margin category with eligible people but
no respondents is collapsed into the remaining categories (stated); weights trimmed at `trim` x the median weight and re-raked
(trimmed raking, up to 5 rounds). Kish design effect deff = n sum(w^2) / (sum w)^2, effective n = n / deff; 95% CI
p +/- 1.96 sqrt(p(1-p)/n_eff). Unweighted estimates are reported beside the weighted ones.
Group comparison (group_by practice | payer | line_of_business): weighted least squares of the item on group indicators plus
case-mix adjusters (age band, sex, dual, and self-rated general health when it is not the item and is available), the
CAHPS patient-mix adjustment approach (AHRQ CAHPS Health Plan Survey case-mix adjustment; Zaslavsky et al. 2001); adjusted
group score = group effect evaluated at the overall respondent mix.
Key drivers: for an outcome item (default general_health), Pearson correlation and standardized (beta) WLS coefficient of
every other item of the same respondents, ranked by |beta|. Association, not cause.
"""
from collections import Counter, defaultdict
from datetime import date
import json
import math
import re

import numpy as np

from . import revenue_common as rc

VERSION = 'survey-adjustment/1'
OP = 'survey_adjustment'
POSITIVE_TRUE = lambda k: (lambda d: None if d.get(k) is None else int(bool(d.get(k))))


def _gh(d, top):
    v = (d.get('general_health_reported') or '').lower().replace(' ', '_')
    if not v or v in ('not_recorded', 'unknown'): return None
    return int(v in ('excellent', 'very_good', 'good')) if top else int(v in ('fair', 'poor'))


def _num(k, cut):
    def f(d):
        v = d.get(k)
        return None if not isinstance(v, (int, float)) or isinstance(v, bool) else int(v >= cut)
    return f


def _any_true(k):
    def f(d):
        v = d.get(k)
        return None if not isinstance(v, dict) else int(any(bool(x) for x in v.values()))
    return f


def _eq(k, values):
    def f(d):
        v = d.get(k)
        return None if v in (None, '', 'not_quantified', 'not_recorded') else int(v in values)
    return f


def _food(d):
    if 'food_difficulty_reported' in d: return int(bool(d['food_difficulty_reported']))
    if isinstance(d.get('positive_domains'), list): return int(any(str(x).startswith('food') for x in d['positive_domains']))
    return None


def _transport(d):
    if 'transport_difficulty_reported' in d: return int(bool(d['transport_difficulty_reported']))
    if 'transport' in d: return int(d['transport'] == 'unreliable')
    if isinstance(d.get('positive_domains'), list): return int('transportation_barrier' in d['positive_domains'])
    return None


def _hrsn(d):
    return int(bool(d['positive_domains'])) if isinstance(d.get('positive_domains'), list) else None


# item -> (stream, extractor, label)
ITEMS = {
    'general_health': ('health_risk_assessments', lambda d: _gh(d, True), 'self-rated health excellent / very good / good (top box)'),
    'fair_poor_health': ('health_risk_assessments', lambda d: _gh(d, False), 'self-rated health fair or poor'),
    'falls': ('health_risk_assessments', _num('falls_past_year_reported', 1), 'one or more falls in the past year'),
    'falls_2plus': ('health_risk_assessments', _num('falls_past_year_reported', 2), 'two or more falls in the past year'),
    'loneliness': ('health_risk_assessments', POSITIVE_TRUE('loneliness_reported'), 'reports loneliness'),
    'pain_limits': ('health_risk_assessments', _eq('pain_reported', ('limits_activity',)), 'pain limits activity'),
    'high_stress': ('health_risk_assessments', _eq('stress_reported', ('high',)), 'high stress'),
    'phq2_positive': ('health_risk_assessments', _num('phq2_total', 3), 'PHQ-2 positive (total >= 3)'),
    'food_access': ('health_risk_assessments', POSITIVE_TRUE('food_access_difficulty_reported'), 'difficulty accessing food'),
    'hearing_concern': ('health_risk_assessments', POSITIVE_TRUE('hearing_concern_reported'), 'hearing concern'),
    'vision_concern': ('health_risk_assessments', POSITIVE_TRUE('vision_concern_reported'), 'vision concern'),
    'memory_concern': ('health_risk_assessments', POSITIVE_TRUE('memory_concern_reported'), 'memory concern'),
    'adl_any': ('health_risk_assessments', _any_true('adl_assistance_reported'), 'needs help with any ADL'),
    'iadl_any': ('health_risk_assessments', _any_true('iadl_assistance_reported'), 'needs help with any IADL'),
    'inactive': ('health_risk_assessments', _eq('physical_activity_reported', ('little regular activity', 'activity limited by symptoms')),
                 'little regular physical activity or activity limited by symptoms'),
    'phq9_moderate': ('mood_questionnaires', _num('total', 10), 'PHQ-9 total >= 10 (moderate or worse depression)'),
    'food_insecurity': ('social_screens', _food, 'food insecurity reported'),
    'transport_barrier': ('social_screens', _transport, 'transportation barrier'),
    'cost_of_care': ('social_screens', POSITIVE_TRUE('difficulty_paying_for_care_reported'), 'difficulty paying for care'),
    'resource_request': ('social_screens', POSITIVE_TRUE('requests_resource_contact'), 'requests a resource contact'),
    'hrsn_positive': ('social_screens', _hrsn, 'any positive AHC HRSN domain'),
    'medical_bill_difficulty': ('financial_screens', POSITIVE_TRUE('difficulty_paying_medical_bills'), 'difficulty paying medical bills'),
}
STREAMS = tuple(dict.fromkeys(v[0] for v in ITEMS.values()))
ELIGIBLE = ('auto', 'all', 'adult', 'age65', 'medicare')
GROUP_BY = ('none', 'practice', 'payer', 'line_of_business')
MARGINS = ('age_band', 'sex', 'line_of_business', 'dual')
AGE_BANDS = ((0, 17, '0-17'), (18, 44, '18-44'), (45, 64, '45-64'), (65, 74, '65-74'), (75, 84, '75-84'), (85, 200, '85+'))
MEDICARE_LOB = ('medicare', 'dual', 'medigap')
FAMILIES = (('general_health', 'fair_poor_health'), ('falls', 'falls_2plus'))  # items read from the same answer
REDUNDANT = ('fair_poor_health', 'falls_2plus')  # never a driver beside its family's primary item

PRODUCT = {'population': 'optional', 'view': 'table',
           'use': 'SURVEY WEIGHTING / ADJUSTMENT: population estimates of survey items (HRA self-rated health, falls, loneliness, '
                  'pain, PHQ-2/PHQ-9, social and financial screens) raked to the eligible population (age, sex, line of business, dual) '
                  'with design effect and CI; CAHPS-style patient-mix adjusted comparisons by practice / payer / line of business; key drivers.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'item': 'general_health|falls|loneliness|phq2_positive|...', 'eligible': 'auto|all|adult|age65|medicare',
                           'group_by': 'none|practice|payer|line_of_business', 'months': 24, 'trim': 5.0, 'drivers': True,
                           'driver_outcome': 'general_health', 'min_group': 10},
            'meaning': 'Most recent survey response per patient in the window -> 0/1 item; respondents raked (IPF) to the eligible '
                       'population margins (age band, sex, line of business, dual), trimmed weights, Kish deff / effective n, weighted '
                       'estimate with 95% CI beside the unweighted; group_by -> WLS case-mix (patient-mix) adjusted group scores; key '
                       'drivers = standardized coefficients of the other items on the driver outcome.'}
NATIVE = ('value',)


def schema(obj, string, array):
    return obj({'item': string(tuple(ITEMS)), 'eligible': string(ELIGIBLE), 'group_by': string(GROUP_BY),
                'months': {'type': 'integer', 'minimum': 1, 'maximum': 120, 'description': 'Survey window in months ending at the as-of date'},
                'trim': {'type': 'number', 'minimum': 1.5, 'maximum': 50, 'description': 'Trim weights at this multiple of the median weight'},
                'drivers': {'type': 'boolean', 'description': 'Run key-driver analysis'},
                'driver_outcome': string(tuple(ITEMS)),
                'min_group': {'type': 'integer', 'minimum': 1, 'maximum': 1000, 'description': 'Minimum respondents to report a group'}})


def validate(p):
    allowed = {'item', 'eligible', 'group_by', 'months', 'trim', 'drivers', 'driver_outcome', 'min_group'}
    if not isinstance(p, dict) or set(p) - allowed: raise ValueError('survey_adjustment parameters: ' + ', '.join(sorted(allowed)))
    out = {'item': p.get('item') or 'general_health', 'eligible': p.get('eligible') or 'auto', 'group_by': p.get('group_by') or 'none',
           'months': p.get('months', 24), 'trim': float(p.get('trim', 5.0)), 'drivers': p.get('drivers', True),
           'driver_outcome': p.get('driver_outcome') or None, 'min_group': p.get('min_group', 10)}
    if out['item'] not in ITEMS: raise ValueError(f'item: one of {tuple(ITEMS)}')
    if out['driver_outcome'] is not None and out['driver_outcome'] not in ITEMS: raise ValueError('driver_outcome: an item')
    if out['eligible'] not in ELIGIBLE: raise ValueError(f'eligible: {ELIGIBLE}')
    if out['group_by'] not in GROUP_BY: raise ValueError(f'group_by: {GROUP_BY}')
    if type(out['months']) is not int or not 1 <= out['months'] <= 120: raise ValueError('months: 1..120')
    if not math.isfinite(out['trim']) or not 1.5 <= out['trim'] <= 50: raise ValueError('trim: 1.5..50')
    if type(out['drivers']) is not bool: raise ValueError('drivers: true|false')
    if type(out['min_group']) is not int or out['min_group'] < 1: raise ValueError('min_group: positive integer')
    return out


# ---- data -------------------------------------------------------------------------------------------------------------
def age_band(age):
    if age is None: return 'unknown'
    for lo, hi, label in AGE_BANDS:
        if lo <= age <= hi: return label
    return 'unknown'


def _age(birth, as_of, fallback):
    try:
        b = date.fromisoformat(str(birth)[:10]); a = date.fromisoformat(as_of)
        return a.year - b.year - ((a.month, a.day) < (b.month, b.day))
    except (TypeError, ValueError):
        return fallback


def people(db, keep, as_of):
    cols = rc.columns(db, 'patient')
    sel = ['patient_id', 'birth_date' if 'birth_date' in cols else 'NULL', 'age_latest' if 'age_latest' in cols else 'NULL',
           'sex_reported' if 'sex_reported' in cols else 'NULL', 'line_of_business' if 'line_of_business' in cols else 'NULL',
           'pcp_practice_id' if 'pcp_practice_id' in cols else 'NULL', 'payer_id' if 'payer_id' in cols else 'NULL']
    dual = set()
    if 'enrollment_span' in rc.tables(db) and 'dual_status' in rc.columns(db, 'enrollment_span'):
        dual = {r[0] for r in db.execute("SELECT DISTINCT patient_id FROM enrollment_span WHERE COALESCE(dual_status,'') <> ''")}
    out = {}
    for pid, birth, age_l, sex, lob, practice, payer in db.execute(f"SELECT {', '.join(sel)} FROM patient"):
        if keep is not None and pid not in keep: continue
        age = _age(birth, as_of, age_l)
        lob = (lob or 'unknown') or 'unknown'
        out[pid] = {'age': age, 'age_band': age_band(age), 'sex': sex or 'unknown', 'line_of_business': lob,
                    'dual': 'dual' if (pid in dual or lob == 'dual') else 'not_dual', 'practice': practice or 'unattributed',
                    'payer': payer or 'unknown'}
    return out


def responses(db, stream, start, end, keep):
    """{patient_id: (event_date, record_id, payload)} most recent response in [start, end]."""
    out = {}
    for pid, rid, d, text in db.execute("SELECT patient_id, record_id, event_date, payload FROM record WHERE stream=? AND event_date BETWEEN ? AND ?",
                                        (stream, start, end)):
        if keep is not None and pid not in keep: continue
        if pid not in out or (d or '') > out[pid][0]:
            out[pid] = (d, rid, rc.payload(text))
    return out


def eligible_frame(pop, stream, mode):
    if mode == 'auto': mode = 'adult' if stream in ('health_risk_assessments', 'mood_questionnaires') else 'all'
    if mode == 'all': return dict(pop), 'everyone in scope'
    if mode == 'adult': return {k: v for k, v in pop.items() if (v['age'] or 0) >= 18}, 'adults 18+ in scope'
    if mode == 'age65': return {k: v for k, v in pop.items() if (v['age'] or 0) >= 65}, 'age 65 or older'
    if mode == 'medicare': return {k: v for k, v in pop.items() if v['line_of_business'] in MEDICARE_LOB}, 'Medicare / dual / Medigap line of business'
    raise ValueError(f'eligible: {ELIGIBLE}')


# ---- weighting --------------------------------------------------------------------------------------------------------
def rake(resp_cats, targets, base, iters=200, tol=1e-8, start=None):
    """IPF: resp_cats = list of dicts {margin: category}; targets = {margin: {category: population count}}; start = starting
    weights (default `base` each). Returns weights (np.array) and the collapsed categories {margin: [category, ...]}."""
    w = np.full(len(resp_cats), float(base)) if start is None else np.array(start, dtype=float)
    collapsed = {}
    tg = {}
    total = sum(next(iter(targets.values())).values()) if targets else 0
    for m, t in targets.items():
        present = {r[m] for r in resp_cats}
        missing = [c for c, n in t.items() if n and c not in present]
        if missing: collapsed[m] = sorted(missing, key=str)
        kept = {c: n for c, n in t.items() if c in present}
        s = sum(kept.values())
        tg[m] = {c: n * total / s for c, n in kept.items()} if s else {}
    idx = {m: defaultdict(list) for m in tg}
    for i, r in enumerate(resp_cats):
        for m in tg: idx[m][r[m]].append(i)
    for _ in range(iters):
        delta = 0.0
        for m, t in tg.items():
            for c, rows in idx[m].items():
                cur = w[rows].sum()
                if cur <= 0 or c not in t: continue
                f = t[c] / cur
                delta = max(delta, abs(f - 1)); w[rows] *= f
        if delta < tol: break
    return w, collapsed


def trimmed_rake(resp_cats, targets, base, trim, rounds=5):
    """Rake, cap weights at trim x median, re-rake from the capped weights; final weights respect the cap."""
    w, collapsed = rake(resp_cats, targets, base)
    trimmed = 0
    for _ in range(rounds):
        cap = trim * float(np.median(w))
        over = w > cap * (1 + 1e-9)
        if not over.any(): break
        trimmed = max(trimmed, int(over.sum()))
        w, _ = rake(resp_cats, targets, base, iters=50, start=np.minimum(w, cap))
    cap = trim * float(np.median(w))
    return np.minimum(w, cap), collapsed, trimmed


def kish(w):
    w = np.asarray(w, dtype=float)
    if not len(w) or w.sum() <= 0: return 1.0, 0.0
    deff = len(w) * float((w ** 2).sum()) / float(w.sum()) ** 2
    return deff, len(w) / deff


def estimate(y, w):
    y = np.asarray(y, dtype=float); w = np.asarray(w, dtype=float)
    p = float((w * y).sum() / w.sum())
    deff, neff = kish(w)
    se = math.sqrt(max(p * (1 - p), 0) / neff) if neff else float('nan')
    pu = float(y.mean()); seu = math.sqrt(pu * (1 - pu) / len(y))
    return {'weighted': round(p, 4), 'ci95': [round(max(0, p - 1.96 * se), 4), round(min(1, p + 1.96 * se), 4)],
            'unweighted': round(pu, 4), 'unweighted_ci95': [round(max(0, pu - 1.96 * seu), 4), round(min(1, pu + 1.96 * seu), 4)],
            'design_effect': round(deff, 3), 'effective_n': round(neff, 1), 'respondents': len(y)}


def wls(X, y, w):
    W = np.asarray(w, dtype=float)
    XtW = X.T * W
    beta = np.linalg.lstsq(XtW @ X, XtW @ y, rcond=None)[0]
    return beta


def _dummies(values, levels):
    return np.array([[1.0 if v == l else 0.0 for l in levels] for v in values]) if levels else np.zeros((len(values), 0))


def case_mix(rows, y, w, group_key, adjusters, min_group):
    """CAHPS-style patient-mix adjustment: WLS of y on group + adjuster dummies; adjusted group mean at the overall mix."""
    groups = Counter(r[group_key] for r in rows)
    keep = [g for g, n in groups.items() if n >= min_group]
    if len(keep) < 1: return [], []
    sel = [i for i, r in enumerate(rows) if r[group_key] in keep]
    rs = [rows[i] for i in sel]; ys = np.asarray([y[i] for i in sel], dtype=float); ws = np.asarray([w[i] for i in sel], dtype=float)
    glevels = sorted(keep)
    G = _dummies([r[group_key] for r in rs], glevels)  # full group coding, no intercept
    A, used = [], []
    for a in adjusters:
        if a == group_key: continue
        levels = sorted({r[a] for r in rs})
        if len(levels) < 2: continue
        A.append(_dummies([r[a] for r in rs], levels[1:])); used.append(a)
    Am = np.hstack(A) if A else np.zeros((len(rs), 0))
    X = np.hstack([G, Am])
    beta = wls(X, ys, ws)
    mix = (Am * ws[:, None]).sum(0) / ws.sum() if Am.shape[1] else np.zeros(0)
    adj_part = float(mix @ beta[len(glevels):]) if Am.shape[1] else 0.0
    out = []
    for j, g in enumerate(glevels):
        m = G[:, j] == 1
        raw = float((ws[m] * ys[m]).sum() / ws[m].sum())
        _, neff = kish(ws[m])
        adjusted = min(1.0, max(0.0, float(beta[j]) + adj_part))
        se = math.sqrt(max(adjusted * (1 - adjusted), 1e-9) / neff) if neff else None
        out.append({'group': g, 'respondents': int(m.sum()), 'weighted_raw': round(raw, 4), 'patient_mix_adjusted': round(adjusted, 4),
                    'adjustment': round(adjusted - raw, 4), 'ci95': [round(max(0, adjusted - 1.96 * se), 4), round(min(1, adjusted + 1.96 * se), 4)] if se else None})
    overall = float((ws * ys).sum() / ws.sum())
    for o in out:
        o['vs_overall'] = round(o['patient_mix_adjusted'] - overall, 4)
    return sorted(out, key=lambda o: -o['patient_mix_adjusted']), used


def key_drivers(outcome_item, item_values, weights_by_pid):
    """Correlation and standardized WLS beta of each other item on the outcome item (same respondents)."""
    oy = item_values.get(outcome_item) or {}
    family = next((f for f in FAMILIES if outcome_item in f), (outcome_item,))
    others = [k for k in item_values if k not in family and k not in REDUNDANT and ITEMS[k][0] == ITEMS[outcome_item][0]]
    pids = [p for p in oy if all(p in item_values[k] for k in others)]
    others = [k for k in others if len({item_values[k][p] for p in pids}) > 1]
    if len(pids) < 10 or not others: return []
    y = np.array([oy[p] for p in pids], dtype=float); w = np.array([weights_by_pid.get(p, 1.0) for p in pids])
    X = np.array([[item_values[k][p] for k in others] for p in pids], dtype=float)
    mu = (X * w[:, None]).sum(0) / w.sum(); sd = np.sqrt(((X - mu) ** 2 * w[:, None]).sum(0) / w.sum())
    my = (y * w).sum() / w.sum(); sy = math.sqrt(((y - my) ** 2 * w).sum() / w.sum())
    if sy == 0: return []
    Z = (X - mu) / np.where(sd > 0, sd, 1); zy = (y - my) / sy
    beta = wls(np.hstack([np.ones((len(y), 1)), Z]), zy, w)[1:]
    rows = []
    for j, k in enumerate(others):
        r = float(((Z[:, j] * zy) * w).sum() / w.sum())
        rows.append({'driver': k, 'label': ITEMS[k][2], 'correlation': round(r, 3), 'standardized_beta': round(float(beta[j]), 3),
                     'prevalence_weighted': round(float((X[:, j] * w).sum() / w.sum()), 4)})
    return sorted(rows, key=lambda r: -abs(r['standardized_beta']))


def run(db, patient_ids, p, as_of):
    keep = set(patient_ids) if patient_ids is not None else None
    start, end = rc.window(as_of, p['months'])
    stream, extract, label = ITEMS[p['item']]
    pop = people(db, keep, as_of)
    frame, frame_label = eligible_frame(pop, stream, p['eligible'])
    resp = {pid: r for pid, r in responses(db, stream, start, end, keep).items() if pid in frame}
    answered = {pid: (r, extract(r[2])) for pid, r in resp.items()}
    answered = {pid: (r, v) for pid, (r, v) in answered.items() if v is not None}
    assumptions = [f'Synthetic warehouse; survey stream {stream}, most recent response per patient from {start} to {end}.',
                   f'Item {p["item"]}: {label}.', f'Eligible population: {frame_label} ({len(frame):,} patients in scope).',
                   'Nonresponse weights rake respondents to the eligible margins of age band, sex, line of business and dual status; '
                   'weighting corrects for those characteristics only, not for nonresponse related to the answer itself.',
                   f'Weights trimmed at {p["trim"]:g} x the median weight.']
    report = {'schema': VERSION, 'as_of': as_of, 'parameters': p, 'stream': stream, 'item_label': label, 'period': {'start': start, 'end': end},
              'eligible': len(frame), 'respondents': len(answered), 'response_rate': round(len(answered) / len(frame), 4) if frame else None,
              'assumptions': assumptions, 'method': METHOD}
    if not answered:
        report.update(status='answered', estimate=None, rows=[], margins=[], groups=[], drivers=[])
        assumptions.insert(0, f'No {stream} responses for eligible patients in the window: the item is unknown for this population.')
        return report
    pids = sorted(answered)
    cats = [{m: frame[pid][m] for m in MARGINS} for pid in pids]
    targets = {m: Counter(v[m] for v in frame.values()) for m in MARGINS}
    w, collapsed, trimmed = trimmed_rake(cats, targets, len(frame) / len(pids), p['trim'])
    y = [answered[pid][1] for pid in pids]
    est = estimate(y, w)
    if report['response_rate'] is not None and report['response_rate'] < 0.10:
        assumptions.append(f"Response rate {report['response_rate']:.1%}: the instrument reaches a selected group (e.g. PHQ-9 after a positive "
                           'screen); demographic weighting cannot correct selection into being surveyed, so the estimate describes those asked.')
    if collapsed:
        assumptions.append('Margin categories with eligible patients but no respondents were collapsed into the others: ' +
                           '; '.join(f"{m}: {', '.join(map(str, c))}" for m, c in collapsed.items()) + '.')
    if trimmed: assumptions.append(f'{trimmed} weights hit the trim cap.')
    margins = []
    for m in MARGINS:
        n_pop = targets[m]; n_resp = Counter(c[m] for c in cats)
        wsum = defaultdict(float)
        for c, wi in zip(cats, w): wsum[c[m]] += wi
        tot = sum(n_pop.values())
        for c in sorted(n_pop, key=str):
            margins.append({'margin': m, 'category': c, 'eligible_share': round(n_pop[c] / tot, 4), 'respondent_share': round(n_resp.get(c, 0) / len(pids), 4),
                            'weighted_share': round(float(wsum.get(c, 0)) / float(w.sum()), 4), 'response_rate': round(n_resp.get(c, 0) / n_pop[c], 4)})
    groups, used = [], []
    if p['group_by'] != 'none':
        rows = [{**frame[pid], 'general_health': str(_gh(answered[pid][0][2], True))} for pid in pids]
        adjusters = ['age_band', 'sex', 'dual'] + (['general_health'] if stream == 'health_risk_assessments' and p['item'] not in ('general_health', 'fair_poor_health') else [])
        if p['group_by'] != 'line_of_business': adjusters.append('line_of_business')
        groups, used = case_mix(rows, y, w, p['group_by'], adjusters, p['min_group'])
        assumptions.append(f"Patient-mix adjusters: {', '.join(used) or 'none varied'}; groups with fewer than {p['min_group']} respondents not reported.")
    drivers = []
    if p['drivers']:
        outcome = p['driver_outcome'] or ('general_health' if stream == 'health_risk_assessments' else p['item'])
        if ITEMS[outcome][0] == stream:
            item_values = {k: {pid: v for pid in pids if (v := ITEMS[k][1](answered[pid][0][2])) is not None}
                           for k in ITEMS if ITEMS[k][0] == stream}
            drivers = key_drivers(outcome, item_values, dict(zip(pids, w)))
            report['driver_outcome'] = outcome
    table = [{'patient_id': pid, 'record_id': answered[pid][0][1], 'response_date': answered[pid][0][0], 'value': answered[pid][1],
              'weight': round(float(wi), 3), **{m: frame[pid][m] for m in MARGINS}} for pid, wi in zip(pids, w)]
    report.update(status='answered', estimate=est, margins=margins, groups=groups, drivers=drivers, rows=table)
    return report


METHOD = ('Most recent survey response per patient in the window -> 0/1 item; raking (iterative proportional fitting) of respondents to the '
          'eligible population margins (age band, sex, line of business, dual status) with trimmed weights; Kish design effect and effective n; '
          'weighted estimate with 95% CI beside the unweighted; CAHPS-style patient-mix adjustment by weighted least squares with the group '
          'effect evaluated at the overall respondent mix; key drivers as standardized WLS coefficients and correlations.')


def execute(path, patient_ids, params, as_of):
    p = validate(params)
    with rc.connect(path) as db:
        return run(db, patient_ids, p, str(as_of)[:10])


def headline(r):
    e = r.get('estimate')
    lab = r['item_label']
    if not e: return f"Survey item '{lab}': no responses from the {r['eligible']:,} eligible patients in the window (unknown)"
    s = (f"{lab}: {e['weighted']:.1%} weighted (95% CI {e['ci95'][0]:.1%}-{e['ci95'][1]:.1%}) vs {e['unweighted']:.1%} unweighted; "
         f"{e['respondents']:,} respondents of {r['eligible']:,} eligible ({r['response_rate']:.0%} response), design effect {e['design_effect']:.2f}, "
         f"effective n {e['effective_n']:,.0f}")
    if r.get('groups'):
        g = r['groups']
        s += f"; patient-mix adjusted by {r['parameters']['group_by']}: highest {g[0]['group']} {g[0]['patient_mix_adjusted']:.1%}, lowest {g[-1]['group']} {g[-1]['patient_mix_adjusted']:.1%}"
    if r.get('drivers'):
        d = r['drivers'][0]
        s += f"; top driver of {r['driver_outcome']}: {d['driver']} (beta {d['standardized_beta']:+.2f})"
    return s


def answer(report, plan, question):
    table = report.get('groups') or report.get('rows') or []
    e = report.get('estimate')
    out = rc.answer(report, plan, value=headline(report) + '.', table=table, number=e['weighted'] if e else None,
                    definitions=[{'term': 'raking', 'basis': 'iterative proportional fitting of weights to population margins (Deming & Stephan 1940)'},
                                 {'term': 'design effect', 'basis': 'Kish: n sum(w^2) / (sum w)^2; effective n = n / deff'},
                                 {'term': 'patient-mix adjustment', 'basis': 'CAHPS case-mix adjustment: regression-adjusted group scores at a common respondent mix'}])
    out['overall'] = {'survey_adjustment': {k: report.get(k) for k in ('item_label', 'stream', 'eligible', 'respondents', 'response_rate',
                                                                        'estimate', 'margins', 'groups', 'drivers', 'driver_outcome', 'period')}}
    return out


# ---- free local-compiler route ----------------------------------------------------------------------------------------
SURVEY_RX = (r'\bsurveys?\b|\bhras?\b|\bhealth risk assessments?\b|\brespondents?\b|\bnon-?response\b|\bresponse bias\b|'
             r'\bsurvey[- ]weight\w*|\braking\b|\brak(?:e|ed)\b|\bpost-?stratif\w*|\bpatient[- ]mix adjust\w*|\bcase[- ]mix adjusted (?:survey|scores?)\b|'
             r'\btop[- ]box\b|\bkey drivers?\b|\bself[- ]rated health\b|\bself[- ]reported health\b|\bphq-?[29] (?:positivity|positive rate|screen results)\b')
ITEM_RX = [('fair_poor_health', r'\b(?:fair or poor|fair/poor|poor or fair)\b'), ('general_health', r'\bself[- ](?:rated|reported) health\b|\bgeneral health\b|\btop[- ]box\b'),
           ('falls_2plus', r'\b(?:two|2) or more falls\b'), ('falls', r'\bfall(?:s|ing)?\b'), ('loneliness', r'\blonel\w*|\bsocial isolation\b'),
           ('pain_limits', r'\bpain\b'), ('high_stress', r'\bstress\w*'), ('phq9_moderate', r'\bphq-?9\b'), ('phq2_positive', r'\bphq-?2\b|\bdepress\w*'),
           ('memory_concern', r'\bmemory\b|\bcogniti\w*'), ('hearing_concern', r'\bhearing\b'), ('vision_concern', r'\bvision\b|\bsight\b'),
           ('adl_any', r'\badls?\b|\bactivities of daily living\b'), ('iadl_any', r'\biadls?\b'), ('inactive', r'\bphysical activity\b|\binactiv\w*|\bexercise\b'),
           ('medical_bill_difficulty', r'\bmedical bills?\b'), ('cost_of_care', r'\bpaying for care\b|\bcost of care\b|\bafford\w*'),
           ('food_insecurity', r'\bfood insecur\w*|\bfood\b'), ('transport_barrier', r'\btransport\w*'), ('resource_request', r'\bresource contact\b|\brequest\w* (?:a )?resources?\b'),
           ('hrsn_positive', r'\bhrsn\b|\bhealth[- ]related social needs?\b|\bsocial needs? screen\w*')]


def route(question, profile, as_of):
    from .engine_suite import plan
    q = rc.low(question)
    if not re.search(SURVEY_RX, q): return None
    if rc.knowledge_only(q): return None
    if re.search(r'\b(?:send|mail|launch|design|write|draft)\b.{0,30}\bsurveys?\b', q): return None  # survey operations, not estimates
    params = {}
    item = next((k for k, rx in ITEM_RX if re.search(rx, q)), None)
    if item: params['item'] = item
    if re.search(r'\bby (?:pcp )?practices?\b|\bacross practices\b|\bper practice\b|\beach practice\b', q): params['group_by'] = 'practice'
    elif re.search(r'\bby (?:payer|plan|health plan)s?\b|\bacross (?:payers|plans)\b|\beach (?:payer|plan)\b', q): params['group_by'] = 'payer'
    elif re.search(r'\bby (?:line of business|lob|product)\b', q): params['group_by'] = 'line_of_business'
    if re.search(r'\b(?:65|sixty-five) (?:and|or) (?:older|over)\b|\bseniors\b', q): params['eligible'] = 'age65'
    elif re.search(r'\bmedicare (?:population|members|beneficiaries|enrollees|book)\b', q): params['eligible'] = 'medicare'
    elif re.search(r'\badults?\b', q): params['eligible'] = 'adult'
    elif re.search(r'\bwhole population\b|\ball (?:patients|members)\b|\beveryone\b', q): params['eligible'] = 'all'
    m = re.search(r'\b(?:last|past) (\d{1,3}) months\b', q)
    if m and 1 <= int(m.group(1)) <= 120: params['months'] = int(m.group(1))
    elif re.search(r'\b(?:last|past) year\b|\blast 12 months\b', q): params['months'] = 12
    return plan(OP, params, 'Survey item estimate weighted for nonresponse, patient-mix adjusted comparison and key drivers', question,
                [f"item {params.get('item', 'general_health')}"])


TEMPLATES = ['What share of HRA respondents rate their health good or better after weighting for nonresponse?',
             'Weight the health risk assessment results to our Medicare population: how common are falls?',
             'Is loneliness on the HRA biased by who responded? Give the raked estimate.',
             'Patient-mix adjusted self-rated health by practice from the survey',
             'What drives poor self-rated health among survey respondents? Key driver analysis',
             'PHQ-2 positive rate among HRA respondents weighted to the eligible population',
             'Survey-weighted food insecurity prevalence from the social needs screens',
             'Compare fair or poor self-rated health across payers with case-mix adjusted survey scores']
