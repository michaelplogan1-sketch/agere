import copy,json,sqlite3,unittest
from unittest.mock import patch
import test_analysis_graph as F
from clinical_core.agere.clinical import cardiometabolic_review as C, program_work as W, cardiometabolic_options as O, cardiometabolic_components as K
from clinical_core.agere.clinical import option_evidence_registry as Registry,option_component_registry

class CardiometabolicReview(unittest.TestCase):
 def setUp(self):
  F.GraphExecution.setUp(self);self.addCleanup(self.temp.cleanup)
  self.r={'record_id':'bp-p0','patient_id':'p0','source_table':'vitals','synthetic':True,'identity_status':'matched','event_date':'2026-07-01','event_time':'2026-07-01T12:00:00Z','available_date':'2026-07-02','encounter_id':'e1','blood_pressure_systolic':140,'blood_pressure_diastolic':80,'units':{'blood_pressure_systolic':'mmHg','blood_pressure_diastolic':'mmHg'}}
  self.put(self.r)
 def put(self,r):
  with sqlite3.connect(self.root/'stack.sqlite3') as db:db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?,?)',(r['record_id'],r['patient_id'],r['source_table'],json.dumps(r)))
 def assess(self,rr):return C.assess('p0',rr,'2026-08-01')['bp']
 def test_paired_raw_baseline_and_exact_mean(self):
  r={**self.r,'record_id':'b2','blood_pressure_systolic':141,'blood_pressure_diastolic':81,'event_time':'2026-07-01T13:00:00Z'}
  b=self.assess([self.r,r]);d=b['daily_summaries'][0];self.assertEqual(d['mean_systolic'],{'numerator':281,'denominator':2,'display':140.5});self.assertEqual(len(b['observations']),2);self.assertFalse(b['diagnosis_established'])
 def test_separate_undated_values_never_pair(self):
  rr=[{**self.r,'record_id':str(i),'event_date':None,'measurement':m,'value':v} for i,(m,v) in enumerate([('bp_systolic',140),('bp_diastolic',80)])]
  for r in rr:del r['blood_pressure_systolic'];del r['blood_pressure_diastolic']
  b=self.assess(rr);self.assertEqual(b['counts']['included_records'],0);self.assertEqual(b['counts']['excluded_by_reason'],{'not_a_complete_pair_in_one_record':2})
 def test_date_and_units_not_substituted(self):
  for change,reason in [({'event_date':None,'recorded_date':'2026-07-01'},'source_event_date_unknown_or_unusable'),({'units':{}},'units_not_explicit_mmHg')]:
   self.assertEqual(self.assess([{**self.r,**change}])['observations'][0]['excluded_reason'],reason)
 def test_invalid_numeric_pair_rejected_from_arithmetic(self):
  for v in [True,'140',-1,70,None]:
   b=self.assess([{**self.r,'blood_pressure_systolic':v}]);self.assertEqual(b['counts']['included_records'],0)
 def test_nonfinite_is_rejected_at_projection(self):
  with self.assertRaises(ValueError):self.assess([{**self.r,'blood_pressure_systolic':float('nan')}])
 def test_duplicate_encounter_time_excludes_both(self):
  b=self.assess([self.r,{**self.r,'record_id':'b2','blood_pressure_systolic':142}]);self.assertEqual(b['counts']['excluded_by_reason'],{'multiple_source_records_for_same_encounter_time':2})
 def test_no_pooling_across_days_and_descriptive_delta(self):
  b=self.assess([self.r,{**self.r,'record_id':'b2','event_date':'2026-07-11','event_time':'2026-07-11T12:00:00Z','blood_pressure_systolic':135}]);self.assertEqual(len(b['daily_summaries']),2);self.assertEqual(b['difference_between_last_two_observed_days']['systolic']['display'],-5);self.assertEqual(b['difference_between_last_two_observed_days']['elapsed_days'],10)
 def test_unknown_measurement_context_stays_missing(self):
  r=C.assess('p0',[self.r],'2026-08-01');self.assertIsNone(r['sources'][0]['fields']['device_type']);self.assertTrue(all(q['status']=='unresolved' for q in r['questions']))
 def test_false_response_and_unknown_lab_status_retained(self):
  response={**self.r,'record_id':'response','source_table':'condition_assessments','condition':'diabetes_treatment_response','home_glucose_log_available':False}
  lab={**self.r,'record_id':'a1c','source_table':'labs','test':'HbA1c','value':7.4,'unit':'%'}
  r=C.assess('p0',[response,lab],'2026-08-01');self.assertIs(r['latest_source_accounts']['diabetes_response']['fields']['home_glucose_log_available']['value'],False);self.assertEqual(r['latest_source_accounts']['glycemic']['fields']['result_status']['state'],'not_recorded')
 def test_same_date_glycemic_conflicts_are_not_resolved(self):
  lab={**self.r,'record_id':'a1c','source_table':'labs','test':'HbA1c','value':7.4,'unit':'%'}
  a=C.assess('p0',[lab,{**lab,'record_id':'a1c2','value':8}],'2026-08-01')['latest_source_accounts']['glycemic'];self.assertEqual(a['fields']['value']['state'],'conflicting')
 def test_wrong_patient_future_duplicate_and_unknown_source_fail(self):
  for rr in [[{**self.r,'patient_id':'p1'}],[{**self.r,'available_date':'2027-01-01'}],[self.r,self.r],[{**self.r,'source_table':'notes'}]]:
   with self.assertRaises(ValueError):self.assess(rr)
 def test_immutable_review_staleness(self):
  r=C.prepare(self.root,{'patient_id':'p0'});self.assertEqual(C.inspect(self.root,r['id']),r);self.put({**self.r,'blood_pressure_systolic':145})
  with self.assertRaisesRegex(ValueError,'changed'):C.inspect(self.root,r['id'])
 def test_other_patient_change_does_not_change_selected_review(self):
  r=C.prepare(self.root,{'patient_id':'p0'});self.put({**self.r,'record_id':'p1bp','patient_id':'p1'});self.assertEqual(C.inspect(self.root,r['id']),r)
 def test_no_sources_preserves_unknown_need(self):
  r=C.prepare(self.root,{'patient_id':'p1'});self.assertFalse(r['review']['source_review_candidate']);self.assertEqual(len(r['review']['questions']),7)
 def test_real_work_evidence_and_idempotent_save(self):
  r=C.prepare(self.root,{'patient_id':'p0'});p=C.prepare_work(self.root,{'review_id':r['id']});self.assertEqual(W.materialize(self.root,p['proposal_id'],'Synthetic test')['changes']['created'],1);self.assertEqual(W.materialize(self.root,p['proposal_id'],'Synthetic test')['changes']['unchanged'],1)
  i=W.list_items(self.root,program=C.PROGRAM)['items'][0];a=W.inspect_item(self.root,i['id'])['assessment'];self.assertEqual(i['state'],'review_needed');self.assertIsNone(i['owner']);self.assertEqual([e['record_id'] for e in a['evidence']],['bp-p0']);self.assertFalse(a['clinical_eligibility_established'])
 def test_context_requires_exact_selected_records(self):
  r=C.prepare(self.root,{'patient_id':'p0'})
  for ids in [[],['bp-p0','bp-p0'],['other']]:
   with self.assertRaises(ValueError):C.context(self.root,{'review_id':r['id'],'record_ids':ids})
 def test_prepare_rejects_injected_authority(self):
  with self.assertRaises(ValueError):C.prepare(self.root,{'patient_id':'p0','diagnosis':'hypertension'})
 def test_prior_bundles_unchanged_and_current_component_mapping(self):
  option_component_registry.install();before=Registry.bundles();K.install();after=Registry.bundles();option_component_registry.install();K.install();self.assertEqual(after,Registry.bundles());self.assertTrue(all(x in after for x in before));self.assertEqual(len(after),len(before)+1)
  b=O.bundles()[0];c={'option_evidence':b['evidence'],'reference':b['reference'],'alternatives':b['evidence']['alternatives']};m=K.mapping(c);self.assertEqual(len(m['alternatives']['home_team_review']),3);self.assertEqual(b['evidence']['estimates'],[]);self.assertEqual(len(b['reference']['questions']),7)
  bad=copy.deepcopy(c);bad['option_evidence']['registry_entry']['bundle_hash']='changed'
  with self.assertRaises(ValueError):K.mapping(bad)
if __name__=='__main__':unittest.main()
