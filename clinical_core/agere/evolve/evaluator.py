"""Evaluator: score an outreach policy against the population simulator under a capacity limit.

Setting (Task): a program kind (e.g. pharmacy_gdmt), the deterministic router's plan at as_of, a capacity of C patients per
quarter (optionally also pharmacist hours per quarter, with hours per program variant), a 12-month horizon. A policy ranks
the routed patients and picks a program variant (or none) for each; patients are then filled into quarterly waves in rank
order until the capacity of every quarter in the horizon is used. A patient in wave q starts the program q quarters later
(the simulator delays that patient's first visit by q x 91 days, so later waves get less of the effect inside the horizon
but pay the same program costs).

Simulator runs (Dataset.build). For every Monte Carlo seed and every wave offset, engine.simulate runs once on the full
routed cohort with one forced arm per program variant and return_draws=True. The engine draws every patient's events,
funnel and effects independently given the shared parameter draws (common random numbers across arms and offsets), so a
policy's outcome is the sum of the selected patients' columns at their variant and wave: net savings = gross medical
savings - program cost, admissions / ED visits avoided, gap closures (patients with a new pillar started), pharmacy cost.
Training fitness uses per-patient means (exact for these additive outcomes); intervals use the draws. confirm() re-runs
the engine on the selected subset alone as a check of the decomposition.

Equity check: within every subgroup by line of business and by age band with at least `equity_min_group` routed patients,
the share of patients contacted must be at least `equity_floor` x the overall share contacted. A policy failing it is
infeasible (ranked below every feasible policy).

Splits: patients are split by a salted hash of the patient id (train / held-out); seeds are split into training seeds and
held-out seeds; a backdated as_of is a separate Dataset. Capacity is scaled to the share of patients in the split.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
import hashlib
import math

import numpy as np

from ..plans import catalog as catmod, router
from ..simulator import effects as fx, engine
from ..simulator.data import History
from . import features as featmod, sandbox

METRICS = ('saved', 'program_cost', 'pharmacy_cost', 'admissions_avoided', 'ed_visits_avoided', 'new_therapy', 'pillars_started')
OBJECTIVES = {
    'net_savings': 'gross medical savings minus program cost (USD, 12 months; pharmacy cost reported separately)',
    'admissions_avoided': 'admissions avoided over the horizon',
    'gap_closures': 'patients who start at least one missing GDMT pillar',
}
EQUITY_GROUPS = ('line_of_business', 'age_band')
DEFAULT_PROGRAM_HOURS = {'pharmacy_gdmt_titration': 3.0, 'pharmd_barrier_review': 1.5,
                         'access_virtual_pt_msk': 0.5, 'plan_benefit_virtual_pt': 0.5}


@dataclass
class Task:
    program_kind: str = 'pharmacy_gdmt'
    as_of: str | None = None
    horizon_months: int = 12
    capacity_per_quarter: int = 100
    hours_per_quarter: float | None = None
    program_hours: dict = field(default_factory=lambda: dict(DEFAULT_PROGRAM_HOURS))
    wave_days: int = 91
    iterations: int = 400
    funnel_scenarios: int = 50
    equity_floor: float = 0.5
    equity_min_group: int = 10
    split_salt: str = 'agere-evolve/1'
    heldout_fraction: float = 0.3
    parameters: dict | None = None

    @property
    def n_waves(self):
        return max(1, int(self.horizon_months * 30.44 // self.wave_days))

    def to_dict(self):
        return asdict(self)


def split_of(pid, salt, heldout_fraction):
    u = int(hashlib.sha256(f'{salt}|{pid}'.encode()).hexdigest()[:12], 16) / float(16 ** 12)
    return 'heldout' if u < heldout_fraction else 'train'


class Dataset:
    """Per-patient simulator columns for one (workspace, as_of, task) and a set of seeds."""

    def __init__(self, task, pids, rows, programs, cols, seeds, plan_digest, as_of, timings=None):
        self.task, self.pids, self.rows, self.programs = task, pids, rows, programs
        self.cols, self.seeds, self.plan_digest, self.as_of = cols, list(seeds), plan_digest, as_of
        self.variants = [p['id'] for p in programs]
        self.splits = {s: np.array([i for i, p in enumerate(pids) if split_of(p, task.split_salt, task.heldout_fraction) == s], int)
                       for s in ('train', 'heldout')}
        self.splits['all'] = np.arange(len(pids))
        self.timings = timings or {}
        self._means = {}

    @classmethod
    def build(cls, semantic_path, task, seeds, *, catalog=None, plan=None, log=None):
        cat = catalog or catmod.load()
        if plan is None:
            ids = router.candidates(semantic_path, task.program_kind, task.as_of, catalog=cat)
            plan = router.route(semantic_path, ids, task.program_kind, task.as_of, catalog=cat)
        h = History.load(semantic_path)
        pids, rows = featmod.patient_features(semantic_path, plan, history=h)
        progs = [p for p in catmod.programs_of(cat, plan['program_kind'])]
        sim_costs = (fx.resolve(fx.load_config(), task.parameters)['programs'])
        programs = [{'id': p['id'], 'name': p.get('name'), 'role': p.get('role') or 'standard',
                     'hours': float(task.program_hours.get(p['id'], 1.0)),
                     'placeholder_costs_usd': {k: v.get('value') for k, v in ((sim_costs.get(p['id']) or {}).get('costs') or {}).items()}}
                    for p in progs]
        arms = [{'id': f"v:{p['id']}", 'label': p['id'], 'force_program': p['id']} for p in programs]
        cols, timings = {}, {}
        for seed in seeds:
            for q in range(task.n_waves):
                out = engine.simulate(semantic_path, plan, horizon_months=task.horizon_months, alternatives=arms,
                                      iterations=task.iterations, funnel_scenarios=task.funnel_scenarios, seed=int(seed),
                                      catalog=cat, per_patient=False, start_offset_days=q * task.wave_days,
                                      parameters=task.parameters, return_draws=True)
                d = out['draws']
                assert d['cohort'] == pids, 'simulator cohort order differs from the feature rows'
                for p in programs:
                    a = d['arms'][f"v:{p['id']}"]
                    cols[(int(seed), p['id'], q)] = {m: np.asarray(a[m], np.float32) for m in METRICS}
                timings[f'{seed}:{q}'] = out['timings']['total_s']
                if log:
                    log(f"simulated seed {seed} wave {q} ({out['timings']['total_s']}s)")
        return cls(task, pids, rows, programs, cols, seeds, router.digest(plan), plan['as_of'], timings)

    # --- per-patient means (training fitness) -------------------------------------------------------------------------

    def means(self, seeds=None):
        key = tuple(seeds or self.seeds)
        if key not in self._means:
            m = {}
            for v in self.variants:
                for q in range(self.task.n_waves):
                    m[(v, q)] = {k: np.mean([self.cols[(s, v, q)][k].mean(0) for s in key], axis=0) for k in METRICS}
            self._means[key] = m
        return self._means[key]

    def patient_value(self, i, variant, wave, seeds=None):
        c = self.means(seeds)[(variant, wave)]
        return {'net_savings': float(c['saved'][i] - c['program_cost'][i]), 'admissions_avoided': float(c['admissions_avoided'][i]),
                'gap_closures': float(c['new_therapy'][i]), 'pharmacy_cost': float(c['pharmacy_cost'][i])}


# --- selection -------------------------------------------------------------------------------------------------------------

def capacity_for(task, n_split, n_total):
    frac = n_split / n_total if n_total else 1.0
    cap = max(1, int(round(task.capacity_per_quarter * frac)))
    hours = None if task.hours_per_quarter is None else float(task.hours_per_quarter) * frac
    return cap, hours


def assign_waves(order, choices, programs, cap, hours, n_waves):
    """order: patient positions best first; -> [(i, program, wave)] filling quarterly capacity in order."""
    ph = {p['id']: p['hours'] for p in programs}
    out, wave, used_n, used_h = [], 0, 0, 0.0
    for i in order:
        h = ph.get(choices[i], 1.0)
        while wave < n_waves and (used_n + 1 > cap or (hours is not None and used_h + h > hours + 1e-9)):
            wave, used_n, used_h = wave + 1, 0, 0.0
        if wave >= n_waves:
            break
        out.append((int(i), choices[i], wave))
        used_n += 1
        used_h += h
    return out


def select(ds, scores, choices, split='train'):
    """Policy outputs over all rows -> assignment for the split (capacity scaled to the split)."""
    idx = ds.splits[split]
    cap, hours = capacity_for(ds.task, len(idx), len(ds.pids))
    elig = [i for i in idx if scores[i] is not None and choices[i] is not None]
    order = sorted(elig, key=lambda i: (-scores[i], i))
    return assign_waves(order, choices, ds.programs, cap, hours, ds.task.n_waves)


# --- scoring ---------------------------------------------------------------------------------------------------------------

def equity(ds, assignment, split='train'):
    idx = ds.splits[split]
    chosen = {i for i, _, _ in assignment}
    overall = len(chosen) / len(idx) if len(idx) else 0.0
    groups, ok, worst = [], True, 0.0
    for g in EQUITY_GROUPS:
        by = {}
        for i in idx:
            by.setdefault(ds.rows[i][g], []).append(i)
        for val, members in sorted(by.items(), key=lambda kv: str(kv[0])):
            rate = sum(1 for i in members if i in chosen) / len(members)
            checked = len(members) >= ds.task.equity_min_group and overall > 0
            passed = (not checked) or rate >= ds.task.equity_floor * overall - 1e-12
            shortfall = max(0.0, ds.task.equity_floor * overall - rate) if checked else 0.0
            worst = max(worst, shortfall)
            ok &= passed
            groups.append({'group': g, 'value': val, 'patients': len(members), 'contacted': sum(1 for i in members if i in chosen),
                           'rate': round(rate, 4), 'checked': checked, 'passed': passed})
    return {'passed': bool(ok), 'overall_rate': round(overall, 4), 'floor_ratio': ds.task.equity_floor,
            'worst_shortfall': round(worst, 4), 'groups': groups}


def score_means(ds, assignment, seeds=None):
    m = ds.means(seeds)
    tot = dict.fromkeys(METRICS, 0.0)
    for i, v, q in assignment:
        c = m[(v, q)]
        for k in METRICS:
            tot[k] += float(c[k][i])
    return {'net_savings': tot['saved'] - tot['program_cost'], 'admissions_avoided': tot['admissions_avoided'],
            'gap_closures': tot['new_therapy'], 'gross_savings': tot['saved'], 'program_cost': tot['program_cost'],
            'pharmacy_cost': tot['pharmacy_cost'], 'net_savings_incl_pharmacy': tot['saved'] - tot['program_cost'] - tot['pharmacy_cost'],
            'ed_visits_avoided': tot['ed_visits_avoided'], 'pillars_started': tot['pillars_started']}


def score_draws(ds, assignment, seeds=None):
    """Per-draw totals (seeds stacked) for intervals: {metric: (len(seeds) * N,)}."""
    seeds = list(seeds or ds.seeds)
    out = {k: [] for k in ('net_savings', 'admissions_avoided', 'gap_closures', 'program_cost', 'pharmacy_cost', 'gross_savings')}
    by = {}
    for i, v, q in assignment:
        by.setdefault((v, q), []).append(i)
    for s in seeds:
        N = ds.task.iterations
        acc = {k: np.zeros(N) for k in METRICS}
        for (v, q), ii in by.items():
            c = ds.cols[(s, v, q)]
            for k in METRICS:
                acc[k] += c[k][:, ii].sum(1)
        out['net_savings'].append(acc['saved'] - acc['program_cost'])
        out['admissions_avoided'].append(acc['admissions_avoided'])
        out['gap_closures'].append(acc['new_therapy'])
        out['program_cost'].append(acc['program_cost'])
        out['pharmacy_cost'].append(acc['pharmacy_cost'])
        out['gross_savings'].append(acc['saved'])
    return {k: np.concatenate(v) for k, v in out.items()}


def interval(a, lo=0.05, hi=0.95):
    a = np.asarray(a, float)
    return {'mean': round(float(a.mean()), 2), 'p05': round(float(np.quantile(a, lo)), 2), 'p95': round(float(np.quantile(a, hi)), 2)}


def fitness(scores, eq, objective):
    """Scalar for ranking: feasible policies by the objective; infeasible ones below all feasible (by shortfall)."""
    if not eq['passed']:
        return -1e12 - 1e9 * eq['worst_shortfall']
    return float(scores[objective])


class Evaluator:
    """Runs a policy's code in the sandbox once over every feature row and scores it on any split / seed set."""

    def __init__(self, ds, timeout_s=sandbox.DEFAULT_TIMEOUT_S):
        self.ds, self.timeout_s = ds, timeout_s
        self._cache = {}

    def outputs(self, code):
        key = hashlib.sha256(code.encode()).hexdigest()
        if key not in self._cache:
            self._cache[key] = sandbox.run(code, self.ds.rows, self.ds.programs, timeout_s=self.timeout_s)
        return self._cache[key]

    def evaluate(self, code, split='train', seeds=None, draws=False):
        scores, choices = self.outputs(code)
        a = select(self.ds, scores, choices, split)
        res = {'split': split, 'seeds': list(seeds or self.ds.seeds), 'contacted': len(a),
               'by_wave': [sum(1 for _, _, q in a if q == w) for w in range(self.ds.task.n_waves)],
               'by_program': {v: sum(1 for _, p, _ in a if p == v) for v in self.ds.variants},
               'eligible_in_split': len(self.ds.splits[split]), 'metrics': score_means(self.ds, a, seeds),
               'equity': equity(self.ds, a, split)}
        res['fitness'] = {o: fitness(res['metrics'], res['equity'], o) for o in OBJECTIVES}
        if draws:
            d = score_draws(self.ds, a, seeds)
            res['intervals'] = {k: interval(v) for k, v in d.items()}
            res['_draws'] = d
        res['_assignment'] = a
        return res


def behavior(res, ds, split='train'):
    """MAP-Elites descriptors: (program cost per contacted-capacity share, gap closures share), both 0..1."""
    m = res['metrics']
    idx = ds.splits[split]
    mx = ds.means()
    v0 = ds.variants[0]
    max_cost = float(sum(mx[(v0, 0)]['program_cost'][i] for i in idx)) or 1.0
    max_close = float(sum(max(mx[(v, 0)]['new_therapy'][i] for v in ds.variants) for i in idx)) or 1.0
    return min(1.0, max(0.0, m['program_cost'] / max_cost)), min(1.0, max(0.0, m['gap_closures'] / max_close))


def confirm(semantic_path, ds, assignment, seed, *, catalog=None, iterations=2000):
    """Re-run the engine on the selected patients alone (their variant and wave delay) - the decomposition check."""
    cat = catalog or catmod.load()
    ids = router.candidates(semantic_path, ds.task.program_kind, ds.as_of, catalog=cat)
    plan = router.route(semantic_path, ids, ds.task.program_kind, ds.as_of, catalog=cat)
    want = {ds.pids[i]: (v, q) for i, v, q in assignment}
    rows, offs = [], {}
    progs = {p['id']: p for p in cat['programs']}
    for r in plan['patients']:
        if r.get('route') != 'enroll' or r['patient_id'] not in want:
            continue
        v, q = want[r['patient_id']]
        r2 = dict(r)
        if v != r.get('program_id'):
            slot = (progs[v].get('vendor_slots') or [None])[0]
            r2['program_id'] = v
            r2['vendor_slot'] = {**(slot or {}), 'assignment_reason': 'evolve: policy program choice'}
        rows.append(r2)
        offs[r['patient_id']] = q * ds.task.wave_days
    variant = {**plan, 'patients': rows, 'summary': router.summarize(rows)}
    out = engine.simulate(semantic_path, variant, horizon_months=ds.task.horizon_months, alternatives=['plan'],
                          iterations=iterations or ds.task.iterations, funnel_scenarios=ds.task.funnel_scenarios, seed=int(seed),
                          catalog=cat, per_patient=False, start_offset_days=offs, parameters=ds.task.parameters)
    row = next(r for r in out['summary_table'] if r['arm'] == 'plan')
    q = row.get('quality') or {}
    return {'patients': len(rows), 'net_savings': row['net_savings'], 'admissions_avoided': row['admissions_avoided'],
            'gap_closures': q.get('patients_with_new_therapy'), 'program_cost': row['program_cost'],
            'pharmacy_cost': row['pharmacy_cost_added'], 'simulation_id': out['id']}


def finite(x):
    return x is not None and isinstance(x, (int, float)) and math.isfinite(x)
