"""Source exclusions preserve proposals only behind current native/review guards."""
import copy,json,unittest
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere.clinical import harness_improvement as H,semantic_analyst as S,assertion_reconciliation as AR
from test_assertion_reconciliation import annotation,receipt

class SourceSuspensionRecovery(unittest.TestCase):
 def fixture(self):
  doc,event=annotation();assertion=receipt([doc],[event])['assertions'][0]
  other=copy.deepcopy(assertion);other.update(id='other-assertion',patient_id='q');other['source']['patient_id']='q'
  def row(pid,assertions):
   findings=[{'value':a['value'],'record_id':a['source']['record_id'],'assertion':a}for a in assertions]
   state={'assertions':assertions,'groups':AR.reconcile(pid,assertions,'2030-05-27'),'current_clinical_state_established':False,'method':'native'}
   return {'patient_id':pid,'review_json':json.dumps({'reason':findings}),'review_limitations':json.dumps([]),
    'source_findings_json':json.dumps([{'field':'reason',**v}for v in findings]),'documentary_state_json':json.dumps(state)}
  oldrows=[row('p',[assertion]),row('q',[other])];newrows=[row('p',[]),row('q',[other])]
  newrows[0]['review_limitations']=json.dumps(['Disputed source observation '+assertion['source']['record_id']+' field reason: unchanged input remains unresolved; source, question, model or review policy must change.'])
  old={'columns':list(oldrows[0]),'rows':oldrows,'result_view':'agere_result_1'};new={**old,'rows':newrows}
  def table(rows):return {'columns':['patient_id','amount','evidence'],'rows':[[r['patient_id'],2,r['review_json']]for r in rows],'result_view':'agere_result_2'}
  calls=[{'name':'review_documents','input':{'patient_ids':['p','q'],'questions':{'reason':'Why?'}},'result':old,'actor_callback_id':'origin'},
   {'name':'query_sql','input':{'sql':'SELECT * FROM agere_result_1'},'result':table(oldrows),'actor_callback_id':'origin'}]
  outputs=[new,table(newrows)];draft={'status':'partial','answer_type':'table','table_from_tool':'agere_result_2','headline':'Reasons require current review.',
   'row_prose':{'keys':['patient_id'],'rows':[{'key':{'patient_id':pid},'basis':[{'result_view':'agere_result_2','row':i}],
    'fields':{'explanation':'Source observations remain unverified.'}}for i,pid in enumerate(('p','q'))]}}
  context={'question':'q','as_of':'2030-05-27'};source={'source_digest':'same','as_of':'2030-05-27'}
  cp={'source':source,'context':context,'draft':draft,'draft_digest':H.digest(draft),'review_contexts':[]}
  disposition={'status':'suspended','suspended_assertions':[{'assertion_id':assertion['id']}]}
  return calls,outputs,cp,disposition
 def run_recovery(self,calls,outputs,cp,disposition,notes=None):
  class Tools:
   stack=SimpleNamespace(path='.');document_field_contracts={}
   def __init__(self):self.executed=[]
   def continuation_identity(self):return cp['source']
   def __call__(self,action):
    i=len(self.executed);self.executed.append(action);return copy.deepcopy(outputs[i])
  tools=Tools();actions=[{k:c[k]for k in('name','input')}for c in calls]
  with patch('clinical_core.agere.deliverables.answer_context.source_identity',return_value=cp['source']),patch(
   'clinical_core.agere.clinical.typed_document_review.assertion_disposition',return_value=disposition),H.owned_recovery(
   actions,'q',origin={'question':'q','calls':calls},checkpoint=cp,prior_review_notes=notes):out=H.recover_actions(tools,'q',cp['context'])
  return out,tools
 def test_source_exclusion_reexecutes_suffix_preserves_proposal_and_requires_full_review(self):
  calls,outputs,cp,disposition=self.fixture();original=copy.deepcopy(cp);out,tools=self.run_recovery(calls,outputs,cp,disposition)
  self.assertEqual(len(tools.executed),2);self.assertEqual(out['draft_recovery']['native_receipts_checked'],2)
  self.assertEqual(out['source_refresh']['changed_actions'],[1,2]);self.assertIsNone(out['final_answer']);self.assertIsNone(out['used'])
  self.assertEqual(out['resume_draft']['row_prose'],cp['draft']['row_prose']);self.assertEqual(cp,original)
  self.assertEqual(out['calls'][0]['result'],outputs[0]);self.assertEqual(out['resume_draft']['verification'],'unverified')
  reviewed=[]
  def review(draft,actual):
   reviewed.append((draft,actual));return {'status':'repair','feedback':'Current source coverage unresolved.','retry_disposition':'retain_partial_until_new_evidence'}
  def forbidden(*a,**k):self.fail('No actor/provider is needed to establish this guard')
  result=S.tool_loop('q','',[],forbidden,send=forbidden,model='fixture',max_tools=None,initial_calls=out['calls'],initial_answer=out['resume_draft'],review=review)
  self.assertEqual(len(reviewed),1);self.assertEqual(result['answer']['status'],'partial')
 def test_recovered_machine_note_is_history_and_fresh_pending_feedback_is_retained(self):
  calls,outputs,cp,disposition=self.fixture();feedback='Bind the failed quantity to its current source.'
  cp['draft']['limitations']=['Maximum tolerated dose is unknown.',feedback];cp['draft_digest']=H.digest(cp['draft'])
  old=copy.deepcopy(cp);notes=[{'context_id':'ANS-'+'a'*24,'review_index':0,'review':{'status':'repair','feedback':feedback}}]
  out,tools=self.run_recovery(calls,outputs,cp,disposition,notes)
  self.assertEqual(out['resume_draft']['limitations'],['Maximum tolerated dose is unknown.']);self.assertEqual(cp,old)
  annotation=out['draft_recovery']['prior_review_annotations'][0]
  self.assertEqual(annotation['text'],feedback);self.assertFalse(annotation['resolved']);self.assertTrue(annotation['current_review_required'])
  def review(draft,actual):
   self.assertNotIn(feedback,draft['limitations']);self.assertEqual(len(actual),2)
   return {'status':'repair','repair_kind':'unresolved_verification','feedback':feedback,'retry_disposition':'retain_partial_until_new_evidence'}
  def forbidden(*a,**k):self.fail('No provider dispatch in this guard check')
  result=S.tool_loop('q','',[],forbidden,send=forbidden,model='fixture',max_tools=None,initial_calls=out['calls'],initial_answer=out['resume_draft'],review=review)
  self.assertIn(feedback,result['answer']['limitations']);self.assertEqual(result['answer']['status'],'partial')
 def test_unrelated_reading_source_population_or_limitation_changes_cannot_refresh(self):
  for kind in ('reading','source','population','limitation','unavailable','unsuspended','new_assertion'):
   calls,outputs,cp,disposition=self.fixture();row=outputs[0]['rows'][0]
   if kind=='reading':outputs[0]['rows'][1]['review_json']='{}'
   elif kind=='source':outputs[0]['source_scope']='changed'
   elif kind=='population':row['patient_id']='different'
   elif kind=='limitation':row['review_limitations']='[]'
   elif kind=='unavailable':disposition={'status':'unavailable','suspended_assertions':[]}
   elif kind=='unsuspended':disposition={'status':'current','suspended_assertions':[]}
   else:
    v=json.loads(row['documentary_state_json']);v['assertions']=json.loads(calls[0]['result']['rows'][1]['documentary_state_json'])['assertions'];row['documentary_state_json']=json.dumps(v)
   with self.subTest(kind=kind):
    out,tools=self.run_recovery(calls,outputs,cp,disposition);self.assertNotIn('resume_draft',out);self.assertEqual(len(tools.executed),1)
 def test_changed_scalar_entity_address_or_unrelated_suffix_withholds_prose(self):
  for kind in ('scalar','scalar_type','order','schema','unrelated','view','no_row_prose','error'):
   calls,outputs,cp,disposition=self.fixture()
   if kind=='scalar':outputs[1]['rows'][1][1]=9
   elif kind=='scalar_type':outputs[1]['rows'][1][1]=2.0
   elif kind=='order':outputs[1]['rows'].reverse()
   elif kind=='schema':outputs[1]['columns'][1]='different_amount'
   elif kind=='unrelated':calls[1]['input']['sql']='SELECT * FROM unrelated_source'
   elif kind=='view':outputs[1]['result_view']='agere_result_3'
   elif kind=='no_row_prose':del cp['draft']['row_prose'];cp['draft_digest']=H.digest(cp['draft'])
   else:outputs[1]={'error':'source unavailable'}
   with self.subTest(kind=kind):
    out,tools=self.run_recovery(calls,outputs,cp,disposition);self.assertNotIn('resume_draft',out);self.assertEqual(len(tools.executed),2);self.assertEqual(out['calls'][1]['result'],outputs[1])
 def test_suspension_revoked_mid_replay_withholds_the_proposal(self):
  calls,outputs,cp,disposition=self.fixture()
  original=self.run_recovery
  # The root was suspended when observed; a changed operational disposition
  # must not restore a proposal using that earlier authorization.
  with patch.object(H,'_suspended_review_refresh',return_value=[disposition['suspended_assertions'][0]['assertion_id']]):
   out,tools=original(calls,outputs,cp,{'status':'current','suspended_assertions':[]})
  self.assertEqual(len(tools.executed),2);self.assertNotIn('resume_draft',out)
 def test_changed_global_binding_is_not_rebased_to_another_value(self):
  calls,outputs,cp,disposition=self.fixture();d=cp['draft'];d['facts']={'amount':{'template':'{n} events','bindings':{'n':{'result_view':'agere_result_2','row':1,'column':'amount'}}}};cp['draft_digest']=H.digest(d)
  self.assertTrue(H._source_refresh_draft_bindings(d,calls,[{**c,'result':r}for c,r in zip(calls,outputs)]))
  outputs[1]['rows'][1][1]=3;self.assertFalse(H._source_refresh_draft_bindings(d,calls,[{**c,'result':r}for c,r in zip(calls,outputs)]))
if __name__=='__main__':unittest.main()
