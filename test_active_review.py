import unittest
from clinical_core.agere.clinical.active_review import entropy,js_divergence,select,source_group
from test_context_features import case

def row(pid,text='Heart failure today.',concept='heart failure'):
    x=case(text,'Heart failure');x['concept']=concept
    p={'yes':.8,'no':.2};q={'yes':.2,'no':.8}
    axes={a:{'label':'yes','probabilities':p} for a in ('assertion','subject','time','relation')};alt={a:{'label':'no','probabilities':q} for a in axes}
    return {'synthetic':True,'patient_id':pid,'input':x,'source':{'context':text,'record_id':pid},'annotation':{'axes':axes,'alternate_axes':alt,'decision_authority':False,'review_routing':{'priority':'model_disagreement'}}}

class ActiveReviewTests(unittest.TestCase):
 def test_js_limits_and_symmetry(self):
  p={'a':1.,'b':0.};q={'a':0.,'b':1.}
  self.assertEqual(js_divergence(p,p),0);self.assertEqual(js_divergence(p,q),1);self.assertEqual(js_divergence(p,q),js_divergence(q,p))
 def test_invalid_probabilities_rejected(self):
  for p in ([float('nan'),1.],[-1.,2.],[.2,.2]):
   with self.assertRaises(ValueError):entropy(p)
 def test_copies_do_not_dominate(self):
  r=select([row(str(i)) for i in range(100)],size=10)
  self.assertEqual(r['unique_eligible_groups'],1);self.assertEqual(r['selected'],1)
 def test_exclusion_and_reproducibility(self):
  rows=[row(str(i),'Heart failure '+w+'.') for i,w in enumerate(['today','yesterday','improving','stable','possible','copied'])]
  excluded={source_group(rows[0])};a=select(rows,size=4,exclude_groups=excluded);b=select(list(reversed(rows)),size=4,exclude_groups=excluded)
  self.assertEqual(a['rows'],b['rows']);self.assertNotIn(rows[0]['patient_id'],[x['patient_id'] for x in a['rows']])
  self.assertEqual(a['selection_counts']['score_independent_audit'],1)
  self.assertTrue(all(x['training_label'] is None for x in a['rows']))
 def test_patient_cap(self):
  rows=[row('same','Heart failure '+w+'.') for w in ['today','yesterday','improving','stable','possible','copied']]
  self.assertEqual(select(rows,size=6)['selected'],2)
 def test_no_automatic_authority(self):
  r=row('p');r['annotation']['decision_authority']=True
  with self.assertRaises(ValueError):select([r])

if __name__=='__main__':unittest.main()
