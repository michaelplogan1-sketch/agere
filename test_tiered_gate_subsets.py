"""Model-free checks for the harder promotion test: two queries must agree inside several subsets of patients."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from clinical_core.agere.tiered import curriculum, store


class Subsets(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        path = Path(self.dir.name) / 's.sqlite3'
        db = sqlite3.connect(path)
        db.executescript("CREATE TABLE meta(key TEXT, value TEXT); INSERT INTO meta VALUES('as_of','2030-05-27'),('version','t'),('source_digest','d');"
                         "CREATE TABLE patient(patient_id TEXT); CREATE TABLE claim(patient_id TEXT, amount INTEGER); CREATE TABLE visit(patient_id TEXT);")
        for i in range(200):
            db.execute('INSERT INTO patient VALUES(?)', (f'P-{i:03d}',))
            db.execute('INSERT INTO claim VALUES(?, ?)', (f'P-{i:03d}', 100))
            if i != 7: db.execute('INSERT INTO visit VALUES(?)', (f'P-{i:03d}',))  # one patient has a claim and no visit
        db.execute("INSERT INTO visit VALUES('P-007X')")                               # ... and one visit belongs to nobody in claim
        db.commit(); db.close()
        self.patches = [mock.patch.object(store, 'path', lambda: path), mock.patch.object(store, 'PATIENT_TABLES', ('patient', 'claim', 'visit')),
                        mock.patch.object(store, 'DOCUMENT_VIEW', 'CREATE TEMP VIEW document AS SELECT patient_id AS record_id, patient_id FROM patient')]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.dir.cleanup()

    def test_subsets_are_repeatable_and_differ_by_seed(self):
        a, b = curriculum.patient_subsets('how many claims'), curriculum.patient_subsets('how many claims')
        self.assertEqual(a, b); self.assertEqual([len(x) for x in a], [100, 100, 20, 4])
        self.assertNotEqual(a[0], a[1]); self.assertNotEqual(a, curriculum.patient_subsets('another question'))

    def test_queries_that_mean_the_same_agree_on_every_subset(self):
        ok, detail = curriculum.agree_on_subsets('SELECT COUNT(*) AS n FROM claim', {}, 'SELECT COUNT(DISTINCT patient_id) AS n FROM claim', {}, 'q')
        self.assertTrue(ok); self.assertEqual(len(detail), 4)

    def test_queries_that_agree_on_the_full_data_by_coincidence_are_caught(self):
        # 200 patients with a claim, 200 visit rows: equal on the full data, different in meaning.
        claims, visits = 'SELECT COUNT(DISTINCT patient_id) AS n FROM claim', 'SELECT COUNT(*) AS n FROM visit'
        db = store.connect()
        self.assertEqual(store.run(claims, {}, db)[1], store.run(visits, {}, db)[1])
        ok = any(not curriculum.agree_on_subsets(claims, {}, visits, {}, seed)[0] for seed in ('a', 'b', 'c', 'd', 'e', 'f'))
        self.assertTrue(ok)  # a subset that holds P-007 separates them

    def test_a_query_that_fails_on_a_subset_counts_as_disagreement(self):
        ok, detail = curriculum.agree_on_subsets('SELECT COUNT(*) AS n FROM claim', {}, 'SELECT COUNT(*) AS n FROM no_such_table', {}, 'q')
        self.assertFalse(ok)


if __name__ == '__main__':
    unittest.main()
