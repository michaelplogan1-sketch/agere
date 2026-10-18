"""Evolution loop (FunSearch / AlphaEvolve style) and the held-out report.

    archive = evolve(ds, proposer, train_seeds=[...], generations=6)
    report = heldout_report(ds, archive, baselines, train_seeds, heldout_seeds, backdated=ds_2029)

1. The baselines (evolve/baselines.py) are evaluated and seeded into the MAP-Elites archive.
2. Each generation, for every objective: the two best programs for that objective plus one elite from another niche go to
   the proposer with their training scores and failure cases; every returned program is validated and run in the sandbox
   on the training patients and training seeds; invalid / failing / slow programs are recorded and discarded; valid ones
   enter the archive (kept if they beat the incumbent of their niche for some objective).
3. Best policy per objective = highest training fitness (feasible first). Only then are held-out seeds, held-out patients
   and the backdated as_of looked at (train vs held-out reported side by side to show over- or under-fitting).
Everything is deterministic given the seeds and the cached model responses (parent choice uses a seeded RNG).
"""
from __future__ import annotations

from collections import Counter
import random
import statistics
import time

import numpy as np

from . import evaluator as ev, features as featmod
from .archive import Archive
from .sandbox import SandboxError

GUIDANCE = {
    'net_savings': 'Program costs are paid for every contacted patient (outreach, first visit, completion) while savings come only '
                   'from avoided heart-failure admissions / ED visits; a patient whose expected contribution is negative lowers the '
                   'total, and returning None (no outreach) is allowed.',
    'admissions_avoided': 'Earlier waves get more months of effect; only heart-failure admissions / ED visits are affected.',
    'gap_closures': 'A closure counts when the patient starts at least one missing pillar; reach, enrollment and completion differ by '
                    'patient facts and program variant.',
}
PROFILE_KEYS = ('monthly_admission_rate', 'annual_admission_risk', 'expected_monthly_cost', 'hf_admission_6m', 'hf_admissions_12m',
                'admissions_12m', 'ed_visits_12m', 'missing_count', 'barrier_count', 'interpreter_needed', 'medicaid', 'age',
                'attributed', 'days_since_admission')
CASE_KEYS = ('age_band', 'line_of_business', 'missing_pillars', 'barrier_count', 'monthly_admission_rate', 'monthly_ed_rate',
             'hf_admissions_12m', 'hf_admission_6m', 'admissions_12m', 'ed_visits_12m', 'interpreter_needed', 'medicaid',
             'expected_monthly_cost', 'task_priority')


def feature_stats(rows):
    out = {}
    if not rows:
        return out
    for k in rows[0]:
        vals = [r[k] for r in rows if r.get(k) is not None]
        if not vals:
            out[k] = '[all None]'
        elif all(isinstance(v, bool) for v in vals):
            out[k] = f'[{sum(vals) / len(vals):.0%} true]'
        elif all(isinstance(v, (int, float)) for v in vals):
            nn = len(rows) - len(vals)
            out[k] = f'[min {min(vals):.4g}, median {statistics.median(vals):.4g}, max {max(vals):.4g}' + (f', {nn} None]' if nn else ']')
        elif all(isinstance(v, str) for v in vals):
            out[k] = '[' + ', '.join(f'{a}: {b}' for a, b in Counter(vals).most_common(6)) + ']'
        elif all(isinstance(v, list) for v in vals):
            out[k] = '[' + ', '.join(f'{a}: {b}' for a, b in Counter(x for v in vals for x in v).most_common(6)) + ']'
    return out


def _case(ds, i, value, why, wave=None, program=None):
    r = ds.rows[i]
    return {'why': why, 'value': round(value, 4 if abs(value) < 10 else 1), **({'wave': wave} if wave is not None else {}),
            **({'program': program} if program else {}), **{k: r[k] for k in CASE_KEYS if k in r}}


def failure_cases(ds, res, objective, seeds, k=4):
    """Selected patients contributing least, skipped (train) patients that would contribute most in the first free wave."""
    a = res['_assignment']
    chosen = {i for i, _, _ in a}
    val = lambda i, v, q: ds.patient_value(i, v, q, seeds)[objective]  # noqa: E731
    sel = sorted(((val(i, v, q), i, v, q) for i, v, q in a), key=lambda t: (t[0], t[1]))[:k]
    q_next = min(ds.task.n_waves - 1, max([q for _, _, q in a], default=0))
    skipped = []
    for i in ds.splits['train']:
        if i in chosen:
            continue
        best = max(((val(i, v, 0), v) for v in ds.variants), key=lambda t: t[0])
        skipped.append((best[0], i, best[1]))
    skipped.sort(key=lambda t: (-t[0], t[1]))
    out = [_case(ds, i, x, 'contacted but contributes little', q, v) for x, i, v, q in sel]
    out += [_case(ds, i, x, 'skipped or deferred beyond capacity; value if contacted in wave 0', None, v) for x, i, v in skipped[:k]]
    eq = res['equity']
    if not eq['passed']:
        bad = [g for g in eq['groups'] if not g['passed']]
        out.append({'why': 'equity floor failed', 'groups': [{k2: g[k2] for k2 in ('group', 'value', 'patients', 'contacted', 'rate')} for g in bad],
                    'overall_rate': eq['overall_rate']})
    del q_next
    return out


def profile(ds, assignment, split='train'):
    idx = ds.splits[split]
    chosen = {i for i, _, _ in assignment}
    out = {'contacted': len(chosen), 'not_contacted': len(idx) - len(chosen),
           'by_program': dict(Counter(v for _, v, _ in assignment)),
           'by_wave': dict(Counter(q for _, _, q in assignment))}
    for k in PROFILE_KEYS:
        def avg(ii):
            vals = [float(ds.rows[i][k]) for i in ii if ds.rows[i].get(k) is not None]
            return round(sum(vals) / len(vals), 4) if vals else None
        out[k] = {'contacted': avg([i for i in idx if i in chosen]), 'not_contacted': avg([i for i in idx if i not in chosen])}
    first = [i for i, _, q in assignment if q == 0]
    if first:
        out['first_wave_mean_admission_rate'] = round(sum(ds.rows[i]['monthly_admission_rate'] for i in first) / len(first), 5)
    return out


def _entry(eid, code, origin, res, ds, generation, parents=(), objective=None):
    return {'id': eid, 'code': code, 'origin': origin, 'generation': generation, 'parents': list(parents), 'for_objective': objective,
            'status': 'valid', 'fitness': res['fitness'], 'metrics': {k: round(v, 4) for k, v in res['metrics'].items()},
            'equity_passed': res['equity']['passed'], 'equity_worst_shortfall': res['equity']['worst_shortfall'],
            'contacted': res['contacted'], 'by_wave': res['by_wave'], 'by_program': res['by_program'],
            'behavior': [round(x, 4) for x in ev.behavior(res, ds)]}


def _parent_view(ds, e, res, objective, seeds):
    return {'id': e['id'], 'origin': e['origin'], 'code': e['code'], 'metrics': {k: round(v, 2) for k, v in res['metrics'].items()},
            'equity_summary': {'passed': res['equity']['passed'], 'overall_rate': res['equity']['overall_rate'],
                               'worst_shortfall': res['equity']['worst_shortfall']},
            'by_wave': res['by_wave'], 'by_program': res['by_program'], 'failures': failure_cases(ds, res, objective, seeds)}


def capacity_text(ds):
    cap, hours = ev.capacity_for(ds.task, len(ds.splits['train']), len(ds.pids))
    t = (f"{cap} patients per quarter for the {len(ds.splits['train'])} routed training patients "
         f"({ds.task.capacity_per_quarter}/quarter for the full population of {len(ds.pids)}), {ds.task.n_waves} quarters")
    if hours is not None:
        t += f", and {hours:.0f} pharmacist hours per quarter"
    return t


def evolve(ds, proposer, *, train_seeds, generations=6, objectives=None, seed=7, baselines=None, log=print, evaluator=None,
           max_seconds=None):
    objectives = list(objectives or ev.OBJECTIVES)
    E = evaluator or ev.Evaluator(ds)
    arch = Archive(ev.OBJECTIVES)
    results = {}
    t0 = time.time()

    def run(code, split='train'):
        return E.evaluate(code, split, seeds=train_seeds)

    for name, code in (baselines or {}).items():
        res = run(code)
        e = _entry(f'B:{name}', code, 'baseline', res, ds, 0)
        arch.add(e)
        results[e['id']] = res
        log(f"baseline {name}: " + ', '.join(f'{o}={res["metrics"][o]:.2f}' for o in ev.OBJECTIVES) + f" equity={res['equity']['passed']}")
    schema, stats = featmod.SCHEMA, feature_stats(ds.rows)
    history = []
    recent = {o: [] for o in objectives}   # attempts that did not beat the best, shown so the proposer does not repeat them
    for g in range(1, generations + 1):
        if max_seconds and time.time() - t0 > max_seconds:
            log('time budget reached')
            break
        for obj in objectives:
            rng = random.Random(f'{seed}|{g}|{obj}')
            parents = arch.best(obj, 2)
            if not parents:
                continue
            others = [e for e in arch.elites() if e['id'] not in {p['id'] for p in parents}]
            insp = [rng.choice(others)] if others else []
            ctx = {'objective': obj, 'objective_text': ev.OBJECTIVES[obj], 'generation': g, 'program_kind': ds.task.program_kind,
                   'programs': [{k: p[k] for k in ('id', 'role', 'hours', 'placeholder_costs_usd') if k in p} for p in ds.programs],
                   'capacity_text': capacity_text(ds), 'guidance': GUIDANCE.get(obj),
                   'equity_floor': ds.task.equity_floor, 'equity_min_group': ds.task.equity_min_group,
                   'feature_schema': schema, 'feature_stats': stats,
                   'parents': [_parent_view(ds, p, results[p['id']], obj, train_seeds) for p in parents],
                   'inspiration': [{'id': e['id'], 'code': e['code'], 'metrics': {k: round(v, 2) for k, v in e['metrics'].items()}} for e in insp],
                   'generations': generations, 'recent': recent[obj][-4:]}
            try:
                cands = proposer.propose(ctx)
            except Exception as exc:  # noqa: BLE001 -- a failed proposal call skips this slot
                log(f'gen {g} {obj}: proposer failed: {type(exc).__name__}: {str(exc)[:200]}')
                history.append({'generation': g, 'objective': obj, 'error': str(exc)[:300]})
                continue
            for j, c in enumerate(cands):
                eid = f'G{g:02d}:{obj}:{j}'
                if arch.seen(c['code']):
                    arch.add({'id': eid, 'code': c['code'], 'origin': c['origin'], 'status': 'duplicate', 'generation': g})
                    log(f'{eid}: duplicate of an archived program')
                    continue
                try:
                    res = run(c['code'])
                except SandboxError as exc:
                    arch.add({'id': eid, 'code': c['code'], 'origin': c['origin'], 'status': 'invalid', 'error': str(exc)[:300],
                              'generation': g, 'parents': [p['id'] for p in parents], 'for_objective': obj})
                    log(f'{eid}: invalid ({str(exc)[:120]})')
                    recent[obj].append({'id': eid, 'code': c['code'], 'result': 'invalid: ' + str(exc)[:160]})
                    continue
                e = _entry(eid, c['code'], c['origin'], res, ds, g, [p['id'] for p in parents], obj)
                improved = arch.add(e)
                results[eid] = res
                if obj not in improved:
                    recent[obj].append({'id': eid, 'code': c['code'], 'result': {k: round(v, 2) for k, v in res['metrics'].items()
                                                                                if k in ev.OBJECTIVES} | {'equity_passed': res['equity']['passed']}})
                log(f"{eid}: " + ', '.join(f'{o}={res["metrics"][o]:.2f}' for o in ev.OBJECTIVES)
                    + f" equity={res['equity']['passed']} niche={e.get('cell')} improved={improved}")
            if not cands:
                log(f'gen {g} {obj}: proposer returned no program')
            history.append({'generation': g, 'objective': obj, 'candidates': len(cands),
                            'best': {o: round(arch.best(o)[0]['fitness'][o], 2) for o in ev.OBJECTIVES}})
    arch.history = history
    arch.results = results
    return arch


def _diff(a, b):
    return ev.interval(np.asarray(a) - np.asarray(b))


def heldout_report(ds, arch, baselines, train_seeds, heldout_seeds, *, backdated=None, objectives=None, evaluator=None,
                   proposer=None, confirm_path=None, log=print):
    objectives = list(objectives or ev.OBJECTIVES)
    E = evaluator or ev.Evaluator(ds)
    Eb = ev.Evaluator(backdated) if backdated is not None else None
    policies = {f'baseline:{k}': c for k, c in baselines.items()}
    champions = {}
    for o in objectives:
        best = arch.best(o)[0]
        champions[o] = best
        policies[f'best:{o}'] = best['code']
    rows = {}
    for name, code in policies.items():
        tr = E.evaluate(code, 'train', seeds=train_seeds, draws=True)
        ho = E.evaluate(code, 'heldout', seeds=heldout_seeds, draws=True)
        cross_p = E.evaluate(code, 'heldout', seeds=train_seeds)
        cross_s = E.evaluate(code, 'train', seeds=heldout_seeds)
        row = {'train': {'metrics': _round(tr['metrics']), 'intervals': tr['intervals'], 'equity_passed': tr['equity']['passed'],
                         'contacted': tr['contacted']},
               'heldout': {'metrics': _round(ho['metrics']), 'intervals': ho['intervals'], 'equity': ho['equity'],
                           'contacted': ho['contacted'], 'by_wave': ho['by_wave'], 'by_program': ho['by_program']},
               'heldout_patients_train_seeds': _round(cross_p['metrics']), 'train_patients_heldout_seeds': _round(cross_s['metrics']),
               '_ho': ho, '_tr': tr}
        if Eb is not None:
            bd = Eb.evaluate(code, 'all', seeds=heldout_seeds, draws=True)
            row['backdated'] = {'as_of': backdated.as_of, 'metrics': _round(bd['metrics']), 'intervals': bd['intervals'],
                                'equity_passed': bd['equity']['passed'], 'contacted': bd['contacted'], '_bd': bd}
        rows[name] = row
    # paired differences (same simulator draws per patient: common random numbers)
    for name, row in rows.items():
        if not name.startswith('best:'):
            continue
        row['vs_baselines'] = {}
        for b in baselines:
            br = rows[f'baseline:{b}']
            d = {k: _diff(row['_ho']['_draws'][k], br['_ho']['_draws'][k]) for k in ('net_savings', 'admissions_avoided', 'gap_closures')}
            dt = {k: _diff(row['_tr']['_draws'][k], br['_tr']['_draws'][k]) for k in ('net_savings', 'admissions_avoided', 'gap_closures')}
            entry = {'heldout': d, 'train': dt}
            if 'backdated' in row:
                entry['backdated'] = {k: _diff(row['backdated']['_bd']['_draws'][k], br['backdated']['_bd']['_draws'][k])
                                      for k in ('net_savings', 'admissions_avoided', 'gap_closures')}
            row['vs_baselines'][b] = entry
    out = {'policies': {}, 'champions': {}}
    for name, row in rows.items():
        out['policies'][name] = {k: (_strip(v) if isinstance(v, dict) else v) for k, v in row.items() if not k.startswith('_')}
    for o, e in champions.items():
        tr = rows[f'best:{o}']['_tr']
        prof = profile(ds, tr['_assignment'], 'train')
        text = None
        if proposer is not None:
            try:
                text = proposer.explain(e['code'], prof)
            except Exception as exc:  # noqa: BLE001
                log(f'explanation failed: {exc}')
        out['champions'][o] = {'id': e['id'], 'origin': e['origin'], 'generation': e['generation'], 'code': e['code'],
                               'profile': prof, 'explanation': text, 'rule_summary': rule_summary(prof, ds)}
    if confirm_path is not None:
        out['engine_confirmation'] = {}
        for name in [f'best:{objectives[0]}', 'baseline:router_order']:
            if name not in rows:
                continue
            a = rows[name]['_ho']['_assignment']
            if not a:
                continue
            c = ev.confirm(confirm_path, ds, a, heldout_seeds[0])
            m = ev.score_means(ds, a, heldout_seeds)
            out['engine_confirmation'][name] = {'engine_subset_run_seed': heldout_seeds[0], 'engine_subset_run': c,
                                                'column_sum_heldout_seeds_mean': _round(m)}
            log(f'confirmed {name}: engine net {c["net_savings"]["mean"]} vs columns {m["net_savings"]:.1f}')
    return out


def rule_summary(prof, ds):
    """Deterministic one-liner from the profile (the LLM explanation is separate)."""
    parts = []
    for k, label in (('hf_admission_6m', 'an HF admission in the last 6 months'), ('interpreter_needed', 'interpreter needed'),
                     ('medicaid', 'Medicaid')):
        c, n = prof[k]['contacted'], prof[k]['not_contacted']
        if c is not None and n is not None:
            parts.append(f'{label}: {c:.0%} of contacted vs {n:.0%} of not contacted')
    for k, label in (('monthly_admission_rate', 'monthly admission rate'), ('missing_count', 'missing pillars'),
                     ('barrier_count', 'pillars with a documented barrier'), ('age', 'age')):
        c, n = prof[k]['contacted'], prof[k]['not_contacted']
        if c is not None and n is not None:
            parts.append(f'{label}: {c:.3g} vs {n:.3g}')
    return (f"Contacts {prof['contacted']} of {prof['contacted'] + prof['not_contacted']} training patients "
            f"(waves {dict(sorted(prof['by_wave'].items()))}, programs {prof['by_program']}); " + '; '.join(parts) + '.')


def _round(m):
    return {k: round(float(v), 2) for k, v in m.items()}


def _strip(d):
    return {k: (_strip(v) if isinstance(v, dict) else v) for k, v in d.items() if not str(k).startswith('_')}
