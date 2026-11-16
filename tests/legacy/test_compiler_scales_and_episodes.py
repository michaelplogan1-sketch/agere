"""Typed compiler: admissions count episodes, not source rows; fractional thresholds on 0-100 percentile columns."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from clinical_core.agere.clinical import request_plan as R, sql_query


class ScalesAndEpisodes(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        with sqlite3.connect(self.path / 'semantic.sqlite3') as db:
            db.executescript('''
            CREATE TABLE patient(patient_id TEXT);
            INSERT INTO patient VALUES ('A'),('B'),('C');
            CREATE TABLE stay(patient_id TEXT, admit_date TEXT, type TEXT, stay_source TEXT);
            INSERT INTO stay VALUES ('A','2029-01-02','inpatient','adt'),('A','2029-01-02','inpatient','payer_claim'),
              ('B','2029-03-01','inpatient','adt');
            CREATE TABLE patient_svi(patient_id TEXT, svi_overall REAL);
            INSERT INTO patient_svi VALUES ('A',80.5),('B',40.0),('C',2.1);
            ''')
        self.catalog = R.Catalog.load(self.path)

    def run_plan(self, plan):
        c = R.compile_request(self.catalog, plan['question'], plan['request'])
        return c, sql_query.execute(self.path, {'query': c['query'], 'returns': c['returns']}, target=self.catalog.target)

    def test_admissions_count_episodes(self):
        q = 'How many inpatient stays?'
        c, r = self.run_plan({'question': q, 'request': {
            'source': {'table': 'stay', 'quote': 'inpatient stays'},
            'select': [{'kind': 'count', 'alias': 'stays', 'quote': 'How many'}],
            'where': {'field': 'stay.type', 'op': '=', 'value': 'inpatient', 'quote': 'inpatient'},
            'group_by': [], 'order_by': [], 'limit': None, 'unresolved': []}})
        self.assertEqual(r['value'], 2)

    def test_fractional_threshold_on_percentile_scale(self):
        q = 'How many have svi_overall >= 0.75?'
        c, r = self.run_plan({'question': q, 'request': {
            'source': {'table': 'patient_svi', 'quote': 'have'},
            'select': [{'kind': 'count', 'alias': 'n', 'quote': 'How many'}],
            'where': {'field': 'patient_svi.svi_overall', 'op': '>=', 'value': 0.75, 'quote': 'svi_overall >= 0.75'},
            'group_by': [], 'order_by': [], 'limit': None, 'unresolved': []}})
        self.assertEqual(r['value'], 1)
        self.assertIn('read as 75', c['notes'][0])


if __name__ == '__main__':
    unittest.main()
