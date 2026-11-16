"""Read-back: a deterministic plain-English rendering of a question spec (spec_readback.py)."""
import unittest

from clinical_core.agere.clinical import request_language as L, request_plan as R, spec_readback as SB, spec_fill as F
from spec_test_support import Warehouse, AS_OF


class ReadBack(unittest.TestCase):
    def setUp(self):
        self.w = Warehouse()
        self.addCleanup(self.w.close)
        self.addCleanup(lambda: R.register_executor('note_frames.evaluate', None))
        self.catalog = self.w.catalog

    def plan(self, q):
        return L.parse(self.catalog, q)

    def test_population_condition_defaults_and_window_are_named(self):
        q = 'Which patients have heart failure but no SGLT2 inhibitor fill in the last year?'
        text = SB.read_back(self.plan(q), self.catalog, q)
        self.assertIn('Listed living patients', text)
        self.assertIn('a documented heart failure diagnosis (EHR diagnosis or counted payer-claim diagnosis)', text)
        self.assertIn('no SGLT2 inhibitor fill within the last 12 months', text)
        self.assertIn('Defaults applied:', text)
        self.assertIn('living patients (no death record)', text)
        self.assertIn(f'the 12 months ending {AS_OF} (2029-05-28 to {AS_OF})', text)

    def test_it_is_deterministic_and_changes_with_the_meaning(self):
        q = 'How many patients have diabetes?'
        a = SB.read_back(self.plan(q), self.catalog, q)
        self.assertEqual(a, SB.read_back(self.plan(q), self.catalog, q))
        q2 = 'How many patients have diabetes or heart failure?'
        self.assertIn(' or ', SB.read_back(self.plan(q2), self.catalog, q2))
        self.assertNotEqual(a, SB.read_back(self.plan(q2), self.catalog, q2))

    def test_deceased_are_named_when_the_question_asks_for_them(self):
        q = 'How many deceased patients have diabetes?'
        text = SB.read_back(self.plan('How many patients have diabetes'), self.catalog, q)  # the defaults follow the asked question
        self.assertNotIn('Counted living patients', text)
        self.assertIn('deceased patients included because the question asks for them', text)
        self.assertIn('the deceased are included because the question asks for them', text)

    def test_calendar_year_window_and_utilization_event(self):
        q = 'How many patients had an ED visit in 2029?'
        text = SB.read_back(self.plan(q), self.catalog, q)
        self.assertIn('an ED visit', text)
        self.assertIn('calendar year 2029-01-01 to 2029-12-31', text)

    def test_parts_cover_population_measure_each_predicate_and_the_window(self):
        q = 'Which patients have heart failure but no SGLT2 inhibitor fill in the last year?'
        parts = SB.parts(self.plan(q), self.catalog, q)
        kinds = [p['kind'] for p in parts]
        self.assertEqual(kinds[0], 'population')
        self.assertIn('measure', kinds)
        self.assertGreaterEqual(kinds.count('predicate'), 2)
        self.assertIn('structure', kinds)
        window = next(p for p in parts if p['kind'] == 'window')
        self.assertIn('12 months ending 2030-05-27', window['text'])
        self.assertEqual(window['quotes'], ['in the last year'])
        self.assertEqual(len({p['id'] for p in parts}), len(parts))

    def test_note_predicate_names_frame_status_window_combine_and_unknown(self):
        q = 'How many patients with diabetes had metformin stopped because of side effects in the last year?'
        plan = self.plan('How many patients with diabetes')
        plan['where'] = L.combine('and', [plan['where'], {
            'op': 'note', 'frame': 'medication_event', 'concept': {'term': 'metformin', 'quote': 'metformin'}, 'statuses': ['documented'],
            'detail': {'event': 'stopped', 'reason': ['side_effect', 'cost']}, 'window': {'start': '2029-05-28', 'end': AS_OF, 'quote': 'in the last year'},
            'combine': 'latest', 'quote': 'had metformin stopped because of side effects in the last year'}], q)
        text = SB.read_back(plan, self.catalog, q)
        self.assertIn('a medication event "metformin"', text)
        self.assertIn('event: stopped', text)
        self.assertIn('reason: side effect or cost', text)
        self.assertIn('between 2029-05-28 and 2030-05-27', text)
        self.assertIn('the latest passage decides', text)
        self.assertIn('counted as unknown, not as no', text)

    def test_subject_and_note_answer(self):
        spec = {'source': {'table': 'patient', 'quote': 'SYN-A'}, 'select': [{'kind': 'note_answer', 'alias': 'note_answer', 'quote': 'Why'}],
                'where': None, 'group_by': [], 'order_by': [], 'limit': None, 'unresolved': [],
                'subject': {'kind': 'patient', 'patient_id': 'SYN-A', 'quote': 'SYN-A'}}
        text = SB.read_back(spec, self.catalog, 'Why is SYN-A not on a statin?')
        self.assertIn('patient SYN-A (named in the question)', text)
        self.assertIn('the note answer itself', text)
        self.assertNotIn('living patients', text)

    def test_grouping_ordering_and_limit(self):
        q = 'How many patients have diabetes by coverage type?'
        text = SB.read_back(self.plan(q), self.catalog, q)
        self.assertIn('broken down by coverage type', text)

    def test_unresolved_requirements_are_stated_not_hidden(self):
        spec = self.plan('How many patients have diabetes')
        spec['unresolved'] = [{'quote': 'diabetes', 'reason': 'x'}]
        self.assertIn('Not answered: diabetes', SB.read_back(spec, self.catalog, 'How many patients have diabetes'))


if __name__ == '__main__':
    unittest.main()
