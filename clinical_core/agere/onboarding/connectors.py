"""Read-only source connectors.

Every connector exposes the same small surface the profiler, matcher and applier use:
tables(), columns(table), row_count(table), sample(table, n), rows(table, columns) and
close(). Connections are opened read-only (SQLite URI mode=ro, DuckDB read_only=True,
Postgres default_transaction_read_only). Warehouse connectors (Snowflake, BigQuery,
Databricks) are declared with the configuration they need but carry no credential
handling: a pilot supplies an authenticated client object, Agere never stores secrets.
"""
from pathlib import Path
import re
import sqlite3

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


class ClientConnector(Connector):
    """Cloud warehouse connector over a caller-supplied DB-API client (Snowflake, BigQuery, Databricks SQL).

    Agere does not implement authentication: the pilot team constructs an authenticated, read-only client
    (e.g. a Snowflake role with SELECT only, a BigQuery dataset viewer service account) and passes it in.
    `dialect` selects the information-schema query and sampling clause."""
    DIALECTS = {
        'snowflake': {'tables': "SELECT table_name FROM {db}.information_schema.tables WHERE table_schema = %s ORDER BY 1",
                      'columns': "SELECT column_name, data_type FROM {db}.information_schema.columns WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",
                      'sample': 'SELECT * FROM {t} SAMPLE ({n} ROWS)'},
        'bigquery': {'tables': 'SELECT table_name FROM `{db}`.INFORMATION_SCHEMA.TABLES ORDER BY 1',
                     'columns': 'SELECT column_name, data_type FROM `{db}`.INFORMATION_SCHEMA.COLUMNS WHERE table_name = @t ORDER BY ordinal_position',
                     'sample': 'SELECT * FROM {t} TABLESAMPLE SYSTEM (1 PERCENT) LIMIT {n}'},
        'databricks': {'tables': 'SHOW TABLES IN {db}', 'columns': 'DESCRIBE TABLE {t}', 'sample': 'SELECT * FROM {t} TABLESAMPLE ({n} ROWS)'},
    }
    REQUIRED_CONFIG = {
        'snowflake': ['account', 'warehouse', 'database', 'schema', 'role (SELECT-only)'],
        'bigquery': ['project', 'dataset', 'service account with roles/bigquery.dataViewer + jobUser'],
        'databricks': ['server_hostname', 'http_path', 'catalog.schema', 'token held by the customer'],
    }

    def __init__(self, client, dialect, database):
        if dialect not in self.DIALECTS: raise ValueError(f'Unknown dialect {dialect}')
        self.kind, self.client, self.database = dialect, client, database
        raise NotImplementedError(f'{dialect} connector is a declared interface for pilots; supply a read-only client '
                                  f'and implement execution against it. Needs: {self.REQUIRED_CONFIG[dialect]}')


def connect(spec):
    """Connector from a spec string: sqlite:///path, duckdb:///path, postgresql://... ."""
    s = str(spec)
    if s.startswith('sqlite:///'): return SQLiteConnector(s[len('sqlite:///') - 1:])
    if s.startswith('duckdb:///'): return DuckDBConnector(s[len('duckdb:///') - 1:])
    if s.startswith('postgres://') or s.startswith('postgresql://'): return PostgresConnector(s)
    if s.endswith(('.sqlite', '.sqlite3', '.db')): return SQLiteConnector(s)
    if s.endswith('.duckdb'): return DuckDBConnector(s)
    raise ValueError(f'Unsupported source spec: {spec}')
