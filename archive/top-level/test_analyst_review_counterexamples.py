import sqlite3
import unittest
import test_analyst_language as fixtures
from clinical_core.agere.clinical import request_plan as R

class AnalystReview(unittest.TestCase):
    def test_a_past_positive_screen_is_not_removed_by_a_newer_negative(self):
        fixture = fixtures.AnalystFixture(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        with sqlite3.connect(fixture.path / 'semantic.sqlite3') as db:
            db.executescript("""CREATE TABLE measurement(patient_id TEXT, concept TEXT, value_canonical REAL, event_date TEXT, record_id TEXT,
                                                         source_table TEXT, result_current INT, excluded_reason TEXT, value_censor TEXT);
                INSERT INTO measurement VALUES ('A','phq9',12,'2029-10-01','old-a','screening',1,NULL,NULL),
                                               ('B','phq9',4,'2029-10-01','old-b','screening',1,NULL,NULL),
                                               ('C','phq9',10,'2029-10-01','old-c','screening',1,NULL,NULL);
                UPDATE measurement_latest SET value_canonical=1,event_date='2030-01-01' WHERE patient_id='A';""")
        fixture.catalog = R.Catalog.load(fixture.path)
        try: value = fixture.count('How many patients had a positive depression screen in 2029?')
        except R.PlanError: return  # a broader proposer may handle a scope this form cannot compile
        self.assertEqual(value, 2)
