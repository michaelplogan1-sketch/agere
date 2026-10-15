"""Run reference, Agere or a frontier model with read-only SQL tools."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench.common import (DEFAULT_DB, DEFAULT_SEMANTIC_DB, ROOT, CanonicalWarehouse, SemanticWarehouse, Warehouse,
                          load_questions, reference_answer)
from bench.adapters import MODEL, agere, frontier_canonical, frontier_semantic, frontier_sql

DEFAULT_CANONICAL_DB = ROOT.parent / 'artifacts/canonical/v28-sample/versions.sqlite3'
from bench.score import by_confidence, confidence_of, score_answer
from bench.errata import ERRATA_PATH, compute_expected, load_errata, score_with_errata


def redate(question, as_of):
    """Move a question to a new snapshot: its stated as-of date and its reference cutoff change together."""
    old = question['as_of']
    return {**question, 'as_of': as_of, 'question': question['question'].replace(old, as_of)}


def suite_digest(questions):
    payload = [(q, (ROOT / q['reference_sql']).read_text()) for q in questions]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def errata_expected(question, entry, warehouse, cache_dir):
    """Run-time expected answers for a question's errata readings (canonical + alternatives with SQL), cached."""
    sql = {p['sql']: (ROOT / p['sql']).read_text() for p in [entry['canonical']] + entry.get('alternatives', []) if p.get('sql')}
    digest = hashlib.sha256(json.dumps([entry, sql], sort_keys=True).encode()).hexdigest()[:12]
    cache = cache_dir / f"{question['id']}.errata-{digest}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    computed = {}
    for key, part in [('canonical', entry['canonical'])] + [(a['id'], a) for a in entry.get('alternatives', [])]:
        answer = compute_expected(question, part, warehouse)
        if answer is not None:
            computed[key] = answer
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix('.tmp'); tmp.write_text(json.dumps(computed)); tmp.replace(cache)
    return computed


def semantic_release(path):
    """Release ID the semantic layer was built from (its build's stack/run.json), or None when not recorded."""
    try:
        return json.loads((Path(path).parent / 'run.json').read_text())['snapshot']['release']
    except (OSError, ValueError, KeyError, TypeError):
        return None


def run_one(question, args, identity, attempt=1):
    errata = getattr(args, 'errata_data', None)
    row = {'id': question['id'], 'system': args.system, 'question': question['question'], 'attempt': attempt,
           'category': question['category'], 'split': question['split'], 'as_of': question['as_of'],
           'started_at': datetime.now(timezone.utc).isoformat(), 'suite_sha256': identity,
           'concurrency': args.jobs, 'answer': None, 'score': None, 'error': None, 'reference_error': None,
           'input_tokens': None, 'output_tokens': None, 'estimated_cost_usd': None, 'latency_s': None}
    warehouse = None
    try:
        warehouse = Warehouse(args.database, question['as_of'], args.release, timeout_s=args.sql_timeout)
        row['release'] = warehouse.release
        row['source_manifest'] = {k: warehouse.manifest.get(k) for k in ('adapter', 'source_sha256', 'original_patient_id_digest', 'as_of')}
        try:
            # References are computed once per (suite, release, question) and cached, so parallel
            # runs never re-execute heavy reference SQL inside every worker.
            cache = ROOT / 'results' / 'reference-cache' / f"{identity[:16]}-{warehouse.release}-{question['as_of']}" / f"{question['id']}.json"
            reference_start = time.perf_counter()
            if cache.exists():
                saved = json.loads(cache.read_text())
                expected, evidence = saved['expected'], saved['evidence']
            else:
                expected, evidence = reference_answer(question, warehouse)
                cache.parent.mkdir(parents=True, exist_ok=True)
                tmp = cache.with_suffix('.tmp'); tmp.write_text(json.dumps({'expected': expected, 'evidence': evidence})); tmp.replace(cache)
            row.update(expected=expected, reference_evidence=evidence,
                       reference_latency_s=time.perf_counter() - reference_start)
            entry = errata['questions'].get(question['id']) if errata else None
            if entry is not None:
                row['errata_expected'] = errata_expected(question, entry, warehouse, cache.parent)
        except Exception as exc:
            row['reference_error'] = type(exc).__name__ + ': ' + str(exc)
            return row
        start = time.perf_counter()
        try:
            if args.system == 'reference':
                row.update(answer=expected, input_tokens=0, output_tokens=0, estimated_cost_usd=0.0,
                           new_paid_api_calls=0, cost_scope='no_model_calls', model=None)
            elif args.system == 'agere':
                row.update(agere(question, args.url))
            elif args.system == 'frontier_semantic':
                semantic = SemanticWarehouse(args.semantic_database, timeout_s=args.sql_timeout)
                try:
                    # The semantic layer must be built from the same release the references read.
                    row['semantic_release'] = semantic_release(args.semantic_database)
                    if row['semantic_release'] not in (None, warehouse.release):
                        raise ValueError(f"Semantic layer release {row['semantic_release']} != warehouse release {warehouse.release}")
                    row.update(frontier_semantic(question, semantic, model=args.model, max_tools=args.max_tools))
                finally:
                    semantic.close()
            elif args.system == 'frontier_canonical':
                canonical = CanonicalWarehouse(args.canonical_database, timeout_s=args.sql_timeout)
                try:
                    # The canonical model must be built from the same release the references read.
                    row['canonical_release'] = canonical.meta.get('release')
                    if row['canonical_release'] not in (None, warehouse.release):
                        raise ValueError(f"Canonical release {row['canonical_release']} != warehouse release {warehouse.release}")
                    row.update(frontier_canonical(question, canonical, model=args.model, max_tools=args.max_tools))
                finally:
                    canonical.close()
            else:
                row.update(frontier_sql(question, warehouse, model=args.model, max_tools=args.max_tools))
        finally:
            row['latency_s'] = row['reference_latency_s'] if args.system == 'reference' else time.perf_counter() - start
        row['score'] = score_answer(question, expected, row['answer'])
        if errata:
            row['errata_score'] = score_with_errata(question, expected, row['answer'],
                                                    errata['questions'].get(question['id']),
                                                    row.get('release'), row.get('errata_expected'))
    except Exception as exc:
        row['error'] = type(exc).__name__ + ': ' + str(exc)
        if row.get('expected') is not None:
            row['score'] = score_answer(question, row['expected'], None)
    finally:
        if warehouse is not None:
            warehouse.close()
    return row


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--system', choices=['reference', 'agere', 'frontier_sql', 'frontier_semantic', 'frontier_canonical'],
                        required=True)
    parser.add_argument('--database', type=Path, default=DEFAULT_DB)
    parser.add_argument('--semantic-database', type=Path, default=DEFAULT_SEMANTIC_DB,
                        help='frontier_semantic: the semantic layer (<build>/stack/semantic.sqlite3) of the same release')
    parser.add_argument('--canonical-database', type=Path, default=DEFAULT_CANONICAL_DB,
                        help='frontier_canonical: the canonical model database (SQLite) built from the same release')
    parser.add_argument('--release')
    parser.add_argument('--as-of', help='Re-date every question to this snapshot (YYYY-MM-DD), e.g. a newer warehouse cutoff; '
                                        'the date in the question text and the reference cutoff both change')
    parser.add_argument('--ids', nargs='+', help='Space- or comma-separated question IDs')
    parser.add_argument('--url', default='http://127.0.0.1:8885')
    parser.add_argument('--model', default=MODEL)
    parser.add_argument('--max-tools', type=int, help='SQL calls per question (default 20 frontier_sql, 8 frontier_semantic/frontier_canonical)')
    parser.add_argument('--sql-timeout', type=float, default=180)
    parser.add_argument('--jobs', type=int, default=1)
    parser.add_argument('--resume', type=Path, help='Append only unanswered IDs to this prior JSONL; never silently retry a paid attempt')
    parser.add_argument('--repeat', type=int, default=1,
                        help='Independent attempts per question (e.g. 3 for pass^3 reporting in score.py); default 1')
    parser.add_argument('--errata', nargs='?', const=ERRATA_PATH, type=Path,
                        help='Also compute errata readings and an errata_score per row (row score stays the default scorer)')
    args = parser.parse_args(argv)
    if args.max_tools is None:
        args.max_tools = 8 if args.system in ('frontier_semantic', 'frontier_canonical') else 20
    if not 1 <= args.jobs <= 4 or not 1 <= args.max_tools <= 20 or args.sql_timeout <= 0 or not 1 <= args.repeat <= 5:
        parser.error('jobs must be 1..4, max-tools 1..20, repeat 1..5 and SQL timeout positive')
    all_questions = load_questions(); identity = suite_digest(all_questions)
    args.errata_data = load_errata(args.errata, {q['id']: q for q in all_questions}) if args.errata else None
    if args.as_of:
        all_questions = [redate(q, args.as_of) for q in all_questions]
    questions = all_questions
    if args.ids:
        ids = {i for arg in args.ids for i in arg.split(',')}
        unknown = ids - {q['id'] for q in questions}
        if unknown:
            parser.error('Unknown IDs: ' + ','.join(sorted(unknown)))
        questions = [q for q in questions if q['id'] in ids]
    if args.resume:
        destination = args.resume.resolve()
        existing = [json.loads(line) for line in destination.read_text().splitlines() if line.strip()]
        if any(r['system'] != args.system or r['suite_sha256'] != identity for r in existing):
            parser.error('Resume file system/suite mismatch')
        if args.system in ('frontier_sql', 'frontier_semantic', 'frontier_canonical') and any(r.get('model') != args.model for r in existing):
            parser.error('Resume file model mismatch')
        warehouse = Warehouse(args.database, release=args.release)
        try:
            if any(r.get('release') != warehouse.release or
                   r.get('source_manifest', {}).get('source_sha256') != warehouse.manifest.get('source_sha256')
                   for r in existing):
                parser.error('Resume file source mismatch')
        finally:
            warehouse.close()
        done = {}
        for r in existing:
            done[r['id']] = done.get(r['id'], 0) + 1
        pending = [(q, n) for q in questions for n in range(done.get(q['id'], 0) + 1, args.repeat + 1)]
    else:
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        destination = ROOT / 'results' / args.system / (timestamp + '.jsonl')
        pending = [(q, n) for q in questions for n in range(1, args.repeat + 1)]
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = None
    if args.system in ('frontier_semantic', 'frontier_canonical'):
        snapshot = (SemanticWarehouse(args.semantic_database) if args.system == 'frontier_semantic'
                    else CanonicalWarehouse(args.canonical_database))
        try:
            off = sorted({q['as_of'] for q, _ in pending} - {snapshot.as_of})
        finally:
            snapshot.close()
        if off:
            print(f'Note: {args.system} snapshot is {snapshot.as_of}; questions dated {off} rely on available_date filtering '
                  f'(use --as-of {snapshot.as_of} to align references with the snapshot)', flush=True)
    print(f'Results: {destination}', flush=True)
    failed_references = 0
    finished = []
    with destination.open('a') as output, ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(run_one, question, args, identity, attempt) for question, attempt in pending]
        for future in as_completed(futures):
            row = future.result(); row['benchmark_commit'] = commit
            output.write(json.dumps(row, allow_nan=False) + '\n'); output.flush()
            failed_references += int(row['reference_error'] is not None)
            finished.append(row)
            status = row.get('reference_error') or row.get('error') or row['score']['reason']
            confidence = confidence_of(row.get('answer'))
            print(f'{row["id"]}: {status}{f" [confidence {confidence}]" if confidence else ""}; {row["latency_s"]} s; '
                  f'USD {row["estimated_cost_usd"]}', flush=True)
    for label, group in by_confidence(finished).items():
        if group['asserted']:
            print(f'confidence {label}: {group["value_matches"]}/{group["asserted"]} value matches, {group["correct"]} correct', flush=True)
    return 1 if failed_references else 0


if __name__ == '__main__':
    raise SystemExit(main())
