"""An EMR's Bulk FHIR export read into the semantic layer, and the hospital harm measures over it, against references
computed from the FHIR files themselves."""
import os
import unittest

from clinical_core.agere.tiered import fhir_import, proof, recipes, store

DB = fhir_import.OUT


class Env:
    def __enter__(self):
        self.old = os.environ.get('AGERE_TIERED_DB'); os.environ['AGERE_TIERED_DB'] = str(DB)
    def __exit__(self, *exc):
        if self.old is None: os.environ.pop('AGERE_TIERED_DB', None)
        else: os.environ['AGERE_TIERED_DB'] = self.old


@unittest.skipUnless(DB.exists() and proof.FHIR_EXPORT.exists(), 'FHIR export not built (fhir_synth, then fhir_import)')
class Fhir(unittest.TestCase):
    def test_harm_measures_match_the_fhir_files(self):
        with Env():
            for y in ('2028', '2029'):
                self.assertEqual(recipes.execute('harm_severe_hypoglycemia', {'year': y})[2][0][1], proof.ref_fh_hypo_events(None, y)[0][0])
                self.assertEqual(recipes.execute('harm_opioid_adverse_events', {'year': y})[2][0][1], proof.ref_fh_orae_events(None, y)[0][0])
                self.assertEqual(recipes.execute('harm_acute_kidney_injury', {'year': y})[2][0][1], proof.ref_fh_aki_events(None, y)[0][0])
                self.assertEqual(recipes.execute('harm_pressure_injury', {'year': y})[2][0][1], proof.ref_fh_pressure_injury_events(None, y)[0][0])
                self.assertEqual(recipes.execute('harm_falls_with_injury', {'year': y})[2][0][1], proof.ref_fh_falls_injury_events(None, y)[0][0])
                self.assertEqual(recipes.execute('harm_severe_hyperglycemia', {'year': y})[2][0][2], proof.ref_fh_hyper_days(None, y)[0][0])

    def test_operating_room_doses_are_placed_in_the_operating_room(self):
        with Env():
            n = store.run("SELECT COUNT(*) FROM medication_administration WHERE drug_class = 'opioid_antagonist' AND location_type = 'OR'")[1][0][0]
            self.assertGreater(n, 0)

    def test_readiness_on_the_fhir_source(self):
        with Env():
            status = dict(store.run('SELECT measure_id, status FROM measure_readiness')[1])
            for m in ('HH-HYPO', 'HH-ORAE', 'HH-AKI', 'HH-PI', 'HH-FI'): self.assertEqual(status[m], 'ready', m)
            self.assertEqual(status['CLABSI'], 'not_ready')

    def test_the_semantic_layer_recipes_run_on_it(self):
        with Env():
            self.assertTrue(recipes.execute('ed_boarding', {'year': '2029'})[2])
            self.assertTrue(recipes.execute('readmission_rate', {'year': '2029'})[2])


if __name__ == '__main__':
    unittest.main()
