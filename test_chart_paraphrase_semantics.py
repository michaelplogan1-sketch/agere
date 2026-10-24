import unittest
from clinical_core.agere.clinical import literal_check as L, chart_intents as C, chart_reader_op as O, analysis_graph as G

class Meaning(unittest.TestCase):
 def test_spelled_quantities_are_checked_symmetrically_without_question_echo(self):
  evidence=L.executable_text({'window':{'start':'2030-01-01','end':'2030-05-27','phrase':'last twelve months'}})
  self.assertNotIn('number(s) 12 from the question are not in the plan',L.missing('claims during the last twelve months',evidence))
  echoed=L.executable_text({'question':'last twelve months','window':{'start':'2030-01-01','end':'2030-05-27'}})
  self.assertIn('number(s) 12 from the question are not in the plan',L.missing('claims during the last twelve months',echoed))
  self.assertIn('number(s) 18 from the question are not in the plan',L.missing('last eighteen months',evidence))
 def test_evidential_by_is_not_a_grouping_but_grouping_still_is(self):
  self.assertIsNone(G.requested_shape('Is control improving, going by the last two results?'))
  self.assertEqual(G.requested_shape('Compare latest results by practice'),'table')
  self.assertEqual(G.requested_shape('Going by the labs, compare control by payer'),'table')
 def test_plural_lab_names_keep_the_same_trend_measure(self):
  for term in ('A1cs','HbA1cs','hemoglobin A1cs'):
   a=C.parse('Looking at the latest two '+term+', has glycemic control improved or deteriorated?','2030-05-27')
   self.assertEqual((a['domain'],a['concept']['value'],a['intent']),('lab','hba1c','trend'))
 def test_chart_label_is_a_populated_result_and_unknown_remains_empty(self):
  for value in ('PRACTICE-A','commercial',0):
   a=O.answer({'answer_contract':{'status':'answered','answer_type':'label','value':value,'headline':str(value),'table':[]},'patient_ids':['P-TEST']},{'coverage':'supported_observation'},'Which practice?')
   self.assertEqual(a['table'],[{'patient_id':'P-TEST','value':value}])
  a=O.answer({'answer_contract':{'status':'not_available','answer_type':'label','value':None,'table':[]},'patient_ids':['P-TEST']},{'coverage':'supported_observation'},'Which practice?')
  self.assertEqual(a['table'],[])

 def test_claim_payments_remain_money_while_explicit_lists_remain_lists(self):
  for q in ("What did the plan pay out on claims during the last twelve months?", "Over the past year, what was the plan's total claims payment?"):
   self.assertEqual(C.parse(q,'2030-05-27')['intent'],'sum')
  self.assertEqual(C.parse('List each paid claim in 2029','2030-05-27')['intent'],'list')
  self.assertEqual(C.parse('How many claims were paid in 2029','2030-05-27')['intent'],'count_in_window')
 def test_fronted_yes_no_does_not_become_a_latest_numeric_value(self):
  self.assertEqual(C.parse('In the last twelve months, has a UACR been done?','2030-05-27')['intent'],'status')
