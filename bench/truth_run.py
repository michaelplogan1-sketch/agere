"""Run a contestant on the generator's truth-backed question set and score it with the generator's sealed scorer.

The answers live only in the generator's private/ folder; this runner never reads them. It writes the scorer's contract
({id, answer_type, count|value|patient_ids|table, status, interpretation}) plus a sidecar with cost, latency and the raw
answer, then calls `python3 -m ecosystem.eval_scorer` (aggregates only).

    .venv/bin/python bench/truth_run.py --release healthcare-warehouse-v2.8 --system frontier_semantic \
        --model claude-haiku-5-5 --semantic-database <build>/stack/semantic.sqlite3 --mode defined --jobs 3
Modes: plain = the question only (how users ask); defined = question + the reproducible definition.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench.adapters import MODEL, agere, frontier_semantic
from bench.common import ROOT, SemanticWarehouse

ECOSYSTEM = Path('/Users/mikel/synthetic-patient-ecosystem')
SAMPLE = ROOT.parent / 'artifacts/healthcare-warehouse-v2.8-sample-0.30'
STATUS = {'answered': 'answered', 'unsupported': 'unsupported'}  # partial / grounding_failed / no answer -> abstain


def prompt(q, mode, as_of):
    text = q['question'] if mode == 'plain' else f"{q['question']} Definition: {q['definition']}"
    return f'{text} Use data received as of {as_of}.'


def _number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _scalar_from_table(table):
    if isinstance(table, list) and len(table) == 1 and isinstance(table[0], dict):
        nums = [v for v in table[0].values() if _number(v)]
        if len(nums) == 1:
            return nums[0]
    return None


def contract(q, answer):
    """The scorer's contract. Extraction rules are identical for every contestant."""
    answer = answer if isinstance(answer, dict) else {}
    out = {'id': q['id'], 'answer_type': q['answer_type'], 'status': STATUS.get(answer.get('status'), 'abstain'),
           'interpretation': ' '.join(str(g) for g in (answer.get('grounding') or []))[:1500]}
    kind = q['answer_type']
    if kind in ('count', 'value'):
        v = answer.get(kind)
        if v is None:
            v = answer.get('value' if kind == 'count' else 'count')
        if v is None:
            v = _scalar_from_table(answer.get('table'))
        out[kind] = v
    elif kind == 'patient_set':
        out['patient_ids'] = answer.get('patient_ids') or [r.get('patient_id') for r in answer.get('table') or []
                                                           if isinstance(r, dict) and r.get('patient_id')]
    else:
        rows = []
        for r in answer.get('table') or []:
            if not isinstance(r, dict) or not r:
                continue
            keys = [k for k, v in r.items() if not _number(v)]
            vals = [v for v in r.values() if _number(v)]
            if vals:
                rows.append({'key': str(r[keys[-1]]) if keys else str(list(r.values())[0]), 'value': vals[-1]})  # innermost label = the grouping
        out['table'] = rows
    if out['status'] == 'answered' and all(out.get(k) in (None, [], {}) for k in ('count', 'value', 'patient_ids', 'table')):
        out['status'] = 'abstain'
    return out


def run_one(q, args):
    item = {'id': q['id'], 'question': prompt(q, args.mode, args.as_of), 'as_of': args.as_of}
    start = time.perf_counter()
    try:
        if args.system == 'agere':
            row = agere(item, args.url)
        elif args.system == 'frontier_semantic':
            semantic = SemanticWarehouse(args.semantic_database, timeout_s=args.sql_timeout)
            try:
                row = frontier_semantic(item, semantic, model=args.model, max_tools=8)
            finally:
                semantic.close()
        else:
            from bench.adapters import frontier_canonical
            from bench.common import CanonicalWarehouse
            canonical = CanonicalWarehouse(args.canonical_database, timeout_s=args.sql_timeout)
            try:
                row = frontier_canonical(item, canonical, model=args.model, max_tools=8)
            finally:
                canonical.close()
    except Exception as exc:
        row = {'answer': None, 'error': f'{type(exc).__name__}: {exc}'}
    row.pop('response', None)
    return {'id': q['id'], 'domain': q['domain'], 'system': args.system, 'model': args.model if args.system != 'agere' else None,
            'mode': args.mode, 'latency_s': time.perf_counter() - start,
            'estimated_cost_usd': row.get('estimated_cost_usd'), 'error': row.get('error'),
            'answer': row.get('answer'), 'contract': contract(q, row.get('answer')), 'tool_calls': row.get('tool_calls')}


def score(release, path, per_question=False):
    cmd = ['python3', '-m', 'ecosystem.eval_scorer', '--release', f'releases/{release}', '--questions', str(path),
           '--sample-release', str(SAMPLE)] + (['--per-question'] if per_question else [])
    return subprocess.run(cmd, cwd=ECOSYSTEM, capture_output=True, text=True)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--release', default='healthcare-warehouse-v2.8')
    p.add_argument('--system', choices=['agere', 'frontier_semantic', 'frontier_canonical'], required=True)
    p.add_argument('--model', default=MODEL)
    p.add_argument('--mode', choices=['plain', 'defined'], default='defined')
    p.add_argument('--as-of', default='2030-05-27')
    p.add_argument('--url', default='http://127.0.0.1:8885')
    p.add_argument('--semantic-database', type=Path)
    p.add_argument('--canonical-database', type=Path)
    p.add_argument('--sql-timeout', type=float, default=180)
    p.add_argument('--jobs', type=int, default=3)
    p.add_argument('--ids', nargs='+')
    p.add_argument('--label', default='')
    p.add_argument('--rescore', type=Path, help='Re-derive the contract from a saved run directory and re-score (no model calls)')
    args = p.parse_args(argv)
    if args.rescore:
        args.rescore = args.rescore.resolve()
        qs = {json.loads(l)['id']: json.loads(l) for l in open(ECOSYSTEM / 'releases' / args.release / 'questions/questions.jsonl')}
        rows = [json.loads(l) for l in open(args.rescore / 'rows.jsonl')]
        with open(args.rescore / 'question_answers.jsonl', 'w') as f:
            for r in rows:
                r['contract'] = contract(qs[r['id']], r['answer'])
                f.write(json.dumps(r['contract'], default=str) + '\n')
        result = score(args.release, args.rescore / 'question_answers.jsonl')
        (args.rescore / 'score.txt').write_text(result.stdout + result.stderr)
        per = score(args.release, args.rescore / 'question_answers.jsonl', per_question=True)
        (args.rescore / 'per_question.txt').write_text(per.stdout + per.stderr)
        print(args.rescore)
        return
    questions = [json.loads(l) for l in open(ECOSYSTEM / 'releases' / args.release / 'questions/questions.jsonl')]
    if args.ids:
        ids = {i for a in args.ids for i in a.split(',')}
        questions = [q for q in questions if q['id'] in ids]
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    name = '-'.join(x for x in [args.system, args.model.replace('claude-', '') if args.system != 'agere' else '',
                                args.mode, args.label, stamp] if x)
    out = ROOT / 'results/truth' / args.release / name
    out.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(args.jobs) as pool:
        rows = list(pool.map(lambda q: run_one(q, args), questions))
    with open(out / 'rows.jsonl', 'w') as f:
        for r in rows:
            f.write(json.dumps(r, default=str) + '\n')
    with open(out / 'question_answers.jsonl', 'w') as f:
        for r in rows:
            f.write(json.dumps(r['contract'], default=str) + '\n')
    cost = sum(r['estimated_cost_usd'] or 0 for r in rows)
    lat = sorted(r['latency_s'] for r in rows)
    result = score(args.release, out / 'question_answers.jsonl')
    (out / 'score.txt').write_text(result.stdout + result.stderr)
    per = score(args.release, out / 'question_answers.jsonl', per_question=True)
    (out / 'per_question.txt').write_text(per.stdout + per.stderr)
    print(out)
    print(f'n={len(rows)} cost=${cost:.2f} (${cost / max(len(rows), 1):.4f}/q) latency median {lat[len(lat) // 2]:.1f}s '
          f'p90 {lat[int(len(lat) * .9)]:.1f}s errors={sum(1 for r in rows if r["error"])}')
    print(result.stdout[-4000:], result.stderr[-2000:])


if __name__ == '__main__':
    main()
