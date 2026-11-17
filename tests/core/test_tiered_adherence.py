"""Adherence questions reach the PDC course recipes (adherence.py); code only."""
import unittest

from clinical_core.agere.tiered import recipes


class Adherence(unittest.TestCase):
    def test_wordings_match(self):
        self.assertEqual(recipes.match('What is our medication adherence in 2029?'), ('adherence_pdc', {'year': '2029'}))
        self.assertEqual(recipes.match('What share of patients on statins were adherent in 2029?'), ('adherence_pdc_class', {'drug_class': 'statin', 'year': '2029'}))
        self.assertEqual(recipes.match('What is our ace inhibitor adherence in 2029?')[1]['drug_class'], 'ace_inhibitor')

    def test_computed_recipes_are_not_shown_to_the_sql_writer_as_examples(self):
        from clinical_core.agere.tiered import models
        self.assertNotIn('adherence_pdc', models.definitions('medication adherence 2029'))
        self.assertNotIn('claims_runout', models.definitions('ibnr as of 2029-06-30'))


if __name__ == '__main__':
    unittest.main()
