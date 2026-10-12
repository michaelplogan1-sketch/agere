import copy,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from clinical_core.agere.clinical import domain_review as d

class DomainReview(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)
  (self.path/'run.json').write_text(json.dumps({'synthetic':True,'source_run':'fixture','source_digest':'fixture','snapshot':{'as_of':'2026-10-10'}}))
  self.db=sqlite3.connect(self.path/'stack.sqlite3');self.addCleanup(self.db.close)
  self.db.executescript("CREATE TABLE patients(id TEXT);INSERT INTO patients VALUES ('p');INSERT INTO patients VALUES ('without');CREATE TABLE records(id TEXT,patient TEXT,stream TEXT,payload TEXT)")
  self.asthma=self.record('a','condition_assessments',condition='asthma',reliever_directions_confirmed=False,symptom_days_per_week_reported=5)
  self.spiro=self.record('s','diagnostic_test_reports',test_kind='spirometry',measurements={'FEV1_FVC':.62,'postbronchodilator_test_performed':False,'percent_predicted':None,'lower_limit_of_normal':None},test_quality='adequate',report_status='final')
  self.label=self.record('l','conditions',description='Asthma',onset='2015');self.label['event_date']=None
 def record(self,key,stream,**fields):return {'record_id':key,'patient_id':'p','source_table':stream,'event_date':'2026-07-01','available_date':'2026-07-02','recorded_date':'2026-07-01','identity_status':'matched','synthetic':True,**fields}
 def assess(self,*records,domain='respiratory'):return d.assess(domain,'p',list(records),'2026-10-10')
 def save(self,*records):
  self.db.executemany('INSERT INTO records VALUES (?,?,?,?)',[(r['record_id'],r['patient_id'],r['source_table'],json.dumps(r)) for r in records]);self.db.commit()
 def test_sources_never_establish_diagnosis_or_service(self):
  r=self.assess(self.asthma,self.spiro,self.label);self.assertTrue(r['source_review_candidate']);self.assertFalse(r['clinical_state_established']);self.assertFalse(r['clinical_eligibility_established']);self.assertFalse(r['clinical_execution_authorized'])
 def test_exact_literal_false_opens_review_question(self):
  r=self.assess(self.asthma);self.assertEqual(r['issues'][0]['kind'],'directions_unconfirmed_in_source');self.assertIs(r['issues'][0]['evidence'][0]['value'],False)
 def test_string_or_missing_false_does_not_open_confirmation_issue(self):
  for v in [None,'false',0]:self.assertEqual(self.assess({**self.asthma,'reliever_directions_confirmed':v})['issues'],[])
 def test_postbronchodilator_and_reference_limits_are_separate(self):
  r=self.assess(self.spiro);self.assertEqual([i['kind'] for i in r['issues']].count('reference_value_not_supplied'),2);self.assertEqual(r['issues'][0]['evidence'][0]['field'],'measurements.postbronchodilator_test_performed')
 def test_no_ratio_threshold_or_severity_inferred(self):
  for value in [.2,.69,.8]:
   r=self.assess({**self.spiro,'measurements':{'FEV1_FVC':value}});self.assertFalse(r['clinical_state_established']);self.assertFalse(any('severity' in i['kind'] for i in r['issues']))
 def test_latest_true_does_not_carry_older_false_issue(self):
  later={**self.asthma,'record_id':'new','event_date':'2026-08-01','reliever_directions_confirmed':True}
  r=self.assess(self.asthma,later);self.assertEqual(r['issues'],[]);self.assertEqual(r['suggested_record_ids'],['new']);self.assertEqual(r['records_outside_suggested_context'],1);self.assertEqual(len(r['sources']),2)
 def test_same_date_conflict_is_unknown_not_false(self):
  r=self.assess(self.asthma,{**self.asthma,'record_id':'other','reliever_directions_confirmed':True});self.assertEqual(r['issues'],[]);self.assertEqual(r['latest_source_accounts']['asthma_accounts']['fields']['reliever_directions_confirmed']['state'],'conflicting')
 def test_undated_condition_label_retained_without_imputed_date(self):
  r=self.assess(self.label);self.assertEqual(r['suggested_record_ids'],['l']);self.assertIsNone(r['latest_source_accounts']['condition_labels']['date']);self.assertEqual(r['sources'][0]['fields']['onset'],'2015')
 def test_future_or_undated_interviews_retained_outside_default_context(self):
  for when in [None,'bad','2027-01-01']:
   r=self.assess({**self.asthma,'event_date':when});self.assertEqual(r['suggested_record_ids'],[]);self.assertEqual(len(r['sources']),1);self.assertEqual(r['issues'],[])
 def test_behavioral_labels_not_current_screen_or_safety_assessment(self):
  r=self.assess({**self.label,'description':'MDD, recurrent'},domain='behavioral');self.assertTrue(r['source_review_candidate']);self.assertEqual(r['issues'],[]);self.assertTrue(any('patient safety' in x for x in r['unknowns']));self.assertFalse(r['clinical_state_established'])
 def test_no_sources_means_unknown_coverage_not_absent_need(self):
  for domain in ['respiratory','behavioral']:
   r=self.assess(domain=domain);self.assertFalse(r['source_review_candidate']);self.assertEqual(r['status'],'no_selected_source_accounts');self.assertTrue(r['unknowns'])
 def test_exact_selector_does_not_match_negation_or_unrelated_description(self):
  for description in ['No asthma','Family history of asthma','Obstructive sleep apnea syndrome']:
   with self.assertRaises(ValueError):self.assess({**self.label,'description':description})
 def test_cross_patient_unsynthetic_unmatched_future_arrival_rejected(self):
  for fields in [{'patient_id':'other'},{'synthetic':False},{'identity_status':'unmatched'},{'available_date':'2027-01-01'}]:
   with self.assertRaises(ValueError):self.assess({**self.asthma,**fields})
 def test_duplicate_record_identity_rejected(self):
  with self.assertRaises(ValueError):self.assess(self.asthma,self.asthma)
 def test_nested_unlisted_and_hidden_generator_fields_not_evidence(self):
  r=copy.deepcopy(self.spiro);r.update(original_text='PRIVATE',target='PRIVATE');r['measurements'].update(clean_text='PRIVATE',target='PRIVATE');self.assertNotIn('PRIVATE',json.dumps(self.assess(r)))
 def test_unexpected_nested_field_not_coerced_to_a_clinical_value(self):
  r=self.assess({**self.asthma,'reliever_directions_confirmed':{'value':False,'target':'PRIVATE'}});self.assertEqual(r['issues'],[]);self.assertNotIn('PRIVATE',json.dumps(r))
 def test_nonfinite_source_numeric_rejected(self):
  with self.assertRaises(ValueError):self.assess({**self.asthma,'symptom_days_per_week_reported':float('nan')})
 def test_full_population_counts_and_receipt_replay(self):
  self.save(self.asthma,self.spiro,self.label);r=d.prepare(self.path,'respiratory');self.assertEqual(r['counts']['population'],2);self.assertEqual(r['counts']['source_review_candidates'],1);self.assertEqual(r['counts']['no_selected_source_accounts'],1);self.assertEqual(r['counts']['records_without_usable_source_date'],1);self.assertEqual(d.inspect(self.path,r['id']),r)
 def test_source_mutation_invalidates_receipt(self):
  self.save(self.asthma);r=d.prepare(self.path,'respiratory');self.db.execute('DELETE FROM records');self.db.commit()
  with self.assertRaisesRegex(ValueError,'changed'):d.inspect(self.path,r['id'])
 def test_payload_or_id_tampering_rejected(self):
  self.save(self.asthma);r=d.prepare(self.path,'respiratory');r['id']='0'*64;d.location(self.path,r['id']).write_text(json.dumps(r))
  with self.assertRaisesRegex(ValueError,'integrity'):d.inspect(self.path,r['id'])
 def test_context_uses_exact_user_selection_and_preserves_older_sources(self):
  old={**self.asthma,'record_id':'old','event_date':'2026-05-01'};self.save(self.asthma,old);r=d.prepare(self.path,'respiratory')
  with patch.object(d.record_context,'create',return_value={'id':'context'}) as create:
   result=d.context(self.path,{'review_id':r['id'],'patient_id':'p','record_ids':['old']});self.assertEqual(create.call_args.args[3],['old']);self.assertEqual(result['domain_records_outside_context'],1);self.assertFalse(result['guidance_in_model_input'])
 def test_context_cross_patient_empty_duplicates_and_unknown_records_rejected(self):
  self.save(self.asthma);r=d.prepare(self.path,'respiratory')
  for pid,ids in [('without',['a']),('p',[]),('p',['a','a']),('p',['not-received'])]:
   with self.assertRaises(ValueError):d.context(self.path,{'review_id':r['id'],'patient_id':pid,'record_ids':ids})
 def test_pagination_domain_and_patient_validation(self):
  self.save(self.asthma);r=d.prepare(self.path,'respiratory');self.assertEqual(d.page(self.path,'respiratory',r['id'])['matching_patients'],1)
  with self.assertRaises(ValueError):d.page(self.path,'behavioral',r['id'])
  with self.assertRaises(ValueError):d.page(self.path,'respiratory',r['id'],'other')
  with self.assertRaises(ValueError):d.page(self.path,'respiratory',offset=-1)

if __name__=='__main__':unittest.main()
