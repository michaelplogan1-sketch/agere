"""Free-parser source vocabulary: status adjectives, specialty nouns, money fields, 'both'/'plus', by month."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from clinical_core.agere.clinical import request_plan as R, request_language as L, sql_query


class SourceLanguage(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        with sqlite3.connect(self.path / 'semantic.sqlite3') as db:
            db.executescript('''
            CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO meta VALUES ('version','semantic-layer/fixture'),('as_of','2030-05-27');
            CREATE TABLE patient(patient_id TEXT, pcp_clinician_id TEXT);
            INSERT INTO patient VALUES ('A','C1'),('B',NULL);
            CREATE TABLE condition(patient_id TEXT, concept TEXT, event_date TEXT, is_diagnosis INTEGER);
            INSERT INTO condition VALUES ('A','diabetes','2028-01-01',1),('A','chronic_kidney_disease','2028-01-01',1),('B','diabetes','2028-01-01',1);
            CREATE TABLE clinician(clinician_id TEXT, specialty TEXT);
            INSERT INTO clinician VALUES ('C1','Cardiology'),('C2','Cardiology'),('C3','Nephrology');
            CREATE TABLE appointment(appointment_id TEXT, patient_id TEXT, scheduled_date TEXT, outcome TEXT);
            INSERT INTO appointment VALUES ('1','A','2029-01-05','kept'),('2','A','2029-02-05','cancelled'),('3','B','2029-02-06','kept'),
              ('4','B','2028-02-06','kept');
            CREATE TABLE payer_claim(payer_claim_id TEXT, patient_id TEXT, service_from TEXT, final_status TEXT,
              allowed_cents INTEGER, counts_in_metrics INTEGER);
            INSERT INTO payer_claim VALUES ('p1','A','2029-02-01','paid',12345,1),('p2','B','2029-03-01','paid',1000,0);
            ''')
        self.catalog = R.Catalog.load(self.path)

    def run_q(self, q):
        c = R.compile_request(self.catalog, q, L.parse(self.catalog, q, bound=False))
        r = sql_query.execute(self.path, {'query': c['query'], 'returns': c['returns']}, target=self.catalog.target)
        return r.get('value', r.get('rows'))

    def test_status_words_and_adjectives(self):
        self.assertEqual(self.run_q('How many appointments were cancelled in 2029?'), 1)
        self.assertEqual(self.run_q('How many kept appointments were there in 2029?'), 2)

    def test_specialty_nouns_and_assigned_pcp(self):
        self.assertEqual(self.run_q('How many cardiologists are there?'), 2)
        self.assertEqual(self.run_q('Number of cardiology providers we have?'), 2)
        self.assertEqual(self.run_q('How many patients have an assigned PCP?'), 1)

    def test_money_fields_both_plus_and_by_month(self):
        self.assertEqual(self.run_q('What were total allowed dollars on 2029 medical claims?'), 12345)  # cents; shown as dollars
        self.assertEqual(self.run_q('How many patients have both diabetes and chronic kidney disease?'), 1)
        self.assertEqual(self.run_q('How many of our patients have diabetes plus CKD?'), 1)
        self.assertEqual(self.run_q('Show appointments by month for 2029'), [{'month': '2029-01', 'count': 1}, {'month': '2029-02', 'count': 2}])


if __name__ == '__main__':
    unittest.main()
