import unittest
from unittest.mock import patch
from clinical_core.contracts import digest
from clinical_core.agere.clinical import medication_work_view as V

class CoverageView(unittest.TestCase):
 def test_literal_origin_has_no_fabricated_assessment_or_model_answer(self):
  task={'id':'t','revision':2,'plan_id':'p','patient_id':'patient'}
  review={'id':'r','medication_records':2,'nested_medication_items':1,'narrative_records':3,'received_records_outside_review':9}
  plan={'patient_id':'patient','id':'p','origin':{'kind':V.M.KIND,'id':'r','patient_id':'patient','hash':digest(review)}}
  with patch.object(V.R,'task',return_value=task),patch.object(V.C,'inspect_plan',return_value=plan),patch.object(V.M,'inspect',return_value=review):
   r=V.prepare('unused','t');self.assertEqual(r['answers'],[]);self.assertEqual(r['source_inventory']['medication_records'],2);self.assertFalse(r['clinical_meaning_validated'])
 def test_old_origins_delegate_without_reformatting(self):
  with patch.object(V.R,'task',return_value={'plan_id':'p'}),patch.object(V.C,'inspect_plan',return_value={'origin':{'kind':'assessment'}}),patch.object(V.old,'prepare',return_value={'legacy':'same'}) as f:
   self.assertEqual(V.prepare('path','task'),{'legacy':'same'});f.assert_called_once_with('path','task')
 def test_cross_patient_work_rejected(self):
  with patch.object(V.R,'task',return_value={'plan_id':'p','patient_id':'a'}),patch.object(V.C,'inspect_plan',return_value={'patient_id':'b','origin':{'kind':V.M.KIND}}):
   with self.assertRaises(ValueError):V.prepare('path','task')

if __name__=='__main__':unittest.main()
