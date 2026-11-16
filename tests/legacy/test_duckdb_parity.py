import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools' / 'duckdb_parity'))

try:
    import duckdb  # noqa: F401
    import replay as R
except ImportError:
    R = None


@unittest.skipIf(R is None, 'duckdb not installed')
class ParityReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db = self.dir / 'semantic.sqlite3'
        con = sqlite3.connect(self.db)
        con.execute('CREATE TABLE record(record_id TEXT, patient_id TEXT, event_date TEXT, payload TEXT)')
        rows = [('r1', 'p1', '2029-01-05', {'claim_status': 'paid', 'paid_amount': 10.5}),
                ('r2', 'p1', '2029-02-07', {'claim_status': 'denied', 'paid_amount': 0}),
                ('r3', 'p2', '2029-02-09', {'claim_status': 'paid', 'paid_amount': 4.25})]
        con.executemany('INSERT INTO record VALUES (?,?,?,?)', [(a, b, c, json.dumps(d)) for a, b, c, d in rows])
        con.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
        con.execute("INSERT INTO meta VALUES ('source_digest', 'abc')")
        con.commit()
        con.close()
        self.mirror, built = R.ensure_mirror(self.db, mirror_dir=self.dir / 'mirrors')
        self.assertEqual(built['tables'], 2)

    def tearDown(self):
        self.tmp.cleanup()

    def run_one(self, sql):
        return R.replay([{'id': 'q', 'sql': sql}], self.db, self.mirror, limit_s=10)[0]

    def test_identical_counts_and_numbers(self):
        r = self.run_one('SELECT patient_id, COUNT(*) AS n, SUM(1.0) AS s FROM record GROUP BY 1 ORDER BY 1;')
        self.assertEqual(r['outcome'], 'identical')

    def test_json_extract_equality_is_a_silent_difference(self):
        # DuckDB json_extract returns '"paid"'; cast to text it counts 0 there and 2 on SQLite, with no error.
        r = self.run_one("SELECT COUNT(*) FROM record WHERE CAST(json_extract(payload,'$.claim_status') AS TEXT) = 'paid'")
        self.assertEqual(r['outcome'], 'differs')
        self.assertEqual(r['detail']['first'], {'sqlite': [2], 'duckdb': [0]})
        r = self.run_one("SELECT COUNT(*) FROM record WHERE substr(json_extract(payload,'$.claim_status'),1,4) = 'paid'")
        self.assertEqual(r['outcome'], 'differs')

    def test_bare_json_extract_equality_raises_on_duckdb(self):
        r = self.run_one("SELECT COUNT(*) FROM record WHERE json_extract(payload,'$.claim_status') = 'paid'")
        self.assertEqual(r['outcome'], 'duckdb_fails')
        self.assertIn('Malformed JSON', r['duckdb_error'])

    def test_sqlite_only_function_fails_on_duckdb(self):
        r = self.run_one("SELECT julianday('2029-02-07') - julianday(event_date) FROM record")
        self.assertEqual(r['outcome'], 'duckdb_fails')
        self.assertIn('julianday', r['duckdb_error'])

    def test_population_statements_are_skipped(self):
        r = self.run_one('SELECT COUNT(*) FROM record WHERE patient_id IN (SELECT patient_id FROM population)')
        self.assertEqual(r['outcome'], 'skipped')
        r = self.run_one("SELECT json_extract(payload, ?) FROM record")
        self.assertEqual((r['outcome'], r['reason']), ('skipped', 'bound parameters not stored'))
        self.assertIsNone(R.skip_reason("SELECT '?' AS q, CAST(x AS TEXT)::VARCHAR FROM record"))

    def test_writes_are_refused_on_both(self):
        r = self.run_one("WITH x AS (SELECT 1) SELECT * FROM x; DELETE FROM record")
        self.assertEqual(r['outcome'], 'both_fail')

    def test_mirror_is_reused_until_source_changes(self):
        again, built = R.ensure_mirror(self.db, mirror_dir=self.dir / 'mirrors')
        self.assertEqual((again, built), (self.mirror, None))
        con = sqlite3.connect(self.db)
        con.execute("UPDATE meta SET value='def' WHERE key='source_digest'")
        con.commit()
        con.close()
        changed, built = R.ensure_mirror(self.db, mirror_dir=self.dir / 'mirrors')
        self.assertNotEqual(changed, self.mirror)
        self.assertIsNotNone(built)

    def test_summary_gate_counts_differs_and_duckdb_failures(self):
        results = R.replay([{'id': 'a', 'sql': 'SELECT 1'},
                            {'id': 'b', 'sql': "SELECT COUNT(*) FROM record WHERE json_extract(payload,'$.claim_status') LIKE 'paid'"},
                            {'id': 'c', 'sql': "SELECT julianday('2029-01-01')"}], self.db, self.mirror, limit_s=10)
        s = R.summarise(results)
        self.assertEqual((s['counts']['identical'], s['counts']['differs'], s['counts']['duckdb_fails']), (1, 1, 1))
        self.assertEqual(s['gate_failures'], 2)
        self.assertIn('silent', R.markdown({'run_at': 't', 'corpora': {'x': {'sqlite': str(self.db), 'summary': s,
                                                                             'results': results}}}).lower())

    def test_normalise_treats_int_and_float_alike(self):
        self.assertEqual(R.normalise([(1, 2.00001), (None, 'a')]), R.normalise([(None, 'a'), (1.0, 2.0)]))


if __name__ == '__main__':
    unittest.main()
