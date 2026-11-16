"""Hospital operations core: the hospital_visit view and recipes against independent Python references."""
import sqlite3
import unittest
from pathlib import Path

from clinical_core.agere.tiered import governed, proof, recipes, store
from tests.core.needs_data import needs_warehouse


def original():
    return sqlite3.connect(Path(store.DEFAULT_DB).resolve().as_uri() + '?mode=ro', uri=True)


@needs_warehouse
class Hospital(unittest.TestCase):
    def test_ed_thresholds_match_the_reference(self):
        for y in ('2028', '2029'):
            _, cols, rows = recipes.execute('ed_timeliness_thresholds', {'year': y})
            self.assertEqual(rows[0][cols.index('discharged_stayed_over_8_hours')], proof.ref_hv_discharged_over_8h(original(), y)[0][0])
            self.assertEqual(rows[0][cols.index('boarded_over_4_hours')], proof.ref_hv_boarded_over_4h(original(), y)[0][0])

    def test_cohort_outcomes_match_the_reference(self):
        _, cols, rows = recipes.execute('heart_failure_hospitalizations', {'year': '2028'})
        self.assertEqual(rows[0][cols.index('mean_length_of_stay_days')], proof.ref_hv_hf_los(original(), '2028')[0][0])
        _, cols, rows = recipes.execute('sepsis_hospitalizations', {'year': '2029'})
        self.assertEqual(rows[0][cols.index('readmission_rate')], proof.ref_hv_sepsis_readmission_rate(original(), '2029')[0][0])

    def test_raw_timestamps_are_refused_for_ed_times(self):
        self.assertTrue(governed.check('What was the median ED length of stay in 2029?', 'SELECT AVG(julianday(ed_departure) - julianday(ed_arrival)) FROM visit_group'))
        self.assertEqual(governed.check('What was the median ED length of stay in 2029?', recipes.RECIPES['ed_length_of_stay']['sql']), [])

    def test_a_cohort_restricts_hospital_visits(self):
        some = [r[0] for r in store.run("SELECT DISTINCT patient_id FROM visit_group ORDER BY 1 LIMIT 5")[1]]
        self.assertLessEqual(store.run('SELECT COUNT(DISTINCT patient_id) FROM hospital_visit', db=store.connect(some))[1][0][0], 5)

    def test_portable_sql_runs_on_the_warehouse_file(self):
        text = store.portable(recipes.RECIPES['ed_admission_rate']['sql'], None, {'year': '2029'})
        self.assertEqual([list(r) for r in original().execute(text, {'year': '2029'})], recipes.execute('ed_admission_rate', {'year': '2029'})[2])


if __name__ == '__main__':
    unittest.main()
