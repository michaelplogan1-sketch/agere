import unittest
from clinical_core.agere.clinical import warehouse_interpretation as w
from clinical_core.agere.clinical.clinical_events import bind_event,scoped_records,timeline
from clinical_core.agere.clinical.clinical_inputs import measurements
from clinical_core.agere.clinical.analytic_registry import renal_series
from clinical_core.agere.clinical.economical_planner import local_plan
from clinical_core.agere.narrative_sources import text_fields,coverage
from clinical_core.agere.clinical.evidence_reader import candidates

ASOF='2028-09-30'
def record(rid,stream,**fields):
 r={'record_id':rid,'patient_id':'P-1','source_table':stream,'source_system':'received','organization_id':'site','event_date':'2028-09-01',
    'available_date':'2028-09-02','synthetic':True,'identity_status':'matched','_warehouse_source':{'adapter':w.ADAPTER}}
 r.update(fields);r['_warehouse_fields']=list(fields);return r

class NativeInterpretation(unittest.TestCase):
 def test_narrative_fields_reach_reader_with_exact_quotes(self):
  for field in ('ordering_reason','course_summary','history_text','goc'):
   r=record(field,'imported_documents',**{field:'The patient has chronic kidney disease; review records.'})
   self.assertEqual(list(text_fields(r)),[{'field':field,'text':r[field]}])
   self.assertNotIn('imported_documents.'+field,coverage([r])['unmapped_string_fields'])
   found=list(candidates('P-1',[r],ASOF));self.assertTrue(found)
   self.assertTrue(all(x['source']['quote']==r[field][x['source']['start']:x['source']['end']] for x in found))
 def test_collection_time_comes_from_received_specimen(self):
  lab=record('l','labs',test='creatinine',value=1.4,unit='mg/dL',result_status='final',specimen_record_id='s')
  specimen=record('s','laboratory_specimens',collected_time='2028-08-28T23:30:00-05:00')
  r=bind_event(lab,ASOF,w.index([lab,specimen]))
  self.assertEqual(r['clinical_date'],'2028-08-28');self.assertEqual(r['clinical_date_evidence'][0]['record_id'],'s')
  self.assertEqual(r['source_event_date'],'2028-09-01')
  points=renal_series([lab,specimen],'creatinine',ASOF)['series'];self.assertEqual(len(points),1)
  self.assertEqual(points[0]['evidence']['date_basis'],'explicit_collection_date')
  obs=measurements('P-1',[lab,specimen],[],ASOF)['creatinine'][0]
  self.assertEqual(obs['clinical_date'],'2028-08-28');self.assertIsNone(obs['exclusion'])
 def test_missing_cross_patient_and_late_specimens_do_not_supply_dates(self):
  l=record('l','labs',specimen_record_id='s');s=record('s','laboratory_specimens',collected_time='2028-08-28T12:00:00+00:00')
  for rows in [[l],[l,{**s,'patient_id':'P-2'}],[l,{**s,'available_date':'2028-10-01'}]]:
   bound=bind_event(l,ASOF,w.index(rows));self.assertIsNone(bound['clinical_date']);self.assertTrue(bound['clock_link_issues'])
 def test_windowed_lab_keeps_collection_dependency_outside_window(self):
  l=record('l','labs',test='creatinine',value=1.2,unit='mg/dL',result_status='final',specimen_record_id='s')
  s=record('s','laboratory_specimens',event_date='2028-08-28',collected_time='2028-08-28T12:00:00+00:00')
  selected,_=scoped_records([l,s],{'basis':'source_event','start':'2028-09-01','end':ASOF},ASOF)
  series=renal_series(selected,'creatinine',ASOF,reference_records=[l,s])['series']
  self.assertEqual(len(series),1);self.assertEqual(series[0]['date'],'2028-08-28')
 def test_same_day_conflicting_specimen_instants_remain_unresolved(self):
  l=record('l','labs',specimen_time='2028-08-28T10:00:00+00:00',specimen_record_id='s')
  s=record('s','laboratory_specimens',collected_time='2028-08-28T12:00:00+00:00')
  self.assertEqual(bind_event(l,ASOF,w.index([l,s]))['clinical_date_status'],'conflicting_explicit_dates')
 def test_naive_timestamp_and_signature_are_not_measurement_dates(self):
  for fields in [{'specimen_time':'2028-08-28T10:00:00'},{'signed_date':'2028-08-28'},{'result_time':'2028-08-28T10:00:00+00:00'}]:
   self.assertIsNone(bind_event(record('l','labs',**fields),ASOF)['clinical_date'])
 def test_adt_roles_use_matching_encounter_not_referring_encounter(self):
  e=record('e','encounters',encounter_id='stay',type='inpatient',start_time='2028-08-26T23:30:00+00:00',end_time='2028-09-01T12:00:00+00:00')
  a=record('a','adt',encounter_id='stay',event='admission',referring_encounter_id='different')
  d=record('d','adt',encounter_id='stay',event='discharge')
  ix=w.index([e,a,d]);self.assertEqual(bind_event(a,ASOF,ix)['clinical_date'],'2028-08-26')
  self.assertEqual(bind_event(d,ASOF,ix)['clinical_date'],'2028-09-01')
  selected,scope=scoped_records([e,a,d],{'basis':'clinical','start':'2028-09-01','end':ASOF},ASOF)
  self.assertEqual([r['record_id'] for r in selected],['d'])
  view=timeline({'patient_id':'P-1'},selected,ASOF,reference_records=[e,a,d])
  self.assertEqual(view['dated_events'][0]['clinical_date'],'2028-09-01')
 def test_duplicate_encounter_exports_are_evidence_not_extra_votes(self):
  a=record('a','adt',event='admission',encounter_id='stay')
  e=record('e','encounters',type='inpatient',encounter_id='stay',start_time='2028-08-28T12:00:00+00:00')
  ix=w.index([a,e,{**e,'record_id':'e2'}]);b=bind_event(a,ASOF,ix)
  self.assertEqual(b['clinical_date'],'2028-08-28');self.assertEqual(len(b['clinical_date_evidence']),2)
 def test_reported_use_does_not_become_ingestion_or_override_order(self):
  rows=[record('o','medication_orders',medication_name='x',status='active'),record('r','medication_followup_reports',medication_name='x',reported_intent='not_using')]
  result=w.interpret({'patient_id':'P-1'},rows,ASOF)
  self.assertEqual({s['relation'] for s in result['statements']},{'prescription_order','reported_use'})
  self.assertFalse(next(s for s in result['statements'] if s['relation']=='reported_use')['reported_use'])
  self.assertFalse(result['clinical_state_established'])
 def test_unconfirmed_care_and_bill_do_not_establish_absence_or_hardship(self):
  rows=[record('r','referral_status_updates',service_performed_confirmed=False),record('b','patient_billing_accounts',balance_cents=500)]
  r=w.interpret({'patient_id':'P-1'},rows,ASOF)
  self.assertNotIn('reported_affordability',r['relation_counts']);self.assertFalse(r['care_gap_established'])
  self.assertNotIn('care_not_received',r['relation_counts'])
 def test_undated_account_does_not_overwrite_dated_account(self):
  rows=[record('a','medication_followup_reports',medication_name='x',reported_intent='using'),record('b','medication_followup_reports',medication_name='x',reported_intent='not_using',event_date=None)]
  r=w.interpret({'patient_id':'P-1'},rows,ASOF);self.assertEqual(r['states'][0]['status'],'undated')
  self.assertEqual(r['evidence_acquisition_steps'][0]['action'],'Verify statement timing')
 def test_cross_patient_statement_input_rejected(self):
  with self.assertRaises(ValueError):w.interpret({'patient_id':'P-1'},[record('x','financial_screens',patient_id='P-2')],ASOF)
 def test_duplicate_document_lineage_is_not_a_treatment_conflict(self):
  a=record('a','advance_care_documents',document_id='doc',extracted_treatment_fields={'cpr':'attempt'},legal_validity='not_determined')
  b={**a,'record_id':'b','duplicate_of_record_id':'a'}
  result=w.interpret({'patient_id':'P-1'},[a,b],ASOF)
  self.assertEqual(len(result['states']),1);self.assertEqual(result['states'][0]['status'],'documented_source_statement')
  self.assertFalse(result['clinical_state_established'])
 def test_local_plan_reaches_reusable_operator(self):
  graph,_=local_plan('Reconcile warehouse source meanings',ASOF)
  self.assertEqual(graph['nodes'][-1]['parameters']['operator'],'warehouse_interpretation')
 def test_window_cannot_hide_required_source_dependencies(self):
  from clinical_core.agere.clinical.analysis_graph import validate
  graph,_=local_plan('Reconcile warehouse source meanings',ASOF)
  graph['nodes'][-1]['parameters']['analysis_window']={'basis':'source_event','start':'2028-09-01','end':ASOF}
  with self.assertRaises(ValueError):validate(graph,ASOF)

if __name__=='__main__':unittest.main()
