"""Recursive native-tool configurations, exact guards and callback substitution; no provider calls."""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere.clinical import harness_improvement as H,semantic_analyst as S


class Executor:
    def __init__(self,root,source='v1',wrong=False):
        self.stack=SimpleNamespace(path=root);self.source=source;self.wrong=wrong;self.executed=[]
        self.db=sqlite3.connect(':memory:');self.semantic=SimpleNamespace(close=lambda:None)
        self.subtool_accounting={'new_paid_api_calls':0,'estimated_cost_usd':0.0}
    def continuation_identity(self):return {'source':self.source,'as_of':'2030-05-27','scope':['A']}
    def __call__(self,use):
        self.executed.append(use)
        return {'columns':['value'],'rows':[[99 if self.wrong else len(self.executed)]],
                'result_view':'agere_result_'+str(len(self.executed))}


class Harness(unittest.TestCase):
    def test_population_comparison_normalizes_only_validated_derivative_identifiers(self):
        import copy
        from clinical_core.agere.clinical.membership import NATIVE_POPULATIONS
        def fixture(elapsed):
            parent={'columns':['patients'],'rows':[[2]],'executed_query':'SELECT COUNT(DISTINCT patient_id) patients FROM patient',
                    'result_view':'agere_result_1','elapsed_s':elapsed}
            proof={'binding':{'result_view':'agere_result_1','row':0,'column':'patients'},'source':{'source_digest':'authored'},
                   'result_digest':H.digest(parent),'source_row_digest':H.digest([2]),'executed_query_digest':H.digest(parent['executed_query']),
                   'patient_ids_digest':H.digest(['A','B']),'patient_count':2,'counted_source_rows':2,'selection_query':'SELECT patient_id FROM patient'}
            scope={'id':'POP-'+H.digest(proof)[:24],'label':'Patients','proof':proof,'patient_ids':['A','B'],'patient_count':2,'counted_source_rows':2}
            result={'schema':NATIVE_POPULATIONS,'source':proof['source'],'scopes':[scope],
                    'rows':[{'population_id':scope['id'],'label':'Patients','patient_count':2,'patient_ids':json.dumps(['A','B'])}]}
            return result,[{'name':'query_sql','result':parent}]
        a,ac=fixture(1);b,bc=fixture(2);before=copy.deepcopy(a)
        self.assertNotEqual(a,b)
        self.assertEqual(H.comparison_result('inspect_populations',a,ac),H.comparison_result('inspect_populations',b,bc))
        self.assertEqual(a,before)
        for mutation in ('members','parent','source','table','id','proof'):
            bad,parents=fixture(1)
            if mutation=='members':bad['scopes'][0]['patient_ids']=['A','C']
            elif mutation=='parent':parents[0]['result']['rows']=[[3]]
            elif mutation=='source':bad['source']={'source_digest':'different'}
            elif mutation=='table':bad['rows'][0]['patient_count']=3
            elif mutation=='id':bad['scopes'][0]['id']='wrong'
            else:bad['scopes'][0]['proof']['result_digest']='wrong'
            with self.subTest(mutation=mutation):
                self.assertEqual(H.comparison_result('inspect_populations',bad,parents).get('population_binding_validation'),'failed')

    def failed_native_checkpoint(self):
        actions,origin,checkpoint=self.recovery_checkpoint()
        failure={'name':'query_sql','input':{'sql':'SELECT * FROM missing_view'},
                 'result':{'error':'no such table: missing_view'},'actor_callback_id':'failed-native-callback'}
        origin['calls'].insert(1,failure)
        return H.recovery_actions(origin['calls']),origin,checkpoint

    def test_draft_recovery_preserves_failed_sql_and_requires_current_review(self):
        import copy
        actions,origin,checkpoint=self.failed_native_checkpoint();before=copy.deepcopy(origin)
        executor=self.executor();attempts=[];original=Executor.__call__
        def execute(e,use):
            attempts.append(use)
            self.assertNotIn('expected_error',use)
            if use['input']['sql']=='SELECT * FROM missing_view':raise sqlite3.OperationalError('no such table: missing_view')
            return original(e,use)
        with patch('clinical_core.agere.deliverables.answer_context.source_identity',return_value=checkpoint['source']):
            captured=H.recovery_checkpoint(executor,self.q,checkpoint['context'],{'answer':checkpoint['draft'],'tool_calls':origin['calls']})
            with H.owned_recovery(actions,self.q,origin=origin,checkpoint=captured),patch.object(Executor,'__call__',execute):
                out=H.recover_actions(executor,self.q,checkpoint['context'])
        self.assertEqual(origin,before);self.assertEqual(len(attempts),3)
        self.assertEqual(out['draft_recovery']['native_receipts_checked'],3)
        self.assertTrue(out['calls'][1]['historical_native_error_reproduced'])
        self.assertEqual(out['calls'][1]['result'],origin['calls'][1]['result'])
        self.assertEqual(out['resume_draft']['verification'],'unverified')
        self.assertTrue(out['draft_recovery']['full_current_review_required']);self.assertIsNone(out['final_answer'])

    def test_failed_sql_draft_withheld_if_error_changes_or_query_now_succeeds(self):
        original=Executor.__call__
        for result in ({'error':'different failure'},{'columns':['value'],'rows':[[2]],'result_view':'agere_result_2'},
                       {'error':'no such table: missing_view','result_view':'agere_result_2'}):
            actions,origin,checkpoint=self.failed_native_checkpoint();e=self.executor();attempts=[]
            def execute(e,use):
                attempts.append(use)
                return result if use['input']['sql']=='SELECT * FROM missing_view'else original(e,use)
            with self.subTest(result=result),patch('clinical_core.agere.deliverables.answer_context.source_identity',return_value=checkpoint['source']),H.owned_recovery(actions,self.q,origin=origin,checkpoint=checkpoint),patch.object(Executor,'__call__',execute):
                out=H.recover_actions(e,self.q,checkpoint['context'])
            self.assertEqual(len(attempts),2);self.assertNotIn('resume_draft',out)
            self.assertEqual(out['draft_recovery']['status'],'withheld')

    def test_checkpoint_cannot_authorize_provider_failures_or_fabricated_expected_error(self):
        for kind in ('provider','changed_error','evidence_on_failure','undeclared_error'):
            actions,origin,checkpoint=self.failed_native_checkpoint()
            if kind=='provider':actions[1]['name']=origin['calls'][1]['name']='review_documents'
            elif kind=='changed_error':actions[1]['expected_error']='invented'
            elif kind=='evidence_on_failure':origin['calls'][1]['result']['rows']=[]
            else:actions[1].pop('expected_error')
            with self.subTest(kind=kind),self.assertRaises(ValueError),H.owned_recovery(actions,self.q,origin=origin,checkpoint=checkpoint):pass

    def test_checkpoint_requires_real_headline_and_fact_dependencies(self):
        for field in ('headline','facts'):
            actions,origin,checkpoint=self.recovery_checkpoint()
            value={'template':'{x}','bindings':{'x':{'result_view':'missing_result','row':0,'column':'value'}}}
            checkpoint['draft'][field]=value if field=='headline'else {'claim':value}
            checkpoint['draft_digest']=H.digest(checkpoint['draft'])
            with self.subTest(field=field),self.assertRaises(ValueError),H.owned_recovery(actions,self.q,origin=origin,checkpoint=checkpoint):pass

    def test_checkpoint_prefers_unfinished_compiler_draft_and_rejects_changed_identity(self):
        import copy
        _,origin,checkpoint=self.recovery_checkpoint();executor=self.executor()
        run={'answer':{**checkpoint['draft'],'headline':'Earlier native table'},'tool_calls':origin['calls'],
             'unfinished_draft':{'draft':checkpoint['draft'],'draft_digest':checkpoint['draft_digest']},
             'answer_reviews':[{'repair_kind':'deterministic_compiler'}]}
        with patch('clinical_core.agere.deliverables.answer_context.source_identity',return_value=checkpoint['source']):
            self.assertEqual(H.recovery_checkpoint(executor,self.q,checkpoint['context'],run),checkpoint)
            for kind in ('digest','review','origin'):
                bad=copy.deepcopy(run)
                if kind=='digest':bad['unfinished_draft']['draft']['headline']='Unbound changed draft'
                elif kind=='review':bad['answer_reviews']=[{'status':'confirmed'}]
                else:bad['tool_calls'][0].pop('actor_callback_id')
                with self.subTest(kind=kind),self.assertRaises(ValueError):
                    H.recovery_checkpoint(executor,self.q,checkpoint['context'],bad)

    def test_compiler_rejected_answered_draft_is_resumable_without_promoting_it(self):
        import copy
        _,origin,checkpoint=self.recovery_checkpoint();executor=self.executor()
        draft={**checkpoint['draft'],'status':'answered','verification':'checked'}
        run={'answer':checkpoint['draft'],'tool_calls':origin['calls'],
             'unfinished_draft':{'draft':draft,'draft_digest':H.digest(draft)},
             'answer_reviews':[{'status':'repair','repair_kind':'deterministic_compiler'}]}
        before=copy.deepcopy(run)
        with patch('clinical_core.agere.deliverables.answer_context.source_identity',return_value=checkpoint['source']):
            captured=H.recovery_checkpoint(executor,self.q,checkpoint['context'],run)
            self.assertEqual(captured['draft'],{**draft,'status':'partial','verification':'unverified'})
            self.assertEqual(captured['draft_digest'],H.digest(captured['draft']))
            self.assertEqual(run,before)
            run.pop('unfinished_draft');run['answer']=draft
            self.assertIsNone(H.recovery_checkpoint(executor,self.q,checkpoint['context'],run))

    def test_checkpoint_capture_keeps_unaccepted_draft_and_rejects_missing_callback_origin(self):
        import copy
        actions,origin,checkpoint=self.recovery_checkpoint();executor=self.executor()
        run={'answer':checkpoint['draft'],'tool_calls':origin['calls'],'answer_reviews':[]}
        with patch('clinical_core.agere.deliverables.answer_context.source_identity',return_value=checkpoint['source']):
            captured=H.recovery_checkpoint(executor,self.q,checkpoint['context'],run)
            self.assertEqual(captured,checkpoint)
            captured['draft']['headline']='Client mutation'
            self.assertNotEqual(captured['draft'],run['answer'])
            bad=copy.deepcopy(run);bad['tool_calls'][0].pop('actor_callback_id')
            with self.assertRaises(ValueError):H.recovery_checkpoint(executor,self.q,checkpoint['context'],bad)
            bad=copy.deepcopy(run);bad['answer']['status']='answered'
            self.assertIsNone(H.recovery_checkpoint(executor,self.q,checkpoint['context'],bad))

    def recovery_checkpoint(self):
        import copy
        _,calls=self.trajectory(2)
        actions=[{k:copy.deepcopy(c[k])for k in ('name','input')}for c in calls]
        draft={'status':'partial','answer_type':'table','table_from_tool':'agere_result_2',
               'headline':'The requested interpretation remains unresolved.'}
        context={**self.context,'question':self.q}
        checkpoint={'source':{'source_digest':'v1'},'context':context,'draft':draft,
                    'draft_digest':H.digest(draft),'review_contexts':[]}
        return actions,{'question':self.q,'calls':calls},checkpoint

    def test_owned_draft_recovery_reexecutes_every_receipt_and_still_requires_current_review(self):
        from clinical_core.agere.clinical import execution_state as E
        actions,origin,checkpoint=self.recovery_checkpoint();executor=self.executor()
        with patch('clinical_core.agere.deliverables.answer_context.source_identity',return_value=checkpoint['source']),H.owned_recovery(
                actions,self.q,origin=origin,checkpoint=checkpoint):
            replay=H.attempt_replay(executor,self.q,checkpoint['context'],self.model)
        self.assertEqual(len(executor.executed),2);self.assertIsNone(replay['final_answer']);self.assertIsNone(replay['used'])
        self.assertEqual(replay['certificates'],[])
        self.assertEqual(replay['draft_recovery']['native_receipts_checked'],2)
        self.assertFalse(replay['draft_recovery']['prior_verdict_accepted'])
        self.assertNotIn('resume_draft',E.recovery_context(replay,executor))
        reviewed=[]
        def review(draft,calls):
            reviewed.append(draft);self.assertEqual(draft['verification'],'unverified')
            self.assertEqual(calls,replay['calls'])
            return {'status':'repair','feedback':'Coverage is undecided.','retry_disposition':'retain_partial_until_new_evidence'}
        forbidden=lambda *a,**k:(_ for _ in ()).throw(AssertionError('No further generation justified'))
        out=S.tool_loop(self.q,'',[],forbidden,send=forbidden,model=self.model,max_tools=None,
                       initial_calls=replay['calls'],initial_answer=replay['resume_draft'],review=review)
        self.assertEqual(len(reviewed),1);self.assertEqual(out['answer']['status'],'partial')
        self.assertNotIn('verification',checkpoint['draft'])

    def test_draft_recovery_rejects_changed_source_context_state_and_missing_origins(self):
        import copy
        actions,origin,checkpoint=self.recovery_checkpoint()
        for kind in ('source','context','draft','origin','workflow'):
            cp=copy.deepcopy(checkpoint);o=copy.deepcopy(origin);executor=self.executor();context=cp['context']
            if kind=='source':cp['source']['source_digest']='stale'
            elif kind=='context':context={**context,'as_of':'2031-01-01'}
            elif kind=='draft':cp['draft']['headline']='Changed after the checkpoint was bound'
            elif kind=='origin':o['calls'][1]=None
            elif kind=='workflow':cp['draft']['workflow']=[{'kind':'monitor'}];cp['draft_digest']=H.digest(cp['draft'])
            with self.subTest(kind=kind),patch('clinical_core.agere.deliverables.answer_context.source_identity',return_value=checkpoint['source']):
                with self.assertRaises(ValueError),H.owned_recovery(actions,self.q,origin=o,checkpoint=cp):
                    H.attempt_replay(executor,self.q,context,self.model)
            self.assertEqual(executor.executed,[])

    def test_changed_native_value_withholds_draft_and_retains_actual_prefix(self):
        actions,origin,checkpoint=self.recovery_checkpoint();executor=self.executor(wrong=True)
        with patch('clinical_core.agere.deliverables.answer_context.source_identity',return_value=checkpoint['source']),H.owned_recovery(
                actions,self.q,origin=origin,checkpoint=checkpoint):
            replay=H.attempt_replay(executor,self.q,checkpoint['context'],self.model)
        self.assertEqual(len(executor.executed),1);self.assertNotIn('resume_draft',replay)
        self.assertEqual(replay['calls'][0]['result']['rows'],[[99]])
        self.assertEqual(replay['draft_recovery']['status'],'withheld')

    def test_historical_operational_review_is_bound_but_cannot_be_answer_evidence(self):
        import copy
        actions,origin,checkpoint=self.recovery_checkpoint()
        review={'support':{'packets':[{'id':'A','label':'unknown','decided_by':None}]},'feedback':'Unverified historical review'}
        name='agere_review_feedback_'+H.digest(review)
        origin['calls'][0]['result'].update(read_views=[name],incomplete_dependencies=[{'authority':'unverified_review_feedback','result_view':name}])
        checkpoint['review_contexts']=[review]
        executor=self.executor();original_execute=Executor.__call__;restored=[]
        def execute(e,use):
            self.assertEqual(restored,[review]);out=original_execute(e,use)
            if len(e.executed)==1:out.update({k:copy.deepcopy(origin['calls'][0]['result'][k])for k in ('read_views','incomplete_dependencies')})
            return out
        def restore(r,t):restored.append(r);return {'feedback_digest':H.digest(r)}
        with patch('clinical_core.agere.deliverables.answer_context.source_identity',return_value=checkpoint['source']),patch(
                'clinical_core.agere.clinical.execution_state.review_reference',side_effect=restore),patch.object(Executor,'__call__',execute),H.owned_recovery(
                actions,self.q,origin=origin,checkpoint=checkpoint):
            replay=H.attempt_replay(executor,self.q,checkpoint['context'],self.model)
        self.assertIn('resume_draft',replay)
        for kind in ('dependent','unbound_review'):
            cp=copy.deepcopy(checkpoint)
            if kind=='dependent':cp['draft']['table_from_tool']='agere_result_1';cp['draft_digest']=H.digest(cp['draft'])
            else:cp['review_contexts'][0]['feedback']='Changed review'
            with self.subTest(kind=kind),self.assertRaises(ValueError),H.owned_recovery(actions,self.q,origin=origin,checkpoint=cp):pass

    def test_persisted_literal_action_preserves_nested_field_order(self):
        def execute(executor,use):
            executor.executed.append(use)
            fields=use['input']['questions']
            return {'columns':['review_json'],'rows':[[json.dumps(fields)]]}
        action={'name':'review_documents','input':{'questions':{
            'z_reason':{'question':'Why?','entity':{'kind':'drug','id':'Z'}},
            'a_reason':{'question':'When?','entity':{'kind':'drug','id':'A'}}}}}
        with patch.object(Executor,'__call__',execute):
            e=self.executor();out=e(action)
            calls=[{**action,'result':out,'actor_callback_id':'original_callback'}]
            candidate=H.propose(e,self.q,self.context,self.model,calls,lambda:self.executor())
            self.assertEqual(candidate['status'],'validated_pending_complete_workflow')
            H.finish(self.root,{'answer':{'status':'answered'},'workflow':[],
                'investigation':{'recursive_improvement':{'candidate':candidate}}})
            replay=H.replay(self.executor(),self.q,self.context,self.model)
        self.assertIsNone(replay['reason']);self.assertEqual(len(replay['certificates']),1)
        self.assertEqual(replay['calls'][0]['result'],out)
        self.assertEqual(list(replay['calls'][0]['input']['questions']),['z_reason','a_reason'])

    def test_recovery_preserves_actual_callback_origin_but_executes_current_result(self):
        _,calls=self.trajectory(2)
        actions=[{k:c[k]for k in ('name','input')}for c in calls]
        e=self.executor(wrong=True)
        with H.owned_recovery(actions,self.q,origin={'question':self.q,'calls':calls}):
            out=H.recover_actions(e,self.q)
        self.assertEqual(len(e.executed),2)
        self.assertEqual([c['actor_callback_id']for c in out['calls']],['callback_1','callback_2'])
        self.assertEqual(out['calls'][0]['result']['rows'],[[99]])
        self.assertFalse(out['calls'][0]['recovery_origin']['result_acceptance_inherited'])
        self.assertFalse(out['calls'][0]['recovery_origin']['new_actor_call'])
        self.assertIsNone(out['used']);self.assertIsNone(out['final_answer'])
        # Old action lineage cannot bypass fresh output validation/selection.
        candidate=H.propose(e,self.q,self.context,self.model,out['calls'],lambda:self.executor())
        self.assertEqual(candidate['status'],'quarantined')

    def test_recovery_origin_rejects_wrong_question_action_position_and_missing_receipt(self):
        import copy
        _,calls=self.trajectory(2);actions=[{k:c[k]for k in ('name','input')}for c in calls]
        for kind in ('question','length','input','callback','result','order'):
            origin={'question':self.q,'calls':copy.deepcopy(calls)}
            if kind=='question':origin['question']='Different population'
            elif kind=='length':origin['calls'].pop()
            elif kind=='input':origin['calls'][0]['input']={'sql':'SELECT 99'}
            elif kind=='callback':origin['calls'][0]['actor_callback_id']=''
            elif kind=='result':origin['calls'][0].pop('result')
            elif kind=='order':origin['calls'].reverse()
            e=self.executor()
            with self.subTest(kind=kind),self.assertRaises(ValueError):
                with H.owned_recovery(actions,self.q,origin=origin):H.recover_actions(e,self.q)
            self.assertEqual(e.executed,[])

    def test_missing_origin_never_invents_callbacks_and_mutated_actions_fail_before_execution(self):
        import copy
        _,calls=self.trajectory(2);actions=[{k:copy.deepcopy(c[k])for k in ('name','input')}for c in calls]
        e=self.executor()
        with H.owned_recovery(actions,self.q,origin={'question':self.q,'calls':[calls[0],None]}):
            out=H.recover_actions(e,self.q)
        self.assertIn('actor_callback_id',out['calls'][0]);self.assertNotIn('actor_callback_id',out['calls'][1])
        e=self.executor()
        with H.owned_recovery(actions,self.q,origin={'question':self.q,'calls':calls}):
            actions[0]['input']['sql']='SELECT 99'
            with self.assertRaisesRegex(ValueError,'changed after'):H.recover_actions(e,self.q)
        self.assertEqual(e.executed,[])

    def supported_partial(self):
        review={'status':'repair','repair_kind':'unresolved_verification',
            'retry_disposition':'retain_partial_until_new_evidence',
            'progress_state':{'coverage':'unknown','unresolved_verification':[]},
            'coverage':{'label':'answers_partially','decided_by':None},
            'support':{'label':'supported','decided_by':'fixture','all_dependencies_checked':True,
                'packets':[{'id':'patient_A','label':'supported','decided_by':'fixture'}]}}
        return {'answer':{'status':'partial','verification':'unverified','missing':['Current assignment unavailable']},
            'investigation':{'review_stopped':'unresolved_coverage','answer_reviews':[review]},'workflow':[]}

    def proposal_run(self):
        result=self.supported_partial()
        result['answer'].update(answer_type='table',table_from_tool='agere_result_2',headline='The current interpretation is unresolved.')
        return result

    def propose_draft(self,executor,calls,result):
        context={**self.context,'question':self.q}
        with patch('clinical_core.agere.deliverables.answer_context.source_identity',return_value={'source_digest':'v1'}):
            return H.propose(executor,self.q,context,self.model,calls,lambda:self.executor(),proposal_result=result)

    def test_shaped_empty_output_membership_does_not_revalidate_identical_native_work(self):
        import copy
        from clinical_core.agere.clinical import backstop_agent as B
        original_execute=Executor.__call__
        def patient_execute(executor,use):
            result=original_execute(executor,use)
            return {**result,'columns':['patient_id','value'],'rows':[['A',result['rows'][0][0]]]}
        with patch.object(Executor,'__call__',patient_execute):
            executor,calls=self.trajectory(2)
            result=self.proposal_run();feedback='Coverage remains unresolved.'
            result['answer'].update(table=[{'patient_id':'A','value':2}],patient_ids=[],limitations=[feedback],
                table_binding={'result_view':'agere_result_2','rows':1,'copied_from_executed_result':True})
            result['investigation']['answer_reviews'][0]['feedback']=feedback
            candidate=self.propose_draft(executor,calls,result)
            result['investigation']['recursive_improvement']={'candidate':candidate};H.finish(self.root,result)
            context={**self.context,'question':self.q};fresh=self.executor()
            replay=H.replay(fresh,self.q,context,self.model)
            reviewed=[]
            def review(answer,actual):
                B.shaped(answer);reviewed.append(copy.deepcopy(answer))
                self.assertEqual(actual,replay['calls'])
                return copy.deepcopy(result['investigation']['answer_reviews'][0])
            forbidden=lambda *a,**k:self.fail('Unchanged checked work must not execute again or call an actor')
            current=S.tool_loop(self.q,'',[],forbidden,send=forbidden,model=self.model,max_tools=None,
                initial_calls=replay['calls'],initial_answer=replay['resume_draft'],review=review)
            self.assertEqual(len(fresh.executed),2);self.assertEqual(len(reviewed),1)
            self.assertNotIn('patient_ids',current['answer'])
            self.assertEqual(current['answer']['table'],result['answer']['table'])
            with patch('clinical_core.agere.deliverables.answer_context.source_identity',return_value={'source_digest':'v1'}):
                self.assertIsNone(H.propose(fresh,self.q,context,self.model,current['tool_calls'],forbidden,proposal_result=current))
            selected=H.latest(self.root,H.identity(fresh,self.q,context,self.model))
            self.assertEqual(selected['id'],candidate['id'])
            self.assertEqual(selected['reviewable_draft']['patient_ids'],[])
            # An explicit empty INPUT population is a distinct guard, not this output placeholder.
            scoped=H.replay(self.executor(),self.q,{**context,'patient_scope':[]},self.model)
            self.assertIsNone(scoped['used']);self.assertEqual(scoped['calls'],[])

    def test_changed_proposal_or_same_length_action_still_requires_fresh_validation(self):
        import copy
        executor,calls=self.trajectory(2);result=self.proposal_run()
        calls[0]['input']['comment']='Retained source meaning'
        candidate=self.propose_draft(executor,calls,result)
        result['investigation']['recursive_improvement']={'candidate':candidate};H.finish(self.root,result)
        context={**self.context,'question':self.q}
        for kind in ('headline','membership','source','action','input_order'):
            changed=copy.deepcopy(result);actions=copy.deepcopy(calls);validated=[]
            if kind=='headline':changed['answer']['headline']='A changed interpretation requires review.'
            elif kind=='membership':changed['answer']['patient_ids']=['B']
            elif kind=='source':changed['answer']['sources']=[{'record_id':'R2','date':'2030-01-01','quote':'New source'}]
            elif kind=='action':actions[0]['input']['sql']='SELECT 99'
            else:actions[0]['input']=dict(reversed(list(actions[0]['input'].items())))
            def fresh():
                check=self.executor();validated.append(check);return check
            with self.subTest(kind=kind),patch('clinical_core.agere.deliverables.answer_context.source_identity',return_value={'source_digest':'v1'}):
                revision=H.propose(executor,self.q,context,self.model,actions,fresh,proposal_result=changed)
                self.assertEqual(revision['status'],'validated_pending_complete_workflow')
                self.assertEqual(len(validated),1);self.assertEqual(len(validated[0].executed),2)

    def test_empty_output_membership_equivalence_preserves_interpreted_table_binding(self):
        import copy
        prose={'schema':'source-bound-row-prose/1','keys':['patient_id'],'rows':[{'key':{'patient_id':'A'},'fields':{'reason':'Source-bound explanation'}}]}
        proposal={'answer_type':'table','patient_ids':[],'table_from_tool':'agere_result_1',
            'table':[{'patient_id':'A','reason':'Source-bound explanation'}],'row_prose':prose,
            'table_binding':{'result_view':'agere_result_1','rows':1,'copied_from_executed_result':False,
                'native_columns_copied':True,'row_prose':{'schema':'source-bound-row-prose/1','proposal_digest':H.digest(prose)}}}
        normalized=H._proposal_without_empty_output_membership(proposal)
        self.assertNotIn('patient_ids',normalized);self.assertEqual(proposal['patient_ids'],[])
        for kind in ('prose','binding','membership','empty_table','answer_type','result_view','rows'):
            changed=copy.deepcopy(proposal)
            if kind=='prose':changed['row_prose']['rows'][0]['fields']['reason']='Different explanation'
            elif kind=='binding':changed['table_binding']['native_columns_copied']=False
            elif kind=='membership':changed['patient_ids']=['B']
            elif kind=='empty_table':changed['table']=[]
            elif kind=='answer_type':changed['answer_type']='patient_set'
            elif kind=='result_view':changed['table_from_tool']='agere_result_2'
            else:changed['table_binding']['rows']=2
            with self.subTest(kind=kind):self.assertEqual(H._proposal_without_empty_output_membership(changed),changed)

    def test_partial_proposal_replays_all_native_work_then_requires_current_review(self):
        import copy
        e,calls=self.trajectory(2);result=self.proposal_run();original=copy.deepcopy(result)
        c=self.propose_draft(e,calls,result);self.assertEqual(c['status'],'validated_pending_complete_workflow')
        result['investigation']['recursive_improvement']={'candidate':c};H.finish(self.root,result)
        context={**self.context,'question':self.q};executor=self.executor();replay=H.replay(executor,self.q,context,self.model)
        self.assertEqual(replay['used'],c['id']);self.assertEqual(len(replay['certificates']),2)
        self.assertEqual(len(executor.executed),2);self.assertIsNone(replay['final_answer'])
        self.assertEqual(replay['resume_draft'],original['answer'])
        self.assertTrue(replay['draft_recovery']['full_current_review_required'])
        self.assertFalse(replay['draft_recovery']['prior_verdict_accepted'])
        reviewed=[]
        def review(answer,actual):
            reviewed.append(copy.deepcopy(answer));self.assertEqual(actual,replay['calls'])
            return copy.deepcopy(original['investigation']['answer_reviews'][-1])
        forbidden=lambda *a,**k:self.fail('No actor justified by unchanged review uncertainty')
        out=S.tool_loop(self.q,'',[],forbidden,send=forbidden,model=self.model,max_tools=None,
            initial_calls=replay['calls'],initial_answer=replay['resume_draft'],review=review)
        self.assertEqual(len(reviewed),1);self.assertEqual(out['answer']['status'],'partial')
        self.assertFalse(out.get('final_decision_reused'))
        with H.database(self.root)as db:
            outcome=json.loads(db.execute("SELECT payload FROM harness_candidate_events WHERE candidate=? AND kind='outcome'",(c['id'],)).fetchone()[0])
        self.assertFalse(outcome['complete_request']);self.assertTrue(outcome['reviewable_proposal_qualified'])
        self.assertFalse(outcome['clinical_qualification']);self.assertFalse(outcome['total_cost_qualified'])
        self.assertIsNone(self.propose_draft(e,calls,original))

    def test_checked_native_prefix_withholds_all_final_prose_and_keeps_current_review_required(self):
        import copy
        e,calls=self.trajectory(2);result=self.proposal_run();context={**self.context,'question':self.q}
        with patch('clinical_core.agere.deliverables.answer_context.source_identity',return_value={'source_digest':'v1'}):
            c=H.propose(e,self.q,context,self.model,calls,lambda:self.executor(),proposal_result=result,retain_proposal=False)
        self.assertEqual(c['status'],'validated_pending_complete_workflow')
        result['investigation']['recursive_improvement']={'candidate':c};H.finish(self.root,result)
        selected=H.latest(self.root,H.identity(e,self.q,context,self.model))
        self.assertEqual(selected['qualification_mode'],'reviewed_partial_prefix')
        self.assertNotIn('reviewable_draft',selected);self.assertIsNone(selected['final_answer'])
        with patch('clinical_core.agere.deliverables.answer_context.source_identity',return_value={'source_digest':'v1'}):
            self.assertIsNone(H.propose(e,self.q,context,self.model,calls,lambda:self.executor(),
                                       proposal_result=result,retain_proposal=False))
        replay=H.replay(self.executor(),self.q,context,self.model)
        self.assertEqual(len(replay['certificates']),2);self.assertNotIn('resume_draft',replay)
        self.assertIsNone(replay['final_answer'])
        # A changed or failed review cannot qualify this checked prefix.
        changed=copy.deepcopy(result);changed['investigation']['answer_reviews'][-1]['support']['label']='contradicted'
        H.finish(self.root,changed)
        self.assertIsNone(H.latest(self.root,H.identity(e,self.q,context,self.model)))
        with self.assertRaises(ValueError):
            H.propose(e,self.q,context,self.model,calls,lambda:self.executor(),retain_proposal=False)

    def test_selected_proposal_does_not_prevent_explicit_native_only_qualification(self):
        e,calls=self.trajectory(2);result=self.proposal_run();c=self.propose_draft(e,calls,result)
        result['investigation']['recursive_improvement']={'candidate':c};H.finish(self.root,result)
        context={**self.context,'question':self.q}
        with patch('clinical_core.agere.deliverables.answer_context.source_identity',return_value={'source_digest':'v1'}):
            native=H.propose(e,self.q,context,self.model,calls,lambda:self.executor(),
                             proposal_result=result,retain_proposal=False)
        self.assertIsNotNone(native)
        result['investigation']['recursive_improvement']={'candidate':native};H.finish(self.root,result)
        selected=H.latest(self.root,H.identity(e,self.q,context,self.model))
        self.assertEqual(selected['qualification_mode'],'reviewed_partial_prefix')
        self.assertNotIn('reviewable_draft',selected)

    def test_proposal_changed_native_or_feedback_withholds_draft(self):
        e,calls=self.trajectory(2);result=self.proposal_run();c=self.propose_draft(e,calls,result)
        result['investigation']['recursive_improvement']={'candidate':c};H.finish(self.root,result)
        context={**self.context,'question':self.q}
        for question,ctx,executor in ((self.q+' changed',context,self.executor()),(self.q,{**context,'as_of':'2031-01-01'},self.executor()),(self.q,context,self.executor(source='v2'))):
            replay=H.replay(executor,question,ctx,self.model)
            self.assertIsNone(replay['used']);self.assertNotIn('resume_draft',replay);self.assertEqual(executor.executed,[])
        changed=H.replay(self.executor(wrong=True),self.q,context,self.model)
        self.assertNotIn('resume_draft',changed);self.assertIsNone(changed['final_answer'])
        self.assertEqual(changed['calls'][0]['result']['rows'],[[99]])
        self.assertIsNone(H.latest(self.root,H.identity(e,self.q,context,self.model)))
        c=self.propose_draft(e,calls,result);result['investigation']['recursive_improvement']={'candidate':c};H.finish(self.root,result)
        H.record_question_correction(self.root,self.q,'The source interpretation needs correction.')
        replay=H.replay(self.executor(),self.q,context,self.model)
        self.assertIsNone(replay['used']);self.assertNotIn('resume_draft',replay)

    def test_proposal_validation_cannot_inherit_owned_recovery_or_incomplete_prefix(self):
        import copy
        e,calls=self.trajectory(2);result=self.proposal_run();original=Executor.__call__;observed=[]
        def execute(executor,use):
            observed.append(H.recovering(self.q));return original(executor,use)
        with H.owned_recovery([{'name':'query_sql','input':{'sql':'SELECT 1'}}],self.q),patch.object(Executor,'__call__',execute):
            c=self.propose_draft(e,calls,result)
            self.assertTrue(H.recovering(self.q))
        self.assertEqual(observed,[False,False]);self.assertEqual(c['status'],'validated_pending_complete_workflow')
        for kind in ('missing_origin','operational','error'):
            changed=copy.deepcopy(calls)
            if kind=='missing_origin':changed[1].pop('actor_callback_id')
            elif kind=='operational':changed[1]['result']['incomplete_dependencies']=[{'authority':'unverified_review_feedback','result_view':'review'}]
            else:changed[1]['result']['error']='Unresolved native failure'
            with self.subTest(kind=kind):self.assertIsNone(self.propose_draft(e,changed,result))

    def test_proposal_rejects_failed_or_incomplete_review_and_downstream_work(self):
        import copy
        result=self.proposal_run()
        for kind in ('source_fault','missing_check','compiler_failure','error','workflow','no_progress'):
            changed=copy.deepcopy(result);inv=changed['investigation'];review=inv['answer_reviews'][-1]
            if kind=='source_fault':review['support']['packets'][0].update(label='contradicted',decided_by='fixture')
            elif kind=='missing_check':review['support']['all_dependencies_checked']=False
            elif kind=='compiler_failure':inv['unaccepted_draft']={'reason':'compiler_repair_required'}
            elif kind=='error':inv['error']='Provider failed'
            elif kind=='workflow':changed['workflow']=[{'kind':'action'}]
            else:inv['review_stopped']='no_progress'
            with self.subTest(kind=kind):self.assertIsNone(H.draft_proposal_evidence(changed))

    def test_joint_source_uncertainty_reuses_only_proposal_and_preserves_unknown(self):
        import copy
        from test_completion_routing import joint_fixture
        from clinical_core.agere.clinical import backstop_agent as B
        packets,results=joint_fixture();result=self.proposal_run()
        review=result['investigation']['answer_reviews'][-1]
        review['support'].update(label='unknown',decided_by=None,packets=results,
            joint_verification_wait=B.joint_dependency_wait(packets,results))
        review.update(review_stop_reason='unresolved_source_support',
            progress_state=B.verification_repair_state(review['coverage'],review['support']))
        result['investigation']['review_stopped']='unresolved_source_support'
        self.assertIsNone(H.partial_prefix_evidence(result))
        self.assertEqual(H.draft_proposal_evidence(result)['unresolved_joint_packets'],['patient_A'])
        e,calls=self.trajectory(2);c=self.propose_draft(e,calls,result)
        result['investigation']['recursive_improvement']={'candidate':c};H.finish(self.root,result)
        replay=H.replay(self.executor(),self.q,{**self.context,'question':self.q},self.model)
        self.assertEqual(replay['used'],c['id']);self.assertIsNone(replay['final_answer'])
        self.assertEqual(replay['resume_draft']['verification'],'unverified')
        for kind in ('unknown_child','missing_joint_certificate','source_fault','global_stop'):
            changed=copy.deepcopy(result);support=changed['investigation']['answer_reviews'][-1]['support']
            if kind=='unknown_child':support['packets'][1].update(label='unknown',decided_by=None)
            elif kind=='missing_joint_certificate':support['joint_verification_wait']=[]
            elif kind=='source_fault':support['failed_packet_ids']=['patient_A']
            else:support['global_stop']=True
            with self.subTest(kind=kind):self.assertIsNone(H.draft_proposal_evidence(changed))

    def test_source_supported_partial_can_select_only_reexecuted_prefix_without_final_answer(self):
        import copy
        e,calls=self.trajectory(2);result=self.supported_partial();original=copy.deepcopy(result)
        c=H.propose(e,self.q,self.context,self.model,calls,lambda:self.executor(),partial_result=result)
        result['investigation']['recursive_improvement']={'candidate':c}
        H.finish(self.root,result)
        replay=H.replay(self.executor(),self.q,self.context,self.model)
        self.assertEqual(replay['used'],c['id']);self.assertEqual(len(replay['certificates']),2)
        self.assertIsNone(replay['final_answer']);self.assertEqual(result['answer'],original['answer'])
        with H.database(self.root)as db:
            outcome=json.loads(db.execute("SELECT payload FROM harness_candidate_events WHERE candidate=? AND kind='outcome'",(c['id'],)).fetchone()[0])
        self.assertFalse(outcome['complete_request']);self.assertTrue(outcome['execution_only_prefix_qualified'])
        self.assertFalse(outcome['clinical_qualification'])
        with self.assertRaisesRegex(ValueError,'no stored final answer'):
            H.propose(e,self.q,self.context,self.model,calls,lambda:self.executor(),partial_result=result,final_answer=result['answer'])
        bad=copy.deepcopy(result);bad['investigation']['answer_reviews'][-1]['support']['packets'][0]['decided_by']=None
        bad['investigation']['recursive_improvement']={'replay':{'used':c['id']}}
        H.finish(self.root,bad)
        self.assertIsNone(H.latest(self.root,H.identity(e,self.q,self.context,self.model)))

    def test_portable_prefix_stops_before_operational_feedback_and_keeps_the_full_trace(self):
        import copy
        e,calls=self.trajectory(3);calls[1]['result']['incomplete_dependencies']=[{
            'result_view':'agere_review_feedback_prior','authority':'unverified_review_feedback',
            'reason':'Operational review state, not answer evidence.'}]
        original=copy.deepcopy(calls);result=self.supported_partial()
        candidate=H.propose(e,self.q,self.context,self.model,calls,lambda:self.executor(),partial_result=result)
        self.assertEqual(candidate['status'],'validated_pending_complete_workflow')
        self.assertEqual(candidate['actions'],1);self.assertEqual(calls,original)
        with H.database(self.root)as db:
            saved=json.loads(db.execute('SELECT payload FROM harness_candidates WHERE id=?',(candidate['id'],)).fetchone()[0])
        self.assertEqual(saved['prefix_boundary']['call_index'],2)
        self.assertEqual(saved['prefix_boundary']['result_digest'],H.digest(calls[1]['result']))
        self.assertIsNone(saved['final_answer'])
        result['investigation']['recursive_improvement']={'candidate':candidate};H.finish(self.root,result)
        replay=H.replay(self.executor(),self.q,self.context,self.model)
        self.assertEqual(len(replay['calls']),1);self.assertIsNone(replay['final_answer'])
        self.assertIsNone(H.propose(e,self.q,self.context,self.model,calls[1:],lambda:self.executor(),partial_result=result))

    def test_feedback_words_in_native_evidence_do_not_cut_the_prefix(self):
        e,calls=self.trajectory(2)
        # Content names cannot confer operational authority or change learning.
        for c in calls:c['input']['comment']='agere_review_feedback is mentioned in a source'
        c=H.propose(e,self.q,self.context,self.model,calls,lambda:self.executor(),partial_result=self.supported_partial())
        self.assertEqual(c['status'],'validated_pending_complete_workflow');self.assertEqual(c['actions'],2)

    def test_partial_prefix_cannot_relax_uncertain_sources_coverage_faults_or_unfinished_work(self):
        for kind in ('undecided_source','source_fault','unchecked','duplicate','coverage_fault','pending_fields',
                     'workflow','answer_workflow','no_review','no_stop','ordinary_partial','bad_metadata'):
            result=self.supported_partial();inv=result['investigation'];review=inv['answer_reviews'][-1];support=review['support']
            if kind=='undecided_source':support['packets'][0]['decided_by']=None
            elif kind=='source_fault':support['label']='contradicted'
            elif kind=='unchecked':support['all_dependencies_checked']=False
            elif kind=='duplicate':support['packets']*=2
            elif kind=='coverage_fault':review['coverage']['decided_by']='fixture'
            elif kind=='pending_fields':review['progress_state']['unresolved_verification']=[{'id':'A'}]
            elif kind=='workflow':result['workflow']=[{'status':'unresolved'}]
            elif kind=='answer_workflow':result['answer']['workflow']=[{'kind':'monitor'}]
            elif kind=='no_review':inv['answer_reviews']=[]
            elif kind=='no_stop':inv.pop('review_stopped')
            elif kind=='ordinary_partial':review.pop('retry_disposition')
            elif kind=='bad_metadata':result['investigation']='broken'
            with self.subTest(kind=kind):self.assertIsNone(H.partial_prefix_evidence(result))

    def test_failed_source_meaning_is_restored_without_reexecuting_provider_work(self):
        from clinical_core.agere.clinical import backstop_agent as B,document_review as D
        tools=B.Tools.__new__(B.Tools);tools.continuation_identity=lambda:{'source':'v1'};tools.question=self.q
        tools.document_field_contracts={};tools.document_reviews=[]
        with H.owned_recovery([],self.q,field_contracts={'reason':'What original reason was requested?'}):
            out=H.recover_actions(tools,self.q)
        self.assertEqual(out['recovered_read_only_actions'],0)
        self.assertEqual(out['restored_field_contracts'],['reason'])
        changed={'question':'A different source question','entity':{'kind':'medication_class','id':'mra','aliases':['MRA']},
                 'relation':'documented_status','subject':'patient'}
        with patch.object(D,'review',side_effect=AssertionError('No source dispatch')):
            with self.assertRaisesRegex(ValueError,'Source field contract changed'):
                tools._call({'name':'review_documents','input':{'patient_ids':['A'],'questions':{'reason':changed}}})

    def test_recovery_cannot_overwrite_a_current_field_guard(self):
        e=self.executor();e.document_field_contracts={'reason':'Current meaning'}
        with H.owned_recovery([{'name':'query_sql','input':{'sql':'SELECT 1'}}],self.q,field_contracts={'reason':'Changed meaning'}):
            with self.assertRaisesRegex(ValueError,'field contract changed'):H.recover_actions(e,self.q)
        self.assertEqual(e.executed,[])

    def test_owned_recovery_retains_exact_historical_composition_failure_and_later_work(self):
        e=self.executor()
        original=e.__class__.__call__
        message='items_from requires collect and a received collection column, without a columns projection'
        def execute(use):
            if use['name']=='compose_table':
                self.assertNotIn('expected_error',use)
                raise ValueError(message)
            return original(e,use)
        actions=[{'name':'query_sql','input':{'sql':'SELECT 1'}},
                 {'name':'compose_table','input':{'base':'agere_result_1'},'expected_error':message},
                 {'name':'query_sql','input':{'sql':'SELECT 2'}}]
        with H.owned_recovery(actions,self.q):
            with patch.object(Executor,'__call__',lambda _,use:execute(use)):
                out=H.recover_actions(e,self.q)
        self.assertEqual(out['recovered_read_only_actions'],3)
        self.assertEqual(out['calls'][1]['result'],{'error':message})
        self.assertTrue(out['calls'][1]['historical_native_error_reproduced'])
        self.assertEqual(out['calls'][-1]['result']['rows'],[[2]])
        self.assertIsNone(out['final_answer']);self.assertIsNone(out['used'])

    def test_owned_recovery_stops_on_changed_failure_success_or_undeclared_error(self):
        for expected,outcome in [('old','changed'),('old',None),(None,'changed')]:
            with self.subTest(expected=expected,outcome=outcome):
                e=self.executor();seen=[]
                def execute(_,use):
                    seen.append(use)
                    if outcome:raise ValueError(outcome)
                    return {'columns':['value'],'rows':[[1]],'result_view':'agere_result_1'}
                action={'name':'compose_table','input':{}}
                if expected:action['expected_error']=expected
                actions=[action,{'name':'query_sql','input':{'sql':'SELECT 2'}}]
                with H.owned_recovery(actions,self.q),patch.object(Executor,'__call__',execute):out=H.recover_actions(e,self.q)
                self.assertEqual(len(seen),1);self.assertEqual(out['recovered_read_only_actions'],1)
                self.assertFalse(out.get('historical_native_errors'))

    def test_historical_error_metadata_cannot_bypass_a_provider_or_source_guard(self):
        e=self.executor()
        with H.owned_recovery([{'name':'review_documents','input':{},'expected_error':'provider failed'}],self.q):
            with self.assertRaisesRegex(ValueError,'pure native'):H.recover_actions(e,self.q)
        self.assertEqual(e.executed,[])
        def change(_,use):
            e.source='changed';raise ValueError('old')
        with H.owned_recovery([{'name':'compose_table','input':{},'expected_error':'old'}],self.q),patch.object(Executor,'__call__',change):
            with self.assertRaisesRegex(ValueError,'Source/scope changed'):H.recover_actions(e,self.q)

    def test_fresh_composition_preserves_raw_provenance_without_mistaking_elapsed_time_for_changed_evidence(self):
        from clinical_core.agere.clinical.result_assembly import compose
        import copy
        results={'base':{'columns':['patient_id'],'rows':[{'patient_id':'A'}],'elapsed_s':1},
                 'source':{'columns':['patient_id','value','record_id'],'rows':[{'patient_id':'A','value':3,'record_id':'R1'}],'accounting':{'new_paid_api_calls':1}}}
        def build():return compose(results,base='base',keys=['patient_id'],joins=[{'result':'source','as':'evidence','on':{'patient_id':'patient_id'},'mode':'collect'}])
        first=build();saved=copy.deepcopy(first)
        results['base']['elapsed_s']=9;results['source']['accounting']['new_paid_api_calls']=0
        second=build()
        self.assertNotEqual(first['assembly']['base_result_digest'],second['assembly']['base_result_digest'])
        self.assertNotEqual(first['assembly']['joins'][0]['source_result_digest'],second['assembly']['joins'][0]['source_result_digest'])
        self.assertEqual(H.semantic_result(first),H.semantic_result(second));self.assertEqual(first,saved)
        results['source']['rows'][0]['record_id']='R2'
        self.assertNotEqual(H.semantic_result(first),H.semantic_result(build()))
        results['source']['rows'][0].update(record_id='R1',value=4)
        self.assertNotEqual(H.semantic_result(first),H.semantic_result(build()))

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        env=patch.dict(os.environ,{'AGERE_RECURSIVE_IMPROVEMENT':'on','AGERE_EVALUATION':'0'});env.start();self.addCleanup(env.stop)
        self.q='Review the source and produce a brief';self.context={'as_of':'2030-05-27'};self.model='fixed-fixture'
    def executor(self,**kw):
        e=Executor(self.root,**kw);self.addCleanup(e.db.close);return e
    def trajectory(self,n):
        e=self.executor();calls=[]
        for i in range(n):
            use={'name':'query_sql','input':{'sql':f'SELECT {i+1}'}}
            calls.append({**use,'result':e(use),'actor_callback_id':f'callback_{i+1}'})
        return e,calls
    def select(self,candidate,complete=True):
        H.finish(self.root,{'answer':{'status':'answered' if complete else 'partial'},'workflow':[],
                           'investigation':{'recursive_improvement':{'candidate':candidate}}})

    def test_candidate_validation_then_complete_workflow_selection_and_recursive_extension(self):
        e,calls=self.trajectory(2)
        c=H.propose(e,self.q,self.context,self.model,calls,lambda:self.executor())
        self.assertEqual(c['status'],'validated_pending_complete_workflow')
        self.assertIsNone(H.replay(self.executor(),self.q,self.context,self.model)['used'])
        self.select(c)
        replay=H.replay(self.executor(),self.q,self.context,self.model)
        self.assertEqual((replay['used'],len(replay['certificates'])),(c['id'],2))
        e,calls=self.trajectory(3)
        revision=H.propose(e,self.q,self.context,self.model,calls,lambda:self.executor())
        self.assertEqual(revision['round'],2)
        self.select(revision)
        self.assertEqual(len(H.replay(self.executor(),self.q,self.context,self.model)['certificates']),3)

    def test_mismatched_outputs_and_incomplete_workflows_never_select_a_configuration(self):
        e,calls=self.trajectory(2)
        bad=H.propose(e,self.q,self.context,self.model,calls,lambda:self.executor(wrong=True))
        self.assertEqual(bad['status'],'quarantined')
        good=H.propose(e,self.q,self.context,self.model,calls,lambda:self.executor())
        self.select(good,complete=False)
        self.assertIsNone(H.replay(self.executor(),self.q,self.context,self.model)['used'])

    def test_changed_source_scope_question_or_model_abstains_and_changed_receipt_quarantines(self):
        e,calls=self.trajectory(2);c=H.propose(e,self.q,self.context,self.model,calls,lambda:self.executor());self.select(c)
        for executor,q,ctx,model in [(self.executor(source='v2'),self.q,self.context,self.model),
                (self.executor(),self.q+' changed',self.context,self.model),
                (self.executor(),self.q,{'as_of':'2031-01-01'},self.model),(self.executor(),self.q,self.context,'changed-model')]:
            self.assertIsNone(H.replay(executor,q,ctx,model)['used']);self.assertEqual(executor.executed,[])
        wrong=H.replay(self.executor(wrong=True),self.q,self.context,self.model)
        self.assertTrue(wrong['reason']);self.assertEqual(wrong['certificates'],[])
        self.assertIsNone(H.replay(self.executor(),self.q,self.context,self.model)['used'])

    def test_replayed_final_decision_is_rechecked_without_calling_the_actor_and_failure_resumes_it(self):
        e,calls=self.trajectory(2);draft={'status':'answered','answer_type':'count','count':2}
        c=H.propose(e,self.q,self.context,self.model,calls,lambda:self.executor(),final_answer=draft);self.select(c)
        replay=H.replay(self.executor(),self.q,self.context,self.model)
        sender=unittest.mock.Mock(side_effect=AssertionError('Resolved callback should be substituted'))
        r=S.tool_loop(self.q,'system',[],lambda u:None,send=sender,model=self.model,max_tools=None,
                      review=lambda a,c:{'status':'confirmed'},initial_calls=replay['calls'],initial_answer=replay['final_answer'])
        self.assertTrue(r['final_decision_reused']);sender.assert_not_called();self.assertEqual(r['answer']['count'],2)
        response={'content':[{'type':'text','text':json.dumps({'status':'partial','missing':['fresh evidence needed']})}]}
        sender=unittest.mock.Mock(return_value=(response,.001,False))
        reviews=iter([{'status':'repair','feedback':'Source requires additional interpretation'},{'status':'confirmed'}])
        r=S.tool_loop(self.q,'system',[],lambda u:None,send=sender,model=self.model,max_tools=None,
                      review=lambda a,c:next(reviews),initial_calls=replay['calls'],initial_answer=replay['final_answer'])
        self.assertEqual(sender.call_count,1);self.assertEqual(len(r['tool_calls']),2)

    def test_failed_revision_keeps_recursive_parent_and_cost_regression_retires_reuse(self):
        e,calls=self.trajectory(2)
        first=H.propose(e,self.q,self.context,self.model,calls,lambda:self.executor(),baseline_metrics={'estimated_cost_usd':.01})
        self.select(first)
        H.finish(self.root,{'answer':{'status':'answered'},'workflow':[],
            'investigation':{'recursive_improvement':{'replay':{'used':first['id']},'actor_metrics':{'estimated_cost_usd':.02}}}})
        self.assertIsNone(H.latest(self.root,H.identity(e,self.q,self.context,self.model)))
        e,calls=self.trajectory(3)
        second=H.propose(e,self.q,self.context,self.model,calls,lambda:self.executor())
        self.assertEqual(second['round'],2)
        with H.database(self.root) as db:
            payload=json.loads(db.execute('SELECT payload FROM harness_candidates WHERE id=?',(second['id'],)).fetchone()[0])
        self.assertEqual(payload['parent'],first['id'])

    def test_physician_feedback_invalidates_reuse_and_audit_rows_cannot_be_modified_or_deleted(self):
        e,calls=self.trajectory(2);c=H.propose(e,self.q,self.context,self.model,calls,lambda:self.executor());self.select(c)
        H.record_feedback(self.root,c['id'],accepted=False,reason='Physician found a missed source qualifier')
        self.assertIsNone(H.replay(self.executor(),self.q,self.context,self.model)['used'])
        db=H.database(self.root);self.addCleanup(db.close)
        for sql in ("DELETE FROM harness_candidates","DELETE FROM harness_candidate_events",
                    "UPDATE harness_candidates SET payload='{}'","UPDATE harness_candidate_events SET payload='{}'"):
            with self.assertRaisesRegex(sqlite3.IntegrityError,'Immutable'):db.execute(sql)

    def test_changed_source_during_last_validation_action_quarantines_and_storage_failure_preserves_answer(self):
        e,calls=self.trajectory(1)
        class ChangesSource(Executor):
            def __call__(self,use):
                out=super().__call__(use);self.source='changed';return out
        fresh=ChangesSource(self.root);self.addCleanup(fresh.db.close)
        c=H.propose(e,self.q,self.context,self.model,calls,lambda:fresh)
        self.assertEqual(c['status'],'quarantined')
        result={'answer':{'status':'answered'},'workflow':[{'status':'ready'}]}
        with patch.object(H,'database',side_effect=sqlite3.OperationalError('Fixture disk unavailable')):
            out=H.attempt_replay(e,self.q,self.context,self.model)
            self.assertIsNone(out['used']);self.assertEqual(out['calls'],[])
            H.finish(self.root,result)
        self.assertEqual(result['answer']['status'],'answered')
        self.assertIn('bookkeeping_error',result['investigation']['recursive_improvement'])

    def test_dropped_or_changed_downstream_steps_cannot_select_an_execution_revision(self):
        e,calls=self.trajectory(1)
        step={'kind':'deliverable','quote':'create a brief','title':'Brief','template':'brief','unresolved':[]}
        candidate=H.propose(e,self.q,self.context,self.model,calls,lambda:self.executor(),
            final_answer={'status':'answered','workflow':[step]})
        H.finish(self.root,{'answer':{'status':'answered'},'workflow':[],
            'investigation':{'recursive_improvement':{'candidate':candidate}}})
        self.assertIsNone(H.latest(self.root,H.identity(e,self.q,self.context,self.model)))

    def test_failed_request_feedback_is_retained_and_linked_without_selecting_an_answer(self):
        H.failed(self.root,self.q,RuntimeError('Incomplete source acquisition'))
        e,calls=self.trajectory(1)
        self.assertIsNone(H.latest(self.root,H.identity(e,self.q,self.context,self.model)))
        c=H.propose(e,self.q,self.context,self.model,calls,lambda:self.executor())
        db=H.database(self.root);self.addCleanup(db.close)
        feedback=json.loads(db.execute("SELECT payload FROM harness_candidate_events WHERE candidate=? AND kind='candidate'",(c['id'],)).fetchone()[0])
        self.assertEqual(len(feedback['failed_request_feedback_events']),1)
        self.assertIsNone(H.latest(self.root,H.identity(e,self.q,self.context,self.model)))

    def test_monitor_initialization_qualifies_execution_then_adverse_feedback_retires_it(self):
        e,calls=self.trajectory(1)
        step={'kind':'monitor','quote':'monitor the result','mode':'answer','scope':'result','unresolved':[]}
        c=H.propose(e,self.q,self.context,self.model,calls,lambda:self.executor(),
                    final_answer={'status':'answered','workflow':[step]})
        H.finish(self.root,{'answer':{'status':'answered'},'workflow':[{**step,'status':'watching'}],
                           'investigation':{'recursive_improvement':{'candidate':c}}})
        self.assertEqual(H.latest(self.root,H.identity(e,self.q,self.context,self.model))['id'],c['id'])
        H.record_feedback(self.root,c['id'],accepted=False,reason='Observed mismatch on later review')
        self.assertIsNone(H.latest(self.root,H.identity(e,self.q,self.context,self.model)))

    def test_cheaper_actor_cannot_hide_a_more_expensive_complete_workflow(self):
        e,calls=self.trajectory(1)
        c=H.propose(e,self.q,self.context,self.model,calls,lambda:self.executor(),baseline_metrics={'estimated_cost_usd':.1})
        H.finish(self.root,{'answer':{'status':'answered'},'workflow':[],
                           'cost_receipt':{'usd':.2,'usd_known':True,'paid_calls':2},
                           'investigation':{'recursive_improvement':{'candidate':c}}})
        H.finish(self.root,{'answer':{'status':'answered'},'workflow':[],
                           'cost_receipt':{'usd':.4,'usd_known':True,'paid_calls':3},
                           'investigation':{'recursive_improvement':{'replay':{'used':c['id']},'actor_metrics':{'estimated_cost_usd':.01}}}})
        self.assertIsNone(H.latest(self.root,H.identity(e,self.q,self.context,self.model)))
        with H.database(self.root) as db:
            feedback=json.loads(db.execute("SELECT payload FROM harness_candidate_events WHERE candidate=? AND kind='outcome' ORDER BY seq DESC LIMIT 1",(c['id'],)).fetchone()[0])
        self.assertTrue(feedback['observed_full_request_api_cost_regression'])
        self.assertFalse(feedback['observed_actor_cost_regression'])
        self.assertFalse(feedback['total_cost_qualified'])

    def test_disabled_selection_still_records_the_incomplete_outcome(self):
        e,calls=self.trajectory(1)
        c=H.propose(e,self.q,self.context,self.model,calls,lambda:self.executor())
        with patch.dict(os.environ,{'AGERE_RECURSIVE_IMPROVEMENT':'off'}):
            H.finish(self.root,{'answer':{'status':'partial'},'workflow':[],
                'cost_receipt':{'usd':.3,'usd_known':True,'paid_calls':2},
                'investigation':{'recursive_improvement':{'candidate':c}}})
        with H.database(self.root)as db:
            events=[(kind,json.loads(payload))for kind,payload in db.execute('SELECT kind,payload FROM harness_candidate_events WHERE candidate=?',(c['id'],))]
        outcome=[payload for kind,payload in events if kind=='outcome'][-1]
        self.assertFalse(outcome['complete_request']);self.assertFalse(outcome['candidate_selection_enabled'])
        self.assertFalse(outcome['clinical_qualification']);self.assertEqual(outcome['full_request_cost']['api_usd'],.3)
        self.assertNotIn('selected',[kind for kind,payload in events])

    def test_disabled_selection_retains_failure_without_creating_a_candidate(self):
        with patch.dict(os.environ,{'AGERE_RECURSIVE_IMPROVEMENT':'off'}):
            H.failed(self.root,self.q,ValueError('Source changed'))
        with H.database(self.root)as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM harness_candidates').fetchone()[0],0)
            row=json.loads(db.execute("SELECT payload FROM harness_candidate_events WHERE kind='failed_request'").fetchone()[0])
        self.assertFalse(row['candidate_selection_enabled']);self.assertFalse(row['clinical_qualification'])
        self.assertEqual(row['error_type'],'ValueError')


if __name__=='__main__':unittest.main()
