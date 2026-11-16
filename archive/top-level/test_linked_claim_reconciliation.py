import copy,json,unittest
from unittest.mock import patch
from pathlib import Path
import test_case_passes as F
from clinical_core.agere.clinical import linked_claim_reconciliation as L,linked_claim_gateway as G
from clinical_core.contracts import digest

class LinkedClaims(unittest.TestCase):
 raw=F.CasePasses.raw
 answer=F.CasePasses.answer
 sender=F.CasePasses.sender
 run_synthesis=F.CasePasses.run_synthesis
 def setUp(self):
  F.CasePasses.setUp(self);self.parent=self.run_synthesis();self.parent['dependencies']['availability_dependency']='received'
  self.refs=L.R.manifest(self.parent,self.row)
  self.received={'dependency':'received','records':{r['record_id']:r for r in self.records}}
  self.job=L.request(self.parent,self.row,self.refs,self.received)
  self.original=copy.deepcopy(self.parent)
  for target,kw in [(L.recovery,{'install':lambda:None})]:
   p=patch.multiple(target,**kw);p.start();self.addCleanup(p.stop)
  for obj,name,value in [(L.S,'inspect',self.parent),(L.S,'context',self.row),(L.A,'snapshot',self.received)]:
   p=patch.object(obj,name,return_value=value);p.start();self.addCleanup(p.stop)
 def result(self):
  excerpt=next(iter(self.job['excerpt_pool']))
  return {'findings':[{'id':'f1','kind':'documented_account','text':'The supplied source describes an unconfirmed request.','excerpt_ids':[excerpt],'unresolved':['Completion is not established.']}],
   'claim_reviews':[{'input_id':key,'status':'unassessed','finding_ids':[],'reason':'The excerpt cannot establish completion.'} for key in self.job['finding_ids']],
   'unanswered':['Full source meaning has not been revalidated.'],'review_steps':[{'kind':'review_source_meaning','finding_ids':['f1'],'question':'What does the source establish?'}]}
 def parsed(self,value=None):return L.parse(self.raw(self.job['body'],value or self.result()),self.job,self.refs)
 def fake_send(self,body,ledger,out,*,parse):
  raw=self.raw(body,self.result());result=parse(raw);key=G.request_key(body);ledger.reserve(key,'claude',body['model']);ledger.finish(key,response=raw)
  return result,{'cache_hit':False,'request_hash':key,'usage':{}}
 def run_claims(self):
  with patch.object(G,'send',side_effect=self.fake_send),patch.object(L.C,'token_check',return_value={'input_tokens':750,'provider_count_cached':False}):
   return L.synthesize(self.path,self.parent['id'],self.ledger,allow_live=True)
 def test_all_claim_text_qualifications_excerpts_and_edges_retained(self):
  w=json.loads(self.job['body']['messages'][0]['content']);claims={x[0]:x for x in w['claims']['rows']}
  for p in self.parent['passes']:
   for f in p['result']['findings']:
    self.assertEqual(claims[f['id']][3:5],[f['text'],f['unresolved']])
    self.assertEqual([self.job['excerpt_pool'][k]['quote'] for k in claims[f['id']][5]],[c['quote'] for c in f['citations']])
  self.assertEqual(w['typed_source_links'],self.parent['dependencies']['edges'])
  self.assertEqual(self.parent,self.original)
 def test_linked_transport_uses_same_effective_model_as_its_request(self):
  from clinical_core.agere import model_policy as policy
  for scope,expected in [(policy.first_rung,policy.SONNET),(policy.question_path,policy.HAIKU),(policy.deliverable,policy.OPUS)]:
   with scope():
    job=L.request(self.parent,self.row,self.refs,self.received)
    self.assertEqual(job['body']['model'],expected)
    G.validate(job['body'])
    L.parse(self.raw(job['body'],self.result()),job,self.refs)
 def test_canonical_identity_explicit_and_wrong_patient_rejected(self):
  w=json.loads(self.job['body']['messages'][0]['content']);self.assertEqual(w['canonical_patient'],'p');self.assertIn('raw source_patient_id',self.job['body']['system'])
  r=copy.deepcopy(self.row);next(iter(r['model_context']['sources'].values()))['source']['patient_id']='other'
  with self.assertRaisesRegex(ValueError,'Canonical'):L.request(self.parent,r,self.refs,self.received)
 def test_request_bound_does_not_trim_or_dispatch(self):
  parent=copy.deepcopy(self.parent);parent['passes'][0]['result']['findings'][0]['text']='x'*40000
  with patch.object(G,'send') as send:
   with self.assertRaisesRegex(ValueError,'32768'):L.request(parent,self.row,self.refs,self.received)
   send.assert_not_called()
 def test_complete_declared_source_inventory_has_no_record_count_cap(self):
  row=copy.deepcopy(self.row);sources=row['model_context']['sources']
  template=copy.deepcopy(next(iter(sources.values())))
  for n in range(133-len(sources)):
   key='additional-'+str(n);item=copy.deepcopy(template);item['source']['record_id']=key;sources[key]=item
  job=L.request(self.parent,row,self.refs,self.received)
  self.assertEqual(len(job['source_records']),133)
  self.assertEqual(set(job['source_records']),set(sources))
 def test_subscription_keeps_every_cross_group_claim_and_duplicate_guard(self):
  parent=copy.deepcopy(self.parent);template=parent['passes'][0]['result']['findings'][0]
  parent['passes'][0]['result']['findings']=[{**copy.deepcopy(template),'id':'g'+str(n)}for n in range(133)]
  parent['passes']=parent['passes'][:1]
  with patch.object(G,'maximum_request_bytes',return_value=None):
   job=L.request(parent,self.row,self.refs,self.received)
   self.assertEqual(len(job['finding_ids']),133)
   rows=json.loads(job['body']['messages'][0]['content'])['claims']['rows']
   self.assertEqual([r[0]for r in rows],['g'+str(n)for n in range(133)])
   self.assertTrue(all(r[3:5]==[template['text'],template['unresolved']]for r in rows))
   parent['passes'][0]['result']['findings'][-1]['id']='g0'
   with self.assertRaisesRegex(ValueError,'Unique'):L.request(parent,self.row,self.refs,self.received)
  parent['passes'][0]['result']['findings'][-1]['id']='g132'
  with patch.object(G,'maximum_request_bytes',return_value=32768):
   with self.assertRaisesRegex(ValueError,'bounded'):L.request(parent,self.row,self.refs,self.received)
 def test_missing_duplicate_unknown_claim_dispositions_rejected(self):
  for kind in ['missing','duplicate','unknown']:
   v=self.result()
   if kind=='missing':v['claim_reviews'].pop()
   elif kind=='duplicate':v['claim_reviews'][-1]=v['claim_reviews'][0]
   else:v['claim_reviews'][0]['input_id']='invented'
   with self.assertRaises(ValueError):self.parsed(v)
 def test_unknown_or_duplicate_excerpt_rejected(self):
  for ids in [['invented'],['e1','e1']]:
   v=self.result();v['findings'][0]['excerpt_ids']=ids
   with self.assertRaisesRegex(ValueError,'excerpt'):self.parsed(v)
 def test_parent_quote_tampering_rejected(self):
  p=copy.deepcopy(self.parent);p['passes'][0]['result']['findings'][0]['citations'][0]['quote']='absent'
  with self.assertRaisesRegex(ValueError,'exact unique'):L.request(p,self.row,self.refs,self.received)
 def test_supported_claim_needs_existing_conclusion(self):
  v=self.result();v['claim_reviews'][0]['status']=L.STATES[0]
  with self.assertRaisesRegex(ValueError,'need a consolidated'):self.parsed(v)
  v['claim_reviews'][0]['finding_ids']=['other']
  with self.assertRaisesRegex(ValueError,'Unknown'):self.parsed(v)
  v['claim_reviews'][0]['finding_ids']=['f1'];self.parsed(v)
 def test_wrong_model_incomplete_and_invalid_conflict_rejected(self):
  for key,val in [('model','unexpected'),('stop_reason','max_tokens')]:
   r=self.raw(self.job['body'],self.result());r[key]=val
   with self.assertRaises(ValueError):L.parse(r,self.job,self.refs)
  v=self.result();v['findings'][0]['kind']='conflicting_accounts'
  with self.assertRaisesRegex(ValueError,'two distinct'):self.parsed(v)
 def test_bare_and_unknown_prose_unit_references_rejected(self):
  for ref in ['u1','p99:u1']:
   v=self.result();v['claim_reviews'][0]['reason']='Review '+ref
   with self.assertRaises(ValueError):self.parsed(v)
 def test_success_separates_history_preserves_coverage_and_replays(self):
  a=self.run_claims();self.assertEqual(len(a['findings']),1);self.assertEqual(a['retained_source_findings'],self.job['original_findings']);self.assertEqual(a['passes'],self.parent['passes'])
  self.assertEqual(a['coverage']['unassessed_units'],self.parent['coverage']['unassessed_units']);self.assertTrue(a['coverage']['all_source_claims_accounted'])
  self.assertTrue(all(f['stage']=='r1' for f in a['findings']))
  with patch.object(G,'send') as send:
   b=L.synthesize(self.path,self.parent['id'],self.ledger);self.assertTrue(b['cache_hit']);self.assertEqual(b['new_paid_api_calls'],0);send.assert_not_called()
 def test_no_silent_failed_retry(self):
  key=G.request_key(self.job['body']);self.ledger.reserve(key,'claude',self.job['body']['model']);self.ledger.finish(key,error='fixture')
  with patch.object(G,'send') as send,patch.object(L.C,'token_check') as tokens:
   with self.assertRaisesRegex(ValueError,'no silent retry'):L.synthesize(self.path,self.parent['id'],self.ledger,allow_live=True)
   send.assert_not_called();tokens.assert_not_called()
 def test_disabled_live_no_calls(self):
  with patch.object(G,'send') as send,patch.object(L.C,'token_check') as tokens:
   with self.assertRaisesRegex(ValueError,'disabled'):L.synthesize(self.path,self.parent['id'],self.ledger)
   send.assert_not_called();tokens.assert_not_called()
 def test_parent_or_result_change_invalidates(self):
  a=self.run_claims();a['findings'][0]['text']='changed';L.location(self.path,a['id']).write_text(json.dumps(a))
  with self.assertRaisesRegex(ValueError,'integrity'):L.inspect(self.path,a['id'])
 def test_source_race_does_not_save_answer(self):
  original=self.fake_send
  def race2(*args,**kwargs):
   result=original(*args,**kwargs);L.S.inspect.return_value={**self.parent,'question':'changed'};return result
  with patch.object(G,'send',side_effect=race2),patch.object(L.C,'token_check',return_value={'input_tokens':750,'provider_count_cached':False}):
   with self.assertRaisesRegex(ValueError,'changed during'):L.synthesize(self.path,self.parent['id'],self.ledger,allow_live=True)
  self.assertEqual(len(list((self.path/'linked-claim-reconciliation').glob('*-progress.json'))),1)
 def test_gateway_rejects_nonstage_and_missing_native_receipt(self):
  body=copy.deepcopy(self.job['body']);body['messages'][0]['content']='{}'
  with self.assertRaises(ValueError):G.validate(body)
  with patch.object(G.urllib.request,'urlopen') as send:
   with self.assertRaises(FileNotFoundError):G.send(self.job['body'],self.ledger,self.path,parse=lambda r:r)
   send.assert_not_called()
 def test_gateway_failed_row_and_cached_replay_do_not_network(self):
  key=G.request_key(self.job['body']);self.ledger.reserve(key,'claude',self.job['body']['model']);self.ledger.finish(key,error='failure')
  with patch.object(G.urllib.request,'urlopen') as send:
   with self.assertRaisesRegex(ValueError,'no silent retry'):G.send(self.job['body'],self.ledger,self.path,parse=lambda r:r)
   send.assert_not_called()

 def test_gateway_cached_success_replays_without_tokens_or_network(self):
  raw={**self.raw(self.job['body'],self.result()),'usage':{'input_tokens':100,'output_tokens':100}}
  key=G.request_key(self.job['body']);self.ledger.reserve(key,'claude',self.job['body']['model']);self.ledger.finish(key,response=raw)
  with patch.object(G.urllib.request,'urlopen') as send:
   result,usage=G.send(self.job['body'],self.ledger,self.path,parse=lambda raw:L.parse(raw,self.job,self.refs))
   self.assertTrue(usage['cache_hit']);send.assert_not_called();self.assertEqual(len(result['claim_reviews']),len(self.job['finding_ids']))
 def test_gateway_retains_returned_usage_on_invalid_response(self):
  import io
  body=self.job['body'];tokenkey=digest({k:body[k] for k in ('model','messages','system','output_config')})
  (self.path/(tokenkey+'-tokens.json')).write_text(json.dumps({'request_hash':tokenkey,'input_tokens':100}))
  raw={**self.raw(body,{'bad':'schema'}),'usage':{'input_tokens':100,'output_tokens':50}}
  with patch.dict(G.os.environ,{'ANTHROPIC_API_KEY':'test-only'}),patch.object(G.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(raw).encode())):
   with self.assertRaises(RuntimeError):G.send(body,self.ledger,self.path,parse=lambda raw:L.parse(raw,self.job,self.refs))
  key=G.request_key(body);self.assertEqual(self.ledger.db.execute('SELECT status FROM calls WHERE id=?',(key,)).fetchone()[0],'failed')
  self.assertEqual(json.loads((self.path/(key+'.json')).read_text())['response'],raw)
  with patch.object(G.urllib.request,'urlopen') as send:
   with self.assertRaisesRegex(ValueError,'no silent retry'):G.send(body,self.ledger,self.path,parse=lambda r:r)
   send.assert_not_called()
 def test_qualified_claim_links_are_renamed_and_current_steps_use_consolidated_ids(self):
  original=self.result
  def supported():
   value=original();value['claim_reviews'][0].update(status=L.STATES[1],finding_ids=['f1']);return value
  with patch.object(self,'result',side_effect=supported):a=self.run_claims()
  self.assertEqual(a['claim_reviews'][0]['finding_ids'],['r1_f1'])
  self.assertEqual(a['review_steps'][0]['finding_ids'],['r1_f1'])
 def test_system_unassessed_coverage_never_promoted(self):
  self.parent['coverage'].update(system_unassessed_units=5,model_accounted_units=11)
  a=self.run_claims();self.assertEqual(a['coverage']['system_unassessed_units'],5);self.assertEqual(a['coverage']['model_accounted_units'],11)
  self.assertFalse(a['coverage']['source_meaning_revalidated'])

 def test_parent_with_result_digest_checks_exact_original_preimage(self):
  from clinical_core.agere.clinical import linked_claim_integrity as fix
  self.parent['result_digest']='parent-digest'
  a=self.run_claims()
  self.assertEqual(fix.inspect(self.path,a['id']),a)
  a['findings'][0]['text']='tampered';L.location(self.path,a['id']).write_text(json.dumps(a))
  with self.assertRaisesRegex(ValueError,'integrity'):fix.inspect(self.path,a['id'])
 def test_parent_without_digest_uses_original_integrity_recipe(self):
  from clinical_core.agere.clinical import linked_claim_integrity as fix
  a=self.run_claims();self.assertEqual(fix.inspect(self.path,a['id']),a)

if __name__=='__main__':unittest.main()
