"""Model-free checks for ED Second Look and Facility Line: the rules, not the data."""
import unittest

from clinical_core.agere.tiered import recipes, second_look


class FacilityLine(unittest.TestCase):
    def test_an_unstable_patient_is_always_an_emergency_transfer(self):
        out = second_look.facility_outcome({'unstable': 'yes', 'change': 'no', 'can_treat': 'yes', 'seen': 'yes'})
        self.assertTrue(out['urgent']); self.assertIn('ambulance', out['do'])

    def test_no_outcome_until_enough_is_known(self):
        self.assertIsNone(second_look.facility_outcome({}))
        self.assertIsNone(second_look.facility_outcome({'unstable': 'no'}))
        self.assertIsNone(second_look.facility_outcome({'unstable': 'no', 'change': 'yes', 'seen': 'yes'}))

    def test_a_treatable_change_stays_and_an_untreatable_one_transfers_with_the_reason(self):
        self.assertIn('Stay at the facility', second_look.facility_outcome({'unstable': 'no', 'change': 'no'})['do'])
        stay = second_look.facility_outcome({'unstable': 'no', 'change': 'yes', 'seen': 'no', 'can_treat': 'yes'})
        self.assertIn('Treat in place', stay['do']); self.assertIn("facility's clinician to assess today", stay['do'])
        self.assertIn('Transfer to the emergency department', second_look.facility_outcome({'unstable': 'no', 'change': 'yes', 'seen': 'yes', 'can_treat': 'no'})['do'])


class Wording(unittest.TestCase):
    def test_the_two_lists_are_recipes_with_a_declared_empty_answer(self):
        self.assertEqual(recipes.match('Who came back to the emergency department on 2029-01-23?')[0], 'ed_return_arrivals')
        self.assertEqual(recipes.match('Which patients are in a facility after discharge on 2029-02-10?')[0], 'facility_census')
        self.assertTrue(recipes.RECIPES['ed_return_arrivals']['empty'] and recipes.RECIPES['facility_census']['empty'])


if __name__ == '__main__':
    unittest.main()
