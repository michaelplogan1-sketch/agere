import copy,itertools,sqlite3,unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import test_resource_capacity as fixture
from test_resource_capacity import job,pool
from clinical_core.agere.clinical import precedence_capacity as c

def edge(a,b,lag=0):return {'before_task_id':a,'after_task_id':b,'lag_minutes':lag,'reason':'Explicit hypothetical prerequisite; no clinical sequence inferred.'}

class DependencySimulation(unittest.TestCase):
 def test_chain_lag_and_wait_partition(self):
  r=c.simulate([job('a','a',10),job('b','b',5),job('c','b',5)], [pool('a',['a']),pool('b',['b'],1,20)],12,[edge('a','b',3),edge('b','c',2)])
  self.assertEqual([(j['start_minute'],j['finish_minute']) for j in r['schedule']],[(0,10),(20,25),(27,32)])
  self.assertEqual([(j['dependency_delay_minutes'],j['queue_wait_minutes'],j['total_delay_after_ready_minutes']) for j in r['schedule']],[(0,0,0),(13,7,20),(27,0,27)])
  self.assertEqual((r['completed_by_horizon'],r['dependency_waiting_at_horizon'],r['ready_waiting_at_horizon']),(1,2,0))
 def test_late_original_readiness_dominates_lag(self):
  r=c.simulate([job('a','a',5),job('b','a',5,20)],[pool('p',['a'],2)],10,[edge('a','b',3)])
  self.assertEqual(r['schedule'][1]['effective_ready_minute'],20);self.assertEqual(r['schedule'][1]['dependency_delay_minutes'],0)
  self.assertEqual((r['not_ready_at_horizon'],r['dependency_waiting_at_horizon']),(1,0))
 def test_fork_join_waits_for_all_predecessors(self):
  r=c.simulate([job('d','a',2),job('b','a',3),job('c','a',7),job('a','a',2)], [pool('p',['a'],3)],5,[edge('a','b'),edge('a','c'),edge('b','d',5),edge('c','d')])
  self.assertEqual([j['start_minute'] for j in r['schedule']],[10,2,2,0]);self.assertEqual(r['whole_batch_clearance_minutes'],12)
 def test_blocked_roots_propagate_through_graph(self):
  r=c.simulate([job('a','none',3),job('b','off',4),job('c','on',5),job('d','on',5),job('e','on',2)], [pool('off',['off'],0),pool('on',['on'])],10,[edge('a','c'),edge('b','c'),edge('c','d')])
  self.assertEqual((r['blocked_count'],r['direct_resource_blocked_count'],r['dependency_blocked_count']),(4,2,2))
  self.assertEqual([j['blocking_root_task_ids'] for j in r['blocked_tasks']],[['a'],['b'],['a','b'],['a','b']]);self.assertIsNone(r['whole_batch_clearance_minutes']);self.assertEqual(r['completed_by_horizon'],1)
 def test_invalid_scope_cycles_duplicates_lags_and_authority(self):
  bad=[[edge('a','a')],[edge('a','foreign')],[edge('a','b'),edge('b','a')],[edge('a','b')]*2,[{**edge('a','b'),'clinical_priority':'urgent'}]]
  bad += [[edge('a','b',lag)] for lag in (True,-1,1.5,10081)]
  bad += [[{**edge('a','b'),'reason':''}],None]
  for edges in bad:
   with self.assertRaises(ValueError):c.simulate([job('a','x',1),job('b','x',2)],[pool('p',['x'])],10,edges)
 def test_unchanged_resource_policy_skips_unready_tasks(self):
  r=c.simulate([job('b','a',1),job('c','b',1),job('a','a',3,5)], [pool('specific',['a']),pool('flex',['a','b'])],5,[edge('a','b')])
  self.assertEqual([j['start_minute'] for j in r['schedule']],[8,0,5]);self.assertEqual(r['schedule'][2]['pool_id'],'specific')
 def test_exact_dependency_boundary_counts_as_ready_waiting(self):
  r=c.simulate([job('a','x',5),job('b','x',5)],[pool('p',['x'],2)],5,[edge('a','b')])
  self.assertEqual((r['completed_by_horizon'],r['in_progress_at_horizon'],r['ready_waiting_at_horizon'],r['dependency_waiting_at_horizon']),(1,0,1,0))
 def test_accumulated_lags_can_exceed_original_ready_bound(self):
  r=c.simulate([job(str(i),'x',43200) for i in range(3)],[pool('p',['x'],3)],10080,[edge('0','1',10080),edge('1','2',10080)])
  self.assertEqual(r['whole_batch_clearance_minutes'],149760)
 def test_no_dependencies_exactly_reproduces_resource_baseline(self):
  for ready in itertools.product((0,2,5),repeat=3):
   for workers in (0,1,2):
    jobs=[job('a','a',3,ready[0]),job('b','b',2,ready[1]),job('c','a',1,ready[2])];ps=[pool('a',['a'],workers,1),pool('b',['b'],1,3)]
    baseline=c.R.simulate(jobs,ps,4);r=c.simulate(jobs,ps,4,[])
    for k,v in baseline.items():
     if k in ('schedule','blocked_tasks'):
      self.assertEqual([{key:row[key] for key in old} for old,row in zip(v,r[k])],v);self.assertEqual(len(v),len(r[k]))
     else:self.assertEqual(r[k],v,k)
    self.assertEqual(r['dependency_waiting_at_horizon'],0)
 def test_all_small_dags_conserve_work_constraints_and_worker_capacity(self):
  possible=list(itertools.combinations(range(4),2));count=0
  for bits in itertools.product((False,True),repeat=len(possible)):
   edges=[edge(str(a),str(b),a%2) for (a,b),use in zip(possible,bits) if use]
   for workers in (1,2):
    for ready in (0,3):
     jobs=[job(str(i),'x',i+1,ready if i%2 else 0) for i in (3,1,0,2)];ps=[pool('p',['x'],workers,1)]
     r=c.simulate(jobs,ps,5,edges);ss={j['id']:j for j in r['schedule']};count+=1
     self.assertEqual(sum(r[k] for k in ('completed_by_horizon','in_progress_at_horizon','ready_waiting_at_horizon','dependency_waiting_at_horizon','not_ready_at_horizon','blocked_count')),4)
     for e in edges:self.assertGreaterEqual(ss[e['after_task_id']]['start_minute'],ss[e['before_task_id']]['finish_minute']+e['lag_minutes'])
     used=sum(min(j['duration_minutes'],max(0,5-j['start_minute'])) for j in ss.values());self.assertEqual(used+r['remaining_work_minutes'],10)
     for j in ss.values():
      self.assertEqual(j['dependency_delay_minutes']+j['queue_wait_minutes'],j['total_delay_after_ready_minutes']);self.assertGreaterEqual(j['start_minute'],max(1,j['ready_minute']));self.assertGreaterEqual(j['queue_wait_minutes'],0)
     for w in range(1,workers+1):
      ws=sorted([j for j in ss.values() if j['worker_number']==w],key=lambda j:j['start_minute']);self.assertTrue(all(a['finish_minute']<=b['start_minute'] for a,b in zip(ws,ws[1:])))
  self.assertEqual(count,256)
 def test_empty_batch_and_input_immutability(self):
  jobs=[job('a','x',3),job('b','x',2)];ps=[pool('p',['x'],2)];edges=[edge('a','b')];before=copy.deepcopy([jobs,ps,edges]);c.simulate(jobs,ps,2,edges);self.assertEqual([jobs,ps,edges],before)
  self.assertEqual(c.simulate([],ps,1,[])['status'],'empty_batch')

class DependencyWork(unittest.TestCase):
 prepare=fixture.ResourceWork.prepare
 queue=fixture.ResourceWork.queue
 update=fixture.ResourceWork.update
 event=fixture.ResourceWork.event
 def setUp(self):
  fixture.ResourceWork.setUp(self)
  self.resource=c.R.compare(self.path,self.resource_request)
  self.dependency_request={'parent_id':self.resource['id'],'question':'What if the second task waits for a handoff from the first?', 'assumption_source':'Supplied engineering assumptions; no real clinical prerequisite.', 'dependencies':[edge(self.first['id'],self.second['id'],10)]}
 def test_preserved_parent_scope_and_exact_baseline(self):
  row=c.compare(self.path,self.dependency_request);self.assertEqual(row['original_question'],self.request['question']);self.assertEqual(row['resource_question'],self.resource['question']);self.assertEqual(row['selected_task_ids'],self.original['selected_task_ids'])
  for case,x in row['comparisons'].items():self.assertEqual(x['without_dependencies'],self.resource['comparisons'][case]['proposed'])
  central=row['comparisons']['central'];self.assertEqual(central['without_dependencies']['whole_batch_clearance_minutes'],30);self.assertEqual(central['with_dependencies']['whole_batch_clearance_minutes'],35)
  self.assertEqual(row['tasks_changed'],0);self.assertEqual(row['service_slots_reserved'],0)
 def test_concurrent_replay_and_immutable_history(self):
  with ThreadPoolExecutor(max_workers=2) as ex:rows=list(ex.map(lambda _:c.compare(self.path,self.dependency_request),range(2)))
  self.assertEqual(rows[0],rows[1]);self.assertTrue(c.inspect(self.path,rows[0]['id'])['current']);self.assertEqual(len(c.history(self.path,self.resource['id'])['scenarios']),1)
  db=c.database(self.path)
  for op in ('UPDATE scenarios SET buyer=buyer','DELETE FROM scenarios'):
   with self.assertRaises(sqlite3.IntegrityError):db.execute(op)
  db.close()
 def test_changed_task_portfolio_or_implementation_retains_historical_result(self):
  row=c.compare(self.path,self.dependency_request)
  with patch.object(c,'identity',return_value='changed'):self.assertFalse(c.inspect(self.path,row['id'])['current'])
  self.event(self.first,action='hold');stale=c.inspect(self.path,row['id']);self.assertFalse(stale['current']);self.assertEqual(stale['comparisons'],row['comparisons'])
  with self.assertRaises(ValueError):c.compare(self.path,self.dependency_request)
 def test_unknown_scope_provenance_and_extra_authority_fail_before_saving(self):
  for change in ({'dependencies':[edge(self.first['id'],'foreign')]},{'question':''},{'assumption_source':''},{'actor':'clinician'},{'dependencies':[edge(self.first['id'],self.second['id']),edge(self.second['id'],self.first['id'])]}):
   with self.assertRaises(ValueError):c.compare(self.path,{**self.dependency_request,**change})
  self.assertEqual(c.history(self.path,self.resource['id'])['scenarios'],[])
 def test_parent_race_rolls_back(self):
  old=c.parent;calls=[]
  def raced(*args):
   row=old(*args);calls.append(1)
   if len(calls)>1:row['question']='Changed'
   return row
  with patch.object(c,'parent',side_effect=raced):
   with self.assertRaisesRegex(ValueError,'during dependency'):c.compare(self.path,self.dependency_request)
  self.assertEqual(c.history(self.path,self.resource['id'])['scenarios'],[])
 def test_prior_resource_and_work_history_remain_unchanged(self):
  db=c.R.base.care.db_open(self.path);before={t:list(db.execute('SELECT * FROM '+t+' ORDER BY 1,2')) for t in ('capability_tasks','capability_events')};db.close()
  c.compare(self.path,self.dependency_request)
  db=c.R.base.care.db_open(self.path);after={t:list(db.execute('SELECT * FROM '+t+' ORDER BY 1,2')) for t in before};db.close();self.assertEqual(before,after);self.assertEqual(c.parent(self.path,self.resource['id']),self.resource)
 def test_corrupt_result_and_corrupt_baseline_rejected(self):
  bad=copy.deepcopy(self.resource);bad['comparisons']['central']['proposed']['completed_by_horizon']=99
  with self.assertRaisesRegex(ValueError,'does not reproduce'):c.calculate(bad,self.dependency_request)
  row=c.compare(self.path,self.dependency_request);db=c.database(self.path);db.execute('DROP TRIGGER scenarios_UPDATE');db.execute("UPDATE scenarios SET payload=replace(payload,'supplied_operational_scenario','clinical_approval')");db.commit();db.close()
  with self.assertRaisesRegex(ValueError,'integrity'):c.inspect(self.path,row['id'])
 def test_blocked_parent_scenario_is_preserved_without_fake_clearance(self):
  blocked=c.R.compare(self.path,{**self.resource_request,'pools':[pool('p',['pharmacy'],0)]})
  row=c.compare(self.path,{**self.dependency_request,'parent_id':blocked['id']});self.assertEqual(row['comparisons']['central']['with_dependencies']['blocked_count'],2);self.assertIsNone(row['comparisons']['central']['whole_batch_minutes_difference'])

if __name__=='__main__':unittest.main()
