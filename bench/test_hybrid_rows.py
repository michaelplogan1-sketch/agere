"""The hybrid core's `rows` answers (clinical workflow task steps): spec, shape, checks, vote."""
import unittest

from bench import hybrid_run
from clinical_core.agere.hybrid import contract as hc, execute as hx, vote as hv

TASK = {'id': 'CT-X', 'title': 'T', 'task': 'Find them.',
        'definitions': {'index': 'Index date = 2028-12-31 (not the release as_of).'},
        'steps': [{'step': 'a', 'definition': 'Eligible.', 'output': {'patient_id': 'str', 'score': 'int'}},
                  {'step': 'b', 'definition': 'Dosed.', 'output': {'patient_id': 'str', 'drug': 'drug name as in the drug list',
                                                                    'dose': 'number', 'missing': 'list of A | B'}}]}


class RowsTest(unittest.TestCase):
    def setUp(self):
        self.a, self.b = hybrid_run.task_steps(TASK)

    def test_steps(self):
        self.assertEqual(self.a['id'], 'CT-X.a')
        self.assertEqual(self.a['as_of'], '2028-12-31')
        self.assertEqual(self.b['spec']['keys'], ['patient_id', 'drug'])
        self.assertEqual(self.b['spec']['columns'], {'patient_id': 'str', 'drug': 'str', 'dose': 'number', 'missing': 'list'})

    def test_contract_keys(self):
        c = {'answer_type': 'rows', 'grain': {'entity': 'person', 'key': 'p'}, 'population': 'x', 'numerator': 'x',
             'window': {'start': None, 'end': '2028-12-31', 'anchor': 'fill.fill_date'}, 'as_of': '2028-12-31',
             'unit': 'count', 'group_by': ['patient_id']}
        q = self.b | {'keys': self.b['spec']['keys']}
        self.assertIn("group_by must be the step's key columns ['patient_id', 'drug']", hc.validate(c, q))
        self.assertEqual(hc.validate(c | {'group_by': ['patient_id', 'drug']}, q), [])

    def test_shape_and_checks(self):
        contract = {'answer_type': 'rows'}
        ans, why = hx.to_answer(contract, ['drug', 'patient_id', 'dose', 'missing'],
                                [('x', 'P-1', 12.5, '["B","A"]')], self.b['spec'])
        self.assertIsNone(why)
        self.assertEqual(ans['rows'][0]['missing'], ['B', 'A'])
        self.assertEqual(hx.checks(contract, ans, None, self.b['spec']), [])
        _, why = hx.to_answer(contract, ['patient_id', 'drug'], [], self.b['spec'])
        self.assertIn('exactly the columns', why)
        dup = {'rows': [{'patient_id': 'P-1', 'score': 2}, {'patient_id': 'P-1', 'score': 3}]}
        self.assertTrue(any('duplicate' in p for p in hx.checks(contract, dup, None, self.a['spec'])))
        frac = {'rows': [{'patient_id': 'P-1', 'score': 2.5}]}
        self.assertIn('score must be an integer', hx.checks(contract, frac, None, self.a['spec']))

    def test_vote_ignores_order(self):
        r1 = {'rows': [{'patient_id': 'P-1', 'missing': ['A', 'B']}, {'patient_id': 'P-2', 'missing': ['A']}]}
        r2 = {'rows': [{'patient_id': 'P-2', 'missing': ['A']}, {'patient_id': 'P-1', 'missing': ['B', 'A']}]}
        self.assertEqual(hv.canonical('rows', r1), hv.canonical('rows', r2))


if __name__ == '__main__':
    unittest.main()
