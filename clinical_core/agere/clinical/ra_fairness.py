"""Risk-adjustment fairness diagnostics (FRONTIER #33; Zink & Rose, Biometrics 2020, "Fair regression for health care payment").

Does the risk score pay enough for each group? For scored patients (risk_adjustment.run, focus='fairness'):
  predicted cost  payment RAF x (total realized cost / total payment RAF): the RAF re-expressed in dollars so predicted and
                  realized totals match across the scope (budget neutral);
  realized cost   allowed dollars (medical + pharmacy, vbc._claims: payer feed at final adjudication when present) in the
                  collection year;
  predictive ratio PR_g = sum predicted / sum realized over group g (1 = paid as spent; < 1 = undercompensated), with a
                  bootstrap 90% interval (resampling patients within the group, fixed seed).
Groups: sex, age band, coverage type (dual / Medicaid / Medicare...), race, ethnicity, each chronic condition
(concept_patient), and 'no payment HCC'. Group-importance search: groups with at least MIN_SUPPORT patients whose
PR interval lies wholly below 1 are undercompensated (ranked by PR); wholly above 1, overcompensated.
Fair regression option: ordinary least squares of realized cost on the risk-score design [1, payment RAF, demographic
RAF, payment-HCC count] vs the same model with a penalty forcing the mean residual of the named (default: the
undercompensated) groups to zero (Zink & Rose's constrained regression, solved as an augmented least-squares problem).
Reported: R-squared of each (fit loss) and the named groups' PRs before/after plus the mean |PR - 1| across all groups
(fairness gain). The fair model is a diagnostic; the payment policy choice belongs to the user. Synthetic data only.
"""
from collections import defaultdict
import math

import numpy as np

VERSION = 'ra-fairness/1'
MIN_SUPPORT = 30
BOOT = 300
SEED = 20291033
PENALTY = 1e4
AGE_GROUPS = ((0, 64, 'under 65'), (65, 74, '65-74'), (75, 84, '75-84'), (85, 200, '85+'))


def predictive_ratio(pred, actual, boot=BOOT, seed=SEED):
    """PR = sum(pred)/sum(actual) with a bootstrap 90% interval. -> (pr, low, high); None entries when realized cost is zero."""
    pred = np.asarray(pred, float); actual = np.asarray(actual, float)
    if not len(pred) or actual.sum() <= 0: return None, None, None
    pr = float(pred.sum() / actual.sum())
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(pred), size=(boot, len(pred)))
    a = actual[idx].sum(1); p = pred[idx].sum(1)
    ok = a > 0
    if not ok.any(): return pr, None, None
    ratios = p[ok] / a[ok]
    return pr, float(np.percentile(ratios, 5)), float(np.percentile(ratios, 95))


def group_table(rows, pred, actual, min_support=MIN_SUPPORT):
    """rows: per-patient dicts with 'groups': [(dimension, value)]. -> list of group rows sorted by predictive ratio."""
    members = defaultdict(list)
    for i, r in enumerate(rows):
        for g in r['groups']: members[g].append(i)
    out = []
    for (dim, val), idx in members.items():
        idx = np.array(idx)
        pr, lo, hi = predictive_ratio(pred[idx], actual[idx])
        supported = len(idx) >= min_support
        status = ('undercompensated' if supported and hi is not None and hi < 1 else
                  'overcompensated' if supported and lo is not None and lo > 1 else
                  'within interval' if supported else 'below minimum support')
        out.append({'dimension': dim, 'group': val, 'patients': int(len(idx)), 'predicted_cost': round(float(pred[idx].sum()), 2),
                    'realized_cost': round(float(actual[idx].sum()), 2), 'predictive_ratio': None if pr is None else round(pr, 3),
                    'pr_ci90': [None if lo is None else round(lo, 3), None if hi is None else round(hi, 3)],
                    'mean_payment_raf': round(float(np.mean([rows[i]['raf'] for i in idx])), 3), 'status': status})
    return sorted(out, key=lambda r: (r['predictive_ratio'] is None, r['predictive_ratio'] if r['predictive_ratio'] is not None else 0))


def _r2(y, fit):
    ss = float(((y - y.mean()) ** 2).sum())
    return round(1 - float(((y - fit) ** 2).sum()) / ss, 4) if ss > 0 else None


def fair_regression(X, y, groups, penalty=PENALTY):
    """Least squares of y on X, and the penalized fit with mean residual ~ 0 in each named group (index arrays).
    -> {'beta_ols', 'beta_fair', 'r2_ols', 'r2_fair', 'fit_ols', 'fit_fair', 'group_mean_residual': {..}}"""
    X = np.asarray(X, float); y = np.asarray(y, float)
    b_ols, *_ = np.linalg.lstsq(X, y, rcond=None)
    rows, targets = [X], [y]
    n = len(y)
    for idx in groups:
        if not len(idx): continue
        w = math.sqrt(penalty * n)  # each constraint weighs like `penalty` copies of the whole sample
        rows.append(w * X[idx].mean(0, keepdims=True)); targets.append(np.array([w * y[idx].mean()]))
    b_fair, *_ = np.linalg.lstsq(np.vstack(rows), np.concatenate(targets), rcond=None)
    f_ols, f_fair = X @ b_ols, X @ b_fair
    return {'beta_ols': b_ols, 'beta_fair': b_fair, 'fit_ols': f_ols, 'fit_fair': f_fair, 'r2_ols': _r2(y, f_ols), 'r2_fair': _r2(y, f_fair),
            'group_mean_residual': {'ols': [float((y[i] - f_ols[i]).mean()) for i in groups if len(i)],
                                    'fair': [float((y[i] - f_fair[i]).mean()) for i in groups if len(i)]}}


def _age_group(age):
    return next((label for lo, hi, label in AGE_GROUPS if age is not None and lo <= age <= hi), 'unknown')


def realized_cost(db, start, end, pids):
    from . import vbc
    keep = set(pids)
    cost = defaultdict(float)
    for pid, _month, allowed, _paid, _kind, _arr in vbc._claims(db, start, end):
        if pid in keep: cost[pid] += (allowed or 0) / 100
    return cost


def diagnose(db, pats, start, end, named=()):
    """pats: risk_adjustment patient dicts (payment_raf, demographic_payment_raf, payment_hcc_count, age, sex, coverage_type).
    named: [(dimension, value)] groups to constrain in the fair regression (default: the undercompensated groups, at most 3)."""
    if not pats: return {'version': VERSION, 'status': 'empty', 'groups': []}
    pids = [p['patient_id'] for p in pats]
    cost = realized_cost(db, start, end, pids)
    demo = {}
    cols = {r[1] for r in db.execute('PRAGMA table_info(patient)')}
    if {'race', 'ethnicity'} <= cols:
        demo = {pid: (race, eth) for pid, race, eth in db.execute('SELECT patient_id, race, ethnicity FROM patient')}
    conds = defaultdict(set)
    try:
        from .aggregate import BURDEN_CONCEPTS
        for pid, c in db.execute('SELECT patient_id, concept FROM concept_patient WHERE concept IN (%s)' % ','.join('?' * len(BURDEN_CONCEPTS)),
                                 BURDEN_CONCEPTS):
            conds[pid].add(c)
    except Exception:  # noqa: BLE001 - no concept table: condition groups are skipped
        pass
    rows = []
    for p in pats:
        race, eth = demo.get(p['patient_id'], (None, None))
        g = [('sex', p['sex']), ('age_band', _age_group(p['age'])), ('coverage_type', p.get('coverage_type') or 'unknown')]
        if race: g.append(('race', race))
        if eth: g.append(('ethnicity', eth))
        g += [('condition', c) for c in sorted(conds.get(p['patient_id'], ()))]
        if not (p.get('payment_hcc_count') or {}).get('V28'): g.append(('payment_hccs', 'none'))
        rows.append({'patient_id': p['patient_id'], 'raf': p['payment_raf'], 'groups': g})
    actual = np.array([cost.get(p['patient_id'], 0.0) for p in pats])
    raf = np.array([p['payment_raf'] for p in pats], float)
    if actual.sum() <= 0 or raf.sum() <= 0:
        return {'version': VERSION, 'status': 'no_cost', 'groups': [], 'patients': len(pats),
                'note': 'No realized allowed cost in the collection year for the scored patients: predictive ratios cannot be computed.'}
    scale = actual.sum() / raf.sum()
    pred = raf * scale
    table = group_table(rows, pred, actual)
    under = [r for r in table if r['status'] == 'undercompensated']
    over = [r for r in table if r['status'] == 'overcompensated']
    target, basis = list(named), 'named'
    if not target and under: target, basis = [(r['dimension'], r['group']) for r in under[:3]], 'undercompensated groups'
    if not target:  # nothing significant: still offer the fair fit on the lowest-PR groups with enough patients
        target = [(r['dimension'], r['group']) for r in table if r['patients'] >= MIN_SUPPORT and r['predictive_ratio'] is not None
                  and r['predictive_ratio'] < 1][:2]
        basis = 'lowest predictive ratios with minimum support (none significantly undercompensated)'
    idx_of = lambda key: np.array([i for i, r in enumerate(rows) if key in r['groups']], int)
    X = np.column_stack([np.ones(len(pats)), raf, [p.get('demographic_payment_raf') or 0.0 for p in pats],
                         [(p.get('payment_hcc_count') or {}).get('V28') or 0 for p in pats]])
    fair = None
    if target:
        groups = [idx_of(k) for k in target]
        fit = fair_regression(X, actual, groups)
        ols_fit, fair_fit = np.maximum(fit['fit_ols'], 0), np.maximum(fit['fit_fair'], 0)
        # budget-neutral rescale so PRs compare like the RAF table
        ols_fit *= actual.sum() / max(ols_fit.sum(), 1e-9); fair_fit *= actual.sum() / max(fair_fit.sum(), 1e-9)
        t_ols, t_fair = group_table(rows, ols_fit, actual), group_table(rows, fair_fit, actual)
        dev = lambda t: round(float(np.mean([abs(r['predictive_ratio'] - 1) for r in t if r['predictive_ratio'] is not None
                                              and r['patients'] >= MIN_SUPPORT])), 4)
        pr_of = lambda t, k: next((r['predictive_ratio'] for r in t if (r['dimension'], r['group']) == k), None)
        fair = {'basis': basis, 'constrained_groups': [{'dimension': d, 'group': v, 'pr_raf': pr_of(table, (d, v)), 'pr_ols': pr_of(t_ols, (d, v)),
                                        'pr_fair': pr_of(t_fair, (d, v))} for d, v in target],
                'design': ['intercept', 'payment RAF', 'demographic RAF', 'V28 payment-HCC count'],
                'r2_ols': fit['r2_ols'], 'r2_fair': fit['r2_fair'],
                'fit_loss_r2': None if fit['r2_ols'] is None or fit['r2_fair'] is None else round(fit['r2_ols'] - fit['r2_fair'], 4),
                'mean_abs_pr_deviation': {'raf': dev(table), 'ols': dev(t_ols), 'fair': dev(t_fair)},
                'note': 'Penalized least squares forcing each constrained group\'s mean residual to ~0 (Zink & Rose 2020); a diagnostic, '
                        'not a payment recommendation.'}
    return {'version': VERSION, 'status': 'answered', 'patients': len(pats), 'period': {'start': start, 'end': end},
            'realized_cost_total': round(float(actual.sum()), 2), 'dollars_per_raf': round(float(scale), 2),
            'patients_with_zero_cost': int((actual == 0).sum()), 'groups': table, 'undercompensated': under, 'overcompensated': over,
            'overall_pr': 1.0, 'min_support': MIN_SUPPORT, 'fair_regression': fair,
            'method': ('Predicted cost = payment RAF x (realized cost / total RAF), budget neutral over the scope; realized cost = allowed '
                       'medical + pharmacy dollars in the collection year; predictive ratio = sum predicted / sum realized per group with a '
                       f'bootstrap 90% interval ({BOOT} resamples); undercompensated = at least {MIN_SUPPORT} patients and the whole interval '
                       'below 1; fair regression = least squares with a penalty forcing zero mean residual in the constrained groups.')}


def summary(f):
    if f.get('status') != 'answered':
        return f.get('note') or 'No patients scored for the fairness diagnostic.'
    def g(r): return f"{r['dimension']} {r['group']} PR {r['predictive_ratio']:.2f} (90% {r['pr_ci90'][0]}-{r['pr_ci90'][1]}, {r['patients']} patients)"
    text = (f"Risk-score fairness over {f['patients']} scored patients ({f['period']['start']} to {f['period']['end']}, realized "
            f"${f['realized_cost_total']:,.0f}; ${f['dollars_per_raf']:,.0f} per 1.0 RAF): ")
    text += (f"{len(f['undercompensated'])} undercompensated group(s): " + '; '.join(g(r) for r in f['undercompensated'][:5])
             if f['undercompensated'] else 'no group with enough patients is undercompensated beyond chance')
    if f['overcompensated']:
        text += f". {len(f['overcompensated'])} overcompensated: " + '; '.join(g(r) for r in f['overcompensated'][:3])
    fr = f.get('fair_regression')
    if fr:
        text += (f". Fair regression ({fr['basis']}) constraining {', '.join(c['dimension'] + ' ' + str(c['group']) for c in fr['constrained_groups'])}: "
                 f"R-squared {fr['r2_ols']} -> {fr['r2_fair']} (fit loss {fr['fit_loss_r2']}), mean |PR-1| across groups "
                 f"{fr['mean_abs_pr_deviation']['ols']} -> {fr['mean_abs_pr_deviation']['fair']}; "
                 + '; '.join(f"{c['group']} PR {c['pr_ols']} -> {c['pr_fair']}" for c in fr['constrained_groups']))
    return text + '.'
