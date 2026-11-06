"""Native population choices reopen and follow the exact counted patient set."""
import json,unittest
from pathlib import Path
from unittest.mock import Mock
from clinical_core.agere.deliverables import answer_context as A,action_center
from clinical_core.agere.clinical import backstop_agent as B,conversation as C
import test_shared_planner as fixtures

class PopulationContext(unittest.TestCase):
    setUp=fixtures.SharedPlanner.setUp

    def capture_summary(self):
        source=A.source_identity(self.path)
        rows=[['A','x'],['A','x'],['B','y']]
        query="SELECT kind,COUNT(*) source_rows,COUNT(DISTINCT patient_id) patients FROM agere_result_1 GROUP BY kind ORDER BY kind"
        producer={'name':'execute_analysis','input':{'operation':'authored'},'result':{
            'result_view':'agere_result_1','columns':['patient_id','kind'],'rows':rows}}
        result={'result_view':'agere_result_2','columns':['kind','source_rows','patients'],
            'rows':[['x',2,1],['y',1,1]],'truncated':False,'native_sql_contract':B.NATIVE_SQL_CONTRACT,
            'declared_executed_query':query,'executed_query':query,
            'source_contracts':{'source_digest':source['source_digest'],'as_of':source['as_of']}}
        calls=[producer,{'name':'query_sql','input':{'sql':query},'result':result}]
        answer={'status':'answered','answer_type':'table','headline':'Source groups counted.',
            'table':[dict(zip(result['columns'],r))for r in result['rows']],
            'table_from_tool':'agere_result_2','table_binding':{'result_view':'agere_result_2'},'sources':[]}
        saved=A.capture(self.path,'Count the recorded source groups.',{'answer':answer,
            'investigation':{'tool_calls':calls},'execution_snapshot':source})
        return saved

    def test_prepare_reopen_and_follow_exact_selected_group_without_model(self):
        saved=self.capture_summary();self.assertFalse(saved['result_scope_available'])
        before=Path(A.root(self.path)/saved['id']/'answer.json').read_bytes()
        out=A.prepare_population_scopes(self.path,{'context_id':saved['id']})
        self.assertEqual(out['new_paid_api_calls'],0);self.assertEqual(len(out['population_scopes']),2)
        new=out['action_context'];context=A.load(self.path,new['id'],current=True)
        scope=next(s for s in out['population_scopes']if s['label']=='x')
        selected=A.reference_scope(context,'population:'+scope['id'])
        self.assertEqual(selected['patient_ids'],['A']);self.assertFalse(A.supports_patient_work(selected))
        follow=A.followup(self.path,new['id'],'show me the patient list',scope='population:'+scope['id'])
        self.assertEqual(follow['answer']['patient_ids'],['A']);self.assertEqual(follow['answer']['count'],1)
        child=A.capture(self.path,follow['question'],follow,original_question=context['original_question'])
        loaded=A.load(self.path,child['id'],current=True)
        self.assertEqual(loaded['input_patient_ids'],['A'])
        self.assertEqual(Path(A.root(self.path)/saved['id']/'answer.json').read_bytes(),before)

    def test_unknown_population_and_generic_summary_followup_never_widen(self):
        saved=self.capture_summary();out=A.prepare_population_scopes(self.path,{'context_id':saved['id']})
        context=A.load(self.path,out['action_context']['id'],current=True)
        with self.assertRaises(ValueError):A.reference_scope(context,'population:POP-'+'0'*24)
        from clinical_core.agere.clinical.question_input import QuestionNotUnderstood
        with self.assertRaises(QuestionNotUnderstood):A.followup(self.path,context['id'],'show me the patient list')

    def test_public_inspection_has_choices_without_all_private_patient_memberships(self):
        saved=self.capture_summary();out=A.prepare_population_scopes(self.path,{'context_id':saved['id']})
        view=action_center.inspection_view(A.inspect(self.path,out['action_context']['id']))['context']
        self.assertEqual(len(view['population_scopes']),2);self.assertIsNone(view['native_population_scopes'])
        self.assertNotIn('patient_ids',view['population_scopes'][0]);self.assertFalse(view['result_scope_available'])

    def test_scopes_with_tampered_membership_or_policy_are_not_available(self):
        saved=self.capture_summary();out=A.prepare_population_scopes(self.path,{'context_id':saved['id']})
        context=A.load(self.path,out['action_context']['id'],current=True)
        context['native_population_scopes']['scopes'][0]['patient_ids']=['B']
        self.assertEqual(len(A.scope_options(context)['population_scopes']),1)
        context['native_population_scopes']['membership_policy']='old-code'
        self.assertEqual(A.scope_options(context)['population_scopes'],[])

    def test_chat_lists_selected_native_population_without_a_planner_and_generic_auto_clarifies(self):
        saved=self.capture_summary();prepared=A.prepare_population_scopes(self.path,{'context_id':saved['id']})
        self.stack.shared_request_planning=True;self.stack.compose=Mock(side_effect=AssertionError('No broader search or model'))
        key=prepared['action_context']['id'];population=prepared['population_scopes'][0]
        from clinical_core.agere.clinical.question_input import QuestionNotUnderstood
        with self.assertRaises(QuestionNotUnderstood):C.handle_compose(self.stack,{'question':'show me the patient list',
            'conversation_id':'population-generic','answer_context_id':key,'answer_scope':'auto'})
        out=C.handle_compose(self.stack,{'question':'show me the patient list','conversation_id':'population-selected',
            'answer_context_id':key,'answer_scope':'population:'+population['id']})
        self.assertEqual(out['answer']['patient_ids'],['A']);self.assertEqual(out['new_paid_api_calls'],0)
        self.assertEqual(out['cohort_transform']['input_patient_ids'],['A']);self.stack.compose.assert_not_called()

    def test_auto_context_keeps_a_complete_new_question_independent(self):
        saved=self.capture_summary();self.stack.shared_request_planning=True
        question='How many patients have a recorded diabetes diagnosis?'
        self.stack.compose=Mock(return_value={'question':question,'answer':{'status':'answered','answer_type':'count','count':0,
            'headline':'No result in the authored routing fixture.','patient_ids':[]},'new_paid_api_calls':0})
        C.handle_compose(self.stack,{'question':question,'conversation_id':'population-new-question',
            'answer_context_id':saved['id'],'answer_scope':'auto'})
        self.assertEqual(self.stack.compose.call_args.args[0],question)
        self.assertIsNone(self.stack.compose.call_args.kwargs.get('population'))

    def test_repeated_prepare_reuses_scopes_and_descendants_keep_population_policy(self):
        from unittest.mock import patch
        from clinical_core.agere.clinical import membership as M
        saved=self.capture_summary();out=A.prepare_population_scopes(self.path,{'context_id':saved['id']})
        root=out['action_context']['id'];scope='population:'+out['population_scopes'][0]['id']
        cached=A.prepare_population_scopes(self.path,{'context_id':root})
        self.assertTrue(cached['cache_hit']);self.assertEqual(cached['native_sql_checks'],0)
        first=A.followup(self.path,root,'show me the patient list',scope=scope)
        child=A.capture(self.path,first['question'],first)
        second=A.followup(self.path,child['id'],'count those patients')
        grandchild=A.capture(self.path,second['question'],second)
        self.assertEqual(A.load(self.path,grandchild['id'],current=True)['cohort_transform']['population_policy'],M.native_population_identity())
        with patch.object(M,'native_population_identity',return_value='changed-population-code'):
            with self.assertRaises(ValueError):A.load(self.path,grandchild['id'],current=True)

if __name__=='__main__':unittest.main()
