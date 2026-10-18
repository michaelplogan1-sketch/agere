"""Use an approved evolved outreach policy inside the deterministic router (config switch, default off).

config/agere/evolve_policy.json:
    {"enabled": false, "policies": {"pharmacy_gdmt": "artifacts/evolve/<run>/policies/net_savings.json"}}

When enabled and the referenced policy artifact for the plan's program kind has "approved": true, router.route() hands its
plan to apply(): the policy ranks the enrolled patients, picks a program variant (or none) and fills the policy's capacity
per quarter in rank order. Selected rows keep route 'enroll' and gain row['outreach_policy'] (rank, wave, start offset in
days, score, program); the simulator reads that start offset. Enrolled rows the policy does not reach this horizon get
route 'capacity_waitlist'. With the switch off (the default) or no approved policy the plan is returned unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / 'config' / 'agere' / 'evolve_policy.json'


def load_config(path=None):
    p = Path(path or CONFIG)
    if not p.is_file():
        return {'enabled': False}
    return json.loads(p.read_text())


def enabled(path=None):
    return bool(load_config(path).get('enabled'))


def configured_policy(program_kind, path=None):
    cfg = load_config(path)
    if not cfg.get('enabled'):
        return None
    ref = (cfg.get('policies') or {}).get(program_kind)
    if not ref:
        return None
    f = Path(ref) if Path(ref).is_absolute() else ROOT / ref
    pol = json.loads(f.read_text())
    return pol if pol.get('approved') is True else None


def apply(plan, semantic_db, policy, *, catalog=None):
    """The plan with the policy's ranking, waves and program choices applied (a new dict; the input is not modified)."""
    from ..plans import catalog as catmod, router
    from . import evaluator as ev, features as featmod, sandbox
    if not policy or policy.get('approved') is not True:
        raise ValueError('only an approved policy (approved: true) can be applied')
    if policy.get('program_kind') != plan.get('program_kind'):
        return plan
    cat = catalog or catmod.load()
    t = policy.get('task') or {}
    hours_by = {**ev.DEFAULT_PROGRAM_HOURS, **(t.get('program_hours') or {})}
    programs = [{'id': p['id'], 'name': p.get('name'), 'role': p.get('role') or 'standard', 'hours': float(hours_by.get(p['id'], 1.0))}
                for p in catmod.programs_of(cat, plan['program_kind'])]
    pids, rows = featmod.patient_features(semantic_db, plan)
    scores, choices = sandbox.run(policy['code'], rows, programs)
    wave_days = int(t.get('wave_days') or 91)
    n_waves = max(1, int(int(t.get('horizon_months') or 12) * 30.44 // wave_days))
    order = sorted([i for i in range(len(pids)) if scores[i] is not None and choices[i] is not None], key=lambda i: (-scores[i], i))
    assignment = ev.assign_waves(order, choices, programs, int(t.get('capacity_per_quarter') or len(pids)), t.get('hours_per_quarter'),
                                 n_waves)
    pos = {pid: i for i, pid in enumerate(pids)}
    chosen = {pids[i]: (rank, v, q) for rank, (i, v, q) in enumerate(assignment)}
    progs = {p['id']: p for p in cat['programs']}
    out_rows = []
    for r in plan['patients']:
        if r.get('route') != 'enroll' or r['patient_id'] not in pos:
            out_rows.append(r)
            continue
        r2 = dict(r)
        i = pos[r['patient_id']]
        if r['patient_id'] in chosen:
            rank, v, q = chosen[r['patient_id']]
            if v != r.get('program_id'):
                facts = {'payer_id': r['coverage']['payer_id']['value'], 'product': r['coverage']['product']['value'],
                         'line_of_business': r['coverage']['line_of_business']['value'],
                         'practice_id': r['attribution']['practice_id']['value']}
                slot, why = router._assign(progs[v], r['patient_id'], facts)
                if slot is not None:
                    r2['program_id'] = v
                    r2['vendor_slot'] = {**slot, 'assignment_reason': f'outreach policy program choice; {why}'}
                    if r2.get('task'):
                        r2['task'] = {**r2['task'], 'program_id': v}
            r2['outreach_policy'] = {'policy_id': policy.get('id'), 'selected': True, 'rank': rank + 1, 'wave': q,
                                     'start_offset_days': q * wave_days, 'score': scores[i], 'program_id': r2.get('program_id')}
        else:
            r2['route'] = 'capacity_waitlist'
            r2['outreach_policy'] = {'policy_id': policy.get('id'), 'selected': False, 'score': scores[i],
                                     'reason': 'policy skipped this patient' if scores[i] is None or choices[i] is None
                                     else 'beyond the policy capacity within the horizon'}
            r2['decision'] = router.F('capacity_waitlist: outreach policy', [])
        out_rows.append(r2)
    out_rows.sort(key=lambda r: (0, r['outreach_policy']['rank']) if (r.get('outreach_policy') or {}).get('selected') else (1, 0))
    return {**plan, 'patients': out_rows, 'summary': router.summarize(out_rows),
            'outreach_policy': {'policy_id': policy.get('id'), 'objective': policy.get('objective'), 'selected': len(assignment),
                                'waitlisted': sum(1 for r in out_rows if r.get('route') == 'capacity_waitlist'),
                                'capacity_per_quarter': t.get('capacity_per_quarter'), 'waves': n_waves}}
