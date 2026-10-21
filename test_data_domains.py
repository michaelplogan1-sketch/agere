"""Honest 'not available' for data domains the warehouse never received; anything that might exist plans normally."""
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest

from clinical_core.agere.clinical import data_domains as D, request_plan as R, shared_planner as S


class DataDomains(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        with sqlite3.connect(self.path / 'semantic.sqlite3') as db:
            db.executescript('''
            CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO meta VALUES ('version','semantic-layer/fixture'),('as_of','2030-05-27');
            CREATE TABLE patient(patient_id TEXT);
            CREATE TABLE patient_survey(patient_id TEXT, satisfaction INTEGER);
            ''')
        self.catalog = R.Catalog.load(self.path)

    def test_absent_domain_is_named(self):
        label, reason = D.check(self.catalog, 'How many prior authorization requests were denied in 2029?')
        self.assertIn('prior authorization', label)
        r = S.compose(SimpleNamespace(path=self.path, allow_live=False), 'How many prior auths were denied?')
        self.assertEqual(r['answer']['status'], 'not_available')
        self.assertIs(r['answer']['answerable'], False)
        self.assertEqual(r['new_paid_api_calls'], 0)

    def test_a_field_that_could_hold_it_falls_through(self):
        self.assertIsNone(D.check(self.catalog, 'What were our patient satisfaction scores?'))  # patient_survey exists

    def test_period_after_the_snapshot(self):
        self.assertIn('runs through 2030-05-27', D.check(self.catalog, 'How many ED visits in June 2031?')[1])
        self.assertIsNone(D.check(self.catalog, 'How many ED visits in May 2030?'))


if __name__ == '__main__':
    unittest.main()
