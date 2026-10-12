import copy,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from clinical_core.agere.clinical import functional_review as f

class FunctionalReview(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)
  self.meta={'synthetic':True,'snapshot':{'as_of':'2026-10-10'},'source_digest':'fixture'}
  (self.path/'run.json').write_text(json.dumps(self.meta));self.db=sqlite3.connect(self.path/'stack.sqlite3');self.addCleanup(self.db.close)
  self.db.executescript("CREATE TABLE patients(id TEXT);INSERT INTO patients VALUES ('p');INSERT INTO patients VALUES ('without');CREATE TABLE records(id TEXT,patient TEXT,stream TEXT,payload TEXT)")
  self.exam=self.record('e','functional_mobility_exams',age_reported=78,timed_up_and_go_seconds=12,test_protocol=f.guidance()['review_configuration']['accepted_source_protocols'][0],homebound_assessed=False)
  self.interview=self.record('i','functional_assessments',falls_past_year_reported=1,help_with_appointments_or_medications_reported=True)
 def record(self,key,stream,**fields):return {'record_id':key,'patient_id':'p','source_table':stream,'event_date':'2026-07-01','available_date':'2026-07-02','synthetic':True,'identity_status':'matched',**fields}
 def assess(self,*records):return f.assess('p',list(records),'2026-10-10')
 def save(self,r):self.db.execute('INSERT INTO records VALUES (?,?,?,?)',(r['record_id'],r['patient_id'],r['source_table'],json.dumps(r)));self.db.commit()
 def kinds(self,r):return {s['kind'] for s in r['signals']}
 def test_threshold_is_inclusive_and_not_eligibility(self):
  r=self.assess(self.exam);self.assertIn('tug_threshold_review',self.kinds(r));self.assertEqual(r['home_health_eligibility'],'unresolved');self.assertFalse(r['clinical_execution_authorized'])
  self.exam['timed_up_and_go_seconds']=11.9;self.assertFalse(self.assess(self.exam)['source_review_candidate'])
 def test_source_age_and_protocol_must_be_supported(self):
  for changes in [{'age_reported':64},{'age_reported':None},{'test_protocol':'unmapped'},{'timed_up_and_go_seconds':True},{'timed_up_and_go_seconds':'12'},{'timed_up_and_go_seconds':0},{'timed_up_and_go_seconds':601}]:
   r=self.assess({**self.exam,**changes});self.assertNotIn('tug_threshold_review',self.kinds(r));self.assertTrue(r['unknowns'])
 def test_literal_false_never_becomes_not_assessed(self):
  r=self.assess(self.exam);v=r['latest_source_accounts']['functional_mobility_exams']['fields']['homebound_assessed'];self.assertIs(v['value'],False);self.assertEqual(v['state'],'literal_report')
  self.assertNotIn('not assessed',json.dumps(r));self.assertFalse(r['clinical_state_established'])
 def test_missing_false_and_conflicting_are_distinct(self):
  missing={k:v for k,v in self.exam.items() if k!='homebound_assessed'}
  r=self.assess(missing);self.assertEqual(r['latest_source_accounts']['functional_mobility_exams']['fields']['homebound_assessed']['state'],'not_recorded')
  r=self.assess(self.exam,{**self.exam,'record_id':'other','homebound_assessed':True});self.assertEqual(r['latest_source_accounts']['functional_mobility_exams']['fields']['homebound_assessed']['state'],'conflicting')
 def test_same_date_conflicting_test_values_do_not_trigger_threshold(self):
  r=self.assess(self.exam,{**self.exam,'record_id':'other','timed_up_and_go_seconds':10});self.assertNotIn('tug_threshold_review',self.kinds(r));self.assertTrue(any('conflicting' in x for x in r['unknowns']))
 def test_latest_reports_do_not_carry_old_support_forward(self):
  later={**self.interview,'record_id':'later','event_date':'2026-08-01','falls_past_year_reported':0,'help_with_appointments_or_medications_reported':False}
  r=self.assess(self.interview,later);self.assertFalse(r['source_review_candidate']);self.assertEqual(len(r['sources']),2);self.assertFalse(r['clinical_state_established'])
 def test_fall_and_support_reports_have_exact_field_evidence(self):
  r=self.assess(self.interview);self.assertEqual(self.kinds(r),{'reported_fall_history','reported_support_need'})
  self.assertTrue(all(s['source_date']=='2026-07-01' and s['evidence'][0]['record_id']=='i' for s in r['signals']))
 def test_future_or_undated_source_retained_without_signal(self):
  for when in [None,'invalid','2027-01-01']:
   r=self.assess({**self.exam,'event_date':when});self.assertFalse(r['source_review_candidate']);self.assertEqual(len(r['sources']),1)
 def test_no_sources_is_unknown_not_no_need(self):
  r=self.assess();self.assertEqual(r['status'],'no_selected_sources_received');self.assertEqual(r['home_health_eligibility'],'unresolved');self.assertTrue(r['unknowns'])
 def test_cross_patient_unmatched_unsynthetic_and_future_arrival_rejected(self):
  for change in [{'patient_id':'other'},{'synthetic':False},{'identity_status':'unmatched'},{'available_date':'2027-01-01'}]:
   with self.assertRaises(ValueError):self.assess({**self.exam,**change})
 def test_duplicate_identity_rejected(self):
  with self.assertRaises(ValueError):self.assess(self.exam,self.exam)
 def test_private_generator_and_unrelated_fields_excluded(self):
  r=self.assess({**self.exam,'original_text':'HIDDEN','target':'HIDDEN','future_outcome':'HIDDEN','clean_text':'HIDDEN'});self.assertNotIn('HIDDEN',json.dumps(r))
 def test_population_counts_complete_roster_and_replay(self):
  self.save(self.exam);r=f.prepare(self.path);self.assertEqual(r['counts']['population'],2);self.assertEqual(r['counts']['no_selected_sources_received'],1);self.assertEqual(r['counts']['source_review_candidates'],1)
  self.assertEqual(f.inspect(self.path,r['id']),r);self.assertEqual(f.page(self.path,r['id'])['matching_patients'],1)
  self.assertEqual(f.page(self.path,r['id'],'without')['patients'][0]['status'],'no_selected_sources_received')
 def test_deletion_or_roster_change_invalidates_population_receipt(self):
  self.save(self.exam);r=f.prepare(self.path);self.db.execute("DELETE FROM records WHERE id='e'");self.db.commit()
  with self.assertRaisesRegex(ValueError,'changed'):f.inspect(self.path,r['id'])
 def test_tampering_is_detected(self):
  self.save(self.exam);r=f.prepare(self.path);r['counts']['population']=0;f.location(self.path,r['id']).write_text(json.dumps(r))
  with self.assertRaisesRegex(ValueError,'integrity'):f.inspect(self.path,r['id'])
 def test_context_handoff_uses_only_exact_received_selection(self):
  self.save(self.exam);self.save(self.interview);r=f.prepare(self.path)
  with patch.object(f.record_context,'create',return_value={'id':'context'}) as create:
   out=f.context(self.path,{'review_id':r['id'],'patient_id':'p'});self.assertEqual(create.call_args.args[3],['e','i']);self.assertEqual(out['new_paid_api_calls'],0)
  with self.assertRaisesRegex(ValueError,'sources required'):f.context(self.path,{'review_id':r['id'],'patient_id':'without'})

if __name__=='__main__':unittest.main()
