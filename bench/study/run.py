"""Run the pre-registered study (docs/agere/study/PROTOCOL.md).

    .venv/bin/python bench/study/run.py --arms A,B,C,D,E,F1,F2,F3,F4,F5,F6 --questions both --run-id S1 --repeat 2
    .venv/bin/python bench/study/run.py --arms A,D,E,F5 --questions locked --run-id smoke1          # one pass

Each arm-pass runs in its own worker process with its own environment flags, a fresh ledger (AGERE_LEDGER_PATH, which every
product caller reads) and its own copy-on-write workspace clone with empty answer
caches (see isolation.py). Pass 2 of arm A reuses pass 1's clone and ledger: warm by design. Everything else is cold.
After each arm-pass the answers are scored (score.py); at the end analyze.py and report.py run.

    --workers 1 (default) gives exact per-question cost from the ledger (rowid window) and uncontended latency; with more
    workers per-question cost is the product-reported cost and the arm total still comes from the ledger.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bench.study import common, isolation  # noqa: E402

PY = sys.executable


def _now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


# --- one arm-pass (runs inside the worker process) ---------------------------------------------------------------------------

def _agere_row(ws, q, pipeline):
    from clinical_core.agere.onboarding.workspace import _answer_record
    t0 = time.time()
    out = ws.ask(q['question'], answer_type=q.get('answer_type'), pipeline=pipeline)
    seconds = round(time.time() - t0, 2)
    ids = (out.get('answer') or {}).get('patient_ids') or [] if isinstance(out.get('answer'), dict) else []
    rec = _answer_record(q['id'], out, q.get('answer_type'), ws.customer_ids(ids) if ids else None)
    cert = out.get('certified') if isinstance(out.get('certified'), dict) else None
    return {**rec, 'seconds': seconds, 'pipeline': out.get('pipeline'), 'route': out.get('route') or (out.get('interpretation') or '')[:200],
            'interpretation': (out.get('interpretation') or '')[:400], 'new_paid_api_calls': out.get('new_paid_api_calls'),
            'certified': {k: cert.get(k) for k in ('tier', 'alpha', 'delta', 'bound', 'n_calibration', 'threshold', 'score')} if cert else None,
            'pipeline_fallback': out.get('pipeline_fallback')}


def run_arm_pass(spec, *, ws=None, send_factory=None, keys=True):
    """Answer spec['questions_file'] for one arm-pass and write its files. ws / send_factory are injected by tests."""
    out_dir = Path(spec['out_dir'])
    out_dir.mkdir(parents=True, exist_ok=True)
    arm = common.ARMS[spec['arm']]
    questions = common.read_jsonl(spec['questions_file'])
    if ws is None:
        from bench.study.validation import require_validated
        require_validated(questions)
    ledger_path = spec['ledger_path']
    t_start = time.time()
    if ws is None:
        if keys:
            from run_integrated_stack import keys as load_keys
            load_keys()
        from clinical_core.agere.onboarding.workspace import Workspace
        ws = Workspace(spec['workspace_slug'], root=spec['workspace_root'])
    prepare_s = 0.0
    if arm['kind'] == 'agere' and arm.get('pipeline') == 'full' and spec.get('prepare', True):
        from clinical_core.agere.onboarding import workspace_stack
        t0 = time.time()
        workspace_stack.prepare(ws, allow_copy=False)
        prepare_s = round(time.time() - t0, 2)
    as_of = ws.state.get('as_of')
    docs_manifest = None
    if arm['kind'] == 'baseline':
        from bench.study import baseline
        docs_text, docs_manifest = baseline.docs_bundle(ws.docs_dir, spec.get('docs_level') or baseline.DEFAULT_DOCS)
        if send_factory is None:
            from clinical_core.agere.onboarding.workspace import ledgered_send_factory
            send_factory = ledgered_send_factory(out_dir / 'receipts')

    exact = int(spec.get('workers') or 1) == 1

    def one(q):
        before = isolation.ledger_max_rowid(ledger_path) if exact else None
        t0 = time.time()
        try:
            if arm['kind'] == 'agere':
                row = _agere_row(ws, q, arm['pipeline'])
            else:
                with send_factory() as send:
                    row = baseline.answer(q, model=arm['model'], docs_text=docs_text, as_of=as_of, db_path=ws.semantic_path,
                                          send=send, customer_ids=ws.customer_ids)
        except Exception as exc:  # noqa: BLE001 - a failed question records its error; the batch goes on
            row = {'id': q['id'], 'answer_type': q.get('answer_type'), 'status': 'abstain',
                   'error': f'{type(exc).__name__}: {exc}'[:300], 'seconds': round(time.time() - t0, 2)}
        row.update(set=q['set'], domain=q.get('domain'))
        if exact:
            calls = isolation.priced(isolation.ledger_rows(ledger_path, before))
            costs = [c['cost_usd'] for c in calls if c['status'] == 'validated']
            row['ledger_calls'] = len(calls)
            row['ledger_cost_usd'] = None if any(c is None for c in costs) else round(sum(costs), 8)
            row['free'] = len(calls) == 0
        else:
            paid, cost = row.get('new_paid_api_calls'), row.get('cost_usd')
            row['free'] = None if paid is None and cost is None else (not paid and not cost)
        return row

    rows = []
    if questions:  # the first question alone (fills the provider prompt cache once), then the pool
        rows.append(one(questions[0]))
        with ThreadPoolExecutor(max_workers=int(spec.get('workers') or 1)) as pool:
            rows.extend(pool.map(one, questions[1:]))
    common.write_jsonl(out_dir / 'answers.jsonl', rows)
    common.write_jsonl(out_dir / 'breadth_submission.jsonl',
                       [{k: v for k, v in r.items() if k not in ('cost_usd', 'decision', 'ledger_cost_usd', 'ledger_calls', 'free',
                                                                  'set', 'domain', 'certified', 'attempts', 'sql')}
                        for r in rows if r.get('set') == 'breadth'])
    excerpt = isolation.priced(isolation.ledger_rows(ledger_path, int(spec.get('ledger_start_rowid') or 0)))
    common.write_jsonl(out_dir / 'ledger.jsonl', excerpt)
    timings = {'started_at': spec.get('started_at'), 'finished_at': _now(), 'prepare_seconds': prepare_s,
               'total_seconds': round(time.time() - t_start, 2), 'workers': int(spec.get('workers') or 1),
               'per_question_cost_basis': 'ledger rowid window' if exact else 'product-reported cost_usd',
               'per_question': {f"{r['set']}:{r['id']}": r.get('seconds') for r in rows}}
    (out_dir / 'timings.json').write_text(json.dumps(timings, indent=1))
    if docs_manifest:
        (out_dir / 'baseline_docs.json').write_text(json.dumps(docs_manifest, indent=1))
    return rows


# --- orchestration ------------------------------------------------------------------------------------------------------------

def subprocess_runner(spec, env):
    spec_path = Path(spec['out_dir']) / 'spec.json'
    spec_path.write_text(json.dumps(spec, indent=1))
    log = Path(spec['out_dir']) / 'worker.log'
    with open(log, 'w') as f:
        rc = subprocess.run([PY, str(Path(__file__).resolve()), 'worker', '--spec', str(spec_path)], env=env, cwd=str(common.ROOT),
                            stdout=f, stderr=subprocess.STDOUT).returncode
    if rc != 0:
        raise RuntimeError(f"worker for {spec['arm']} pass {spec['pass']} failed (exit {rc}); see {log}")


def orchestrate(arms, run_id, *, which='locked', limit=None, repeat=1, workers=1, docs_level=None, questions=None,
                runner=None, scorer=None, workspace_dir=None, keep_workspaces=False, analyze=True, study_root=None,
                clone=None):
    """Run every arm-pass, score each, then analyze and report. Test hooks: questions, runner(spec, env), scorer(run_dir, arm,
    n), clone(src, dst_root) and workspace_dir (a fake workspace folder)."""
    from bench.study import score as score_mod
    bad = [a for a in arms if a not in common.ARMS]
    if bad:
        raise SystemExit(f'unknown arms {bad}; choose from {list(common.ARM_ORDER)}')
    run_dir = Path(study_root or common.STUDY_ROOT) / run_id
    if (run_dir / 'run.json').exists():
        raise SystemExit(f'{run_dir} already holds a run; pick a new --run-id')
    workspace_slug = common.WORKSPACE
    if questions is None:
        questions = common.load_questions(which, limit)
        if which in ('locked', 'both'):
            workspace_slug = common.locked_questions()[1]
    validation = None
    if runner is None:
        from bench.study.validation import require_validated
        validation = require_validated(questions)
    run_dir.mkdir(parents=True, exist_ok=True)
    common.write_jsonl(run_dir / 'questions.jsonl', questions)
    src_ws = Path(workspace_dir) if workspace_dir else (common.ROOT / 'artifacts' / 'workspaces' / workspace_slug)
    ws_fingerprint = None
    if runner is None:  # a real run: never clone a workspace mid-build
        need_full = any(common.ARMS[a].get('pipeline') == 'full' for a in arms)
        ws_fingerprint = isolation.preflight(src_ws, need_full=need_full)
    runner = runner or subprocess_runner
    clone = clone or isolation.clone_workspace
    run = {'schema': 'agere-study-run/1', 'run_id': run_id, 'created_at': _now(), 'protocol': 'docs/agere/study/PROTOCOL.md',
           'protocol_sha256': common.sha256_file(common.ROOT / 'docs' / 'agere' / 'study' / 'PROTOCOL.md')
           if (common.ROOT / 'docs' / 'agere' / 'study' / 'PROTOCOL.md').is_file() else None,
           'git': common.git_state(), 'pricing_file': 'config/agere/pricing.json', 'pricing_sha256': common.sha256_file(common.PRICING),
           'thresholds_file': 'config/agere/cascade_thresholds.json', 'thresholds_sha256': common.sha256_file(common.THRESHOLDS),
           'questions': {'which': which, 'limit': limit, 'fingerprint': common.question_fingerprint(questions),
                         'file': 'questions.jsonl'},
           'workspace': {'slug': workspace_slug, 'source': str(src_ws), 'fingerprint': ws_fingerprint}, 'repeat': repeat, 'workers': workers,
           'docs_level': docs_level, 'reference_validation': validation,
           'set2_scorer': common.SET2_SCORER, 'arms': {}, 'passes': []}
    for arm in arms:
        a = common.ARMS[arm]
        run['arms'][arm] = {'label': a['label'], 'kind': a['kind'], 'model': a.get('model'), 'pipeline': a.get('pipeline'),
                            'flags_set': a['env'], 'warm_second_pass': arm == common.WARM_ARM and repeat > 1}
    (run_dir / 'run.json').write_text(json.dumps(run, indent=1))
    clones = {}
    for n in range(1, repeat + 1):
        for arm in arms:
            out_dir = common.arm_pass_dir(run_dir, arm, n)
            out_dir.mkdir(parents=True, exist_ok=True)
            ledger = out_dir / 'ledger.sqlite3'
            start_rowid = 0
            warm = arm == common.WARM_ARM and n > 1
            if warm:
                prev = common.arm_pass_dir(run_dir, arm, n - 1) / 'ledger.sqlite3'
                if prev.is_file():
                    shutil.copyfile(prev, ledger)
                    start_rowid = isolation.ledger_max_rowid(ledger)
                ws_root = clones[(arm, n - 1)]
            else:
                ws_root = run_dir / '_workspaces' / f'{arm}-p{n}'
                info = clone(src_ws, ws_root)
                (out_dir / 'workspace_clone.json').write_text(json.dumps(info, indent=1))
            clones[(arm, n)] = ws_root
            isolation.seed_ledger(ledger)
            env = common.worker_env(arm)
            env['AGERE_LEDGER_PATH'] = str(ledger.resolve())
            env['AGERE_LEARNING_LOOP_DIR'] = str((out_dir / 'learning-loop').resolve())
            env['PYTHONPATH'] = str(common.ROOT) + os.pathsep + env.get('PYTHONPATH', '')
            spec = {'run_dir': str(run_dir), 'arm': arm, 'pass': n, 'out_dir': str(out_dir), 'questions_file': str(run_dir / 'questions.jsonl'),
                    'ledger_path': str(ledger.resolve()), 'ledger_start_rowid': start_rowid, 'workspace_root': str(ws_root),
                    'workspace_slug': src_ws.name, 'workers': workers, 'docs_level': docs_level, 'warm': warm, 'started_at': _now()}
            (out_dir / 'env.json').write_text(json.dumps({'agere_flags': common.agere_flags(env), 'warm': warm,
                                                          'workspace_root': str(ws_root), 'ledger': str(ledger)}, indent=1))
            run['passes'].append({'arm': arm, 'pass': n, 'dir': str(out_dir.relative_to(run_dir)), 'warm': warm,
                                  'agere_flags': common.agere_flags(env), 'started_at': spec['started_at']})
            (run_dir / 'run.json').write_text(json.dumps(run, indent=1))
            print(f'[{_now()}] {arm} pass {n}{" (warm)" if warm else ""} ...', flush=True)
            try:
                runner(spec, env)
                (scorer or score_mod.score_arm_pass)(run_dir, arm, n)
                run['passes'][-1]['status'] = 'done'
            except Exception as exc:  # noqa: BLE001 - one failed arm-pass is recorded; the others still run
                run['passes'][-1]['status'] = f'failed: {exc}'[:400]
                print(f'  FAILED: {exc}', flush=True)
            run['passes'][-1]['finished_at'] = _now()
            (run_dir / 'run.json').write_text(json.dumps(run, indent=1))
            _summary(run_dir, arm, n)
            reused_later = arm == common.WARM_ARM and n < repeat
            if not keep_workspaces and not reused_later and str(ws_root).startswith(str(run_dir)):
                shutil.rmtree(ws_root, ignore_errors=True)  # clones are copy-on-write; this drops only what the pass wrote
    if not keep_workspaces:
        shutil.rmtree(run_dir / '_workspaces', ignore_errors=True)
    if analyze:
        from bench.study import analyze as analyze_mod, report as report_mod
        analyze_mod.analyze(run_dir)
        report_mod.render(run_dir)
    return run_dir


def _summary(run_dir, arm, n):
    p = common.arm_pass_dir(run_dir, arm, n) / 'score.json'
    if p.is_file():
        s = json.loads(p.read_text())['summary']
        print(f"  {arm} p{n}: {s['correct']}/{s['n']} correct, ${s['cost_usd']:.4f} (ledger ${s['ledger_cost_usd']:.4f}), "
              f"median {s['median_seconds']}s, free {s['free']}/{s['n']}", flush=True)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'worker':
        ap = argparse.ArgumentParser()
        ap.add_argument('cmd')
        ap.add_argument('--spec', type=Path, required=True)
        a = ap.parse_args()
        run_arm_pass(json.loads(a.spec.read_text()))
        return
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--arms', required=True, help='comma-separated: A,B,C,D,E,F1..F6 (F1..F6 expands)')
    ap.add_argument('--questions', choices=['locked', 'breadth', 'both'], default='locked')
    ap.add_argument('--run-id', required=True)
    ap.add_argument('--limit', type=int, help='first N questions of each set')
    ap.add_argument('--repeat', type=int, default=1, help='passes per arm (protocol: 2)')
    ap.add_argument('--workers', type=int, default=1)
    ap.add_argument('--docs-level', choices=['index', 'modeled', 'full'], help='documentation given to arms B-D (default full)')
    ap.add_argument('--keep-workspaces', action='store_true', help='keep the per-arm workspace clones for inspection')
    ap.add_argument('--no-analyze', action='store_true')
    a = ap.parse_args()
    arms = []
    for tok in a.arms.split(','):
        tok = tok.strip()
        if '..' in tok:
            lo, hi = tok.split('..')
            arms += [f'F{i}' for i in range(int(lo.lstrip('F')), int(hi.lstrip('F')) + 1)]
        elif tok:
            arms.append(tok)
    run_dir = orchestrate(arms, a.run_id, which=a.questions, limit=a.limit, repeat=a.repeat, workers=a.workers,
                          docs_level=a.docs_level, keep_workspaces=a.keep_workspaces, analyze=not a.no_analyze)
    print(f'run folder: {run_dir}')


if __name__ == '__main__':
    main()
