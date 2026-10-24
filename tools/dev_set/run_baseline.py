"""Score the fair competitor baseline on the development question set with the same scorer as Agere.

The baseline is bench.adapters.frontier_semantic: a frontier model (Claude Opus by default) given the documented semantic
model (tables, column docs, concept code sets, governed metric definitions, verified example queries) and one read-only
SQL tool over the same semantic database. That is the shape of Databricks Genie, Snowflake Cortex Analyst, ChatGPT with
read-only warehouse access and similar "LLM + semantic layer" products. It never calls Agere's planner, checks or caches.

Follow-ups are sent as their resolved standalone question (key.standalone): the baseline keeps no conversation, so this is
the generous reading for it. Run once per milestone (build first, measure once); the held-out split is the comparison.

    .venv/bin/python tools/dev_set/run_baseline.py --split heldout --out <file.json>
"""
import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bench.adapters import MODEL, frontier_semantic  # noqa: E402
from bench.common import SemanticWarehouse  # noqa: E402

_spec = importlib.util.spec_from_file_location('dev_run', Path(__file__).with_name('run.py'))
RUN = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RUN)

SEMANTIC = ROOT / 'artifacts' / 'agere-healthcare-v28-sample-0.30' / 'stack' / 'semantic.sqlite3'


def as_response(result):
    """The baseline's answer contract in the response shape the dev-set scorer reads."""
    answer = result.get('answer') if isinstance(result.get('answer'), dict) else None
    if answer is None:
        return {'status': 'error', 'error': result.get('error') or 'no answer'}
    answer = dict(answer)
    answer.setdefault('headline', answer.get('value') if isinstance(answer.get('value'), str) else answer.get('answer'))
    return {'answer': answer, 'cost_receipt': {'usd': result.get('estimated_cost_usd'), 'paid_calls': result.get('new_paid_api_calls')}}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--split', choices=('dev', 'heldout', 'all'), default='heldout')
    ap.add_argument('--bucket')
    ap.add_argument('--ids', nargs='*')
    ap.add_argument('--model', default=MODEL)
    ap.add_argument('--max-tools', type=int, default=8)
    ap.add_argument('--semantic', type=Path, default=SEMANTIC)
    ap.add_argument('--out', type=Path)
    a = ap.parse_args(argv)
    qs = [json.loads(l) for l in open(ROOT / 'tools' / 'dev_set' / 'questions.jsonl')]
    sel = [q for q in qs if (a.split == 'all' or q['split'] == a.split) and (not a.bucket or q['bucket'] == a.bucket)
           and (not a.ids or q['id'] in a.ids)]
    semantic = SemanticWarehouse(a.semantic)
    as_of = str(semantic.as_of)[:10]
    rows = []
    for q in sel:
        text = q['key'].get('standalone') if q.get('follow_up_of') and q['key'].get('standalone') else q['question']
        t0 = time.time()
        result = frontier_semantic({'id': q['id'], 'question': text, 'as_of': as_of}, semantic, model=a.model, max_tools=a.max_tools)
        resp = as_response(result)
        correct, detail = RUN.score(q, resp)
        row = {'id': q['id'], 'bucket': q['bucket'], 'split': q['split'], 'asked': text, 'status': RUN.status_of(resp),
               'answered': RUN.status_of(resp) in RUN.ANSWERED, 'correct': correct, 'detail': detail, 'seconds': round(time.time() - t0, 2),
               'usd': result.get('estimated_cost_usd'), 'tool_calls': len(result.get('tool_calls') or []), 'model': result.get('model'),
               'error': result.get('error')}
        rows.append(row)
        print(f"{q['id']:>6} {q['bucket'][:14]:14} {str(row['status'])[:14]:14} correct={correct} {row['seconds']:.0f}s ${row['usd'] or 0:.3f}", flush=True)
    by = {}
    for r in rows:
        by.setdefault(r['bucket'], []).append(r)
    print('\n=== baseline per bucket ===')
    for b, rs in by.items():
        n, c, ans = len(rs), sum(1 for r in rs if r['correct']), sum(1 for r in rs if r['answered'])
        usd = sum(r['usd'] or 0 for r in rs)
        lat = sorted(r['seconds'] for r in rs)
        print(f"{b}\n  n={n} accuracy={c / n:.1%} answered={ans / n:.1%} precision_on_answered={(c / ans if ans else 0):.1%} "
              f"usd={usd:.2f} p50={lat[len(lat) // 2]:.0f}s")
    n, c = len(rows), sum(1 for r in rows if r['correct'])
    print(f"OVERALL n={n} accuracy={(c / n if n else 0):.1%} usd={sum(r['usd'] or 0 for r in rows):.2f}")
    if a.out:
        a.out.write_text(json.dumps({'model': a.model, 'split': a.split, 'rows': rows}, indent=1, default=str))


if __name__ == '__main__':
    main()
