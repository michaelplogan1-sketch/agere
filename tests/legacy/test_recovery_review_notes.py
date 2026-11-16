"""Prior machine instructions have exact provenance and never settle current checks."""
import copy,unittest
from unittest.mock import patch
from clinical_core.agere.clinical import harness_improvement as H,proposers as P

class ReviewNotes(unittest.TestCase):
 def fixture(self):
  key='ANS-'+'a'*24;parent='ANS-'+'b'*24
  current={'question':'original','source':{'identity':'source'},'investigation':{'recovery_checkpoint':{'draft':{'limitations':['clinical unknown','old repair']}},'answer_reviews':[]},'planner_receipt':{'artifact_feedback_context':parent}}
  previous={'question':'original','source':{'identity':'source'},'investigation':{'answer_reviews':[{'status':'repair','feedback':'old repair'}]}}
  return key,parent,current,previous
 def test_saved_same_scope_lineage_supplies_only_exact_review_annotations(self):
  key,parent,current,previous=self.fixture();before=copy.deepcopy([current,previous])
  with patch('clinical_core.agere.deliverables.answer_context.load',return_value=previous)as load:
   notes=P.prior_recovery_review_notes('.',key,current)
  self.assertEqual(notes,[{'context_id':parent,'review_index':0,'review':previous['investigation']['answer_reviews'][0]}])
  load.assert_called_once_with('.',parent,current=False,source_review=True)
  self.assertEqual([current,previous],before)
 def test_unavailable_mismatched_or_cyclic_lineage_cannot_supply_annotations(self):
  for kind in ('source','question','unavailable','cycle','invalid_id'):
   key,parent,current,previous=self.fixture()
   if kind=='source':previous['source']={'identity':'changed'}
   elif kind=='question':previous['question']='other'
   elif kind=='cycle':current['planner_receipt']['artifact_feedback_context']=key
   elif kind=='invalid_id':current['planner_receipt']['artifact_feedback_context']='../../source'
   with self.subTest(kind=kind),patch('clinical_core.agere.deliverables.answer_context.load',return_value=previous,side_effect=ValueError('unavailable')if kind=='unavailable'else None):
    self.assertEqual(P.prior_recovery_review_notes('.',key,current),[])
 def test_exact_machine_text_only_and_no_resolved_claim_or_mutation(self):
  note={'context_id':'ANS-'+'a'*24,'review_index':0,'review':{'status':'repair','feedback':'old repair'}}
  draft={'status':'partial','limitations':['clinical unknown','old repair','Source quotes old repair.',{'text':'old repair'}], 'table':[{'quote':'old repair'}]};before=copy.deepcopy(draft)
  shown,annotations=H.separate_prior_review_notes(draft,[note])
  self.assertEqual(shown['limitations'],['clinical unknown','Source quotes old repair.',{'text':'old repair'}]);self.assertEqual(shown['table'],draft['table']);self.assertEqual(draft,before)
  self.assertFalse(annotations[0]['resolved']);self.assertEqual(annotations[0]['review_digest'],H.digest(note['review']))
  note['review']['status']='confirmed';self.assertEqual(H.separate_prior_review_notes(draft,[note]),(draft,[]))
  self.assertEqual(H.separate_prior_review_notes(draft,[]),(draft,[]))
 def test_unbound_review_annotation_arguments_are_rejected(self):
  with self.assertRaises(ValueError),H.owned_recovery([], 'q', prior_review_notes=[]):pass

if __name__=='__main__':unittest.main()
