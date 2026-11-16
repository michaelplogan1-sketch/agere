"""Free-parser note facts, event-count thresholds, shares of a group, field averages and monthly attribution."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from clinical_core.agere.clinical import request_plan as R, request_language as L, sql_query


class NoteCountShare(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        with sqlite3.connect(self.path / 'semantic.sqlite3') as db:
            db.executescript('''
            CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO meta VALUES ('version','semantic-layer/fixture'),('as_of','2030-05-27');
            CREATE TABLE patient(patient_id TEXT);
            INSERT INTO patient VALUES ('A'),('B'),('C');
            CREATE TABLE note_fact(patient_id TEXT, fact_type TEXT, value, negated INTEGER, event_date TEXT);
            INSERT INTO note_fact VALUES ('A','smoking_status','current',0,'2029-01-01'),('B','smoking_status','current',1,'2029-01-01'),
              ('A','ejection_fraction',35,0,'2029-01-01'),('B','ejection_fraction',55,0,'2029-01-01'),
              ('C','ejection_fraction','not_quantifiable',0,'2029-01-01'),('C','goals_of_care','dnr',0,'2029-01-01');
            CREATE TABLE utilization_event(patient_id TEXT, kind TEXT, event_date TEXT);
            INSERT INTO utilization_event VALUES ('A','ed_visit','2029-08-01'),('A','ed_visit','2029-09-01'),('A','ed_visit','2029-10-01'),
              ('B','ed_visit','2029-08-01');
            CREATE TABLE social_need(patient_id TEXT, need TEXT, positive INTEGER, event_date TEXT);
            INSERT INTO social_need VALUES ('A','transport',1,'2029-01-01'),('B','transport',0,'2029-01-01'),
              ('C','transport',0,'2029-01-01'),('C','transport',0,'2029-02-01');
            CREATE TABLE appointment(appointment_id TEXT, patient_id TEXT, scheduled_date TEXT, lead_time_days INTEGER, outcome TEXT);
            INSERT INTO appointment VALUES ('1','A','2029-01-01',10,'no_show'),('2','A','2029-02-01',20,'no_show'),('3','B','2029-03-01',30,'kept');
            CREATE TABLE member_month(patient_id TEXT, month TEXT, attribution_practice_id TEXT);
            INSERT INTO member_month VALUES ('A','2030-05','P1'),('B','2030-05',NULL),('C','2030-04','P1');
            ''')
        self.catalog = R.Catalog.load(self.path)

    def run_q(self, q):
        c = R.compile_request(self.catalog, q, L.parse(self.catalog, q, bound=False))
        r = sql_query.execute(self.path, {'query': c['query'], 'returns': c['returns']}, target=self.catalog.target)
        return r.get('value', r.get('rows'))

    def test_note_facts_count_affirmed_mentions(self):
        self.assertEqual(self.run_q('How many patients are documented as current smokers in their notes?'), 1)
        self.assertEqual(self.run_q('How many patients have a DNR documented in their notes?'), 1)
        self.assertEqual(self.run_q('How many patients have a documented ejection fraction below 40% in their notes?'), 1)
        self.assertEqual(self.run_q('How many patients have an EF of 50% or higher documented in notes?'), 1)  # text EF excluded

    def test_event_count_thresholds(self):
        self.assertEqual(self.run_q('How many patients had three or more ED visits in the last 12 months?'), 1)
        self.assertEqual(self.run_q('How many patients have no-showed two or more times?'), 1)

    def test_share_of_a_screened_group_and_averages(self):
        self.assertAlmostEqual(self.run_q('Of patients screened for transportation needs, what percent screened positive?'), 100 / 3)
        self.assertEqual(self.run_q('What was the average lead time in days between booking and the appointment for 2029 appointments?'), 20)

    def test_monthly_attribution(self):
        self.assertEqual(self.run_q('How many members were attributed to a practice in May 2030?'), 1)
        self.assertEqual(self.run_q('How many members were enrolled in May 2030?'), 2)


if __name__ == '__main__':
    unittest.main()
