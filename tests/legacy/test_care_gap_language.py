"""Free-parser care gaps: living, age forms, having or lacking received evidence in a window, social-need screens."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from clinical_core.agere.clinical import request_plan as R, request_language as L, sql_query


class CareGapLanguage(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        with sqlite3.connect(self.path / 'semantic.sqlite3') as db:
            db.executescript('''
            CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO meta VALUES ('version','semantic-layer/fixture'),('as_of','2030-05-27');
            CREATE TABLE patient(patient_id TEXT, birth_date TEXT, sex_reported TEXT);
            INSERT INTO patient VALUES ('A','1950-01-01','F'),('B','1955-01-01','M'),('C','1990-01-01','F'),('D','1940-01-01','F');
            CREATE TABLE death(patient_id TEXT, death_date TEXT);
            INSERT INTO death VALUES ('D','2029-01-01');
            CREATE TABLE condition(patient_id TEXT, concept TEXT, event_date TEXT, is_diagnosis INTEGER);
            INSERT INTO condition VALUES ('A','diabetes','2028-01-01',1),('B','diabetes','2028-01-01',1),
              ('C','coronary_artery_disease','2028-01-01',1),('D','diabetes','2028-01-01',1);
            CREATE TABLE measurement(patient_id TEXT, concept TEXT, event_date TEXT);
            INSERT INTO measurement VALUES ('A','hba1c','2030-01-01'),('B','hba1c','2028-01-01'),('A','egfr','2030-02-01');
            CREATE TABLE immunization(patient_id TEXT, vaccine TEXT, status TEXT, event_date TEXT, season TEXT);
            INSERT INTO immunization VALUES ('A','influenza','completed','2029-10-01','2029-2030'),
              ('B','influenza high-dose','completed','2028-10-01','2028-2029');
            CREATE TABLE fill(patient_id TEXT, drug_class TEXT, fill_date TEXT);
            INSERT INTO fill VALUES ('C','statin','2028-01-01');
            CREATE TABLE social_need(patient_id TEXT, need TEXT, positive INTEGER, event_date TEXT);
            INSERT INTO social_need VALUES ('A','food',1,'2029-01-01'),('B','food',0,'2029-01-01'),('C','transport',1,'2029-01-01');
            ''')
        self.catalog = R.Catalog.load(self.path)

    def count(self, q):
        c = R.compile_request(self.catalog, q, L.parse(self.catalog, q, bound=False))
        return sql_query.execute(self.path, {'query': c['query'], 'returns': c['returns']}, target=self.catalog.target)['value']

    def test_gaps_in_a_trailing_window(self):
        self.assertEqual(self.count('How many living patients with diabetes have had no HbA1c test in the last 12 months?'), 1)  # B
        self.assertEqual(self.count('How many living diabetics had neither an eGFR nor an HbA1c in the last 12 months?'), 1)   # B
        self.assertEqual(self.count('How many patients with coronary disease or a prior heart attack had no statin fill in the past year?'), 1)

    def test_age_forms_living_and_seasons(self):
        # a care gap counts living patients unless the question names living or deceased patients (Mike, 2026-10-21)
        self.assertEqual(self.count('How many patients 65 and older did not get a flu shot in the 2029-2030 season?'), 1)  # B
        self.assertEqual(self.count('How many patients 65 and older, living or deceased, did not get a flu shot in the 2029-2030 season?'), 2)
        self.assertEqual(self.count('How many patients 65 and older, living or deceased, have never had a flu shot on record?'), 1)  # D: history, not a gap
        self.assertEqual(self.count('How many patients 65 and older have never had a flu shot on record?'), 0)  # D is deceased: patients are living patients (2026-10-23)
        self.assertEqual(self.count('How many living patients 65+ did not get a flu shot in the 2029-2030 season?'), 1)  # B
        self.assertEqual(self.count('How many women aged 30 to 85 have ever received a flu shot on record?'), 1)  # A
        self.assertEqual(self.count('How many patients with diabetes do not have coronary disease?'), 2)  # A, B: D is deceased (decision context, 2026-10-23)
        self.assertEqual(self.count('How many patients, living or deceased, with diabetes do not have coronary disease?'), 3)

    def test_social_need_screens(self):
        self.assertEqual(self.count('How many patients have screened positive for food insecurity?'), 1)
        self.assertEqual(self.count('How many patients have screened for food?'), 2)

    def test_a_lone_noun_is_not_evidence(self):
        with self.assertRaises(R.PlanError):
            self.count('How many patients HbA1c?')


    def test_deceased_patients_age_at_death(self):
        # D (born 1940) died 2029-01-01 aged 89; living A (1950) is 80 at the 2030-05-27 snapshot (Mike, 2026-10-21)
        # patients are living patients (decision context, 2026-10-23): the deceased are counted, at their age at death, only when asked for
        self.assertEqual(self.count('How many patients, living or deceased, are over 89?'), 0)
        self.assertEqual(self.count('How many patients, living or deceased, are over 88?'), 1)
        self.assertEqual(self.count('How many patients are over 88?'), 0)

if __name__ == '__main__':
    unittest.main()
