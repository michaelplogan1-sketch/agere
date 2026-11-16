"""Run an evolutionary policy search on a workspace and save the artifacts.

    .venv/bin/python -m clinical_core.agere.evolve --semantic <path/semantic.sqlite3> --kind pharmacy_gdmt \
        --capacity 100 --generations 6 --run pilot-gdmt-cap100 [--live] [--backdated 2029-05-27]

Without --live no model is called: the proposer replays cached responses only (a request that is not cached fails and
that slot is skipped). Artifacts: artifacts/evolve/<run>/ (run.json, archive.json, report.json, policies/<objective>.json
and .py, summary.txt); model requests and responses: artifacts/evolve/llm/ with the ledger artifacts/evolve/ledger.sqlite3.
"""
from __future__ import annotations

import argparse
import json
import pickle
import time
from pathlib import Path

from . import baselines as basemod, evaluator as ev, loop
from .proposer import LLMProposer

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'artifacts' / 'evolve'
POLICY_SCHEMA = 'agere-evolve-policy/1'
SIM_FIELDS = ('program_kind', 'as_of', 'horizon_months', 'wave_days', 'iterations', 'funnel_scenarios', 'parameters')


class _OfflineTransport:
    def __call__(self, *a, **k):
        raise RuntimeError('offline: response not cached (run with --live to call the model)')


def build_or_load(path, task, seeds, cache=None, log=print):
    if cache and Path(cache).is_file():
        ds = pickle.loads(Path(cache).read_bytes())
        sim = lambda t: {k: t.to_dict()[k] for k in SIM_FIELDS}  # noqa: E731
        if sim(ds.task) == sim(task) and list(ds.seeds) == list(seeds):
            log(f'loaded dataset cache {cache} (simulator columns; capacity / equity settings from this run)')
            progs = [{**p, 'hours': float(task.program_hours.get(p['id'], 1.0))} for p in ds.programs]
            return ev.Dataset(task, ds.pids, ds.rows, progs, ds.cols, ds.seeds, ds.plan_digest, ds.as_of, ds.timings)
    ds = ev.Dataset.build(path, task, seeds, log=log)
    if cache:
        Path(cache).parent.mkdir(parents=True, exist_ok=True)
        Path(cache).write_bytes(pickle.dumps(ds))
    return ds


def policy_artifact(objective, champ, report_row, task, run_name, ds):
    return {'schema': POLICY_SCHEMA, 'id': f'{run_name}:{objective}', 'objective': objective, 'program_kind': task.program_kind,
            'code': champ['code'], 'origin': champ['origin'], 'generation': champ['generation'],
            'task': {k: task.to_dict()[k] for k in ('capacity_per_quarter', 'hours_per_quarter', 'program_hours', 'wave_days',
                                                    'horizon_months', 'equity_floor', 'equity_min_group')},
            'trained_on': {'as_of': ds.as_of, 'plan_digest': ds.plan_digest, 'run': run_name},
            'heldout': report_row.get('heldout', {}).get('intervals'), 'explanation': champ.get('explanation'),
            'rule_summary': champ.get('rule_summary'),
            'approved': False, 'approved_by': None,
            'approval_note': 'Set approved: true (and reference this file in config/agere/evolve_policy.json with enabled: true) '
                             'only after review; the router ignores unapproved policies.'}


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--semantic', required=True)
    p.add_argument('--kind', default='pharmacy_gdmt')
    p.add_argument('--as-of')
    p.add_argument('--backdated')
    p.add_argument('--capacity', type=int, default=100)
    p.add_argument('--hours', type=float)
    p.add_argument('--generations', type=int, default=6)
    p.add_argument('--train-seeds', default='101,102,103')
    p.add_argument('--heldout-seeds', default='201,202,203')
    p.add_argument('--iterations', type=int, default=400)
    p.add_argument('--funnel-scenarios', type=int, default=50)
    p.add_argument('--run', required=True)
    p.add_argument('--model', default='claude-sonnet-5-5')
    p.add_argument('--per-call', type=int, default=2)
    p.add_argument('--live', action='store_true')
    p.add_argument('--cap-usd', type=float, default=30.0, help='ledger reservation cap (conservative $0.50 per call reserved)')
    p.add_argument('--cache-dir')
    p.add_argument('--seed', type=int, default=7)
    a = p.parse_args(argv)
    t0 = time.time()
    run_dir = OUT / a.run
    (run_dir / 'policies').mkdir(parents=True, exist_ok=True)
    logf = open(run_dir / 'log.txt', 'w')

    def log(msg):
        print(msg, flush=True)
        logf.write(msg + '\n')
        logf.flush()

    tr = [int(x) for x in a.train_seeds.split(',')]
    ho = [int(x) for x in a.heldout_seeds.split(',')]
    task = ev.Task(program_kind=a.kind, as_of=a.as_of, capacity_per_quarter=a.capacity, hours_per_quarter=a.hours,
                   iterations=a.iterations, funnel_scenarios=a.funnel_scenarios)
    cache = Path(a.cache_dir) if a.cache_dir else None
    ds = build_or_load(a.semantic, task, tr + ho, cache / f"ds-{a.kind}-{a.as_of or 'meta'}-{a.iterations}-{'-'.join(map(str, tr + ho))}.pkl" if cache else None, log)
    bd = None
    if a.backdated:
        btask = ev.Task(**{**task.to_dict(), 'as_of': a.backdated})
        bd = build_or_load(a.semantic, btask, ho, cache / f"ds-{a.kind}-{a.backdated}-{a.iterations}-{'-'.join(map(str, ho))}.pkl" if cache else None, log)
    t_sim = time.time() - t0
    from ..gateway import BudgetLedger
    if a.live:
        from clinical_core.agere.providers import load_keys as keys  # repository helper: loads provider keys into the environment
        keys()
    ledger = BudgetLedger(OUT / 'ledger.sqlite3', cap_usd=a.cap_usd)
    prop = LLMProposer(ledger, OUT / 'llm', model=a.model, n=a.per_call, transport=None if a.live else _OfflineTransport())
    bases = basemod.all_baselines()
    arch = loop.evolve(ds, prop, train_seeds=tr, generations=a.generations, baselines=bases, seed=a.seed, log=log)
    rep = loop.heldout_report(ds, arch, bases, tr, ho, backdated=bd, proposer=prop, confirm_path=a.semantic, log=log)
    spend = {'this_run': prop.spend(), 'ledger': ledger.receipt()}
    run = {'run': a.run, 'semantic': str(a.semantic), 'task': task.to_dict(), 'as_of': ds.as_of, 'backdated_as_of': a.backdated,
           'train_seeds': tr, 'heldout_seeds': ho, 'patients': {k: int(len(v)) for k, v in ds.splits.items()},
           'capacity_train': ev.capacity_for(task, len(ds.splits['train']), len(ds.pids)),
           'capacity_heldout': ev.capacity_for(task, len(ds.splits['heldout']), len(ds.pids)),
           'generations': a.generations, 'model': a.model, 'archive': arch.summary(), 'history': arch.history, 'spend': spend,
           'seconds': {'simulation_precompute': round(t_sim, 1), 'total': round(time.time() - t0, 1)},
           'synthetic_data_only': True, 'costs_are_placeholders': True}
    (run_dir / 'run.json').write_text(json.dumps(run, indent=1, default=str))
    (run_dir / 'archive.json').write_text(json.dumps([{k: v for k, v in e.items()} for e in arch.entries], indent=1, default=str))
    (run_dir / 'report.json').write_text(json.dumps(rep, indent=1, default=str))
    for o, champ in rep['champions'].items():
        art = policy_artifact(o, champ, rep['policies'][f'best:{o}'], task, a.run, ds)
        (run_dir / 'policies' / f'{o}.json').write_text(json.dumps(art, indent=1, default=str))
        (run_dir / 'policies' / f'{o}.py').write_text(f'# {a.run} best policy for {o} (generation {champ["generation"]}, '
                                                      f'{champ["origin"]}); not approved\n' + champ['code'])
    (run_dir / 'summary.txt').write_text(summary_text(run, rep))
    log(summary_text(run, rep))
    logf.close()
    return run, rep


def summary_text(run, rep):
    L = [f"Run {run['run']}: {run['task']['program_kind']} at {run['as_of']}, capacity {run['task']['capacity_per_quarter']}/quarter "
         f"(train {run['capacity_train'][0]}/q for {run['patients']['train']} patients, held-out {run['capacity_heldout'][0]}/q for "
         f"{run['patients']['heldout']}); {run['generations']} generations; archive {run['archive']}; spend {run['spend']['this_run']}."]
    for name, r in rep['policies'].items():
        h = r['heldout']['intervals']
        L.append(f"{name:32s} train net {r['train']['metrics']['net_savings']:>10.0f} | held-out net {h['net_savings']['mean']:>9.0f} "
                 f"[{h['net_savings']['p05']:.0f}, {h['net_savings']['p95']:.0f}] adm {h['admissions_avoided']['mean']:.2f} "
                 f"closures {h['gap_closures']['mean']:.1f} contacted {r['heldout']['contacted']} equity {r['heldout']['equity']['passed']}"
                 + (f" | backdated net {r['backdated']['intervals']['net_savings']['mean']:.0f}" if 'backdated' in r else ''))
    for o, c in rep['champions'].items():
        L.append(f"\nBest for {o}: {c['id']} ({c['origin']})\n{c['rule_summary']}\n{c.get('explanation') or ''}")
        vb = rep['policies'][f'best:{o}'].get('vs_baselines') or {}
        for b, d in vb.items():
            x = d['heldout'][o]
            L.append(f"  vs {b}: held-out {o} difference {x['mean']:+.2f} [{x['p05']:+.2f}, {x['p95']:+.2f}]")
    return '\n'.join(L) + '\n'


if __name__ == '__main__':
    main()
