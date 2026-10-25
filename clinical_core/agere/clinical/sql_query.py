"""Governed read-only SQL over the semantic layer.

Planners write one SELECT against the typed, concept-normalised tables (see
semantic_layer.SCHEMA_DOC). Execution is read-only by construction: the
connection is opened mode=ro and an authorizer permits only reads and
functions (no writes, ATTACH, PRAGMA or schema changes); a progress handler
enforces a time limit and results are capped. An upstream population, when
given, is exposed to the query as the CTE `population(patient_id)`.
"""
import json, re, sqlite3, time

from . import semantic_layer
from .age import register as register_age
from .dose import register as register_dose


def register(db):
    from .clinical_inputs import register as register_clinical_inputs
    semantic_layer.ensure_diagnosis_view(db)  # a semantic.sqlite3 built before diagnosis_any gets it as a temp view
    semantic_layer.ensure_concept_view(db)  # ... and the condition rows of concept_patient at the same evidence definition
    semantic_layer.ensure_sdoh_latest_view(db)  # the latest matched purchased-SDOH delivery per patient
    return register_clinical_inputs(register_dose(register_age(db)))

VERSION = 'semantic-sql/1'
RETURNS = ('count', 'value', 'patient_set', 'table')
MAX_ROWS = 200000
TIME_LIMIT_SECONDS = 600
_ALLOWED = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
_RECURSIVE = getattr(sqlite3, 'SQLITE_RECURSIVE', None)
if _RECURSIVE is not None: _ALLOWED.add(_RECURSIVE)


_RELATIONS = {}


def decision_context_marker():
    from .decision_context import SEGMENT_MARKER
    return SEGMENT_MARKER


def _relations(target):
    """The table names of a semantic database (cached per file and modification time)."""
    import os
    try:
        key = (str(target), os.stat(target).st_mtime_ns)
    except OSError:
        return None
    if key not in _RELATIONS:
        try:
            with sqlite3.connect(f'file:{target}?mode=ro', uri=True) as db:
                names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")}
                if 'patient' in names and any(r[1] == 'data_segment' for r in db.execute('PRAGMA table_info(patient)')):
                    names.add(decision_context_marker())  # the patient relation can tell a duplicate chart
                _RELATIONS[key] = frozenset(names)
        except sqlite3.Error:
            return None
    return _RELATIONS[key]


def validate(p):
    if not isinstance(p, dict) or set(p) - {'query', 'returns'} or not isinstance(p.get('query'), str):
        raise ValueError('sql parameters: query (one SELECT), returns')
    q = p['query'].strip().rstrip(';').strip()
    code = re.sub(r'--[^\n]*', '', q).strip().rstrip(';').strip()  # comments (e.g. the stated definition) are not statements
    if not code: raise ValueError('A SQL query is required')  # no length cap (Mike, 2026-10-20): governed macros expand inline
    if not re.match(r'(?is)^(select|with)\b', code) or ';' in code:
        raise ValueError('Exactly one SELECT/WITH statement is allowed')
    if p.get('returns') not in RETURNS: raise ValueError(f'returns must be one of {RETURNS}')
    if re.search(r'\bage_latest\b', code, re.I):
        raise ValueError('Stale feed age is not an analytical field; use age_at(birth_date, as_of).')
    return {'query': q, 'returns': p['returns']}


_DEFINITION_VIEW = re.compile(r'(?i)\b(?:living_patient|condition_member|latest_lab|last_12_months|medicare_member)\b')


def uses_definition_views(query):
    """True when the query reads one of the analyst's decision-context definition views (analyst_definitions.VIEW_NAMES)."""
    return bool(_DEFINITION_VIEW.search(re.sub(r"'[^']*'", "''", re.sub(r'--[^\n]*', ' ', query or ''))))


def define_definition_views(db):
    """Create the definition views as TEMP views on this (read-only, not yet authorized) connection, exactly as the analyst's own connection has them
    (semantic_analyst.SemanticWarehouse.define): a graph built from the analyst's SQL then executes where the analyst ran it. Never written to the file."""
    from . import analyst_definitions
    names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name")]
    columns = {n: [r[1] for r in db.execute(f'PRAGMA table_info("{n}")')] for n in names}
    as_of = next((r[0] for r in db.execute("SELECT value FROM meta WHERE key = 'as_of'")), None) if 'meta' in columns else None
    views = analyst_definitions.build_views(columns, str(as_of or '2000-01-01')[:10])
    db.execute('PRAGMA query_only=OFF')
    try:
        for name in analyst_definitions.VIEW_NAMES:  # dependency order: living_patient before medicare_member
            if name in views: db.execute(f'CREATE TEMP VIEW {name} AS {views[name]}')
    finally:
        db.execute('PRAGMA query_only=ON')


def _authorizer(action, *_):
    return sqlite3.SQLITE_OK if action in _ALLOWED else sqlite3.SQLITE_DENY


def execute(path, params, patient_ids=None, target=None, time_limit=None):
    """target: an existing semantic.sqlite3 to read as-is (never rebuilt; used to replay against a read-only build)."""
    p = validate(params)
    from .answer_budget import tier_remaining  # a query inside a cascade tier never outlives the tier's time share (a runaway candidate SQL held a question for 19 minutes)
    limit = tier_remaining(time_limit or TIME_LIMIT_SECONDS)
    from . import decision_context  # the question's decision context: living patients and latest labs in the SQL that runs
    target = target or semantic_layer.ensure(path)
    run_sql, dc_receipt = decision_context.enforce_sql(p['query'], p['returns'], _relations(target) if decision_context.current() else None)
    shown = p
    p = {**p, 'query': run_sql}
    from . import duckdb_engine  # DuckDB serving (AGERE_SQL_ENGINE=duckdb) with SQLite fallback; shadow comparison
    engine_receipt = None
    uses_views = uses_definition_views(run_sql)  # the analyst's definition views exist only as temp views on a SQLite connection
    if duckdb_engine.engine() == 'duckdb' and not uses_views:
        started = time.perf_counter()
        columns, rows, engine_receipt = duckdb_engine.serve(target, p['query'], patient_ids, time_limit=limit,
                                                            max_rows=MAX_ROWS)
        if columns is not None: return _result(p, columns, rows, started, engine_receipt, shown, dc_receipt)
    from . import plan_optimizer  # repeated sub-selects run once (fused CTE); an unfusable form is re-run as planned
    query, fusion = plan_optimizer.fuse_sql(p['query'])
    unfused, args = p['query'], {}
    if re.search(r'\bpopulation\b', query, re.I):
        if patient_ids is None: raise ValueError('Query references population but no population input was given')
        cte = 'population(patient_id) AS (SELECT value FROM json_each(:population))'
        add = lambda q: (f'WITH {cte}, ' + q[4:].lstrip()) if re.match(r'(?is)^with\b', q) else f'WITH {cte} {q}'
        query, unfused = add(query), add(unfused)
        args['population'] = json.dumps(sorted(patient_ids))
    db = register(sqlite3.connect(f'file:{target}?mode=ro', uri=True))
    if uses_views: define_definition_views(db)
    deadline = time.perf_counter() + limit
    db.set_progress_handler(lambda: 1 if time.perf_counter() > deadline else 0, 10000)
    db.set_authorizer(_authorizer)
    started = time.perf_counter()
    try:
        try:
            cursor = db.execute(query, args)
            columns = [c[0] for c in cursor.description or []]
            rows = cursor.fetchmany(MAX_ROWS + 1)
        except sqlite3.DatabaseError as exc:
            if not fusion['fused'] or 'interrupt' in str(exc).lower(): raise
            fusion = {**fusion, 'fused': [], 'fallback': str(exc)[:120]}  # the rewrite did not stand alone: run the planned text
            cursor = db.execute(unfused, args)
            columns = [c[0] for c in cursor.description or []]
            rows = cursor.fetchmany(MAX_ROWS + 1)
    except sqlite3.DatabaseError as exc:  # includes authorizer denials and the time limit
        raise ValueError(f'SQL failed: {exc}') from exc
    finally:
        db.close()
    if duckdb_engine.shadow_on():
        engine_receipt = {**(engine_receipt or {'engine': 'sqlite'}), 'shadow': duckdb_engine.shadow(
            target, p['query'], patient_ids, columns, rows[:MAX_ROWS], round(time.perf_counter() - started, 4), max_rows=MAX_ROWS)}
    if fusion['fused'] or fusion.get('fallback'): engine_receipt = {**(engine_receipt or {'engine': 'sqlite'}), 'plan_optimizer': fusion}
    return _result(p, columns, rows, started, engine_receipt, shown, dc_receipt)


def _result(p, columns, rows, started, engine_receipt=None, shown=None, dc_receipt=None):
    truncated = len(rows) > MAX_ROWS
    rows = [list(r) for r in rows[:MAX_ROWS]]
    result = {'schema': VERSION, 'query': (shown or p)['query'], 'returns': p['returns'], 'columns': columns,
              'row_count': len(rows), 'truncated': truncated, 'elapsed_seconds': round(time.perf_counter() - started, 3)}
    if dc_receipt and dc_receipt.get('rewritten'): result['decision_context'] = {**dc_receipt, 'executed_query': p['query']}
    if engine_receipt: result['engine'] = engine_receipt
    if p['returns'] in ('count', 'value'):
        if not rows or not columns: raise ValueError('Scalar query returned no rows')
        if len(rows) != 1 or len(columns) != 1:
            raise ValueError('Scalar query must return exactly one row and one column; use table for multiple outputs')
        value = rows[0][0]
        if p['returns'] == 'count' and not isinstance(value, int): raise ValueError('Count query must return an integer')
        result['value'] = value
    elif p['returns'] == 'patient_set':
        result['patient_ids'] = sorted({r[0] for r in rows if r and r[0] is not None})
    else:
        result['rows'] = [dict(zip(columns, r)) for r in rows]
    return result
