import copy,json,sqlite3,unittest
from unittest.mock import patch
from clinical_core.agere.clinical import appointment_navigation as N, care_capabilities as C
from test_care_capabilities import CareCapabilities
from test_appointment_followthrough import pair,assess,source

class Navigation(unittest.TestCase):
 def setUp(self):
  CareCapabilities.setUp(self)
  self.cap.update(need_tags=['care_navigation'],pathway_ids=[])
  C.save_portfolio(self.path,{**self.save_request,'expected_revision':1,'capabilities':[self.cap]})
  self.source={'id':'b'*64,'patient_id':'p1','as_of':'2026-10-10','appointment':assess(pair('no_show')),'original_work_questions':['Original question remains open'],'responsibilities':N.COMPONENTS,**N.LIMITS}
  p=patch.object(N,'inspect',side_effect=lambda path,key:copy.deepcopy(self.source));p.start();self.addCleanup(p.stop)
  for name in ('origin','prepare'):self.addCleanup(setattr,C,name,getattr(C,name))
  N.install();N.install()
  self.req.update(origin={'kind':N.KIND,'id':'b'*64,'finding_ids':[]},need_tags=['care_navigation'])
 def plan(self):return C.prepare(self.path,self.req)
 def task(self):
  p=self.plan();return C.enqueue(self.path,{'plan_id':p['id'],'option_id':p['options'][0]['id'],'actor':'fixture'})['task'],p
 def fixture_inputs(self):
  t,p=self.task();ctx={'dependencies_current':True,'patient_context':None};cov={'coverage_dependencies_current':True,'coverage_receipt':None};part={'queue_participation_current':True}
  mock=patch.object(N,'inputs',side_effect=lambda path,key:copy.deepcopy((t,p,ctx,cov,part)));mock.start();self.addCleanup(mock.stop)
  return t,p,{'task_id':t['id'],'expected_revision':t['revision'],'component_bindings':[],'coordination_note':'Synthetic review proposal only'}
 def test_state_never_confers_need_consent_or_priority(self):
  p=self.plan();self.assertEqual(p['origin']['review'],self.source)
  for key in ('clinical_need_established','cause_of_no_show_established','current_patient_consent_established','clinical_priority_assigned'):self.assertFalse(p['origin']['review'][key])
  self.assertEqual(p['options'][0]['status'],'requires_verification')
 def test_no_treatment_indication_or_extra_tags(self):
  for tags in (['medication_review'],['care_navigation','hf'],[]):
   self.req['need_tags']=tags
   with self.assertRaises(ValueError):self.plan()
 def test_cutoff_must_match(self):
  self.req['coverage']['as_of']='2026-10-09'
  with self.assertRaises(ValueError):self.plan()
 def test_model_findings_rejected(self):
  self.req['origin']['finding_ids']=['risk']
  with self.assertRaises(ValueError):self.plan()
 def test_available_service_queue_replay_and_source_handoff(self):
  t,p=self.task();self.assertTrue(C.enqueue(self.path,{'plan_id':p['id'],'option_id':p['options'][0]['id'],'actor':'fixture'})['idempotent_replay'])
  r=C.task_event(self.path,{'task_id':t['id'],'expected_revision':1,'request_id':'handoff','action':'prepare_handoff','actor':'fixture','note':'local evidence only'})
  self.assertEqual(r['task']['handoff']['origin']['review'],self.source);self.assertFalse(r['task']['handoff']['external_dispatch_performed'])
 def test_mismatch_cannot_queue(self):
  self.req['coverage']['plan_id']='different';p=self.plan();self.assertEqual(p['options'][0]['status'],'blocked')
  with self.assertRaises(ValueError):C.enqueue(self.path,{'plan_id':p['id'],'option_id':p['options'][0]['id'],'actor':'fixture'})
 def test_unavailable_cannot_queue(self):
  self.cap['available']=False;C.save_portfolio(self.path,{**self.save_request,'expected_revision':2,'capabilities':[self.cap]});p=self.plan()
  with self.assertRaises(ValueError):C.enqueue(self.path,{'plan_id':p['id'],'option_id':p['options'][0]['id'],'actor':'fixture'})
 def test_legacy_origin_unchanged(self):
  r=C.origin(self.path,{'kind':'answer','id':'a'*64,'finding_ids':['f1']});self.assertEqual(r['kind'],'answer');self.assertNotIn('review',r)
 def test_source_change_invalidates_plan(self):
  p=self.plan();self.source['appointment']['status']='unresolved_followthrough'
  with self.assertRaises(ValueError):C.inspect_plan(self.path,p['id'])
 def test_proposal_defaults_four_unassigned(self):
  t,p,req=self.fixture_inputs();r=N.proposal_snapshot(self.path,req)
  self.assertEqual(len(r['unassigned_components']),4);self.assertTrue(all(not c['question_resolved'] and not c['responsibility_accepted'] for c in r['components']))
 def test_explicit_service_proposal_and_unresolved_remaining(self):
  t,p,req=self.fixture_inputs();req['component_bindings']=[{'component_key':'source_reconciliation','option_id':p['options'][0]['id'],'rationale':'Check exact source account'}];r=N.proposal_snapshot(self.path,req)
  self.assertEqual(r['components'][0]['state'],'service_proposed_requires_verification');self.assertEqual(len(r['unassigned_components']),3);self.assertFalse(r['components'][0]['responsibility_accepted'])
 def test_blocked_proposal_remains_blocked(self):
  t,p,req=self.fixture_inputs();p['options'][0]['status']='blocked'
  with patch.object(N,'inputs',return_value=(t,p,{}, {}, {})):
   req['component_bindings']=[{'component_key':'followthrough','option_id':p['options'][0]['id'],'rationale':'Mismatch must stay visible'}];r=N.proposal_snapshot(self.path,req)
  self.assertEqual(r['blocked_components'],['followthrough'])
 def test_cross_plan_or_unknown_component_rejected(self):
  t,p,req=self.fixture_inputs()
  for component,option in [('followthrough','other'),('unknown',p['options'][0]['id'])]:
   req['component_bindings']=[{'component_key':component,'option_id':option,'rationale':'fixture'}]
   with self.assertRaises(ValueError):N.proposal_snapshot(self.path,req)
 def test_duplicate_component_rejected(self):
  t,p,req=self.fixture_inputs();b={'component_key':'followthrough','option_id':p['options'][0]['id'],'rationale':'fixture'};req['component_bindings']=[b,b]
  with self.assertRaises(ValueError):N.proposal_snapshot(self.path,req)
 def test_task_revision_and_boolean_rejected(self):
  t,p,req=self.fixture_inputs()
  for rev in (2,True):
   req['expected_revision']=rev
   with self.assertRaises(ValueError):N.proposal_snapshot(self.path,req)
 def test_preview_compare_and_immutable_replay(self):
  t,p,req=self.fixture_inputs();r=N.proposal_snapshot(self.path,req);saved=N.prepare_proposal(self.path,{'proposal':req,'preview_id':r['id']});self.assertEqual(saved,r);self.assertEqual(N.inspect_proposal(self.path,r['id']),r)
  self.assertEqual(N.prepare_proposal(self.path,{'proposal':req,'preview_id':r['id']}),r)
  db=N.db_open(self.path);self.assertEqual(db.execute('SELECT count(*) FROM proposals').fetchone()[0],1)
  with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM proposals')
  db.close();req['coordination_note']='changed'
  with self.assertRaises(ValueError):N.prepare_proposal(self.path,{'proposal':req,'preview_id':r['id']})
 def test_inspect_detects_tampered_receipt(self):
  t,p,req=self.fixture_inputs();r=N.proposal_snapshot(self.path,req);r['patient_id']='other';N.store(self.path,'proposals',r)
  with self.assertRaises(ValueError):N.load(self.path,'proposals',r['id'])
 def test_patient_dependency_failure(self):
  t,p=self.task()
  with patch.object(N.P,'task_view',return_value={'dependencies_current':False}),patch.object(N.W,'task_view',return_value={'coverage_dependencies_current':True}),patch.object(N.E,'task_view',return_value={'queue_participation_current':True}):
   with self.assertRaises(ValueError):N.inputs(self.path,t['id'])
 def test_participation_dependency_failure(self):
  t,p=self.task()
  with patch.object(N.P,'task_view',return_value={'dependencies_current':True}),patch.object(N.W,'task_view',return_value={'coverage_dependencies_current':True}),patch.object(N.E,'task_view',return_value={'queue_participation_current':False}):
   with self.assertRaises(ValueError):N.inputs(self.path,t['id'])

class SourcePacket(unittest.TestCase):
 def snapshot(self,records):
  row=assess(records);review={'id':'a'*64,'patients':[row],'source':{'snapshot':{'as_of':'2026-02-15'}},'contract':N.A.CONTRACT}
  proposal={'proposal_id':'b'*64,'patients':[{'assessment_id':'c'*64,'missing_information':['Original question']}]}
  with patch.object(N.A,'inspect',return_value=review),patch.object(N.C.work,'prepare',return_value=proposal),patch.object(N,'parent_hash',return_value='parenthash'),patch.object(N.C,'origin',return_value={'kind':'assessment','id':'c'*64}):return N.source_snapshot(None,{'review_id':review['id'],'patient_id':'p'})
 def test_explicit_cancelled_is_not_no_show(self):
  r=self.snapshot(pair('cancelled'));self.assertEqual(r['appointment']['status'],'explicit_cancelled');self.assertIsNone(r['appointment']['recorded_label']);self.assertEqual(r['original_work_questions'],['Original question'])
 def test_absence_stays_unknown(self):
  r=self.snapshot(pair()[:1]);self.assertEqual(r['appointment']['status'],'no_explicit_terminal_report');self.assertIsNone(r['appointment']['recorded_label'])
 def test_conflict_retains_both_exact_reports(self):
  rr=pair()+[source('conflict','no_show',event_date='2026-02-01',available_date='2026-02-04')];r=self.snapshot(rr);self.assertEqual(r['appointment']['status'],'unresolved_followthrough');self.assertEqual(len(r['appointment']['sources']),3)
 def test_no_sources_rejected(self):
  with self.assertRaises(ValueError):self.snapshot([])
 def test_no_research_predictions_in_source_origin(self):
  r=self.snapshot(pair('no_show'));self.assertNotIn('model_score',r);self.assertFalse(r['clinical_priority_assigned']);self.assertEqual(r['appointment']['sources'],assess(pair('no_show'))['sources'])

class SourceFreshness(unittest.TestCase):
 def setUp(self):
  from test_appointment_followthrough import Storage
  Storage.setUp(self);self.addCleanup(self.tmp.cleanup)
  self.a=N.A.prepare(self.root,{'patient_ids':['p']})
  self.origin={'kind':'assessment','id':'c'*64}
  p=patch.object(N.C.work,'prepare',return_value={'proposal_id':'b'*64,'patients':[{'assessment_id':'c'*64,'missing_information':['Original question']}]});p.start();self.addCleanup(p.stop)
  p=patch.object(N.C,'origin',side_effect=lambda path,key:copy.deepcopy(self.origin));p.start();self.addCleanup(p.stop)
  self.r=N.prepare(self.root,{'review_id':self.a['id'],'patient_id':'p'})
 def test_selected_current_replay(self):self.assertEqual(N.inspect(self.root,self.r['id']),self.r)
 def test_parent_receipt_changed(self):
  N.A.location(self.root,self.a['id']).write_text('{}')
  with self.assertRaises(ValueError):N.inspect(self.root,self.r['id'])
 def test_selected_source_changed(self):
  db=sqlite3.connect(self.root/'stack.sqlite3');rr=pair('no_show')[1];db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(rr),'terminal'));db.commit();db.close()
  with self.assertRaises(ValueError):N.inspect(self.root,self.r['id'])
 def test_original_work_changed(self):
  self.origin['changed']=True
  with self.assertRaises(ValueError):N.inspect(self.root,self.r['id'])
 def test_implementation_changed(self):
  with patch.object(N,'identity',return_value='new'):
   with self.assertRaises(ValueError):N.inspect(self.root,self.r['id'])
 def test_other_patient_not_treated_as_selected_evidence(self):
  db=sqlite3.connect(self.root/'stack.sqlite3');rr={**pair()[0],'patient_id':'other','record_id':'other'};db.execute('INSERT INTO patients VALUES (?,?)',('other','{}'));db.execute('INSERT INTO records VALUES (?,?,?,?)',('other','other','appointments',json.dumps(rr)));db.commit();db.close()
  self.assertEqual(N.inspect(self.root,self.r['id']),self.r)

if __name__=='__main__':unittest.main()
