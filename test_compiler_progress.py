"""Compiler retries follow failed checks and native evidence, not rewording."""
import copy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from clinical_core.agere.clinical import backstop_agent as B, execution_state as E, semantic_analyst as S, system_one_gate as G


class CompilerProgress(unittest.TestCase):
    def setUp(self):
        self.calls = [{'name': 'query_sql', 'input': {'sql': 'SELECT 12 AS patients'},
                       'result': {'result_view': 'agere_result_0', 'columns': ['patients'], 'rows': [[12]], 'truncated': False}}]

    def candidate(self, text):
        return {'status': 'answered', 'answer_type': 'table', 'table_from_tool': 'agere_result_0', 'headline': text}

    def review(self, answer, calls):
        return B.review_final(SimpleNamespace(), 'Show the clinic result', answer, calls, SimpleNamespace(read={}), ledger=None, as_of='2030-01-01')

    def test_spelling_a_numeral_and_renaming_fact_keys_does_not_reset_failure(self):
        a = self.candidate('0 eligible people'); a['facts'] = {'old_name': 'Four categories'}
        b = self.candidate('zero eligible people'); b['facts'] = {'new_name': '4 categories'}
        with patch.object(G, 'check', side_effect=AssertionError('Compiler rejects before inference')):
            first = self.review(a, self.calls); second = self.review(b, self.calls)
        self.assertNotEqual(first['feedback'], second['feedback'])
        self.assertEqual(first['progress_state'], second['progress_state'])
        replies = iter([self.candidate('0 eligible people'), self.candidate('zero eligible people')]); sent = []
        def send(body):
            sent.append(body)
            return {'content': [{'type': 'text', 'text': json.dumps(next(replies))}]}, 0, False
        with patch.object(G, 'check', side_effect=AssertionError('No paid check for compiler failure')):
            out = S.tool_loop('question', 'system', [], lambda use: None, send=send, model='scripted', max_tools=None,
                              review=self.review, initial_calls=self.calls, compact_execution_state=True)
        self.assertEqual(len(sent), 2)
        self.assertEqual(out['review_stopped'], 'no_progress')
        self.assertEqual(out['answer']['verification'], 'unverified')
        self.assertEqual(out['tool_calls'], self.calls)

    def test_reduced_failure_count_and_changed_failure_kind_remain_progress(self):
        a = self.candidate('0 people'); a['facts'] = {'count': '4 groups'}
        b = self.candidate('0 people')
        c = self.candidate({'template': '{n} people', 'bindings': {'n': {'result_view': 'agere_result_99', 'row': 0, 'column': 'patients'}}})
        first = self.review(a, self.calls); second = self.review(b, self.calls); third = self.review(c, self.calls)
        self.assertNotEqual(first['progress_state'], second['progress_state'])
        self.assertNotEqual(second['progress_state'], third['progress_state'])
        self.assertEqual(third['progress_state']['prose'][0]['code'], 'missing_native_result')

    def test_native_scalar_dependencies_count_before_all_prose_compiles(self):
        a = self.candidate('0 categories')
        a['facts'] = {'count': {'template': '{n} people', 'bindings': {'n': {'result_view': 'agere_result_1', 'row': 0, 'column': 'patients'}}}}
        extra = {'name': 'query_sql', 'input': {'sql': 'SELECT COUNT(*) AS patients FROM selected_people'},
                 'result': {'result_view': 'agere_result_1', 'columns': ['patients'], 'rows': [[12]]}}
        changed = copy.deepcopy(extra); changed['result']['rows'] = [[13]]
        self.assertNotEqual(E.progress_evidence([*self.calls, extra], a), E.progress_evidence([*self.calls, changed], a))

    def test_bound_category_constants_still_compile_from_native_cells(self):
        calls = copy.deepcopy(self.calls)
        calls[0]['result']['columns'].append('category_zero'); calls[0]['result']['rows'][0].append(0)
        a = {'table_binding': {'result_view': 'agere_result_0'},
             'headline': {'template': 'Category {label}: {n} people', 'bindings': {
                 'label': {'result_view': 'agere_result_0', 'row': 0, 'column': 'category_zero'},
                 'n': {'result_view': 'agere_result_0', 'row': 0, 'column': 'patients'}}}}
        B.bind_computed_prose(a, calls)
        self.assertEqual(a['headline'], 'Category 0: 12 people')


if __name__ == '__main__':
    unittest.main()
