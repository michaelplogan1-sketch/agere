import unittest
from clinical_core.agere.clinical import appointment_uncertainty as U

def fixture():
 rows=[{'patient_id':str(i),'component':str(i//2),'partition':'development_validation','recorded_label':i%2} for i in range(20)]
 pred=[{'patient_id':r['patient_id'],'recorded_label':r['recorded_label'],'models':{n:.5 for n in U.Q.Q.PLAN['candidates']}} for r in rows];return rows,pred
class Uncertainty(unittest.TestCase):
 def test_identical_baseline_exact_zero(self):
  r,p=fixture();q=U.calculate(r,p,.5);self.assertEqual(q['protected_components'],10);self.assertTrue(all(v['lower']==v['upper']==v['difference']==0 for v in q['comparisons'].values()))
 def test_perfect_prediction_negative_difference(self):
  r,p=fixture()
  for x in p:x['models']={n:x['recorded_label'] for n in x['models']}
  q=U.calculate(r,p,.5);self.assertTrue(all(v['lower']==v['upper']==v['difference']==-.25 for v in q['comparisons'].values()))
 def test_stable_under_row_order_and_preserves_pairs(self):
  r,p=fixture();self.assertEqual(U.calculate(r,p,.5),U.calculate(list(reversed(r)),list(reversed(p)),.5))
 def test_no_other_partitions(self):
  r,p=fixture();r[0]['partition']='train'
  with self.assertRaises(ValueError):U.calculate(r,p,.5)
 def test_exact_join_and_label_binding(self):
  r,p=fixture()
  with self.assertRaises(ValueError):U.calculate(r,p[:-1],.5)
  p[0]['recorded_label']=1
  with self.assertRaises(ValueError):U.calculate(r,p,.5)
 def test_nonfinite_or_invalid_probabilities(self):
  for value in (float('nan'),1.5,True):
   r,p=fixture();p[0]['models']['logistic']=value
   with self.assertRaises(ValueError):U.calculate(r,p,.5)
 def test_single_component_is_not_independent_sample(self):
  r,p=fixture()
  for v in r:v['component']='one'
  with self.assertRaises(ValueError):U.calculate(r,p,.5)
if __name__=='__main__':unittest.main()
