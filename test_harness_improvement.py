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
