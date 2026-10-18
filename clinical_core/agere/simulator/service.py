"""Workspace entry points for the simulator (Workspace.simulate / Workspace.compare_simulation and the API route).

Simulations are saved as <workspace>/simulations/<id>.json so a later compare-to-actual can read the prediction exactly as
it was issued.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..plans import catalog as catmod
from . import actual, engine

DIR = 'simulations'
PUBLIC_DROP = ('inputs',)


def _target(target, program_kind, question, cat):
    """plan dict | plan envelope | program kind | request text -> (plan, program_kind, question)."""
    if isinstance(target, dict):
        return (target['plan'] if 'plan' in target and isinstance(target['plan'], dict) else target), None, None
    if isinstance(target, str) and target.strip():
        t = target.strip()
        return (None, t, None) if t in catmod.kinds(cat) else (None, None, t)
    return None, program_kind, question


def simulate(ws, target=None, *, horizon_months=12, alternatives=None, program_kind=None, question=None, patient_ids=None,
             as_of=None, save=True, **kw):
    if not ws.semantic_path.is_file():
        raise RuntimeError(f'Workspace {ws.slug} has no semantic layer yet; run it first')
    cat = catmod.load()
    plan, kind, q = _target(target, program_kind, question, cat)
    if plan is None and not kind and not q and patient_ids is None:
        raise ValueError('a plan, program_kind or question is required')
    out = engine.simulate(ws.semantic_path, plan, program_kind=kind, question=q, patient_ids=patient_ids,
                          as_of=as_of or ws.state.get('as_of') or None, horizon_months=horizon_months, alternatives=alternatives,
                          workspace=ws.slug, catalog=cat, **kw)
    if save:
        d = Path(ws.dir) / DIR
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f"{out['id']}.json.tmp"
        tmp.write_text(json.dumps(out, default=str))
        tmp.replace(d / f"{out['id']}.json")
        out['saved'] = f"{DIR}/{out['id']}.json"
    return out


def load(ws, simulation_id):
    f = Path(ws.dir) / DIR / f'{simulation_id}.json'
    if not simulation_id.replace('-', '').isalnum() or not f.is_file():
        raise FileNotFoundError(f'No simulation {simulation_id!r} in workspace {ws.slug}')
    return json.loads(f.read_text())


def compare(ws, simulation_id, observed_through=None):
    sim = load(ws, simulation_id)
    return actual.compare_to_actual(ws.semantic_path, sim, observed_through)


def public(out):
    return {k: v for k, v in out.items() if k not in PUBLIC_DROP}
