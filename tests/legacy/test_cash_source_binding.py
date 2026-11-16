"""Adjudication amounts cannot become received cash through output labels."""
import json,unittest
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere.clinical import financial_accounts as F,backstop_agent as B,system_one_gate as G

QUESTION='Show net payer-paid claim dollars and payer cash transactions separately by payment year.'
SCHEMA={'payer_adjudication':['paid_cents','billed_cents','paid_date','patient_id'],'payer_claim':['paid_cents','patient_id','service_from'],'claim':['net_payer_cash_cents','patient_id','service_from'],'record':['patient_id','stream','payload','event_date','available_date'] ,'current_record':['patient_id','stream','payload','event_date','available_date']}
def call(sql,view='agere_result_1',reads=None,columns=None,row=None,dependencies=None):
 columns=columns or ['dollars'];row=row or {'dollars':100}
 return {'name':'query_sql','input':{'sql':sql},'result':{'result_view':view,'columns':columns,'rows':[row],'truncated':False,'read_views':dependencies or [],'source_contracts':{'relations':[{'name':t,'columns':SCHEMA[t]}for t in reads or []],'definitions':[]}}}
def answer(view='agere_result_1'):return {'status':'answered','answer_type':'table','table_from_tool':view,'headline':'Received financial observations.'}
class CashBinding(unittest.TestCase):
 def check(self,calls,question=QUESTION):return F.cash_source_issues(question,answer(calls[-1]['result']['result_view']),calls)
 def test_paid_date_and_cash_label_do_not_change_adjudication_value_role(self):
  q="SELECT 'payment_year_payer_cash' AS lifecycle,SUM(paid_cents)/100.0 AS dollars FROM payer_adjudication GROUP BY substr(paid_date,1,4)"
  c=call(q,reads=['payer_adjudication'])
  self.assertTrue(self.check([c]))
  with patch.object(G,'check',side_effect=AssertionError('Wrong source is rejected without paid review')):
   r=B.review_final(SimpleNamespace(),'Show payer cash transactions in dollars',answer(),[c],SimpleNamespace(read={}),ledger=None,as_of='2030-05-27')
  self.assertEqual(r['status'],'repair');self.assertEqual(r['calls'],0);self.assertIn('Adjudication',r['feedback'])
 def test_native_alias_wrappers_retain_wrong_original_value_role(self):
  a=call('SELECT SUM(paid_cents) AS cents FROM payer_adjudication',reads=['payer_adjudication'],columns=['cents'],row={'cents':100})
  b=call('SELECT cents/100.0 AS dollars FROM agere_result_1','agere_result_2',dependencies=['agere_result_1'])
  self.assertTrue(self.check([a,b]))
 def test_unused_cash_query_or_cash_only_join_does_not_supply_the_amount(self):
  cash="SELECT SUM(json_extract(payload,'$.allocated_cents'))/100.0 AS dollars FROM record WHERE stream='payer_cash_allocations'"
  a=call(cash,reads=['record']);b=call('SELECT SUM(paid_cents)/100.0 AS dollars FROM payer_adjudication','agere_result_2',reads=['payer_adjudication'])
  self.assertTrue(self.check([a,b]))
  joined="SELECT SUM(pa.paid_cents)/100.0 AS dollars FROM payer_adjudication pa CROSS JOIN (SELECT json_extract(payload,'$.allocated_cents') AS amount FROM record WHERE stream='payer_cash_allocations') cash"
  self.assertTrue(self.check([call(joined,reads=['payer_adjudication','record'])]))
 def test_registered_received_cash_amount_through_ctes_and_native_views_passes(self):
  cash="WITH raw AS(SELECT json_extract(r.payload,'$.allocated_cents') AS cents FROM record r WHERE r.stream='payer_cash_allocations') SELECT SUM(cents)/100.0 AS dollars FROM raw"
  self.assertEqual(self.check([call(cash,reads=['record'])]),[])
  a=call(cash,reads=['record']);b=call('SELECT dollars FROM agere_result_1','agere_result_2',dependencies=['agere_result_1'])
  self.assertEqual(self.check([a,b]),[])
 def test_expanded_executed_query_supplies_actual_macro_source(self):
  c=call("SELECT SUM(json_extract(payload,'$.allocated_cents'))/100.0 AS dollars FROM {{cash_records}}",reads=['record'])
  c['result']['executed_query']="SELECT SUM(json_extract(payload,'$.allocated_cents'))/100.0 AS dollars FROM record WHERE stream='payer_cash_allocations'"
  self.assertEqual(self.check([c]),[])
 def test_native_json_each_receipt_without_schema_columns_keeps_real_ancestry(self):
  a=call("SELECT SUM(json_extract(payload,'$.allocated_cents'))/100.0 AS dollars FROM record WHERE stream='payer_cash_allocations'",reads=['record'])
  b=call('SELECT dollars FROM agere_result_1','agere_result_2',dependencies=['agere_result_1'])
  b['result']['source_contracts']['relations'].append({'name':'json_each','columns':[]})
  self.assertEqual(self.check([a,b]),[])
 def test_named_cash_and_adjudication_columns_can_coexist(self):
  a=call('SELECT SUM(paid_cents)/100.0 AS net_payer_paid_dollars FROM payer_claim',reads=['payer_claim'],columns=['net_payer_paid_dollars'],row={'net_payer_paid_dollars':200})
  b=call("SELECT SUM(json_extract(payload,'$.allocated_cents'))/100.0 AS net_payer_cash_dollars FROM record WHERE stream='payer_cash_allocations'",'agere_result_2',reads=['record'],columns=['net_payer_cash_dollars'],row={'net_payer_cash_dollars':100})
  c=call('SELECT p.net_payer_paid_dollars,c.net_payer_cash_dollars FROM agere_result_1 p CROSS JOIN agere_result_2 c','agere_result_3',dependencies=['agere_result_1','agere_result_2'],columns=['net_payer_paid_dollars','net_payer_cash_dollars'],row={'net_payer_paid_dollars':200,'net_payer_cash_dollars':100})
  self.assertEqual(self.check([a,b,c]),[])
 def test_textual_cash_currency_status_is_not_a_claimed_monetary_value(self):
  sql="SELECT SUM(json_extract(payload,'$.allocated_cents'))/100.0 AS dollars,'payload currency not recorded; USD not established' AS cash_currency_status FROM record WHERE stream='payer_cash_allocations'"
  c=call(sql,reads=['record'],columns=['dollars','cash_currency_status'],row={'dollars':100,'cash_currency_status':'payload currency not recorded; USD not established'})
  self.assertEqual(self.check([c]),[])
  self.assertTrue(self.check([call('SELECT 100 AS payer_cash',columns=['payer_cash'],row={'payer_cash':100})]))
 def test_literals_wrong_stream_paths_and_unrestricted_or_cannot_assert_cash(self):
  for sql,reads in [("SELECT 1 AS dollars,'payer_cash_allocations' AS stream,'$.allocated_cents' AS path",[]),
   ("SELECT SUM(json_extract(payload,'$.paid_cents'))/100.0 AS dollars FROM record WHERE stream='payer_cash_allocations'",['record']),
   ("SELECT SUM(json_extract(payload,'$.allocated_cents'))/100.0 AS dollars FROM record WHERE stream='patient_payments'",['record']),
   ("SELECT SUM(json_extract(payload,'$.allocated_cents'))/100.0 AS dollars FROM record WHERE stream='payer_cash_allocations' OR 1=1",['record'])]:
   with self.subTest(sql=sql):self.assertTrue(self.check([call(sql,reads=reads)]))
 def test_dated_transactions_cannot_use_a_claim_cash_rollup(self):
  c=call('SELECT SUM(net_payer_cash_cents)/100.0 AS dollars FROM claim',reads=['claim'])
  self.assertTrue(self.check([c]));self.assertEqual(self.check([c],'Show total payer cash received in dollars.'),[])
 def test_both_lifecycles_can_remain_in_a_native_union(self):
  sql="SELECT 'adjudicated_paid' AS lifecycle,SUM(paid_cents)/100.0 AS dollars FROM payer_claim UNION ALL SELECT 'payer_cash' AS lifecycle,SUM(json_extract(payload,'$.allocated_cents'))/100.0 AS dollars FROM record WHERE stream='payer_cash_allocations'"
  self.assertEqual(self.check([call(sql,reads=['payer_claim','record'],columns=['lifecycle','dollars'],row={'lifecycle':'payer_cash','dollars':100})]),[])
 def test_other_cash_column_or_swapped_union_label_cannot_launder_paid_amount(self):
  sql="SELECT SUM(pa.paid_cents)/100.0 AS payer_cash_dollars,SUM(json_extract(r.payload,'$.allocated_cents'))/100.0 AS other_dollars FROM payer_adjudication pa JOIN record r USING(patient_id) WHERE r.stream='payer_cash_allocations'"
  self.assertTrue(self.check([call(sql,reads=['payer_adjudication','record'],columns=['payer_cash_dollars','other_dollars'],row={'payer_cash_dollars':100,'other_dollars':100})]))
  sql="SELECT 'payer_cash' AS lifecycle,SUM(paid_cents)/100.0 AS dollars FROM payer_claim UNION ALL SELECT 'adjudicated_paid' AS lifecycle,SUM(json_extract(payload,'$.allocated_cents'))/100.0 AS dollars FROM record WHERE stream='payer_cash_allocations'"
  self.assertTrue(self.check([call(sql,reads=['payer_claim','record'],columns=['lifecycle','dollars'],row={'lifecycle':'payer_cash','dollars':100})]))
 def test_same_scalar_cannot_mix_cash_with_adjudicated_dollars(self):
  sql="SELECT SUM(pa.paid_cents)/100.0+SUM(json_extract(r.payload,'$.allocated_cents'))/100.0 AS dollars FROM payer_adjudication pa JOIN record r USING(patient_id) WHERE r.stream='payer_cash_allocations'"
  self.assertTrue(self.check([call(sql,reads=['payer_adjudication','record'])]))
 def test_unknown_cash_is_preserved_beside_known_other_lifecycle(self):
  c=call("SELECT 'payer_cash' AS lifecycle,NULL AS dollars UNION ALL SELECT 'adjudicated_paid',100",columns=['lifecycle','dollars']);c['result']['rows']=[{'lifecycle':'payer_cash','dollars':None},{'lifecycle':'adjudicated_paid','dollars':100}]
  self.assertEqual(self.check([c]),[])
 def test_unknown_amounts_are_not_invented_or_forced_to_zero(self):
  c=call('SELECT NULL AS dollars',row={'dollars':None});self.assertEqual(self.check([c]),[])
 def test_other_cash_roles_are_not_silently_redefined_as_payer_cash(self):
  c=call('SELECT 100 AS dollars')
  self.assertEqual(self.check([c],'Show patient cash receipts in dollars.'),[])
  self.assertEqual(self.check([c],'Show cash payments in dollars.'),[])
  self.assertTrue(self.check([c],'Show cash received from payers in dollars.'))
  self.assertTrue(self.check([c],'Show cash collected from payers in dollars.'))
 def test_alias_and_empty_results_cannot_launder_wrong_source(self):
  for alias in ['payer_cash','total','dollars']:
   c=call('SELECT SUM(paid_cents)/100.0 AS '+alias+' FROM payer_adjudication',reads=['payer_adjudication'],columns=[alias],row={alias:100})
   self.assertTrue(self.check([c]));c['result']['rows']=[{alias:None}];self.assertTrue(self.check([c]));c['result']['rows']=[];self.assertTrue(self.check([c]))
 def test_calendar_grain_cannot_use_undated_claim_cash_rollup(self):
  c=call('SELECT SUM(net_payer_cash_cents)/100.0 AS dollars FROM claim GROUP BY substr(service_from,1,4)',reads=['claim'])
  for q in ['Show payer cash by year.','Show monthly payer cash.','Show payer cash in 2024.']:self.assertTrue(self.check([c],q))
 def test_executed_payer_cash_role_does_not_depend_on_question_word_order(self):
  c=call('SELECT SUM(net_payer_cash_cents)/100.0 AS received_cash_dollars FROM claim',reads=['claim'],columns=['received_cash_dollars'],row={'received_cash_dollars':100})
  for q in ['Compare allowed, adjudicated paid and separately received cash by payer for 2029 services.',
            'For 2029 services, compare financial lifecycle amounts.']:
   self.assertTrue(self.check([c],q))
  self.assertEqual(self.check([c],'Compare overall financial lifecycle amounts.'),[])
 def test_unused_payer_rollup_does_not_redefine_a_separate_cash_role(self):
  a=call('SELECT SUM(net_payer_cash_cents) AS payer_cash_cents FROM claim',reads=['claim'],columns=['payer_cash_cents'],row={'payer_cash_cents':100})
  b=call('SELECT 50 AS patient_cash_dollars','agere_result_2',columns=['patient_cash_dollars'],row={'patient_cash_dollars':50})
  self.assertEqual(self.check([a,b],'Show patient cash for 2029.'),[])
 def test_cash_transaction_count_is_not_a_monetary_value(self):
  a=call("SELECT COUNT(*) AS cash_transactions FROM record WHERE stream='payer_cash_allocations'",reads=['record'],columns=['cash_transactions'],row={'cash_transactions':2})
  self.assertEqual(self.check([a]),[])
  b=call('SELECT cash_transactions FROM agere_result_1','agere_result_2',dependencies=['agere_result_1'],columns=['cash_transactions'],row={'cash_transactions':2})
  self.assertEqual(self.check([a,b]),[])
 def test_cash_cannot_mix_with_unregistered_monetary_fields(self):
  sql="SELECT SUM(json_extract(r.payload,'$.allocated_cents'))-SUM(pa.billed_cents) AS total FROM record r JOIN payer_adjudication pa USING(patient_id) WHERE r.stream='payer_cash_allocations'"
  self.assertTrue(self.check([call(sql,reads=['record','payer_adjudication'],columns=['total'],row={'total':100})]))
  sql="SELECT SUM(json_extract(payload,'$.allocated_cents'))-SUM(json_extract(payload,'$.billed_cents')) AS dollars FROM record WHERE stream='payer_cash_allocations'"
  self.assertTrue(self.check([call(sql,reads=['record'])]))
 def test_defining_cte_and_conditional_stream_predicates_bind_cash(self):
  queries=["WITH c AS(SELECT r.payload FROM record r WHERE r.stream='payer_cash_allocations')SELECT SUM(json_extract(c.payload,'$.allocated_cents')) AS dollars FROM c",
   "SELECT SUM(CASE WHEN stream='payer_cash_allocations' THEN json_extract(payload,'$.allocated_cents') END) AS dollars FROM record"]
  for sql in queries:self.assertEqual(self.check([call(sql,reads=['record'])]),[],sql)
  for predicate in ["stream<>'payer_cash_allocations'","stream IN('payer_cash_allocations','patient_payments')"]:
   self.assertTrue(self.check([call("SELECT SUM(json_extract(payload,'$.allocated_cents')) AS dollars FROM record WHERE "+predicate,reads=['record'])]))

if __name__=='__main__':unittest.main()
