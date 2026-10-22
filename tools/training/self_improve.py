"""The self-improving learning loop as ONE runnable job: harvested traffic and corrections -> datasets -> retrain -> promote.

  1. harvest   what learning_hooks.after_answer wrote since the last run (<training>/traffic): verified answers
               (labels-traffic), outcome rows (router-outcomes: which tier's checked answer was right), corrections, gaps.
  2. loop      tools/learning_loop over the stack's learning store (read-only): gap clusters, regression cases from
               corrections, bottling pairs, the bottled retrain decision.
  3. datasets  datasets.build over the factory labels + traffic (corrected questions excluded, template-family splits,
               benchmarks excluded) and router_outcomes.build (the outcome-labelled tier-router set).
  4. retrain   train_all.run on those datasets when due (>= --min-new new verified rows since the last retrain, or --force):
               router / shape / tier_router heads now; LoRA learners run with --allow-mlx (one heavy job at a time) and are
               otherwise written as deferred commands.
  5. promote   train_all promotes only on held-out wins (receipts under <runs>/promotions); the runtime picks promoted
               models up by pointer (local_normalizer.promoted_adapter, tier_router.model_path, typed adapter pointer).
State (last run, row counts) lives in <training>/self-improve/state.json; every run writes a receipt next to it.
Scheduled: tools/training/overnight_chain.sh runs it after the corpus steps (no separate service).

Usage: python tools/training/self_improve.py [--force] [--allow-mlx] [--install] [--learners router,shape,tier_router]
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

VERSION = 'self-improve/1'
HEADS = ['router', 'shape', 'tier_router']
LORAS = ['normalizer', 'typed', 'bottled', 'narrow']


def training_root():
    if os.environ.get('AGERE_TRAINING_DIR'): return Path(os.environ['AGERE_TRAINING_DIR'])
    return next((p / 'artifacts' / 'training' for p in (ROOT, *ROOT.parents) if (p / 'artifacts' / 'training').exists()),
                ROOT / 'artifacts' / 'training')


def count_lines(paths):
    return sum(sum(1 for l in Path(p).read_text().splitlines() if l.strip()) for p in paths)


def harvest(traffic):
    traffic = Path(traffic)
    g = lambda pat: sorted(traffic.glob(pat)) if traffic.exists() else []
    return {'verified_rows': count_lines(g('labels-traffic-*.jsonl')), 'router_outcomes': count_lines(g('router-outcomes-*.jsonl')),
            'corrections': count_lines(g('corrections-*.jsonl')), 'gaps': count_lines(g('gaps-*.jsonl'))}


def run(*, root=None, stack=None, labels=None, force=False, min_new=200, learners=None, allow_mlx=False, install=False,
        train=None, loop=True):
    root = Path(root or training_root())
    traffic = root / 'traffic'
    state_dir = root / 'self-improve'; state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / 'state.json'
    state = json.loads(state_file.read_text()) if state_file.exists() else {'retrained_at_rows': 0}
    stamp = time.strftime('%Y%m%d-%H%M%S')
    receipt = {'version': VERSION, 'at': stamp, 'steps': {}}
    got = harvest(traffic); receipt['steps']['harvest'] = got
    if loop and stack:
        try:
            from tools.learning_loop.loop import run as loop_run
            summary = loop_run(Path(stack), state_dir / 'learning-loop', name=Path(stack).parent.name)
            receipt['steps']['learning_loop'] = {k: v for k, v in summary.items() if isinstance(v, (int, float, str, bool))}
            receipt['steps']['learning_loop']['retrain'] = summary.get('retrain')
        except Exception as exc:  # noqa: BLE001 - one step failing never stops the next
            receipt['steps']['learning_loop'] = {'error': f'{type(exc).__name__}: {exc}'[:300]}
    label_dirs = [str(d) for d in (labels or [root / 'labels', root / 'labels-1m']) if Path(d).exists()] + ([str(traffic)] if traffic.exists() else [])
    ds = root / 'datasets-self-improve' / stamp
    try:
        from tools.training import datasets as D
        from clinical_core.agere.clinical import request_plan as R
        tables = R.Catalog.load(Path(stack)).tables if stack else None
        manifest = D.build(label_dirs, ds, catalog_tables=tables) if label_dirs else None
        receipt['steps']['datasets'] = {'dir': str(ds), 'labels': label_dirs,
                                        'learners': {k: v['rows'] for k, v in (manifest or {}).get('learners', {}).items()}}
    except Exception as exc:  # noqa: BLE001
        receipt['steps']['datasets'] = {'error': f'{type(exc).__name__}: {exc}'[:300]}
    try:
        from tools.training import router_outcomes as RO
        replays = sorted((root / 'replay').glob('replay-*.jsonl')) if (root / 'replay').exists() else []
        receipt['steps']['router_outcomes'] = RO.build([traffic], replays, ds)
    except Exception as exc:  # noqa: BLE001
        receipt['steps']['router_outcomes'] = {'error': f'{type(exc).__name__}: {exc}'[:300]}
    new = got['verified_rows'] + got['router_outcomes'] - state.get('retrained_at_rows', 0)
    due = force or new >= min_new
    receipt['steps']['due'] = {'new_rows_since_retrain': new, 'min_new': min_new, 'forced': force, 'due': due}
    if due and 'error' not in receipt['steps']['datasets']:
        from tools.training import train_all as T
        train = train or T.run
        try:
            summary = train(str(ds), str(root / 'runs' / f'self-improve-{stamp}'), stack, False, learners or HEADS + LORAS,
                            allow_mlx, install, False)
            receipt['steps']['retrain'] = summary
            state['retrained_at_rows'] = got['verified_rows'] + got['router_outcomes']
            state['last_retrain'] = stamp
            receipt['steps']['promoted'] = sorted(k for k, v in (summary.get('results') or {}).items()
                                                  if isinstance(v, dict) and v.get('decision') == 'promoted')
        except Exception as exc:  # noqa: BLE001
            receipt['steps']['retrain'] = {'error': f'{type(exc).__name__}: {exc}'[:300]}
    state['last_run'] = stamp
    state_file.write_text(json.dumps(state, indent=1))
    (state_dir / f'run-{stamp}.json').write_text(json.dumps(receipt, indent=1, default=str))
    return receipt


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--root'); ap.add_argument('--stack'); ap.add_argument('--force', action='store_true')
    ap.add_argument('--min-new', type=int, default=200); ap.add_argument('--learners', default='')
    ap.add_argument('--allow-mlx', action='store_true'); ap.add_argument('--install', action='store_true')
    ap.add_argument('--no-loop', action='store_true')
    a = ap.parse_args(argv)
    from tools.training.question_factory import active_stack
    stack = a.stack or str(active_stack())
    print(json.dumps(run(root=a.root, stack=stack, force=a.force, min_new=a.min_new, learners=[l for l in a.learners.split(',') if l] or None,
                         allow_mlx=a.allow_mlx, install=a.install, loop=not a.no_loop), indent=1, default=str))


if __name__ == '__main__':
    main()
