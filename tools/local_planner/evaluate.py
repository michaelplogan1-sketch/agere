"""Evaluate the local normalizer on held-out targets: exact canonical match, guard rejections, UNSUPPORTED handling.

A held-out row counts as correct when the model's output, after the guard, compiles to the same request as its
canonical target (different wording that compiles identically is still correct). Usage:
  AGERE_NORMALIZER_ADAPTER=path python tools/local_planner/evaluate.py [--limit N]
"""
import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from clinical_core.agere.clinical import local_normalizer as N, request_plan as R, request_language as L  # noqa: E402


def compiled(catalog, q):
    try:
        program = L.parse_program(catalog, q)
        if program is not None: return json.dumps(program, sort_keys=True)
        return R.compile_request(catalog, q, L.parse(catalog, q))['query']
    except Exception:  # noqa: BLE001
        return None


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--limit', type=int, default=400)
    args = ap.parse_args()
    catalog = R.Catalog.load(ROOT / 'artifacts' / 'agere-healthcare-v28-sample-0.30' / 'stack')
    rows = [json.loads(l) for l in (ROOT / 'artifacts' / 'agere-local-planner-v1' / 'mlx' / 'valid.jsonl').open()][:args.limit]
    c, seconds, misses = Counter(), [], []
    for r in rows:
        question, target = r['messages'][1]['content'], r['messages'][2]['content']
        t = time.time(); out = N.propose(question) or ''; seconds.append(time.time() - t)
        if target == N.UNSUPPORTED:
            c['neg_total'] += 1
            c['neg_declined' if out == N.UNSUPPORTED or N.guard(question, out) or not compiled(catalog, out) else 'neg_answered'] += 1
            continue
        c['pos_total'] += 1
        if out == N.UNSUPPORTED: c['pos_declined'] += 1; continue
        if N.guard(question, out): c['pos_guard_rejected'] += 1; continue
        got, want = compiled(catalog, out), compiled(catalog, target)
        if got is None: c['pos_not_compiled'] += 1
        elif got == want: c['pos_correct'] += 1
        else: c['pos_wrong'] += 1; misses.append({'question': question, 'target': target, 'output': out})
    seconds.sort()
    print(json.dumps({**c, 'median_s': round(seconds[len(seconds) // 2], 2), 'p95_s': round(seconds[int(len(seconds) * .95)], 2)}, indent=1))
    for m in misses[:15]: print('WRONG', json.dumps(m))


if __name__ == '__main__':
    main()
