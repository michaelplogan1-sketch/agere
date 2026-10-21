"""Medical-claim finance questions use counted payer claims (Mike, 2026-10-21); older provider-claim plans retire."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from clinical_core.agere.clinical import request_plan as R, request_language as L, sql_query
from clinical_core.agere.clinical.clinical_definitions import superseded


class PayerClaims(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        with sqlite3.connect(self.path / 'semantic.sqlite3') as db:
            db.executescript('''
            CREATE TABLE patient(patient_id TEXT);
            CREATE TABLE claim(claim_id TEXT, patient_id TEXT, service_date TEXT, final_status TEXT);
            INSERT INTO claim VALUES ('c1','A','2029-02-01','paid'),('c2','A','2029-02-01','paid'),('c3','B','2029-03-01','denied');
            CREATE TABLE payer_claim(payer_claim_id TEXT, patient_id TEXT, service_from TEXT, final_status TEXT, counts_in_metrics INTEGER);
            INSERT INTO payer_claim VALUES ('p1','A','2029-02-01','paid',1),('p1v0','A','2029-02-01','paid',0),('p2','B','2029-03-01','denied',1);
            ''')
        self.catalog = R.Catalog.load(self.path)

    def count(self, q):
        c = R.compile_request(self.catalog, q, L.parse(self.catalog, q, bound=False))
        return sql_query.execute(self.path, {'query': c['query'], 'returns': c['returns']}, target=self.catalog.target)['value']

    def test_claims_are_counted_payer_claims(self):
        self.assertEqual(self.count('How many claims were there in 2029?'), 2)
        self.assertEqual(self.count('How many medical claims were there in 2029?'), 2)

    def test_provider_claim_plans_retire_unless_asked(self):
        plan = {'analysis': {'steps': [{'sql': 'SELECT COUNT(*) FROM claim WHERE final_status = 1'}]}}
        self.assertTrue(superseded('What share of 2029 medical claims were denied?', plan, self.catalog.tables))
        self.assertFalse(superseded('How many provider claims were denied?', plan, self.catalog.tables))
        self.assertFalse(superseded('Denials?', {'request': {'source': {'table': 'payer_claim'}}}, self.catalog.tables))
        self.assertFalse(superseded('Denials?', plan, {'claim': ['claim_id']}))  # no counted payer claims received


if __name__ == '__main__':
    unittest.main()
