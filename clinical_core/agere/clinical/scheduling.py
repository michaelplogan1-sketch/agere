"""`scheduling` operation: access and yield management for clinic sessions (airline overbooking applied to slots).

Answers "how much should each clinic overbook?", "which appointments are likely no-shows next week?", "how many extra
slots can practice 3 safely book?", "what is our access (lead time)?" and "who should get a reminder call?".

No-show model: L2-regularised logistic regression (Newton/IRLS, numpy) on features known before the visit day: lead
time (booking -> scheduled), the patient's prior appointments and prior no-shows (only appointments scheduled at least
PRIOR_LAG_DAYS earlier, so their outcome had been received), visit purpose, scheduling organisation, day of week, payer,
age and positive social-needs screens (transport, food, cost of care) dated on or before the visit. Evaluated on a
time split (train on earlier resolved appointments, score the last `test_months` before the snapshot) against the
baseline every clinic already has: the practice's own historical no-show rate. AUC, Brier, ECE, reliability table and
calibration slope come from conformal.py's helpers. Live predictions refit on every resolved appointment to the snapshot.

Overbooking: per session (organisation x day) with capacity C, shows S = PoissonBinomial(booked show probabilities)
+ Binomial(k, show probability of an extra booking). Expected net value of booking k extra =
value_per_visit x E[min(S, C)] - overflow_cost x E[max(S - C, 0)]. The full EV(k) curve is computed exactly (DP
convolution) and k* = argmax (optionally subject to P(overflow) <= max_overflow_probability). Equivalent newsvendor /
EMSR rule: add an extra while P(S >= C) < value / (value + overflow_cost). value_per_visit, overflow_cost and the
reminder effect are ASSUMPTIONS with stated defaults, overridable by the user.

Access: lead time (booked -> scheduled) per organisation. Third-next-available needs open-slot inventory / templates,
which are not received; it is reported as not computable rather than approximated.
"""
from collections import Counter, defaultdict
from datetime import date, timedelta
import math, sqlite3

import numpy as np

from . import conformal, semantic_layer

VERSION = 'scheduling/1'
FOCUS = ('all', 'no_show_risk', 'overbooking', 'access', 'reminders')
PRIOR_LAG_DAYS = 7  # a prior appointment's outcome counts only once it would have been received
DEFAULTS = {'focus': 'all', 'practice': '', 'horizon_days': 14, 'value_per_visit': 1.0, 'overflow_cost': 2.0,
            'reminder_effect': 0.3, 'reminder_capacity': 25, 'capacity': None, 'max_overflow_probability': None,
            'test_months': 12, 'l2': None, 'top': 50}
L2_GRID = (1.0, 10.0, 50.0, 200.0)  # chosen by an inner time split when l2 is not stated
NEEDS = ('transport', 'food', 'cost_of_care')
DOW = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')


def _num(v, lo, hi, name, integer=False):
    if (type(v) is not int) if integer else (type(v) not in (int, float) or not math.isfinite(v)):
        raise ValueError(f'scheduling {name} must be {"an integer" if integer else "a number"}')
    if not lo <= v <= hi: raise ValueError(f'scheduling {name} {lo}..{hi}')
    return v if integer else float(v)


def validate(p):
    if not isinstance(p, dict) or set(p) - set(DEFAULTS):
        raise ValueError('scheduling parameters: ' + ', '.join(sorted(DEFAULTS)))
    out = {**DEFAULTS, **{k: v for k, v in p.items() if v is not None or k in ('capacity', 'max_overflow_probability', 'l2')}}
    if out['focus'] not in FOCUS: raise ValueError(f'scheduling focus: {FOCUS}')
    if not isinstance(out['practice'], str) or len(out['practice']) > 80: raise ValueError('scheduling practice: an organisation id or ""')
    out['practice'] = out['practice'].strip()
    out['horizon_days'] = _num(out['horizon_days'], 1, 120, 'horizon_days', True)
    out['value_per_visit'] = _num(out['value_per_visit'], 0.01, 1e6, 'value_per_visit')
    out['overflow_cost'] = _num(out['overflow_cost'], 0.0, 1e6, 'overflow_cost')
    out['reminder_effect'] = _num(out['reminder_effect'], 0.0, 1.0, 'reminder_effect')
    out['reminder_capacity'] = _num(out['reminder_capacity'], 0, 5000, 'reminder_capacity', True)
    if out['capacity'] is not None: out['capacity'] = _num(out['capacity'], 1, 200, 'capacity', True)
    if out['max_overflow_probability'] is not None:
        out['max_overflow_probability'] = _num(out['max_overflow_probability'], 0.0, 1.0, 'max_overflow_probability')
    out['test_months'] = _num(out['test_months'], 3, 24, 'test_months', True)
    if out['l2'] is not None: out['l2'] = _num(out['l2'], 0.0, 1e4, 'l2')
    out['top'] = _num(out['top'], 1, 1000, 'top', True)
    return out


# ---- show distribution and overbooking ---------------------------------------------------------
def poisson_binomial(probs):
    """pmf of the number of successes among independent Bernoulli(p_i) (exact DP convolution)."""
    pmf = np.ones(1)
    for p in np.asarray(probs, dtype=float):
        pmf = np.append(pmf * (1 - p), 0.0) + np.append(0.0, pmf * p)
    return pmf


def session_curve(show_probs, capacity, extra_show_prob, value=1.0, overflow_cost=2.0, max_extra=None):
    """EV(k) for k = 0..max_extra extra bookings on top of `show_probs` (already booked) in a session of `capacity`."""
    base = poisson_binomial(show_probs)
    max_extra = max_extra if max_extra is not None else max(3, int(math.ceil(capacity * 0.6)) + 2)
    rows, pmf = [], base
    for k in range(max_extra + 1):
        if k: pmf = np.append(pmf * (1 - extra_show_prob), 0.0) + np.append(0.0, pmf * extra_show_prob)
        s = np.arange(len(pmf))
        served = float(np.sum(pmf * np.minimum(s, capacity)))
        overflow = float(np.sum(pmf * np.maximum(s - capacity, 0)))
        rows.append({'extra': k, 'booked': len(show_probs) + k, 'expected_shows': round(float(np.sum(pmf * s)), 4),
                     'expected_served': round(served, 4), 'expected_overflow': round(overflow, 4),
                     'expected_idle': round(capacity - served, 4), 'p_overflow': round(float(pmf[capacity + 1:].sum()), 4),
                     'expected_value': round(value * served - overflow_cost * overflow, 4)})
    return rows


def best_extra(curve, max_overflow_probability=None):
    ok = [r for r in curve if max_overflow_probability is None or r['p_overflow'] <= max_overflow_probability + 1e-12] or curve[:1]
    return max(ok, key=lambda r: (round(r['expected_value'], 9), -r['extra']))


# ---- logistic regression and scoring ------------------------------------------------------------
def fit_logistic(X, y, l2=1.0, iters=50):
    """L2-penalised logistic regression by Newton steps; column 0 (intercept) unpenalised."""
    X, y = np.asarray(X, float), np.asarray(y, float)
    w = np.zeros(X.shape[1])
    prior = min(max(y.mean(), 1e-4), 1 - 1e-4) if len(y) else 0.5
    w[0] = math.log(prior / (1 - prior))
    pen = np.full(X.shape[1], l2); pen[0] = 0.0
    for _ in range(iters):
        z = np.clip(X @ w, -30, 30); p = 1 / (1 + np.exp(-z))
        g = X.T @ (p - y) + pen * w
        H = (X * (p * (1 - p))[:, None]).T @ X + np.diag(pen + 1e-9)
        step = np.linalg.solve(H, g)
        w -= step
        if np.max(np.abs(step)) < 1e-8: break
    return w


def predict(w, X):
    return 1 / (1 + np.exp(-np.clip(np.asarray(X, float) @ w, -30, 30)))


def auc(p, y):
    p, y = np.asarray(p, float), np.asarray(y, bool)
    n1, n0 = int(y.sum()), int((~y).sum())
    if not n1 or not n0: return None
    order = np.argsort(p, kind='mergesort'); ranks = np.empty(len(p)); sp = p[order]
    i = 0
    while i < len(sp):  # average ranks for ties
        j = i
        while j + 1 < len(sp) and sp[j + 1] == sp[i]: j += 1
        ranks[order[i:j + 1]] = (i + j) / 2 + 1; i = j + 1
    return float((ranks[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def calibration_slope(p, y):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    X = np.column_stack([np.ones(len(p)), np.log(p / (1 - p))])
    w = fit_logistic(X, y, l2=0.0)
    return {'intercept': round(float(w[0]), 3), 'slope': round(float(w[1]), 3)}


def evaluate(p, y):
    p, y = np.asarray(p, float), np.asarray(y, float)
    a = auc(p, y)
    return {'cases': int(len(y)), 'events': int(y.sum()), 'observed_rate': round(float(y.mean()), 4) if len(y) else None,
            'mean_predicted': round(float(p.mean()), 4) if len(p) else None, 'auc': None if a is None else round(a, 4),
            'brier': round(conformal.brier(p, y), 5) if len(y) else None, 'ece': round(conformal.ece(p, y), 5) if len(y) else None,
            'log_loss': round(float(-np.mean(y * np.log(np.clip(p, 1e-9, 1)) + (1 - y) * np.log(np.clip(1 - p, 1e-9, 1)))), 5) if len(y) else None}


# ---- data ---------------------------------------------------------------------------------------
def _ord(d): return date.fromisoformat(d[:10]).toordinal()


def _load(db, patient_ids, as_of=None):
    from .age import age_at
    as_of = as_of or dict(db.execute('SELECT key,value FROM meta')).get('as_of')
    keep = set(patient_ids) if patient_ids is not None else None
    patients = {pid: (age_at(born, as_of), cov) for pid, born, cov in db.execute('SELECT patient_id, birth_date, coverage_type FROM patient')}
    needs = defaultdict(list)
    for pid, need, d in db.execute(f"SELECT patient_id, need, event_date FROM social_need WHERE positive = 1 AND event_date IS NOT NULL "
                                   f"AND need IN ({','.join('?' * len(NEEDS))})", NEEDS):
        needs[pid].append((need, _ord(d)))
    rows = []
    for aid, pid, sched, booked, final, ns, cancelled, resched, kept, org, purpose in db.execute(
            'SELECT appointment_id, patient_id, scheduled_date, booked_date, final_status, ever_no_show, ever_cancelled, '
            'ever_rescheduled, kept, organization_id, purpose FROM appointment WHERE scheduled_date IS NOT NULL'):
        if keep is not None and pid not in keep: continue
        age, cov = patients.get(pid, (None, None))
        rows.append({'appointment_id': aid, 'patient_id': pid, 'scheduled_date': sched[:10], 'sched': _ord(sched),
                     'booked': _ord(booked) if booked else None, 'final_status': final, 'no_show': int(bool(ns)),
                     'cancelled': int(bool(cancelled)), 'rescheduled': int(bool(resched)), 'kept': int(bool(kept)),
                     'organization_id': org or 'unknown', 'purpose': purpose or 'unspecified', 'age': age, 'coverage': cov or 'unknown'})
    return rows, needs


def _history(rows):
    """Per appointment: the patient's earlier resolved appointments and no-shows whose outcome was received (>= PRIOR_LAG_DAYS)."""
    by = defaultdict(list)
    for r in rows: by[r['patient_id']].append(r)
    for appts in by.values():
        appts.sort(key=lambda r: r['sched'])
        resolved = [(r['sched'], r['no_show']) for r in appts if r['kept'] or r['no_show']]
        days = [d for d, _ in resolved]; cum = np.cumsum([0] + [n for _, n in resolved])
        for r in appts:
            i = int(np.searchsorted(days, r['sched'] - PRIOR_LAG_DAYS, side='right'))
            r['prior'], r['prior_no_shows'] = i, int(cum[i])


def _family(org):
    parts = org.rsplit('-', 1)
    return parts[0] if len(parts) == 2 and parts[1].isdigit() else org


class Design:
    """Feature map fixed on training rows (vocabularies and scaling)."""
    def __init__(self, train, needs, base_rate):
        self.base = base_rate
        purposes = Counter(r['purpose'] for r in train)
        self.purposes = [k for k, n in purposes.most_common(15) if n >= 30][1:]  # most common is the reference
        orgs = Counter(r['organization_id'] for r in train)
        self.orgs = sorted(k for k, n in orgs.items() if n >= 200)
        fams = Counter(_family(r['organization_id']) for r in train if r['organization_id'] not in self.orgs)
        self.families = sorted(k for k, n in fams.items() if n >= 50)
        if self.orgs: self.orgs = self.orgs[1:]
        self.coverages = sorted({r['coverage'] for r in train} - {'commercial'})
        self.needs = needs
        self.names = (['intercept', 'log_lead_days', 'log_prior_appointments', 'prior_no_shows', 'prior_no_show_rate_smoothed', 'age_per_10y',
                       'age_missing'] + [f'need_{n}' for n in NEEDS] + [f'dow_{d}' for d in DOW[1:]] + [f'payer_{c}' for c in self.coverages] +
                      [f'purpose_{p}' for p in self.purposes] + [f'org_{o}' for o in self.orgs] + [f'org_family_{f}' for f in self.families])
        raw = self._raw(train)
        self.mu, self.sd = raw[:, 1:7].mean(0), raw[:, 1:7].std(0)
        self.sd[self.sd == 0] = 1.0

    def _raw(self, rows):
        X = np.zeros((len(rows), len(self.names)))
        idx = {n: i for i, n in enumerate(self.names)}
        for j, r in enumerate(rows):
            lead = max(0, r['sched'] - r['booked']) if r['booked'] is not None else 0
            X[j, 0] = 1
            X[j, 1] = math.log1p(lead)
            X[j, 2] = math.log1p(r['prior'])
            X[j, 3] = r['prior_no_shows']
            X[j, 4] = (r['prior_no_shows'] + 2 * self.base) / (r['prior'] + 2)
            X[j, 5] = (r['age'] or 0) / 10
            X[j, 6] = r['age'] is None
            for need, d in self.needs.get(r['patient_id'], ()):
                if d <= r['sched']: X[j, idx[f'need_{need}']] = 1
            dow = DOW[date.fromordinal(r['sched']).weekday()]
            if f'dow_{dow}' in idx: X[j, idx[f'dow_{dow}']] = 1
            for key in (f"payer_{r['coverage']}", f"purpose_{r['purpose']}", f"org_{r['organization_id']}"):
                if key in idx: X[j, idx[key]] = 1
            fam = f"org_family_{_family(r['organization_id'])}"
            if r['organization_id'] not in self.orgs and fam in idx: X[j, idx[fam]] = 1
        return X

    def matrix(self, rows):
        X = self._raw(rows)
        X[:, 1:7] = (X[:, 1:7] - self.mu) / self.sd
        return X


def choose_l2(train, needs, stated):
    """Stated penalty, or the grid value with the lowest log loss on the last 6 months of the training window."""
    if stated is not None: return stated, None
    cut = max(r['sched'] for r in train) - 183
    inner, held = [r for r in train if r['sched'] <= cut], [r for r in train if r['sched'] > cut]
    if len(inner) < 200 or len(held) < 50 or len({r['no_show'] for r in inner}) < 2: return 10.0, None
    d = Design(inner, needs, float(np.mean([r['no_show'] for r in inner])))
    Xi, Xh, yh = d.matrix(inner), d.matrix(held), np.array([r['no_show'] for r in held], float)
    scores = {}
    for l2 in L2_GRID:
        q = np.clip(predict(fit_logistic(Xi, [r['no_show'] for r in inner], l2), Xh), 1e-9, 1 - 1e-9)
        scores[l2] = round(float(-np.mean(yh * np.log(q) + (1 - yh) * np.log(1 - q))), 6)
    return min(scores, key=scores.get), {'held_out_log_loss': {str(k): v for k, v in scores.items()}, 'held_out_cases': len(held)}


def _practice_rates(rows, base, strength=50):
    n, e = Counter(), Counter()
    for r in rows: n[r['organization_id']] += 1; e[r['organization_id']] += r['no_show']
    return {o: (e[o] + strength * base) / (n[o] + strength) for o in n}


def _resolve_practice(token, orgs):
    if not token: return None
    t = token.lower().replace(' ', '-')
    exact = [o for o in orgs if o.lower() == t]
    if exact: return exact[0]
    ends = [o for o in orgs if o.lower().endswith('-' + t) or o.lower().endswith(t)]
    pref = [o for o in ends if 'practice' in o.lower()]
    return (pref or sorted(ends) or [None])[0]


def _defects(rows, as_of_ord):
    years = defaultdict(lambda: Counter())
    for r in rows:
        y = r['scheduled_date'][:4]
        years[y]['appointments'] += 1; years[y]['cancelled'] += r['cancelled']; years[y]['rescheduled'] += r['rescheduled']
    by_year = [{'year': y, **c} for y, c in sorted(years.items())]
    out = []
    silent = [b['year'] for b in by_year if b['appointments'] >= 500 and b['cancelled'] == 0]
    with_c = [b['year'] for b in by_year if b['cancelled'] > 0]
    if silent:
        out.append({'code': 'cancellations_stop', 'years_without_cancellations': silent, 'years_with_cancellations': with_c,
                    'statement': f"No cancellations are recorded for appointments scheduled in {', '.join(silent)} "
                                 f"(cancellations appear only in {', '.join(with_c) or 'no year'}; known generator defect D5). Cancellations "
                                 'release slots and are a second source of empty capacity, so the overbooking curves here count no-shows only, '
                                 'cancellation risk cannot be modelled, and appointments that would have been cancelled sit in the no-show '
                                 'and kept counts instead.'})
    total = sum(b['appointments'] for b in by_year); resched = sum(b['rescheduled'] for b in by_year)
    if total and resched / total < 0.005:
        out.append({'code': 'few_reschedules', 'rescheduled': resched, 'appointments': total,
                    'statement': f'Only {resched} of {total} appointments were ever rescheduled ({resched / total:.2%}); rebooking '
                                 'behaviour cannot be learned, so reminders are modelled as preventing a no-show, not as converting it to a reschedule.'})
    unresolved = sum(1 for r in rows if r['sched'] <= as_of_ord and not (r['kept'] or r['no_show'] or r['cancelled'] or r['rescheduled']))
    if unresolved:
        out.append({'code': 'unresolved_past_bookings', 'appointments': unresolved,
                    'statement': f'{unresolved} appointments dated on or before the snapshot never received an outcome status; they are '
                                 'excluded from training and evaluation (if they were mostly no-shows the no-show rate is understated).'})
    out.append({'code': 'no_slot_inventory', 'statement': 'No clinic templates, slot counts or open-slot inventory are received, so session '
                'capacity is taken from observed daily volume (or the capacity you state) and third-next-available cannot be computed.'})
    return out, by_year


def execute(path, patient_ids, params, as_of=None):
    p = validate(params)
    db = sqlite3.connect(f'file:{semantic_layer.ensure(path)}?mode=ro', uri=True)
    try:
        as_of = as_of or dict(db.execute('SELECT key, value FROM meta')).get('as_of')
        rows, needs = _load(db, patient_ids, as_of)
    finally:
        db.close()
    as_of_ord = _ord(as_of)
    _history(rows)
    orgs = sorted({r['organization_id'] for r in rows})
    practice = _resolve_practice(p['practice'], orgs)
    notes = []
    if p['practice'] and practice is None: notes.append(f"No scheduling organisation matches '{p['practice']}'; all organisations are shown.")
    defects, by_year = _defects(rows, as_of_ord)
    resolved = [r for r in rows if (r['kept'] or r['no_show']) and r['sched'] <= as_of_ord]
    split = (date.fromordinal(as_of_ord) - timedelta(days=round(30.44 * p['test_months']))).toordinal()
    train = [r for r in resolved if r['sched'] <= split]
    test = [r for r in resolved if r['sched'] > split]
    upcoming = [r for r in rows if r['sched'] > as_of_ord and not (r['cancelled'] or r['rescheduled'] or r['kept'] or r['no_show'])]
    window = [r for r in upcoming if r['sched'] <= as_of_ord + p['horizon_days']]
    report = {'schema': VERSION, 'parameters': p, 'as_of': as_of, 'practice': practice, 'notes': notes,
              'counts': {'appointments': len(rows), 'resolved': len(resolved), 'train': len(train), 'test': len(test),
                         'upcoming': len(upcoming), 'in_horizon': len(window)},
              'data_defects': defects, 'by_year': by_year, 'model': None, 'sessions': [], 'upcoming_sessions': [], 'appointments': [],
              'reminders': None, 'access': [], 'access_unavailable': ['third_next_available', 'fill_rate'],
              'assumptions': [f"Value of a served visit = {p['value_per_visit']:g} and cost of each patient beyond capacity (overtime/wait) = "
                              f"{p['overflow_cost']:g}, in the same units (assumed defaults unless you state them; critical ratio "
                              f"{p['value_per_visit'] / (p['value_per_visit'] + p['overflow_cost']):.2f}).",
                              f"A reminder call prevents {p['reminder_effect']:.0%} of the no-shows it targets (assumed; reminder effects in "
                              'published trials vary widely).',
                              'Show/no-show outcomes are independent across appointments in a session.'],
              'method': ('L2-regularised logistic regression (Newton) on lead time, prior appointments and no-shows (outcome received '
                         f'>= {PRIOR_LAG_DAYS} days earlier), purpose, organisation, weekday, payer, age and dated social-needs screens; time-split '
                         f"evaluation (last {p['test_months']} months held out) against each organisation's historical no-show rate; AUC, Brier, "
                         'ECE (10 bins), reliability table and calibration slope. Overbooking: exact Poisson-binomial show distribution per session, '
                         'EV(k) = value x E[min(S, C)] - overflow_cost x E[max(S - C, 0)], k* = argmax (newsvendor/EMSR critical ratio '
                         'value/(value + overflow_cost)). Reminders: ranked by P(no-show) x assumed reminder effect.'),
              'limitations': ['Synthetic data; the no-show model has no clinical or operational validation.',
                              'Session capacity is inferred from observed daily volume unless stated; no slot templates are received.']}
    if len(train) < 200 or len(test) < 50 or len({r['no_show'] for r in train}) < 2:
        report['reading'] = (f"Not enough resolved appointments to fit and test a no-show model ({len(train)} training, {len(test)} test; "
                             'need >= 200 and >= 50 with both outcomes).')
        return report
    base = float(np.mean([r['no_show'] for r in train]))
    l2, tuning = choose_l2(train, needs, p['l2'])
    design = Design(train, needs, base)
    w = fit_logistic(design.matrix(train), [r['no_show'] for r in train], l2)
    p_test = predict(w, design.matrix(test)); y_test = np.array([r['no_show'] for r in test], float)
    rates = _practice_rates(train, base)
    p_base = np.array([rates.get(r['organization_id'], base) for r in test])
    model_eval, base_eval = evaluate(p_test, y_test), evaluate(p_base, y_test)
    model_eval.update(calibration_slope(p_test, y_test)); base_eval.update(calibration_slope(p_base, y_test))
    coef = sorted(({'feature': n, 'coefficient': round(float(c), 4), 'odds_ratio': round(math.exp(c), 3)}
                   for n, c in zip(design.names[1:], w[1:])), key=lambda x: -abs(x['coefficient']))
    report['model'] = {'train_window': [train[0]['scheduled_date'] if train else None, date.fromordinal(split).isoformat()],
                       'test_window': [date.fromordinal(split + 1).isoformat(), as_of[:10]],
                       'train_no_show_rate': round(base, 4), 'l2': l2, 'l2_tuning': tuning, 'features': len(design.names) - 1, 'model': model_eval, 'baseline': base_eval,
                       'baseline_name': 'organisation historical no-show rate (shrunk to network, 50 pseudo-appointments)',
                       'reliability': conformal.reliability(p_test, y_test), 'reliability_baseline': conformal.reliability(p_base, y_test),
                       'coefficients': coef[:20],
                       'beats_baseline': bool(model_eval['auc'] is not None and base_eval['auc'] is not None and
                                              model_eval['auc'] > base_eval['auc'] + 0.01 and model_eval['brier'] <= base_eval['brier'])}
    # live model: refit on everything resolved up to the snapshot
    base_all = float(np.mean([r['no_show'] for r in resolved]))
    live = Design(resolved, needs, base_all)
    w_live = fit_logistic(live.matrix(resolved), [r['no_show'] for r in resolved], l2)
    recent = [r for r in resolved if r['sched'] > split]
    p_recent = predict(w_live, live.matrix(recent)) if recent else np.array([])
    p_up = predict(w_live, live.matrix(upcoming)) if upcoming else np.array([])
    for r, q in zip(upcoming, p_up): r['p'] = float(q)
    # sessions: typical session per organisation (capacity = median daily volume in the test window) + EV curve
    per_org, daily = defaultdict(list), defaultdict(Counter)
    for r, q in zip(recent, p_recent): per_org[r['organization_id']].append(float(q)); daily[r['organization_id']][r['sched']] += 1
    session_orgs = [practice] if practice else [o for o in sorted(per_org) if sum(daily[o].values()) >= 100]
    v, c = p['value_per_visit'], p['overflow_cost']
    for org in session_orgs:
        probs = np.array(per_org.get(org, []))
        if not len(probs): continue
        cap = p['capacity'] or max(1, int(round(float(np.median(list(daily[org].values()))))))
        q_show = 1 - np.quantile(probs, (np.arange(cap) + 0.5) / cap)  # a representative full session
        extra = 1 - float(probs.mean())
        curve = session_curve(q_show, cap, extra, v, c)
        best = best_extra(curve, p['max_overflow_probability'])
        report['sessions'].append({'practice': org, 'capacity': cap, 'capacity_basis': 'stated' if p['capacity'] else
                                   f"median appointments per active day, last {p['test_months']} months ({len(daily[org])} days)",
                                   'mean_no_show_probability': round(float(probs.mean()), 4), 'recommended_extra': best['extra'],
                                   'expected_value_gain': round(best['expected_value'] - curve[0]['expected_value'], 4),
                                   'p_overflow': best['p_overflow'], 'expected_overflow': best['expected_overflow'],
                                   'expected_idle_without': curve[0]['expected_idle'], 'expected_idle_with': best['expected_idle'],
                                   'curve': curve})
    caps = {s['practice']: s['capacity'] for s in report['sessions']}
    sess = defaultdict(list)
    for r in window:
        if practice and r['organization_id'] != practice: continue
        sess[(r['organization_id'], r['scheduled_date'])].append(r)
    for (org, day), appts in sorted(sess.items()):
        cap = p['capacity'] or caps.get(org)
        if not cap: continue
        probs = np.array(per_org.get(org) or [r['p'] for r in appts])
        curve = session_curve([1 - r['p'] for r in appts], cap, 1 - float(probs.mean()), v, c, max_extra=cap + int(math.ceil(cap * 0.6)) + 2)
        best = best_extra(curve, p['max_overflow_probability'])
        report['upcoming_sessions'].append({'practice': org, 'date': day, 'booked': len(appts), 'capacity': cap,
                                            'expected_no_shows': round(sum(r['p'] for r in appts), 3), 'book_extra': best['extra'],
                                            'open_slots': max(cap - len(appts), 0), 'overbook_beyond_capacity': max(len(appts) + best['extra'] - cap, 0),
                                            'p_overflow': best['p_overflow']})
    ranked = sorted((r for r in window if not practice or r['organization_id'] == practice), key=lambda r: -r['p'])
    eff = p['reminder_effect']
    for i, r in enumerate(ranked):
        reasons = []
        if r['prior_no_shows']: reasons.append(f"{r['prior_no_shows']} prior no-show{'s' if r['prior_no_shows'] > 1 else ''} of {r['prior']}")
        lead = r['sched'] - r['booked'] if r['booked'] is not None else None
        if lead is not None and lead >= 30: reasons.append(f'booked {lead} days ahead')
        for need, d in needs.get(r['patient_id'], ()):
            if d <= r['sched'] and f'{need.replace("_", " ")} need' not in reasons: reasons.append(f'{need.replace("_", " ")} need')
        report['appointments'].append({'rank': i + 1, 'appointment_id': r['appointment_id'], 'patient_id': r['patient_id'],
                                       'practice': r['organization_id'], 'scheduled_date': r['scheduled_date'], 'purpose': r['purpose'],
                                       'lead_days': lead, 'p_no_show': round(r['p'], 4), 'expected_no_shows_averted': round(r['p'] * eff, 4),
                                       'remind': i < p['reminder_capacity'], 'reasons': reasons})
    chosen = report['appointments'][:p['reminder_capacity']]
    if ranked:
        report['reminders'] = {'capacity': p['reminder_capacity'], 'selected': len(chosen), 'candidates': len(ranked),
                               'expected_no_shows': round(sum(r['p'] for r in ranked), 3),
                               'expected_averted': round(sum(a['expected_no_shows_averted'] for a in chosen), 3),
                               'random_choice_averted': round(eff * len(chosen) * sum(r['p'] for r in ranked) / len(ranked), 3)}
    report['appointments'] = report['appointments'][:p['top']]
    # access: lead time per organisation over the test window (all bookings, whatever their outcome)
    leads = defaultdict(list)
    for r in rows:
        if split < r['sched'] <= as_of_ord and r['booked'] is not None and (not practice or r['organization_id'] == practice):
            leads[r['organization_id']].append(r['sched'] - r['booked'])
    for org, ls in sorted(leads.items(), key=lambda kv: -len(kv[1])):
        if len(ls) < 30 and not practice: continue
        ns = [x for x in recent if x['organization_id'] == org]
        report['access'].append({'practice': org, 'appointments': len(ls), 'median_lead_days': float(np.median(ls)),
                                 'p90_lead_days': float(np.quantile(ls, 0.9)), 'mean_lead_days': round(float(np.mean(ls)), 1),
                                 'active_days': len(daily.get(org, {})),
                                 'appointments_per_active_day': round(len(ls) / max(len(daily.get(org, {})), 1), 2),
                                 'no_show_rate': round(float(np.mean([x['no_show'] for x in ns])), 4) if ns else None})
    report['reading'] = summary(report)
    return report


def summary(report):
    p, m = report['parameters'], report.get('model')
    if not m: return report.get('reading') or 'No scheduling model could be fitted.'
    me, be = m['model'], m['baseline']
    parts = []
    focus = p['focus']
    model_line = (f"No-show model (time split, {me['cases']} held-out appointments, {me['observed_rate']:.1%} no-shows): AUC {me['auc']:.3f} vs "
                  f"{be['auc']:.3f} for the practice-rate baseline, Brier {me['brier']:.4f} vs {be['brier']:.4f}, calibration slope {me['slope']:.2f}"
                  + ('' if m['beats_baseline'] else ' - the model does not meaningfully beat the practice-rate baseline on this data') + '.')
    sessions = report['sessions']
    if focus in ('all', 'overbooking') and sessions:
        if report.get('practice') or len(sessions) == 1:
            s = sessions[0]
            parts.append(f"{s['practice']}: with a typical session of {s['capacity']} slots and a {s['mean_no_show_probability']:.1%} predicted no-show "
                         f"rate, book {s['recommended_extra']} extra per session (P(overflow) {s['p_overflow']:.0%}, expected idle slots "
                         f"{s['expected_idle_without']:.2f} -> {s['expected_idle_with']:.2f}).")
        else:
            rec = ', '.join(f"{s['practice']} +{s['recommended_extra']}" for s in sessions[:12])
            parts.append(f"Recommended extra bookings per typical session: {rec}.")
    if focus in ('all', 'no_show_risk', 'reminders'):
        apps, rem = report['appointments'], report.get('reminders')
        if apps:
            top = apps[0]
            parts.append(f"{report['counts']['in_horizon']} upcoming appointments in the next {p['horizon_days']} days; expected no-shows "
                         f"{rem['expected_no_shows']:.1f}; highest risk {top['appointment_id']} ({top['practice']}, {top['scheduled_date']}, "
                         f"P(no-show) {top['p_no_show']:.0%}).")
            if focus in ('all', 'reminders'):
                parts.append(f"Calling the top {rem['selected']} averts an expected {rem['expected_averted']:.2f} no-shows "
                             f"(vs {rem['random_choice_averted']:.2f} for {rem['selected']} random calls) at an assumed {p['reminder_effect']:.0%} reminder effect.")
        else:
            parts.append(f"No booked appointments fall in the next {p['horizon_days']} days after {report['as_of']}.")
    if focus in ('all', 'access') and report['access']:
        a = report['access'][0] if report.get('practice') else None
        if a: parts.append(f"{a['practice']} access: median lead time {a['median_lead_days']:g} days (90th percentile {a['p90_lead_days']:g}).")
        else:
            meds = [x['median_lead_days'] for x in report['access']]
            parts.append(f"Median booking lead time {min(meds):g}-{max(meds):g} days across {len(meds)} organisations; third-next-available is not computable (no slot inventory).")
    parts.append(model_line)
    d = next((x for x in report['data_defects'] if x['code'] == 'cancellations_stop'), None)
    if d: parts.append(f"Data defect: no cancellations recorded after {d['years_with_cancellations'][-1] if d['years_with_cancellations'] else 'the start'}, so overbooking counts no-shows only.")
    return ' '.join(parts)
