import copy,sqlite3,unittest
from fractions import Fraction
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import test_precedence_capacity as fixture
from test_resource_capacity import job,pool
from clinical_core.agere.clinical import work_cost as c

class CostArithmetic(unittest.TestCase):
 def test_exact_decimal_and_half_cent_rounding(self):
  self.assertEqual(c.cents('120.5','Rate'),12050);self.assertEqual(c.money(Fraction(1,2))['display_amount'],'0.01');self.assertEqual(c.money(Fraction(-1,2))['display_amount'],'-0.01')
  self.assertEqual(c.exact(c.money(Fraction(1,3))),Fraction(1,3));self.assertEqual(c.money(Fraction(1,3))['display_amount'],'0.00')
 def test_invalid_money_never_coerced(self):
  for value in (1,True,None,'-1','0.001','NaN','Infinity','1e3',' 1','01.00','1,000','1.','1000000000000'):
   with self.assertRaises(ValueError):c.cents(value,'Money')
 def test_occupied_idle_and_fixed_cost_are_not_double_counted(self):
  ps=[pool('p',['x'],2,5)];sim=c.R.simulate([{**job('a','x',10),'patient_id':'one'},{**job('b','x',5,15),'patient_id':'one'}],ps,15)
  r=c.window_cost(sim,ps,{'p':6000},2000,15,1)
  self.assertEqual(c.exact(r['occupied_labor_cost']),1000);self.assertEqual(c.exact(r['available_staff_labor_cost']),2000);self.assertEqual(c.exact(r['idle_staff_labor_cost']),1000)
  self.assertEqual(c.exact(r['cost_views']['occupied_time']['total']),3000);self.assertEqual(c.exact(r['cost_views']['available_staff_time']['total']),4000)
  self.assertEqual(c.exact(r['cost_views']['occupied_time']['per_selected_patient']),3000);self.assertEqual(c.exact(r['cost_views']['occupied_time']['per_selected_task']),1500)
 def test_fractional_cents_sum_before_rounding(self):
  ps=[pool('p',['x'],3)];sim=c.R.simulate([{**job(str(i),'x',1),'patient_id':'p'} for i in range(3)],ps,1)
  r=c.window_cost(sim,ps,{'p':1},0,1,1)
  self.assertEqual(c.exact(r['occupied_labor_cost']),Fraction(1,20));self.assertEqual(sum(c.exact(t['occupied_labor_cost']) for t in r['task_occupied_costs']),Fraction(1,20))
 def test_blocked_whole_batch_unavailable_but_horizon_cost_explicit(self):
  ps=[pool('p',['x'],0)];sim=c.R.simulate([{**job('a','x',10),'patient_id':'p'}],ps,15)
  self.assertEqual(c.window_cost(sim,ps,{'p':6000},100,sim['whole_batch_clearance_minutes'],1)['status'],'unavailable_blocked_batch')
  r=c.window_cost(sim,ps,{'p':6000},100,15,1);self.assertEqual(r['blocked_task_ids'],['a']);self.assertEqual(c.exact(r['cost_views']['occupied_time']['total']),100)
 def test_unused_team_capacity_counts_until_global_window_end(self):
  ps=[pool('p',['x']),pool('unused',['y'],2,5)];sim=c.R.simulate([{**job('a','x',10),'patient_id':'p'}],ps,20)
  r=c.window_cost(sim,ps,{'p':6000,'unused':12000},0,10,1)
  self.assertEqual(c.exact(r['available_staff_labor_cost']),3000);self.assertEqual(c.exact(r['occupied_labor_cost']),1000)
 def test_costs_conserve_across_pool_rates_and_horizons(self):
  for rate in (0,1,29,6000):
   for horizon in (1,5,10,20):
    ps=[pool('a',['a'],2,3),pool('b',['b'],1,7)];js=[{**job('a','a',5),'patient_id':'p'},{**job('b','b',8),'patient_id':'p'}];sim=c.R.simulate(js,ps,horizon);r=c.window_cost(sim,ps,{'a':rate,'b':rate+1},111,horizon,1)
    self.assertEqual(sum(c.exact(p['occupied_labor_cost']) for p in r['pools']),c.exact(r['occupied_labor_cost']))
    self.assertEqual(c.exact(r['idle_staff_labor_cost'])+c.exact(r['occupied_labor_cost']),c.exact(r['available_staff_labor_cost']))
    for view in ('occupied_time','available_staff_time'):self.assertEqual(c.exact(r['cost_views'][view]['per_selected_task'])*2,c.exact(r['cost_views'][view]['total']))

class CostWork(unittest.TestCase):
 prepare=fixture.DependencyWork.prepare
 queue=fixture.DependencyWork.queue
 update=fixture.DependencyWork.update
 event=fixture.DependencyWork.event
 def setUp(self):
  fixture.DependencyWork.setUp(self);self.dep=c.P.compare(self.path,self.dependency_request)
  self.cost_request={'parent_type':'dependency','parent_id':self.dep['id'],'question':'How do handoffs affect task and available-staff cost?', 'assumption_source':'Engineering arithmetic using supplied costs, not measured costs or clinical value.','currency':'USD','rates':[{'pool_id':'pharmacy','hourly_rate':'60.00','source':'Supplied hypothetical loaded hourly rate.'}],'fixed_costs':[{'id':'setup','label':'Batch setup','amount':'20.00','source':'Supplied one-time batch assumption.'}]}
 def test_dependency_question_scope_baseline_and_costs(self):
  r=c.compare(self.path,self.cost_request);self.assertEqual(r['original_question'],self.dep['question']);self.assertEqual(r['selected_task_ids'],self.original['selected_task_ids'])
  central=r['comparisons']['central'];self.assertEqual(c.exact(central['proposed']['whole_batch']['cost_views']['occupied_time']['total']),4000)
  self.assertEqual(c.exact(central['baseline']['whole_batch']['cost_views']['available_staff_time']['total']),7000)
  self.assertEqual(c.exact(central['proposed']['whole_batch']['cost_views']['available_staff_time']['total']),8000)
  self.assertEqual(c.exact(central['proposed_minus_baseline']['whole_batch']['available_staff_time']),1000)
  self.assertFalse(r['realized_savings_established']);self.assertFalse(r['revenue_established']);self.assertEqual(r['tasks_changed'],0)
 def test_resource_parent_uses_its_one_worker_baseline(self):
  r=c.compare(self.path,{**self.cost_request,'parent_type':'resource','parent_id':self.resource['id']});central=r['comparisons']['central']
  self.assertEqual(c.exact(central['baseline']['whole_batch']['available_staff_labor_cost']),2500);self.assertEqual(c.exact(central['proposed']['whole_batch']['available_staff_labor_cost']),5000)
 def test_missing_and_foreign_rates_currency_and_authority_rejected(self):
  for change in ({'rates':[]},{'rates':self.cost_request['rates']*2},{'rates':[{**self.cost_request['rates'][0],'pool_id':'foreign'}]},{'currency':'EUR'},{'fixed_costs':None},{'fixed_costs':self.cost_request['fixed_costs']*2},{'question':''},{'assumption_source':''},{'verified_contract':True}):
   with self.assertRaises(ValueError):c.compare(self.path,{**self.cost_request,**change})
  self.assertEqual(c.history(self.path,'dependency',self.dep['id'])['scenarios'],[])
 def test_zero_rates_and_no_fixed_costs_are_explicit_valid_assumptions(self):
  r=c.compare(self.path,{**self.cost_request,'rates':[{**self.cost_request['rates'][0],'hourly_rate':'0'}],'fixed_costs':[]})
  self.assertEqual(r['request']['rates'][0]['hourly_rate'],'0.00');self.assertEqual(c.exact(r['comparisons']['central']['proposed']['whole_batch']['cost_views']['occupied_time']['total']),0)
 def test_concurrent_replay_immutable_result(self):
  with ThreadPoolExecutor(max_workers=2) as ex:rows=list(ex.map(lambda _:c.compare(self.path,self.cost_request),range(2)))
  self.assertEqual(rows[0],rows[1]);self.assertTrue(c.inspect(self.path,rows[0]['id'])['current']);self.assertEqual(len(c.history(self.path,'dependency',self.dep['id'])['scenarios']),1)
  db=c.database(self.path)
  for sql in ('DELETE FROM scenarios','UPDATE scenarios SET buyer=buyer'):
   with self.assertRaises(sqlite3.IntegrityError):db.execute(sql)
  db.close()
 def test_changed_parent_or_implementation_keeps_historical_receipt(self):
  r=c.compare(self.path,self.cost_request)
  with patch.object(c,'identity',return_value='changed'):self.assertFalse(c.inspect(self.path,r['id'])['current'])
  self.event(self.first,action='hold');old=c.inspect(self.path,r['id']);self.assertFalse(old['current']);self.assertEqual(old['comparisons'],r['comparisons'])
  with self.assertRaises(ValueError):c.compare(self.path,self.cost_request)
 def test_parent_race_cannot_save(self):
  old=c.parent;calls=[]
  def raced(*a):
   result=old(*a);calls.append(1)
   if len(calls)>1:result['question']='changed'
   return result
  with patch.object(c,'parent',side_effect=raced):
   with self.assertRaisesRegex(ValueError,'during cost'):c.compare(self.path,self.cost_request)
  self.assertEqual(c.history(self.path,'dependency',self.dep['id'])['scenarios'],[])
 def test_original_work_and_dependency_scenarios_remain_unchanged(self):
  db=c.R.base.care.db_open(self.path);before={t:list(db.execute('SELECT * FROM '+t+' ORDER BY 1,2')) for t in ('capability_tasks','capability_events')};db.close();c.compare(self.path,self.cost_request)
  db=c.R.base.care.db_open(self.path);after={t:list(db.execute('SELECT * FROM '+t+' ORDER BY 1,2')) for t in before};db.close();self.assertEqual(before,after);self.assertEqual(c.parent(self.path,'dependency',self.dep['id']),self.dep)
 def test_tampered_saved_cost_rejected(self):
  r=c.compare(self.path,self.cost_request);db=c.database(self.path);db.execute('DROP TRIGGER scenarios_UPDATE');db.execute("UPDATE scenarios SET payload=replace(payload,'supplied_operational_cost_scenario','verified_savings')");db.commit();db.close()
  with self.assertRaisesRegex(ValueError,'integrity'):c.inspect(self.path,r['id'])

if __name__=='__main__':unittest.main()
