import copy, itertools, sqlite3, unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import test_care_capabilities as fixture
from clinical_core.agere.clinical import work_capacity as c

class Simulation(unittest.TestCase):
 def jobs(self,values):return [{'id':str(i),'duration_minutes':v} for i,v in enumerate(values)]
 def test_exact_fifo_and_horizon_boundary(self):
  r=c.simulate(self.jobs([30,10,20,5]),2,30)
  self.assertEqual([(x['start_minute'],x['finish_minute'],x['scenario_worker']) for x in r['schedule']],[(0,30,1),(0,10,2),(10,30,2),(30,35,1)])
  self.assertEqual((r['completed_by_horizon'],r['in_progress_at_horizon'],r['waiting_at_horizon']),(3,0,1))
  self.assertEqual(r['remaining_work_minutes'],5);self.assertEqual(r['clearance_minutes'],35)
  self.assertEqual(r['mean_wait_minutes'],10);self.assertEqual(r['assumed_worker_utilization'],1)
 def test_zero_staff_does_not_invent_completion(self):
  r=c.simulate(self.jobs([10,20]),0,60)
  self.assertIsNone(r['clearance_minutes']);self.assertEqual(r['waiting_at_horizon'],2);self.assertEqual(r['schedule'],[])
  self.assertEqual(c.simulate([],0,60)['clearance_minutes'],0)
 def test_partial_work_and_indivisible_tasks(self):
  r=c.simulate(self.jobs([100,5]),1,20)
  self.assertEqual((r['completed_by_horizon'],r['in_progress_at_horizon'],r['waiting_at_horizon']),(0,1,1))
  self.assertEqual(r['remaining_work_minutes'],85)
 def test_exhaustive_small_schedules_conserve_work_and_bound_makespan(self):
  for durations in itertools.product((1,2,4),repeat=4):
   for workers in (1,2,3):
    r=c.simulate(self.jobs(durations),workers,3)
    self.assertEqual(sum(r[k] for k in ('completed_by_horizon','in_progress_at_horizon','waiting_at_horizon')),4)
    self.assertGreaterEqual(r['clearance_minutes'],r['clearance_lower_bound_minutes'])
    self.assertLessEqual(r['clearance_minutes'],(2-1/workers)*r['clearance_lower_bound_minutes']+1e-9)
    self.assertTrue(0<=r['assumed_worker_utilization']<=1)
    self.assertAlmostEqual(r['assumed_worker_utilization']*workers*3+r['remaining_work_minutes'],sum(durations))
    for worker in range(1,workers+1):
     schedule=[s for s in r['schedule'] if s['scenario_worker']==worker]
     self.assertTrue(all(a['finish_minute']<=b['start_minute'] for a,b in zip(schedule,schedule[1:])))
 def test_no_bool_negative_nan_or_duplicate_inputs(self):
  for v in (True,-1,1.5,float('nan'),129):
   with self.assertRaises(ValueError):c.simulate(self.jobs([1]),v,1)
  for v in (False,0,-1,float('inf'),1.5):
   with self.assertRaises(ValueError):c.simulate(self.jobs([v]),1,1)
  with self.assertRaises(ValueError):c.simulate([{'id':'a','duration_minutes':1}]*2,1,2)

class WorkCapacity(unittest.TestCase):
 prepare=fixture.CareCapabilities.prepare
 queue=fixture.CareCapabilities.queue
 update=fixture.CareCapabilities.update
 event=fixture.CareCapabilities.event
 def setUp(self):
  fixture.CareCapabilities.setUp(self)
  self.answers={'a'*64:self.answer}
  p=patch.object(c.care.source_synthesis,'inspect',side_effect=lambda path,key:copy.deepcopy(self.answers[key]));p.start();self.addCleanup(p.stop)
  self.first=self.queue()['task']
  self.answer=copy.deepcopy(self.answer)
  self.answer['patient_id']='p2';self.req['origin']['id']='b'*64;self.answer['id']='b'*64
  self.answers['b'*64]=self.answer
  self.second=self.queue()['task']
  self.snap=c.snapshot(self.path,'buyer1')
  self.request={'buyer_id':'buyer1','snapshot_id':self.snap['id'],'task_ids':[self.second['id'],self.first['id']],
    'question':'How does a second review worker change this selected batch?',
    'assumption_source':'Synthetic engineering scenario; durations and pooled ability are supplied, not measured.',
    'workers':2,'horizon_minutes':15,'durations':{'pharmacy':{'low':5,'central':10,'high':20}}}
 def test_exact_question_task_scope_sensitivity_and_baseline(self):
  r=c.compare(self.path,self.request)
  self.assertEqual(r['selected_task_ids'],[self.first['id'],self.second['id']]);self.assertEqual(r['question'],self.request['question'])
  central=r['comparisons']['central'];self.assertEqual(central['single_worker_baseline']['clearance_minutes'],20);self.assertEqual(central['proposed']['clearance_minutes'],10)
  self.assertEqual(central['additional_completed_by_horizon'],1)
  self.assertEqual(r['comparisons']['high']['proposed']['completed_by_horizon'],0)
  self.assertEqual(r['service_slots_reserved'],0);self.assertEqual(r['tasks_changed'],0)
 def test_compare_preserves_task_tables(self):
  db=c.care.db_open(self.path);before=[list(db.execute('select * from '+t+' order by 1,2')) for t in ('capability_tasks','capability_events')];db.close()
  c.compare(self.path,self.request)
  db=c.care.db_open(self.path);after=[list(db.execute('select * from '+t+' order by 1,2')) for t in ('capability_tasks','capability_events')];db.close()
  self.assertEqual(before,after)
 def test_missing_durations_or_provenance_never_default(self):
  for change in ({'durations':{}},{'durations':{'other':{'low':1,'central':2,'high':3}}},{'assumption_source':''},{'question':''},{'durations':{'pharmacy':{'low':30,'central':10,'high':20}}}):
   with self.assertRaises(ValueError):c.compare(self.path,{**self.request,**change})
 def test_extra_authority_and_foreign_tasks_rejected(self):
  for change in ({'actor':'clinician'},{'priority':'urgent'},{'task_ids':['other']},{'task_ids':[]},{'task_ids':[self.first['id']]*2}):
   with self.assertRaises(ValueError):c.compare(self.path,{**self.request,**change})
 def test_hold_and_cancel_are_visible_not_scheduled(self):
  self.event(self.first,action='hold');snap=c.snapshot(self.path,'buyer1')
  row=next(t for t in snap['tasks'] if t['id']==self.first['id']);self.assertFalse(row['selectable']);self.assertTrue(row['exclusion_reasons'])
  with self.assertRaises(ValueError):c.compare(self.path,{**self.request,'snapshot_id':snap['id']})
 def test_changed_source_invalidates_without_destroying_history(self):
  r=c.compare(self.path,self.request);self.answer['question']='changed source answer'
  self.assertFalse(c.inspect(self.path,r['id'])['current']);self.assertEqual(len(c.history(self.path,'buyer1')['scenarios']),1)
  with self.assertRaises(ValueError):c.compare(self.path,self.request)
 def test_task_changes_invalidate_even_same_duration(self):
  r=c.compare(self.path,self.request);self.event(self.first)
  row=c.inspect(self.path,r['id']);self.assertFalse(row['current']);self.assertEqual(row['comparisons'],r['comparisons'])
 def test_new_task_and_portfolio_changes_invalidate(self):
  r=c.compare(self.path,self.request);self.cap['capacity_slots']=99;self.update()
  self.assertFalse(c.inspect(self.path,r['id'])['current'])
 def test_idempotent_concurrent_saves_and_immutable_history(self):
  with ThreadPoolExecutor(max_workers=2) as pool:rows=list(pool.map(lambda _:c.compare(self.path,self.request),range(2)))
  self.assertEqual(rows[0],rows[1]);self.assertTrue(c.inspect(self.path,rows[0]['id'])['current'])
  self.assertEqual(len(c.history(self.path,'buyer1')['scenarios']),1)
  db=c.database(self.path)
  with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM scenarios')
  db.close()
 def test_source_race_cannot_commit(self):
  old=c.snapshot;calls=[]
  def changed(*a):
   s=old(*a);calls.append(s)
   if len(calls)>1:s['id']='changed'
   return s
  with patch.object(c,'snapshot',side_effect=changed):
   with self.assertRaisesRegex(ValueError,'during comparison'):c.compare(self.path,self.request)
  self.assertEqual(c.history(self.path,'buyer1')['scenarios'],[])
 def test_complete_unselected_scope_retained(self):
  r=c.compare(self.path,{**self.request,'task_ids':[self.first['id']]})
  self.assertEqual(r['unselected_task_ids'],[self.second['id']]);self.assertEqual(len(r['snapshot']['tasks']),2)
 def test_implementation_change_preserves_stale_result(self):
  r=c.compare(self.path,self.request)
  with patch.object(c,'identity',return_value='new'):
   stale=c.inspect(self.path,r['id']);self.assertFalse(stale['current']);self.assertIn('implementation',stale['stale_reason'])
 def test_tampering_is_not_a_current_result(self):
  r=c.compare(self.path,self.request);db=c.database(self.path)
  db.execute('DROP TRIGGER scenarios_UPDATE');db.execute("UPDATE scenarios SET payload=replace(payload, 'supplied_operational_scenario', 'invented_authority')");db.commit();db.close()
  with self.assertRaisesRegex(ValueError,'integrity'):c.inspect(self.path,r['id'])

if __name__=='__main__':unittest.main()
