"""Replay SQL against SQLite and a DuckDB mirror of the same database; report per-statement parity.

Step A of docs/agere/DUCKDB_PARQUET_MIGRATION_PLAN_2026-10-21.md. Read-only on both engines, no behaviour
change. A statement that runs on DuckDB is not evidence it answers correctly: DuckDB json_extract returns
JSON ('"paid"'). Bare `= 'paid'` raises a conversion error, but CAST(... AS TEXT) = 'paid', substr(...) and
LIKE see the quotes and match nothing, with no error. Every statement is therefore compared on its
normalised result rows, not on whether it ran.

Corpora:
  plans  every SELECT/WITH `query` in <stack>/question-plans.sqlite3, run on <stack>/semantic.sqlite3
  bench  bench/sql/**/*.sql (reference SQL), run on <build>/warehouse.sqlite3
  file   --sql-file: a JSON list of statements (or {"id","sql"} objects), run on --sqlite

Outcomes: identical | differs | duckdb_fails | sqlite_fails | both_fail | skipped (needs a population).
The DuckDB mirror is a CTAS copy of every table (sqlite scanner, no type changes), cached under
artifacts/duckdb-parity/mirrors/ and keyed on the source file's size, mtime and meta.source_digest.

    .venv/bin/python tools/duckdb_parity/replay.py                       # plans + bench, active build
    .venv/bin/python tools/duckdb_parity/replay.py --corpus plans --gate # exit 1 on any differs/duckdb_fails
"""
import argparse
import datetime
import decimal
import hashlib
import json
import re
import sqlite3
import statistics
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import duckdb  # noqa: E402

from clinical_core.agere.clinical import sql_query  # noqa: E402

SCHEMA = 'agere-duckdb-parity/1'
OUT = ROOT / 'artifacts/duckdb-parity'
FLOAT_DP = 4
OUTCOMES = ('identical', 'differs', 'duckdb_fails', 'sqlite_fails', 'both_fail', 'skipped')


# ---------------------------------------------------------------- corpora

def active_build():
    return Path(json.loads((ROOT / 'config/agere/active_warehouse.json').read_text())['build'])


def _statement(q):
    return isinstance(q, str) and re.match(r'(?is)\s*(select|with)\b', q)


def plan_statements(stack):
    """Distinct SELECT/WITH `query` strings stored anywhere in the plan cache, in first-seen order."""
    db = sqlite3.connect(f'file:{Path(stack) / "question-plans.sqlite3"}?mode=ro', uri=True)
    found = {}

    def walk(o, where):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == 'query' and _statement(v): found.setdefault(v.strip(), where)
                else: walk(v, where)
        elif isinstance(o, list):
            for x in o: walk(x, where)

    try:
        for table in ('plans', 'shared_requests', 'analysis_shapes'):
            for rowid, payload in db.execute(f'SELECT id, payload FROM {table}'):
                try: walk(json.loads(payload), f'{table}:{rowid}')
                except ValueError: continue
    finally:
        db.close()
    return [{'id': f'plan-{_digest(q)[:12]}', 'origin': where, 'sql': q} for q, where in found.items()]


def bench_statements(root=ROOT / 'bench/sql'):
    return [{'id': f'bench:{p.relative_to(root)}', 'origin': str(p.relative_to(ROOT)), 'sql': p.read_text()}
            for p in sorted(Path(root).rglob('*.sql'))]


def file_statements(path):
    items = json.loads(Path(path).read_text())
    out = []
    for i, it in enumerate(items):
        sql = it['sql'] if isinstance(it, dict) else it
        out.append({'id': it.get('id', f'file-{i}') if isinstance(it, dict) else f'file-{i}', 'origin': str(path), 'sql': sql})
    return out


# ---------------------------------------------------------------- DuckDB mirror

def _digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def source_key(sqlite_path):
    p = Path(sqlite_path)
    st = p.stat()
    meta = ''
    db = sqlite3.connect(f'file:{p}?mode=ro', uri=True)
    try:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone():
            row = db.execute("SELECT * FROM meta WHERE key='source_digest'").fetchone()
            meta = json.dumps(row) if row else ''
    except sqlite3.DatabaseError:
        pass
    finally:
        db.close()
    return _digest(f'{p.resolve()}|{st.st_size}|{st.st_mtime_ns}|{meta}')[:16]


def ensure_mirror(sqlite_path, mirror_dir=OUT / 'mirrors', rebuild=False):
    """DuckDB copy of every table in sqlite_path; rebuilt only when the source key changes."""
    p = Path(sqlite_path)
    key = source_key(p)
    target = Path(mirror_dir) / f'{p.parent.name}-{p.stem}-{key}.duckdb'
    if target.exists() and not rebuild: return target, None
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix('.duckdb.tmp')
    if tmp.exists(): tmp.unlink()
    started = time.perf_counter()
    db = duckdb.connect(str(tmp))
    try:
        db.execute("ATTACH '" + str(p).replace("'", "''") + "' AS src (TYPE sqlite, READ_ONLY)")
        tables = [r[0] for r in db.execute(
            "SELECT table_name FROM duckdb_tables() WHERE database_name = 'src' ORDER BY table_name").fetchall()]
        for t in tables:
            db.execute(f'CREATE TABLE "{t}" AS SELECT * FROM src."{t}"')
        db.execute('DETACH src')
    finally:
        db.close()
    tmp.rename(target)
    return target, {'tables': len(tables), 'seconds': round(time.perf_counter() - started, 1)}


# ---------------------------------------------------------------- execution

def _sqlite_conn(path):
    db = sql_query.register(sqlite3.connect(f'file:{path}?mode=ro', uri=True))
    db.set_authorizer(sql_query._authorizer)
    return db


def _duck_conn(path, threads=None, memory_limit=None):
    db = duckdb.connect(str(path), read_only=True)
    if threads: db.execute(f'SET threads = {int(threads)}')
    if memory_limit: db.execute(f"SET memory_limit = '{memory_limit}'")
    db.execute('SET enable_external_access = false')
    db.execute('SET lock_configuration = true')
    return db


def _clean(sql):
    return sql.strip().rstrip(';').strip()


def run_sqlite(db, sql, limit_s, max_rows):
    deadline = time.perf_counter() + limit_s
    db.set_progress_handler(lambda: 1 if time.perf_counter() > deadline else 0, 10000)
    started = time.perf_counter()
    try:
        cur = db.execute(_clean(sql))
        cols = [c[0] for c in cur.description or []]
        rows = cur.fetchmany(max_rows + 1)
        return {'ok': True, 'seconds': time.perf_counter() - started, 'columns': cols, 'rows': rows}
    except sqlite3.DatabaseError as exc:
        timed_out = time.perf_counter() > deadline
        return {'ok': False, 'seconds': time.perf_counter() - started,
                'error': 'time limit' if timed_out else str(exc).splitlines()[0][:300]}
    finally:
        db.set_progress_handler(None, 0)


def run_duckdb(db, sql, limit_s, max_rows):
    timer = threading.Timer(limit_s, db.interrupt)
    started = time.perf_counter()
    timer.start()
    try:
        cur = db.execute(_clean(sql))
        cols = [c[0] for c in cur.description or []]
        rows = cur.fetchmany(max_rows + 1)
        return {'ok': True, 'seconds': time.perf_counter() - started, 'columns': cols, 'rows': rows}
    except duckdb.Error as exc:
        timed_out = time.perf_counter() - started >= limit_s
        return {'ok': False, 'seconds': time.perf_counter() - started,
                'error': 'time limit' if timed_out else str(exc).splitlines()[0][:300]}
    finally:
        timer.cancel()


# ---------------------------------------------------------------- comparison

def _value(v):
    if v is None: return None
    if isinstance(v, bool): return int(v)
    if isinstance(v, (int, float, decimal.Decimal)):
        f = float(v)
        return int(f) if f.is_integer() else round(f, FLOAT_DP)
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)): return v.isoformat()
    if isinstance(v, bytes): return v.hex()
    return str(v)


def normalise(rows):
    """Order-insensitive multiset of rows; numbers compared by value (1 == 1.0), floats to FLOAT_DP places."""
    return sorted((tuple(_value(v) for v in r) for r in rows), key=repr)


def compare(a, b):
    """(identical?, detail) for two successful results."""
    if len(a['columns']) != len(b['columns']):
        return False, {'reason': 'column count', 'sqlite': len(a['columns']), 'duckdb': len(b['columns'])}
    if len(a['rows']) != len(b['rows']):
        return False, {'reason': 'row count', 'sqlite': len(a['rows']), 'duckdb': len(b['rows']),
                       'sqlite_sample': [list(map(_value, r)) for r in a['rows'][:3]],
                       'duckdb_sample': [list(map(_value, r)) for r in b['rows'][:3]]}
    na, nb = normalise(a['rows']), normalise(b['rows'])
    if na == nb: return True, None
    diffs = [(x, y) for x, y in zip(na, nb) if x != y]
    return False, {'reason': 'values', 'rows_differing': len(diffs),
                   'first': {'sqlite': list(diffs[0][0]), 'duckdb': list(diffs[0][1])}}


def error_class(msg):
    """Collapse an engine error to a stable class: quoted names and numbers removed."""
    m = re.sub(r'"[^"]*"|\'[^\']*\'', '…', msg or '')
    return re.sub(r'\d+', 'N', m)[:90]


def skip_reason(sql):
    """Statements whose inputs are bound at runtime and not stored with them cannot be replayed faithfully."""
    if re.search(r'\bpopulation\b', sql, re.I): return 'references population (bound at runtime)'
    code = re.sub(r"'(?:[^']|'')*'|\"[^\"]*\"", '', sql)
    if re.search(r'\?|(?<![:\w]):[A-Za-z_]\w*', code):  # discovery probes keep only query_parameters_digest
        return 'bound parameters not stored'
    return None


def replay(statements, sqlite_path, duck_path, *, limit_s=120, max_rows=sql_query.MAX_ROWS,
           threads=None, memory_limit=None, progress=None):
    sdb, ddb = _sqlite_conn(sqlite_path), _duck_conn(duck_path, threads, memory_limit)
    results = []
    try:
        for i, st in enumerate(statements):
            row = {'id': st['id'], 'origin': st.get('origin'), 'sql_sha256': _digest(_clean(st['sql']))}
            reason = skip_reason(st['sql'])
            if reason:
                row['outcome'], row['reason'] = 'skipped', reason
                results.append(row)
                continue
            a = run_sqlite(sdb, st['sql'], limit_s, max_rows)
            b = run_duckdb(ddb, st['sql'], limit_s, max_rows)
            row['sqlite_seconds'], row['duckdb_seconds'] = round(a['seconds'], 4), round(b['seconds'], 4)
            if a['ok'] and b['ok']:
                same, detail = compare(a, b)
                row['outcome'] = 'identical' if same else 'differs'
                if detail: row['detail'] = detail
                row['rows'] = len(a['rows'])
            elif a['ok']:
                row['outcome'], row['duckdb_error'] = 'duckdb_fails', b['error']
            elif b['ok']:
                row['outcome'], row['sqlite_error'] = 'sqlite_fails', a['error']
            else:
                row['outcome'], row['sqlite_error'], row['duckdb_error'] = 'both_fail', a['error'], b['error']
            results.append(row)
            if progress: progress(i + 1, len(statements), row)
    finally:
        sdb.close()
        ddb.close()
    return results


def summarise(results):
    counts = {k: sum(1 for r in results if r['outcome'] == k) for k in OUTCOMES}
    both = [r for r in results if r['outcome'] in ('identical', 'differs')]
    timing = {}
    if both:
        for engine in ('sqlite', 'duckdb'):
            xs = sorted(r[f'{engine}_seconds'] for r in both)
            timing[engine] = {'median': round(statistics.median(xs), 4), 'p90': round(xs[int(0.9 * (len(xs) - 1))], 4),
                              'total': round(sum(xs), 2)}
    errors = {}
    for r in results:
        if r['outcome'] == 'duckdb_fails':
            k = error_class(r['duckdb_error'])
            errors[k] = errors.get(k, 0) + 1
    return {'counts': counts, 'statements': len(results), 'timing_on_both_ran': timing,
            'duckdb_error_classes': dict(sorted(errors.items(), key=lambda kv: -kv[1])),
            'gate_failures': counts['differs'] + counts['duckdb_fails']}


def markdown(report):
    lines = [f"# DuckDB parity replay {report['run_at']}", '']
    for name, corpus in report['corpora'].items():
        s = corpus['summary']
        lines += [f"## {name}: {s['statements']} statements on `{corpus['sqlite']}`", '',
                  '| outcome | n |', '|---|---|'] + [f'| {k} | {v} |' for k, v in s['counts'].items()] + ['']
        if s['timing_on_both_ran']:
            t = s['timing_on_both_ran']
            lines += [f"Timing where both ran: SQLite median {t['sqlite']['median']} s, p90 {t['sqlite']['p90']} s, "
                      f"total {t['sqlite']['total']} s; DuckDB median {t['duckdb']['median']} s, p90 {t['duckdb']['p90']} s, "
                      f"total {t['duckdb']['total']} s.", '']
        if s['duckdb_error_classes']:
            lines += ['DuckDB failures (statements that ran on SQLite):', '']
            lines += [f'- {v} × `{k}`' for k, v in s['duckdb_error_classes'].items()] + ['']
        diffs = [r for r in corpus['results'] if r['outcome'] == 'differs']
        if diffs:
            lines += ['Silent differences (both ran, results differ):', '']
            for r in diffs:
                d = r['detail']
                if d['reason'] == 'values':
                    lines.append(f"- `{r['id']}` ({r['origin']}): {d['rows_differing']} row(s) differ; "
                                 f"first SQLite {d['first']['sqlite']} vs DuckDB {d['first']['duckdb']}")
                else:
                    lines.append(f"- `{r['id']}` ({r['origin']}): {d['reason']} SQLite {d['sqlite']} vs DuckDB {d['duckdb']}")
            lines.append('')
    return '\n'.join(lines)


# ---------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--build', type=Path, help='Agere build dir (default: config/agere/active_warehouse.json)')
    ap.add_argument('--corpus', action='append', choices=('plans', 'bench', 'file'),
                    help='repeatable; default plans + bench')
    ap.add_argument('--sql-file', type=Path, help='JSON list of statements for --corpus file')
    ap.add_argument('--sqlite', type=Path, help='SQLite target for --corpus file')
    ap.add_argument('--limit', type=int, default=0, help='replay only the first N statements per corpus')
    ap.add_argument('--time-limit', type=float, default=120, help='seconds per statement per engine')
    ap.add_argument('--threads', type=int)
    ap.add_argument('--memory-limit', default='8GB')
    ap.add_argument('--rebuild-mirror', action='store_true')
    ap.add_argument('--duckdb', type=Path, help='DuckDB file to compare against (default: the digest-checked semantic.duckdb '
                    'beside the SQLite target when present, else a cached mirror)')
    ap.add_argument('--out', type=Path, help='report directory (default artifacts/duckdb-parity/<UTC stamp>)')
    ap.add_argument('--gate', action='store_true', help='exit 1 if any statement differs or fails only on DuckDB')
    args = ap.parse_args(argv)

    build = args.build or active_build()
    corpora = args.corpus or ['plans', 'bench']
    plan = {}
    if 'plans' in corpora: plan['plans'] = (build / 'stack/semantic.sqlite3', plan_statements(build / 'stack'))
    if 'bench' in corpora: plan['bench'] = (build / 'warehouse.sqlite3', bench_statements())
    if 'file' in corpora:
        if not (args.sql_file and args.sqlite): ap.error('--corpus file needs --sql-file and --sqlite')
        plan['file'] = (args.sqlite, file_statements(args.sql_file))

    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    out = args.out or OUT / stamp
    out.mkdir(parents=True, exist_ok=True)
    report = {'schema': SCHEMA, 'run_at': stamp, 'build': str(build), 'duckdb': duckdb.__version__,
              'sqlite': sqlite3.sqlite_version, 'time_limit_s': args.time_limit, 'corpora': {}}
    for name, (sqlite_path, statements) in plan.items():
        if args.limit: statements = statements[:args.limit]
        from clinical_core.agere.clinical import duckdb_engine
        beside = None if args.rebuild_mirror else (args.duckdb or duckdb_engine.current(sqlite_path))  # step B semantic.duckdb
        mirror, built = (Path(beside), None) if beside else ensure_mirror(sqlite_path, rebuild=args.rebuild_mirror)
        if built: print(f'[{name}] mirror built: {mirror.name} ({built["tables"]} tables, {built["seconds"]} s)', flush=True)

        def progress(i, n, row, name=name):
            if row['outcome'] != 'identical' or i % 10 == 0 or i == n:
                print(f'[{name}] {i}/{n} {row["id"]}: {row["outcome"]}', flush=True)

        results = replay(statements, sqlite_path, mirror, limit_s=args.time_limit,
                         threads=args.threads, memory_limit=args.memory_limit, progress=progress)
        report['corpora'][name] = {'sqlite': str(sqlite_path), 'mirror': str(mirror), 'mirror_build': built,
                                   'summary': summarise(results), 'results': results}

    (out / 'report.json').write_text(json.dumps(report, indent=1, default=str))
    (out / 'REPORT.md').write_text(markdown(report))
    print(markdown(report))
    print(f'report: {out}')
    failures = sum(c['summary']['gate_failures'] for c in report['corpora'].values())
    return 1 if args.gate and failures else 0


if __name__ == '__main__':
    sys.exit(main())
