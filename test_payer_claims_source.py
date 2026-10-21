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
            CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO meta VALUES ('version','semantic-layer/fixture'),('as_of','2030-05-27');
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

    def test_saved_age_analysis_without_age_at_death_retires(self):
        tables = {'death': ['patient_id', 'death_date']}
        old = {'analysis': {'steps': [{'sql': "SELECT COUNT(*) FROM patient WHERE age_at(birth_date, '2030-05-27') >= 65"}]}}
        self.assertTrue(superseded('How many patients 65+?', old, tables))
        self.assertFalse(superseded('How many patients 65+?', old, {}))

    def metric(self, q):
        r = L.parse(self.catalog, q, bound=False)
        return [(a['parameters']['id'], a['parameters']['period']['start']) for a in r['analyses']]

    def test_finance_rates_and_totals_resolve_to_payer_metrics(self):
        for q, ident in [('What was the denial rate in 2029?', 'payer_claim_denial_rate'),
                         ('What was the 2029 denial rate?', 'payer_claim_denial_rate'),
                         ('What share of 2029 medical claims were denied?', 'payer_claim_denial_rate'),
                         ('What share of claims were denied in 2029?', 'payer_claim_denial_rate'),
                         ('Claim denial rate for 2029 dates of service?', 'payer_claim_denial_rate'),
                         ('What was the PMPM in 2029?', 'payer_allowed_medical_pmpm'),
                         ('What was the overall medical PMPM (paid per member per month) for 2029?', 'payer_paid_medical_pmpm'),
                         ("What's our 2029 medical cost per member per month on a paid basis?", 'payer_paid_medical_pmpm'),
                         ('What was the total paid amount on medical claims for services in 2029?', 'payer_medical_paid'),
                         ('What was the first-pass denial rate in 2029?', 'first_pass_denial_rate')]:
            with self.subTest(q=q): self.assertEqual(self.metric(q), [(ident, '2029-01-01')])

    def test_two_year_comparisons(self):
        for q in ('How did total medical paid change from 2028 to 2029?', 'denial rate in 2029 vs 2028',
                  'Compared with 2028, did the 2029 denial rate go up or down?'):
            with self.subTest(q=q):
                got = self.metric(q)
                self.assertEqual([s for _, s in got], ['2028-01-01', '2029-01-01'])
                self.assertEqual(len({i for i, _ in got}), 1)

    def test_scope_the_measure_cannot_apply_still_declines(self):
        with self.assertRaises(R.PlanError):
            L.parse(self.catalog, 'What was the 2029 denial rate for institutional claims only?', bound=False)

if __name__ == '__main__':
    unittest.main()
