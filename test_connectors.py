import csv
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from clinical_core.agere.onboarding import connectors as C

try:
    import duckdb
except ImportError:
    duckdb = None


class FilesConnectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'Patients 2025.csv').write_text('id,name,age,zip\n1,Ann,34,01234\n2,Bob,,02134\n3,"Cy, Jr",51,98101\n')
        (self.root / 'events.jsonl').write_text('{"pid": 1, "code": "I50.9", "v": 1.5}\n\n{"pid": 2, "code": "E11", "extra": {"a": 1}}\n')
        (self.root / 'lab.json').write_text('{"rows": [{"k": 1}, {"k": 2}]}')
        (self.root / 'notes.txt').write_text('ignored')
        part = self.root / 'Claims'
        (part / 'y2025').mkdir(parents=True)
        (part / 'part-0001.csv').write_text('claim,amt\nA,10\nB,20\n')
        (part / 'y2025' / 'part-0002.csv').write_text('claim,amt\nC,30.5\n')
        self.before = sorted(p.name for p in self.root.rglob('*'))

    def tearDown(self): self.tmp.cleanup()

    def test_tables_and_names(self):
        c = C.FilesConnector(self.root)
        self.assertEqual(c.tables(), ['claims', 'events', 'lab', 'patients_2025'])

    def test_csv_types_and_values(self):
        c = C.FilesConnector(self.root)
        self.assertEqual(c.columns('patients_2025'), [('id', 'INTEGER'), ('name', 'TEXT'), ('age', 'INTEGER'), ('zip', 'TEXT')])
        self.assertEqual(c.row_count('patients_2025'), 3)
        s = c.sample('patients_2025', 2)
        self.assertEqual(s[1], {'id': 2, 'name': 'Bob', 'age': None, 'zip': '02134'})
        self.assertEqual(list(c.rows('patients_2025', ['name', 'id']))[2], ('Cy, Jr', 3))
        with self.assertRaises(ValueError): c.rows('patients_2025', ['nope'])

    def test_jsonl_and_json(self):
        c = C.FilesConnector(self.root)
        cols = dict(c.columns('events'))
        self.assertEqual((cols['pid'], cols['v'], cols['extra']), ('INTEGER', 'REAL', 'TEXT'))
        self.assertEqual(c.sample('events', 5)[0]['extra'], None)
        self.assertEqual(list(c.rows('lab', ['k'])), [(1,), (2,)])

    def test_folder_parts_one_table(self):
        c = C.FilesConnector(self.root)
        self.assertEqual(c.row_count('claims'), 3)
        self.assertEqual(c.columns('claims'), [('claim', 'TEXT'), ('amt', 'REAL')])
        self.assertEqual(list(c.rows('claims', ['amt'])), [(10.0,), (20.0,), (30.5,)])

    def test_read_only_and_describe(self):
        c = C.FilesConnector(self.root)
        for t in c.tables(): c.row_count(t); c.sample(t, 3)
        self.assertEqual(sorted(p.name for p in self.root.rglob('*')), self.before)
        self.assertTrue(c.describe()['read_only'])

    def test_connect_routing(self):
        self.assertIsInstance(C.connect(f'files://{self.root}'), C.FilesConnector)
        self.assertIsInstance(C.connect(str(self.root)), C.FilesConnector)

    def test_parquet(self):
        if duckdb is None:
            with mock.patch.dict(sys.modules, {'duckdb': None}):
                (self.root / 'x.parquet').write_bytes(b'PAR1')
                with self.assertRaisesRegex(RuntimeError, 'pip install duckdb'):
                    C.FilesConnector(self.root).columns('x')
            return
        con = duckdb.connect(':memory:')
        con.execute("COPY (SELECT 1 AS id, 'a' AS s UNION ALL SELECT 2, 'b') TO '%s' (FORMAT PARQUET)" % (self.root / 'pq.parquet'))
        c = C.FilesConnector(self.root)
        self.assertEqual(c.row_count('pq'), 2)
        self.assertEqual([n for n, _ in c.columns('pq')], ['id', 's'])
        self.assertEqual(list(c.rows('pq', ['s'])), [('a',), ('b',)])


class FakeCursor:
    def __init__(self, client): self.c, self.res, self.description, self.sizes = client, [], None, []

    def execute(self, sql, params=None):
        self.c.executed.append((sql, params))
        low = sql.lower()
        db = self.c.db
        if 'information_schema' in low and self.c.no_info:
            raise RuntimeError('no information_schema')
        if 'information_schema' in low and '.tables' in low:
            self.res = [(r[0],) for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY 1")]
            self.description = [('table_name',)]
        elif 'information_schema' in low and '.columns' in low:
            t = list(params.values())[-1] if isinstance(params, dict) else params[-1]
            self.res = [(r[1], r[2]) for r in db.execute(f'PRAGMA table_info("{t}")')]
            self.description = [('column_name',), ('data_type',)]
        elif low.startswith('show tables'):
            self.res = [('sch', r[0], False) for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY 1")]
        elif low.startswith('describe table'):
            t = sql.split('`.`')[-1].rstrip('`')
            self.res = [(r[1], r[2], None) for r in db.execute(f'PRAGMA table_info("{t}")')] + [('', '', ''), ('# Detail', 'x', 'y')]
        else:
            assert self.c.prefix in sql, sql
            cur = db.execute(sql.replace(self.c.prefix, ''))
            self.res, self.description = cur.fetchall(), cur.description

    def fetchall(self): r, self.res = self.res, []; return r

    def fetchmany(self, n):
        self.sizes.append(n)
        r, self.res = self.res[:n], self.res[n:]
        return r

    def close(self): self.c.closed_cursors += 1


class FakeClient:
    def __init__(self, prefix, no_info=False):
        self.db = sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE patients (id INTEGER, name TEXT)')
        self.db.executemany('INSERT INTO patients VALUES (?,?)', [(i, f'n{i}') for i in range(1, 6)])
        self.db.execute('CREATE TABLE claims (cid TEXT)')
        self.prefix, self.no_info, self.executed, self.closed_cursors, self.closed = prefix, no_info, [], 0, False
        self.cursors = []

    def cursor(self): c = FakeCursor(self); self.cursors.append(c); return c
    def close(self): self.closed = True


CASES = {
    'snowflake': ('DEMO.PUBLIC', '"DEMO"."PUBLIC".', '"', '%s'),
    'bigquery': ('my-proj.ds', '`my-proj`.`ds`.', '`', '%(p0)s'),
    'databricks': ('cat.sch', '`cat`.`sch`.', '`', '%(p0)s'),
}


class ClientConnectorTests(unittest.TestCase):
    def test_each_dialect(self):
        for dialect, (db, prefix, q, ph) in CASES.items():
            with self.subTest(dialect):
                fc = FakeClient(prefix)
                c = C.ClientConnector(fc, dialect, db)
                self.assertEqual(c.tables(), ['claims', 'patients'])
                self.assertEqual(c.columns('patients'), [('id', 'INTEGER'), ('name', 'TEXT')])
                self.assertEqual(c.row_count('patients'), 5)
                s = c.sample('patients', 2)
                self.assertEqual(s, [{'id': 1, 'name': 'n1'}, {'id': 2, 'name': 'n2'}])
                self.assertEqual(list(c.rows('patients', ['name']))[4], ('n5',))
                sqls = [x[0] for x in fc.executed]
                self.assertTrue(any(f'FROM {prefix}{q}patients{q}' in x or f'FROM {prefix}{q}patients{q} LIMIT' in x for x in sqls))
                col_sql, col_params = [x for x in fc.executed if 'column_name' in x[0]][0]
                self.assertIn(ph, col_sql)
                self.assertNotIn('patients', col_sql)  # value goes through the driver, not the SQL text
                self.assertIn('patients', col_params if not isinstance(col_params, dict) else col_params.values())
                self.assertTrue(all(x.lstrip().split()[0].upper() in ('SELECT', 'SHOW', 'DESCRIBE') for x in sqls))
                self.assertEqual(c.describe(), {'kind': dialect, 'read_only': True, 'database': db})
                c.close(); self.assertTrue(fc.closed)

    def test_snowflake_params_positional(self):
        fc = FakeClient('"DEMO"."PUBLIC".')
        C.ClientConnector(fc, 'snowflake', 'demo.public').tables()
        self.assertEqual(fc.executed[0][1], ('PUBLIC',))
        self.assertIn('"DEMO".information_schema.tables', fc.executed[0][0])

    def test_bigquery_scoped_info_schema(self):
        fc = FakeClient('`p`.`d`.')
        C.ClientConnector(fc, 'bigquery', 'p.d').tables()
        self.assertEqual(fc.executed[0], ('SELECT table_name FROM `p`.`d`.INFORMATION_SCHEMA.TABLES ORDER BY 1', None))

    def test_rows_streams_in_10000_batches(self):
        fc = FakeClient('"A"."B".')
        c = C.ClientConnector(fc, 'snowflake', 'A.B')
        it = c.rows('patients', ['id'])
        self.assertEqual(next(it), (1,))
        self.assertEqual(fc.cursors[-1].sizes, [10000])
        self.assertEqual(len(list(it)), 4)
        self.assertGreaterEqual(fc.closed_cursors, 1)

    def test_databricks_legacy_fallback(self):
        fc = FakeClient('`hive_metastore`.`sch`.', no_info=True)
        c = C.ClientConnector(fc, 'databricks', 'hive_metastore.sch')
        self.assertEqual(c.tables(), ['claims', 'patients'])
        self.assertEqual(c.columns('patients'), [('id', 'INTEGER'), ('name', 'TEXT')])

    def test_unsafe_names_and_bad_config(self):
        c = C.ClientConnector(FakeClient('"A"."B".'), 'snowflake', 'A.B')
        for bad in ('x"; DROP TABLE y; --', 'a\nb', '', 'a\\b'):
            with self.assertRaises(ValueError): c.row_count(bad)
        with self.assertRaises(ValueError): C.ClientConnector(FakeClient(''), 'bigquery', 'a`b.c')
        with self.assertRaises(ValueError): C.ClientConnector(FakeClient(''), 'bigquery', 'only')
        with self.assertRaises(ValueError): C.ClientConnector(FakeClient(''), 'oracle', 'a.b')

    def test_qmark_and_named_styles(self):
        for style in ('qmark', 'named'):
            fc = FakeClient('"A"."B".')
            cc = C.ClientConnector(fc, 'snowflake', 'A.B', paramstyle=style)
            if style == 'qmark':
                self.assertEqual(cc._bind(['x']), (['?'], ('x',)))
            else:
                self.assertEqual(cc._bind(['x']), ([':p0'], {'p0': 'x'}))


class ConnectRoutingTests(unittest.TestCase):
    def test_missing_sdk_messages(self):
        mods = {'snowflake': None, 'snowflake.connector': None, 'google.cloud.bigquery': None, 'google.cloud': None, 'databricks': None, 'databricks.sql': None}
        with mock.patch.dict(sys.modules, mods):
            for spec, pkg in (('snowflake://DB/SCH', 'snowflake-connector-python'), ('bigquery://p/d', 'google-cloud-bigquery'),
                              ('databricks://cat/sch', 'databricks-sql-connector')):
                with self.subTest(spec):
                    with self.assertRaisesRegex(RuntimeError, 'pip install ' + pkg):
                        C.connect(spec)

    def test_routing_uses_factory_and_args(self):
        seen = {}
        def fake(scheme):
            def f(a, b): seen[scheme] = (a, b); return FakeClient('')
            return f
        with mock.patch.object(C, 'snowflake_client', fake('snowflake')), mock.patch.object(C, 'bigquery_client', fake('bigquery')), \
                mock.patch.object(C, 'databricks_client', fake('databricks')):
            s = C.connect('snowflake://DB/SCH'); b = C.connect('bigquery://my-proj/ds'); d = C.connect('databricks://cat/sch')
        self.assertEqual(seen, {'snowflake': ('DB', 'SCH'), 'bigquery': ('my-proj', 'ds'), 'databricks': ('cat', 'sch')})
        self.assertEqual((s.kind, b.database, d.database), ('snowflake', 'my-proj.ds', 'cat.sch'))

    def test_bad_spec_and_missing_env_never_leaks(self):
        with self.assertRaises(ValueError): C.connect('snowflake://onlyone')
        with self.assertRaises(ValueError): C.connect('/definitely/not/a/thing.xyz')
        fake_sf = mock.MagicMock()
        with mock.patch.dict(sys.modules, {'snowflake': fake_sf, 'snowflake.connector': fake_sf.connector}), \
                mock.patch.dict(os.environ, {'SNOWFLAKE_ACCOUNT': 'acct', 'SNOWFLAKE_USER': 'u', 'SNOWFLAKE_WAREHOUSE': 'w', 'SNOWFLAKE_PASSWORD': 'sekret-pw'}, clear=False):
            conn = C.connect('snowflake://DB/SCH')
            kw = fake_sf.connector.connect.call_args.kwargs
            self.assertEqual((kw['database'], kw['schema'], kw['password']), ('DB', 'SCH', 'sekret-pw'))
            self.assertNotIn('sekret-pw', str(conn.describe()))
        with mock.patch.dict(sys.modules, {'snowflake': fake_sf, 'snowflake.connector': fake_sf.connector}), \
                mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'SNOWFLAKE_ACCOUNT'):
                C.connect('snowflake://DB/SCH')

    def test_existing_routes(self):
        with self.assertRaises(ValueError): C.connect('nope')


if __name__ == '__main__':
    unittest.main()
