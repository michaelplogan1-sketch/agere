"""The local model may paraphrase a question; these guards refuse paraphrases that change its meaning."""
import unittest

from clinical_core.agere.clinical import nl2sql
from clinical_core.agere.clinical.analysis_graph import requested_shape
from clinical_core.agere.clinical.economical_planner import rewrite_dropped

AS_OF = '2029-08-15'


class MeaningDrift(unittest.TestCase):
    def drift(self, q, w):
        return nl2sql.meaning_drift(q, w, AS_OF)

    def test_inclusive_threshold_cannot_become_strict(self):
        self.assertIn('at least', self.drift('systolic reading of 160 or higher', 'systolic over 160'))
        self.assertEqual(self.drift('systolic reading of 160 or higher', 'systolic at least 160'), [])

    def test_granularity_and_partial_periods_must_survive(self):
        self.assertIn('granularity', self.drift('Monthly urgent care volume in 2029', 'How many urgent care visits in 2029?'))
        self.assertIn('partial period', self.drift('referrals in the first half of 2029', 'How many referrals in 2029?'))

    def test_snapshot_date_is_not_a_cutoff(self):
        self.assertNotIn('cutoff', self.drift('How many X? As of 2029-08-15.', 'How many X?'))
        self.assertNotIn('cutoff', self.drift('How many X? As of Aug 15, 2029.', 'How many X?'))
        self.assertIn('cutoff', self.drift("As of New Year's Eve 2028, how many claims?", 'How many claims in 2028?'))

    def test_member_id_is_not_a_population(self):
        self.assertNotIn('patients', self.drift('rejections for a bad member ID', 'claim acknowledgements rejected'))


class Coverage(unittest.TestCase):
    def test_stream_named_by_user_must_survive(self):
        before = {'recognised': ['stream medical_claims', 'year']}
        self.assertEqual(rewrite_dropped(before, {'recognised': ['stream patient_payments', 'year']}), ['stream medical_claims'])
        self.assertEqual(rewrite_dropped(before, {'recognised': ['stream medical_claims']}), [])

    def test_enumerated_categories_are_implied_by_group_by(self):
        before = {'recognised': ['group by coverage_type', 'payer medicare', 'payer medicaid']}
        self.assertEqual(rewrite_dropped(before, {'recognised': ['group by coverage_type']}), [])


class Shape(unittest.TestCase):
    def test_requested_shapes(self):
        self.assertEqual(requested_shape('What % of Medicare patients had a visit?'), 'value')
        self.assertEqual(requested_shape('Monthly urgent care volume'), 'table')
        self.assertEqual(requested_shape('Break down ER visits by payer'), 'table')
        self.assertEqual(requested_shape('ED visits per 1,000 patients in 2029'), 'value')
        self.assertEqual(requested_shape('How many hematology referrals?'), 'count')
        self.assertEqual(requested_shape('Which CKD patients have high potassium?'), 'patient_set')


if __name__ == '__main__':
    unittest.main()
