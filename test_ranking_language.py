"""Free-parser rankings ('most common', 'top N') and never-filled prescriptions."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from clinical_core.agere.clinical import request_plan as R, request_language as L, sql_query


class RankingLanguage(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        with sqlite3.connect(self.path / 'semantic.sqlite3') as db:
            db.executescript('''
            CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO meta VALUES ('version','semantic-layer/fixture'),('as_of','2030-05-27');
            CREATE TABLE patient(patient_id TEXT);
            CREATE TABLE note_fact(patient_id TEXT, fact_type TEXT, value TEXT, negated INTEGER, event_date TEXT);
            INSERT INTO note_fact VALUES ('A','symptom','cough',0,'2029-01-01'),('B','symptom','cough',0,'2029-02-01'),
              ('A','symptom','fever',0,'2029-01-01'),('B','symptom','fever',1,'2029-01-01'),('C','symptom','fever',1,'2029-01-01');
            CREATE TABLE payer_claim(patient_id TEXT, carc TEXT, final_status TEXT, counts_in_metrics INTEGER, service_from TEXT);
            INSERT INTO payer_claim VALUES ('A','CO-16','denied',1,'2029-01-01'),('B','CO-16','denied_upheld',1,'2029-01-01'),
              ('C','CO-50','denied',1,'2029-01-01'),('C','CO-50','denied',0,'2029-01-01'),('A',NULL,'paid',1,'2029-01-01');
            CREATE TABLE prescription(patient_id TEXT, fill_status TEXT, ordered_date TEXT);
            INSERT INTO prescription VALUES ('A','filled','2029-01-01'),('B','not_filled','2029-01-01'),('C','not_filled','2029-01-01');
            ''')
        self.catalog = R.Catalog.load(self.path)

    def run_q(self, q):
        c = R.compile_request(self.catalog, q, L.parse(self.catalog, q, bound=False))
        r = sql_query.execute(self.path, {'query': c['query'], 'returns': c['returns']}, target=self.catalog.target)
        return r.get('value', r.get('rows'))

    def test_most_common_affirmed_symptom(self):
        self.assertEqual(self.run_q('What is the most frequently documented symptom in notes (affirmed, not negated)?'),
                         [{'value': 'cough', 'count': 2}])  # negated fevers do not count

    def test_top_n_denial_codes_on_counted_claims(self):
        self.assertEqual(self.run_q('What are the top two denial reason codes across all medical claims?'),
                         [{'carc': 'CO-16', 'count': 2}, {'carc': 'CO-50', 'count': 1}])

    def test_written_but_never_filled(self):
        self.assertEqual(self.run_q('How many prescriptions were written but never filled?'), 2)


if __name__ == '__main__':
    unittest.main()
