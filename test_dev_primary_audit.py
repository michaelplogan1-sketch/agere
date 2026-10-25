import unittest
from tools.dev_set.primary_audit import audit

class PrimaryAudit(unittest.TestCase):
    def question(self, kind, expected):
        return {'split': 'authored', 'answer_type': kind, 'key': {'answer': expected}}

    def test_wrong_primary_date_is_not_rescued_by_the_table(self):
        q = self.question('date', '2029-12-27')
        response = {'answer': {'primary_answer_type': 'date', 'primary_value': '2029-12-21',
                              'table': [{'discharge_date': '2029-12-27'}]}}
        self.assertEqual(audit(q, response)['verdict'], 'wrong')
        response['answer']['primary_value'] = '2029-12-27'
        self.assertEqual(audit(q, response)['verdict'], 'correct')

    def test_untyped_text_and_history_do_not_establish_a_primary_date(self):
        self.assertEqual(audit(self.question('date', '2029-12-27'), {'answer': {
            'answer_type': 'table', 'value': 'Discharged 2029-12-27',
            'table': [{'discharge_date': '2029-12-27'}]}})['verdict'], 'unverified')

    def test_false_boolean_is_not_replaced_by_a_positive_row_count(self):
        self.assertEqual(audit(self.question('yes_no', False), {'answer': {
            'yes_no': False, 'count': 7, 'value': 'No'}})['verdict'], 'correct')
        self.assertEqual(audit(self.question('count', 1), {'answer': {
            'answer_type': 'count', 'value': True}})['verdict'], 'unverified')

    def test_primary_number_not_an_incidental_number_is_compared(self):
        self.assertEqual(audit(self.question('count', 8), {'answer': {
            'answer_type': 'count', 'value': 51, 'table': [{'classes': 8}]}})['verdict'], 'wrong')

    def test_closed_evaluations_are_refused(self):
        q = self.question('date', '2029-12-27'); q['split'] = 'heldout'
        with self.assertRaises(ValueError): audit(q, {})

    def test_conflicting_primary_booleans_are_not_cherry_picked(self):
        self.assertEqual(audit(self.question('yes_no', False), {'answer': {
            'primary_answer_type': 'yes_no', 'primary_value': True, 'yes_no': False}})['verdict'], 'unverified')
