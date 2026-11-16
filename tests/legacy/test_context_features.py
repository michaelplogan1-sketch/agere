import unittest
from clinical_core.agere.clinical.context_features import context_view

def case(text,target):
 a=text.index(target)
 return dict(text=text,concept=target,target_quote=target,target_start=a,target_end=a+len(target),prefix_omitted=False,suffix_omitted=False)

class ContextFeatureTests(unittest.TestCase):
 def test_coordinated_negation(self):
  v=context_view(case('No chest pain or dyspnea reported.','dyspnea'))
  self.assertEqual(v['flags']['negation:coordinated'],1)
  self.assertEqual(v['flags']['negation:left'],1)
 def test_cross_clause_negation(self):
  v=context_view(case('No current medicines; outside records are incomplete.','outside records'))
  self.assertEqual(v['flags']['negation:local'],0)
  self.assertEqual(v['flags']['negation:unrelated_outside'],1)
 def test_contrast_boundary(self):
  v=context_view(case('No fever but dyspnea persists.','dyspnea'))
  self.assertEqual(v['flags']['negation:local'],0)
  self.assertEqual(v['trace']['focus_text'],'dyspnea persists')
 def test_copy_context(self):
  v=context_view(case('Prior note (2024-05-01):\nProblems:\nHeart failure.','Heart failure'))
  self.assertEqual(v['flags']['historical:copy_heading'],1)
 def test_current_heading_ends_copy(self):
  v=context_view(case('Prior note (2024-05-01):\nCough.\nCurrent assessment:\nHeart failure.','Heart failure'))
  self.assertEqual(v['flags']['historical:copy_heading'],0)
  self.assertEqual(v['flags']['current:active_heading'],1)
 def test_span_integrity(self):
  x=case('Prior note:\nNo fever but dyspnea possible today.','dyspnea');v=context_view(x)
  for span in v['trace']['cue_spans']:self.assertEqual(x['text'][span['start']:span['end']],span['quote'])
 def test_uncertainty_not_absence(self):
  v=context_view(case('Listed regimen: metoprolol; current use not verified.','metoprolol'))
  self.assertEqual(v['flags']['uncertainty:right'],1)
 def test_problem_list_not_metadata(self):
  v=context_view(case('Problem list: hypertension, chronic kidney disease.','chronic kidney disease'))
  self.assertEqual(v['flags']['list:problem'],1)
  self.assertEqual(v['flags']['metadata:bare_heading'],0)
 def test_pseudo_negation_separate_feature(self):
  v=context_view(case('No change in dyspnea.','dyspnea'))
  self.assertEqual(v['flags']['negation:pseudo'],1)
 def test_invalid_target(self):
  x=case('Heart failure.','Heart failure');x['target_start']=1
  with self.assertRaises(ValueError):context_view(x)

if __name__=='__main__':unittest.main()
