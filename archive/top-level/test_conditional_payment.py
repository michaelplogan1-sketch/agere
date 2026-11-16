import copy,sqlite3,unittest
from fractions import Fraction
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import test_work_cost as fixture
from clinical_core.agere.clinical import conditional_payment as p

def request(cost,patients):
 return {'cost_id':cost['id'],'question':'What would these supplied payments cover for this fixed work?',
  'terms':{'id':'engineering-example','version':'1','label':'Hypothetical payment terms','source':'Synthetic engineering assumption; not a payer contract','currency':'USD','unit_kind':'selected_patient','unit_definition':'One payment per distinct selected patient for this fixed batch','payment_per_unit':'50.00','period_start':'2029-01-01','period_end':'2029-12-31'},
  'cost_window':'whole_batch','cost_basis':'available_staff_time','alignment_rationale':'Hypothetical single batch within this period; actual alignment unverified',
  'assumed_payable_unit_ids':patients,'eligibility_assumption':'Assume these units payable solely for engineering comparison; no eligibility verified'}

class Arithmetic(unittest.TestCase):
 def test_exact_cent_threshold_rounds_up(self):
  r=p.arithmetic(Fraction(100,3),10,3,4)
  self.assertEqual(p.C.exact(r['required_rate_at_assumed_units']),Fraction(100,9))
  self.assertEqual(p.C.exact(r['minimum_whole_cent_rate']),12)
  self.assertEqual(r['required_payable_units_at_supplied_rate'],4)
  self.assertTrue(r['within_selected_unit_scope'])
 def test_zero_units_do_not_divide_or_claim_eligibility(self):
  r=p.arithmetic(100,20,0,4);self.assertIsNone(r['required_rate_at_assumed_units']);self.assertIsNone(r['minimum_whole_cent_rate'])
  self.assertEqual(p.C.exact(r['receipts_minus_modeled_cost']),-100);self.assertFalse(r['within_selected_unit_scope'])
 def test_zero_rate_and_zero_cost_have_distinct_thresholds(self):
  a=p.arithmetic(10,0,2,2);b=p.arithmetic(0,0,2,2)
  self.assertIsNone(a['required_payable_units_at_supplied_rate']);self.assertFalse(a['within_selected_unit_scope'])
  self.assertEqual(b['required_payable_units_at_supplied_rate'],0);self.assertTrue(b['within_selected_unit_scope'])
 def test_fixed_scope_sensitivity_and_minimal_thresholds(self):
  for cost in (0,Fraction(1,7),20,2001):
   for rate in (0,1,100):
    for n in range(5):
     r=p.arithmetic(cost,rate,n,4)
     self.assertEqual(len(r['sensitivity']),5)
     for s in r['sensitivity']:self.assertEqual(p.C.exact(s['receipts_minus_modeled_cost']),rate*s['assumed_payable_units']-cost)
     if n:
      threshold=p.C.exact(r['minimum_whole_cent_rate']);self.assertGreaterEqual(n*threshold,cost)
      if threshold:self.assertLess(n*(threshold-1),cost)
     units=r['required_payable_units_at_supplied_rate']
     if units is not None:
      self.assertGreaterEqual(units*rate,cost)
      if units:self.assertLess((units-1)*rate,cost)
 def test_invalid_numeric_counts_rejected(self):
  for n in (-1,True,1.0,'1',5):
   with self.assertRaises(ValueError):p.arithmetic(1,1,n,4)

class PaymentWork(unittest.TestCase):
 prepare=fixture.CostWork.prepare
 queue=fixture.CostWork.queue
 update=fixture.CostWork.update
 event=fixture.CostWork.event
 def setUp(self):
  fixture.CostWork.setUp(self);self.cost=p.C.compare(self.path,self.cost_request);self.patients=p.X.population(self.path,self.cost);self.req=request(self.cost,self.patients)
 def test_cost_question_scope_and_conditional_balances(self):
  r=p.compare(self.path,self.req);self.assertEqual(r['selected_task_ids'],self.cost['selected_task_ids']);self.assertEqual(r['patient_ids'],self.patients)
  a=r['comparisons']['central'];receipts=5000*len(self.patients)
  self.assertEqual(p.C.exact(a['baseline']['receipts_minus_modeled_cost']),receipts-7000)
  self.assertEqual(p.C.exact(a['proposed']['receipts_minus_modeled_cost']),receipts-8000)
  self.assertEqual(p.C.exact(a['proposed_minus_baseline_balance']),-1000)
  self.assertTrue(p.inspect(self.path,r['id'])['current']);self.assertFalse(r['program_revenue_established']);self.assertIsNone(r['verified_eligible_units'])
  self.assertEqual(len(r['requirements']),3+2*len(self.patients));self.assertTrue(all(x['status']=='unresolved' and not x['evidence_bound'] for x in r['requirements']))
 def test_unit_types_and_zero_assumptions_preserve_fixed_cost(self):
  for kind in ('selected_task','selected_batch','selected_patient'):
   req=copy.deepcopy(self.req);req['terms'].update(id=kind,unit_kind=kind);req['assumed_payable_unit_ids']=[]
   r=p.compare(self.path,req);self.assertEqual(r['maximum_selected_units'],len(p.units(self.cost,self.patients,kind)))
   self.assertEqual(p.C.exact(r['comparisons']['central']['proposed']['modeled_cost']),8000)
   self.assertEqual(p.C.exact(r['comparisons']['central']['proposed']['hypothetical_receipts']),0)
 def test_foreign_duplicate_and_wrong_type_units_rejected(self):
  for ids in (['foreign'],self.patients*2,None,{},[1]):
   with self.assertRaises(ValueError):p.compare(self.path,{**self.req,'assumed_payable_unit_ids':ids})
  self.assertEqual(p.history(self.path,self.cost['id'])['scenarios'],[])
 def test_exact_terms_currency_dates_sources_and_authority_required(self):
  for change in ({'currency':'EUR'},{'payment_per_unit':12},{'payment_per_unit':'0.001'},{'source':''},{'unit_definition':''},{'unit_kind':'patient_month'},{'period_end':'2028-12-31'},{'period_start':'20290101'},{'period_start':'2029-02-30'},{'verified_contract':True}):
   with self.assertRaises(ValueError):p.compare(self.path,{**self.req,'terms':{**self.req['terms'],**change}})
  for change in ({'cost_basis':'revenue'},{'cost_window':'future'},{'eligibility_assumption':''},{'alignment_rationale':''},{'signed_contract_established':True}):
   with self.assertRaises(ValueError):p.compare(self.path,{**self.req,**change})
 def test_version_conflict_requires_new_version(self):
  first=p.compare(self.path,self.req);changed=copy.deepcopy(self.req);changed['terms']['payment_per_unit']='60.00'
  with self.assertRaisesRegex(ValueError,'new version'):p.compare(self.path,changed)
  changed['terms']['version']='2';second=p.compare(self.path,changed)
  self.assertNotEqual(first['id'],second['id']);self.assertEqual(len(p.history(self.path,self.cost['id'])['scenarios']),2)
  self.assertTrue(p.inspect(self.path,first['id'])['current']);self.assertEqual(p.inspect(self.path,first['id'])['request']['terms']['payment_per_unit'],'50.00')
 def test_changed_unit_assumptions_reuse_same_terms_without_rewrite(self):
  first=p.compare(self.path,self.req);second=p.compare(self.path,{**self.req,'assumed_payable_unit_ids':[]})
  self.assertNotEqual(first['id'],second['id']);self.assertEqual(first['terms_hash'],second['terms_hash'])
 def test_replay_concurrency_and_immutable_terms_scenarios(self):
  with ThreadPoolExecutor(max_workers=2) as ex:rows=list(ex.map(lambda _:p.compare(self.path,self.req),range(2)))
  self.assertEqual(rows[0],rows[1]);self.assertEqual(len(p.history(self.path,self.cost['id'])['scenarios']),1);db=p.database(self.path)
  for table in ('terms','scenarios'):
   for sql in ('DELETE FROM '+table,'UPDATE '+table+' SET payload=payload'):
    with self.assertRaises(sqlite3.IntegrityError):db.execute(sql)
  db.close()
 def test_concurrent_terms_conflict_has_one_winner(self):
  other=copy.deepcopy(self.req);other['terms']['payment_per_unit']='100.00'
  def save(req):
   try:return p.compare(self.path,req)
   except ValueError as e:return str(e)
  with ThreadPoolExecutor(max_workers=2) as ex:rows=list(ex.map(save,[self.req,other]))
  self.assertEqual(sum(isinstance(r,dict) for r in rows),1);self.assertEqual(len(p.history(self.path,self.cost['id'])['scenarios']),1)
 def test_blocked_batch_never_produces_payment_balance(self):
  altered=copy.deepcopy(self.cost);altered['comparisons']['central']['proposed']['whole_batch']={'status':'unavailable_blocked_batch','reason':'Blocked work','blocked_task_ids':['one']}
  r=p.calculate(altered,self.patients,self.req);self.assertNotIn('receipts_minus_modeled_cost',r['comparisons']['central']['proposed']);self.assertIsNone(r['comparisons']['central']['proposed_minus_baseline_balance'])
 def test_parent_changes_invalidate_without_deleting_history(self):
  r=p.compare(self.path,self.req);self.event(self.first,action='hold');old=p.inspect(self.path,r['id']);self.assertFalse(old['current']);self.assertEqual(old['comparisons'],r['comparisons'])
  with self.assertRaises(ValueError):p.compare(self.path,self.req)
 def test_implementation_change_stale(self):
  r=p.compare(self.path,self.req)
  with patch.object(p,'identity',return_value='changed'):self.assertFalse(p.inspect(self.path,r['id'])['current'])
 def test_scope_race_saves_neither_terms_nor_scenario(self):
  original=p.scope;calls=[]
  def race(*a):
   c,ids=original(*a);calls.append(1)
   if len(calls)>1:c['question']='changed'
   return c,ids
  with patch.object(p,'scope',side_effect=race):
   with self.assertRaisesRegex(ValueError,'during payment'):p.compare(self.path,self.req)
  db=p.database(self.path);self.assertEqual(db.execute('SELECT count(*) FROM terms').fetchone()[0],0);self.assertEqual(db.execute('SELECT count(*) FROM scenarios').fetchone()[0],0);db.close()
 def test_original_cost_work_and_financial_stores_untouched(self):
  db=p.C.R.base.care.db_open(self.path);before={t:list(db.execute('SELECT * FROM '+t+' ORDER BY 1,2')) for t in ('capability_tasks','capability_events')};db.close();p.compare(self.path,self.req)
  db=p.C.R.base.care.db_open(self.path);after={t:list(db.execute('SELECT * FROM '+t+' ORDER BY 1,2')) for t in before};db.close();self.assertEqual(before,after)
  self.assertEqual(p.X.parent(self.path,self.cost['id']),self.cost);self.assertFalse((self.path/'work-cost-context.sqlite3').exists())
 def test_tampered_terms_or_scenario_rejected(self):
  r=p.compare(self.path,self.req);db=p.database(self.path);db.execute('DROP TRIGGER terms_UPDATE');db.execute("UPDATE terms SET hash='changed'");db.commit();db.close()
  with self.assertRaisesRegex(ValueError,'registration integrity'):p.inspect(self.path,r['id'])

if __name__=='__main__':unittest.main()
