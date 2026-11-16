import copy,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from clinical_core.contracts import digest
from clinical_core.agere.clinical import medication_source_review as M,care_capabilities as C
from test_warehouse_interpretation import record,ASOF
import test_care_capabilities as fixtures

def sources():
 return [record('list','medication_lists',name='Drug A',dose='10 mg',event_date=None),
  record('report','medication_reconciliation',respondent='patient',medications=[{'name':'Drug A','reported_status':'using','dose_reported':'1 tablet'},{'name':'Drug B','reported_status':'not_using'}]),
  record('order','medication_orders',medication_name='Drug C',order_context='facility_single_dose',dose=500),
  record('admin','medication_administrations',medication_name='Drug C',order_record_id='order',administration_status='not_given',not_given_reason='patient_declined'),
  record('fill','pharmacy_dispenses',medication_name='Drug A',status='dispensed'),
  record('copy','pharmacy_history',medication_name='Drug A',original_dispense_record_id='fill'),
  record('note','notes',text='Use and directions require confirmation.')]

class LiteralReview(unittest.TestCase):
 def test_items_do_not_become_current_regimen_or_named_followup_accounts(self):
  r=M.project('P-1',sources(),ASOF);self.assertEqual(r['medication_records'],6);self.assertEqual(r['nested_medication_items'],2)
  self.assertTrue(all(not x['current_regimen_established'] for x in r['medication_accounts']))
  entries=[x for x in r['medication_accounts'] if x['field_path']];self.assertEqual(entries[1]['fields']['reported_status'],'not_using')
  self.assertEqual(entries[0]['fields']['dose_reported'],'1 tablet');self.assertEqual(r['medication_accounts'][0]['source_date'],None)
 def test_all_literal_provenance_matches_and_copy_is_not_vote(self):
  src=sources();r=M.project('P-1',src,ASOF);idx={v['record_id']:v for v in src}
  for a in r['medication_accounts']+r['narrative_accounts']:
   for e in a['evidence']:
    self.assertEqual(e['value'],M.M.value_at(idx[e['record_id']],e['field_path']));self.assertEqual(e['record_hash'],digest(idx[e['record_id']]))
  self.assertEqual(len(r['source_links']),2);self.assertTrue(all(not l['independent_corroboration'] for l in r['source_links']))
 def test_wrong_missing_and_cross_patient_links_are_not_silently_joined(self):
  s=sources();s[3]['order_record_id']='fill';s[5]['original_dispense_record_id']='absent';r=M.project('P-1',s,ASOF)
  self.assertEqual({x['reason'] for x in r['source_link_issues']},{'wrong_linked_source_type','referenced_source_not_received'})
  s[0]['patient_id']='P-2'
  with self.assertRaises(ValueError):M.project('P-1',s,ASOF)
 def test_future_receipt_duplicate_and_invalid_items(self):
  s=sources();s[0]['available_date']='2099-01-01'
  with self.assertRaises(ValueError):M.project('P-1',s,ASOF)
  s=sources();s.append(s[0])
  with self.assertRaises(ValueError):M.project('P-1',s,ASOF)
  s=sources();s[1]['medications']=[None];self.assertEqual(M.project('P-1',s,ASOF)['source_link_issues'][0]['reason'],'medication_item_invalid')
 def test_hidden_nested_values_not_exposed_even_as_provenance(self):
  s=sources();key=next(iter(M.context_builder.narrative_sources.FORBIDDEN));s[1]['medications'][0][key]='HIDDEN_TEST_SENTINEL'
  r=M.project('P-1',s,ASOF);self.assertNotIn('HIDDEN_TEST_SENTINEL',json.dumps(r))
 def test_unreceived_native_contract_and_other_source_are_separate(self):
  q={'id':'q','status':'unresolved','contract':M.Q.definition('native_medication_nonuse')}
  r=M.coverage([q],sources())[0];self.assertFalse(r['question_resolved_by_this_review'])
  self.assertEqual(r['binding_coverage'][0]['category_counts'],{'medication_followup_reports':0})
  self.assertEqual(r['binding_coverage'][0]['coverage_status'],'requested_source_categories_not_received')
 def test_named_beta_blocker_contract_remains_exact(self):
  q={'id':'q','status':'unresolved','contract':M.Q.definition('medication_report_readiness')}
  r=M.coverage([q],sources())[0];self.assertEqual(r['binding_coverage'][0]['source_contract']['concept'],'medication bb')
  self.assertEqual(r['binding_coverage'][0]['category_counts']['medication_administration'],0)
 def test_dependency_records_do_not_imply_item_account_roots_received(self):
  q={'id':'q','status':'unresolved','contract':M.Q.definition('medication_regimen_confirmation')}
  r=M.coverage([q],sources())[0]['binding_coverage'][0]
  self.assertEqual(r['coverage_status'],'requested_item_account_roots_not_received');self.assertEqual(r['category_counts']['pharmacy_dispenses'],1)
 def test_complete_bound_no_silent_truncation(self):
  s=[record(str(i),'medication_lists',name='A') for i in range(401)]
  with self.assertRaisesRegex(ValueError,'no records were dropped'):M.project('P-1',s,ASOF)

class Persistence(unittest.TestCase):
 def setUp(self):
  t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.path=Path(t.name);self.req={'portfolio_id':'p','patient_id':'P-1'}
  self.questions=[{'id':'q','status':'unresolved','contract':M.Q.definition('native_medication_nonuse')}];self.records=sources()
  def source(path,request):return self.questions,copy.deepcopy(self.records),{'snapshot':{'as_of':ASOF},'received_fingerprint':digest(self.records),'implementation':M.identity()}
  p=patch.object(M,'source',side_effect=source);p.start();self.addCleanup(p.stop)
 def test_idempotent_immutable_and_stale_retained(self):
  r=M.prepare(self.path,self.req);self.assertEqual(r,M.prepare(self.path,self.req));self.assertEqual(M.inspect(self.path,r['id']),r)
  db=M.database(self.path)
  with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM reviews')
  db.close();self.records[1]['medications'][0]['reported_status']='changed'
  with self.assertRaises(ValueError):M.inspect(self.path,r['id'])
  h=M.listing(self.path,self.req);self.assertFalse(h['reviews'][0]['current']);self.assertEqual(h['reviews'][0]['id'],r['id'])
 def test_change_between_snapshot_and_write_rejected(self):
  original=M.snapshot
  def change(*a):
   r=original(*a);self.records[0]['dose']='new';return r
  with patch.object(M,'snapshot',side_effect=change),self.assertRaisesRegex(ValueError,'changed during'):M.prepare(self.path,self.req)

class Routing(unittest.TestCase):
 def setUp(self):
  fixtures.CareCapabilities.setUp(self);oldorigin=C.origin;oldprepare=C.prepare
  self.addCleanup(setattr,C,'origin',oldorigin);self.addCleanup(setattr,C,'prepare',oldprepare);M.install()
  self.review={'id':'r'*64,'patient_id':'p1','as_of':'2026-10-10','review_questions':['Confirm current use.'],'clinical_execution_authorized':False}
  p=patch.object(M,'inspect',side_effect=lambda *a:copy.deepcopy(self.review));p.start();self.addCleanup(p.stop)
  self.req['origin']={'kind':M.KIND,'id':self.review['id'],'finding_ids':[]}
 def test_match_queue_handoff_contains_exact_source_review(self):
  plan=C.prepare(self.path,self.req);t=C.enqueue(self.path,{'plan_id':plan['id'],'option_id':plan['options'][0]['id'],'actor':'engineering test'})['task']
  out=C.task_event(self.path,{'task_id':t['id'],'expected_revision':t['revision'],'request_id':'event','action':'prepare_handoff','actor':'engineering test','note':'Local fixture'})
  self.assertEqual(out['task']['handoff']['origin']['review'],self.review);self.assertFalse(out['task']['handoff']['human_review_completed'])
  self.assertEqual(out['external_actions_executed'],0);self.review['as_of']='2026-10-11'
  with self.assertRaises(ValueError):C.inspect_plan(self.path,plan['id'])
 def test_no_titration_indication_or_temporal_mismatch_from_documentary_origin(self):
  for key,value in [('need_tags',['titration']),('origin',{'kind':M.KIND,'id':'r'*64,'finding_ids':['fake']})]:
   req={**self.req,key:value}
   with self.assertRaises(ValueError):C.prepare(self.path,req)
  req=copy.deepcopy(self.req);req['coverage']['as_of']='2026-10-09'
  with self.assertRaises(ValueError):C.prepare(self.path,req)
 def test_legacy_origins_keep_exact_identity_and_adapter_installs_once(self):
  old=C.origin;M.install();self.assertIs(C.origin,old)
  req=copy.deepcopy(self.req);req['origin']={'kind':'answer','id':'a'*64,'finding_ids':['f1']}
  self.assertEqual(C.prepare(self.path,req)['origin']['kind'],'answer')

if __name__=='__main__':unittest.main()
