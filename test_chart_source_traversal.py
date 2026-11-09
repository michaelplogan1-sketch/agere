"""Source relationships must not become a single structured chart value."""
import unittest
from unittest.mock import patch

from clinical_core.agere.clinical import chart_notes as CN, chart_reader as CR
from test_chart_reader import AS_OF, RICH, Base


class ReferenceCapability(Base):
    def test_relationship_reviews_defer_before_chart_or_model_work(self):
        questions = [
            f"Review {RICH}'s accounts about accepting an appointment and test/report documents explicitly linked to those accounts. Use focused source review first.",
            f'Inspect referenced reports for {RICH} and retain unresolved statements.',
            f'For {RICH}, review records attached to the medication account.',
            f'Follow source references for {RICH}, keeping source dates.',
            f'Review the results linked to the initial record for {RICH}.',
        ]
        with patch.object(CR, 'connect', side_effect=AssertionError('Reduced chart route ran')), \
                patch.object(CR, '_so_choice', side_effect=AssertionError('Unneeded classifier ran')):
            for question in questions:
                with self.subTest(question=question):
                    self.assertTrue(CN.source_traversal_requested(question))
                    self.assertIsNone(CR.tier(self.stack, question))

    def test_explicit_chart_spec_cannot_bypass_the_capability_boundary(self):
        question = f'Review linked reports about an appointment for {RICH}.'
        spec = {'subject': {'patient_id': RICH}, 'question': question,
                'chart': {'intent': 'last_event', 'domain': 'appointment'}}
        with patch.object(CR, 'connect', side_effect=AssertionError('Reduced chart query ran')):
            answer = CR.answer(self.stack, spec, as_of=AS_OF)
        self.assertEqual(answer['status'], 'unsupported')
        self.assertEqual(answer['missing'], ['source_reference_traversal'])
        self.assertEqual(answer['sources'], [])
        self.assertEqual(answer['patient_ids'], [RICH])
        self.assertIsNone(answer['intent'])
        self.assertEqual(spec['question'], question)

    def test_citations_and_ordinary_chart_queries_remain_supported(self):
        for question in [f'When was the latest appointment for {RICH}?',
                         f'List appointments for {RICH} and show source record ids.',
                         f'What was the latest A1c for {RICH}? Cite its record.',
                         f'Do the notes say {RICH} has trouble getting to appointments?']:
            with self.subTest(question=question):
                self.assertFalse(CN.source_traversal_requested(question))
                self.assertIsNotNone(CR.tier(self.stack, question))


if __name__ == '__main__':
    unittest.main()
