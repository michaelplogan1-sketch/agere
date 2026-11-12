"""Saved typed proofs are candidates; only exact current inputs can consume them."""
import copy,unittest
from types import SimpleNamespace
from unittest.mock import Mock
from clinical_core.agere.clinical import harness_improvement as H,backstop_agent as B
from clinical_core.contracts import digest
from clinical_core.agere.deliverables.receipts import uncompact_json
from test_complete_claim_composition import packet,fields
import json

class VerificationRecovery(unittest.TestCase):
 def test_restored_field_proof_is_consumed_without_repeating_the_reader(self):
  p=packet();review=B.localize_support_failure(p,fields,as_of=p['date'],ledger=None)['field_review']
  history=[{'support':{'packets':[{'id':p['id'],'label':'unknown','decided_by':None,'field_review':review}]}}]
  old=copy.deepcopy(history);tools=SimpleNamespace()
  receipt=H.restore_verification_candidates(tools,history)
  self.assertEqual(receipt['source_packet_candidates'],1);self.assertFalse(receipt['whole_answer_accepted'])
  out=B.check_support_packets([p],Mock(side_effect=AssertionError('redundant joint review')),
   as_of=p['date'],ledger=None,field_decider=Mock(side_effect=AssertionError('unchanged field re-read')),
   prior_field_reviews=tools.support_field_reviews)
  self.assertEqual(out['label'],'supported');self.assertEqual(out['steps'],[])
  self.assertEqual(history,old)
 def test_source_question_value_or_threshold_change_does_not_inherit_support(self):
  p=packet();review=B.localize_support_failure(p,fields,as_of=p['date'],ledger=None)['field_review']
  for change in ('source','question','value','threshold'):
   q=copy.deepcopy(p);r=copy.deepcopy(review);state=uncompact_json(json.loads(q['text']))
   if change=='source':state['source_contexts'][0]['text']='Changed source'
   elif change=='question':state['question']='Different question'
   elif change=='value':state['answer_rows'][0]['relationship']='Current cardiologist'
   else:r['supported_claims'][0]['verification_observation']['model_judgment']['probability']=.1
   if change!='threshold':
    from clinical_core.agere.deliverables.receipts import readable_json
    q['text']=json.dumps(readable_json(state))
   tools=SimpleNamespace();H.restore_verification_candidates(tools,[{'support':{'packets':[{'field_review':r}]}}])
   self.assertEqual(B.bound_field_support(q,tools.support_field_reviews.get(digest(q['text']))),[],change)
 def test_later_adverse_packet_retires_old_positive(self):
  p=packet();r=B.localize_support_failure(p,fields,as_of=p['date'],ledger=None)['field_review']
  positive={'field_review':r,'label':'supported','decided_by':'authored'}
  adverse={**positive,'label':'contradicted','typed_review':{'field_review':r}}
  tools=SimpleNamespace();receipt=H.restore_verification_candidates(tools,[{'support':{'packets':[positive]}},{'support':{'packets':[adverse]}}])
  self.assertFalse(tools.support_field_reviews);self.assertEqual(receipt['retired_packet_candidates'],1)
 def test_owned_native_replay_restores_candidates_only_after_complete_match(self):
  from unittest.mock import patch
  import test_harness_improvement as fixture_module
  fixture=fixture_module.Harness();fixture.setUp();self.addCleanup(fixture.doCleanups)
  p=packet();r=B.localize_support_failure(p,fields,as_of=p['date'],ledger=None)['field_review']
  history=[{'support':{'packets':[{'field_review':r,'label':'unknown','decided_by':None}]}}]
  for changed in (False,True):
   actions,origin,checkpoint=fixture.recovery_checkpoint();executor=fixture.executor(wrong=changed)
   with patch('clinical_core.agere.deliverables.answer_context.source_identity',return_value=checkpoint['source']),H.owned_recovery(
    actions,fixture.q,origin=origin,checkpoint=checkpoint,verification_candidates=history):
    replay=H.attempt_replay(executor,fixture.q,checkpoint['context'],fixture.model)
   self.assertIsNone(replay['final_answer']);self.assertIsNone(replay['used'])
   if changed:self.assertFalse(getattr(executor,'support_field_reviews',{}))
   else:
    self.assertEqual(replay['draft_recovery']['verification_candidates']['source_packet_candidates'],1)
    self.assertEqual(len(B.bound_field_support(p,executor.support_field_reviews[digest(p['text'])])),2)
 def test_later_program_fault_retires_earlier_positive(self):
  from clinical_core.agere.clinical.mixed_native_work import task_identity
  positive={'schema':'mixed-native-work/1','task_identity':task_identity(),'key':'authored-program','supported_fields':{'value':{'state':'supported'}}}
  fault={**positive,'supported_fields':{},'typed_review':{'judgments':{'f':{'label':'wrong_program','decided_by':'authored'}}}}
  tools=SimpleNamespace();r=H.restore_verification_candidates(tools,[{'shared_program_reviews':[positive]},{'shared_program_reviews':[fault]}])
  self.assertFalse(tools.program_field_reviews);self.assertEqual(r['retired_program_candidates'],1)
 def test_legacy_or_changed_task_proof_is_preserved_but_not_reactivated(self):
  p=packet();r=B.localize_support_failure(p,fields,as_of=p['date'],ledger=None)['field_review']
  r.pop('task_identity');history=[{'support':{'packets':[{'field_review':r}]}}];before=copy.deepcopy(history)
  tools=SimpleNamespace();out=H.restore_verification_candidates(tools,history)
  self.assertEqual(out['legacy_or_changed_task_candidates_withheld'],1)
  self.assertFalse(tools.support_field_reviews);self.assertEqual(B.bound_field_support(p,r),[])
  self.assertEqual(history,before)
 def test_restored_program_reuses_exact_task_but_changed_task_is_withheld(self):
  from clinical_core.agere.clinical import mixed_native_work as M
  from test_mixed_native_work import mixed,judge,ASOF
  a,c=mixed();branches=M.candidates(a,c,ASOF)
  reviews=M.review('Show sources',branches,judge=judge,ledger=None,cache={})
  tools=SimpleNamespace();H.restore_verification_candidates(tools,[{'shared_program_reviews':reviews}])
  reused=M.review('Show sources',branches,judge=Mock(side_effect=AssertionError('repeated program review')),
   ledger=None,cache=tools.program_field_reviews)
  self.assertTrue(reused[0]['reused'])
  reviews[0]['task_identity']='different-task';tools=SimpleNamespace()
  receipt=H.restore_verification_candidates(tools,[{'shared_program_reviews':reviews}])
  self.assertFalse(tools.program_field_reviews)
  self.assertEqual(receipt['legacy_or_changed_task_candidates_withheld'],1)
 def test_unbound_recovery_cannot_supply_candidates(self):
  with self.assertRaisesRegex(ValueError,'exact owned draft'),H.owned_recovery([], 'question',verification_candidates=[]):pass

if __name__=='__main__':unittest.main()
