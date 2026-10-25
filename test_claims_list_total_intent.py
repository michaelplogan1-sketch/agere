import unittest
import test_chart_reader as charts
from clinical_core.agere.clinical import chart_intents as C
class ClaimsIntent(unittest.TestCase):
 def test_show_claims_preserves_records_and_totals_are_explicit(self):
  charts.Base.setUpClass();fixture=charts.Base();self.addCleanup(charts.Base.tearDownClass)
  for q in ('Show the paid claims for {P}','Show all paid claims for {P}','List paid claims for {P}'):
   a=fixture.ask(q);self.assertEqual(a['answer_type'],'table',q)
   self.assertEqual(C.parse(q,'2030-05-27')['intent'],'list')
  for q in ('Show the total paid claims for {P}','Show how much the plan paid for {P} in claims',"What did the plan pay out on {P}'s claims?"):
   self.assertEqual(C.parse(q,'2030-05-27')['intent'],'sum',q)
   self.assertEqual(fixture.ask(q)['answer_type'],'value',q)
