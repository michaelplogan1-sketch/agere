import copy
import json
import sqlite3
import unittest
from unittest.mock import patch
import test_evidence_threads as fixture
import test_case_passes as pass_fixture
from test_warehouse_interpretation import record, ASOF
from clinical_core.contracts import digest
from clinical_core.agere.gateway import BudgetLedger
from clinical_core.agere.clinical import record_context as rc, record_synthesis as rs, evidence_needs as needs, context_builder as ctx, case_synthesis as single

class RecordContext(unittest.TestCase):
 setUpBase=fixture.Threads.setUp
 put=fixture.Threads.put
 chain=fixture.Threads.chain
 def setUp(self):
  self.setUpBase();self.chain();self.put(record('extra','medication_information_requests'),record('other','financial_screens'))
  ev=sqlite3.connect(self.path/'events.sqlite');ev.execute('CREATE TABLE events(id TEXT,patient TEXT,payload TEXT)');ev.close()
  for p in [patch.object(ctx,'dependency_identity',return_value=('parent',{},None,[])),patch.object(rc.longitudinal,'connect',side_effect=lambda path:(sqlite3.connect(self.path/'events.sqlite'),{}))]:p.start();self.addCleanup(p.stop)
 def create(self,ids=None):return rc.create(self.path,'P-1','What does this request document?',ids or ['a','b','c','d'])
 def test_excludes_same_category_records_without_omitting_selected_units(self):
  x=self.create();self.assertEqual(set(x['model_context']['sources']),{'a','b','c','d'})
  self.assertEqual(x['record_scope']['excluded_from_selected_categories'],1);self.assertEqual(x['record_scope']['excluded_records'],2)
  self.assertFalse(x['record_scope']['complete_categories']);self.assertTrue(x['dispatch']['source_review_allowed'])
  self.assertEqual(x['budget']['used'],len(ctx.encode(x['model_context'])))
  self.assertEqual(rc.verified(self.path,x['id']),x)
 def test_unknown_wrong_patient_and_duplicate_references_rejected(self):
  self.put(record('private','medication_information_requests',patient_id='P-2',requested_information='PRIVATE'))
  for ids in [[],['private'],['unknown'],['a','a']]:
   with self.assertRaises(ValueError):rc.create(self.path,'P-1','What is documented?',ids)
 def test_changed_selected_or_excluded_source_invalidates(self):
  for rid in ['a','extra']:
   x=self.create();self.put(record(rid,'medication_information_requests',requested_information='Revised '+rid))
   with self.assertRaisesRegex(ValueError,'changed'):rc.inspect(self.path,x['id'])
 def test_tampered_saved_scope_rejected(self):
  x=self.create();x['record_scope']['excluded_records']=0;rc.location(self.path,x['id']).write_text(json.dumps(x))
  with self.assertRaisesRegex(ValueError,'identity'):rc.inspect(self.path,x['id'])
 def test_whole_unit_overflow_cannot_dispatch(self):
  self.put(record('big','medication_information_requests',requested_information='No completion documented. '*4000))
  x=self.create(['big']);self.assertFalse(x['dispatch']['source_review_allowed']);self.assertTrue(x['omissions'])
  with self.assertRaisesRegex(ValueError,'Complete'):rc.verified(self.path,x['id'])
 def test_hidden_truth_not_in_transported_context(self):
  self.put(record('a','medication_information_requests',truth={'secret':'NO TRANSPORT'}))
  x=self.create(['a']);self.assertNotIn('NO TRANSPORT',json.dumps(x))
 def test_need_states_never_resolve_meaning_or_claim_absent_care(self):
  rows=rc.received(self.path,'P-1')[0];a={'record_scope':{'record_ids':['a']},'dependencies':{'record_passes':{'a':[1,2]}}}
  loc=needs.locate(rows,a,[{'record_id':r,'field':f} for r,f in [('a',''),('extra',''),('absent',''),('a','nonexistent')]])
  self.assertEqual([x['status'] for x in loc],['available_in_selected_scope','received_outside_selected_scope','not_received_for_patient','field_not_received'])
  self.assertEqual(loc[0]['source_passes'],[1,2]);self.assertTrue(all(not x['question_resolved'] and not x['care_not_received'] for x in loc))
 def test_exact_binding_and_context_closure_do_not_bind_abbreviations(self):
  row=self.create();a={'id':'a'*64,'patient_id':'P-1','context_id':row['id'],'question':'What is documented?','findings':[],
   'review_steps':[{'id':'need1','finding_ids':[],'question':'Inspect record a.'},{'id':'need2','finding_ids':[],'question':'Inspect a… or an unknown outside event.'}]}
  with patch.object(needs.source_synthesis,'inspect',return_value=a):
   r=needs.prepare(self.path,a['id']);self.assertEqual(r['needs'][0]['selectors'][0]['status'],'available_in_selected_scope')
   self.assertFalse(r['needs'][1]['selectors'])
   # A prefix isn't exact even when only one source starts that way.
   a['review_steps'][1]['question']='Inspect extr….';r=needs.prepare(self.path,a['id']);self.assertFalse(r['needs'][1]['selectors'])
   c=needs.context_for_need(self.path,r['id'],'need1');self.assertEqual(c['record_scope']['record_ids'],['a','b','c','d']);self.assertFalse(c['meaning_resolved']);self.assertTrue((self.path/'evidence-followups'/(c['id']+'.json')).exists())
   with self.assertRaisesRegex(ValueError,'Exact available'):needs.context_for_need(self.path,r['id'],'need2')
 def test_source_pass_unit_alias_binds_by_its_own_manifest_only(self):
  self.put(record('a','medication_information_requests',text='Received request needs review.'))
  row=self.create();u=next(v for v in row['model_context']['evidence'] if v['kind']=='narrative')
  a={'id':'a'*64,'patient_id':'P-1','context_id':row['id'],'question':'What is documented?','findings':[],
   'passes':[{'pass_number':1,'result':{'unit_reviews':[{'unit':'u6','source_unit_id':u['id']}]}}],
   'review_steps':[{'id':'n1','finding_ids':['p1_f1'],'question':'What does u6 document?'},
    {'id':'n2','finding_ids':['r1_f1'],'question':'What does pass 1 mean by u6?'},
    {'id':'n3','finding_ids':['p1_f1','p2_f1'],'question':'What does u6 mean?'},
    {'id':'n4','finding_ids':['p1_f1'],'question':'What does pass 2 mean by u6?'}]}
  with patch.object(needs.source_synthesis,'inspect',return_value=a):
   r=needs.prepare(self.path,a['id'])
  bound=r['needs'][0]['unit_alias_bindings'][0];self.assertEqual(bound['source_unit_id'],u['id']);self.assertEqual(bound['field'],u['field'])
  self.assertEqual(r['needs'][1]['selectors'],[]);self.assertEqual(r['needs'][2]['selectors'],[]);self.assertEqual(r['needs'][3]['selectors'],[])
  self.assertEqual(r['needs'][0]['semantic_resolution'],'pending')
 def test_present_but_untransported_document_field_does_not_claim_model_access(self):
  doc=record('doc','documents',text='Unaccepted document text',extracted_text='Received extraction');doc.pop('_warehouse_source');doc.pop('_warehouse_fields');self.put(doc)
  row=self.create(['doc']);a={'id':'a'*64,'patient_id':'P-1','context_id':row['id'],'question':'What is documented?','findings':[],'review_steps':[]}
  with patch.object(needs.source_synthesis,'inspect',return_value=a):
   r=needs.prepare(self.path,a['id'],[{'record_id':'doc','field':'text'},{'record_id':'doc','field':'extracted_text'}])
   self.assertEqual([s['status'] for s in r['explicit_selectors']],['received_field_not_transported','available_in_selected_scope'])
   with self.assertRaisesRegex(ValueError,'Exact available'):needs.context_for_need(self.path,r['id'],'explicit')
 def test_explicit_selectors_remain_distinct_from_proposed_review_questions(self):
  row=self.create();a={'id':'a'*64,'patient_id':'P-1','context_id':row['id'],'question':'What is documented?','findings':[],
   'review_steps':[{'id':'need1','finding_ids':[],'question':'Inspect record a.'}]}
  with patch.object(needs.source_synthesis,'inspect',return_value=a):
   r=needs.prepare(self.path,a['id'],[{'record_id':'extra','field':''}])
   self.assertEqual([s['record_id'] for s in r['explicit_selectors']],['extra'])
   self.assertEqual([s['record_id'] for s in r['needs'][0]['selectors']],['a'])
   c=needs.context_for_need(self.path,r['id'],'explicit');self.assertEqual(c['record_scope']['record_ids'],['extra'])
   self.assertEqual(needs.prepare(self.path,a['id'])['explicit_selectors'],[])
   for invalid in ({},False,''):
    with self.assertRaisesRegex(ValueError,'selectors'):needs.prepare(self.path,a['id'],invalid)
 def test_unavailable_selector_never_silently_dropped_when_preparing_scope(self):
  row=self.create();a={'id':'a'*64,'patient_id':'P-1','context_id':row['id'],'question':'What is documented?','findings':[],'review_steps':[]}
  with patch.object(needs.source_synthesis,'inspect',return_value=a):
   r=needs.prepare(self.path,a['id'],[{'record_id':'a','field':''},{'record_id':'absent','field':''}])
   with self.assertRaisesRegex(ValueError,'silent'):needs.context_for_need(self.path,r['id'],'explicit')

class RecordRunner(unittest.TestCase):
 def setUp(self):
  pass_fixture.CasePasses.setUp(self)
  self.row['record_scope']={'record_ids':[r['record_id'] for r in self.records],'complete_categories':False}
  self.row['missing_evidence']['record_scope']={'kind':'explicit_received_records','complete_categories':False}
  def verified(path,key):
   actual={r[0]:digest(json.loads(r[1])) for r in self.db.execute('SELECT id,payload FROM records')}
   expected={r['record_id']:digest(r) for r in self.records}
   if actual!=expected:raise ValueError('Source changed')
   return self.row
  p=patch.object(rc,'verified',side_effect=verified);p.start();self.addCleanup(p.stop)
 raw=pass_fixture.CasePasses.raw
 answer=pass_fixture.CasePasses.answer
 sender=pass_fixture.CasePasses.sender
 def run_synthesis(self,sender=None):
  with patch.object(single,'send',side_effect=sender or self.sender),patch.object(single,'token_check',return_value={'input_tokens':600,'provider_count_cached':False}):
   return rs.synthesize(self.path,self.row['id'],self.ledger,allow_live=True)
 def test_scoped_transport_replay_and_unit_accounting(self):
  seen=[]
  def send(body,*args,**kw):
   wire=json.loads(body['messages'][0]['content']);seen.append(wire)
   if wire['stage']=='source_pass':self.assertFalse(wire['record_scope']['complete_categories'])
   self.assertLessEqual(rs.passes.request_bytes(body),20000)
   return self.sender(body,*args,**kw)
  x=self.run_synthesis(send);y=rs.synthesize(self.path,self.row['id'],self.ledger)
  self.assertEqual(x['id'],y['id']);self.assertEqual(y['new_paid_api_calls'],0);self.assertTrue(y['cache_hit'])
  self.assertEqual(x['coverage']['submitted_units'],len(self.row['model_context']['evidence']));self.assertFalse(x['coverage']['complete_categories'])
  self.assertTrue(all(not s['execution_authorized'] for s in x['review_steps']))
 def test_single_pass_needs_no_reconciliation_call(self):
  self.row['model_context']['evidence']=self.row['model_context']['evidence'][:4]
  keep={u['source_ref'] for u in self.row['model_context']['evidence']};self.row['model_context']['sources']={k:v for k,v in self.row['model_context']['sources'].items() if k in keep}
  x=self.run_synthesis();self.assertEqual(x['new_paid_api_calls'],1);self.assertEqual(x['coverage']['reconciliation_groups'],0)
 def test_budget_and_disabled_live_block_before_provider(self):
  with patch.object(single,'send') as send,patch.object(single,'token_check') as count:
   with self.assertRaisesRegex(ValueError,'disabled'):rs.synthesize(self.path,self.row['id'],self.ledger)
   with patch.object(self.ledger,'receipt',return_value={'remaining_reservation_usd':.1}):
    with self.assertRaisesRegex(ValueError,'authorization'):rs.synthesize(self.path,self.row['id'],self.ledger,allow_live=True)
   send.assert_not_called();count.assert_not_called()
 def test_reconciliation_failure_can_resume_paid_source_responses(self):
  with patch.object(rs.passes,'reconciliation_jobs',side_effect=ValueError('fixture overflow')):
   with self.assertRaisesRegex(ValueError,'overflow'):self.run_synthesis()
  before=self.ledger.receipt()['calls']['validated'];x=self.run_synthesis()
  self.assertEqual(x['new_paid_api_calls'],1);self.assertEqual(self.ledger.receipt()['calls']['validated'],before+1)
 def test_source_change_during_call_never_commits_answer(self):
  def mutate(*a,**kw):
   x=self.sender(*a,**kw);self.db.execute("DELETE FROM records WHERE id='r0'");self.db.commit();return x
  with self.assertRaisesRegex(ValueError,'changed'):self.run_synthesis(mutate)
  self.assertEqual(len(list((self.path/'record-synthesis').glob('*-progress.json'))),1)
  self.assertEqual(self.ledger.receipt()['calls']['validated'],1)
 def test_failed_paid_request_never_silently_retried(self):
  def fail(body,provider,ledger,out,*,parse):
   key=digest({'contract':'context-student/1','provider':provider,'body':body});ledger.reserve(key,provider,body['model']);ledger.finish(key,error='fixture');raise RuntimeError('fixture')
  with self.assertRaises(RuntimeError):self.run_synthesis(fail)
  with patch.object(single,'send') as send:
   with self.assertRaisesRegex(ValueError,'no silent'):rs.synthesize(self.path,self.row['id'],self.ledger,allow_live=True)
   send.assert_not_called()

if __name__=='__main__':unittest.main()
