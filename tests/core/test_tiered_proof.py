"""Model-free checks for the proof scorecard's scoring rules."""
import unittest

from clinical_core.agere.tiered import proof


class Scoring(unittest.TestCase):
    def test_right_declined_and_wrong(self):
        item = {'reference': [[70]], 'references': [[[70]]], 'unanswerable': False}
        self.assertEqual(proof.outcome_kind(item, {'status': 'answered', 'rows': [[70]]}), 'right')
        self.assertEqual(proof.outcome_kind(item, {'status': 'answered', 'rows': [[72]]}), 'wrong')
        self.assertEqual(proof.outcome_kind(item, {'status': 'unanswered', 'rows': None}), 'declined')
        self.assertEqual(proof.outcome_kind(item, {'status': 'answered', 'rows': [[i] for i in range(70)]}), 'right')   # listed, one row each

    def test_an_unanswerable_question_answered_is_wrong(self):
        item = {'reference': None, 'references': [], 'unanswerable': True}
        self.assertEqual(proof.outcome_kind(item, {'status': 'answered', 'rows': [[1.02]]}), 'wrong')
        self.assertEqual(proof.outcome_kind(item, {'status': 'unanswered', 'rows': None}), 'declined')

    def test_any_audited_reading_counts(self):
        item = {'reference': [[142]], 'references': [[[142]], [[144]]], 'unanswerable': False}
        self.assertEqual(proof.outcome_kind(item, {'status': 'answered', 'rows': [[144]]}), 'right')
        self.assertEqual(proof.outcome_kind(item, {'status': 'answered', 'rows': [[163]]}), 'wrong')


if __name__ == '__main__':
    unittest.main()
