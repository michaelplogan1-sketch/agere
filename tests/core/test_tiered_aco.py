"""The ACO job: governed risk-score and contract tables, and the recipes over them, against independent references."""
import sqlite3
import unittest
from pathlib import Path

from clinical_core.agere.tiered import aco, governed, proof, recipes, store


def original():
    return sqlite3.connect(Path(store.DEFAULT_DB).resolve().as_uri() + '?mode=ro', uri=True)


class Aco(unittest.TestCase):
    def test_every_aco_member_is_scored(self):
        db = store.connect()
        n = store.run("SELECT COUNT(*) FROM risk_score WHERE year = '2029' AND patient_id IN "
                      "(SELECT patient_id FROM member_month WHERE payer_id = 'WH-PAYER-MSSP' AND substr(month, 1, 4) = '2029')", db=db)[1][0][0]
        self.assertEqual(n, proof.ref_aco_members(original(), '2029')[0][0])

    def test_risk_score_matches_the_engine_run_directly(self):
        _, _, rows = recipes.execute('aco_risk_score', {'year': '2029'})
        self.assertEqual(rows[0][1], proof.ref_aco_average_risk(original(), '2029')[0][0])

    def test_shared_savings_follow_the_contract_terms(self):
        for year in ('2027', '2028', '2029'):
            _, cols, rows = recipes.execute('aco_shared_savings', {'year': year})
            self.assertEqual(rows[0][cols.index('shared_savings_dollars')], proof.ref_aco_shared_savings(original(), year)[0][0])
            self.assertEqual(rows[0][cols.index('terms_are_demonstration_assumptions')], 1)

    def test_no_terms_means_no_answer(self):
        self.assertEqual(recipes.execute('aco_shared_savings', {'year': '2030'})[2], [])
        self.assertTrue(recipes.RECIPES['aco_shared_savings'].get('empty'))

    def test_pmpm_is_the_aco_population_not_all_medicare(self):
        _, cols, rows = recipes.execute('aco_pmpm', {'year': '2029'})
        self.assertEqual(rows[0][cols.index('actual_pmpm_dollars')], proof.ref_aco_pmpm(original(), '2029')[0][0])
        self.assertTrue(governed.check('What was PMPM for our ACO in 2029?', "SELECT SUM(paid_cents) FROM payer_claim WHERE line_of_business = 'medicare'"))

    def test_wording_reaches_the_recipes(self):
        self.assertEqual(recipes.match('What are our projected MSSP shared savings for 2029?')[0], 'aco_shared_savings')
        self.assertEqual(recipes.match('Which MSSP members drove the most spending in 2029?'), ('aco_spend_drivers', {'year': '2029', 'n': 25}))

    def test_contract_rows_come_from_the_contract_files(self):
        self.assertIn(('demo-mssp-aco', 'WH-PAYER-MSSP', '2029', 1050.0, 0.02, 0.5, 1, 1), aco.contracts())


if __name__ == '__main__':
    unittest.main()
