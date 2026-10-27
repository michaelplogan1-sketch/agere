"""DuckDB answer engine beside SQLite: build, engine switch, dialect lint, shadow comparison.

Steps B-E of docs/agere/DUCKDB_PARQUET_MIGRATION_PLAN_2026-10-21.md. SQLite stays the serving default and the fallback
for every statement DuckDB cannot answer faithfully; nothing here ever blocks an answer.

  build(sqlite_path)              B: semantic.duckdb beside semantic.sqlite3 (sqlite scanner ATTACH read-only, CTAS every
                                  table, meta copied incl. source_digest, written to .tmp then swapped). Columns keep their
                                  SQLite types so today's SQLite-dialect SQL means the same thing on both engines; typed
                                  DATE/JSON columns wait for the compiler to emit DuckDB (step D proper). Never raises.
  current(sqlite_path)            the semantic.duckdb whose source_digest/version match the SQLite build, else None
  engine()                        C: 'sqlite' | 'duckdb' (env AGERE_SQL_ENGINE, else active_warehouse.json 'engine', else sqlite)
  lint(sql)                       D: reasons a statement would answer differently (or not at all) on DuckDB: bare json_extract
                                  (JSON vs scalar -> silent zero), SQLite-only date functions, json_each, unregistered UDFs
  run(duck_path, query, patient_ids, time_limit, max_rows) -> (columns, rows)  read-only guarded executor
  serve(sqlite_path, query, patient_ids, ...) -> (columns, rows, receipt) | (None, None, receipt)  C with fallback receipt
  shadow(...)                     E: AGERE_SQL_SHADOW=duckdb runs DuckDB beside the served SQLite result, compares normalised
                                  rows and logs to <stack>/duckdb-shadow.sqlite3; the served answer is never changed
Synthetic data only; no model calls.
"""
import datetime, decimal, hashlib, json, math, os, re, sqlite3, threading, time
from pathlib import Path

VERSION = 'agere-duckdb-engine/1'
ROOT = Path(__file__).resolve().parents[3]
ENGINES = ('sqlite', 'duckdb')
FLOAT_DP = 4
SHADOW_DB = 'duckdb-shadow.sqlite3'

# Python UDFs registered on the DuckDB connection (same implementations as sql_query.register on SQLite).
_UDFS = {'age_at': (('VARCHAR', 'VARCHAR'), 'BIGINT'), 'drug_key': (('VARCHAR',), 'VARCHAR'),
         'dose_mg': (('VARCHAR',), 'DOUBLE'), 'per_day': (('VARCHAR',), 'DOUBLE'), 'target_mg': (('VARCHAR',), 'DOUBLE'),
         'target_max_mg': (('VARCHAR',), 'DOUBLE'), 'statement_mg': (('VARCHAR', 'VARCHAR'), 'DOUBLE'),
         'statement_per_day': (('VARCHAR', 'VARCHAR'), 'DOUBLE'), 'statement_status': (('VARCHAR',), 'VARCHAR'),
         'statement_day': (('VARCHAR',), 'VARCHAR')}
_SQLITE_ONLY_UDFS = ('clinical_measurements',)
LINT = [
    ('bare_json_extract', re.compile(r'\bjson_extract\s*\(', re.I),
     'json_extract returns JSON on DuckDB ("paid"), a scalar on SQLite: comparisons silently match nothing'),
    ('julianday', re.compile(r'\bjulianday\s*\(', re.I), 'julianday is SQLite-only'),
    ('date_modifier', re.compile(r"\b(?:date|datetime|strftime)\s*\([^()]*,\s*'[+-]?\s*\d+\s+(?:day|month|year)s?'", re.I),
     "date(x, '-N months') modifiers are SQLite-only"),
    ('date_now', re.compile(r"\b(?:date|datetime)\s*\(\s*'now'", re.I), "date('now') is SQLite-only"),
    ('json_each', re.compile(r'\bjson_each\s*\(', re.I), 'json_each is SQLite-only'),
    ('sqlite_udf', re.compile(r'\b(?:' + '|'.join(_SQLITE_ONLY_UDFS) + r')\s*\(', re.I), 'SQLite-only table UDF'),
    ('group_concat_sep', re.compile(r'\bgroup_concat\s*\(\s*DISTINCT', re.I), 'group_concat(DISTINCT ...) ordering differs'),
]


def engine():
    e = os.environ.get('AGERE_SQL_ENGINE', '').strip().lower()
    if not e:
        try: e = str(json.loads((ROOT / 'config/agere/active_warehouse.json').read_text()).get('engine') or '').lower()
        except (OSError, ValueError): e = ''
    return e if e in ENGINES else 'sqlite'


def shadow_on():
    return os.environ.get('AGERE_SQL_SHADOW', '').strip().lower() == 'duckdb'


def lint(sql):
    code = re.sub(r'--[^\n]*', '', sql or '')
    issues=[{'rule': k, 'reason': why} for k, rx, why in LINT if rx.search(code)]
    try:
        from sqlglot import parse_one,exp
        tree=parse_one(sql,read='sqlite')
        def constant(node):
            if isinstance(node,exp.Paren):return constant(node.this)
            if isinstance(node,exp.Neg):
                value=constant(node.this);return -value if value is not None else None
            if isinstance(node,exp.Literal) and node.is_number:
                try:return float(node.this)
                except ValueError:pass
            return None
        def real(node):
            if isinstance(node,exp.Paren):return real(node.this)
            if isinstance(node,exp.Literal) and node.is_number:return any(c in str(node.this).lower() for c in '.e')
            if isinstance(node,exp.Cast):return str(node.args.get('to')).upper() in ('FLOAT','DOUBLE','REAL')
            if isinstance(node,(exp.Mul,exp.Add,exp.Sub,exp.Div)):return real(node.this) or real(node.expression)
            return False
        for division in tree.find_all(exp.Div) if tree is not None else []:
            denominator=division.expression;value=constant(denominator)
            protected=isinstance(denominator,exp.Nullif) and constant(denominator.expression)==0
            if not ((real(division.this) or real(denominator)) and ((value is not None and value!=0) or protected)):
                issues.append({'rule':'division_semantics','reason':'Integer promotion or zero-denominator compatibility is unproven; retain SQLite. Use explicit real arithmetic and a compatible nonzero/NULLIF denominator when intended.'})
                break
    except Exception:
        issues.append({'rule':'arithmetic_contract_unverified','reason':'Cannot verify numeric expression compatibility; retain SQLite'})
    return issues


def _meta(sqlite_path):
    db = sqlite3.connect(f'file:{sqlite_path}?mode=ro', uri=True)
    try: return dict(db.execute('SELECT key, value FROM meta').fetchall())
    except sqlite3.DatabaseError: return {}
    finally: db.close()


def target_for(sqlite_path):
    return Path(sqlite_path).with_suffix('.duckdb')


def build(sqlite_path, out=None):
    """-> {'path', 'tables', 'seconds', 'source_digest'} or {'error'}. Never raises (heal, don't block)."""
    started = time.perf_counter()
    src = Path(sqlite_path)
    target = Path(out) if out else target_for(src)
    tmp = target.with_name(target.name + '.tmp')
    try:
        import duckdb
        meta = _meta(src)
        tmp.unlink(missing_ok=True)
        db = duckdb.connect(str(tmp))
        try:
            db.execute('SET enable_progress_bar = false')
            db.execute("ATTACH '" + str(src).replace("'", "''") + "' AS src (TYPE sqlite, READ_ONLY)")
            tables = [r[0] for r in db.execute("SELECT table_name FROM duckdb_tables() WHERE database_name = 'src' "
                                               "ORDER BY table_name").fetchall()]
            for t in tables:
                db.execute(f'CREATE TABLE "{t}" AS SELECT * FROM src."{t}"')
            db.execute('DETACH src')
            db.execute('CREATE TABLE IF NOT EXISTS meta(key VARCHAR, value VARCHAR)')
            db.execute("DELETE FROM meta WHERE key IN ('duckdb_engine', 'duckdb_source')")
            db.execute('INSERT INTO meta VALUES (?, ?), (?, ?)', ['duckdb_engine', VERSION, 'duckdb_source', _source_key(src, meta)])
        finally:
            db.close()
        tmp.replace(target)
        return {'path': str(target), 'tables': len(tables), 'seconds': round(time.perf_counter() - started, 1),
                'source_digest': meta.get('source_digest')}
    except Exception as exc:  # noqa: BLE001 - the SQLite build stands; DuckDB is optional
        try: tmp.unlink(missing_ok=True)
        except OSError: pass
        return {'error': f'{type(exc).__name__}: {exc}'[:300], 'seconds': round(time.perf_counter() - started, 1)}


def _source_key(src, meta):
    return hashlib.sha256(json.dumps([meta.get('version'), meta.get('source_digest'), Path(src).stat().st_size],
                                     sort_keys=True).encode()).hexdigest()[:24]


def current(sqlite_path):
    """The digest-checked semantic.duckdb for this SQLite build, or None (stale, missing or unreadable)."""
    target = target_for(sqlite_path)
    if not target.exists(): return None
    try:
        import duckdb
        db = duckdb.connect(str(target), read_only=True)
        try: meta = dict(db.execute('SELECT key, value FROM meta').fetchall())
        finally: db.close()
        return target if meta.get('duckdb_source') == _source_key(sqlite_path, _meta(sqlite_path)) else None
    except Exception:  # noqa: BLE001
        return None


def _udf(fn, arity):
    def safe(args):
        try: return fn(*args)
        except Exception:  # noqa: BLE001 - a UDF error on one row is NULL, as a failed parse is on SQLite paths
            return None
    if arity == 1: return lambda a: safe((a,))
    return lambda a, b: safe((a, b))  # DuckDB reads the arity from the signature


def _register(db):
    from .age import age_at
    from . import dose
    for name, (params, ret) in _UDFS.items():
        fn = age_at if name == 'age_at' else getattr(dose, name)
        db.create_function(name, _udf(fn, len(params)), list(params), ret, null_handling='special', side_effects=False)
    return db


def run(duck_path, query, patient_ids=None, *, time_limit=600, max_rows=200000):
    """Guarded read-only execution (bench/tuva.py pattern) -> (columns, rows). Raises ValueError on any refusal/failure."""
    import duckdb
    db = duckdb.connect(str(duck_path), read_only=True)
    try:
        db.execute('SET enable_external_access = false')
        _register(db)
        db.execute('SET lock_configuration = true')
        try: statements = duckdb.extract_statements(query)
        except duckdb.Error as exc: raise ValueError(f'DuckDB parse failed: {exc}') from exc
        if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
            raise ValueError('Exactly one SELECT statement is allowed')
        args = []
        if re.search(r'\bpopulation\b', query, re.I):
            if patient_ids is None: raise ValueError('Query references population but no population input was given')
            cte = 'population(patient_id) AS (SELECT unnest(?::VARCHAR[]))'
            query = (f'WITH {cte}, ' + query.lstrip()[4:].lstrip()) if re.match(r'(?is)^\s*with\b', query) else f'WITH {cte} {query}'
            args = [sorted(map(str, patient_ids))]
        timer = threading.Timer(time_limit, db.interrupt)
        timer.start()
        try:
            cur = db.execute(query, args)
            columns = [c[0] for c in cur.description or []]
            rows = cur.fetchmany(max_rows + 1)
        except duckdb.Error as exc:
            raise ValueError(f'DuckDB failed: {str(exc).splitlines()[0][:300]}') from exc
        finally:
            timer.cancel()
        return columns, [tuple(_plain(v) for v in r) for r in rows]
    finally:
        db.close()


def _plain(v):
    """DuckDB values in the Python types SQLite would return (Decimal -> float, dates -> ISO text)."""
    if isinstance(v, decimal.Decimal):v=float(v)
    if isinstance(v,float) and not math.isfinite(v):raise ValueError('Nonfinite DuckDB numeric result; SQLite compatibility/undefined-result handling is required')
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)): return v.isoformat()
    return v


def serve(sqlite_path, query, patient_ids=None, *, time_limit=600, max_rows=200000):
    """C: DuckDB serving with SQLite fallback -> (columns, rows, receipt) or (None, None, receipt naming the fallback)."""
    receipt = {'engine': 'duckdb', 'version': VERSION}
    found = lint(query)
    duck = current(sqlite_path)
    if found or duck is None:
        return None, None, {**receipt, 'engine': 'sqlite', 'fallback': 'semantic.duckdb missing or stale' if duck is None else
                            'dialect lint: ' + '; '.join(f['rule'] for f in found)}
    try:
        columns, rows = run(duck, query, patient_ids, time_limit=time_limit, max_rows=max_rows)
        return columns, rows, receipt
    except ValueError as exc:
        return None, None, {**receipt, 'engine': 'sqlite', 'fallback': str(exc)[:300]}


# ---------------------------------------------------------------- shadow (step E)

def _value(v):
    if v is None: return None
    if isinstance(v, bool): return int(v)
    if isinstance(v, (int, float, decimal.Decimal)):
        f = float(v); return int(f) if f.is_integer() else round(f, FLOAT_DP)
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)): return v.isoformat()
    if isinstance(v, bytes): return v.hex()
    return str(v)


def normalise(rows):
    """Order-insensitive multiset of rows, numbers by value, floats to FLOAT_DP places (same rule as the parity harness)."""
    return sorted((tuple(_value(v) for v in r) for r in rows), key=repr)


def compare(a_cols, a_rows, b_cols, b_rows):
    if len(a_cols) != len(b_cols): return 'differs', {'reason': 'column count', 'sqlite': len(a_cols), 'duckdb': len(b_cols)}
    if len(a_rows) != len(b_rows): return 'differs', {'reason': 'row count', 'sqlite': len(a_rows), 'duckdb': len(b_rows)}
    na, nb = normalise(a_rows), normalise(b_rows)
    if na == nb: return 'identical', None
    diffs = [(x, y) for x, y in zip(na, nb) if x != y]
    return 'differs', {'reason': 'values', 'rows_differing': len(diffs), 'first': {'sqlite': list(diffs[0][0]), 'duckdb': list(diffs[0][1])}}


def shadow(sqlite_path, query, patient_ids, columns, rows, sqlite_seconds, *, time_limit=120, max_rows=200000, log_dir=None):
    """E: run the statement on DuckDB too and log the comparison. -> the logged row (never raises, never changes the answer)."""
    started = time.perf_counter()
    row = {'at': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
           'sql_sha256': hashlib.sha256(query.strip().encode()).hexdigest(), 'sqlite_seconds': sqlite_seconds,
           'sqlite_rows': len(rows), 'lint': [f['rule'] for f in lint(query)]}
    try:
        duck = current(sqlite_path)
        if duck is None:
            row.update(outcome='no_duckdb', detail='semantic.duckdb missing or stale')
        else:
            try:
                d_cols, d_rows = run(duck, query, patient_ids, time_limit=time_limit, max_rows=max_rows)
                row['duckdb_seconds'] = round(time.perf_counter() - started, 4)
                outcome, detail = compare(columns, [tuple(r) for r in rows], d_cols, d_rows)
                row.update(outcome=outcome, detail=detail)
            except ValueError as exc:
                row.update(outcome='duckdb_fails', detail=str(exc)[:300])
        _log(Path(log_dir) if log_dir else Path(sqlite_path).parent, row, query)
    except Exception as exc:  # noqa: BLE001 - shadow is observation only
        row.update(outcome='shadow_error', detail=f'{type(exc).__name__}: {exc}'[:300])
    return row


def _log(directory, row, query):
    db = sqlite3.connect(directory / SHADOW_DB, timeout=10)
    try:
        db.execute('CREATE TABLE IF NOT EXISTS shadow(at TEXT, sql_sha256 TEXT, outcome TEXT, sqlite_seconds REAL, duckdb_seconds REAL, '
                   'sqlite_rows INTEGER, lint TEXT, detail TEXT, query TEXT)')
        db.execute('INSERT INTO shadow VALUES (?,?,?,?,?,?,?,?,?)',
                   (row['at'], row['sql_sha256'], row.get('outcome'), row.get('sqlite_seconds'), row.get('duckdb_seconds'),
                    row.get('sqlite_rows'), json.dumps(row.get('lint')), json.dumps(row.get('detail'), default=str), query))
        db.commit()
    finally:
        db.close()


def shadow_summary(directory):
    """Counts per outcome in <directory>/duckdb-shadow.sqlite3 (the flip criterion: zero unexplained 'differs')."""
    p = Path(directory) / SHADOW_DB
    if not p.exists(): return {}
    db = sqlite3.connect(f'file:{p}?mode=ro', uri=True)
    try: return dict(db.execute('SELECT outcome, count(*) FROM shadow GROUP BY 1').fetchall())
    finally: db.close()
