"""Rule changes keep full populations and both immutable source-receipt lineages."""
import copy,json,sqlite3,unittest
from unittest.mock import patch
from clinical_core.agere.clinical import protocol_change as P,care_protocols as C,analysis_graph as G
from clinical_core.agere.clinical.economical_planner import local_plan
from clinical_core.agere.clinical.graph_workbench import protocol_change_examples
import test_care_protocols as fixture


class VersionImpact(unittest.TestCase):
 def setUp(self):
  self.f=fixture.ProtocolCache();self.f.setUp();self.addCleanup(self.f.tearDown);self.root=self.f.root
  self.params={'before':'affordability_action_review@1','after':'affordability_action_review@2'}

 def test_explicit_versions_only_and_same_family(self):
  for p in ({'before':'affordability_action_review','after':'affordability_action_review@2'}, {'before':'affordability_action_review@1','after':'affordability_action_review@1'}, {'before':'low_egfr_specialist_review@1','after':'affordability_action_review@2'}):
   with self.assertRaises(ValueError):P.validate(p)

 def test_all_patients_both_receipts_and_rule_changes_persist(self):
  self.f.review(self.f.a)
  out=P.execute(self.root,['p','q'],self.params);self.assertEqual(out['patients_evaluated'],2)
  self.assertEqual(out['change_counts']['exited_review'],1);self.assertEqual(out['change_counts']['entered_review'],0)
  self.assertIn('bindings',out['definition_fields_changed']);self.assertFalse(out['clinical_eligibility_established'])
  row=P.inspect(self.root,out['id'],'p');self.assertTrue(row['protocol_receipts']['before']['trigger']);self.assertIsNone(row['protocol_receipts']['after']['trigger'])
  self.assertTrue(P.execute(self.root,['p','q'],self.params)['comparison_cache_hit'])
  with self.assertRaises(ValueError):P.inspect(self.root,out['id'],'not_assessed')
  db=P.storage(self.root)
  try:
   with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM comparisons')
  finally:db.close()

 def test_changed_review_invalidates_affected_patient_only(self):
  self.f.review(self.f.a);out=P.execute(self.root,['p','q'],self.params)
  self.f.review(self.f.a,status='unresolved',revision=1)
  with self.assertRaises(ValueError):P.inspect(self.root,out['id'],'p')
  self.assertEqual(P.inspect(self.root,out['id'],'q')['patient_change']['patient_id'],'q')
  new=P.execute(self.root,['p','q'],self.params);self.assertNotEqual(new['id'],out['id'])
  self.assertEqual(new['change_counts']['exited_review'],0)

 def test_empty_and_same_patient_same_snapshot_guards(self):
  out=P.execute(self.root,[],self.params);self.assertEqual(out['patients_evaluated'],0)
  self.f.review(self.f.a);out=P.execute(self.root,['p'],self.params);pair=P.inspect(self.root,out['id'],'p')['protocol_receipts']
  for altered in ({**pair['after'],'patient_id':'q'},{**pair['after'],'as_of':'2020-01-01'}):
   with self.assertRaises(ValueError):P.project(pair['before'],altered)
  with self.assertRaises(ValueError):P.execute(self.root,['not_assessed'],self.params)

 def test_registered_question_compiles_through_actual_planner(self):
  ex=protocol_change_examples()[0];graph,route=local_plan(ex['question'],fixture.ASOF)
  self.assertEqual(route,'registered_example');self.assertEqual(G.validate(graph,fixture.ASOF)['nodes'][-1]['operation'],'protocol_change')
  with self.assertRaises(ValueError):G.validate({**graph,'nodes':graph['nodes']+[{'id':'bad','operation':'worklist','inputs':['version_impact'],'parameters':{'program':'hf_review'},'purpose':'invalid output type'}]},fixture.ASOF)


if __name__=='__main__':unittest.main()
