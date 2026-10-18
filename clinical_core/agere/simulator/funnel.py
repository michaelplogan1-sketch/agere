"""Who actually receives the intervention: the action backbone's simulated funnel, run once per Monte Carlo scenario.

An arm is a variant of the routed plan:
  {'id': 'plan'}                                   the plan as routed (each patient through the program the router chose)
  {'id': ..., 'programs': ['access_virtual_pt_msk']} only patients the router sent to these programs get them; the rest
                                                   get nothing (the program's real reach in this population)
  {'id': ..., 'force_program': 'pharmd_barrier_review'} every routed patient goes through this program instead
                                                   (hypothetical; the program's own counterparty rates still apply)
For each arm and scenario k the plan is walked by plans/act.act(plan, scenario=...) -- the same step logic, counterparty
rates (programs.json) and seeds as the durable backbone, with a distinct scenario per Monte Carlo draw -- and each patient's
milestones become: outreach attempted, enrolled, first visit attended (with its day after as_of), completed.
"""
from __future__ import annotations

import copy
from datetime import date

import numpy as np

from ..plans import act as actmod, catalog as catmod

NONE, ATTENDED, COMPLETED = 0, 1, 2


def arm_plan(plan, arm, cat):
    """(plan variant, routed patient ids in the variant, programs used)."""
    rows = [r for r in plan['patients'] if r.get('route') == 'enroll']
    progs = {p['id']: p for p in cat['programs']}
    if arm.get('force_program'):
        prog = progs.get(arm['force_program'])
        if prog is None:
            raise ValueError(f"Unknown program {arm['force_program']!r}")
        slot = (prog.get('vendor_slots') or [None])[0]
        sel = []
        for r in rows:
            r2 = copy.copy(r)
            r2['program_id'] = prog['id']
            r2['vendor_slot'] = {**(slot or {}), 'assignment_reason': 'simulator: forced arm'}
            sel.append(r2)
        used = [prog['id']]
    elif arm.get('programs'):
        keep = set(arm['programs'])
        unknown = keep - set(progs)
        if unknown:
            raise ValueError(f'Unknown programs {sorted(unknown)}')
        sel = [r for r in rows if r.get('program_id') in keep]
        used = sorted(keep)
    else:
        sel = rows
        used = sorted({r['program_id'] for r in rows if r.get('program_id')})
    return {**plan, 'patients': sel}, [r['patient_id'] for r in sel], used


def _day_of(at, as_of):
    return (date.fromisoformat(at[:10]) - as_of).days


def scenarios(plan_variant, cohort, k, *, workspace, seed, cat):
    """Arrays (k, len(cohort)): status (NONE / ATTENDED / COMPLETED), start day (first attended visit; -1 none), and
    milestone flags outreach / enrolled / attended / completed; program id per patient."""
    n = len(cohort)
    index = {pid: i for i, pid in enumerate(cohort)}
    status = np.zeros((k, n), np.int8)
    start = np.full((k, n), -1, np.int32)
    flags = {f: np.zeros((k, n), bool) for f in ('outreach', 'enrolled', 'attended', 'completed')}
    program = [None] * n
    for r in plan_variant['patients']:
        program[index[r['patient_id']]] = r.get('program_id')
    if not plan_variant['patients']:
        return status, start, flags, program
    as_of = date.fromisoformat(plan_variant['as_of'])
    for s in range(k):
        out = actmod.act(plan_variant, workspace=workspace, catalog=cat, scenario=f'sim:{seed}:{s}')
        for a in out['actions']:
            i = index.get(a['patient_id'])
            if i is None:
                continue
            m = a['milestones']
            flags['outreach'][s, i] = bool(m.get('outreach_attempted') or any(st['step'] in ('patient_outreach', 'chart_review')
                                                                               for st in a['steps']))
            flags['enrolled'][s, i] = bool(m.get('enrolled'))
            att = [st for st in a['steps'] if st['step'] == 'first_visit' and (st.get('receipt') or {}).get('outcome') == 'attended']
            if att:
                flags['attended'][s, i] = True
                start[s, i] = max(0, _day_of(att[-1]['receipt']['at'], as_of))
                status[s, i] = ATTENDED
            if m.get('completed'):
                flags['completed'][s, i] = True
                status[s, i] = COMPLETED
                if start[s, i] < 0:
                    comp = [st for st in a['steps'] if st['step'] == 'completion_report' and st.get('receipt')]
                    start[s, i] = max(0, _day_of(comp[-1]['receipt']['at'], as_of)) if comp else 0
    return status, start, flags, program


def funnel_rates(status, flags):
    k = max(status.shape[0], 1)
    n = status.shape[1]
    return {'patients': int(n), 'outreach': round(float(flags['outreach'].sum() / k), 1), 'enrolled': round(float(flags['enrolled'].sum() / k), 1),
            'attended_first_visit': round(float(flags['attended'].sum() / k), 1), 'completed': round(float(flags['completed'].sum() / k), 1),
            'p_completed_per_patient': round(float(flags['completed'].mean()), 3) if n else 0.0}


def default_arms(kind, plan, cat):
    """Default comparison for a program kind: the routed plan plus each alternative program."""
    routed = sorted({r['program_id'] for r in plan['patients'] if r.get('route') == 'enroll' and r.get('program_id')})
    arms = [{'id': 'plan', 'label': 'Routed plan (' + ', '.join(routed) + ')' if routed else 'Routed plan'}]
    for p in catmod.programs_of(cat, kind):
        if p['id'] in routed and len(routed) > 1:
            arms.append({'id': p['id'], 'label': f"{p['name']} only (patients it accepts)", 'programs': [p['id']]})
        elif p['id'] not in routed:
            arms.append({'id': p['id'], 'label': f"{p['name']} for every routed patient (hypothetical)", 'force_program': p['id']})
    return arms


def normalize_arms(alternatives, kind, plan, cat):
    if not alternatives:
        return default_arms(kind, plan, cat)
    progs = {p['id']: p for p in cat['programs']}
    routed = {r['program_id'] for r in plan['patients'] if r.get('route') == 'enroll' and r.get('program_id')}
    arms = []
    for a in alternatives:
        if isinstance(a, str):
            if a in ('plan', 'routed'):
                arms.append({'id': 'plan', 'label': 'Routed plan'})
            elif a in ('do_nothing', 'baseline'):
                continue
            elif a in progs:
                arms.append({'id': a, 'label': f"{progs[a]['name']} only (patients it accepts)", 'programs': [a]} if a in routed else
                            {'id': a, 'label': f"{progs[a]['name']} for every routed patient (hypothetical)", 'force_program': a})
            else:
                raise ValueError(f'Unknown alternative {a!r}: use plan, a program id, or an arm dict')
        elif isinstance(a, dict):
            if not a.get('id'):
                raise ValueError('Each arm needs an id')
            arms.append(dict(a))
        else:
            raise ValueError('alternatives: a list of program ids or arm dicts')
    ids = [a['id'] for a in arms]
    if len(set(ids)) != len(ids) or 'do_nothing' in ids:
        raise ValueError('Arm ids must be unique and not do_nothing')
    return arms
