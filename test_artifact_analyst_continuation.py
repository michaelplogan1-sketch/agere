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
  original=copy.deepcopy(self.result);original['workflow']=[];original['graph']={'nodes':[]}
  original['planner_receipt']={'request_contract':{'population':[]}}
  call=original['investigation']['tool_calls'][0]
  call.update(actor_callback_id='actual-callback')
  call['result']['result_view']='agere_result_1'
  from clinical_core.agere.clinical.backstop_agent import NATIVE_SQL_CONTRACT
  call['result'].update(native_sql_contract=NATIVE_SQL_CONTRACT,executed_query=call['input']['sql'],
                        columns=['patient_id','events'],truncated=False)
  draft={'status':'partial','answer_type':'table','table_from_tool':'agere_result_1','headline':'Still under review'}
  ctx={'question':self.question,'patient_scope':[]}
  checkpoint={'source':A.source_identity(self.path),'context':ctx,'draft':draft,'draft_digest':H.digest(draft),'review_contexts':[]}
  original['investigation']['recovery_checkpoint']=checkpoint
  from clinical_core.agere.clinical import span_repair as SP
  finding={'value':'Authored observation','assertion':{'id':'suspended-fixture'}}
  original['investigation']['document_reviews']=[{'questions':{'field':'What is documented?'},
   'rows':[{'patient_id':'A','review_json':json.dumps({'field':[finding]})}]}]
  SP.remember_observation(self.path,'span-fixture',{'label':'finding'},['authored-request'],assertion_id='suspended-fixture')
  key=A.capture(self.path,self.question,original)['id'];test=self
  SP.quarantine_assertions(self.path,['suspended-fixture'],'Authored unadjudicated source objection')
  snapshot=A.snapshot_path(A.root(self.path)/key/'answer.json');saved=snapshot.read_bytes()
  with self.assertRaisesRegex(ValueError,'suspended source reading'):
   A.load(self.path,key,current=True)
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
  self.assertEqual(snapshot.read_bytes(),saved)
  self.assertEqual(A.load(self.path,key,current=True,source_review=True)['source_disposition']['status'],'suspended')
  with self.assertRaisesRegex(ValueError,'suspended source reading'):
   A.create_work(self.path,{'context_id':key,'program':'custom','patient_ids':['A'],'owner':'Fixture review team','note':'Synthetic only'})
  self.assertEqual(A.inspect(self.path,key)['items'],[])

 def test_resume_without_bound_checkpoint_stops_before_provider(self):
  key=A.capture(self.path,self.question,self.result)['id']
  with patch.object(P,'Cascade') as cascade,self.assertRaisesRegex(ValueError,'no current unfinished draft'):
   P.continue_from_artifact(self.stack,key)
  cascade.assert_not_called()

 def test_interrupted_resume_saves_new_draft_and_failure_accounting_without_replacing_history(self):
  from clinical_core.agere.gateway import BudgetLedger
  from clinical_core.agere.service import _failure_receipts
  def bound_origin(original,checkpoint):
   from clinical_core.agere.clinical.backstop_agent import NATIVE_SQL_CONTRACT
   call=original['investigation']['tool_calls'][0];call['actor_callback_id']='saved-native-callback'
   call['result'].update(result_view='agere_result_1',native_sql_contract=NATIVE_SQL_CONTRACT,
     executed_query=call['input']['sql'],columns=['patient_id','events'],truncated=False)
   checkpoint['draft'].update(table_from_tool='agere_result_1')
   checkpoint['draft_digest']=H.digest(checkpoint['draft'])
  for changed in (None,'source','population','question','draft','live_source','save_error','evaluation'):
   with self.subTest(changed=changed):
    key=self.draft_with_rejected_returned_answer(bound_origin)
    original=A.snapshot_path(A.root(self.path)/key/'answer.json').read_bytes()
    partial=copy.deepcopy(self.result);partial.update(question=self.question,workflow=[],graph={'nodes':[]},
      execution_snapshot=A.source_identity(self.path),request_contract={'population':None})
    partial['answer'].update(status='partial',verification='unverified',headline='New native work, review interrupted')
    draft={'status':'partial','headline':'New unfinished draft '+str(changed)}
    partial['investigation']['recovery_checkpoint']={'source':partial['execution_snapshot'],
      'context':{'question':self.question},'draft':draft,'draft_digest':H.digest(draft),'review_contexts':[]}
    if changed=='source':partial['execution_snapshot']={**partial['execution_snapshot'],'source_digest':'changed'}
    if changed=='population':partial['request_contract']['population']=[]
    if changed=='question':partial['question']='A different question'
    if changed=='draft':partial['investigation']['recovery_checkpoint']['draft_digest']='changed'
    error=ProviderUnavailable('Authored subscription interruption',kind='rate_limit');error.partial_result=partial
    test=self
    class Cascade:
     def __init__(self,*args,**kwargs):
      self.ledger=BudgetLedger(test.path/'resume-attempts.sqlite3');self.log=[];self.started=0
     def attempt(self,*args):
      if changed=='live_source':
       with sqlite3.connect(test.path/'semantic.sqlite3') as db:db.execute("UPDATE meta SET value='new-source' WHERE key='source_digest'")
      raise error
     def usage(self):return {'new_paid_api_calls':1,'known_api_estimate_usd':.01,'api_cost_known':False}
     def close(self):self.ledger.close()
    from contextlib import ExitStack
    from clinical_core.agere.clinical import evaluation
    review_context=A.load_unfinished_for_review(self.path,key)
    with ExitStack() as patches:
     patches.enter_context(patch.object(P,'Cascade',Cascade));patches.enter_context(patch.object(P.request_plan.Catalog,'load',return_value=object()))
     if changed=='save_error':patches.enter_context(patch.object(A,'capture',side_effect=OSError('Authored disk interruption')))
     if changed=='evaluation':
      # Evaluation normally loads its own isolated context. Supply that boundary
      # here to check that a partial failure never publishes into ordinary work.
      patches.enter_context(patch.object(A,'load_unfinished_for_review',return_value=review_context))
      patches.enter_context(evaluation.scope('authored-resume-isolation'))
     with self.assertRaises(ProviderUnavailable)as caught:P.continue_from_artifact(self.stack,key)
    self.assertIs(caught.exception,error)
    self.assertEqual(A.snapshot_path(A.root(self.path)/key/'answer.json').read_bytes(),original)
    response=_failure_receipts(error)
    self.assertEqual(response['new_paid_api_calls'],1);self.assertIsNone(response['planning_economics']['new_api_estimate_usd'])
    if changed=='evaluation':
     self.assertNotIn('action_context',response);self.assertNotIn('action_context_error',response)
    elif changed:
     self.assertNotIn('action_context',response);self.assertIn('action_context_error',response)
    else:
     saved=response['action_context']['id'];self.assertNotEqual(saved,key)
     reopened=A.load(self.path,saved,source_review=True)
     self.assertEqual(reopened['question'],self.question)
     self.assertEqual(reopened['investigation']['recovery_checkpoint']['draft'],draft)
     self.assertEqual(reopened['planner_receipt']['artifact_feedback_context'],key)
     self.assertEqual(reopened['answer']['verification'],'unverified');self.assertEqual(reopened['workflow'],[])

 def draft_with_rejected_returned_answer(self,mutation=None):
  original=copy.deepcopy(self.result);original['workflow']=[]
  self.draft_fixture_sequence=getattr(self,'draft_fixture_sequence',0)+1
  original['answer']['fixture_case']=self.draft_fixture_sequence
  original['graph']={'nodes':[]}
  draft={'status':'partial','headline':'Corrected source basis','table':[]}
  checkpoint={'source':A.source_identity(self.path),'context':{'question':self.question},
              'draft':draft,'draft_digest':H.digest(draft),'review_contexts':[]}
  original['investigation']['recovery_checkpoint']=checkpoint
  if mutation:mutation(original,checkpoint)
  return A.capture(self.path,self.question,original)['id']

 def test_corrected_draft_can_be_reinvestigated_without_accepting_invalid_returned_answer(self):
  from clinical_core.agere.clinical import plan_fence as F
  key=self.draft_with_rejected_returned_answer()
  snapshot=A.snapshot_path(A.root(self.path)/key/'answer.json');before=snapshot.read_bytes()
  def premises(*args,answer=None,**kwargs):
   return []if answer.get('headline')=='Corrected source basis'else['definitions: old basis']
  with patch.object(F,'premise_issues',side_effect=premises):
   with self.assertRaisesRegex(ValueError,'invalidated'):A.load(self.path,key,current=True,source_review=True)
   reviewed=A.load_unfinished_for_review(self.path,key)
   self.assertTrue(reviewed['invalidated'])
   self.assertEqual(reviewed['answer']['headline'],'Unverified source findings')
   self.assertEqual(reviewed['draft_review_basis']['authority'],'unfinished_investigation_only')
   self.assertFalse(reviewed['dependency_validation'][0]['premises_current'])
   with self.assertRaisesRegex(ValueError,'invalidated'):
    A.create_work(self.path,{'context_id':key,'program':'custom','patient_ids':['A'],'owner':'Fixture','note':''})
  self.assertEqual(snapshot.read_bytes(),before)

 def test_draft_review_does_not_waive_other_invalidation_or_bad_checkpoint(self):
  from clinical_core.agere.clinical import plan_fence as F
  from clinical_core.agere.clinical import shared_planner as S
  changes={
   'wrong_digest':lambda o,c:c.update(draft_digest='wrong'),
   'foreign_question':lambda o,c:c['context'].update(question='Another question'),
   'foreign_source':lambda o,c:c.update(source={'source_digest':'foreign'}),
   'nonpartial_draft':lambda o,c:c['draft'].update(status='answered'),
   'existing_workflow':lambda o,c:o.update(workflow=[self.step]),
   'incomplete_evidence':lambda o,c:o['answer'].update(evidence_completeness={'status':'incomplete'})}
  for name,mutation in changes.items():
   with self.subTest(name=name):
    key=self.draft_with_rejected_returned_answer(mutation)
    with patch.object(F,'premise_issues',return_value=['definitions: old basis']),self.assertRaisesRegex(ValueError,'unfinished|integrity|invalidated'):
     A.load_unfinished_for_review(self.path,key)
  key=self.draft_with_rejected_returned_answer()
  def premises(*args,answer=None,**kwargs):return []if answer.get('headline')=='Corrected source basis'else['old basis']
  with patch.object(F,'premise_issues',side_effect=premises),patch.object(S,'policy_identity',return_value='changed'):
   # An old policy can seed read-only re-investigation, never accepted reuse.
   reviewed=A.load_unfinished_for_review(self.path,key)
   self.assertTrue(reviewed['invalidated'])
   with self.assertRaisesRegex(ValueError,'invalidated'):A.load(self.path,key,current=True,source_review=True)
  snapshot=A.snapshot_path(A.root(self.path)/key/'answer.json');data=A.read_snapshot(snapshot)
  data['investigation']['recovery_checkpoint']['draft']['headline']='tampered'
  # Deliberately corrupt the immutable file without resealing it.
  import gzip
  with gzip.open(snapshot,'wt')if snapshot.suffix=='.gz'else open(snapshot,'w')as stream:json.dump(data,stream)
  with patch.object(F,'premise_issues',return_value=[]),self.assertRaisesRegex(ValueError,'unfinished|integrity'):
   A.load_unfinished_for_review(self.path,key)

if __name__=='__main__':unittest.main()
