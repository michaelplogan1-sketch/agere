import unittest
from clinical_core.agere.clinical import program_documents as D,program_document_change as X
from clinical_core.agere.clinical.economical_planner import local_plan
from test_program_documents import package
import test_protocol_requirements as fixtures

class QuotedVersionImpact(unittest.TestCase):
 def setUp(self):
  self.f=fixtures.RequirementBindings();self.f.setUp();self.addCleanup(self.f.doCleanups);self.root=self.f.root
  self.p={'before':D.install(self.root,package())['id'],'after':D.install(self.root,package('2',extra=True))['id']}

 def test_complete_same_patient_comparison_explains_missing_added_input(self):
  out=X.execute(self.root,['P-1','P-2'],self.p);self.assertEqual(out['patients_evaluated'],2)
  self.assertEqual(out['change_counts'],{'status_changed':1,'entered_supported':0,'left_supported':1,'became_unresolved':1})
  row=X.inspect(self.root,out['id'],'P-1');self.assertEqual(row['patient_change']['changed_inputs'],['attribution'])
  self.assertIsNone(row['program_receipts']['after']['inputs']['attribution']['value']);self.assertEqual(row['program_receipts']['before']['status'],'supported')
  self.assertTrue(X.execute(self.root,['P-1','P-2'],self.p)['comparison_cache_hit'])
  with self.assertRaises(ValueError):X.inspect(self.root,out['id'],'unselected')
  self.assertEqual(X.execute(self.root,[],self.p)['patients_evaluated'],0)

 def test_stale_source_or_semantic_review_rejects_old_impact(self):
  out=X.execute(self.root,['P-1','P-2'],self.p);self.f.save([self.f.screen(difficulty_paying_medical_bills=False)])
  with self.assertRaises(ValueError):X.inspect(self.root,out['id'],'P-1')
  self.assertEqual(X.inspect(self.root,out['id'],'P-2')['patient_change']['after']['status'],'unresolved')
  D.review(self.root,{'package':self.p['after'],'expected_revision':0,'actor':'ISOLATED TEST FIXTURE','status':'changes_requested','note':'Fixture only'})
  with self.assertRaises(ValueError):X.inspect(self.root,out['id'],'P-2')

 def test_rejects_different_programs_or_unmapped_questions(self):
  p=package('another');p['spec']['id']='other_program';key=D.install(self.root,p)['id']
  with self.assertRaises(ValueError):X.prepare(self.root,{'before':self.p['before'],'after':key})
  q=X.question(**self.p);plan,route=local_plan(q,'2029-02-15');self.assertEqual(route,'exact_imported_program_comparison');self.assertEqual(plan['nodes'][-1]['parameters'],self.p)
  self.assertIsNone(X.local_question(q+' Only adults.'))
  with self.assertRaises(ValueError):X.validate({'before':key,'after':key})

if __name__=='__main__':unittest.main()
