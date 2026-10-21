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
    return register_clinical_inputs(register_dose(register_age(db)))

VERSION = 'semantic-sql/1'
RETURNS = ('count', 'value', 'patient_set', 'table')
MAX_ROWS = 200000
TIME_LIMIT_SECONDS = 600
_ALLOWED = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
_RECURSIVE = getattr(sqlite3, 'SQLITE_RECURSIVE', None)
if _RECURSIVE is not None: _ALLOWED.add(_RECURSIVE)


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


def _authorizer(action, *_):
    return sqlite3.SQLITE_OK if action in _ALLOWED else sqlite3.SQLITE_DENY


def execute(path, params, patient_ids=None, target=None, time_limit=None):
    """target: an existing semantic.sqlite3 to read as-is (never rebuilt; used to replay against a read-only build)."""
    p = validate(params)
    target = target or semantic_layer.ensure(path)
    query, args = p['query'], {}
    if re.search(r'\bpopulation\b', query, re.I):
        if patient_ids is None: raise ValueError('Query references population but no population input was given')
        cte = 'population(patient_id) AS (SELECT value FROM json_each(:population))'
        query = (f'WITH {cte}, ' + query[4:].lstrip()) if re.match(r'(?is)^with\b', query) else f'WITH {cte} {query}'
        args['population'] = json.dumps(sorted(patient_ids))
    db = register(sqlite3.connect(f'file:{target}?mode=ro', uri=True))
    deadline = time.perf_counter() + (time_limit or TIME_LIMIT_SECONDS)
    db.set_progress_handler(lambda: 1 if time.perf_counter() > deadline else 0, 10000)
    db.set_authorizer(_authorizer)
    started = time.perf_counter()
    try:
        cursor = db.execute(query, args)
        columns = [c[0] for c in cursor.description or []]
        rows = cursor.fetchmany(MAX_ROWS + 1)
    except sqlite3.DatabaseError as exc:  # includes authorizer denials and the time limit
        raise ValueError(f'SQL failed: {exc}') from exc
    finally:
        db.close()
    truncated = len(rows) > MAX_ROWS
    rows = [list(r) for r in rows[:MAX_ROWS]]
    result = {'schema': VERSION, 'query': p['query'], 'returns': p['returns'], 'columns': columns,
              'row_count': len(rows), 'truncated': truncated, 'elapsed_seconds': round(time.perf_counter() - started, 3)}
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
