"""Read-only source connectors.

Every connector exposes the same small surface the profiler, matcher and applier use:
tables(), columns(table), row_count(table), sample(table, n), rows(table, columns) and
close(). Connections are opened read-only (SQLite URI mode=ro, DuckDB read_only=True,
Postgres default_transaction_read_only). Warehouse connectors (Snowflake, BigQuery,
Databricks) are declared with the configuration they need but carry no credential
handling: a pilot supplies an authenticated DB-API client (or the factories below build one from
environment variables only), Agere never stores secrets. FilesConnector reads a folder of
CSV / TSV / JSON / JSONL / Parquet extracts without writing anything into it.
"""
import csv
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

IDENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_$]*$')


def _ident(name):
    if not IDENT.match(str(name)): raise ValueError(f'Unsafe identifier: {name!r}')
    return '"' + name + '"'


class Connector:
    kind = 'abstract'
    read_only = True

    def tables(self): raise NotImplementedError
    def columns(self, table): raise NotImplementedError  # [(name, declared_type)]
    def row_count(self, table): raise NotImplementedError
    def sample(self, table, n): raise NotImplementedError  # list[dict]
    def rows(self, table, columns): raise NotImplementedError  # iterator of tuples, full scan
    def close(self): pass

    def describe(self):
        return {'kind': self.kind, 'read_only': self.read_only}


class SQLiteConnector(Connector):
    kind = 'sqlite'

    def __init__(self, path):
        self.path = Path(path).resolve()
        if not self.path.is_file(): raise FileNotFoundError(self.path)
        self.db = sqlite3.connect(f'file:{self.path}?mode=ro', uri=True)
        self.db.execute('PRAGMA query_only = 1')

    def tables(self):
        return [r[0] for r in self.db.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name")]

    def columns(self, table):
        return [(r[1], (r[2] or '').upper()) for r in self.db.execute(f'PRAGMA table_info({_ident(table)})')]

    def row_count(self, table):
        return self.db.execute(f'SELECT count(*) FROM {_ident(table)}').fetchone()[0]

    def sample(self, table, n):
        cur = self.db.execute(f'SELECT * FROM {_ident(table)} LIMIT ?', (int(n),))
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur]

    def rows(self, table, columns):
        cols = ', '.join(_ident(c) for c in columns)
        return self.db.execute(f'SELECT {cols} FROM {_ident(table)}')

    def close(self): self.db.close()

    def describe(self): return {'kind': self.kind, 'read_only': True, 'path': str(self.path)}


class DuckDBConnector(Connector):
    """DuckDB file (or Parquet/CSV folders attached as views by the caller). Requires the duckdb package."""
    kind = 'duckdb'

    def __init__(self, path):
        try:
            import duckdb
        except ImportError as e:  # optional dependency
            raise RuntimeError('duckdb is not installed; pip install duckdb to profile DuckDB/Parquet sources') from e
        self.path = str(path)
        self.db = duckdb.connect(self.path, read_only=True)

    def tables(self):
        return [r[0] for r in self.db.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main' ORDER BY 1").fetchall()]

    def columns(self, table):
        return [(r[0], str(r[1]).upper()) for r in self.db.execute(
            'SELECT column_name, data_type FROM information_schema.columns WHERE table_name = ? ORDER BY ordinal_position', [table]).fetchall()]

    def row_count(self, table): return self.db.execute(f'SELECT count(*) FROM {_ident(table)}').fetchone()[0]

    def sample(self, table, n):
        cur = self.db.execute(f'SELECT * FROM {_ident(table)} LIMIT {int(n)}')
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]

    def rows(self, table, columns):
        return iter(self.db.execute(f"SELECT {', '.join(_ident(c) for c in columns)} FROM {_ident(table)}").fetchall())

    def close(self): self.db.close()


class PostgresConnector(Connector):
    """Postgres via psycopg (v3) with a read-only session. The DSN comes from the caller's environment;
    nothing here reads or stores credentials."""
    kind = 'postgres'

    def __init__(self, dsn, schema='public'):
        try:
            import psycopg
        except ImportError as e:
            raise RuntimeError('psycopg is not installed; pip install psycopg to profile Postgres sources') from e
        self.schema = schema
        self.db = psycopg.connect(dsn, autocommit=True)
        self.db.execute('SET default_transaction_read_only = on')

    def tables(self):
        return [r[0] for r in self.db.execute(
            'SELECT table_name FROM information_schema.tables WHERE table_schema = %s ORDER BY 1', (self.schema,)).fetchall()]

    def columns(self, table):
        return [(r[0], r[1].upper()) for r in self.db.execute(
            'SELECT column_name, data_type FROM information_schema.columns WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position',
            (self.schema, table)).fetchall()]

    def _q(self, table): return f'{_ident(self.schema)}.{_ident(table)}'

    def row_count(self, table): return self.db.execute(f'SELECT count(*) FROM {self._q(table)}').fetchone()[0]

    def sample(self, table, n):
        cur = self.db.execute(f'SELECT * FROM {self._q(table)} LIMIT {int(n)}')
        names = [d.name for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]

    def rows(self, table, columns):
        return self.db.execute(f"SELECT {', '.join(_ident(c) for c in columns)} FROM {self._q(table)}")

    def close(self): self.db.close()


def _quote(name, q, label='identifier'):
    """Quote a warehouse identifier. Names come from the customer warehouse (may contain spaces), so reject
    only what could break out of the quoting: empty, control characters, the quote character, backslash."""
    n = str(name)
    if not n or any(ord(c) < 32 for c in n) or q in n or '\\' in n:
        raise ValueError(f'Unsafe {label}: {name!r}')
    return q + n + q


class ClientConnector(Connector):
    """Cloud warehouse connector over a DB-API 2.0 client (Snowflake, BigQuery, Databricks SQL).

    `client` is any object with cursor() (a connection). Agere never stores credentials: the pilot passes an
    authenticated, SELECT-only client, or uses snowflake_client / bigquery_client / databricks_client which read
    environment variables. `database` is 'DB.SCHEMA' (snowflake), 'project.dataset' (bigquery) or
    'catalog.schema' (databricks). Only SELECT / SHOW / DESCRIBE statements are ever issued. Table names are
    validated and quoted per dialect; only values (schema/table lookups) go through driver parameters."""
    DIALECTS = {
        'snowflake': {'quote': '"', 'paramstyle': 'format'},       # snowflake-connector-python: %s
        'bigquery': {'quote': '`', 'paramstyle': 'pyformat'},      # google-cloud-bigquery dbapi: %(name)s
        'databricks': {'quote': '`', 'paramstyle': 'pyformat'},    # databricks-sql-connector: %(name)s (v2/v3 inline)
    }
    REQUIRED_CONFIG = {
        'snowflake': ['account', 'warehouse', 'database', 'schema', 'role (SELECT-only)'],
        'bigquery': ['project', 'dataset', 'service account with roles/bigquery.dataViewer + jobUser'],
        'databricks': ['server_hostname', 'http_path', 'catalog.schema', 'token held by the customer'],
    }
    FETCH = 10000

    def __init__(self, client, dialect, database, paramstyle=None):
        if dialect not in self.DIALECTS: raise ValueError(f'Unknown dialect {dialect}')
        parts = str(database).split('.')
        if len(parts) != 2 or not all(parts):
            raise ValueError(f'database must be two dot-separated parts for {dialect}: {self.REQUIRED_CONFIG[dialect]}')
        cfg = self.DIALECTS[dialect]
        self.kind, self.client, self.database = dialect, client, database
        self._q = cfg['quote']
        self.paramstyle = paramstyle or cfg['paramstyle']
        if self.paramstyle not in ('format', 'pyformat', 'named', 'qmark'): raise ValueError(f'Unknown paramstyle {self.paramstyle}')
        if dialect == 'snowflake': parts = [p.upper() if p == p.lower() else p for p in parts]
        self.parts = parts
        for p in parts: _quote(p, self._q, 'database part')

    # -- SQL construction ------------------------------------------------------------------
    def _id(self, name): return _quote(name, self._q)

    def _fq(self, table):
        return '.'.join(self._id(p) for p in self.parts + [table])

    def _info(self):
        """Quoted prefix of the INFORMATION_SCHEMA views, plus whether they are already schema-scoped."""
        if self.kind == 'bigquery': return f'{self._id(self.parts[0])}.{self._id(self.parts[1])}.INFORMATION_SCHEMA', True
        return f'{self._id(self.parts[0])}.information_schema', False

    def _bind(self, values):
        values = list(values)
        if self.paramstyle == 'format': return ['%s'] * len(values), tuple(values)
        if self.paramstyle == 'qmark': return ['?'] * len(values), tuple(values)
        names = [f'p{i}' for i in range(len(values))]
        fmt = '%({})s' if self.paramstyle == 'pyformat' else ':{}'
        return [fmt.format(n) for n in names], dict(zip(names, values))

    def _run(self, sql, values=()):
        head = sql.lstrip().split(None, 1)[0].upper()
        if head not in ('SELECT', 'SHOW', 'DESCRIBE'): raise ValueError(f'Refusing non-read statement: {head}')
        cur = self.client.cursor()
        try:
            if values:
                _, params = self._bind(values)
                cur.execute(sql, params)
            else:
                cur.execute(sql)
        except Exception:
            self._close_cursor(cur)
            raise
        return cur

    @staticmethod
    def _close_cursor(cur):
        try: cur.close()
        except Exception: pass

    def _all(self, sql, values=()):
        cur = self._run(sql, values)
        try: return cur.fetchall()
        finally: self._close_cursor(cur)

    def _where(self, extra_cols, values):
        info, scoped = self._info()
        conds, vals = [], []
        if not scoped: conds.append('table_schema'); vals.append(self.parts[1])
        conds += extra_cols; vals += values
        ph, _ = self._bind(vals)
        return info, ' AND '.join(f'{c} = {p}' for c, p in zip(conds, ph)), vals

    # -- Connector surface -----------------------------------------------------------------
    def tables(self):
        info, where, vals = self._where([], [])
        try:
            rows = self._all(f'SELECT table_name FROM {info}.tables WHERE {where} ORDER BY 1' if where
                             else f'SELECT table_name FROM {info}.TABLES ORDER BY 1', vals)
            return [r[0] for r in rows]
        except Exception:
            if self.kind != 'databricks': raise
            # Legacy hive_metastore has no information_schema; SHOW TABLES returns (database, tableName, isTemporary).
            rows = self._all(f'SHOW TABLES IN {self._id(self.parts[0])}.{self._id(self.parts[1])}')
            return sorted(r[1] for r in rows)

    def columns(self, table):
        _quote(table, self._q, 'table')
        info, where, vals = self._where(['table_name'], [table])
        try:
            rows = self._all(f'SELECT column_name, data_type FROM {info}.columns WHERE {where} ORDER BY ordinal_position', vals)
            return [(r[0], str(r[1]).upper()) for r in rows]
        except Exception:
            if self.kind != 'databricks': raise
            rows = self._all(f'DESCRIBE TABLE {self._fq(table)}')
            out = []
            for r in rows:
                if not r[0] or str(r[0]).startswith('#'): break  # partition / detail sections follow a blank or '#' row
                out.append((r[0], str(r[1]).upper()))
            return out

    def row_count(self, table):
        return int(self._all(f'SELECT count(*) FROM {self._fq(table)}')[0][0])

    def sample(self, table, n):
        # Deterministic LIMIT (not TABLESAMPLE) so a table smaller than n is always returned whole.
        cur = self._run(f'SELECT * FROM {self._fq(table)} LIMIT {int(n)}')
        try:
            names = [d[0] for d in cur.description]
            return [dict(zip(names, r)) for r in cur.fetchall()]
        finally: self._close_cursor(cur)

    def rows(self, table, columns):
        cols = ', '.join(self._id(c) for c in columns)
        cur = self._run(f'SELECT {cols} FROM {self._fq(table)}')
        try:
            while True:
                batch = cur.fetchmany(self.FETCH)
                if not batch: break
                for r in batch: yield tuple(r)
        finally: self._close_cursor(cur)

    def close(self):
        try: self.client.close()
        except Exception: pass

    def describe(self): return {'kind': self.kind, 'read_only': True, 'database': self.database}


# -- Authenticated client factories: credentials come from the environment only, never stored -----------

def _need_env(*names):
    out = []
    for n in names:
        v = os.environ.get(n)
        if not v: raise RuntimeError(f'Environment variable {n} is not set')
        out.append(v)
    return out


def snowflake_client(database, schema):
    """snowflake-connector-python connection. Env: SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PASSWORD or
    SNOWFLAKE_PRIVATE_KEY_PATH (optional SNOWFLAKE_PRIVATE_KEY_PASSPHRASE), SNOWFLAKE_WAREHOUSE, SNOWFLAKE_ROLE."""
    try:
        import snowflake.connector as sf
    except ImportError as e:
        raise RuntimeError('snowflake-connector-python is not installed; pip install snowflake-connector-python') from e
    account, user, warehouse = _need_env('SNOWFLAKE_ACCOUNT', 'SNOWFLAKE_USER', 'SNOWFLAKE_WAREHOUSE')
    kw = dict(account=account, user=user, warehouse=warehouse, database=database, schema=schema,
              session_parameters={'QUERY_TAG': 'agere-readonly'})
    if os.environ.get('SNOWFLAKE_ROLE'): kw['role'] = os.environ['SNOWFLAKE_ROLE']
    key_path = os.environ.get('SNOWFLAKE_PRIVATE_KEY_PATH')
    if key_path:
        try:
            from cryptography.hazmat.primitives import serialization
        except ImportError as e:
            raise RuntimeError('cryptography is not installed; pip install cryptography for Snowflake key-pair auth') from e
        pw = os.environ.get('SNOWFLAKE_PRIVATE_KEY_PASSPHRASE')
        with open(key_path, 'rb') as f:
            key = serialization.load_pem_private_key(f.read(), password=pw.encode() if pw else None)
        kw['private_key'] = key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                              serialization.NoEncryption())
    else:
        kw['password'] = _need_env('SNOWFLAKE_PASSWORD')[0]
    return sf.connect(**kw)


def bigquery_client(project, dataset=None):
    """BigQuery DB-API connection using Application Default Credentials / GOOGLE_APPLICATION_CREDENTIALS."""
    try:
        from google.cloud import bigquery
        from google.cloud.bigquery import dbapi
    except ImportError as e:
        raise RuntimeError('google-cloud-bigquery is not installed; pip install google-cloud-bigquery') from e
    return dbapi.connect(bigquery.Client(project=project))


def databricks_client(catalog, schema):
    """databricks-sql-connector connection. Env: DATABRICKS_SERVER_HOSTNAME, DATABRICKS_HTTP_PATH, DATABRICKS_TOKEN."""
    try:
        from databricks import sql as dbsql
    except ImportError as e:
        raise RuntimeError('databricks-sql-connector is not installed; pip install databricks-sql-connector') from e
    host, http_path, token = _need_env('DATABRICKS_SERVER_HOSTNAME', 'DATABRICKS_HTTP_PATH', 'DATABRICKS_TOKEN')
    return dbsql.connect(server_hostname=host, http_path=http_path, access_token=token, catalog=catalog, schema=schema)


# -- Extract files -----------------------------------------------------------------------------------

class FilesConnector(Connector):
    """A folder of CSV / TSV / JSON / JSONL / Parquet extracts (the usual day-one customer handoff).

    Table name = sanitized file stem (lowercase, non-alphanumeric -> '_'). A subfolder of same-schema part files
    (claims/part-0001.parquet, ...) becomes one table named after the folder. CSV and JSON use the standard
    library; Parquet needs duckdb. Files are only ever opened for reading."""
    kind = 'files'
    EXTS = {'.csv': 'csv', '.tsv': 'csv', '.jsonl': 'jsonl', '.ndjson': 'jsonl', '.json': 'json', '.parquet': 'parquet'}
    INFER_ROWS = 2000

    def __init__(self, path):
        self.path = Path(path).resolve()
        if not self.path.is_dir(): raise NotADirectoryError(self.path)
        self._tables = {}
        csv.field_size_limit(min(sys.maxsize, 2 ** 31 - 1))
        groups = {}
        for p in sorted(self.path.iterdir()):
            if p.name.startswith('.'): continue
            if p.is_file() and p.suffix.lower() in self.EXTS: groups.setdefault(self._name(p.stem), []).append([p])
            elif p.is_dir():
                files = sorted(f for f in p.rglob('*') if f.is_file() and f.suffix.lower() in self.EXTS
                               and not any(x.startswith('.') for x in f.relative_to(p).parts))
                if files: groups.setdefault(self._name(p.name), []).append(files)
        for name, entries in groups.items():
            for i, files in enumerate(entries):
                self._tables[name if i == 0 else f'{name}_{i + 1}'] = {'files': files}
        self._duck = None

    @staticmethod
    def _name(stem):
        n = re.sub(r'[^a-z0-9]+', '_', stem.lower()).strip('_') or 'table'
        return 't_' + n if n[0].isdigit() else n

    # per-file readers: return (columns [(name, type)], iterator factory of dict rows)
    def _duckdb(self):
        if self._duck is None:
            try:
                import duckdb
            except ImportError as e:
                raise RuntimeError('duckdb is not installed; pip install duckdb to read Parquet extracts') from e
            self._duck = duckdb.connect(':memory:')
        return self._duck

    def _file_schema(self, f):
        kind = self.EXTS[f.suffix.lower()]
        if kind == 'parquet':
            d = self._duck_describe(f)
            return d
        names, sample = None, []
        if kind == 'csv':
            names, rows = self._csv_open(f)
            raw = []
            for r in rows:
                raw.append(r)
                if len(raw) >= self.INFER_ROWS: break
            return [(n, self._infer_text([r[i] if i < len(r) else '' for r in raw])) for i, n in enumerate(names)]
        recs = []
        for r in self._json_records(f):
            recs.append(r)
            if len(recs) >= self.INFER_ROWS: break
        cols = {}
        for r in recs:
            for k, v in r.items():
                cols[k] = self._merge(cols.get(k), self._py_type(v))
        return [(k, t or 'TEXT') for k, t in cols.items()]

    def _duck_describe(self, f):
        rows = self._duckdb().execute('DESCRIBE SELECT * FROM read_parquet(?)', [str(f)]).fetchall()
        return [(r[0], str(r[1]).upper()) for r in rows]

    @staticmethod
    def _merge(a, b):
        if a is None or a == b: return b if a is None else a
        if b is None: return a
        return 'REAL' if {a, b} == {'INTEGER', 'REAL'} else 'TEXT'

    @staticmethod
    def _py_type(v):
        if v is None: return None
        if isinstance(v, bool): return 'BOOLEAN'
        if isinstance(v, int): return 'INTEGER'
        if isinstance(v, float): return 'REAL'
        return 'TEXT'

    _INT = re.compile(r'^[+-]?(0|[1-9]\d*)$')
    _REAL = re.compile(r'^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$')

    def _infer_text(self, values):
        vals = [v for v in values if v not in ('', None)]
        if not vals: return 'TEXT'
        if all(self._INT.match(v) for v in vals): return 'INTEGER'
        if all(self._REAL.match(v) and not (len(v) > 1 and v[0] == '0' and v[1].isdigit()) for v in vals): return 'REAL'
        return 'TEXT'

    @staticmethod
    def _convert(t):
        def conv(v):
            if v == '' or v is None: return None
            try:
                if t == 'INTEGER': return int(v)
                if t == 'REAL': return float(v)
            except ValueError: pass
            return v
        return conv

    def _csv_open(self, f):
        fh = open(f, 'r', encoding='utf-8-sig', newline='')
        rd = csv.reader(fh, delimiter='\t' if f.suffix.lower() == '.tsv' else ',')
        try: header = next(rd)
        except StopIteration: header = []
        names = [h.strip() or f'column_{i + 1}' for i, h in enumerate(header)]
        seen = {}
        for i, n in enumerate(names):  # de-duplicate repeated headers
            if n in seen: seen[n] += 1; names[i] = f'{n}_{seen[n]}'
            else: seen[n] = 1

        def gen():
            try:
                for r in rd: yield r
            finally: fh.close()
        return names, gen()

    def _json_records(self, f):
        kind = self.EXTS[f.suffix.lower()]
        if kind == 'jsonl':
            with open(f, 'r', encoding='utf-8-sig') as fh:
                for line in fh:
                    line = line.strip()
                    if line: yield self._flat(json.loads(line))
            return
        with open(f, 'r', encoding='utf-8-sig') as fh: data = json.load(fh)
        if isinstance(data, dict):
            lists = [v for v in data.values() if isinstance(v, list)]
            data = lists[0] if len(lists) == 1 else [data]
        for r in data: yield self._flat(r)

    @staticmethod
    def _flat(r):
        if not isinstance(r, dict): return {'value': r if not isinstance(r, (list, dict)) else json.dumps(r)}
        return {k: (json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else v) for k, v in r.items()}

    def _file_rows(self, f, types):
        """Yield dict rows for one file."""
        kind = self.EXTS[f.suffix.lower()]
        if kind == 'csv':
            names, rows = self._csv_open(f)
            conv = [self._convert(types.get(n)) for n in names]
            for r in rows:
                yield {n: conv[i](r[i]) if i < len(r) else None for i, n in enumerate(names)}
        elif kind == 'parquet':
            cur = self._duckdb().execute('SELECT * FROM read_parquet(?)', [str(f)])
            names = [d[0] for d in cur.description]
            while True:
                batch = cur.fetchmany(10000)
                if not batch: break
                for r in batch: yield dict(zip(names, r))
        else:
            yield from self._json_records(f)

    def _info(self, table):
        if table not in self._tables: raise KeyError(f'Unknown table {table}')
        t = self._tables[table]
        if 'columns' not in t:
            cols, per_file = {}, {}
            for f in t['files']:
                sch = self._file_schema(f)
                per_file[f] = dict(sch)
                for n, ty in sch: cols[n] = self._merge(cols.get(n), ty)
            t['columns'], t['types'] = list(cols.items()), per_file
        return t

    def _iter(self, table):
        t = self._info(table)
        for f in t['files']: yield from self._file_rows(f, t['types'][f])

    def tables(self): return sorted(self._tables)

    def columns(self, table): return list(self._info(table)['columns'])

    def row_count(self, table): return sum(1 for _ in self._iter(table))

    def sample(self, table, n):
        names = [c for c, _ in self.columns(table)]
        out = []
        if int(n) <= 0: return out
        for r in self._iter(table):
            out.append({c: r.get(c) for c in names})
            if len(out) >= int(n): break
        return out

    def rows(self, table, columns):
        known = {c for c, _ in self.columns(table)}
        bad = [c for c in columns if c not in known]
        if bad: raise ValueError(f'Unknown columns in {table}: {bad}')
        return (tuple(r.get(c) for c in columns) for r in self._iter(table))

    def close(self):
        if self._duck is not None: self._duck.close(); self._duck = None

    def describe(self): return {'kind': self.kind, 'read_only': True, 'path': str(self.path), 'tables': len(self._tables)}


def connect(spec):
    """Connector from a spec string: sqlite:///path, duckdb:///path, postgresql://..., files:///abs/dir (or a
    directory path), snowflake://DB/SCHEMA, bigquery://project/dataset, databricks://catalog/schema.
    Warehouse credentials are read from environment variables by the client factories."""
    s = str(spec)
    if s.startswith('sqlite:///'): return SQLiteConnector(s[len('sqlite:///') - 1:])
    if s.startswith('duckdb:///'): return DuckDBConnector(s[len('duckdb:///') - 1:])
    if s.startswith('postgres://') or s.startswith('postgresql://'): return PostgresConnector(s)
    if s.startswith('files://'): return FilesConnector(s[len('files://'):])
    for scheme, factory in (('snowflake', snowflake_client), ('bigquery', bigquery_client), ('databricks', databricks_client)):
        if s.startswith(scheme + '://'):
            parts = [p for p in s[len(scheme) + 3:].split('/') if p]
            if len(parts) != 2: raise ValueError(f'{scheme} spec must be {scheme}://<first>/<second>: {spec}')
            conn_parts = parts
            return ClientConnector(factory(*conn_parts), scheme, '.'.join(conn_parts))
    if s.endswith(('.sqlite', '.sqlite3', '.db')): return SQLiteConnector(s)
    if s.endswith('.duckdb'): return DuckDBConnector(s)
    if Path(s).is_dir(): return FilesConnector(s)
    raise ValueError(f'Unsupported source spec: {spec}')
