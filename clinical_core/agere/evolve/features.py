"""De-identified patient features for outreach policies: what an evolved prioritize() / choose_program() may read.

    rows = patient_features(semantic_path, plan)   # one dict per routed (route == 'enroll') patient, in plan order

Sources: the routed plan (gap, coverage, attribution, the counterparty facts the action backbone reads) and the simulator's
own history and do-nothing baseline model (monthly admission / ED rates and expected spending at as_of, prior utilization).
No patient id, member id, name, date of birth or practice id ever enters a feature row; the evaluator keeps the mapping from
row position to patient on its side.
"""
from __future__ import annotations

import math

import numpy as np

from ..plans import act as actmod
from ..simulator import baseline as bl
from ..simulator.data import History, add_months, day

PILLARS = ('rasi', 'beta_blocker', 'mra', 'sglt2i')
PRIORITY_RANK = {'high': 3, 'medium': 2, 'low': 1}
HF_PREFIX = ('I50',)

SCHEMA = {
    'age': 'age in years at as_of (None if unknown)',
    'age_band': "'<65' | '65-74' | '75+' | 'unknown'",
    'male': 'bool',
    'line_of_business': "coverage line of business at as_of, e.g. 'medicare', 'medicaid', 'commercial', 'dual'",
    'product': 'coverage product at as_of',
    'attributed': 'bool: an attributed practice / clinician exists',
    'attribution_method': "'enrollment_span' | 'most_recent_pcp_visit' | 'patient_pcp' | 'unattributed'",
    'missing_pillars': "list of open GDMT pillar gaps among 'rasi', 'beta_blocker', 'mra', 'sglt2i'",
    'missing_count': 'number of open pillar gaps (0-4)',
    'present_count': 'pillars already on therapy (0-4)',
    'missing_rasi / missing_beta_blocker / missing_mra / missing_sglt2i': 'bool per pillar',
    'barrier_count': 'pillars with a current documented barrier (drug intolerance, cost, declined, ...)',
    'barrier_documented_count': 'missing pillars fully explained by documented barriers',
    'all_missing_barrier_documented': 'bool: every missing pillar has a documented barrier',
    'barrier_cost_only': 'bool: the only documented barriers are cost / access',
    'barrier_declined': 'bool: patient previously declined',
    'routed_program': 'program the deterministic router chose',
    'task_priority': 'router task priority: 3 high, 2 medium, 1 low',
    'router_rank': 'position in the router order scaled to 0..1 (0 = first)',
    'interpreter_needed': 'bool (counterparty reach modifier)',
    'medicaid': 'bool (counterparty reach modifier)',
    'dual': 'bool (dual eligible)',
    'age_80_plus': 'bool',
    'monthly_admission_rate': 'do-nothing expected admissions per month (simulator baseline model)',
    'monthly_ed_rate': 'do-nothing expected ED visits per month',
    'annual_admission_risk': 'P(at least one admission in 12 months) from the baseline rate',
    'expected_monthly_cost': 'do-nothing expected medical spending per month (USD)',
    'admissions_12m': 'admissions in the 12 months before as_of',
    'ed_visits_12m': 'ED visits in the 12 months before as_of',
    'hf_admissions_12m': 'admissions with a principal heart-failure (I50) diagnosis, 12 months',
    'hf_admission_6m': 'bool: an HF admission in the last 6 months',
    'hf_ed_visits_12m': 'ED visits with a principal HF diagnosis, 12 months',
    'admitted_last_90d': 'bool',
    'days_since_admission': 'days since the last admission (None if none in history)',
    'days_since_ed': 'days since the last ED visit (None if none in history)',
    'spend_12m': 'paid medical spending in the 12 months before as_of (USD)',
    'chronic_conditions': 'distinct chronic condition categories before as_of',
    'years_since_hf_diagnosis': 'years since the first HF diagnosis (None if not dated)',
}


def _v(field):
    return field.get('value') if isinstance(field, dict) else field


def _age_band(age):
    if age is None:
        return 'unknown'
    return '<65' if age < 65 else ('65-74' if age < 75 else '75+')


def baseline_rates(h, as_of_day, horizon_months=12, lookback_months=12, pids=()):
    """Do-nothing monthly admission / ED rates and spending per patient from the simulator's baseline model."""
    L, W = bl.windows(h, as_of_day, horizon_months, lookback_months)
    model = bl.Model(h, as_of_day, L, W)
    pool = bl.event_costs(h, list(h.events), add_months(as_of_day, -24), as_of_day)
    ca = float(np.mean(pool['admission'])) if pool['admission'] else 0.0
    ce = float(np.mean(pool['ed'])) if pool['ed'] else 0.0
    X = np.array([bl.features(h, p, as_of_day, L) for p in pids], float).reshape(-1, len(bl.FEATURES))
    if not len(pids):
        return {}
    ra, re_, rc = model.rates(X)
    return {p: (float(ra[i]), float(re_[i]), float(rc[i] + ra[i] * ca + re_[i] * ce)) for i, p in enumerate(pids)}


def patient_features(semantic_path, plan, *, history=None):
    """-> (patient ids, feature rows) for the plan's enrolled patients, in plan order. Ids stay with the caller."""
    h = history or History.load(semantic_path)
    A = day(plan['as_of'])
    rows = [r for r in plan['patients'] if r.get('route') == 'enroll']
    pids = [r['patient_id'] for r in rows]
    rates = baseline_rates(h, A, pids=pids)
    n = max(len(rows) - 1, 1)
    out = []
    y1, m6 = add_months(A, -12), add_months(A, -6)
    for k, r in enumerate(rows):
        pid = r['patient_id']
        gap = r.get('gap') or {}
        cov = r.get('coverage') or {}
        facts = actmod.patient_facts(r)
        missing = list(_v(gap.get('missing_pillars')) or [])
        age = _v(r.get('age'))
        ev = h.events.get(pid, ())
        past = [e for e in ev if e[0] < A]
        adm12 = [e for e in past if e[1] == 'admission' and e[0] >= y1]
        ed12 = [e for e in past if e[1] == 'ed' and e[0] >= y1]
        hf = lambda e: any(str(dx).startswith(HF_PREFIX) for dx in e[3])  # noqa: E731
        last_adm = max((e[0] for e in past if e[1] == 'admission'), default=None)
        last_ed = max((e[0] for e in past if e[1] == 'ed'), default=None)
        adm, ed, ecost, ocost = h.window(pid, y1, A)
        ra, re_, cost = rates.get(pid, (0.0, 0.0, 0.0))
        hf_day = h.hf_dx.get(pid)
        task = r.get('task') or {}
        out.append({
            'age': age, 'age_band': _age_band(age), 'male': h.sex.get(pid) == 'M',
            'line_of_business': _v(cov.get('line_of_business')) or 'unknown', 'product': _v(cov.get('product')) or 'unknown',
            'attributed': (r.get('attribution') or {}).get('method') not in (None, 'unattributed'),
            'attribution_method': (r.get('attribution') or {}).get('method') or 'unattributed',
            'missing_pillars': missing, 'missing_count': len(missing),
            'present_count': len(_v(gap.get('present_pillars')) or []),
            **{f'missing_{p}': p in missing for p in PILLARS},
            'barrier_count': len(_v(gap.get('barrier_pillars')) or []),
            'barrier_documented_count': len(_v(gap.get('barrier_documented_pillars')) or []),
            'all_missing_barrier_documented': bool(gap.get('all_missing_barrier_documented')),
            'barrier_cost_only': bool(facts.get('barrier_cost_only')), 'barrier_declined': bool(facts.get('barrier_declined')),
            'routed_program': r.get('program_id'), 'task_priority': PRIORITY_RANK.get(task.get('priority'), 2),
            'router_rank': round(k / n, 6),
            'interpreter_needed': bool(facts.get('interpreter_needed')), 'medicaid': bool(facts.get('medicaid')),
            'dual': bool(facts.get('dual')), 'age_80_plus': bool(facts.get('age_80_plus')),
            'monthly_admission_rate': round(ra, 6), 'monthly_ed_rate': round(re_, 6),
            'annual_admission_risk': round(1 - math.exp(-12 * ra), 5), 'expected_monthly_cost': round(cost, 2),
            'admissions_12m': len(adm12), 'ed_visits_12m': len(ed12),
            'hf_admissions_12m': sum(1 for e in adm12 if hf(e)), 'hf_admission_6m': any(hf(e) and e[0] >= m6 for e in adm12),
            'hf_ed_visits_12m': sum(1 for e in ed12 if hf(e)),
            'admitted_last_90d': any(A - 90 <= e[0] for e in adm12),
            'days_since_admission': (A - last_adm) if last_adm is not None else None,
            'days_since_ed': (A - last_ed) if last_ed is not None else None,
            'spend_12m': round(float(ecost + ocost), 2),
            'chronic_conditions': len({c for d, c in h.chronic.get(pid, ()) if d < A}),
            'years_since_hf_diagnosis': round((A - hf_day) / 365.25, 2) if hf_day is not None and hf_day < A else None,
        })
    return pids, out
