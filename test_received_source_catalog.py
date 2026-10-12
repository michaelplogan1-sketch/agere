import copy,json,unittest
from pathlib import Path
from clinical_core.agere import narrative_sources as N,received_source_catalog as C
from clinical_core.agere.healthcare_warehouse import project
from clinical_core.agere.clinical import warehouse_interpretation as W,cohorts,context_builder as B,native_requirements as R


def record(rid,table,**fields):return {'record_id':rid,'patient_id':'P','source_table':table,'synthetic':True,'event_date':'2029-07-01','available_date':'2029-07-02','_warehouse_source':{'adapter':W.ADAPTER},'_warehouse_fields':list(fields),**fields}


class ReceivedSourceCatalog(unittest.TestCase):
 def test_every_inspected_source_exposes_literal_fields_and_cohort_contract(self):
  examples=json.loads(Path('artifacts/agere-warehouse-v123-inventory/new-source-examples.json').read_text())
  self.assertEqual(set(examples),set(C.DEFINITIONS))
  for table,raw in examples.items():
   row={**raw,'facts':json.dumps(raw['facts'])};r=project(row);spec=C.DEFINITIONS[table];statement=W.statement(r,'2029-08-15',W.index([r]))
   self.assertEqual(statement['values'],{k:r[k] for k in spec['fields'] if k in r});self.assertFalse(statement['clinical_truth_established']);self.assertEqual(cohorts.RECORD_FIELDS[table],set(spec['fields']))
   self.assertEqual(R.fields()[spec['relation']],set(spec['fields']))

 def test_source_specific_order_links_do_not_confuse_procedures_with_medications(self):
  target=record('order','procedure_orders');r=record('result','endoscopy_reports',order_record_id='order');s=W.statement(r,'2029-08-15',W.index([target,r]));self.assertFalse(s['link_issues']);self.assertEqual(s['linked_sources'][0]['target']['source_table'],'procedure_orders')
  target['source_table']='medication_orders';s=W.statement(r,'2029-08-15',W.index([target,r]));self.assertEqual(s['link_issues'][0]['reason'],'wrong_linked_source_type')

 def test_each_reference_array_member_is_checked_for_patient_type_and_availability(self):
  targets=[record('a','medication_administrations'),record('b','medication_administrations'),record('c','labs')];targets[1]['patient_id']='OTHER';targets[2]['available_date']='2030-01-01'
  r=record('report','inpatient_discharge_reconciliation',inpatient_oral_administration_record_ids=['a','b','missing'],reviewed_laboratory_record_ids=['c'])
  row=W.statement(r,'2029-08-15',W.index([r,*targets]));self.assertEqual(len(row['linked_sources']),1);self.assertEqual({x['reason'] for x in row['link_issues']},{'cross_patient_link_rejected','referenced_source_not_received','linked_source_not_available_by_cutoff'});self.assertTrue(all('[' in x['field'] for x in row['link_issues']))

 def test_received_nested_text_preserves_offsets_and_structured_siblings(self):
  r=record('copy','received_home_health_documents',content={'text':'Do not restart the old medication.','medication_changed':False,'truth':'must not be read'},purpose='medication_reply')
  parts=list(N.text_fields(r));self.assertEqual(parts,[{'field':'content.text','text':'Do not restart the old medication.'}]);fields=B.structured_without_text(r,parts);self.assertEqual(fields['content'],{'medication_changed':False})
  candidate=B.units('P',[r],[],{'streams':['received_home_health_documents'],'concepts':[]},'2029-08-15');self.assertEqual(candidate[0]['reported_fields']['content'],{'medication_changed':False});self.assertEqual(candidate[1]['field'],'content.text');self.assertEqual(candidate[1]['text'],r['content']['text'])

 def test_nested_goal_text_keeps_timeframe_and_list_positions(self):
  r=record('plan','home_health_plans',goals=[{'description':'Transfer safely','timeframe_days':28},{'description':'Walk farther','timeframe_days':14}]);parts=list(N.text_fields(r));self.assertEqual([p['field'] for p in parts],['goals[0].description','goals[1].description']);self.assertEqual(B.structured_without_text(r,parts)['goals'],[{'timeframe_days':28},{'timeframe_days':14}])

 def test_explicit_procedure_clock_does_not_use_signature_or_arrival(self):
  r=record('procedure','endoscopy_reports',procedure_start='2029-07-01T15:15:00+00:00');c=W.clock(r,'2029-08-15');self.assertEqual(c['clinical_date'],'2029-07-01')
  r=record('copy','received_procedure_reports',original_signed_time='2029-07-01T15:55:00+00:00');self.assertIsNone(W.clock(r,'2029-08-15')['clinical_date'])

 def test_inpatient_ef_requires_exact_test_quality_and_final_status(self):
  from clinical_core.agere.clinical import clinical_inputs as I
  r=record('echo','inpatient_cardiac_reports',test='transthoracic_echocardiogram',LVEF_percent=35,status='performed_final',image_quality='adequate_for_global_function',acquired_time='2029-07-01T15:00:00+00:00');r['identity_status']='matched'
  row=I.measurements('P',[r],[],'2029-08-15')['lvef'][0]
  self.assertIsNone(row['exclusion']);self.assertEqual(row['clinical_date'],'2029-07-01');self.assertEqual(row['evidence'][0]['field'],'LVEF_percent')
  for change,reason in [({'status':'ordered'},'unfinalized_result'),({'image_quality':'limited'},'measurement_quality_unresolved'),({'LVEF_percent':101},'numeric_value_outside_source_range')]:
   self.assertEqual(I.measurements('P',[r|change],[],'2029-08-15')['lvef'][0]['exclusion'],reason)
  self.assertNotIn('lvef',I.measurements('P',[r|{'test':'12_lead_ECG'}],[],'2029-08-15'))

if __name__=='__main__':unittest.main()
