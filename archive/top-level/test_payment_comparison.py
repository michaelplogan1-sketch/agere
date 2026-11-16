import copy,sqlite3,unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
import test_conditional_payment as fixture
from clinical_core.agere.clinical import payment_comparison as b

class PaymentDifference(unittest.TestCase):
 prepare=fixture.PaymentWork.prepare
 queue=fixture.PaymentWork.queue
 update=fixture.PaymentWork.update
 event=fixture.PaymentWork.event
 def setUp(self):
  fixture.PaymentWork.setUp(self);self.left=b.P.compare(self.path,self.req);self.changed=copy.deepcopy(self.req)
  self.changed['terms'].update(version='2',payment_per_unit='60.00');self.changed['assumed_payable_unit_ids']=self.patients[:1]
  self.right=b.P.compare(self.path,self.changed);self.bridge={'left_id':self.left['id'],'right_id':self.right['id'],'question':'How do revised rate and payable-unit assumptions change the fixed-work balance?'}
 def test_exact_rate_then_count_decomposition_and_unchanged_cost(self):
  r=b.compare(self.path,self.bridge);n=len(self.patients)
  self.assertEqual(b.P.C.exact(r['rate_effect_at_original_count']),n*1000)
  self.assertEqual(b.P.C.exact(r['count_effect_at_compared_rate']),(1-n)*6000)
  self.assertEqual(b.P.C.exact(r['total_hypothetical_receipt_difference']),6000-n*5000)
  for case in ('low','central','high'):
   for side in ('baseline','proposed'):
    v=r['comparisons'][case][side];self.assertEqual(b.P.C.exact(v['difference']),6000-n*5000)
  self.assertEqual(r['removed_assumed_units'],self.patients[1:]);self.assertTrue(b.inspect(self.path,r['id'])['current'])
 def test_same_count_different_membership_is_explicit_zero_count_effect(self):
  self.assertGreaterEqual(len(self.patients),2)
  a=b.P.compare(self.path,{**self.req,'assumed_payable_unit_ids':self.patients[:1]});c=b.P.compare(self.path,{**self.req,'assumed_payable_unit_ids':self.patients[1:2]})
  r=b.calculate(a,c,'Why does changed membership not change the supplied flat payment arithmetic?')
  self.assertEqual(b.P.C.exact(r['total_hypothetical_receipt_difference']),0);self.assertEqual(r['added_assumed_units'],self.patients[1:2]);self.assertEqual(r['removed_assumed_units'],self.patients[:1])
 def test_reverse_difference_negates_total_but_explains_different_order(self):
  a=b.calculate(self.left,self.right,'Forward');c=b.calculate(self.right,self.left,'Reverse')
  self.assertEqual(b.P.C.exact(a['total_hypothetical_receipt_difference']),-b.P.C.exact(c['total_hypothetical_receipt_difference']))
  self.assertNotEqual(b.P.C.exact(a['rate_effect_at_original_count']),-b.P.C.exact(c['rate_effect_at_original_count']))
 def test_incompatible_period_unit_currency_or_cost_scope_rejected(self):
  for field,value in [('period_start','2028-01-01'),('period_end','2030-01-01'),('unit_definition','Per month'),('unit_kind','selected_task'),('currency','EUR')]:
   right=copy.deepcopy(self.right);right['request']['terms'][field]=value
   with self.assertRaisesRegex(ValueError,'Compatible'):b.calculate(self.left,right,'Compare')
  for field,value in [('cost_basis','occupied_time'),('cost_window','horizon')]:
   right=copy.deepcopy(self.right);right['request'][field]=value
   with self.assertRaisesRegex(ValueError,'Same cost'):b.calculate(self.left,right,'Compare')
  right={**self.right,'cost_id':'another'}
  with self.assertRaisesRegex(ValueError,'same exact cost'):b.calculate(self.left,right,'Compare')
  with self.assertRaisesRegex(ValueError,'distinct'):b.calculate(self.left,self.left,'Compare')
 def test_unavailable_batch_does_not_manufacture_balance(self):
  a=copy.deepcopy(self.left);c=copy.deepcopy(self.right)
  for r in (a,c):r['comparisons']['high']['proposed']={'status':'unavailable_blocked_batch'}
  out=b.calculate(a,c,'Compare');self.assertEqual(out['comparisons']['high']['proposed'],{'status':'unavailable_blocked_batch'})
 def test_cost_or_balance_inconsistency_rejected(self):
  for field in ('modeled_cost','receipts_minus_modeled_cost'):
   right=copy.deepcopy(self.right);right['comparisons']['central']['proposed'][field]=b.P.C.money(999)
   with self.assertRaises(ValueError):b.calculate(self.left,right,'Compare')
 def test_replay_immutable_history_and_preserved_parents(self):
  with ThreadPoolExecutor(max_workers=2) as ex:rows=list(ex.map(lambda _:b.compare(self.path,self.bridge),range(2)))
  self.assertEqual(rows[0],rows[1]);self.assertEqual(len(b.history(self.path,self.cost['id'])['comparisons']),1)
  self.assertEqual(b.parent(self.path,self.left['id']),self.left);self.assertEqual(b.parent(self.path,self.right['id']),self.right)
  db=b.database(self.path)
  for sql in ('DELETE FROM comparisons','UPDATE comparisons SET cost=cost'):
   with self.assertRaises(sqlite3.IntegrityError):db.execute(sql)
  db.close()
 def test_stale_parent_or_implementation_preserves_historical_difference(self):
  r=b.compare(self.path,self.bridge)
  with patch.object(b,'identity',return_value='changed'):self.assertFalse(b.inspect(self.path,r['id'])['current'])
  self.event(self.first,action='hold');old=b.inspect(self.path,r['id']);self.assertFalse(old['current']);self.assertEqual(old['comparisons'],r['comparisons'])
  with self.assertRaises(ValueError):b.compare(self.path,self.bridge)
 def test_parent_race_and_tampering_rejected(self):
  original=b.parent;calls=[]
  def race(*a):
   row=original(*a);calls.append(1)
   if len(calls)>2:row['question']='changed'
   return row
  with patch.object(b,'parent',side_effect=race):
   with self.assertRaisesRegex(ValueError,'during comparison'):b.compare(self.path,self.bridge)
  self.assertEqual(b.history(self.path,self.cost['id'])['comparisons'],[])
  r=b.compare(self.path,self.bridge);db=b.database(self.path);db.execute('DROP TRIGGER comparisons_UPDATE');db.execute("UPDATE comparisons SET payload=replace(payload,'unresolved','verified')");db.commit();db.close()
  with self.assertRaisesRegex(ValueError,'integrity'):b.inspect(self.path,r['id'])

if __name__=='__main__':unittest.main()
