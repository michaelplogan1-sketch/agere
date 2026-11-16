import copy
import unittest
from unittest.mock import patch
import test_program_work as fixture
from clinical_core.agere.clinical import program_work as work, synthesis_work as bridge

class SynthesisWork(unittest.TestCase):
 def setUp(self):
  fixture.ProgramWork.setUp(self)
  p=patch.object(bridge.evidence_needs,'prepare',return_value={'id':'d'*64,'summary':{'unbound_questions':1}})
  p.start();self.addCleanup(p.stop)
 tearDown=fixture.ProgramWork.tearDown
 def answer(self):
  return {'id':'a'*64,'patient_id':'p21','context_id':'b'*64,'question':'What is documented?',
   'findings':[{'id':'f1'}],'review_steps':[{'id':'c'*64,'status':'proposed','execution_authorized':False,'question':'Review meaning'}]}
 def test_attached_answer_does_not_promote_noncandidate(self):
  with patch.object(bridge.case_synthesis,'inspect',return_value=self.answer()):
   contexts=bridge.contexts(self.root,['a'*64],['p21'])
   p=work.prepare(self.root,'hf_review',['p21'],synthesis_contexts=contexts)
   self.assertEqual(p['counts']['candidates'],0);self.assertEqual(p['synthesis_assessments'],1)
   db=work.database(self.root)
   try:
    proposal=work.load(db,'work_proposals',p['proposal_id']);a=work.load(db,'work_assessments',proposal['assessment_ids'][0])
    self.assertEqual(a['synthesis_context'],contexts['p21']);self.assertFalse(a['clinical_eligibility_established']);self.assertFalse(a['review_candidate'])
   finally:db.close()
   result=work.materialize(self.root,p['proposal_id'],'test fixture actor')
   self.assertEqual(work.list_items(self.root)['summary']['work_items'],0)
 def test_wrong_patient_empty_and_duplicate_answers_rejected(self):
  with patch.object(bridge.case_synthesis,'inspect',return_value=self.answer()):
   for ids,patients in [([],['p21']),(['a'*64,'a'*64],['p21']),(['a'*64],['p00'])]:
    with self.assertRaises(ValueError):bridge.contexts(self.root,ids,patients)
 def test_mutated_context_cannot_enter_assessment(self):
  with patch.object(bridge.case_synthesis,'inspect',return_value=self.answer()):
   contexts=bridge.contexts(self.root,['a'*64],['p21']);contexts['p21']['answers'][0]['review_proposals'][0]['question']='tampered'
   with self.assertRaisesRegex(ValueError,'changed'):work.prepare(self.root,'hf_review',['p21'],synthesis_contexts=contexts)
 def test_stale_synthesis_blocks_materialization(self):
  with patch.object(bridge.case_synthesis,'inspect',return_value=self.answer()):
   contexts=bridge.contexts(self.root,['a'*64],['p21']);p=work.prepare(self.root,'hf_review',['p21'],synthesis_contexts=contexts)
  with patch.object(bridge.case_synthesis,'inspect',side_effect=ValueError('Source changed')):
   with self.assertRaisesRegex(ValueError,'Source changed'):work.materialize(self.root,p['proposal_id'],'test fixture actor')

if __name__=='__main__':unittest.main()
