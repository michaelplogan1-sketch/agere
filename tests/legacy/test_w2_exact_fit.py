import unittest

from clinical_core.agere.clinical import exact_fit, metric_router as R


class ExactFit(unittest.TestCase):
    def setUp(self):
        self.lib = R.library()

    def fit(self, question, mid, slots=None, node=None):
        return exact_fit.fit(question, mid, self.lib[mid], slots or {}, node or {}, '2030-06-30')

    def test_plain_question_fits(self):
        self.assertTrue(self.fit('ED visits per 1,000 member-years', 'ed_visits_per_1000')['fits'])

    def test_unmodelled_sex_restriction_is_a_misfit(self):
        v = self.fit('ED visits per 1,000 among women', 'ed_visits_per_1000')
        self.assertFalse(v['fits'])
        self.assertIn('sex', v['reasons'][0])

    def test_condition_restriction_not_in_definition_is_a_misfit(self):
        v = self.fit('How many ED visits did diabetic patients have?', 'ed_visits')
        self.assertFalse(v['fits'])
        self.assertTrue(any(c['check'] == 'condition:diabetes' and not c['ok'] for c in v['checks']))

    def test_restriction_carried_by_definition_or_slot_fits(self):
        self.assertTrue(self.fit('diabetics who got a kidney health evaluation', 'kidney_health_evaluation_diabetes')['fits'])
        v = self.fit('women aged 50 and over in the cohort', 'patient_cohort', slots={'population': [{'kind': 'sex', 'value': 'female'}, {'kind': 'age', 'min': 50}]})
        self.assertTrue(v['fits'], v['reasons'])

    def test_window_needs_a_period(self):
        v = self.fit('claim denial rate in 2029', 'payer_claim_denial_rate', node={'period': {'start': '2029-01-01', 'end': '2029-12-31'}})
        self.assertTrue(v['fits'], v['reasons'])

    def test_gate_raises_lookup_error_so_the_ladder_falls_through(self):
        with self.assertRaises(LookupError):
            exact_fit.gate('ED visits per 1,000 among women', 'ed_visits_per_1000', self.lib['ed_visits_per_1000'], {}, {}, '2030-06-30')
        out = exact_fit.gate('ED visits per 1,000', 'ed_visits_per_1000', self.lib['ed_visits_per_1000'], {}, {}, '2030-06-30')
        self.assertTrue(out['fits'])


if __name__ == '__main__':
    unittest.main()
