"""Governed read-only SQL over the semantic layer.

Planners write one SELECT against the typed, concept-normalised tables (see
semantic_layer.SCHEMA_DOC). Execution is read-only by construction: the
connection is opened mode=ro and an authorizer permits only reads and
functions (no writes, ATTACH, PRAGMA or schema changes); a progress handler
enforces a time limit and results are capped. An upstream population, when
given, is exposed to the query as the CTE `population(patient_id)`.
"""
import json, math, re, sqlite3, time

from . import semantic_layer
from .age import register as register_age
from .dose import register as register_dose


def register(db):
    from ..request_lineage import trace_sql
    trace_sql(db)
    from .clinical_inputs import register as register_clinical_inputs
    semantic_layer.ensure_diagnosis_view(db)  # a semantic.sqlite3 built before diagnosis_any gets it as a temp view
    semantic_layer.ensure_concept_view(db)  # ... and the condition rows of concept_patient at the same evidence definition
    semantic_layer.ensure_sdoh_latest_view(db)  # the latest matched purchased-SDOH delivery per patient
    return register_clinical_inputs(register_dose(register_age(db)))

VERSION = 'semantic-sql/2'
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


def date_arithmetic_issues(db, query):
    """Detect SQLite's silent numeric coercion of received ISO date text.

    Resolve ordinary columns and CTE aliases through the installed SQL parser.
    A data column is temporal only when every non-NULL received value is ISO
    date text; numeric epochs and explicitly numeric conversions remain valid.
    This checks types, not clinical meaning, and never rewrites the calculation.
    """
    if not any(op in query for op in ('-', '+', '*', '/', '%')):
        return []
    import sqlglot
    from sqlglot import exp
    from sqlglot.optimizer.scope import Scope, traverse_scope
    try:
        tree = sqlglot.parse_one(query, read='sqlite')
    except (sqlglot.errors.ParseError, sqlglot.errors.TokenError):
        # SQLite remains the syntax authority for extensions this parser cannot
        # represent. No type finding is inferred from an unsuccessful parse.
        return []
    profiles, schemas = {}, {}
    def quoted(name):
        return '"' + name.replace('"', '""') + '"'
    def columns(table):
        if table not in schemas:
            try:
                cur = db.execute('SELECT * FROM ' + quoted(table) + ' LIMIT 0')
                schemas[table] = {d[0].casefold(): d[0] for d in cur.description or []}
            except sqlite3.DatabaseError as exc:
                if 'interrupt' in str(exc).lower(): raise
                schemas[table] = {}
        return schemas[table]
    def temporal_column(table, name):
        actual = columns(table).get(name.casefold())
        if actual is None: return False
        key = (table, actual)
        if key not in profiles:
            col = quoted(actual)
            # Exact counterexample search, not sampled type inference: one
            # numeric/mixed value disproves the date-text type immediately.
            invalid = db.execute(
                f"SELECT 1 FROM {quoted(table)} WHERE {col} IS NOT NULL AND NOT (typeof({col})='text' AND "
                f"{col} GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*' "
                f"AND julianday({col}) IS NOT NULL) LIMIT 1").fetchone()
            profiles[key] = invalid is None and db.execute(
                f'SELECT 1 FROM {quoted(table)} WHERE {col} IS NOT NULL LIMIT 1').fetchone() is not None
        return profiles[key]
    def from_source(source, name, seen):
        if isinstance(source, exp.Table):
            return temporal_column(source.name, name)
        if isinstance(source, Scope):
            for index, item in enumerate(source.expression.selects):
                alias = source.outer_columns[index] if index < len(source.outer_columns) else item.alias_or_name
                if alias.casefold() == name.casefold():
                    return temporal(item.this if isinstance(item, exp.Alias) else item, source, seen)
                if item.is_star:
                    return temporal(exp.column(name, table=item.table if isinstance(item, exp.Column) else None), source, seen)
        return False
    def temporal(node, scope, seen=None):
        seen = set() if seen is None else seen
        if isinstance(node, exp.Paren): return temporal(node.this, scope, seen)
        if isinstance(node, exp.Literal):
            return node.is_string and bool(re.match(r'^\d{4}-\d{2}-\d{2}(?:$|[ T])', node.this))
        if isinstance(node, exp.Date) or (isinstance(node, exp.Anonymous) and node.name.casefold() in ('date', 'datetime')):
            return True
        if not isinstance(node, exp.Column): return False
        key = (id(scope), node.table.casefold(), node.name.casefold())
        if key in seen: return False
        seen = seen | {key}
        sources = [source for alias, source in scope.sources.items()
                   if not node.table or alias.casefold() == node.table.casefold()]
        # Unqualified columns need one received source; ambiguity is left to
        # SQLite rather than guessed from a similarly named field.
        if len(sources) != 1 and not node.table:
            sources = [s for s in sources if isinstance(s, exp.Table) and node.name.casefold() in columns(s.name)]
        return len(sources) == 1 and from_source(sources[0], node.name, seen)
    issues = []
    for scope in traverse_scope(tree):
        for node in scope.expression.find_all(exp.Sub, exp.Add, exp.Mul, exp.Div, exp.Mod):
            if node.find_ancestor(exp.Select) is not scope.expression: continue
            if temporal(node.this, scope) or temporal(node.expression, scope):
                issues.append('Raw arithmetic on ISO date text would use SQLite numeric coercion: ' + node.sql(dialect='sqlite') +
                              '. Use julianday for elapsed days or unixepoch for elapsed seconds, then convert units explicitly.')
    return list(dict.fromkeys(issues))


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
    from ..request_lineage import component
    from ...contracts import digest
    with component('governed_sql', query_digest=digest(params),
                   population_digest=digest(patient_ids), target=str(target or path)) as metrics:
        result = _execute(path, params, patient_ids=patient_ids, target=target, time_limit=time_limit)
        metrics.update(rows=result.get('row_count'), truncated=result.get('truncated'))
        return result


def _execute(path, params, patient_ids=None, target=None, time_limit=None):
    """target: an existing semantic.sqlite3 to read as-is (never rebuilt; used to replay against a read-only build)."""
    p = validate(params)
    from .answer_budget import tier_remaining  # a query inside a cascade tier never outlives the tier's time share (a runaway candidate SQL held a question for 19 minutes)
    limit = tier_remaining(time_limit or TIME_LIMIT_SECONDS)
    from . import decision_context  # the question's decision context: living patients and latest labs in the SQL that runs
    target = target or semantic_layer.ensure(path)
    state = decision_context.current()
    token = None
    # A compiled event-per-patient rate sums correlated event counts. Its denominator names patients, but the rate is
    # event grain (Mike 2026-10-25). Keep lab enforcement and an outer composite answer's patient scope intact.
    masked, _ = decision_context._mask(p['query'])
    if (state is not None and p['returns'] != 'patient_set' and not decision_context.LIVING_ASK.search(state.question)
            and not decision_context.PATIENT_COUNT.search(state.question)
            and re.search(r'(?i)\bsum\s*\(\s*\(\s*select\s+count\s*\(', masked)
            and re.search(r'(?i)\bfrom\s+"?(?:utilization_event|stay|fill|appointment)"?\b', masked)):
        import copy
        events = copy.copy(state)
        events.population = {'living_only': False, 'reason': 'event rate: every patient and their events count (Mike 2026-10-25)'}
        token = decision_context._SCOPE.set(events)
    try:
        run_sql, dc_receipt = decision_context.enforce_sql(p['query'], p['returns'], _relations(target) if state is not None else None)
    finally:
        if token is not None: decision_context._SCOPE.reset(token)
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
    if re.search(r'\bpopulation\b',p['query'],re.I) and patient_ids is None:
        raise ValueError('Query references population but no population input was given')
    db = register(sqlite3.connect(f'file:{target}?mode=ro', uri=True))
    if uses_views: define_definition_views(db)
    query, fusion = plan_optimizer.fuse_sql(p['query'], db)
    unfused, args = p['query'], {}
    if re.search(r'\bpopulation\b', query, re.I):
        if patient_ids is None: raise ValueError('Query references population but no population input was given')
        cte = 'population(patient_id) AS (SELECT value FROM json_each(:population))'
        add = lambda q: (f'WITH {cte}, ' + q[4:].lstrip()) if re.match(r'(?is)^with\b', q) else f'WITH {cte} {q}'
        query, unfused = add(query), add(unfused)
        args['population'] = json.dumps(sorted(patient_ids))
    deadline = time.perf_counter() + limit
    db.set_progress_handler(lambda: 1 if time.perf_counter() > deadline else 0, 10000)
    db.set_authorizer(_authorizer)
    started = time.perf_counter()
    try:
        issues = date_arithmetic_issues(db, unfused)
        if issues: raise ValueError('; '.join(issues))
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
    if any(isinstance(v,float) and not math.isfinite(v) for row in rows for v in row):
        raise ValueError('Nonfinite numeric result is not a verified answer; define compatible numeric and undefined-result handling')
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
