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
import re
import subprocess
import sys
import time

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench import manifest as run_manifest
from bench.adapters import MODEL, agere, frontier_semantic
from bench.common import ROOT, SemanticWarehouse

ECOSYSTEM = Path('/Users/mikel/synthetic-patient-ecosystem')
SAMPLE = ROOT.parent / 'artifacts/healthcare-warehouse-v2.8-sample-0.30'
STATUS = {'answered': 'answered', 'unsupported': 'unsupported'}  # partial / grounding_failed / no answer -> abstain
SCORER_FILES = [ECOSYSTEM / 'ecosystem/eval_scorer.py', ECOSYSTEM / 'ecosystem/eval_questions.py']
HASH_CACHE = ROOT / 'results/.hash-cache.json'


def prompt(q, mode, as_of):
    text = q['question'] if mode == 'plain' else f"{q['question']} Definition: {q['definition']}"
    return f'{text} Use data received as of {as_of}.'


def _number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _scalar_from_table(table):
    if isinstance(table, list) and len(table) == 1 and isinstance(table[0], dict):
        (row,), _ = table_rows(table)
        if len(row['values']) == 1:
            return row['value']
    return None


# Numeric columns with these names are part of the row's key (a practice id, a year), not its measure.
_KEY_NAME = re.compile(r'(?:^|_)(?:id|code|key|year|month|quarter|week|date|day|bucket|group|band|rank|label|name)s?$', re.I)
KEY_SEP = ' -> '  # the scorer's multi-column key form (questions-2026-10-17.1)


def table_rows(table):
    """Every row of a table answer with ALL its key columns kept, in column order (key = the labels joined by ' -> '),
    and every measure column kept; value = the last measure column. Same rule for every contestant. Returns (rows,
    issues) where issues lists duplicate keys, rows without a measure and rows with more than one measure column."""
    rows, issues, seen = [], {'duplicate_keys': [], 'rows_without_value': 0, 'multi_value_rows': 0}, set()
    for r in table or []:
        if not isinstance(r, dict) or not r:
            continue
        keys = {k: v for k, v in r.items() if not _number(v) or _KEY_NAME.search(str(k))}
        values = {k: v for k, v in r.items() if k not in keys}
        if not keys:  # an all-number row: its first column is the label
            first = next(iter(r))
            keys, values = {first: r[first]}, {k: v for k, v in r.items() if k != first}
        key = KEY_SEP.join('' if v is None else str(v) for v in keys.values())
        value = list(values.values())[-1] if values else None
        issues['rows_without_value'] += not values
        issues['multi_value_rows'] += len(values) > 1
        if key in seen:
            issues['duplicate_keys'].append(key)
        seen.add(key)
        rows.append({'key': key, 'value': value, 'keys': keys, 'values': values})
    return rows, issues


def contract(q, answer):
    """The scorer's contract. Extraction rules are identical for every contestant. An answer that states an empty
    result (patient_ids [] or table []) stays answered; only an answer with no result field at all is an abstention."""
    answer = answer if isinstance(answer, dict) else {}
    out = {'id': q['id'], 'answer_type': q['answer_type'], 'status': STATUS.get(answer.get('status'), 'abstain'),
           'interpretation': ' '.join(str(g) for g in (answer.get('grounding') or []))[:1500]}
    kind = q['answer_type']
    stated = False
    if kind in ('count', 'value'):
        v = answer.get(kind)
        if v is None:
            v = answer.get('value' if kind == 'count' else 'count')
        if v is None:
            v = _scalar_from_table(answer.get('table'))
        out[kind] = v
        stated = v is not None
    elif kind == 'patient_set':
        if isinstance(answer.get('patient_ids'), list):
            out['patient_ids'] = answer['patient_ids']
            stated = True
        elif isinstance(answer.get('table'), list):
            out['patient_ids'] = [r.get('patient_id') for r in answer['table'] if isinstance(r, dict) and r.get('patient_id')]
            stated = True
        else:
            out['patient_ids'] = []
    else:
        rows, issues = table_rows(answer.get('table'))
        out['table'] = rows
        stated = isinstance(answer.get('table'), list)
        if any(issues.values()):
            out['table_issues'] = issues
    if out['status'] == 'answered' and not stated:
        out['status'] = 'abstain'
    return out


_TUVA = {}
_TUVA_LOCK = __import__('threading').Lock()


def _tuva(args):
    # One shared read-only DuckDB connection; each query takes its own cursor.
    with _TUVA_LOCK:
        if 'w' not in _TUVA:
            from bench.tuva import TuvaWarehouse
            _TUVA['w'] = TuvaWarehouse(timeout_s=args.sql_timeout)
    return _TUVA['w']


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
        elif args.system == 'frontier_tuva':
            from bench.tuva import frontier_tuva
            row = frontier_tuva(item, _tuva(args), model=args.model, max_tools=8)
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
    together = (row.get('response') or {}).get('together') if isinstance(row.get('response'), dict) else None
    row.pop('response', None)
    extra = {}
    if together:
        extra = {'together': {k: together.get(k) for k in ('decision', 'winner', 'reason', 'analyst_state')},
                 'contract_fast': contract(q, together.get('fast_answer')),
                 'contract_analyst': contract(q, together.get('analyst_answer'))}
    return {**extra, 'id': q['id'], 'domain': q['domain'], 'system': args.system, 'model': args.model if args.system != 'agere' else None,
            'mode': args.mode, 'latency_s': time.perf_counter() - start,
            'estimated_cost_usd': row.get('estimated_cost_usd'), 'cost_scope': row.get('cost_scope'),
            'new_paid_api_calls': row.get('new_paid_api_calls'), 'error': row.get('error'),
            'answer': row.get('answer'), 'contract': contract(q, row.get('answer')), 'tool_calls': row.get('tool_calls')}


def manifest_for(args, questions_file, system=None):
    data = [SAMPLE] + [p for p in (args.semantic_database, args.canonical_database) if p]
    docs = list(args.docs or []) + ([ROOT.parent / 'config/agere'] if args.system == 'agere' else [])
    settings = {k: getattr(args, k) for k in ('release', 'system', 'model', 'mode', 'as_of', 'url', 'sql_timeout', 'jobs',
                                               'budget_usd') if hasattr(args, k)}
    settings['max_tools'] = 8
    if args.system == 'agere':
        settings['model'] = None  # the service chooses; each row records the model it reports
    cache = run_manifest.hash_cache(HASH_CACHE)
    m = run_manifest.build(questions_file=questions_file, scorer_files=SCORER_FILES, data=data, docs=docs,
                           model_settings=settings, cache_policy=args.cache_policy, system=system or args.system, cache=cache)
    run_manifest.save_hash_cache(HASH_CACHE, cache)
    return m


def score(release, path, per_question=False):
    cmd = ['python3', '-m', 'ecosystem.eval_scorer', '--release', f'releases/{release}', '--questions', str(path),
           '--sample-release', str(SAMPLE)] + (['--per-question'] if per_question else [])
    return subprocess.run(cmd, cwd=ECOSYSTEM, capture_output=True, text=True)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--release', default='healthcare-warehouse-v2.8')
    p.add_argument('--system', choices=['agere', 'frontier_semantic', 'frontier_canonical', 'frontier_tuva'], required=True)
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
    p.add_argument('--budget-usd', type=float, default=0, help='Stop starting new questions once this much has been spent')
    p.add_argument('--rescore', type=Path, help='Re-derive the contract from a saved run directory and re-score (no model calls)')
    p.add_argument('--cache-policy', choices=run_manifest.CACHE_POLICIES, default='prompt_cache_only',
                   help='declared in the run manifest; replayed answers are always reported apart from paid cost')
    p.add_argument('--docs', nargs='*', type=Path, help='documentation files/dirs the contestant reads (pinned in the manifest)')
    p.add_argument('--resume', type=Path, help='continue a run directory: answers already saved in partial.jsonl are kept, the rest are asked')
    args = p.parse_args(argv)
    if args.rescore:
        args.rescore = args.rescore.resolve()
        qs = {json.loads(l)['id']: json.loads(l) for l in open(ECOSYSTEM / 'releases' / args.release / 'questions/questions.jsonl')}
        run_manifest.write(args.rescore, manifest_for(args, ECOSYSTEM / 'releases' / args.release / 'questions/questions.jsonl',
                                                      system='rescore'), name='manifest_rescore.json')
        rows = [json.loads(l) for l in open(args.rescore / 'rows.jsonl')]
        with open(args.rescore / 'question_answers.jsonl', 'w') as f:
            for r in rows:
                r['contract'] = contract(qs[r['id']], r['answer'])
                f.write(json.dumps(r['contract'], default=str) + '\n')
        result = score(args.release, args.rescore / 'question_answers.jsonl')
        (args.rescore / 'score.txt').write_text(result.stdout + result.stderr)
        run_manifest.pin_answer_key(args.rescore, result.stdout, name='manifest_rescore.json')
        per = score(args.release, args.rescore / 'question_answers.jsonl', per_question=True)
        (args.rescore / 'per_question.txt').write_text(per.stdout + per.stderr)
        print(args.rescore)
        return
    questions = [json.loads(l) for l in open(ECOSYSTEM / 'releases' / args.release / 'questions/questions.jsonl')]
    if args.ids:
        ids = {i for a in args.ids for i in a.split(',')}
        questions = [q for q in questions if q['id'] in ids]
    if args.resume:
        out = args.resume.resolve()
    else:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        name = '-'.join(x for x in [args.system, args.model.replace('claude-', '') if args.system != 'agere' else '',
                                    args.mode, args.label, stamp] if x)
        out = ROOT / 'results/truth' / args.release / name
        out.mkdir(parents=True, exist_ok=True)
        run_manifest.write(out, manifest_for(args, ECOSYSTEM / 'releases' / args.release / 'questions/questions.jsonl'))
    # Every finished answer is appended to partial.jsonl as it lands, so a crash keeps the work; --resume skips those questions.
    # A row whose failure was the service being unreachable is asked again. HTTP 502 (no tier answered, Claude unavailable)
    # and the client timeout are the system's own outcomes and are kept.
    partial = out / 'partial.jsonl'
    transport = lambda r: bool(r.get('error')) and any(t in str(r['error']) for t in ('Connection refused', 'Connection reset', 'URLError', 'Remote end closed', 'HTTP 503'))
    done = {}
    if partial.exists():
        for line in partial.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                if not transport(r): done[r['id']] = r
    order = [q['id'] for q in questions]
    todo = [q for q in questions if q['id'] not in done]
    print(f'{len(done)} saved, {len(todo)} to ask', flush=True)
    spent = [0.0, False]  # known paid spend, whether any row's cost was unknown
    lock = __import__('threading').Lock()

    def guarded(q):
        with lock:
            # Fail closed: once a cost is unknown the spend cannot be bounded, so a budgeted run stops starting questions.
            if args.budget_usd and (spent[0] >= args.budget_usd or spent[1]):
                return {'id': q['id'], 'domain': q['domain'], 'system': args.system, 'mode': args.mode, 'latency_s': 0.0,
                        'estimated_cost_usd': None, 'error': 'skipped: budget reached' if not spent[1] else
                        'skipped: budget unverifiable (a cost was unknown)', 'answer': None, 'contract': contract(q, None)}
        r = run_one(q, args)
        with lock:
            with open(partial, 'a') as f:
                f.write(json.dumps(r, default=str) + '\n')
            kind = run_manifest.cost_class(r)
            if kind == 'paid':
                spent[0] += r['estimated_cost_usd']
            elif kind == 'unknown':
                spent[1] = True
        return r

    with ThreadPoolExecutor(args.jobs) as pool:
        fresh = {r['id']: r for r in pool.map(guarded, todo)}
    rows = [done.get(i) or fresh[i] for i in order]
    with open(out / 'rows.jsonl', 'w') as f:
        for r in rows:
            f.write(json.dumps(r, default=str) + '\n')
    with open(out / 'question_answers.jsonl', 'w') as f:
        for r in rows:
            f.write(json.dumps(r['contract'], default=str) + '\n')
    costs = run_manifest.cost_summary(rows)
    (out / 'cost.json').write_text(json.dumps(costs, indent=1))
    lat = sorted(r['latency_s'] for r in rows)
    result = score(args.release, out / 'question_answers.jsonl')
    (out / 'score.txt').write_text(result.stdout + result.stderr)
    run_manifest.pin_answer_key(out, result.stdout)
    per = score(args.release, out / 'question_answers.jsonl', per_question=True)
    (out / 'per_question.txt').write_text(per.stdout + per.stderr)
    for part in ('fast', 'analyst'):
        if any(f'contract_{part}' in r for r in rows):
            with open(out / f'question_answers_{part}.jsonl', 'w') as f:
                for r in rows:
                    f.write(json.dumps(r.get(f'contract_{part}') or contract({'id': r['id'], 'answer_type': r['contract']['answer_type']}, None), default=str) + '\n')
            res = score(args.release, out / f'question_answers_{part}.jsonl')
            (out / f'score_{part}.txt').write_text(res.stdout + res.stderr)
            res = score(args.release, out / f'question_answers_{part}.jsonl', per_question=True)
            (out / f'per_question_{part}.txt').write_text(res.stdout + res.stderr)
    print(out)
    print(f'n={len(rows)} {run_manifest.format_cost(costs)} latency median {lat[len(lat) // 2]:.1f}s '
          f'p90 {lat[int(len(lat) * .9)]:.1f}s errors={sum(1 for r in rows if r["error"])}')
    print(result.stdout[-4000:], result.stderr[-2000:])


if __name__ == '__main__':
    main()
