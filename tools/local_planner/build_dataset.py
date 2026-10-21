"""Assemble the normalizer training set from canonical questions, verified rewordings and unsupported questions.

Every positive pair keeps the client's numbers and negation (local_normalizer.guard) and its canonical target compiles in
the free parser. Unsupported questions must not parse. Whole canonical questions are held out for validation, so the
validation score measures wording the model has not seen mapped to that target. Bank questions are excluded.

Usage: python tools/local_planner/build_dataset.py
"""
import json
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from clinical_core.agere.clinical import local_normalizer as N, request_plan as R, request_language as L  # noqa: E402

DATA = ROOT / 'artifacts' / 'agere-local-planner-v1'
BANK = Path('/Users/mikel/agere-eval-bank-2/bank.json')


def norm(q): return q.strip().rstrip('?.').lower()


def example(question, answer):
    return {'messages': [*N.prompt(question), {'role': 'assistant', 'content': answer}]}


def main():
    rng = random.Random(23)
    catalog = R.Catalog.load(ROOT / 'artifacts' / 'agere-healthcare-v28-sample-0.30' / 'stack')
    bank = {norm(i['question']) for i in json.loads(BANK.read_text())['items']}
    canon = {json.loads(l)['canonical'] for l in (DATA / 'canonical.jsonl').open()}
    pairs, stats = [], Counter()
    # Hand-written rewordings count twice: they carry real client phrasing; scripted ones are supplementary.
    sources = [(f, 1) for f in sorted((DATA / 'batches').glob('out_*.jsonl'))] + [(f, 2) for f in sorted((DATA / 'hand').glob('out_*.jsonl'))]
    for f, weight in sources:
        for line in f.open():
            try: row = json.loads(line)
            except ValueError: stats['bad_json'] += 1; continue
            c, w = row.get('canonical'), (row.get('rewrite') or '').strip()
            if c not in canon or not w: stats['unknown_canonical'] += 1; continue
            if norm(w) in bank or norm(c) in bank: stats['bank_overlap'] += 1; continue
            reason = N.guard(w, c)
            if reason: stats['guard_rejected'] += 1; continue
            pairs.extend([(w, c)] * weight); stats['kept_hand' if weight > 1 else 'kept'] += 1
    negatives = []
    for line in (DATA / 'batches' / 'negatives.jsonl').open():
        try: q = json.loads(line)['question'].strip()
        except (ValueError, KeyError): continue
        if norm(q) in bank: continue
        try:
            L.parse(catalog, q); stats['negative_parses'] += 1; continue  # the free parser reads it: not a negative
        except Exception:  # noqa: BLE001
            negatives.append(q)
    targets = sorted({c for _, c in pairs}); rng.shuffle(targets)
    held = set(targets[:max(1, len(targets) // 10)])
    train = [example(w, c) for w, c in pairs if c not in held] + [example(c, c) for c in targets if c not in held]
    valid = [example(w, c) for w, c in pairs if c in held]
    rng.shuffle(negatives); cut = len(negatives) // 10
    train += [example(q, N.UNSUPPORTED) for q in negatives[cut:]]
    valid += [example(q, N.UNSUPPORTED) for q in negatives[:cut]]
    rng.shuffle(train); rng.shuffle(valid)
    out = DATA / 'mlx'; out.mkdir(exist_ok=True)
    for name, rows in (('train', train), ('valid', valid), ('test', valid)):
        (out / f'{name}.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
    stats.update(train=len(train), valid=len(valid), negatives=len(negatives), held_out_targets=len(held))
    print(json.dumps(dict(stats), indent=1))


if __name__ == '__main__':
    main()
