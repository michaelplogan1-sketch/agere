"""A code pattern the warehouse never uses must not return 0 as an answer (2026-11-17: 'patients with a fall' counted ICD-10
W00-W19, which this warehouse never codes; falls are in the notes). Code only, an in-memory table."""
import sqlite3
import unittest

from clinical_core.agere.tiered import recipes


class AbsentCodes(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE dx_absent_test(patient_id TEXT, icd10 TEXT)')
        self.db.executemany('INSERT INTO dx_absent_test VALUES (?, ?)', [('p1', 'I50.9'), ('p2', 'E11.9')])

    def test_a_pattern_that_matches_nothing_is_refused_and_points_to_the_documents(self):
        got = recipes.unknown_filter_values("SELECT COUNT(*) FROM dx_absent_test WHERE icd10 GLOB 'W[01][0-9]*'", {}, self.db)
        self.assertIn('reading plan', got)
        self.assertIsNotNone(recipes.unknown_filter_values("SELECT COUNT(*) FROM dx_absent_test WHERE icd10 LIKE :code", {'code': 'W1%'}, self.db))

    def test_a_pattern_that_matches_is_left_alone(self):
        self.assertIsNone(recipes.unknown_filter_values("SELECT COUNT(*) FROM dx_absent_test WHERE icd10 LIKE 'I50%'", {}, self.db))
        self.assertIsNone(recipes.unknown_filter_values("SELECT COUNT(*) FROM dx_absent_test WHERE icd10 NOT LIKE 'W%'", {}, self.db))


if __name__ == '__main__':
    unittest.main()
