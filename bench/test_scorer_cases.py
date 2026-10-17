import unittest

from bench import scorer_cases

OUTCOMES = {'correct', 'incorrect', 'malformed'}


class Fixture(unittest.TestCase):
    def test_cases_are_well_formed(self):
        cases = scorer_cases.load_cases()
        self.assertEqual(len({c['id'] for c in cases}), len(cases))
        for c in cases:
            self.assertLessEqual({'id', 'area', 'title', 'key', 'prediction', 'expected', 'why'}, set(c), c['id'])
            self.assertIn(c['expected'], OUTCOMES, c['id'])
            self.assertIn(c['key']['answer_type'], {'count', 'value', 'patient_set', 'table'}, c['id'])
        self.assertLessEqual({'extra_rows', 'missing_rows', 'empty_set', 'duplicate_keys', 'units', 'malformed',
                              'multi_column'}, {c['area'] for c in cases})

    def test_outcome_mapping(self):
        case = {'id': 'X', 'key': {'answer_type': 'count'}, 'prediction': {}}
        self.assertEqual(scorer_cases.outcome(lambda *a: (True, True, 'answered', None), case), 'correct')
        self.assertEqual(scorer_cases.outcome(lambda *a: (True, False, 'answered', None), case), 'incorrect')
        self.assertEqual(scorer_cases.outcome(lambda *a: (True, False, 'malformed', None), case), 'malformed')
        self.assertTrue(scorer_cases.outcome(lambda *a: [][0], case).startswith('crash: IndexError'))


if __name__ == '__main__':
    unittest.main()
