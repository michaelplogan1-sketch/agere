"""Question loading, read-only SQL execution and reference answers."""
from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
def _active_db():
    """The warehouse of the ACTIVE Agere build (config/agere/active_warehouse.json), so references follow activation."""
    config = ROOT.parent / 'config/agere/active_warehouse.json'
    try:
        db = Path(json.loads(config.read_text())['build']) / 'warehouse.sqlite3'
        if db.exists(): return db
    except (OSError, ValueError, KeyError):
        pass
    return Path('/Users/mikel/clinical-insights-local/artifacts/agere-healthcare-v123/warehouse.sqlite3')


DEFAULT_DB = _active_db()
ANSWER_TYPES = {'count', 'patient_set', 'value', 'table', 'unsupported'}


def load_questions(directory=ROOT / 'questions'):
    cases = []
    for path in sorted(Path(directory).glob('*.json')):
        data = json.loads(path.read_text())
        cases.extend(data if isinstance(data, list) else [data])
    seen = set()
    for q in cases:
        for key in ('id', 'question', 'category', 'as_of', 'interpretation', 'answer_type', 'reference_sql', 'split'):
            if not q.get(key):
                raise ValueError(f'Missing {key} in question {q.get("id")}')
        if q['id'] in seen or q['answer_type'] not in ANSWER_TYPES:
            raise ValueError(f'Invalid/duplicate question: {q["id"]}')
        seen.add(q['id'])
        sql_path = (ROOT / q['reference_sql']).resolve()
        if not sql_path.is_relative_to(ROOT / 'sql') or not sql_path.is_file():
            raise ValueError(f'Invalid reference SQL path: {q["id"]}')
        if q['answer_type'] == 'unsupported' and not q.get('expected_behavior', {}).get('required_missing'):
            raise ValueError('Unsupported cases require an explicit missing-evidence rubric')
    return sorted(cases, key=lambda q: q['id'])


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def empty_answer(kind, status='answered'):
    return dict(status=status, answer_type=kind, count=None, patient_ids=None,
                value=None, table=None, unknown_count=0, missing=[], grounding=[])


def validate_sql_scope(sql):
    """Block explicit base-schema access, including a CTE spoofing a view name.

    The SQLite authorizer reports the innermost CTE name as the read origin.
    A malicious MATERIALIZED CTE named 'records' could impersonate our view.
    Only unqualified public views are needed, so reject main.<anything> outside
    comments, recognizing every SQLite identifier quoting form.
    """
    tokens = re.findall(r"--[^\n]*|/\*[\s\S]*?\*/|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|`(?:``|[^`])*`|\[[^\]]*\]|[A-Za-z_][A-Za-z_0-9]*|[^\s]", sql)
    tokens = [t for t in tokens if not t.startswith(('--', '/*'))]
    for left, right in zip(tokens, tokens[1:]):
        name = left[1:-1] if left[:1] in {'"', "'", '`', '['} else left
        if name.casefold() == 'main' and right == '.':
            raise ValueError('Direct base-schema access is unavailable; query the scoped public views')


class Warehouse:
    """Raw received records, scoped by release, roster and arrival cutoff.

    TEMP views shadow base tables; the authorizer denies direct base-table access,
    mutations, ATTACH, PRAGMA and extension/file functions. The database is opened
    mode=ro and query_only. The product's cached results are never exposed.
    """

    def __init__(self, path=DEFAULT_DB, as_of='2029-08-15', release=None, timeout_s=30):
        self.path = Path(path).resolve()
        self.db = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True)
        self.db.row_factory = sqlite3.Row
        self.timeout_s = timeout_s
        releases = self.db.execute('SELECT id,manifest FROM releases').fetchall()
        candidates = [r for r in releases if release is None or r['id'] == release]
        if len(candidates) != 1:
            self.db.close()
            raise ValueError('Select one unambiguous warehouse release with --release')
        self.release = candidates[0]['id']
        self.manifest = json.loads(candidates[0]['manifest'])
        self.as_of = as_of
        # Quote SQLite literals; never interpolate SQL supplied by a model here.
        literal = lambda x: "'" + str(x).replace("'", "''") + "'"
        rel, cutoff = literal(self.release), literal(as_of)
        self.db.executescript(f'''
            CREATE TEMP VIEW patients AS SELECT * FROM main.patients WHERE release={rel};
            CREATE TEMP VIEW records AS SELECT * FROM main.records
              WHERE release={rel} AND identity='matched' AND arrival <= {cutoff}
                AND patient IN (SELECT id FROM main.patients WHERE release={rel});
            CREATE TEMP VIEW source_entities AS SELECT * FROM main.source_entities WHERE release={rel};
            PRAGMA query_only=ON;
        ''')
        self.db.enable_load_extension(False)
        self.db.set_authorizer(self._authorize)

    @staticmethod
    def _authorize(action, arg1, arg2, database, source):
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION:
            return (sqlite3.SQLITE_DENY if str(arg2).lower() in
                    {'load_extension', 'readfile', 'writefile', 'eval'} else sqlite3.SQLITE_OK)
        if action == sqlite3.SQLITE_READ:
            # SQLite reports materialized CTEs/subqueries without a database.
            # Their underlying physical reads are authorized separately.
            if database is None:
                return sqlite3.SQLITE_OK
            if database == 'temp' and arg1 in {'patients', 'records', 'source_entities'}:
                return sqlite3.SQLITE_OK
            if database == 'main' and ((arg1 == 'patients' and source in {'patients', 'records'}) or
                                      (arg1 in {'records', 'source_entities'} and source == arg1)):
                return sqlite3.SQLITE_OK
            # json_each/json_tree are SQLite virtual table functions, not source tables.
            if database in (None, 'main') and arg1 in {'json_each', 'json_tree'}:
                return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    def query(self, sql, *, max_rows=5000, max_chars=180000):
        if not isinstance(sql, str) or not sql.strip() or len(sql) > 30000:
            raise ValueError('A bounded SQL statement is required')
        validate_sql_scope(sql)
        return bounded_query(self.db, sql, {'as_of': self.as_of, 'release': self.release},
                             self.timeout_s, max_rows, max_chars)

    def close(self):
        self.db.close()


def bounded_query(db, sql, params, timeout_s, max_rows, max_chars):
    """Execute one read under a time limit; explicit truncation by rows and serialized size."""
    start = time.perf_counter()
    db.set_progress_handler(lambda: int(time.perf_counter() - start > timeout_s), 10000)
    try:
        cursor = db.execute(sql, params)
        if cursor.description is None:
            raise ValueError('Query must return rows')
        columns = [d[0] for d in cursor.description]
        if len(columns) != len(set(columns)):
            raise ValueError('Query needs unique output column names')
        rows, chars, truncated = [], 0, False
        for row in cursor:
            item = dict(row)
            size = len(json.dumps(item, allow_nan=False))
            if len(rows) >= max_rows or chars + size > max_chars:
                truncated = True
                break
            rows.append(item)
            chars += size
        return {'columns': columns, 'rows': rows, 'truncated': truncated,
                'elapsed_s': time.perf_counter() - start}
    finally:
        db.set_progress_handler(None, 0)


def _active_semantic_db():
    """The ACTIVE build's semantic layer (<build>/stack/semantic.sqlite3), if the activation names one."""
    config = ROOT.parent / 'config/agere/active_warehouse.json'
    try:
        return Path(json.loads(config.read_text())['build']) / 'stack' / 'semantic.sqlite3'
    except (OSError, ValueError, KeyError):
        return None


DEFAULT_SEMANTIC_DB = _active_semantic_db()


class SemanticWarehouse:
    """Read-only access to a build's semantic layer, as a customer's modeled tables would be exposed.

    Same safety approach as Warehouse: mode=ro, query_only, extension loading off, an authorizer that admits only
    SELECT/READ/RECURSIVE and non-file functions (no writes, ATTACH, PRAGMA or schema changes), a time limit and
    explicit truncation. Unlike Warehouse there are no scoping views: the semantic layer is ONE snapshot (meta.as_of)
    built by the product from the received store, with superseded corrections already removed. Every table is readable.
    """
    DENIED_FUNCTIONS = {'load_extension', 'readfile', 'writefile', 'eval', 'fts3_tokenizer'}

    def __init__(self, path=DEFAULT_SEMANTIC_DB, timeout_s=30):
        if path is None or not Path(path).is_file():
            raise ValueError(f'Semantic layer not found: {path}')
        self.path = Path(path).resolve()
        self.db = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.timeout_s = timeout_s
        self.db.execute('PRAGMA query_only=ON')
        self.meta = {k: v for k, v in self.db.execute('SELECT key, value FROM meta')}
        self.as_of = self.meta.get('as_of')
        names = [r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name")]
        self.columns = {n: [r[1] for r in self.db.execute(f'PRAGMA table_info("{n}")')] for n in names}
        self.db.enable_load_extension(False)
        self.db.set_authorizer(self._authorize)

    @classmethod
    def _authorize(cls, action, arg1, arg2, database, source):
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            # Reads of main tables, CTEs/subqueries (database None) and json_each/json_tree; never the temp schema.
            return sqlite3.SQLITE_OK if database in (None, 'main') else sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION:
            return sqlite3.SQLITE_DENY if str(arg2).lower() in cls.DENIED_FUNCTIONS else sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    def query(self, sql, *, as_of=None, max_rows=2000, max_chars=60000):
        if not isinstance(sql, str) or not sql.strip() or len(sql) > 30000:
            raise ValueError('A bounded SQL statement is required')
        return bounded_query(self.db, sql, {'as_of': as_of or self.as_of}, self.timeout_s, max_rows, max_chars)

    def close(self):
        self.db.close()


def reference_answer(question, warehouse):
    evidence = warehouse.query((ROOT / question['reference_sql']).read_text())
    if evidence['truncated']:
        raise ValueError('Reference query truncated; no reference answer can be assigned')
    kind = question['answer_type']
    answer = empty_answer(kind)
    rows = evidence['rows']
    if kind == 'unsupported':
        # Diagnostic SQL checks the explicit missing-prerequisite assertion.
        if len(rows) != 1 or rows[0].get('prerequisite_available') != 0:
            raise ValueError('Unsupported reference prerequisite changed; review this case')
        answer['status'] = 'unsupported'
        answer['missing'] = [r['description'] for r in question['expected_behavior']['required_missing']]
    elif kind == 'patient_set':
        ids = [r['patient_id'] for r in rows]
        if any(not isinstance(i, str) for i in ids) or len(ids) != len(set(ids)):
            raise ValueError('Reference patient IDs must be distinct strings')
        answer.update(patient_ids=sorted(ids), count=len(ids))
    elif kind == 'table':
        answer['table'] = rows
    else:
        if len(rows) != 1 or set(rows[0]) != {kind}:
            raise ValueError(f'Reference must return one {kind} cell')
        value = rows[0][kind]
        if not finite_number(value) or (kind == 'count' and (type(value) is not int or value < 0)):
            raise ValueError('Reference scalar must be finite, nonnegative integer for counts')
        answer[kind] = value
    return answer, evidence
