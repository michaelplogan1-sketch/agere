"""Benchmark reference audit via distinguishing / mutated databases (bench/mutation_audit.py). Tiny fixture databases only.

    .venv/bin/python -m unittest test_mutation_audit
"""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from bench import mutation_audit as M


def fixture(path):
    db = sqlite3.connect(path)
    db.executescript('''CREATE TABLE visit(patient_id TEXT, event_date TEXT, kind TEXT, cost REAL);
                        CREATE TABLE lookup(kind TEXT, label TEXT);''')
    rows = [(f'P{i}', f'2029-0{1 + i % 9}-1{i % 9}', 'ed' if i % 3 else 'office', 100.0 + i) for i in range(30)]
    db.executemany('INSERT INTO visit VALUES (?,?,?,?)', rows)
    db.executemany('INSERT INTO lookup VALUES (?,?)', [('ed', 'Emergency'), ('office', 'Office')])
    db.commit(); db.close()


class MutationAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / 'w.sqlite3'
        fixture(cls.db)

    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def test_equivalent_queries_survive_every_mutant(self):
        r = M.audit(self.db, "SELECT count(*) FROM visit WHERE kind = 'ed'", "SELECT count(1) FROM visit WHERE kind IN ('ed')", budget=60)
        self.assertEqual(r['verdict'], 'equivalent_on_suite')
        self.assertGreater(r['mutants_tried'], 10)
        self.assertEqual(r['tables'], {'visit': ['kind']})

    def test_rows_vs_patients_agree_on_data_but_a_duplicate_separates_them(self):
        r = M.audit(self.db, "SELECT count(DISTINCT patient_id) FROM visit WHERE kind = 'ed'",
                    "SELECT count(*) FROM visit WHERE kind = 'ed'", budget=200, seed=1)
        self.assertTrue(r['original']['equal'])
        self.assertEqual(r['verdict'], 'distinguished')
        self.assertTrue(all(d['mutation'] for d in r['distinguishing']))
        self.assertIn('mutated database separates them', M.summary(r))

    def test_date_window_boundary_is_found(self):
        r = M.audit(self.db, "SELECT patient_id FROM visit WHERE event_date >= '2029-03-01'",
                    "SELECT patient_id FROM visit WHERE event_date > '2029-03-01'", budget=300, seed=2)
        self.assertIn(r['verdict'], ('distinguished', 'differ_on_original'))

    def test_different_results_and_errors(self):
        self.assertEqual(M.audit(self.db, 'SELECT count(*) FROM visit', "SELECT count(*) FROM visit WHERE kind='ed'", budget=5)['verdict'],
                         'differ_on_original')
        self.assertEqual(M.audit(self.db, 'SELECT nope FROM visit', 'SELECT 1', budget=5)['verdict'], 'reference_error')
        self.assertEqual(M.audit(self.db, 'SELECT 1', 'SELECT * FROM missing_table', budget=5)['verdict'], 'candidate_error')

    def test_source_database_is_never_modified(self):
        before = self.db.read_bytes()
        M.audit(self.db, 'SELECT count(*) FROM visit', 'SELECT count(*) FROM visit', budget=40)
        self.assertEqual(before, self.db.read_bytes())

    def test_evidence_for_adjudication_and_errata(self):
        root = Path(self.tmp.name) / 'bench'
        (root / 'sql' / 'heldout').mkdir(parents=True); (root / 'sql_errata' / 'heldout').mkdir(parents=True)
        (root / 'sql' / 'heldout' / 'H01.sql').write_text("SELECT count(*) FROM visit WHERE kind = 'ed'")
        (root / 'sql_errata' / 'heldout' / 'H01__distinct.sql').write_text("SELECT count(DISTINCT patient_id) FROM visit WHERE kind = 'ed'")
        q = {'reference_sql': 'sql/heldout/H01.sql'}
        ev = M.evidence_for(q, "SELECT count(DISTINCT patient_id) FROM visit WHERE kind = 'ed'", self.db, budget=200, root=root)
        self.assertEqual(ev['verdict'], 'distinguished')
        self.assertIsNone(M.evidence_for(q, 'SELECT count(*) FROM other_db_table', self.db, root=root))  # different database
        self.assertIsNone(M.evidence_for({'reference_sql': None}, 'SELECT 1', self.db, root=root))
        out = M.audit_errata(self.db, root=root, budget=200)
        self.assertEqual([(o['id'], o['verdict']) for o in out], [('H01', 'distinguished')])

    def test_adjudicator_attaches_the_evidence(self):
        src = (Path(__file__).resolve().parent / 'bench' / 'adjudicate.py').read_text()
        self.assertIn('from bench.mutation_audit import evidence_for', src)
        self.assertIn('MUTATION AUDIT', src)


if __name__ == '__main__':
    unittest.main()
