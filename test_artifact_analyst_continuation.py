"""General artifact-to-analyst continuation, authored fixtures with no sends."""
import copy,json,sqlite3,tempfile,unittest,os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,Mock
from clinical_core.agere.deliverables import answer_context as A,workflow as W
from clinical_core.agere.clinical import proposers as P,harness_improvement as H
from clinical_core.agere.gateway import ProviderUnavailable

class Continuation(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)
  with sqlite3.connect(self.path/'semantic.sqlite3') as db:
   db.executescript("CREATE TABLE meta(key,value); INSERT INTO meta VALUES('as_of','2030-05-27'),('source_digest','authored'),('version','fixture'); CREATE TABLE patient(patient_id); INSERT INTO patient VALUES('A'),('B');")
  self.stack=SimpleNamespace(path=self.path,allow_live=True)
  self.question='Compare diabetes utilization and create a service proposal'
  self.step={'kind':'deliverable','title':'Service proposal','purpose':'proposal','template':'explore','quote':'create a service proposal','unresolved':[]}
  self.result={'answer':{'status':'partial','headline':'Unverified source findings','patient_ids':['A'],'table':[{'patient_id':'A','events':3}],
                         'investigation_review':{'status':'repair'}},'workflow':[self.step],
               'new_paid_api_calls':2,'planning_economics':{'new_api_estimate_usd':.02,'known_api_estimate_usd':.02},
               'investigation':{'tool_calls':[{'name':'query_sql','input':{'sql':'SELECT patient_id,3 AS events FROM patient WHERE patient_id=\'A\''},'result':{'rows':[{'patient_id':'A','events':3}]}}]}}
  self.materialized=[]
 def artifact(self,path,key,**kw):
  self.materialized.append(key);c=A.load(path,key);ident='DLV-123456ABCDEF'
  success=len(self.materialized)==2 and getattr(self,'pass_second',False)
  step={**self.step,'status':'ready' if success else 'blocked','deliverable_id':ident}
  d=A.root(path)/key/'deliverables'/ident;d.mkdir(parents=True,exist_ok=True)
  (d/'spec.json').write_text(json.dumps({'status':'delivered' if success else 'rejected','verification':{'semantic_review':{'issues':[] if success else [{'requirement':'source meaning','reason':'Investigate documented contradiction'}]}}}))
  out={'initialized':True,'context_id':key,'steps':[step],'new_paid_api_calls':1,'known_api_estimate_usd':.1,'api_cost_known':True}
  W._save(path,key,out);return out
 def repaired(self,*args):
  key,feedback=args[1:];old=A.load(self.path,key,current=True)
  self.assertEqual(old['question'],self.question);self.assertIn('not clinician truth',feedback[0]['authority'])
  new=copy.deepcopy(self.result);new['answer']['headline']='Reviewed source findings'
  new['answer']['investigation_review']={'status':'confirmed'};new['new_paid_api_calls']=1
  new['planning_economics']={'new_api_estimate_usd':.01,'known_api_estimate_usd':.01}
  return new
 def test_failed_artifact_returns_to_same_question_then_saves_new_source_bound_view(self):
  self.pass_second=True
  with patch.object(W,'materialize',side_effect=self.artifact),patch.object(P,'continue_from_artifact',side_effect=self.repaired) as follow:
   out=W.fulfill(self.stack,self.question,copy.deepcopy(self.result))
  self.assertEqual(follow.call_count,1);self.assertEqual(len(self.materialized),2);self.assertEqual(out['workflow'][0]['status'],'ready')
  self.assertEqual(out['new_paid_api_calls'],5);self.assertAlmostEqual(out['planning_economics']['new_api_estimate_usd'],.23)
  self.assertEqual(len(out['workflow_continuation']['attempts']),2);self.assertFalse(out['workflow_continuation']['clinical_acceptance'])
  self.assertNotEqual(self.materialized[0],self.materialized[1]);self.assertTrue((A.root(self.path)/self.materialized[0]/'continuation.json').is_file())
 def test_rewording_alone_does_not_buy_another_artifact_and_failed_attempt_cost_remains(self):
  def reword(*args):
   new=copy.deepcopy(self.result);new['answer']['headline']='Same facts reworded';new['new_paid_api_calls']=1
   new['planning_economics']={'known_api_estimate_usd':.01,'new_api_estimate_usd':None,'api_cost_known':False};return new
  with patch.object(W,'materialize',side_effect=self.artifact),patch.object(P,'continue_from_artifact',side_effect=reword) as follow:
   out=W.fulfill(self.stack,self.question,copy.deepcopy(self.result))
  self.assertEqual(follow.call_count,1);self.assertEqual(len(self.materialized),1)
  self.assertEqual(out['workflow_continuation']['status'],'no_new_native_evidence_or_confirmed_interpretation')
  self.assertEqual(out['new_paid_api_calls'],4);self.assertIsNone(out['planning_economics']['new_api_estimate_usd']);self.assertAlmostEqual(out['planning_economics']['known_api_estimate_usd'],.13)
 def test_subscription_limit_is_retained_with_no_retry(self):
  error=ProviderUnavailable('Subscription limit',kind='rate_limit');error.planner_receipt={'new_paid_api_calls':1,'known_api_estimate_usd':.005,'api_cost_known':False}
  with patch.dict(os.environ,{'AGERE_CLAUDE_TRANSPORT':'subscription'}),patch.object(W,'materialize',side_effect=self.artifact),patch.object(P,'continue_from_artifact',side_effect=error) as follow:
   out=W.fulfill(self.stack,self.question,copy.deepcopy(self.result))
  self.assertEqual(follow.call_count,1);self.assertEqual(out['workflow_continuation']['status'],'subscription_limit');self.assertIsNone(out['planning_economics']['new_api_estimate_usd'])
 def test_existing_internal_work_is_preserved_without_duplicate_effects(self):
  original=copy.deepcopy(self.result);original['workflow'].append({'kind':'simulation','program':'custom','title':'Internal review'})
  with patch.object(W,'materialize',side_effect=self.artifact),patch.object(P,'continue_from_artifact') as follow:
   out=W.fulfill(self.stack,self.question,original)
  follow.assert_not_called();self.assertEqual(len(self.materialized),1);self.assertEqual(out['workflow_continuation']['status'],'existing_internal_work_requires_reconciliation')
 def test_source_changed_after_failure_cannot_reach_actor(self):
  def changed(*args,**kwargs):
   out=self.artifact(*args,**kwargs)
   with sqlite3.connect(self.path/'semantic.sqlite3') as db:db.execute("UPDATE meta SET value='changed' WHERE key='source_digest'")
   return out
  with patch.object(W,'materialize',side_effect=changed),patch.object(P,'continue_from_artifact') as follow:
   out=W.fulfill(self.stack,self.question,copy.deepcopy(self.result))
   self.assertEqual(out['workflow_continuation']['status'],'source_or_review_unavailable')
   self.assertEqual(out['new_paid_api_calls'],3)
  follow.assert_not_called()
 def test_rendering_subscription_limit_stops_before_another_analyst(self):
  def limited(*args,**kwargs):
   out=self.artifact(*args,**kwargs);out['steps'][0]['provider_failure']={'kind':'rate_limit','transport':'subscription'};return out
  with patch.object(W,'materialize',side_effect=limited),patch.object(P,'continue_from_artifact') as follow:
   out=W.fulfill(self.stack,self.question,copy.deepcopy(self.result))
  follow.assert_not_called();self.assertEqual(out['workflow_continuation']['status'],'subscription_limit')

 def test_progress_ignores_repeated_calls_and_accounting_but_keeps_native_values(self):
  key=A.capture(self.path,self.question,self.result)['id'];context=A.load(self.path,key)
  same=copy.deepcopy(context);same['answer']['headline']='Different wording';same['investigation']['tool_calls']*=2
  for c in same['investigation']['tool_calls']:c['result'].update(accounting={'cost_usd':99},result_view='new_name')
  self.assertEqual(W._progress(context),W._progress(same))
  same['investigation']['tool_calls'][0]['result']['rows'][0]['events']=4
  self.assertNotEqual(W._progress(context),W._progress(same))
 def test_preview_preserves_exact_exclusive_category_counts_and_positive_row_counts(self):
  from clinical_core.agere.clinical.semantic_analyst import actor_result
  rows=[{'patient_id':str(i),'basis':'both' if i<13 else 'note_only' if i<30 else 'coded_only','admissions':i%3,'text':'long context '*100} for i in range(57)]
  raw={'columns':list(rows[0]),'rows':rows,'result_view':'native'};shown=actor_result(raw)
  self.assertTrue(shown['preview_only']);self.assertEqual(len(raw['rows']),57)
  counts=shown['computed_row_count_profile']['columns']
  self.assertEqual(counts['basis']['repeated_values'],[{'value':'both','rows':13},{'value':'note_only','rows':17},{'value':'coded_only','rows':27}])
  self.assertEqual(counts['admissions']['positive_numeric_rows'],38)
  self.assertEqual(counts['patient_id']['singleton_value_rows'],57)
  self.assertEqual(counts['patient_id']['repeated_values'],[])

 def test_continuation_reuses_exact_empty_input_and_reexecutes_tools_without_prior_answer(self):
  from clinical_core.agere.gateway import BudgetLedger
  from clinical_core.agere.clinical import decision_context as D
  original=copy.deepcopy(self.result);original['answer'].update(patient_ids=[],table=[])
  original['planner_receipt']={'request_contract':{'population':[]}}
  key=A.capture(self.path,self.question,original)['id'];test=self
  class Cascade:
   def __init__(self,stack,catalog,question,*,population):
    test.assertEqual(question,test.question);test.assertEqual(population,[])
    self.population=population;self.ledger=BudgetLedger(test.path/'providers.sqlite3');self.started=__import__('time').perf_counter();self.log=[];self.provider_error=None
   def attempt(self,name,limit):
    test.assertEqual(name,'backstop_agent')
    executions=[]
    def native(action):executions.append(action);return {'rows':[]}
    tools=SimpleNamespace(continuation_identity=lambda:{'source':'fixed'})
    class Tools:
     def continuation_identity(self):return {'source':'fixed'}
     def __call__(self,action):return native(action)
    replay=H.recover_actions(Tools(),test.question)
    test.assertIsNone(replay['final_answer']);test.assertEqual(len(executions),1)
    test.assertEqual(replay['downstream_review_feedback'],[{'reason':'machine review'}])
    return ({'kind':'checked_graph','graph':{'nodes':[]},'executed':{'answer':{'status':'partial','patient_ids':[],'table':[],'headline':'Retained unknown'}}},
            {'interpreter':'backstop_agent','proposer':{'calibration':'authored test'}})
   def accept(self,order,name,found):return found
   def usage(self):return {'new_paid_api_calls':0,'known_api_estimate_usd':0.,'api_cost_known':True}
   def close(self):self.ledger.close()
  with patch.object(P,'Cascade',Cascade),patch.object(P.request_plan.Catalog,'load',return_value=object()),patch.object(D,'finalize',side_effect=lambda path,result,state:result):
   out=P.continue_from_artifact(self.stack,key,[{'reason':'machine review'}])
  self.assertEqual(out['request_contract']['population'],[]);self.assertEqual(out['execution_snapshot'],A.source_identity(self.path))

 def test_empty_recovery_still_passes_machine_feedback_without_old_answer(self):
  tools=SimpleNamespace(continuation_identity=lambda:{'source':'fixed'})
  with H.owned_recovery([],self.question,[{'reason':'Investigate'}]):out=H.recover_actions(tools,self.question)
  self.assertEqual(out['downstream_review_feedback'],[{'reason':'Investigate'}]);self.assertIsNone(out['final_answer'])

 def test_saved_draft_resume_preserves_scope_and_reexecutes_before_review(self):
  from clinical_core.agere.gateway import BudgetLedger
  from clinical_core.agere.clinical import decision_context as D
  original=copy.deepcopy(self.result);original['workflow']=[]
  original['planner_receipt']={'request_contract':{'population':[]}}
  call=original['investigation']['tool_calls'][0]
  call.update(actor_callback_id='actual-callback')
  call['result']['result_view']='agere_result_1'
  draft={'status':'partial','answer_type':'table','table_from_tool':'agere_result_1','headline':'Still under review'}
  ctx={'question':self.question,'patient_scope':[]}
  checkpoint={'source':A.source_identity(self.path),'context':ctx,'draft':draft,'draft_digest':H.digest(draft),'review_contexts':[]}
  original['investigation']['recovery_checkpoint']=checkpoint
  key=A.capture(self.path,self.question,original)['id'];test=self
  class Cascade:
   def __init__(self,stack,catalog,question,*,population):
    test.assertEqual(population,[]);self.ledger=BudgetLedger(test.path/'providers.sqlite3');self.started=__import__('time').perf_counter();self.log=[]
   def attempt(self,name,limit):
    class Tools:
     stack=test.stack
     def continuation_identity(self):return {'source':'current'}
     def __call__(self,action):
      test.assertEqual(action,{k:call[k] for k in ('name','input')});return copy.deepcopy(call['result'])
    replay=H.recover_actions(Tools(),test.question,ctx)
    test.assertEqual(replay['resume_draft'],{**draft,'verification':'unverified'})
    test.assertEqual(replay['draft_recovery']['native_receipts_checked'],1)
    test.assertIsNone(replay['final_answer']);test.assertEqual(replay['downstream_review_feedback'],[])
    return ({'kind':'checked_graph','graph':{'nodes':[]},'executed':{'answer':{**draft,'patient_ids':[],'verification':'unverified'}}},
            {'interpreter':'backstop_agent','proposer':{'calibration':'authored test'}})
   def accept(self,order,name,found):return found
   def usage(self):return {'new_paid_api_calls':0,'known_api_estimate_usd':0.,'api_cost_known':True}
   def close(self):self.ledger.close()
  with patch.object(P,'Cascade',Cascade),patch.object(P.request_plan.Catalog,'load',return_value=object()),patch.object(D,'finalize',side_effect=lambda path,result,state:result):
   result=P.continue_from_artifact(self.stack,key)
  self.assertEqual(result['answer']['status'],'partial');self.assertEqual(result['answer']['verification'],'unverified')
  self.assertEqual(result['request_contract']['population'],[])

 def test_resume_without_bound_checkpoint_stops_before_provider(self):
  key=A.capture(self.path,self.question,self.result)['id']
  with patch.object(P,'Cascade') as cascade,self.assertRaisesRegex(ValueError,'no current unfinished draft'):
   P.continue_from_artifact(self.stack,key)
  cascade.assert_not_called()

if __name__=='__main__':unittest.main()
