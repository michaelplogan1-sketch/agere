"""DuckDB answer engine (steps B-E): build beside SQLite, engine switch with SQLite fallback, dialect lint, shadow log."""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    import duckdb  # noqa: F401
    from clinical_core.agere.clinical import duckdb_engine as de, sql_query
except ImportError:
    de = None

# Analyst questions this exercises, as the SQL the compiler/learned tiers emit for them.
PAID_BY_PAYER_MONTH = ("SELECT payer, substr(service_date,1,7) AS month, SUM(net_payer_paid_cents)/100.0 AS paid_dollars "
                       "FROM claim GROUP BY 1, 2 ORDER BY 1, 2")  # "Total paid claims by payer by month"
A1C_OVER_9 = ("SELECT COUNT(DISTINCT patient_id) FROM measurement WHERE concept = 'a1c' AND value > 9 "
              "AND substr(event_date,1,4) = '2029'")  # "How many patients had an A1c over 9 last year?"
AGE_65 = "SELECT COUNT(*) FROM patient WHERE age_at(birth_date, '2030-01-01') >= 65"  # "How many patients are 65 or older?"


def fixture(directory):
    db = sqlite3.connect(directory / 'semantic.sqlite3')
    db.executescript('''
      CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
      INSERT INTO meta VALUES ('version', 'semantic-layer/test'), ('source_digest', 'abc123');
      CREATE TABLE patient(patient_id TEXT, birth_date TEXT);
      INSERT INTO patient VALUES ('p1','1950-03-01'), ('p2','1980-07-09'), ('p3', NULL);
      CREATE TABLE claim(claim_id TEXT, patient_id TEXT, payer TEXT, service_date TEXT, net_payer_paid_cents INTEGER, payload TEXT);
      INSERT INTO claim VALUES ('c1','p1','Aetna','2029-01-05',1050,'{"status":"paid"}'), ('c2','p1','Aetna','2029-01-20',200,'{"status":"paid"}'),
                               ('c3','p2','Cigna','2029-02-09',425,'{"status":"denied"}'), ('c4','p3','Aetna','2029-02-11',999,'{"status":"paid"}');
      CREATE TABLE measurement(patient_id TEXT, concept TEXT, value REAL, event_date TEXT);
      INSERT INTO measurement VALUES ('p1','a1c',9.4,'2029-03-01'), ('p1','a1c',10.1,'2029-06-01'), ('p2','a1c',7.0,'2029-03-01'),
                                     ('p3','a1c',9.2,'2028-12-30');
    ''')
    db.commit(); db.close()
    return directory / 'semantic.sqlite3'


@unittest.skipIf(de is None, 'duckdb not installed')
class DuckdbEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.sqlite = fixture(self.dir)
        self.built = de.build(self.sqlite)

    def tearDown(self):
        self.tmp.cleanup()

    def test_build_beside_sqlite_with_digest(self):
        self.assertEqual(self.built['tables'], 4)
        self.assertEqual(self.built['source_digest'], 'abc123')
        self.assertEqual(de.current(self.sqlite), self.dir / 'semantic.duckdb')
        db = sqlite3.connect(self.sqlite); db.execute("UPDATE meta SET value='changed' WHERE key='source_digest'"); db.commit(); db.close()
        self.assertIsNone(de.current(self.sqlite))  # stale after the SQLite build changes

    def test_build_never_raises(self):
        out = de.build(self.dir / 'missing.sqlite3')
        self.assertIn('error', out)
        self.assertFalse((self.dir / 'missing.duckdb.tmp').exists())

    def test_engine_switch(self):
        with mock.patch.dict(os.environ, {'AGERE_SQL_ENGINE': 'duckdb'}): self.assertEqual(de.engine(), 'duckdb')
        with mock.patch.dict(os.environ, {'AGERE_SQL_ENGINE': 'bogus'}): self.assertEqual(de.engine(), 'sqlite')

    def test_lint(self):
        self.assertEqual([f['rule'] for f in de.lint("SELECT 1 FROM claim WHERE json_extract(payload,'$.status')='paid'")], ['bare_json_extract'])
        self.assertIn('date_modifier', [f['rule'] for f in de.lint("SELECT date(service_date, '-3 months') FROM claim")])
        self.assertEqual(de.lint(PAID_BY_PAYER_MONTH), [])

    def run_both(self, sql, ids=None, returns='table'):
        p = {'query': sql, 'returns': returns}
        with mock.patch.dict(os.environ, {'AGERE_SQL_ENGINE': 'sqlite', 'AGERE_SQL_SHADOW': ''}):
            s = sql_query.execute(None, p, ids, target=self.sqlite)
        with mock.patch.dict(os.environ, {'AGERE_SQL_ENGINE': 'duckdb', 'AGERE_SQL_SHADOW': ''}):
            d = sql_query.execute(None, p, ids, target=self.sqlite)
        return s, d

    def test_duckdb_serves_analyst_questions_with_same_answers(self):
        s, d = self.run_both(PAID_BY_PAYER_MONTH)
        self.assertEqual(d['engine']['engine'], 'duckdb')
        self.assertEqual(de.normalise(map(tuple, (r.values() for r in s['rows']))), de.normalise(map(tuple, (r.values() for r in d['rows']))))
        s, d = self.run_both(A1C_OVER_9, returns='count')
        self.assertEqual((s['value'], d['value']), (1, 1))
        s, d = self.run_both(AGE_65, returns='count')  # Python UDF age_at on DuckDB
        self.assertEqual((s['value'], d['value']), (1, 1))
        self.assertEqual(d['engine']['engine'], 'duckdb')

    def test_population_binding(self):
        sql = 'SELECT COUNT(*) FROM claim WHERE patient_id IN (SELECT patient_id FROM population)'
        s, d = self.run_both(sql, ids=['p1', 'p2'], returns='count')
        self.assertEqual((s['value'], d['value']), (3, 3))
        self.assertEqual(d['engine']['engine'], 'duckdb')

    def test_lint_falls_back_to_sqlite_not_silent_zero(self):
        sql = "SELECT COUNT(*) FROM claim WHERE CAST(json_extract(payload,'$.status') AS TEXT) = 'paid'"
        s, d = self.run_both(sql, returns='count')
        self.assertEqual((s['value'], d['value']), (3, 3))  # DuckDB would have returned 0 silently
        self.assertEqual(d['engine']['engine'], 'sqlite')
        self.assertIn('bare_json_extract', d['engine']['fallback'])

    def test_missing_duckdb_falls_back(self):
        (self.dir / 'semantic.duckdb').unlink()
        _, d = self.run_both(A1C_OVER_9, returns='count')
        self.assertEqual((d['value'], d['engine']['engine']), (1, 'sqlite'))

    def test_shadow_logs_and_never_changes_the_answer(self):
        with mock.patch.dict(os.environ, {'AGERE_SQL_ENGINE': 'sqlite', 'AGERE_SQL_SHADOW': 'duckdb'}):
            r = sql_query.execute(None, {'query': PAID_BY_PAYER_MONTH, 'returns': 'table'}, target=self.sqlite)
            self.assertEqual(r['engine']['shadow']['outcome'], 'identical')
            r = sql_query.execute(None, {'query': "SELECT COUNT(*) FROM claim WHERE CAST(json_extract(payload,'$.status') AS TEXT) = 'paid'",
                                         'returns': 'count'}, target=self.sqlite)
            self.assertEqual(r['value'], 3)  # served by SQLite
            self.assertEqual(r['engine']['shadow']['outcome'], 'differs')
            self.assertEqual(r['engine']['shadow']['lint'], ['bare_json_extract'])
        self.assertEqual(de.shadow_summary(self.dir), {'identical': 1, 'differs': 1})

    def test_refuses_writes(self):
        with self.assertRaises(ValueError):
            de.run(self.dir / 'semantic.duckdb', "SELECT 1; DROP TABLE claim")


if __name__ == '__main__':
    unittest.main()
