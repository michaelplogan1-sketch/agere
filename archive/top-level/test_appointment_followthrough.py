import copy,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from clinical_core.agere.clinical import appointment_followthrough as A,appointment_qualification as Q,baseline_evidence as B
from test_outcome_learning import learning_fixture

def source(key,status='booked',**kw):
 r=dict(record_id=key,patient_id='p',source_table='appointments',source_system='s',organization_id='o',source_patient_id='raw',appointment_id='a',scheduled_date='2026-02-01',status=status,event_date='2026-01-20',available_date='2026-01-21',identity_status='matched',synthetic=True)
 r.update(kw);return r

def pair(status='fulfilled'):
 return [source('booking'),source('terminal',status,event_date='2026-02-01',available_date='2026-02-03')]
def assess(rr,cutoff='2026-02-15'):return A.assess('p',rr,cutoff)
class Observation(unittest.TestCase):
 def test_explicit_classes_and_window(self):
  for status,label in [('fulfilled',1),('no_show',0)]:
   r=assess(pair(status));self.assertEqual(r['recorded_label'],label);self.assertEqual(r['decision'],{'date':'2026-01-22','feature_cutoff':'2026-01-21'});self.assertEqual(r['window']['event_end'],'2026-02-08');self.assertEqual(r['window']['receipt_end'],'2026-02-15')
 def test_absence_never_negative(self):
  r=assess(pair()[:1]);self.assertEqual(r['status'],'no_explicit_terminal_report');self.assertIsNone(r['recorded_label'])
 def test_cancelled_separate(self):
  r=assess(pair('cancelled'));self.assertEqual(r['status'],'explicit_cancelled');self.assertIsNone(r['recorded_label'])
 def test_cancellation_before_scheduled_permitted_after_decision(self):
  rr=pair('cancelled');rr[1].update(event_date='2026-01-25',available_date='2026-01-26');self.assertEqual(assess(rr)['status'],'explicit_cancelled')
 def test_early_positive_is_censored(self):
  r=assess(pair(),'2026-02-14');self.assertEqual(r['status'],'incomplete_followup');self.assertIsNone(r['recorded_label']);self.assertEqual(r['terminal_status'],'fulfilled')
 def test_exact_deadline_inclusive(self):
  rr=pair();rr[1]['available_date']='2026-02-15';self.assertEqual(assess(rr)['recorded_label'],1)
 def test_late_report_does_not_relabel(self):
  rr=pair();rr[1]['available_date']='2026-02-16';r=assess(rr,'2026-03-01');self.assertIsNone(r['recorded_label']);self.assertEqual(r['excluded_reports'][0]['reason'],'received_after_frozen_followup')
 def test_late_conflict_preserved_as_exclusion(self):
  rr=pair()+[source('late','no_show',event_date='2026-02-01',available_date='2026-03-01')];r=assess(rr,'2026-03-01');self.assertEqual(r['recorded_label'],1);self.assertEqual(len(r['history_record_ids']),3)
 def test_future_unreceived_report_not_evidence(self):
  rr=pair();rr[1]['available_date']='2026-03-01';r=assess(rr);self.assertEqual(len(r['sources']),1);self.assertIsNone(r['recorded_label'])
 def test_contradictory_statuses(self):
  rr=pair()+[source('second','no_show',event_date='2026-02-01',available_date='2026-02-04')];r=assess(rr);self.assertIn('conflicting_terminal_statuses',r['issues']);self.assertIsNone(r['recorded_label'])
 def test_unknown_and_unmapped_status(self):
  for value in ('rescheduled','cancelled_by_patient',None):
   rr=pair();rr[1]['status']=value;self.assertIn('unmapped_status',assess(rr)['issues'])
 def test_reschedule_not_silently_merged(self):
  rr=pair();rr[1]['scheduled_date']='2026-02-02';self.assertIn('changed_or_unknown_schedule',assess(rr)['issues'])
 def test_same_identity_duplicate_reports_single_disposition(self):
  rr=pair();rr.append({**rr[1],'record_id':'copy','available_date':'2026-02-04'});r=assess(rr);self.assertEqual(r['recorded_label'],1);self.assertEqual(len(r['terminal_record_ids']),2)
 def test_different_terminal_dates_or_links_ambiguous(self):
  for kw in ({'event_date':'2026-02-02'},{'completed_encounter_id':'different'}):
   rr=pair();rr.append({**rr[1],'record_id':'other',**kw});self.assertIn('ambiguous_terminal_history',assess(rr)['issues'])
 def test_cross_source_not_joined(self):
  for key in ('source_system','organization_id','source_patient_id','appointment_id'):
   rr=pair();rr[1][key]='other';self.assertIsNone(assess(rr)['recorded_label']);self.assertEqual(len(assess(rr)['history_record_ids']),1)
 def test_cross_patient_mismatch_rejected(self):
  rr=pair();rr[1]['patient_id']='other'
  with self.assertRaises(ValueError):assess(rr)
 def test_missing_identity_earliest_not_skipped(self):
  rr=pair();rr[0]['source_patient_id']=None;self.assertEqual(assess(rr)['status'],'incomplete_index_identity')
 def test_tied_different_appointments_not_resolved_by_outcome(self):
  rr=pair()+[source('another',appointment_id='b')];self.assertEqual(assess(rr)['status'],'ambiguous_first_booking')
 def test_later_qualifying_booking_not_substituted(self):
  rr=pair()+[source('later',appointment_id='b',available_date='2026-01-22')];rr[0]['scheduled_date']='2026-01-01';self.assertEqual(assess(rr)['status'],'booking_not_received_before_scheduled_date')
 def test_booking_unknown_receipt_and_event(self):
  for kw,status in [({'available_date':None},'unknown_booking_receipt'),({'event_date':None},'unknown_or_future_booking_event'),({'event_date':'2026-01-22'},'unknown_or_future_booking_event')]:
   rr=pair();rr[0].update(kw);self.assertEqual(assess(rr)['status'],status)
 def test_unknown_terminal_clock_never_filled(self):
  for key,issue in [('event_date','unknown_terminal_event'),('available_date','unknown_same_identity_receipt')]:
   rr=pair();rr[1][key]=None;self.assertIn(issue,assess(rr)['issues'])
 def test_event_window_and_receipt_order(self):
  for event,arrival,issue in [('2026-01-31','2026-02-02','terminal_outside_declared_event_window'),('2026-02-09','2026-02-10','terminal_outside_declared_event_window'),('2026-02-03','2026-02-02','terminal_event_after_receipt')]:
   rr=pair();rr[1].update(event_date=event,available_date=arrival);self.assertIn(issue,assess(rr)['issues'])
 def test_already_known_terminal_excluded(self):
  rr=pair('cancelled');rr[1].update(event_date='2026-01-21',available_date='2026-01-21');self.assertIn('terminal_already_known_at_decision',assess(rr)['issues'])
 def test_no_booking_or_only_old_booking(self):
  self.assertEqual(assess([])['status'],'no_received_booking');rr=pair();rr[0]['available_date']='2025-12-31';self.assertEqual(assess(rr)['status'],'no_received_booking')
 def test_index_report_excluded_from_baseline_and_future_target_unavailable(self):
  rr=pair();prior=source('prior',source_table='observations',event_date='2026-01-18',available_date='2026-01-19');r=assess(rr)
  b=B.evaluate('p',rr+[prior],[],[],Q.anchor(r),B.definition('received_prior_180d'))
  self.assertEqual([s['record_id'] for s in b['included_sources']],['prior']);self.assertEqual(b['cutoff'],'2026-01-21');self.assertEqual(b['record_accounting']['excluded']['index_record'],1)
class Qualification(unittest.TestCase):
 def test_distinct_component_gate_in_addition_to_row_counts(self):
  x,y,p,protected=learning_fixture()
  # Several patients per component cannot satisfy independence by row multiplication.
  for a in x:a['source_template_families']=['shared']
  q=Q.qualify(x,y,p,protected);self.assertFalse(q['fit_input_contract_ready']);self.assertEqual(q['component_count'],1);self.assertTrue(any('distinct source-family' in r for r in q['readiness_gaps']))
 def test_original_reservations_and_outer_families_propagate(self):
  x,y,p,protected=learning_fixture();protected['patient_ids']=['p0'];x[1]['source_template_families']=x[0]['source_template_families'];p['family_partitions']['f2']='validation';q=Q.qualify(x,y,p,protected)
  self.assertFalse({'p0','p1','p2'}&{r['patient_id'] for r in q['rows']});self.assertFalse(q['outer_evaluation_used_for_fitting_or_scoring'])
 def test_independent_fixture_can_pass_without_relaxation(self):
  x,y,p,protected=learning_fixture();q=Q.qualify(x,y,p,protected);self.assertTrue(q['fit_input_contract_ready'])
 def test_nonbinary_status_cannot_fit(self):
  x,y,p,protected=learning_fixture();y[0].update(recorded_label=None);y[0]['qualification'].update(recorded_label=None,timing_eligible_observed_target=False,exclusion_reasons=['explicit_cancelled']);q=Q.qualify(x,y,p,protected);self.assertNotIn('p0',{r['patient_id'] for r in q['rows']})
class Storage(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.meta={'synthetic':True,'snapshot':{'as_of':'2026-02-15'},'source_run':'run','source_digest':'digest'};(self.root/'run.json').write_text(json.dumps(self.meta));db=sqlite3.connect(self.root/'stack.sqlite3');db.executescript('CREATE TABLE patients(id TEXT,payload TEXT);CREATE TABLE records(id TEXT,patient TEXT,stream TEXT,payload TEXT);');db.execute('INSERT INTO patients VALUES (?,?)',('p','{}'))
  for r in pair():db.execute('INSERT INTO records VALUES (?,?,?,?)',(r['record_id'],'p','appointments',json.dumps(r)))
  db.commit();db.close()
 def tearDown(self):self.tmp.cleanup()
 def test_immutable_replay_and_source_freshness(self):
  r=A.prepare(self.root,{'patient_ids':['p']});self.assertEqual(A.prepare(self.root,{'patient_ids':['p']})['id'],r['id']);self.assertEqual(A.inspect(self.root,r['id']),r);self.assertEqual(len(A.history(self.root)),1)
  db=sqlite3.connect(self.root/'stack.sqlite3');v=pair('no_show')[1];db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(v),'terminal'));db.commit();db.close()
  with self.assertRaises(ValueError):A.inspect(self.root,r['id'])
 def test_exact_population_and_receipt_integrity(self):
  for ids in ([],['missing'],['p','p'],'p'):
   with self.assertRaises(ValueError):A.prepare(self.root,{'patient_ids':ids})
  r=A.prepare(self.root,{'patient_ids':None});p=A.location(self.root,r['id']);r['label_counts']={};p.write_text(json.dumps(r))
  with self.assertRaises(ValueError):A.inspect(self.root,r['id'])
 def test_source_fields_survive_projection_and_summary_drops_full_sources(self):
  r=A.prepare(self.root,{'patient_ids':None});self.assertEqual(r['patients'][0]['sources'][0]['record_hash'],A.digest(pair()[0]));self.assertNotIn('sources',A.summary(r)['patients'][0]);self.assertEqual(A.summary(r)['patients'][0]['source_count'],2)
 def test_work_registration_is_selected_and_does_not_shadow(self):
  from clinical_core.agere.clinical import work_adapters
  self.assertEqual(work_adapters.resolve(A.PROGRAM).definition(A.PROGRAM),A.definition(A.PROGRAM));self.assertIn(A.PROGRAM,work_adapters.entries())
if __name__=='__main__':unittest.main()
