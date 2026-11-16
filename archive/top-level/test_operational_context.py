import copy,json,sqlite3,unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import test_work_cost as fixture
from clinical_core.agere.clinical import operational_context as o,operational_sensitivity as s
from clinical_core.contracts import digest

def source(key,stream,**fields):
 return dict(record_id=key,patient_id='p',source_table=stream,source_system='sys',organization_id='org',source_patient_id='raw',event_date='2026-01-02',available_date='2026-01-03',event_date_precision='minute',synthetic=True,identity_status='matched',**fields)
def pair():return [source('a','appointments',appointment_id='appt',completed_encounter_id='enc',scheduled_date='2026-01-02',status='fulfilled'),source('e','encounters',encounter_id='enc',status='completed',type='office',start_time='2026-01-02T23:50:00-06:00',end_time='2026-01-03T00:20:00-06:00',available_time='2026-01-03T00:50:00-06:00')]
def review(rows):return o.assess('p',rows,'2026-01-05','2026-01-01','2026-01-04')
class Timing(unittest.TestCase):
 def test_midnight_interval_and_arrival_exact(self):
  r=review(pair());x=r['appointment_observations'][0];self.assertEqual(x['interval_minutes']['display'],30);self.assertEqual(x['arrival_delay_minutes']['display'],30);self.assertEqual(r['interval_summaries'][0]['mean_minutes']['display'],30);self.assertIsNone(r['staff_effort_minutes']);self.assertFalse(r['task_attribution_established'])
 def test_timezone_arithmetic_and_fractional_minutes(self):
  rows=pair();rows[1].update(start_time='2026-01-02T23:59:00Z',end_time='2026-01-02T18:59:30-05:00');x=review(rows)['appointment_observations'][0];self.assertEqual(x['interval_minutes'],{'numerator':1,'denominator':2,'display':0.5})
 def test_date_only_and_offsetless_do_not_become_intervals(self):
  for t in ('2026-01-02','2026-01-02T12:00:00','bad',None):
   rows=pair();rows[1]['start_time']=t;self.assertEqual(review(rows)['appointment_observations'][0]['interval_issue'],'missing_or_imprecise_offset_clock')
 def test_negative_interval_and_imprecise_source(self):
  for change,reason in [({'end_time':'2026-01-02T22:00:00-06:00'},'negative_encounter_interval'),({'event_date_precision':'day'},'source_clock_precision_not_minute_or_second'),({'status':'in_progress'},'encounter_not_reported_completed'),({'end_time':'2026-01-06T00:00:00Z'},'encounter_end_after_receipt_cutoff')]:
   rows=pair();rows[1].update(change);self.assertEqual(review(rows)['appointment_observations'][0]['interval_issue'],reason)
 def test_invalid_arrival_separate_from_encounter_interval(self):
  for value,reason in [(None,'missing_or_imprecise_arrival_clock'),('2026-01-04T00:50:00-06:00','arrival_clock_date_disagrees'),('2026-01-03T00:10:00-06:00','arrival_before_encounter_end')]:
   rows=pair();rows[1]['available_time']=value;x=review(rows)['appointment_observations'][0];self.assertIsNotNone(x['interval_minutes']);self.assertIsNone(x['arrival_delay_minutes']);self.assertEqual(x['arrival_issue'],reason)
 def test_explicit_link_no_fallback_encounter_or_date_join(self):
  rows=pair();rows[0].pop('completed_encounter_id');rows[0]['encounter_id']='enc';self.assertEqual(review(rows)['appointment_observations'][0]['interval_issue'],'no_explicit_completed_encounter_link')
 def test_cross_source_link_unresolved(self):
  for k in ('source_system','organization_id','source_patient_id'):
   rows=pair();rows[1][k]='other';x=review(rows)['appointment_observations'][0];self.assertEqual(x['encounter_record_ids'],[]);self.assertEqual(x['interval_issue'],'linked_encounter_not_received_in_same_scope')
 def test_ambiguous_encounter_and_duplicate_appointment_excluded(self):
  for idx,reason in [(1,'ambiguous_encounter_identity'),(0,'duplicate_appointment_identity')]:
   rows=pair();rows.append({**rows[idx],'record_id':'duplicate'});r=review(rows);self.assertFalse(r['interval_summaries']);self.assertEqual(r['appointment_observations'][0]['interval_issue'],reason)
 def test_multiple_appointments_one_encounter_not_independent_intervals(self):
  rows=pair();rows.append({**rows[0],'record_id':'b','appointment_id':'other'});r=review(rows);self.assertEqual(r['denominators']['included_unique_linked_encounters'],0);self.assertEqual(r['denominators']['interval_exclusions'],{'encounter_linked_by_multiple_appointment_records':2})
 def test_prior_status_reports_do_not_duplicate_completion_intervals(self):
  rows=pair();rows.extend([{**rows[0],'record_id':'booked','status':'booked','completed_encounter_id':None},{**rows[1],'record_id':'progress','status':'in_progress','end_time':None}]);r=review(rows)
  self.assertEqual(r['denominators']['received_appointment_records'],2);self.assertEqual(r['denominators']['included_unique_linked_encounters'],1);self.assertEqual(r['interval_summaries'][0]['count'],1)
 def test_disagreement_in_status_history_is_not_silently_overwritten(self):
  for target,change,reason in [(0,{'status':'no_show','completed_encounter_id':None},'conflicting_appointment_reports'),(0,{'status':'booked','scheduled_date':'2026-01-04','completed_encounter_id':None},'conflicting_appointment_reports'),(1,{'status':'in_progress','start_time':'2026-01-02T23:00:00-06:00','end_time':None},'conflicting_encounter_reports')]:
   rows=pair();rows.append({**rows[target],**change,'record_id':'prior'});r=review(rows);self.assertEqual(r['appointment_observations'][0]['interval_issue'],reason);self.assertFalse(r['interval_summaries'])
 def test_period_denominators_and_referral_literal_status(self):
  rows=pair();rows[0]['scheduled_date']='2025-12-01';rows.append(source('r','referrals',status='requested',completion_status='not_yet_known'));r=review(rows);self.assertEqual(r['denominators']['appointment_records_in_period'],0);self.assertEqual(r['denominators']['interval_exclusions'],{'outside_selected_period':1});self.assertFalse(r['referral_reports'][0]['fulfillment_established']);self.assertIsNone(r['referral_reports'][0]['wait_minutes'])
 def test_unknown_date_not_substituted_with_receipt(self):
  rows=pair();rows[0]['scheduled_date']=None;self.assertEqual(review(rows)['appointment_observations'][0]['period_issue'],'missing_or_invalid_period_date')
 def test_patient_identity_visibility_and_duplicates_fail_closed(self):
  for change in ({'patient_id':'other'},{'identity_status':'unmatched'},{'synthetic':False},{'available_date':'2026-01-06'}):
   rows=pair();rows[1].update(change)
   with self.assertRaises(ValueError):review(rows)
  with self.assertRaises(ValueError):review(pair()+pair())
 def test_empty_records_are_absent_evidence_not_zero_duration(self):
  r=review([]);self.assertEqual(r['denominators']['included_unique_linked_encounters'],0);self.assertEqual(r['interval_summaries'],[]);self.assertIsNone(r['staff_effort_minutes'])
 def test_strict_dates_and_order(self):
  for values in [('2026-1-5','2026-01-01','2026-01-02'),('2026-01-05','2026-01-04','2026-01-02'),('2026-01-05','2026-01-01','2026-01-06')]:
   with self.assertRaises(ValueError):o.assess('p',pair(),*values)
 def test_raw_baseline_and_stratified_summary(self):
  rows=pair();other=pair()
  for r in other:r['record_id']+='2'
  other[0].update(appointment_id='appt2',completed_encounter_id='enc2');other[1].update(encounter_id='enc2',end_time='2026-01-03T00:00:00-06:00');r=review(rows+other);g=r['interval_summaries'][0];self.assertEqual(g['mean_minutes']['display'],20);self.assertEqual(g['median_minutes']['display'],20);self.assertEqual(len(g['raw_intervals']),2)

class ReceiptCutoff(unittest.TestCase):
 def test_cutoff_uses_arrival_not_event_and_rejects_future_snapshot(self):
  import tempfile
  with tempfile.TemporaryDirectory() as tmp:
   path=__import__('pathlib').Path(tmp);(path/'run.json').write_text(json.dumps({'synthetic':True,'snapshot':{'as_of':'2026-01-05'},'source_digest':'test'}))
   db=sqlite3.connect(path/'stack.sqlite3');db.execute('CREATE TABLE patients(id TEXT)');db.execute("INSERT INTO patients VALUES ('p')");db.execute('CREATE TABLE records(id TEXT,patient TEXT,stream TEXT,payload TEXT)')
   rows=pair();rows[1]['available_date']='2026-01-05'
   for r in rows:db.execute('INSERT INTO records VALUES (?,?,?,?)',(r['record_id'],'p',r['source_table'],json.dumps(r)))
   db.commit();db.close();before=o.received(path,['p'],'2026-01-04')['patients']['p'];after=o.received(path,['p'],'2026-01-05')['patients']['p']
   self.assertEqual([r['record_id'] for r in before],['a']);self.assertEqual(len(after),2)
   self.assertEqual(o.assess('p',before,'2026-01-04','2026-01-01','2026-01-04')['appointment_observations'][0]['interval_issue'],'linked_encounter_not_received_in_same_scope')
   for cutoff in ('2026-01-06','2026-1-4'):
    with self.assertRaises(ValueError):o.received(path,['p'],cutoff)
   with self.assertRaises(ValueError):o.received(path,['foreign'],'2026-01-04')

class OperationalWork(unittest.TestCase):
 prepare=fixture.CostWork.prepare
 queue=fixture.CostWork.queue
 update=fixture.CostWork.update
 event=fixture.CostWork.event
 def setUp(self):
  fixture.CostWork.setUp(self);self.ids=o.population(self.dep,'dependency');self.raw={'source_digest':'test-source','snapshot':{'as_of':'2026-01-05'},'patients':{pid:[{**r,'patient_id':pid} for r in pair()] for pid in self.ids}}
  p=patch.object(o,'received',side_effect=lambda *a:copy.deepcopy(self.raw));p.start();self.addCleanup(p.stop)
  self.selection={'parent_type':'dependency','parent_id':self.dep['id'],'available_by':'2026-01-05','period_start':'2026-01-01','period_end':'2026-01-04'}
 def save(self):
  r=o.preview(self.path,self.selection);return o.prepare(self.path,{**self.selection,'preview_id':r['id']})
 def sensitivity(self,ctx=None):
  ctx=ctx or self.save();return {'context_id':ctx['id'],'question':'How do explicit durations affect the queue?','assumption_source':'Engineering scenario, not observed labor','applicability_rationale':'Received encounters concern a different activity; task comparability and staff effort remain unknown. Supplied durations illustrate sensitivity only.','durations':copy.deepcopy(self.dep['duration_assumptions'])}
 def test_persisted_exact_scope_baseline_and_idempotency(self):
  r=self.save();self.assertEqual(r['patient_ids'],self.ids);self.assertEqual(r['selected_task_ids'],self.dep['selected_task_ids']);self.assertEqual(self.save(),r);self.assertEqual(len(o.history(self.path,'dependency',self.dep['id'])['contexts']),1);self.assertTrue(o.inspect(self.path,r['id'])['current']);self.assertEqual(o.C.parent(self.path,'dependency',self.dep['id']),self.dep)
 def test_changed_source_cannot_save_old_preview_and_history_stays(self):
  r=self.save();self.raw['patients'][self.ids[0]][1]['end_time']='2026-01-03T00:10:00-06:00'
  with self.assertRaisesRegex(ValueError,'preview'):o.prepare(self.path,{**self.selection,'preview_id':r['id']})
  old=o.inspect(self.path,r['id']);self.assertFalse(old['current']);self.assertEqual(old['reports'],r['reports'])
 def test_parent_task_change_invalidates_context_and_sensitivity(self):
  ctx=self.save();scenario=s.compare(self.path,self.sensitivity(ctx));self.event(self.first,action='hold');self.assertFalse(o.inspect(self.path,ctx['id'])['current']);self.assertFalse(s.inspect(self.path,scenario['id'])['current'])
 def test_unknown_extra_authority_and_patient_rejected(self):
  for change in ({'patient_ids':['x']},{'staff_effort_verified':True},{'parent_type':'x'}):
   with self.assertRaises(ValueError):o.preview(self.path,{**self.selection,**change})
  self.raw['patients']['foreign']=[]
  with self.assertRaises(ValueError):o.preview(self.path,self.selection)
 def test_source_race_prevents_context(self):
  calls=[]
  def race(*args):
   calls.append(1);r=copy.deepcopy(self.raw)
   if len(calls)>1:r['source_digest']='changed'
   return r
  with patch.object(o,'received',side_effect=race):
   with self.assertRaisesRegex(ValueError,'changed'):o.preview(self.path,self.selection)
 def test_concurrent_save_and_immutability(self):
  r=o.preview(self.path,self.selection);req={**self.selection,'preview_id':r['id']}
  with ThreadPoolExecutor(max_workers=2) as ex:rows=list(ex.map(lambda _:o.prepare(self.path,req),range(2)))
  self.assertEqual(rows[0],rows[1]);db=o.database(self.path)
  with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM contexts')
  db.close()
 def test_sensitivity_same_durations_reproduce_baseline(self):
  row=s.compare(self.path,self.sensitivity());self.assertTrue(s.inspect(self.path,row['id'])['current']);self.assertFalse(row['empirically_calibrated']);self.assertEqual(row['tasks_changed'],0)
  for comp in row['comparisons'].values():self.assertEqual(comp['original'],comp['supplied_duration_scenario']);self.assertEqual(comp['completion_count_difference'],0)
 def test_sensitivity_only_durations_change_no_automatic_clock_conversion(self):
  req=self.sensitivity()
  for values in req['durations'].values():
   for k in values:values[k]*=2
  row=s.compare(self.path,req)
  for case,comp in row['comparisons'].items():
   self.assertEqual(comp['original'],self.dep['comparisons'][case]['with_dependencies']);a={j['id']:j for j in comp['original']['schedule']};b={j['id']:j for j in comp['supplied_duration_scenario']['schedule']}
   for key in a:
    self.assertEqual(b[key]['duration_minutes'],a[key]['duration_minutes']*2)
    for field in ('patient_id','service_id','ready_minute','predecessor_task_ids'):self.assertEqual(a[key][field],b[key][field])
  self.assertEqual(o.C.parent(self.path,'dependency',self.dep['id']),self.dep)
 def test_resource_parent_supported_and_baseline_preserved(self):
  self.selection.update(parent_type='resource',parent_id=self.resource['id']);ctx=self.save();row=s.compare(self.path,self.sensitivity(ctx));self.assertEqual(row['comparisons']['central']['original'],self.resource['comparisons']['central']['proposed'])
 def test_missing_unordered_extra_duration_and_rationale_rejected(self):
  req=self.sensitivity();service=next(iter(req['durations']))
  for change in ({'durations':{}},{'durations':{service:{'low':20,'central':10,'high':30}}},{'durations':{service:{'low':True,'central':10,'high':30}}},{'applicability_rationale':''},{'measured_labor':True}):
   with self.assertRaises(ValueError):s.compare(self.path,{**req,**change})
 def test_context_change_invalidates_sensitivity_without_replacing_baseline(self):
  row=s.compare(self.path,self.sensitivity());self.raw['source_digest']='new';old=s.inspect(self.path,row['id']);self.assertFalse(old['current']);self.assertEqual(old['comparisons'],row['comparisons'])
 def test_sensitivity_replay_and_immutable(self):
  req=self.sensitivity();r=s.compare(self.path,req);self.assertEqual(s.compare(self.path,req),r);self.assertEqual(len(s.history(self.path,req['context_id'])['scenarios']),1);db=s.database(self.path)
  with self.assertRaises(sqlite3.IntegrityError):db.execute('UPDATE scenarios SET context="other"')
  db.close()
 def test_context_integrity_tampering_rejected(self):
  r=self.save();db=o.database(self.path);db.execute('DROP TRIGGER contexts_UPDATE');db.execute('UPDATE contexts SET payload=?',(json.dumps({**r,'tasks_changed':1}),));db.commit();db.close()
  with self.assertRaisesRegex(ValueError,'integrity'):o.inspect(self.path,r['id'])

if __name__=='__main__':unittest.main()
