"""Engineering contracts, not a model-quality or clinical validation suite."""
import copy,json,unittest
from unittest.mock import patch
import test_default_stable_synthesis as F
from clinical_core.agere.clinical import source_meaning as M, meaning_source_stage as stage
from clinical_core.agere.clinical import meaning_source_dispatch as dispatch,source_synthesis as S
from clinical_core.agere.clinical import availability_source_stage as old,case_passes as P

class Meaning(unittest.TestCase):
 setUp=F.DefaultStable.setUp
 raw=F.DefaultStable.raw
 def packet(self):
  row,bundle,links,packets,key=stage.prepare(self.path,self.row['id']);return packets[0]
 def answer(self,body):
  raw=F.DefaultStable.answer(self,body);v=json.loads(raw['content'][0]['text'])
  if json.loads(body['messages'][0]['content'])['schema']==M.VERSION:
   for r in v['unit_reviews']:r['meaning']={'subject':'patient','time':'at_source_date','assertion':'plan_or_request','reason':'Source records an unconfirmed request at its date.'}
  return self.raw(body,v)
 sender=F.DefaultStable.sender
 def run_new(self):
  with patch.object(S,'stages',S.stages),patch.object(S,'inspect',S.inspect):
   dispatch.install()
   with patch.object(stage.single,'send',side_effect=self.sender),patch.object(stage.single,'token_check',return_value={'input_tokens':500,'provider_count_cached':False}):return S.synthesize(self.path,self.row['id'],self.ledger,allow_live=True)
 def test_all_units_once_and_transport_includes_contract(self):
  row,bundle,links,packets,key=stage.prepare(self.path,self.row['id'])
  self.assertEqual(sorted(bundle['unit_manifest']),sorted(u['id'] for _,us in packets for u in (v['unit'] for v in us.values())))
  for body,units in packets:
   self.assertLessEqual(P.request_bytes(body),20000);wire=json.loads(body['messages'][0]['content'])
   self.assertFalse(wire['canonical_identity']['narrative_subject_verified'])
   self.assertEqual({r[0] for r in wire['canonical_identity']['records']['rows']},{u['unit']['source_ref'] for u in units.values()})
 def test_prepared_request_and_strict_parser_use_effective_policy_model(self):
  from clinical_core.agere import model_policy as policy
  from clinical_core.agere.clinical import source_output_recovery as recovery
  from clinical_core.contracts import digest
  import os
  def check(expected):
   with policy.tracking()as tracked:
    body,units=self.packet()
   self.assertEqual(tracked,[])
   self.assertEqual(body['model'],expected)
   self.assertEqual(recovery.request_key(body),digest({'contract':'context-student/1','provider':'claude','body':{**body,'model':policy.claude_model(body['model'])}}))
   self.assertTrue(M.parse(self.answer(body),body,units,self.row)['unit_reviews'])
  with patch.dict(os.environ,{'AGERE_QUESTION_LADDER':'','AGERE_HAIKU_FIRST':'on','AGERE_QUESTION_OPUS':'off'}):
   check(policy.SONNET)
   with policy.question_path():
    check(policy.HAIKU)
    with policy.escalation():check(policy.SONNET)
   with policy.deliverable():check(policy.OPUS)
 def test_raw_identity_namespace_and_unsubmitted_content_not_promoted(self):
  # Canonical binding remains unchanged even if a raw source-system ID differs.
  u=next(u for u in self.row['model_context']['evidence'] if u['kind']=='structured');u['reported_fields']['source_patient_id']='raw-system-123'
  body,units=self.packet();self.assertIn('raw source_patient_id',body['system'])
  wire=json.loads(body['messages'][0]['content']);self.assertEqual(wire['canonical_identity']['patient_id'],'p')
  self.assertFalse(wire['received_source_availability']['content_of_other_records_submitted'])
 def test_foreign_canonical_patient_rejected(self):
  self.row['model_context']['sources']['r0']['source']['patient_id']='foreign'
  with self.assertRaisesRegex(ValueError,'Canonical|Cross-patient'):self.packet()
 def test_identity_status_mismatch_rejected(self):
  self.row['model_context']['sources']['r0']['source']['identity_status']='unresolved'
  with self.assertRaisesRegex(ValueError,'identity status'):self.packet()
 def test_meaning_stored_as_judgment_not_evidence(self):
  body,units=self.packet();r=M.parse(self.answer(body),body,units,self.row)
  self.assertTrue(all(x['meaning_authority']=='unvalidated_model_judgment' for x in r['unit_reviews']))
  self.assertTrue(all(x['semantic_validation']=='pending' for x in r['unit_reviews']))
 def test_missing_duplicate_invalid_and_extra_meaning_rejected(self):
  body,units=self.packet();base=json.loads(self.answer(body)['content'][0]['text'])
  changes=[lambda v:v['unit_reviews'][0].pop('meaning'),lambda v:v['unit_reviews'].append(v['unit_reviews'][0]),lambda v:v['unit_reviews'][0]['meaning'].update(subject='clinically_verified'),lambda v:v['unit_reviews'][0]['meaning'].update(authority=True),lambda v:v['unit_reviews'][0]['meaning'].update(reason='x'*201)]
  for change in changes:
   with self.subTest(change=change):
    v=copy.deepcopy(base);change(v)
    with self.assertRaises(ValueError):M.parse(self.raw(body,v),body,units,self.row)
 def test_missing_unit_coverage_not_repaired(self):
  body,units=self.packet();v=json.loads(self.answer(body)['content'][0]['text']);v['unit_reviews'].pop()
  with self.assertRaisesRegex(ValueError,'Every source unit'):M.parse(self.raw(body,v),body,units,self.row)
 def test_quote_must_remain_exact_original_source(self):
  body,units=self.packet();v=json.loads(self.answer(body)['content'][0]['text']);v['findings'][0]['citations'][0]['quote']='Verified selected source metadata'
  with self.assertRaises(ValueError):M.parse(self.raw(body,v),body,units,self.row)
 def test_new_route_and_exact_cached_replay(self):
  out=self.run_new();self.assertEqual(out['source_meaning_contract'],M.VERSION);self.assertFalse(out['meaning_judgments_validated'])
  with patch.object(S,'stages',S.stages),patch.object(S,'inspect',S.inspect):
   dispatch.install();self.assertEqual(S.inspect(self.path,out['id'])['id'],out['id'])
   with patch.object(stage.single,'send') as send:
    replay=S.synthesize(self.path,self.row['id'],self.ledger);send.assert_not_called()
    self.assertEqual(replay['id'],out['id']);self.assertEqual(replay['new_paid_api_calls'],0)
 def test_prior_pending_or_failed_route_unchanged(self):
  *_,key=old.prepare(self.path,self.row['id']);target=old.location(self.path,key);target.parent.mkdir(parents=True)
  target.with_name(key+'-progress.json').write_text(json.dumps({'status':'incomplete','error':'Prior exact payload awaiting approval'}))
  with patch.object(S,'stages',S.stages),patch.object(S,'inspect',S.inspect):
   dispatch.install();self.assertIs(S.stages(self.path,self.row['id'])[0],old)
 def test_new_failure_keeps_route_and_never_resends(self):
  from clinical_core.contracts import digest
  def fail(body,provider,ledger,out,*,parse):
   key=digest({'contract':'context-student/1','provider':provider,'body':body});ledger.reserve(key,provider,body['model']);ledger.finish(key,error='fixture');raise RuntimeError('fixture')
  with patch.object(S,'stages',S.stages),patch.object(S,'inspect',S.inspect):
   dispatch.install()
   with patch.object(stage.single,'send',side_effect=fail),patch.object(stage.single,'token_check',return_value={'input_tokens':500,'provider_count_cached':False}):
    with self.assertRaises(RuntimeError):S.synthesize(self.path,self.row['id'],self.ledger,allow_live=True)
   with patch.object(stage.single,'send') as send:
    with self.assertRaisesRegex(ValueError,'no silent'):S.synthesize(self.path,self.row['id'],self.ledger,allow_live=True)
    send.assert_not_called()
 def test_one_pass_returns_meaning_receipt_without_reconciliation(self):
  self.row['model_context']['evidence']=self.row['model_context']['evidence'][:3]
  kept={u['source_ref'] for u in self.row['model_context']['evidence']}
  self.row['model_context']['sources']={k:v for k,v in self.row['model_context']['sources'].items() if k in kept}
  self.db.executemany('DELETE FROM records WHERE id=?',[(r['record_id'],) for r in self.records if r['record_id'] not in kept]);self.db.commit()
  out=self.run_new();self.assertEqual(out['schema'],stage.VERSION);self.assertEqual(out['new_paid_api_calls'],1)
  self.assertEqual(out['coverage']['reconciliation_groups'],0);self.assertEqual(len(out['passes'][0]['result']['unit_reviews']),3)
  self.assertEqual(stage.inspect(self.path,out['id']),out)
 def test_old_completed_source_receipt_keeps_route(self):
  with patch.object(stage.single,'send',side_effect=self.sender),patch.object(stage.single,'token_check',return_value={'input_tokens':500,'provider_count_cached':False}):
   original=old.synthesize(self.path,self.row['id'],self.ledger,allow_live=True)
  with patch.object(S,'stages',S.stages),patch.object(S,'inspect',S.inspect):
   dispatch.install();self.assertIs(S.stages(self.path,self.row['id'])[0],old);self.assertEqual(S.inspect(self.path,original['id']),original)
 def test_cutover_pins_unsent_context_and_cannot_rewrite(self):
  import sqlite3
  db=sqlite3.connect(self.path/'contexts.sqlite3');db.execute('CREATE TABLE contexts(id TEXT)');db.execute('INSERT INTO contexts VALUES (?)',(self.row['id'],));db.commit();db.close()
  self.assertEqual(dispatch.pin_existing(self.path),{self.row['id']})
  self.assertEqual(dispatch.pin_existing(self.path,new_context_ids=[self.row['id']]),{self.row['id']})
  with patch.object(S,'stages',S.stages),patch.object(S,'inspect',S.inspect):
   dispatch.install();self.assertIs(S.stages(self.path,self.row['id'])[0],old)
 def test_cutover_tamper_rejected(self):
  dispatch.pin_existing(self.path);file=self.path/dispatch.PIN;row=json.loads(file.read_text());row['historical_context_ids']=['b'*64];file.write_text(json.dumps(row))
  with self.assertRaisesRegex(ValueError,'integrity'):dispatch.pinned(self.path)
 def test_meaning_tamper_invalidates_answer(self):
  out=self.run_new();parent=stage.inspect(self.path,out['parent_answer_id']) if out.get('parent_answer_id') else out
  parent['passes'][0]['result']['unit_reviews'][0]['meaning']['subject']='other_person';stage.location(self.path,parent['id']).write_text(json.dumps(parent))
  with self.assertRaisesRegex(ValueError,'integrity'):stage.inspect(self.path,parent['id'])
 def test_oversized_indivisible_unit_no_dispatch(self):
  with patch.object(M,'INSTRUCTIONS','x'*21000),patch.object(stage.single,'send') as send:
   with self.assertRaisesRegex(ValueError,'indivisible'):stage.prepare(self.path,self.row['id'])
   send.assert_not_called()

if __name__=='__main__':unittest.main()
