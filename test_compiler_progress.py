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
        # An unbound method note is kept as a labelled limitation (2026-11-13), so only the headline failure remains.
        self.assertEqual(first['progress_state'], second['progress_state'])
        self.assertNotIn('count', a['facts'])
        self.assertTrue(any('numbers not tied to computed values: 4 groups' in l for l in a['limitations']))
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

    def test_source_repair_can_reenter_compiler_without_inheriting_an_old_stall(self):
        calls=copy.deepcopy(self.calls);calls[0]['result']['columns'].append('categories');calls[0]['result']['rows'][0].append(4)
        binding=lambda column:{'result_view':'agere_result_0','row':0,'column':column}
        headline={'template':'{n} people','bindings':{'n':binding('patients')}}
        a=self.candidate(headline);a['facts']={'categories':'four categories'}
        b=copy.deepcopy(a);b['facts']['categories']={'template':'{n} categories','bindings':{'n':binding('categories')}}
        c=copy.deepcopy(b);c['facts']['source_correction']='N1 reports no use in a 14-day interview.'
        d=copy.deepcopy(b);quote='No use on 0 of 14 days.'
        d['facts']['source_correction']="N1 says '"+quote+"' The rationale remains unknown."
        d['sources']=[{'record_id':'N1','date':'2029-01-01','quote':quote}]
        replies=iter([a,b,c,d]);sent=[]
        def send(body):
            sent.append(body);return {'content':[{'type':'text','text':json.dumps(next(replies))}]},0,False
        def review(answer,native):
            check=B.review_final(SimpleNamespace(),'Show the clinic result',answer,native,
                SimpleNamespace(read={'N1':{'text':quote,'date':'2029-01-01'}}),ledger=None,as_of='2030-01-01')
            if check['status']=='repair':return check
            return {'status':'confirmed','feedback':None}if answer.get('sources')else{'status':'repair','feedback':'The source reason needs an attributed correction.'}
        with patch.object(G,'ready',return_value=False):
            out=S.tool_loop('question','system',[],lambda use:None,send=send,model='scripted',max_tools=None,
                            review=review,initial_calls=calls,compact_execution_state=True)
        self.assertEqual(len(sent),4);self.assertNotIn('review_stopped',out)
        self.assertIn(quote,out['answer']['facts']['source_correction'])
        self.assertEqual(out['tool_calls'],calls)

    def test_a_repeated_source_failure_still_stops_after_a_compiler_detour(self):
        assessments=iter([{'status':'repair','feedback':'Same unresolved source dependency.'},
            {'status':'repair','repair_kind':'deterministic_compiler','feedback':'Bind the quantity.','progress_state':{'prose':['unbound_literal']}},
            {'status':'repair','feedback':'Same unresolved source dependency.'}])
        sent=[]
        def send(body):
            sent.append(body);return {'content':[{'type':'text','text':json.dumps(self.candidate('Source review pending.'))}]},0,False
        out=S.tool_loop('question','system',[],lambda use:None,send=send,model='scripted',max_tools=None,
                        review=lambda a,c:next(assessments),initial_calls=self.calls)
        self.assertEqual(len(sent),3);self.assertEqual(out['review_stopped'],'no_progress')

    def test_compiler_churn_still_stops_after_a_new_source_phase(self):
        compiler={'status':'repair','repair_kind':'deterministic_compiler','feedback':'Bind the quantity.',
                  'progress_state':{'prose':['unbound_literal']}}
        assessments=iter([{'status':'repair','feedback':'New source dependency.'},compiler,copy.deepcopy(compiler)])
        sent=[]
        def send(body):
            sent.append(body);return {'content':[{'type':'text','text':json.dumps(self.candidate('Source review pending.'))}]},0,False
        out=S.tool_loop('question','system',[],lambda use:None,send=send,model='scripted',max_tools=None,
                        review=lambda a,c:next(assessments),initial_calls=self.calls)
        self.assertEqual(len(sent),3);self.assertEqual(out['review_stopped'],'no_progress')


if __name__ == '__main__':
    unittest.main()
