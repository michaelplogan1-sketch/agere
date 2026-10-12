import copy,itertools,sqlite3,unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import test_work_capacity as fixture
from clinical_core.agere.clinical import resource_capacity as c

def pool(key,services,workers=1,start=0):
 return {'id':key,'name':'Synthetic '+key,'workers':workers,'available_from_minute':start,'service_ids':services,'assumption_source':'Supplied engineering assumptions, not verified staffing.'}
def job(key,service,duration,ready=0):return {'id':key,'service_id':service,'duration_minutes':duration,'ready_minute':ready}

class ResourceSimulation(unittest.TestCase):
 def test_service_restrictions_and_ready_times(self):
  r=c.simulate([job('a','resp',30),job('b','behavior',20,10),job('c','resp',30),job('d','behavior',20,60)], [pool('resp',['resp'],2),pool('behavior',['behavior'],1,15)],30)
  self.assertEqual([(j['start_minute'],j['finish_minute']) for j in r['schedule']],[(0,30),(15,35),(0,30),(60,80)])
  self.assertEqual(r['whole_batch_clearance_minutes'],80);self.assertEqual(r['mean_queue_wait_minutes_schedulable'],1.25)
  self.assertEqual((r['completed_by_horizon'],r['in_progress_at_horizon'],r['ready_waiting_at_horizon'],r['not_ready_at_horizon'],r['blocked_count']),(2,1,0,1,0))
 def test_flexible_worker_not_consumed_when_specific_worker_available(self):
  r=c.simulate([job('a','a',10),job('b','b',10)],[pool('a-flex',['a','b']),pool('z-specific',['a'])],10)
  self.assertEqual([j['pool_id'] for j in r['schedule']],['z-specific','a-flex']);self.assertEqual(r['completed_by_horizon'],2)
 def test_ready_jobs_do_not_block_on_earlier_unready_task(self):
  r=c.simulate([job('a','a',10,100),job('b','a',5,0)],[pool('p',['a'])],20)
  self.assertEqual([j['start_minute'] for j in r['schedule']],[100,0]);self.assertEqual(r['not_ready_at_horizon'],1)
 def test_no_capable_pool_and_no_staff_are_separate(self):
  r=c.simulate([job('a','a',10),job('b','b',10),job('c','c',10)],[pool('p',['a']),pool('q',['b'],0)],20)
  self.assertEqual([j['reason'] for j in r['blocked_tasks']],['no_staff_in_capable_pools','no_declared_capable_pool'])
  self.assertIsNone(r['whole_batch_clearance_minutes']);self.assertEqual(r['schedulable_clearance_minutes'],10);self.assertEqual(r['remaining_work_minutes'],20)
 def test_delayed_pool_and_exact_boundary(self):
  r=c.simulate([job('a','a',10,5)],[pool('p',['a'],1,10)],10)
  self.assertEqual(r['ready_waiting_at_horizon'],1);self.assertEqual(r['in_progress_at_horizon'],0);self.assertIsNone(r['assumed_utilization'])
  self.assertEqual(r['mean_queue_wait_minutes_schedulable'],5)
 def test_pool_and_task_permutations_are_deterministic_for_fixed_fifo(self):
  ps=[pool('p',['a'],2),pool('q',['a'],1)];jobs=[job(str(i),'a',1) for i in range(6)]
  self.assertEqual(c.simulate(jobs,ps,5),c.simulate(jobs,ps[::-1],5))
 def test_zero_ready_single_pool_matches_frozen_fifo_baseline(self):
  for ds in itertools.product((1,2,4),repeat=4):
   for count in (1,2,3):
    jobs=[job(str(i),'a',d) for i,d in enumerate(ds)]
    old=c.base.simulate(jobs,count,3);new=c.simulate(jobs,[pool('p',['a'],count)],3)
    self.assertEqual([(j['id'],j['start_minute'],j['finish_minute']) for j in old['schedule']],[(j['id'],j['start_minute'],j['finish_minute']) for j in new['schedule']])
    self.assertEqual(old['remaining_work_minutes'],new['remaining_work_minutes']);self.assertEqual(old['clearance_minutes'],new['whole_batch_clearance_minutes'])
 def test_small_resource_scenarios_conserve_and_never_cross_capabilities(self):
  for ready in itertools.product((0,2,5),repeat=3):
   for enabled in (0,1,2):
    jobs=[job('a','a',3,ready[0]),job('b','b',2,ready[1]),job('c','a',1,ready[2])];ps=[pool('a',['a'],enabled,1),pool('b',['b'],1,3)];r=c.simulate(jobs,ps,4)
    self.assertEqual(sum(r[k] for k in ('completed_by_horizon','in_progress_at_horizon','ready_waiting_at_horizon','not_ready_at_horizon','blocked_count')),3)
    used=0
    for j in r['schedule']:
     p=next(p for p in ps if p['id']==j['pool_id']);self.assertIn(j['service_id'],p['service_ids']);self.assertGreaterEqual(j['start_minute'],max(j['ready_minute'],p['available_from_minute']))
     used+=min(j['duration_minutes'],max(0,4-j['start_minute']))
    self.assertEqual(used+r['remaining_work_minutes'],6)
    for p in ps:
     for w in range(1,p['workers']+1):
      ss=sorted([j for j in r['schedule'] if j['pool_id']==p['id'] and j['worker_number']==w],key=lambda j:j['start_minute'])
      self.assertTrue(all(a['finish_minute']<=b['start_minute'] for a,b in zip(ss,ss[1:])))
 def test_invalid_resources_and_ready_times_rejected(self):
  for ps in ([],[pool('a',['a'])]*2,[pool('a',[])],[pool('a',['a'],True)],[pool('a',['a'],129)],None):
   with self.assertRaises(ValueError):c.simulate([job('a','a',2)],ps,10)
  for ready in (False,-1,1.5,None):
   with self.assertRaises(ValueError):c.simulate([job('a','a',2,ready)],[pool('a',['a'])],10)

class ResourceWork(unittest.TestCase):
 prepare=fixture.WorkCapacity.prepare
 queue=fixture.WorkCapacity.queue
 update=fixture.WorkCapacity.update
 event=fixture.WorkCapacity.event
 def setUp(self):
  fixture.WorkCapacity.setUp(self)
  self.original=c.base.compare(self.path,self.request)
  self.resource_request={'parent_id':self.original['id'],'question':'What if pharmacy review begins later and this task is not ready?',
   'assumption_source':'Explicit synthetic task readiness scenario. No measured clinical data.', 'pools':[pool('pharmacy',['pharmacy'],2,5)],
   'ready_minutes':{self.first['id']:0,self.second['id']:20}}
 def test_question_returns_to_original_task_scope_and_baselines(self):
  r=c.compare(self.path,self.resource_request);self.assertEqual(r['parent_id'],self.original['id']);self.assertEqual(r['selected_task_ids'],self.original['selected_task_ids'])
  self.assertEqual(r['original_question'],self.request['question']);self.assertEqual(r['question'],self.resource_request['question'])
  self.assertEqual(r['comparisons']['central']['proposed']['whole_batch_clearance_minutes'],30)
  self.assertEqual(set(r['comparisons']['central']),{'proposed','one_worker_per_active_pool','unrestricted_single_worker_reference'})
  self.assertEqual(c.base.inspect(self.path,self.original['id'])['comparisons'],self.original['comparisons'])
 def test_no_duration_or_readiness_is_invented(self):
  for change in ({'ready_minutes':{}},{'ready_minutes':{**self.resource_request['ready_minutes'],'foreign':0}},{'pools':[pool('x',['foreign'])]},{'assumption_source':''},{'question':''},{'priority':'urgent'}):
   with self.assertRaises(ValueError):c.compare(self.path,{**self.resource_request,**change})
 def test_zero_staff_result_is_saved_with_explicit_blockage(self):
  r=c.compare(self.path,{**self.resource_request,'pools':[pool('p',['pharmacy'],0)]})
  proposed=r['comparisons']['central']['proposed'];self.assertEqual(proposed['blocked_count'],2);self.assertIsNone(proposed['whole_batch_clearance_minutes'])
  self.assertEqual(r['comparisons']['central']['one_worker_per_active_pool']['blocked_count'],2)
  self.assertEqual(r['comparisons']['central']['unrestricted_single_worker_reference']['blocked_count'],0)
 def test_concurrent_replay_and_immutable_history(self):
  with ThreadPoolExecutor(max_workers=2) as ex:rows=list(ex.map(lambda _:c.compare(self.path,self.resource_request),range(2)))
  self.assertEqual(rows[0],rows[1]);self.assertTrue(c.inspect(self.path,rows[0]['id'])['current']);self.assertEqual(len(c.history(self.path,self.original['id'])['scenarios']),1)
  db=c.database(self.path)
  with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM scenarios')
  db.close()
 def test_changed_work_keeps_prior_comparison_as_history(self):
  r=c.compare(self.path,self.resource_request);self.event(self.first,action='hold')
  stale=c.inspect(self.path,r['id']);self.assertFalse(stale['current']);self.assertEqual(stale['comparisons'],r['comparisons'])
  self.assertEqual(len(c.history(self.path,self.original['id'])['scenarios']),1)
  with self.assertRaises(ValueError):c.compare(self.path,self.resource_request)
 def test_source_change_and_implementation_change_are_stale(self):
  r=c.compare(self.path,self.resource_request)
  with patch.object(c,'identity',return_value='changed'):self.assertFalse(c.inspect(self.path,r['id'])['current'])
  self.answer['question']='changed';self.assertFalse(c.inspect(self.path,r['id'])['current'])
 def test_parent_race_cannot_save(self):
  old=c.parent;calls=[]
  def raced(*a):
   result=old(*a);calls.append(1)
   if len(calls)>1:result['question']='changed'
   return result
  with patch.object(c,'parent',side_effect=raced):
   with self.assertRaisesRegex(ValueError,'during resource'):c.compare(self.path,self.resource_request)
  self.assertEqual(c.history(self.path,self.original['id'])['scenarios'],[])
 def test_all_work_and_original_comparison_unchanged(self):
  db=c.base.care.db_open(self.path);before={t:list(db.execute('SELECT * FROM '+t+' ORDER BY 1,2')) for t in ('capability_tasks','capability_events')};db.close()
  c.compare(self.path,self.resource_request)
  db=c.base.care.db_open(self.path);after={t:list(db.execute('SELECT * FROM '+t+' ORDER BY 1,2')) for t in before};db.close()
  self.assertEqual(before,after);self.assertTrue(c.base.inspect(self.path,self.original['id'])['current'])
 def test_corrupt_stored_result_is_rejected(self):
  r=c.compare(self.path,self.resource_request);db=c.database(self.path);db.execute('DROP TRIGGER scenarios_UPDATE');db.execute("UPDATE scenarios SET payload=replace(payload,'supplied_operational_scenario','clinical_approval')");db.commit();db.close()
  with self.assertRaisesRegex(ValueError,'integrity'):c.inspect(self.path,r['id'])

if __name__=='__main__':unittest.main()
