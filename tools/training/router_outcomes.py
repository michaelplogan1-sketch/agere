"""Outcome-labelled router dataset: label = the cascade tier whose CHECKED answer was right.

Sources:
  traffic   router-outcomes-*.jsonl that learning_hooks writes for every verified cascaded answer (accepted tier, the
            tiers tried before it and their outcomes). Corrected questions (corrections-*.jsonl) are dropped.
  replay    labeled factory questions asked through the real cascade (replay(), integrator-scheduled; it runs the stack):
            the accepted tier is the label only when its answer matches the exact label; a mismatch is kept as a negative
            ('wrong_tier') and never as a positive.
build() writes <out>/tier_router/{train,valid,heldout}.jsonl ({question, label, tried, wrong_tiers, how, group}), split by
template family (traffic: a hash bucket of the normalized question), the format train_all.train_tier_router reads.

Usage: python tools/training/router_outcomes.py --traffic artifacts/training/traffic --replay artifacts/training/replay \
         --out artifacts/training/datasets
       python tools/training/router_outcomes.py --replay-run --labels artifacts/training/labels --limit 200 --out ...
"""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

VERSION = 'router-outcomes/1'


def norm(q): return ' '.join(str(q).strip().rstrip('?.!').split()).casefold()


def _jsonl(paths):
    for p in paths:
        for line in Path(p).read_text().splitlines():
            if line.strip():
                try: yield json.loads(line)
                except ValueError: continue


def traffic_rows(dirs):
    out = []
    for d in [Path(x) for x in dirs if x]:
        corrected = {norm(r.get('question', '')) for r in _jsonl(sorted(d.glob('corrections-*.jsonl')))}
        for r in _jsonl(sorted(d.glob('router-outcomes-*.jsonl'))):
            if not r.get('accepted') or norm(r['question']) in corrected: continue
            group = f"traffic/{int(hashlib.sha256(norm(r['question']).encode()).hexdigest()[:4], 16) % 64}"
            out.append({'question': r['question'], 'label': r['accepted'], 'tried': r.get('tried') or [], 'wrong_tiers': [],
                        'how': 'verified_traffic', 'group': group, 'paid': r.get('paid')})
    return out


def comparable(answer):
    """Comparable identity of an answer: a number, a patient-set digest, or a row signature."""
    from tools.training import label as Lb
    if not isinstance(answer, dict): return None
    if answer.get('patient_ids_sha'): return 'set:' + answer['patient_ids_sha']
    if answer.get('patient_ids'): return 'set:' + Lb.signature(sorted(answer['patient_ids']))
    v = answer.get('value', answer.get('count'))
    if isinstance(v, (int, float)) and not isinstance(v, bool): return f'num:{round(float(v), 6)}'
    rows = answer.get('rows') or answer.get('table')
    if isinstance(rows, list) and rows:
        from clinical_core.agere.clinical import bottled
        return 'rows:' + bottled.signature([list(r.values()) if isinstance(r, dict) else r for r in rows[:50]])
    if isinstance(v, dict): return 'val:' + json.dumps(v, sort_keys=True, default=str)
    return None


def outcome(label_row, result):
    """(label tier or None, wrong tier or None) for one replayed question."""
    interp = ((result or {}).get('planner_receipt') or {}).get('interpretation') or {}
    tier = (interp.get('proposer') or {}).get('name') or interp.get('interpreter')
    got, want = comparable((result or {}).get('answer')), comparable(label_row['answer'])
    if (result or {}).get('answer', {}).get('status') != 'answered' or not tier or got is None or want is None: return None, None
    return (tier, None) if got == want else (None, tier)


def replay_rows(files):
    return [r for r in _jsonl(files) if r.get('label') or r.get('wrong_tiers')]


def replay(stack, labeled, out, limit=None, wording='paraphrase'):
    """Ask labeled questions through handle_compose (real cascade) and write replay-<ts>.jsonl outcome rows."""
    from clinical_core.agere.clinical import conversation
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    path = out / f'replay-{time.strftime("%Y%m%d-%H%M%S")}.jsonl'
    n = 0
    with path.open('w') as f:
        for r in labeled:
            if limit and n >= limit: break
            texts = [p['text'] for p in r.get('paraphrases') or [] if not p.get('previous')] if wording == 'paraphrase' else []
            q = texts[0] if texts else r['canonical']
            try:
                result = conversation.handle_compose(stack, {'question': q})
            except Exception as exc:  # noqa: BLE001 - a failure is not a label
                result = {'answer': {'status': f'error:{type(exc).__name__}'}}
            good, bad = outcome(r, result)
            interp = (result.get('planner_receipt') or {}).get('interpretation') or {}
            row = {'question': q, 'label': good, 'wrong_tiers': [bad] if bad else [], 'how': 'labeled_replay',
                   'group': r['template_family'], 'tried': [(e.get('proposer'), e.get('outcome')) for e in interp.get('proposers') or []],
                   'label_id': r['id']}
            f.write(json.dumps(row, default=str) + '\n'); f.flush(); n += 1
    return str(path)


def build(traffic=(), replays=(), out=None):
    from tools.training.datasets import split_of, Bench
    bench = Bench()
    rows = traffic_rows(traffic) + [r for r in replay_rows(replays)]
    seen, parts, stats = set(), {'train': [], 'valid': [], 'heldout': []}, {'rows': 0, 'positives': 0, 'negatives_only': 0, 'benchmark_excluded': 0}
    for r in rows:
        key = (norm(r['question']), r.get('label'))
        if key in seen: continue
        seen.add(key)
        if bench.near(r['question']): stats['benchmark_excluded'] += 1; continue
        stats['rows'] += 1
        stats['positives' if r.get('label') else 'negatives_only'] += 1
        parts[split_of(r['group'])].append(r)
    d = Path(out) / 'tier_router'; d.mkdir(parents=True, exist_ok=True)
    for split, items in parts.items():
        (d / f'{split}.jsonl').write_text(''.join(json.dumps(i, default=str) + '\n' for i in items))
    labels = {}
    for r in parts['train']:
        if r.get('label'): labels[r['label']] = labels.get(r['label'], 0) + 1
    manifest = {'version': VERSION, 'stats': stats, 'rows': {k: len(v) for k, v in parts.items()}, 'train_labels': labels}
    (d / 'manifest.json').write_text(json.dumps(manifest, indent=1))
    return manifest


def main(argv=None):
    from tools.training.question_factory import DEFAULT_OUT
    ap = argparse.ArgumentParser()
    ap.add_argument('--traffic', default=str(DEFAULT_OUT / 'traffic')); ap.add_argument('--replay', default=str(DEFAULT_OUT / 'replay'))
    ap.add_argument('--out', default=str(DEFAULT_OUT / 'datasets'))
    ap.add_argument('--replay-run', action='store_true', help='ask labeled questions through the live stack first (heavy)')
    ap.add_argument('--labels', default=str(DEFAULT_OUT / 'labels')); ap.add_argument('--limit', type=int, default=200)
    a = ap.parse_args(argv)
    if a.replay_run:
        from run_integrated_stack import keys
        keys()
        from clinical_core.agere.clinical.stack_runtime import Stack
        from tools.training.datasets import rows
        build_dir = Path(json.loads((ROOT / 'config/agere/active_warehouse.json').read_text())['build'])
        print(replay(Stack(build_dir / 'stack', allow_live=True), rows(a.labels), a.replay, a.limit))
    replays = sorted(Path(a.replay).glob('replay-*.jsonl')) if Path(a.replay).exists() else []
    print(json.dumps(build([a.traffic], replays, a.out), indent=1))


if __name__ == '__main__':
    main()
