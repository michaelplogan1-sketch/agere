import unittest
from unittest.mock import MagicMock, patch

from clinical_core.agere.intent_router import classify, handle


class Classify(unittest.TestCase):
    def test_routes(self):
        cases = {
            'Which heart failure patients are not on all four GDMT pillars?': 'question',
            'How many patients have diabetes?': 'question',
            'Which women are overdue for breast cancer screening?': 'question',
            'Create a plan to enroll these patients in the PharmD GDMT program': 'plan',
            'Simulate enrolling our HF patients in the PharmD program': 'simulate',
            'What would happen if we launched ACCESS MSK virtual PT?': 'simulate',
            'Build a board story of our ACCESS MSK launch': 'deliverable',
            'Make me a negotiation simulator for our MSSP contract': 'deliverable',
        }
        for q, want in cases.items():
            self.assertEqual(classify(q), want, q)

    def test_explicit_answer_type_is_a_question(self):
        self.assertEqual(classify('Build a board story', 'count'), 'question')


class Handle(unittest.TestCase):
    def test_dispatch(self):
        ws = MagicMock()
        ws.ask.return_value = {'answer': {'count': 1}}
        ws.simulate.return_value = {'summary_table': []}
        self.assertEqual(handle(ws, 'How many patients have diabetes?')['intent'], 'question')
        out = handle(ws, 'Simulate the virtual PT program for back pain')
        self.assertEqual(out['intent'], 'simulate')
        self.assertEqual(ws.simulate.call_args[0][0], 'virtual_pt')
        with patch('clinical_core.agere.deliverables.compose', return_value={'id': 'DLV-1'}) as comp:
            self.assertEqual(handle(ws, 'Build a board story of our ACCESS MSK launch')['intent'], 'deliverable')
            comp.assert_called_once()


if __name__ == '__main__':
    unittest.main()
