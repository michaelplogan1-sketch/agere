"""Two plain readings: recognised by rule, both answered, asked only when the answers differ. No model is called."""
import unittest
from unittest import mock

from clinical_core.agere.tiered import readings, router


def outcome(rows, status='answered'):
    return {'status': status, 'final_tier': 'recipes', 'recipe': {'sql': 'SELECT 1'}, 'columns': ['n'], 'rows': rows,
            'validation': {'ok': True, 'checks': []}, 'steps': [{'tier': 'recipes', 'usd': 0.0}], 'escalations': [], 'seconds': 0.0}


class Detect(unittest.TestCase):
    def test_two_reading_wordings(self):
        for q in ('How many patients were admitted from the ED in 2029?', 'How many patients were readmitted within 30 days in 2028?',
                  'Number of patients discharged before noon in 2029', 'What percent of sepsis patients were readmitted within 30 days?'):
            self.assertIsNotNone(readings.detect(q), q)

    def test_wording_that_settles_the_unit(self):
        for q in ('How many distinct patients were admitted in 2029?', 'How many admissions were there in 2029?',
                  'How many patients had an ED visit in 2029?', 'How many patients were readmitted more than once in 2029?',
                  'How many patients seen by cardiology had a stay?', 'How many patients admitted with sepsis died?', 'How many patients have diabetes?'):
            self.assertIsNone(readings.detect(q), q)

    def test_the_readings_say_their_unit(self):
        d = readings.detect('How many patients were admitted from the ED in 2029?')
        self.assertEqual([r['id'] for r in d['readings']], ['patients', 'events'])
        self.assertIn('each patient once', d['readings'][0]['question'])
        self.assertIn('admissions, not patients', d['readings'][1]['question'])

    def test_denial_rate_is_initial_or_final(self):
        for q in ('What was our denial rate in 2029?', 'Our denial rates by payer for 2029', 'How many of our claims were denied in 2029?'):
            d = readings.detect(q)
            self.assertEqual(d and d['rule'], 'initial_or_final_denials', q)
        for q in ('What was our initial denial rate in 2029?', 'What was the final denial rate in 2029?', 'How many denied claims were overturned in 2029?',
                  'What was the claim denial rate by payer in 2029?'):
            self.assertIsNone(readings.detect(q), q)

    def test_switch(self):
        token = readings.ENABLED.set(False)
        try:
            self.assertIsNone(readings.detect('How many patients were admitted in 2029?'))
        finally:
            readings.ENABLED.reset(token)


class Router(unittest.TestCase):
    Q = 'How many patients were admitted from the ED in 2029?'

    def run_with(self, by_reading):
        def ladder(question, **kw):
            return by_reading['patients' if 'each patient once' in question else 'events']
        with mock.patch.object(router, '_ladder', side_effect=ladder) as m:
            return router.answer(self.Q), m

    def test_different_answers_ask(self):
        o, m = self.run_with({'patients': outcome([[175, '2029']]), 'events': outcome([[261, '2029']])})
        self.assertEqual(m.call_count, 2)
        self.assertEqual(o['status'], 'needs_choice')
        self.assertEqual({r['id']: r['rows'] for r in o['readings']}, {'patients': [[175, '2029']], 'events': [[261, '2029']]})

    def test_same_answer_is_given_once(self):
        o, _ = self.run_with({'patients': outcome([[11, '2029']]), 'events': outcome([[11, 2029]])})
        self.assertEqual(o['status'], 'answered')
        self.assertEqual(o['rows'], [[11, '2029']])
        self.assertTrue(o['readings_checked']['agree'])

    def test_one_reading_unanswered_gives_the_other_and_names_it(self):
        o, _ = self.run_with({'patients': outcome(None, 'unanswered'), 'events': outcome([[261]])})
        self.assertEqual(o['status'], 'answered')
        self.assertEqual(o['readings_checked']['answered_as'], 'Admissions')

    def test_one_reading_questions_climb_once(self):
        with mock.patch.object(router, '_ladder', return_value=outcome([[5]])) as m:
            router.answer('How many admissions were there in 2029?')
        self.assertEqual(m.call_count, 1)


if __name__ == '__main__':
    unittest.main()
