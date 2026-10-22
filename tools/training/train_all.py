"""Retrain every local learner from the labeled datasets, evaluate on HELD-OUT template families, promote only winners.

  router, shape   MiniLM features (learned_router.features) + sklearn logistic regression, temperature fitted on the valid
                  split; scored on held-out families against the current model (router: learned_router_model.json; shape:
                  the majority-class baseline when no model exists).
  normalizer, typed, bottled, narrow
                  MLX LoRA on Qwen3-4B, run under `caffeinate -i`; scored on held-out families by EXECUTION, not text:
                  normalizer = its rewording compiles (free parser) to the labeled answer; typed = its contract compiles and
                  executes to the labeled answer; bottled = its SQL executes to the labeled answer; narrow = its slots
                  round-trip to the labeled slots. Heavy jobs run only with --allow-mlx (one at a time, integrator-scheduled);
                  otherwise the MLX data and the exact command are written and the step is recorded as deferred.
A candidate is promoted only when it beats the current model on the same held-out rows; every decision writes a
promotion receipt under <out>/promotions/. --install copies a promoted router into place; LoRA adapters are promoted
by pointer (<out>/promoted/<learner>.json -> set AGERE_NORMALIZER_ADAPTER etc.). --seed-stores seeds a stack's free
tiers with exact pairs (datasets.seed_stores). --smoke uses tiny data and few iterations.

Usage: python tools/training/train_all.py --datasets artifacts/training/datasets --smoke
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

VERSION = 'training-orchestrator/1'
BASE_MODEL = Path(os.environ.get('AGERE_BASE_MODEL') or next((p / 'models' / 'qwen3-4b' for p in (ROOT, *ROOT.parents)
                                                             if (p / 'models' / 'qwen3-4b').exists()), ROOT / 'models' / 'qwen3-4b'))
MARGIN = 0.005  # a candidate must beat the current model by at least this held-out accuracy
LORA = {'normalizer': {'max_seq_length': 512, 'iters': 2000, 'batch_size': 4},
        'typed': {'max_seq_length': 2048, 'iters': 3000, 'batch_size': 1},
        'bottled': {'max_seq_length': 4608, 'iters': 1500, 'batch_size': 1},
        'narrow': {'max_seq_length': 768, 'iters': 1200, 'batch_size': 2}}


def load(path, limit=None):
    if not Path(path).exists(): return []
    out = []
    with Path(path).open() as f:
        for line in f:
            out.append(json.loads(line))
            if limit and len(out) >= limit: break
    return out


def receipt(out, learner, decision, **detail):
    d = Path(out) / 'promotions'; d.mkdir(parents=True, exist_ok=True)
    body = {'orchestrator': VERSION, 'learner': learner, 'decision': decision, 'at': time.strftime('%Y-%m-%dT%H:%M:%S'), **detail}
    path = d / f'{learner}-{time.strftime("%Y%m%d-%H%M%S")}.json'
    path.write_text(json.dumps(body, indent=1, default=str))
    return {**body, 'receipt': str(path)}


def decide(candidate, current, margin=MARGIN):
    """Promote only on a held-out win over the current model (or the baseline when there is none)."""
    if candidate is None: return 'not_promoted', 'no candidate score'
    if current is None: return 'promoted', 'no current model'
    if candidate >= current + margin: return 'promoted', f'held-out {candidate:.4f} beats current {current:.4f}'
    return 'not_promoted', f'held-out {candidate:.4f} does not beat current {current:.4f} by {margin}'


# ------------------------------------------------------------------------------------------- MiniLM logistic heads
def encoder_for(stack):
    from clinical_core.local_encoders import LocalEncoder
    run = json.loads((Path(stack) / 'run.json').read_text())
    tmp = tempfile.mkdtemp(prefix='agere-train-enc-')
    enc = LocalEncoder(run['encoder_path'], pooling='mean', cache=Path(tmp) / 'cache.sqlite3')
    if enc.identity != run['encoder_identity']: raise SystemExit('encoder identity differs from the stack')
    return enc, run['encoder_identity']


def fit_head(train, valid, labels, encoder, C=4.0):
    from sklearn.linear_model import LogisticRegression
    from clinical_core.agere.clinical import learned_router as LR
    from tools.learned_routing.train import fit_temperature
    X, Xv = LR.features([r['question'] for r in train], encoder), LR.features([r['question'] for r in valid], encoder)
    y, yv = np.array([labels.index(r['label']) for r in train]), np.array([labels.index(r['label']) for r in valid])
    clf = LogisticRegression(C=C, max_iter=4000, class_weight='balanced')
    clf.fit(X, y)
    W = np.zeros((len(labels), X.shape[1])); b = np.full(len(labels), -30.0)
    if len(clf.classes_) == 2:  # sklearn keeps one row for a binary problem: logit(class 1) - logit(class 0)
        W[clf.classes_[0]], b[clf.classes_[0]] = 0.0, 0.0
        W[clf.classes_[1]], b[clf.classes_[1]] = clf.coef_[0], clf.intercept_[0]
    else:
        for i, cls in enumerate(clf.classes_): W[cls] = clf.coef_[i]; b[cls] = clf.intercept_[i]
    temperature = fit_temperature(Xv @ W.T + b, yv) if len(valid) else 1.0
    return {'W': W, 'b': b, 'temperature': temperature}


def head_accuracy(model, rows, labels, encoder):
    from clinical_core.agere.clinical import learned_router as LR
    if not rows: return None
    X = LR.features([r['question'] for r in rows], encoder)
    p = LR.softmax((X @ model['W'].T + model['b']) / model['temperature'])
    y = np.array([labels.index(r['label']) for r in rows])
    return float((p.argmax(1) == y).mean())


def train_router(ds, out, stack, encoder, identity, smoke, install):
    from clinical_core.agere.clinical import learned_router as LR
    from tools.learned_routing.train import choose_threshold
    lim = 2000 if smoke else None
    train, valid, held = (load(Path(ds) / 'router' / f'{s}.jsonl', lim) for s in ('train', 'valid', 'heldout'))
    if not train or len({r['label'] for r in train}) < 2: return receipt(out, 'router', 'skipped', reason='fewer than two labels in train')
    model = fit_head(train, valid, LR.LABELS, encoder)
    cand = head_accuracy(model, held, LR.LABELS, encoder)
    current = LR.load()
    cur = head_accuracy({'W': current['W'], 'b': current['b'], 'temperature': current['temperature']}, held, LR.LABELS, encoder) \
        if current and current.get('encoder_identity') == identity else None
    X = LR.features([r['question'] for r in held], encoder) if held else None
    threshold = 1.01
    if held:
        p = LR.softmax((X @ model['W'].T + model['b']) / model['temperature'])
        y = np.array([LR.LABELS.index(r['label']) for r in held])
        threshold = choose_threshold(p.max(1), p.argmax(1) == y)[0]
    decision, why = decide(cand, cur)
    # Second gate: never regress the curated acceptance set (bench/acceptance, test-only, as tools/learned_routing/train.py).
    acc_cand, acc_cur = acceptance_accuracy(model, encoder), (acceptance_accuracy(
        {'W': current['W'], 'b': current['b'], 'temperature': current['temperature']}, encoder) if cur is not None else None)
    if decision == 'promoted' and acc_cand is not None and acc_cur is not None and acc_cand < acc_cur:
        decision, why = 'not_promoted', f'{why}; but acceptance accuracy {acc_cand:.4f} < current {acc_cur:.4f}'
    d = Path(out) / 'models' / 'router'; d.mkdir(parents=True, exist_ok=True)
    path = d / f'model-{time.strftime("%Y%m%d-%H%M%S")}.json'
    path.write_text(json.dumps({'version': LR.VERSION, 'labels': LR.LABELS, 'cues': LR.cue_names(), 'encoder_identity': identity,
                                'lexical_scale': LR.LEXICAL_SCALE, 'temperature': model['temperature'], 'threshold': threshold,
                                'target_heldout_accuracy': 0.98, 'heldout': {'n': len(held), 'argmax_accuracy': cand},
                                'training': {'rows': len(train), 'source': 'tools/training (exact factory labels)', 'benchmarks_excluded': True},
                                'weights': np.round(model['W'], 6).tolist(), 'bias': np.round(model['b'], 6).tolist()}, separators=(',', ':')))
    installed = False
    if decision == 'promoted' and install and not smoke:
        shutil.copy(path, LR.MODEL_FILE); installed = True
    return receipt(out, 'router', decision, reason=why, candidate=str(path), heldout_rows=len(held), train_rows=len(train),
                   candidate_heldout_accuracy=cand, current_heldout_accuracy=cur, candidate_acceptance_accuracy=acc_cand,
                   current_acceptance_accuracy=acc_cur, installed=installed, smoke=smoke)


def acceptance_accuracy(model, encoder):
    from clinical_core.agere.clinical import learned_router as LR
    from tools.learned_routing.train import bench_sets
    try:
        cases = bench_sets()[0]
    except (OSError, ValueError):
        return None
    cases = [c for c in cases if c.get('expect_operation')]
    if not cases: return None
    X = LR.features([c['question'] for c in cases], encoder)
    p = LR.softmax((X @ model['W'].T + model['b']) / model['temperature'])
    return float(np.mean([LR.LABELS[int(i)] in {c['expect_operation'], *c.get('also_accept', [])} for i, c in zip(p.argmax(1), cases)]))


SHAPES = ['count', 'value', 'table', 'patient_set', 'composite']


def train_shape(ds, out, encoder, identity, smoke):
    lim = 2000 if smoke else None
    train, valid, held = (load(Path(ds) / 'shape' / f'{s}.jsonl', lim) for s in ('train', 'valid', 'heldout'))
    train, valid, held = ([r for r in x if r['label'] in SHAPES] for x in (train, valid, held))
    if not train or len({r['label'] for r in train}) < 2: return receipt(out, 'shape', 'skipped', reason='fewer than two labels in train')
    model = fit_head(train, valid, SHAPES, encoder)
    cand = head_accuracy(model, held, SHAPES, encoder)
    current_path = Path(out) / 'promoted' / 'shape.json'
    if current_path.exists():
        cur_raw = json.loads(current_path.read_text())
        cur = head_accuracy({'W': np.asarray(cur_raw['weights']), 'b': np.asarray(cur_raw['bias']), 'temperature': cur_raw['temperature']},
                            held, SHAPES, encoder) if cur_raw.get('encoder_identity') == identity else None
        basis = 'current promoted shape head'
    else:
        majority = max(SHAPES, key=lambda s: sum(r['label'] == s for r in train))
        cur = sum(r['label'] == majority for r in held) / max(1, len(held)) if held else None
        basis = f'majority-class baseline ({majority})'
    decision, why = decide(cand, cur)
    d = Path(out) / 'models' / 'shape'; d.mkdir(parents=True, exist_ok=True)
    path = d / f'model-{time.strftime("%Y%m%d-%H%M%S")}.json'
    body = {'version': 'answer-shape-head/1', 'labels': SHAPES, 'encoder_identity': identity, 'temperature': model['temperature'],
            'weights': np.round(model['W'], 6).tolist(), 'bias': np.round(model['b'], 6).tolist(), 'heldout_accuracy': cand}
    path.write_text(json.dumps(body, separators=(',', ':')))
    if decision == 'promoted' and not smoke:
        current_path.parent.mkdir(parents=True, exist_ok=True); shutil.copy(path, current_path)
    return receipt(out, 'shape', decision, reason=why, basis=basis, candidate=str(path), candidate_heldout_accuracy=cand,
                   current_heldout_accuracy=cur, heldout_rows=len(held), smoke=smoke)


# --------------------------------------------------------------------------------------------------- MLX LoRA jobs
def lora_command(data, adapter, learner, smoke):
    cfg = LORA[learner]
    iters = 3 if smoke else cfg['iters']
    cmd = [sys.executable, '-m', 'mlx_lm', 'lora', '--model', str(BASE_MODEL), '--train', '--data', str(data),
           '--adapter-path', str(adapter), '--fine-tune-type', 'lora', '--num-layers', '16', '--batch-size', str(1 if smoke else cfg['batch_size']),
           '--iters', str(iters), '--learning-rate', '1e-4', '--max-seq-length', str(cfg['max_seq_length']), '--mask-prompt',
           '--steps-per-report', '1' if smoke else '20', '--steps-per-eval', str(iters if smoke else 200), '--val-batches', '2' if smoke else '20',
           '--save-every', str(iters if smoke else 100), '--seed', '7']
    return (['caffeinate', '-i'] if shutil.which('caffeinate') else []) + cmd


def materialize(learner, ds, data, stack, smoke):
    from tools.training import datasets as D
    limit = 24 if smoke else None
    if learner == 'normalizer': return D.mlx_normalizer(Path(ds) / 'normalizer', data, limit)
    if learner == 'typed':
        from clinical_core.agere.clinical import request_plan as R
        return D.mlx_typed(Path(ds) / 'typed', data, R.Catalog.load(stack).tables, limit)
    if learner == 'bottled': return D.mlx_bottled(Path(ds) / 'bottled', data, stack, limit)
    if learner == 'narrow': return D.mlx_narrow(Path(ds) / 'narrow', data, limit)
    raise ValueError(learner)


def current_adapter(learner, out):
    from clinical_core.agere.clinical import local_normalizer, bottled
    pointer = Path(out) / 'promoted' / f'{learner}.json'
    if pointer.exists(): return Path(json.loads(pointer.read_text())['adapter'])
    return {'normalizer': local_normalizer.ADAPTER, 'bottled': bottled.ADAPTER_DIR,
            'narrow': ROOT / 'artifacts' / 'bottling' / 'adapters-narrow-v1', 'typed': None}[learner]


def train_lora(learner, ds, out, stack, smoke, allow_mlx, eval_limit):
    stamp = time.strftime('%Y%m%d-%H%M%S')
    data = Path(out) / 'mlx' / learner / stamp
    counts = materialize(learner, ds, data, stack, smoke)
    if not counts.get('train'): return receipt(out, learner, 'skipped', reason='no training rows', data=str(data))
    adapter = Path(out) / 'adapters' / learner / stamp
    cmd = lora_command(data, adapter, learner, smoke)
    if not allow_mlx:
        return receipt(out, learner, 'deferred', reason='MLX training is integrator-scheduled; rerun with --allow-mlx',
                       data=str(data), rows=counts, command=' '.join(cmd), smoke=smoke)
    started = time.time()
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    log = Path(out) / 'adapters' / learner / f'{stamp}.log'
    log.parent.mkdir(parents=True, exist_ok=True); log.write_text(proc.stdout + proc.stderr)
    if proc.returncode: return receipt(out, learner, 'failed', reason=f'mlx_lm exited {proc.returncode}', log=str(log), command=' '.join(cmd))
    held = load(Path(ds) / learner / 'heldout.jsonl', eval_limit)
    cand = evaluate_adapter(learner, adapter, held, stack)
    base = current_adapter(learner, out)
    cur = evaluate_adapter(learner, base, held, stack) if base and Path(base).exists() else None
    decision, why = decide(cand, cur)
    if decision == 'promoted' and not smoke:
        p = Path(out) / 'promoted' / f'{learner}.json'; p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({'adapter': str(adapter), 'heldout_accuracy': cand, 'at': stamp}, indent=1))
    return receipt(out, learner, decision, reason=why, adapter=str(adapter), current=str(base) if base else None,
                   candidate_heldout_accuracy=cand, current_heldout_accuracy=cur, heldout_rows=len(held),
                   train_seconds=round(time.time() - started, 1), rows=counts, smoke=smoke, log=str(log))


def evaluate_adapter(learner, adapter, held, stack):
    """Execution accuracy on held-out rows (see module docstring). Loads Qwen3-4B + adapter once."""
    if not held: return None
    from mlx_lm import load as mlx_load, generate
    from mlx_lm.sample_utils import make_sampler
    from clinical_core.agere.clinical import request_plan as R, bottled, local_normalizer as N
    from tools.training import datasets as D, label as Lb
    model, tok = mlx_load(str(BASE_MODEL), adapter_path=str(adapter))
    ctx = Lb.Context(stack, grammar_check=False)
    profile_as_of = None
    ok = 0
    for r in held:
        if learner == 'normalizer': msgs = N.prompt(r['input'])
        elif learner == 'typed': msgs = D.typed_messages(r['question'], ctx.catalog.tables, r['tables'])
        elif learner == 'bottled':
            from clinical_core.agere.clinical import source_profile
            profile_as_of = profile_as_of or (source_profile.load(stack), ctx.as_of)
            msgs = bottled.messages(r['question'], *profile_as_of)
        else: msgs = bottled.narrow_messages(r['question'])
        text = tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False, enable_thinking=False)
        out = generate(model, tok, prompt=text, max_tokens=700, sampler=make_sampler(temp=0.0), verbose=False).strip()
        try: ok += score(learner, r, out, ctx)
        except Exception:  # noqa: BLE001 - an output that does not compile/execute is simply wrong
            pass
    return ok / len(held)


def score(learner, r, out, ctx):
    from tools.training import label as Lb
    from tools.training.question_factory import resolve_request
    import re
    if learner == 'normalizer':
        target = resolve_request(ctx.catalog, r['target']); got = resolve_request(ctx.catalog, out.splitlines()[0].strip())
        if not (target and got): return False
        a = Lb.execute_contract(ctx, r['target'], target, Lb.compile_contract(ctx, r['target'], target))
        b = Lb.execute_contract(ctx, out, got, Lb.compile_contract(ctx, out.splitlines()[0].strip(), got))
        return Lb.answer_key(a) == Lb.answer_key(b)
    if learner == 'typed':
        value = json.loads(re.sub(r'^```(?:json)?\s*|\s*```$', '', out))
        kind = 'program' if 'program' in value else 'cohort_family_slots' if 'cohort_family_slots' in value else 'request'
        request = {'kind': kind, 'value': value.get('program') or value.get('cohort_family_slots') or value}
        if kind == 'cohort_family_slots':
            want = r['target'].get('cohort_family_slots')
            return bool(want) and Lb.answer_key(Lb.execute_slots(ctx, request['value'])[0]) == Lb.answer_key(Lb.execute_slots(ctx, want)[0])
        got = Lb.execute_contract(ctx, r['question'], request, Lb.compile_contract(ctx, r['question'], request))
        want_req = {'kind': 'program', 'value': r['target']['program']} if 'program' in r['target'] else {'kind': 'request', 'value': r['target']}
        want = Lb.execute_contract(ctx, r['question'], want_req, Lb.compile_contract(ctx, r['question'], want_req))
        return Lb.answer_key(got) == Lb.answer_key(want)
    if learner == 'bottled':
        from clinical_core.agere.clinical import bottled
        sql = re.search(r'```sql\s*(.*?)```', out, re.S)
        cols, rows = bottled.execute_ro(ctx.path, sql.group(1) if sql else out)
        _, want = bottled.execute_ro(ctx.path, r['sql'])
        return bottled.signature(rows) == bottled.signature(want)
    from clinical_core.agere.clinical import bottled
    return bottled.slots_key(bottled.parse_slots(out)) == bottled.slots_key(r['slots'])


# ----------------------------------------------------------------------------------------------------------- driver
def run(ds, out, stack=None, smoke=False, learners=None, allow_mlx=False, install=False, seed_stores=False, eval_limit=200):
    from tools.training.question_factory import active_stack, assert_public
    stack = assert_public(stack or active_stack())
    learners = learners or ['router', 'shape', 'normalizer', 'typed', 'bottled', 'narrow']
    results, started = {}, time.time()
    if any(l in learners for l in ('router', 'shape')):
        encoder, identity = encoder_for(stack)
        try:
            if 'router' in learners: results['router'] = train_router(ds, out, stack, encoder, identity, smoke, install)
            if 'shape' in learners: results['shape'] = train_shape(ds, out, encoder, identity, smoke)
        finally:
            encoder.close()
    for learner in ('normalizer', 'typed', 'bottled', 'narrow'):
        if learner in learners:
            results[learner] = train_lora(learner, ds, out, stack, smoke, allow_mlx, 20 if smoke else eval_limit)
    if seed_stores and not smoke:
        from tools.training import datasets as D
        results['seed_stores'] = D.seed_stores(stack, load(Path(ds) / 'cache' / 'train.jsonl'), dry_run=False)
    summary = {'orchestrator': VERSION, 'smoke': smoke, 'seconds': round(time.time() - started, 1),
               'results': {k: {x: v.get(x) for x in ('decision', 'reason', 'candidate_heldout_accuracy', 'current_heldout_accuracy', 'receipt', 'command')
                               if x in v} if isinstance(v, dict) and 'decision' in v else v for k, v in results.items()}}
    Path(out).mkdir(parents=True, exist_ok=True)
    (Path(out) / f'run-{"smoke" if smoke else "full"}-{time.strftime("%Y%m%d-%H%M%S")}.json').write_text(json.dumps(summary, indent=1, default=str))
    return summary


def main(argv=None):
    from tools.training.question_factory import DEFAULT_OUT
    ap = argparse.ArgumentParser()
    ap.add_argument('--datasets', default=str(DEFAULT_OUT / 'datasets')); ap.add_argument('--out', default=str(DEFAULT_OUT / 'runs'))
    ap.add_argument('--stack', default=None); ap.add_argument('--smoke', action='store_true')
    ap.add_argument('--learners', default=''); ap.add_argument('--allow-mlx', action='store_true')
    ap.add_argument('--install', action='store_true'); ap.add_argument('--seed-stores', action='store_true')
    ap.add_argument('--eval-limit', type=int, default=200)
    a = ap.parse_args(argv)
    print(json.dumps(run(a.datasets, a.out, a.stack, a.smoke, [l for l in a.learners.split(',') if l] or None,
                         a.allow_mlx, a.install, a.seed_stores, a.eval_limit), indent=1, default=str))


if __name__ == '__main__':
    main()
