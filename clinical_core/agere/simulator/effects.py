"""Simulator parameters (config.json) and the intervention effect models.

Parameters. Each has a point value and a range (uniform). draw(rng, n) returns n draws, or the point value repeated when
parameter uncertainty is switched off. Overrides replace a parameter by a number (fixed) or {value, low, high}.

GDMT pillar combination (never a product of hazard ratios). The only combined multi-pillar effect is gdmt.combination_hr
(quadruple therapy vs none, van Essen 2025 network meta-analysis, ev:hf_combination_effect_valid). A patient who starts a
subset S of pillars gets

    HR(S) = combination_hr ** (sum_{p in S} w_p / sum_{all four} w_p),   w_p = -log(single-trial HR of pillar p)

i.e. the combined log hazard ratio is allocated across pillars in proportion to each pillar's single-trial log hazard
ratio. The single-trial hazard ratios are used only as relative weights; they are never multiplied together, and starting
all four pillars reproduces combination_hr exactly (0.36), not the product 0.79 x 0.66 x 0.63 x 0.74 = 0.244 that the
negative-knowledge pack forbids. assert_not_forbidden() rejects any combined value within tolerance of a forbidden value.

The realised effect on HF-related events is 1 - real_world_effect_fraction x (1 - HR(S)); only admissions / ED visits
whose principal diagnosis is HF (share measured from the workspace) are affected, from the first fill onward.

Physical therapy after an ED low back pain visit: rate ratios on back-pain ED visits and on other back-pain spending
(explicit assumptions with ranges; no cited effect is loaded), full effect when the program is completed, a partial share
when only the evaluation was attended, from the evaluation onward.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / 'config.json'
PILLARS = ('rasi', 'beta_blocker', 'mra', 'sglt2i')


class ForbiddenEffectError(ValueError):
    pass


def load_config(path=None):
    return json.loads(Path(path or CONFIG_PATH).read_text())


def resolve(cfg, overrides=None):
    """Config with parameter overrides applied: {id: number | {value, low, high}}; program cost overrides as
    'cost.<program_id>.<item>'; ACCESS OAP terms as 'oap.<program_id>.<field>'."""
    cfg = copy.deepcopy(cfg)
    for pid, v in (overrides or {}).items():
        if pid.startswith('cost.'):
            _, prog, item = pid.split('.', 2)
            slot = cfg['programs'][prog]['costs'].setdefault(item, {'kind': 'override'})
        elif pid.startswith('oap.'):  # 'oap.<program_id>.<field>' (ACCESS outcome-aligned payment terms)
            _, prog, item = pid.split('.', 2)
            slot = cfg['programs'][prog]['oap'].setdefault(item, {'kind': 'override'})
        else:
            if pid not in cfg['parameters']:
                raise ValueError(f'Unknown simulator parameter {pid!r}')
            slot = cfg['parameters'][pid]
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            slot.update(value=float(v), low=float(v), high=float(v))
        elif isinstance(v, dict) and 'value' in v:
            slot.update(value=float(v['value']), low=float(v.get('low', v['value'])), high=float(v.get('high', v['value'])))
        else:
            raise ValueError(f'Override for {pid} must be a number or {{value, low, high}}')
        slot['overridden'] = True
    return cfg


def draw(spec, rng, n, uncertain=True):
    v, lo, hi = float(spec['value']), float(spec.get('low', spec['value'])), float(spec.get('high', spec['value']))
    if not uncertain or hi <= lo:
        return np.full(n, v)
    return rng.uniform(lo, hi, size=n)


def assert_not_forbidden(value, cfg):
    for f in cfg.get('forbidden') or []:
        vals = np.atleast_1d(np.asarray(value, float))
        if np.any(np.abs(vals - float(f['value'])) <= float(f.get('tolerance', 0.01))):
            raise ForbiddenEffectError(f"Combined effect {float(vals[np.argmin(np.abs(vals - float(f['value'])))]):.3f} is within "
                                       f"{f.get('tolerance', 0.01)} of the forbidden value {f['value']} ({f['id']}): {f['reason']} "
                                       f"Use {f['use_instead']}.")


def pillar_weights(pillar_hrs):
    """{pillar: share of the combined log hazard ratio}; pillar_hrs = {pillar: single-trial HR (array or float)}."""
    w = {p: -np.log(np.asarray(pillar_hrs[p], float)) for p in PILLARS}
    tot = sum(w.values())
    return {p: w[p] / tot for p in PILLARS}


def gdmt_hr(started_share, combination_hr):
    """HR for a patient who starts pillars with total weight share `started_share` (0..1), anchored to the combined
    effect. started_share = 1 (all four pillars) returns combination_hr itself."""
    return np.power(np.asarray(combination_hr, float), np.asarray(started_share, float))


def combined_hr(pillars, pillar_hrs, combination_hr, cfg=None):
    """Convenience for one set of pillars (used by tests and the parameter table)."""
    w = pillar_weights(pillar_hrs)
    share = sum(w[p] for p in pillars)
    hr = gdmt_hr(share, combination_hr)
    if cfg is not None:
        assert_not_forbidden(hr, cfg)
    return hr


def parameter_table(cfg, used=None):
    rows = []
    for pid, s in cfg['parameters'].items():
        if used is not None and pid not in used:
            continue
        rows.append({'id': pid, 'value': s['value'], 'range': [s.get('low', s['value']), s.get('high', s['value'])], 'unit': s.get('unit'),
                     'kind': 'override' if s.get('overridden') else s.get('kind'),
                     'source': s.get('source') or s.get('why'), **({'evidence_id': s['evidence_id']} if s.get('evidence_id') else {}),
                     **({'outcome': s['outcome']} if s.get('outcome') else {})})
    return rows


def cost_table(cfg, program_ids):
    rows = []
    for prog in program_ids:
        for item, s in ((cfg['programs'].get(prog) or {}).get('costs') or {}).items():
            if s.get('value') or s.get('high'):
                rows.append({'id': f'cost.{prog}.{item}', 'value': s['value'], 'range': [s.get('low', s['value']), s.get('high', s['value'])],
                             'unit': 'USD', 'kind': 'override' if s.get('overridden') else s.get('kind'), 'source': s.get('why')})
    return rows


def beta_share(k, n, rng, size, uncertain=True):
    """Draws of a measured share k / n (Jeffreys Beta posterior)."""
    if n <= 0:
        return np.zeros(size)
    if not uncertain:
        return np.full(size, (k + 0.5) / (n + 1.0))
    return rng.beta(k + 0.5, n - k + 0.5, size=size)


def check_config(cfg):
    """The four pillar weights and the combination parameter must exist; starting all four must reproduce the
    combination value (not the product of the single-trial hazard ratios)."""
    p = cfg['parameters']
    hrs = {x: p[f'gdmt.pillar_hr.{x}']['value'] for x in PILLARS}
    full = float(combined_hr(PILLARS, hrs, p['gdmt.combination_hr']['value'], cfg))
    assert math.isclose(full, p['gdmt.combination_hr']['value'], rel_tol=1e-9)
    return True
