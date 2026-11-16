import unittest
from unittest.mock import patch
from clinical_core.agere.clinical import spec_path as S, answer_format as F

class FinalShape(unittest.TestCase):
    def attach(self, row):
        result = {'graph': {'nodes': [{'operation': 'sql'}]}, 'answer': {
            'status': 'partial', 'answer_type': 'table', 'value': None,
            'count': None, 'table': [row], 'headline': '1 rows',
            'limitations': ['Meaning remains unconfirmed.']}}
        interpretation = {'answer_checks': {'annotations': ['Answer shape: reshaped.']}}
        with patch.object(S.spec_readback, 'read_back', return_value='Original reading'):
            S.attach(result, interpretation, {'kind': 'request', 'request': {}}, None,
                     'How many days did the patient report using the inhaler?')
        F.present(result)
        return result['answer']

    def test_zero_survives_final_contract_execution_and_presentation(self):
        a = self.attach({'days_used': 0})
        self.assertEqual((a['answer_type'], a['count'], a['value']), ('count', 0, 0))
        self.assertEqual(a['status'], 'partial')
        self.assertNotEqual(a.get('answerable'), False)
        self.assertIn('0', a['headline'])
        self.assertIn('Meaning remains unconfirmed.', a['limitations'])

    def test_ambiguous_columns_do_not_become_a_guessed_scalar(self):
        a = self.attach({'a': 3, 'b': 9})
        self.assertIsNone(a.get('count'))
        self.assertNotEqual(a.get('status'), 'answered')
