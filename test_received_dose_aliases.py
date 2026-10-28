"""Received e-prescription aliases across medications; no clinical answer keys."""
import json,unittest
from clinical_core.agere.clinical import dose as D

class Aliases(unittest.TestCase):
 def test_sig_unit_count_and_product_name_preserve_arithmetic_across_classes(self):
  for name,sig,frequency,expected in [('hydrochlorothiazide 12.5 MG Oral Tablet','Take 2 tablets by mouth twice daily','BID',50),('sertraline 50 MG Oral Tablet','Take half tablet by mouth daily','daily',25),('spironolactone 25 MG Oral Tablet','Take 1 tablet by mouth daily','daily',25)]:
   p=json.dumps({'medication_name':name,'sig':sig,'frequency':frequency,'order_status':'active'})
   evidence=json.loads(D.statement_evidence(p,'medication_orders'))
   self.assertEqual(evidence['per_admin_mg']*D.statement_per_day(p,'medication_orders'),expected)
   self.assertEqual(evidence['received']['sig'],sig);self.assertEqual(D.statement_payload_status(p,'medication_orders'),'active_documentation')
 def test_strength_alone_never_becomes_an_administration_dose(self):
  p=json.dumps({'medication_name':'sertraline 50 mg tablet','order_status':'active'})
  self.assertIsNone(D.statement_mg(p,'medication_orders'))
 def test_conditional_or_conflicting_instruction_retains_unknown(self):
  for sig in ('Take 1 to 2 tablets daily','Take 1 tablet daily as needed'):
   p=json.dumps({'medication_name':'sertraline 50 mg tablet','sig':sig,'frequency':'daily'})
   self.assertIsNone(D.statement_mg(p,'medication_orders'))
  p=json.dumps({'medication_name':'sertraline 50 mg tablet','sig':'Take 1 tablet twice daily','frequency':'daily'})
  self.assertIsNone(D.statement_per_day(p,'medication_orders'))
 def test_status_alias_agreement_conflict_and_unknown(self):
  for p,state in [({'status':'taking','order_status':'active'},'active_documentation'),({'status':'active','order_status':'stopped'},'conflicting'),({'order_status':'unrecognized'},'unknown_status'),({},'unknown_status'),({'order_status':'stopped'},'not_active')]:
   self.assertEqual(D.statement_payload_status(json.dumps(p),'medication_orders'),state)
   self.assertEqual(D.statement_status(state),state)
 def test_label_is_still_product_evidence(self):
  self.assertEqual(D.statement_payload_status(json.dumps({'order_status':'active'}),'medication_label_evidence'),'label')

if __name__=='__main__':unittest.main()
