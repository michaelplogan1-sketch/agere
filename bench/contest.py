"""Contest harness for the redesigned benchmark (synthetic-patient-ecosystem docs/BENCHMARK_REDESIGN.md).

Four contestants answer the same frozen question set under ONE budget (same SQL-call cap, per-SQL timeout, wall-clock
limit and max output tokens per model call); there is no per-contestant budget field, by construction:
  agere              the Agere full stack, the live service at --url
  llm_raw            cheap model (default claude-haiku-5-5) over the raw received tables only (frontier_sql)
  llm_tuva           cheap model over the Tuva marts + semantic layer (frontier_tuva)
  llm_tuva_frontier  the same as llm_tuva with claude-opus-5-5

Before any contestant runs, the round is stamped: sha256 of the questions file, the sealed scorer modules, the
warehouse files (or their manifests) and the contestant config. A round directory is frozen by its first stamp; a later
run whose questions, scorer or warehouse hash differs is refused (no regeneration during a round). A question set whose
manifest or questions carry a verification field that is not "verified" is refused; while the generator has not yet
added the field the check is tolerant and says so.

Each contestant runs --repeat N attempts (default 3) in each --modes (plain, defined), saved as
<round>/<contestant>/<mode>/attempt-<k>/{rows.jsonl, question_answers.jsonl, score.json, per_question.json}. The scorer
is the sealed `python3 -m ecosystem.eval_scorer` in the ecosystem repo; this harness never reads private/ or answer keys.
Then: python bench/contest_report.py <round>.

    .venv-tuva/bin/python bench/contest.py --round results/contest/v3-r1 --contestant llm_tuva \
        --release healthcare-warehouse-v2.8 --repeat 3 --modes plain defined
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import subprocess
import sys
import threading
import time

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench import adapters
from bench import manifest as run_manifest
from bench.manifest import sha256_file, sha256_json, warehouse_stamp  # noqa: F401 (re-exported)
from bench.common import ROOT
from bench.truth_run import contract, prompt

log = logging.getLogger('bench.contest')
ECOSYSTEM = Path('/Users/mikel/synthetic-patient-ecosystem')
WORKSPACE = Path('/Users/mikel/clinical-insights-local')  # artifacts live in the main checkout, not in worktrees
RAW_DB = WORKSPACE / 'artifacts/bench-db/v28-sample-indexed.sqlite3'
TUVA_DB = WORKSPACE / 'artifacts/test-warehouse/tuva.duckdb'
NATIVE_DB = WORKSPACE / 'artifacts/test-warehouse/native-v212.duckdb'  # the native warehouse, once built
SAMPLE = WORKSPACE / 'artifacts/healthcare-warehouse-v2.8-sample-0.30'
TUVA_MANIFEST = WORKSPACE / 'test_warehouse/agere_connector/target/manifest.json'
SCORER_MODULES = ('ecosystem/eval_scorer.py', 'ecosystem/eval_questions.py')
CHEAP, FRONTIER = 'claude-haiku-5-5', 'claude-opus-5-5'
CONTESTANTS = {  # name -> (kind, default model)
    'agere': ('agere', None),
    'llm_raw': ('raw', CHEAP),
    'llm_tuva': ('tuva', CHEAP),
    'llm_tuva_frontier': ('tuva', FRONTIER),
}
MODES = ('plain', 'defined')
VERIFIED = {'verified', True}
VERIFICATION_FIELDS = ('verification_status', 'verification', 'verified')


@dataclass(frozen=True)
class Budget:
    """The one budget every contestant gets. max_tokens = max output tokens per model call."""
    max_sql_calls: int = 8
    sql_timeout_s: float = 180.0
    wall_clock_s: float = 900.0
    max_tokens: int = 8192

    def __post_init__(self):
        if not 1 <= self.max_sql_calls <= 20 or self.sql_timeout_s <= 0 or self.wall_clock_s <= 0 or self.max_tokens < 256:
            raise ValueError(f'Invalid budget {self}')


# --- hashing and stamping ----------------------------------------------------------------------------------------------

def _verification_value(obj):
    for field in VERIFICATION_FIELDS:
        if isinstance(obj, dict) and field in obj:
            value = obj[field]
            return field, (value.get('status') if isinstance(value, dict) else value)
    return None, None


def check_verification(questions, manifests):
    """Refuse an unverified question set. manifests: [(label, dict)] (question-set manifest, release manifest section).
    Tolerant while no verification field exists anywhere; returns the stamp entry and logs what it decided."""
    found = []
    for label, manifest in manifests:
        field, value = _verification_value(manifest)
        if field:
            found.append(label)
            if value not in VERIFIED:
                raise SystemExit(f'Refusing to run: {label} {field}={value!r}, not verified')
    per_question = [(q['id'], *_verification_value(q)) for q in questions]
    flagged = [(i, v) for i, f, v in per_question if f]
    if flagged:
        missing = [i for i, f, _ in per_question if not f]
        bad = [i for i, v in flagged if v not in VERIFIED]
        if missing or bad:
            raise SystemExit(f'Refusing to run: {len(bad)} scored questions not verified ({", ".join(bad[:10])}); '
                             f'{len(missing)} without a verification field ({", ".join(missing[:10])})')
        found.append(f'questions ({len(flagged)} verified)')
    if not found:
        log.warning('Question set has no verification field yet; proceeding (tolerant until the generator adds it)')
        return {'status': 'absent', 'note': 'no verification field in the question-set manifest or questions; tolerated'}
    log.info('Question set verified: %s', '; '.join(found))
    return {'status': 'verified', 'sources': found}


def question_set_manifests(release_dir, questions_file):
    out = []
    qm = Path(questions_file).with_name('manifest.json')
    if qm.is_file():
        out.append((str(qm), json.loads(qm.read_text())))
    rm = Path(release_dir) / 'manifest.json'
    if rm.is_file():
        manifest = json.loads(rm.read_text())
        for key in ('question_set', 'questions'):
            if isinstance(manifest.get(key), dict):
                out.append((f'{rm}:{key}', manifest[key]))
    return out


def contestant_config(name, args, budget):
    kind, default_model = CONTESTANTS[name]
    cfg = {'contestant': name, 'kind': kind, 'model': None if kind == 'agere' else (args.model or default_model),
           'budget': asdict(budget), 'as_of': args.as_of}
    if kind == 'agere':
        cfg['url'] = args.url
    elif kind == 'raw':
        cfg.update(raw_db=str(args.raw_db), raw_release=args.raw_release)
    else:
        cfg.update(tuva_db=str(args.tuva_db), tuva_raw_db=str(args.tuva_raw_db) if args.tuva_raw_db else None,
                   tuva_manifest=str(args.tuva_manifest))
    return cfg


def warehouse_paths(cfg):
    return [Path(cfg[k]) for k in ('raw_db', 'tuva_db', 'tuva_raw_db', 'tuva_manifest') if cfg.get(k)]


def build_stamp(args, cfg, questions, cache=None):
    """Everything that must not change during a round, plus this contestant's config."""
    frozen = {'release': args.release, 'questions_file': {'path': str(args.questions_file),
                                                          'sha256': sha256_file(args.questions_file, cache)},
              'scorer': {m: sha256_file(args.ecosystem / m, cache) for m in SCORER_MODULES},
              'sample_release': warehouse_stamp(args.sample_release, cache) if args.sample_release else None,
              'verification': check_verification(questions, question_set_manifests(args.release_dir, args.questions_file))}
    return {'frozen': frozen, 'frozen_sha256': sha256_json(frozen),
            'warehouses': [warehouse_stamp(p, cache) for p in warehouse_paths(cfg)],
            'contestant_config': cfg, 'contestant_config_sha256': sha256_json(cfg),
            'stamped_at': datetime.now(timezone.utc).isoformat(timespec='seconds')}


def attempt_manifest(args, cfg, mode, cache=None):
    """The run manifest (bench/manifest.py) for one attempt: data, docs, questions, scorer, model settings, cache policy."""
    docs = [WORKSPACE / 'config/agere'] if cfg['kind'] == 'agere' else [Path(cfg['tuva_manifest'])] if cfg.get('tuva_manifest') else []
    data = warehouse_paths(cfg) + ([args.sample_release] if args.sample_release else [])
    if cfg['kind'] == 'agere':
        active = WORKSPACE / 'config/agere/active_warehouse.json'
        docs.append(active)
    return run_manifest.build(questions_file=args.questions_file, scorer_files=[args.ecosystem / m for m in SCORER_MODULES],
                              data=data, docs=[d for d in docs if Path(d).exists()],
                              model_settings={**cfg, 'mode': mode, 'jobs': args.jobs},
                              cache_policy=args.cache_policy, system=cfg['contestant'], cache=cache)


def freeze_round(round_dir, stamp):
    """The first stamp freezes the round; a later run must carry the same frozen hashes, and each warehouse path must
    hash the same as the first time any contestant used it."""
    path = Path(round_dir) / 'round.json'
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'frozen': stamp['frozen'], 'frozen_sha256': stamp['frozen_sha256'],
                                    'warehouses': {w['path']: w for w in stamp['warehouses']}}, indent=1))
        return
    first = json.loads(path.read_text())
    if first['frozen_sha256'] != stamp['frozen_sha256']:
        changed = [k for k in stamp['frozen'] if stamp['frozen'][k] != first['frozen'].get(k)]
        raise SystemExit(f'Refusing to run: round {round_dir} was frozen with different {", ".join(changed)}')
    for w in stamp['warehouses']:
        seen = first['warehouses'].get(w['path'])
        if seen and seen['sha256'] != w['sha256']:
            raise SystemExit(f'Refusing to run: warehouse {w["path"]} changed since the round was frozen')
        first['warehouses'][w['path']] = w
    path.write_text(json.dumps(first, indent=1))


# --- contestants -------------------------------------------------------------------------------------------------------

class PromptRecorder:
    """Transport wrapper: records the sha256 of what fixes model behaviour (model, max_tokens, system, tools)."""

    def __init__(self, transport):
        self.transport, self.hashes = transport, set()
        self.accepts_timeout = transport is adapters.post_json  # the wall-clock remainder reaches the real HTTP call

    def __call__(self, url, body, headers=None, **kw):
        self.hashes.add(sha256_json({k: body.get(k) for k in ('model', 'max_tokens', 'system', 'tools')}))
        return self.transport(url, body, headers, **kw)


RAW_DUCKDB_NOTE = ('Read-only SQLite', 'Read-only DuckDB'), ('SQLite views', 'DuckDB tables'), \
    ('Use :as_of for the cutoff if needed.', 'The cutoff is the as_of date given with the question.')


class Contestant:
    def __init__(self, cfg, transport=None, warehouse_factory=None):
        self.cfg, self.budget = cfg, Budget(**cfg['budget'])
        self.transport = transport or adapters.post_json
        self.factory = warehouse_factory
        self._shared, self._lock = None, threading.Lock()

    def warehouse(self):
        if self.factory:
            return self.factory(self.cfg, self.budget)
        if self.cfg['kind'] == 'raw':
            if self.cfg['raw_db'].endswith('.duckdb'):
                from bench.tuva import TuvaWarehouse
                return TuvaWarehouse(self.cfg['raw_db'], raw=None, timeout_s=self.budget.sql_timeout_s)
            from bench.common import Warehouse
            return Warehouse(self.cfg['raw_db'], as_of=self.cfg['as_of'], release=self.cfg.get('raw_release'),
                             timeout_s=self.budget.sql_timeout_s)
        with self._lock:  # one read-only DuckDB connection; each query takes its own cursor
            if self._shared is None:
                from bench.tuva import TuvaWarehouse
                self._shared = TuvaWarehouse(self.cfg['tuva_db'], raw=self.cfg.get('tuva_raw_db'),
                                             timeout_s=self.budget.sql_timeout_s, manifest=self.cfg['tuva_manifest'])
            return self._shared

    def answer(self, item, recorder=None):
        b, deadline = self.budget, time.monotonic() + self.budget.wall_clock_s
        if self.cfg['kind'] == 'agere':
            return adapters.agere(item, self.cfg['url'], transport=self.transport, timeout=b.wall_clock_s)
        common = dict(model=self.cfg['model'], max_tools=b.max_sql_calls, max_tokens=b.max_tokens, deadline=deadline,
                      transport=recorder or PromptRecorder(self.transport))
        if self.cfg['kind'] == 'raw':
            w = self.warehouse()
            try:
                system = None
                if self.cfg['raw_db'].endswith('.duckdb'):
                    system = adapters.SYSTEM
                    for a, c in RAW_DUCKDB_NOTE:
                        system = system.replace(a, c)
                return adapters.frontier_sql(item, w, system=system, **common)
            finally:
                if not self.factory:
                    w.close()
        from bench.tuva import frontier_tuva
        return frontier_tuva(item, self.warehouse(), **common)

    def close(self):
        if self._shared is not None:
            self._shared.close()


def run_question(q, contestant, mode, attempt, as_of):
    text = prompt(q, mode, as_of)
    item = {'id': q['id'], 'question': text, 'as_of': as_of}
    budget = contestant.budget
    recorder = PromptRecorder(contestant.transport) if contestant.cfg['kind'] != 'agere' else None
    start = time.perf_counter()
    try:
        row = contestant.answer(item, recorder)
    except Exception as exc:
        row = {'answer': None, 'error': f'{type(exc).__name__}: {exc}'}
    latency = time.perf_counter() - start
    answer, error = row.get('answer'), row.get('error')
    if latency > budget.wall_clock_s:  # identical rule for every contestant: a late answer is not scored
        answer, error = None, f'wall_clock_exceeded: {latency:.1f}s > {budget.wall_clock_s}s'
    calls = row.get('tool_calls') or []
    cfg = contestant.cfg
    return {'id': q['id'], 'domain': q.get('domain'), 'recoverability': q.get('recoverability'),
            'answer_type': q.get('answer_type'), 'contestant': cfg['contestant'], 'mode': mode, 'attempt': attempt,
            'model': cfg['model'] if cfg['kind'] != 'agere' else row.get('model'),
            'budget': asdict(budget), 'contestant_config_sha256': sha256_json(cfg),
            'question_prompt_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'system_prompt_sha256': sorted(recorder.hashes) if recorder else None,
            'latency_s': round(latency, 3), 'estimated_cost_usd': row.get('estimated_cost_usd'),
            'cost_scope': row.get('cost_scope'), 'input_tokens': row.get('input_tokens'),
            'output_tokens': row.get('output_tokens'), 'new_paid_api_calls': row.get('new_paid_api_calls'),
            'sql_calls': len(calls) if cfg['kind'] != 'agere' else None, 'error': error, 'answer': answer,
            'contract': contract(q, answer), 'tool_calls': calls}


def run_scorer(args, answers_path, per_question):
    cmd = ['python3', '-m', 'ecosystem.eval_scorer', '--release', str(args.release_dir), '--questions', str(answers_path)]
    if args.sample_release:
        cmd += ['--sample-release', str(args.sample_release)]
    if per_question:
        cmd.append('--per-question')
    r = subprocess.run(cmd, cwd=args.ecosystem, capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def save_attempt(out, rows, args, scorer):
    out.mkdir(parents=True, exist_ok=True)
    with open(out / 'rows.jsonl', 'w') as f:
        for r in rows:
            f.write(json.dumps(r, default=str) + '\n')
    with open(out / 'question_answers.jsonl', 'w') as f:
        for r in rows:
            f.write(json.dumps(r['contract'], default=str) + '\n')
    for per, name in ((False, 'score'), (True, 'per_question')):
        code, stdout, stderr = scorer(args, out / 'question_answers.jsonl', per)
        try:
            result = json.loads(stdout)
        except ValueError:
            result = None
        if code != 0 or result is None:
            (out / f'{name}.err.txt').write_text(stdout[-4000:] + stderr[-4000:])
            log.error('Scorer failed for %s (%s); see %s.err.txt', out, name, name)
            continue
        if per:  # keep only ids and correctness: the report needs nothing else
            result = {'per_question': [{k: x[k] for k in ('id', 'correct', 'answered') if k in x}
                                       for x in result.get('per_question', [])]}
        (out / f'{name}.json').write_text(json.dumps(result, indent=1))


def load_questions(path, ids=None):
    qs = [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]
    if ids:
        wanted = {i for a in ids for i in a.split(',')}
        qs = [q for q in qs if q['id'] in wanted]
    return qs


def run(args, transport=None, scorer=run_scorer, warehouse_factory=None, hash_cache=None):
    budget = Budget(args.max_sql_calls, args.sql_timeout, args.wall_clock, args.max_tokens)
    questions = load_questions(args.questions_file, args.ids)
    if not questions:
        raise SystemExit('No questions selected')
    round_dir = Path(args.round)
    cache_path = round_dir / '.hash-cache.json'
    cache = hash_cache if hash_cache is not None else (json.loads(cache_path.read_text()) if cache_path.exists() else {})
    results = []
    for name in args.contestant:
        cfg = contestant_config(name, args, budget)
        stamp = build_stamp(args, cfg, questions, cache)  # before anything runs
        freeze_round(round_dir, stamp)
        if hash_cache is None:
            cache_path.write_text(json.dumps(cache))
        contestant = Contestant(cfg, transport=transport, warehouse_factory=warehouse_factory)
        base = round_dir / name
        base.mkdir(parents=True, exist_ok=True)
        try:
            for mode in args.modes:
                for attempt in range(1, args.repeat + 1):
                    out = base / mode / f'attempt-{attempt}'
                    if (out / 'rows.jsonl').exists() and not args.overwrite:
                        log.info('Skipping existing %s', out)
                        continue
                    (out).mkdir(parents=True, exist_ok=True)
                    (out / 'stamp.json').write_text(json.dumps(stamp, indent=1))
                    run_manifest.write(out, attempt_manifest(args, cfg, mode, cache))
                    with ThreadPoolExecutor(args.jobs) as pool:
                        rows = list(pool.map(lambda q: run_question(q, contestant, mode, attempt, args.as_of), questions))
                    save_attempt(out, rows, args, scorer)
                    results.append(out)
                    print(out, f'n={len(rows)} errors={sum(1 for r in rows if r["error"])}', flush=True)
        finally:
            contestant.close()
    return results


def parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--round', required=True, help='round directory; frozen by its first stamp')
    p.add_argument('--contestant', nargs='+', choices=list(CONTESTANTS), required=True)
    p.add_argument('--release', default='healthcare-warehouse-v2.8')
    p.add_argument('--questions-file', type=Path, help='default: <ecosystem>/releases/<release>/questions/questions.jsonl')
    p.add_argument('--ecosystem', type=Path, default=ECOSYSTEM)
    p.add_argument('--sample-release', type=Path, default=SAMPLE, help='sampled release dir passed to the scorer')
    p.add_argument('--modes', nargs='+', choices=MODES, default=list(MODES))
    p.add_argument('--repeat', type=int, default=3)
    p.add_argument('--model', help='override the contestant default model (applies to every LLM contestant listed)')
    p.add_argument('--as-of', default='2030-05-27')
    p.add_argument('--url', default='http://127.0.0.1:8885')
    p.add_argument('--raw-db', type=Path, default=RAW_DB, help='raw warehouse: SQLite (.sqlite3) or native DuckDB')
    p.add_argument('--raw-release', help='release id inside the raw SQLite warehouse (default: the only one)')
    p.add_argument('--tuva-db', type=Path, default=TUVA_DB, help=f'Tuva DuckDB (native warehouse later: {NATIVE_DB})')
    p.add_argument('--tuva-raw-db', type=Path, default=RAW_DB,
                   help='SQLite landing zone attached as raw; pass "" when the DuckDB already has a raw schema')
    p.add_argument('--tuva-manifest', type=Path, default=TUVA_MANIFEST, help='dbt manifest.json used for the documentation')
    budget = p.add_argument_group('budget (identical for every contestant)')
    budget.add_argument('--max-sql-calls', type=int, default=Budget.max_sql_calls)
    budget.add_argument('--sql-timeout', type=float, default=Budget.sql_timeout_s)
    budget.add_argument('--wall-clock', type=float, default=Budget.wall_clock_s, help='seconds per question')
    budget.add_argument('--max-tokens', type=int, default=Budget.max_tokens, help='max output tokens per model call')
    p.add_argument('--jobs', type=int, default=3)
    p.add_argument('--ids', nargs='+')
    p.add_argument('--overwrite', action='store_true', help='re-run attempts that already have rows')
    p.add_argument('--cache-policy', choices=run_manifest.CACHE_POLICIES, default='prompt_cache_only',
                   help='declared in the run manifest; replayed answers are always reported apart from paid cost')
    return p


def resolve(args):
    args.release_dir = args.ecosystem / 'releases' / args.release
    args.questions_file = args.questions_file or args.release_dir / 'questions/questions.jsonl'
    if args.tuva_raw_db is not None and str(args.tuva_raw_db) in ('', '.'):
        args.tuva_raw_db = None
    if args.repeat < 1:
        raise SystemExit('--repeat must be >= 1')
    return args


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    args = resolve(parser().parse_args(argv))
    run(args)


if __name__ == '__main__':
    main()
