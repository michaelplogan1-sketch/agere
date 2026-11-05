"""Analysis parameters do not become empirical results or entity evidence."""
import copy
import hashlib
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from clinical_core.agere.clinical import backstop_agent as B, system_one_gate as G


class ProseParameterRoles(unittest.TestCase):
    def setUp(self):
        text = 'Missing durations are unknown; never treat those as 0 days.'
        self.calls = [{
            'name': 'query_sql', 'input': {'sql': 'untrusted input is not the receipt'},
            'result': {
                'native_sql_contract': B.NATIVE_SQL_CONTRACT,
                'result_view': 'result', 'truncated': False,
                'executed_query': 'SELECT SUM(CASE WHEN paid = 1 THEN 1 ELSE 0 END) AS payments, '
                                  'COUNT(*) AS invoices FROM invoice WHERE duration_known = 0',
                'columns': ['payments', 'invoices'], 'rows': [[8, 12]],
                'loaded_documentation': {'identity': 'fixture-contract', 'sections': [{
                    'section_id': 'duration', 'title': 'Missing durations',
                    'documentation_text': text, 'sha256': hashlib.sha256(text.encode()).hexdigest()}]}}}]
        self.question = 'Show 90-day invoice payment rates for calendar year 2029.'

    def compile(self, text, *, calls=None, question=None):
        answer = {'table_binding': {'result_view': 'result'}, 'headline': text}
        B.bind_computed_prose(answer, self.calls if calls is None else calls,
                              question=self.question if question is None else question)
        return answer

    def test_parameter_roles_have_origins_and_leave_original_text_unchanged(self):
        for text, kind in [
            ('90-day payment window; within 90 days.', 'request_duration'),
            ('Definition: invoice.paid = 1 (received flag).', 'executed_predicate'),
            ('Definition: duration_known = 0 (missing value).', 'executed_predicate'),
            ('Rate = payments / invoices x 100, unadjusted.', 'percentage_formula'),
            ('Missing durations are not counted as 0 days.', 'documented_missing_value_convention'),
            ('Observed zero-day durations; missing durations are separate.', 'documented_zero_duration_category'),
            ('Zero-duration categories are included.', 'documented_zero_duration_category'),
            ('Missing duration is NULL (not zero).', 'null_zero_distinction'),
        ]:
            with self.subTest(text=text):
                answer = self.compile(text)
                self.assertEqual(answer['headline'], text)
                proofs = answer['computed_prose'][0]['parameters']
                self.assertEqual({p['kind'] for p in proofs}, {kind})
                self.assertTrue(all(p['literal'] in text for p in proofs))
        for text in ('Invoices in 2029 have unknown durations.', 'December 2029 invoices.',
                     'Invoice requests dated 2029 for the selected accounts.'):
            self.assertEqual(self.compile(text)['headline'], text)

    def test_same_numerals_as_counts_doses_or_other_parameters_still_fail(self):
        for text in ('90 invoices', '90 mg daily', 'zero invoices', 'zero mg daily', 'one invoice per account',
                     'zero-week durations', 'There were 1 payments.',
                     'There were 0 missing durations.', 'The rate was 100 percent.',
                     '30-day payment window.', 'within 90 weeks.',
                     'invoice.paid = 0 (received flag).', 'other.paid = 1 (received flag).',
                     'paid > 1 (received flag).', 'Rate = payments / invoices x 1000.',
                     'Rate = payments / invented x 100.', 'not counted as 0 dollars.',
                     'Missing durations are counted as 0 days.', 'There were 2029 invoices.',
                     'December 2028 invoices.', 'Invoice requests dated 2028.'):
            with self.subTest(text=text), self.assertRaises(B.ComputedProseError):
                self.compile(text)

    def test_native_parameters_need_complete_executed_evidence(self):
        for mutation in ('no_contract', 'error', 'truncated', 'missing_query', 'wrong_query'):
            calls = copy.deepcopy(self.calls)
            result = calls[0]['result']
            if mutation == 'no_contract': result.pop('native_sql_contract')
            elif mutation == 'error': result['error'] = 'query failed'
            elif mutation == 'truncated': result['truncated'] = True
            elif mutation == 'missing_query': result.pop('executed_query')
            else: result['executed_query'] = 'SELECT 1'
            with self.subTest(mutation=mutation), self.assertRaises(B.ComputedProseError):
                self.compile('Definition: invoice.paid = 1 (received flag).', calls=calls)

    def test_no_evidence_is_inferred_from_an_ambiguous_join_or_unexecuted_input(self):
        calls = copy.deepcopy(self.calls)
        calls[0]['result']['executed_query'] = 'SELECT invoices FROM invoice JOIN payment USING (id) WHERE paid = 1'
        with self.assertRaises(B.ComputedProseError):
            self.compile('Definition: invoice.paid = 1 (received flag).', calls=calls)
        calls[0]['input']['sql'] = 'SELECT invoices FROM other WHERE paid = 1'
        with self.assertRaises(B.ComputedProseError):
            self.compile('Definition: other.paid = 1 (received flag).', calls=calls)

    def test_documentation_and_request_origins_are_rechecked(self):
        text = 'Missing durations are not counted as 0 days.'
        calls = copy.deepcopy(self.calls)
        calls[0]['result'].pop('loaded_documentation')
        with self.assertRaises(B.ComputedProseError): self.compile(text, calls=calls)
        with self.assertRaises(B.ComputedProseError): self.compile('zero-day durations', calls=calls)
        calls = copy.deepcopy(self.calls)
        calls[0]['result']['loaded_documentation']['sections'][0]['documentation_text'] += 'changed'
        with self.assertRaisesRegex(ValueError, 'digest changed'): self.compile(text, calls=calls)
        answer = self.compile('90-day payment window.')
        answer['computed_prose'][0]['parameters'][0]['question_sha256'] = 'forged'
        B.bind_computed_prose(answer, self.calls, question=self.question)
        self.assertNotEqual(answer['computed_prose'][0]['parameters'][0]['question_sha256'], 'forged')
        with self.assertRaises(B.ComputedProseError):
            B.bind_computed_prose(answer, self.calls, question='Show 30-day invoice payment rates.')

    def test_parameters_do_not_exempt_entity_level_observations(self):
        calls = copy.deepcopy(self.calls)
        calls[0]['result'].update(columns=['invoice_id', 'payments'], rows=[['A', 8]])
        for text in ('Received within 90 days.', 'invoice.paid = 1 (received flag).', 'zero-day duration.',
                     'Duration is NULL (not zero).',
                     'Missing durations are not counted as 0 days.'):
            answer = {'table_from_tool': 'result', 'row_prose': {'keys': ['invoice_id'], 'rows': [{
                'key': {'invoice_id': 'A'}, 'basis': [{'result_view': 'result', 'row': 0}],
                'fields': {'explanation': text}}]}}
            with self.subTest(text=text), self.assertRaises(B.ComputedProseError):
                B.materialize_table(answer, calls)
                B.bind_computed_prose(answer, calls, question=self.question)

    def test_parameter_acceptance_still_requires_meaning_review(self):
        answer = {'status': 'partial', 'answer_type': 'table', 'table_from_tool': 'result',
                  'headline': '90-day invoice payment rates.'}
        with patch.object(G, 'ready', return_value=True), \
             patch.object(G, 'check', side_effect=ValueError('meaning review reached')) as meaning:
            result = B.review_final(SimpleNamespace(), self.question, answer, self.calls,
                                    SimpleNamespace(read={}), ledger=None, as_of='2030-05-27')
        meaning.assert_called_once()
        self.assertIn('meaning review reached', result['reason'])
        self.assertNotEqual(result['status'], 'confirmed')

    def test_parameter_only_fields_survive_an_unrelated_repair_continuation(self):
        from clinical_core.agere.clinical import execution_state as E
        answer = self.compile('90-day payment window.')
        answer['facts'] = {'total': {'template': '{n} invoices.', 'bindings': {
            'n': {'result_view': 'result', 'row': 0, 'column': 'invoices'}}}}
        B.bind_computed_prose(answer, self.calls, question=self.question)
        state = E.draft_state(answer)['last_draft']
        self.assertEqual(state['headline'], '90-day payment window.')
        self.assertIsInstance(state['facts']['total'], dict)
        B.bind_computed_prose(state, self.calls, question=self.question)
        self.assertEqual(state['headline'], answer['headline'])
        self.assertEqual(state['facts'], answer['facts'])


if __name__ == '__main__': unittest.main()
