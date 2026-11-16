import unittest
from clinical_core.agere.clinical.context_reader import review_reasons
from test_context_features import case

def axes(**labels):
 base=dict(assertion='affirmed',subject='patient',time='current',relation='clinical_observation');base.update(labels)
 return {a:{'label':v} for a,v in base.items()}

class ReviewRoutingTests(unittest.TestCase):
 def test_denial_conflict_flag_is_not_label_correction(self):
  a=axes();r=review_reasons(case('No chest pain or dyspnea.','dyspnea'),a,a)
  self.assertIn('polarity_cue_conflict',[v['code'] for v in r]);self.assertEqual(a['assertion']['label'],'affirmed')
 def test_pseudo_negation_not_escalated_as_denial(self):
  a=axes();r=review_reasons(case('No change in dyspnea.','dyspnea'),a,a)
  self.assertNotIn('polarity_cue_conflict',[v['code'] for v in r])
 def test_cross_view_has_exact_disputed_axis(self):
  r=review_reasons(case('Heart failure.','Heart failure'),axes(),axes(time='historical'))
  self.assertEqual(next(x for x in r if x['code']=='cross_view_disagreement')['axes'],['time'])
 def test_copied_current_flag(self):
  a=axes();r=review_reasons(case('Prior note:\nHeart failure.','Heart failure'),a,a)
  self.assertIn('copied_history_current',[v['code'] for v in r])
 def test_missing_context_visible(self):
  x=case('Heart failure.','Heart failure');x['prefix_omitted']=True;a=axes()
  self.assertIn('bounded_context',[v['code'] for v in review_reasons(x,a,a)])
 def test_unknown_not_treated_as_clearance(self):
  a=axes(assertion='uncertain');r=review_reasons(case('Possible heart failure.','heart failure'),a,a)
  self.assertIn('unresolved_proposal',[v['code'] for v in r])

if __name__=='__main__':unittest.main()
