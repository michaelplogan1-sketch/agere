"""Free-parser event sequences: an event within N days after (or before) an anchor event, or the counted row."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from clinical_core.agere.clinical import request_plan as R, request_language as L, sql_query


class SequenceLanguage(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        with sqlite3.connect(self.path / 'semantic.sqlite3') as db:
            db.executescript('''
            CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO meta VALUES ('version','semantic-layer/fixture'),('as_of','2030-05-27');
            CREATE TABLE patient(patient_id TEXT, birth_date TEXT);
            INSERT INTO patient VALUES ('A','1950-01-01'),('B','1950-01-01'),('C','1950-01-01');
            CREATE TABLE stay(patient_id TEXT, admit_date TEXT, discharge_date TEXT, type TEXT, discharge_known INTEGER);
            INSERT INTO stay VALUES ('A','2029-03-01','2029-03-05','inpatient',1),('B','2029-06-01','2029-06-03','inpatient',1),
              ('C','2028-01-01','2028-01-04','inpatient',1),('C','2029-12-20','2029-12-28','inpatient',1);
            CREATE TABLE visit(patient_id TEXT, type_concept TEXT, event_date TEXT);
            INSERT INTO visit VALUES ('A','primary_care_visit','2029-03-10'),('B','primary_care_visit','2029-06-03'),
              ('C','primary_care_visit','2028-01-30');
            CREATE TABLE fill(patient_id TEXT, drug_class TEXT, fill_date TEXT, new_user_180 INTEGER);
            INSERT INTO fill VALUES ('A','statin','2029-04-01',1),('B','statin','2029-08-15',1),('C','statin','2028-01-10',0);
            CREATE TABLE death(patient_id TEXT, death_date TEXT);
            INSERT INTO death VALUES ('C','2030-01-10');
            ''')
        self.catalog = R.Catalog.load(self.path)

    def count(self, q):
        c = R.compile_request(self.catalog, q, L.parse(self.catalog, q, bound=False))
        return sql_query.execute(self.path, {'query': c['query'], 'returns': c['returns']}, target=self.catalog.target)['value']

    def test_follow_up_after_each_counted_discharge(self):
        q = 'Of inpatient discharges in 2029, how many had a primary care visit within 7 days after discharge?'
        self.assertEqual(self.count(q), 1)  # A day 5; B same day (day 0) does not count; C's 2029 stay has none
        self.assertEqual(self.count('How many 2029 hospital discharges got a primary care follow-up within a week?'), 1)

    def test_started_a_drug_after_a_discharge(self):
        self.assertEqual(self.count('How many patients started a statin within 30 days after a hospital discharge?'), 1)  # A
        self.assertEqual(self.count('How many patients started a statin within 90 days after a hospital discharge?'), 2)  # A, B

    def test_admission_before_death(self):
        self.assertEqual(self.count('How many patients who died had an inpatient admission in the 30 days before death?'), 1)
        self.assertEqual(self.count('How many patients who died had an inpatient admission in the 10 days before death?'), 0)


if __name__ == '__main__':
    unittest.main()
