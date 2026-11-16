"""Governed definitions as code checks: each wrong reading seen on a frozen scorecard is rejected; the governed reading passes."""
import unittest

from clinical_core.agere.tiered import governed, recipes, store
from tests.core.needs_data import needs_warehouse


class Checks(unittest.TestCase):
    def wrong(self, q, sql):
        self.assertTrue(governed.check(q, sql), f'should be rejected: {sql}')

    def fine(self, q, sql):
        self.assertEqual(governed.check(q, sql), [], f'should pass: {sql}')

    def test_stays_are_counted_once(self):
        q = 'How many inpatient stays were discharged in 2029?'
        self.wrong(q, "SELECT COUNT(*) FROM stay WHERE type = 'inpatient' AND substr(discharge_date, 1, 4) = :year")
        self.wrong('How many hospital admissions started in 2029?', "SELECT COUNT(*) FROM utilization_event WHERE kind = 'inpatient' AND substr(event_date, 1, 4) = :year")
        self.fine(q, 'SELECT COUNT(*) FROM stay_episode WHERE substr(discharge_date, 1, 4) = :year')
        self.fine('How many patients had an inpatient stay in 2029?', "SELECT COUNT(DISTINCT patient_id) FROM stay WHERE type = 'inpatient'")
        self.fine('How many stays in 2029 have discharge_known set to false?', 'SELECT COUNT(*) FROM stay WHERE discharge_known = 0')
        self.fine('How many observation stays started in 2029?', "SELECT COUNT(*) FROM stay WHERE type = 'observation'")

    def test_latest_values_are_not_every_result(self):
        self.wrong('How many patients had an HbA1c result in 2027?', "SELECT COUNT(DISTINCT patient_id) FROM measurement_latest WHERE concept = 'hba1c'")
        self.fine('What is the latest HbA1c for each patient?', "SELECT patient_id, value_canonical FROM measurement_latest WHERE concept = 'hba1c'")

    @needs_warehouse

    def test_spend_uses_reportable_payer_claims(self):
        self.wrong('What did we spend in total in 2030?', 'SELECT SUM(paid_cents) / 100.0 FROM payer_claim WHERE substr(service_from, 1, 4) = :year')
        self.fine('What did we spend in total in 2030?', 'SELECT SUM(paid_cents) / 100.0 FROM payer_claim WHERE counts_in_metrics = 1')
        self.wrong('How many claims did we have in 2028?', 'SELECT COUNT(*) FROM claim WHERE substr(service_date, 1, 4) = :year')
        self.fine('How many claims were denied in 2028?', 'SELECT COUNT(*) FROM claim WHERE ever_denied = 1')
        self.fine('What was the net payer paid amount in 2028?', 'SELECT SUM(net_payer_paid_cents) FROM claim')

    def test_condition_records_are_the_condition_table(self):
        self.wrong('How many patients have heart failure in their clinical condition records?', "SELECT COUNT(DISTINCT patient_id) FROM concept_patient WHERE concept = 'heart_failure'")
        self.fine('How many patients have heart failure in their clinical condition records?', "SELECT COUNT(DISTINCT patient_id) FROM condition WHERE concept = 'heart_failure'")
        self.wrong('How many patients have heart failure on record from any source?', "SELECT COUNT(DISTINCT patient_id) FROM condition WHERE concept = 'heart_failure'")

    def test_no_shows_are_final_status(self):
        self.wrong('How many no-shows were there in 2029?', "SELECT COUNT(*) FROM appointment WHERE outcome = 'no_show'")
        self.fine('How many no-shows were there in 2029?', "SELECT COUNT(*) FROM appointment WHERE final_status = 'no_show'")
        self.fine('Break down 2028 appointments by outcome.', 'SELECT outcome, COUNT(*) FROM appointment GROUP BY 1')

    def test_flu_shots_count_both_vaccines(self):
        self.wrong('How many patients got a flu shot in 2029?', "SELECT COUNT(DISTINCT patient_id) FROM immunization WHERE vaccine = 'influenza'")
        self.fine('How many patients got a flu shot in 2029?', "SELECT COUNT(DISTINCT patient_id) FROM immunization WHERE vaccine IN ('influenza', 'influenza high-dose')")

    def test_reviewed_recipes_pass_their_own_titles(self):
        flagged = [rid for rid, r in recipes.RECIPES.items() if governed.check(r['title'], r['sql'])]
        self.assertEqual(flagged, ['stays_by_organization'])   # counts stay rows; a model choosing it for a stays question is stopped

    @needs_warehouse

    def test_the_stay_episode_view_matches_the_rate_recipe(self):
        sql = ("SELECT COUNT(*), SUM(readmitted_within_30_days), ROUND(1.0 * SUM(readmitted_within_30_days) / COUNT(*), 4) "
               "FROM stay_episode WHERE substr(discharge_date, 1, 4) = '2029'")
        _, _, rows = recipes.execute('readmission_rate', {'year': '2029'})
        self.assertEqual(store.run(sql)[1], rows)

    def test_switch_off(self):
        token = governed.ENABLED.set(False)
        try:
            self.assertEqual(governed.check('How many no-shows were there?', "SELECT COUNT(*) FROM appointment WHERE outcome = 'no_show'"), [])
        finally:
            governed.ENABLED.reset(token)


if __name__ == '__main__':
    unittest.main()
