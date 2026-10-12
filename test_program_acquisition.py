import copy,itertools,unittest
from clinical_core.agere.clinical import program_documents as D,program_resolution as P,program_acquisition as A,selection_context as S,program_work as W
from clinical_core.agere.clinical.economical_planner import local_plan
from test_program_documents import package
import test_protocol_requirements as fixtures


class ProgramAcquisition(unittest.TestCase):
 def setUp(self):
  self.f=fixtures.RequirementBindings();self.f.setUp();self.addCleanup(self.f.doCleanups);self.root=self.f.root
  self.spec=package('2',extra=True);self.key=D.install(self.root,self.spec)['id'];self.parameters={'package':self.key}

 def row(self,pid='P-2',spec=None):
  key=D.install(self.root,spec or self.spec)['id'];out=D.execute(self.root,[pid],{'package':key,'filter':'all'})[2]
  return P.project(D.load_package(self.root,key),D.inspect(self.root,out['patients'][0]['id']))

 def test_conditional_pair_and_single_negatives_reuse_received_requests(self):
  row=self.row();cases=row['resolution']['cases']
  self.assertEqual(sorted((c['program_value'],len(c['source_values'])) for c in cases),[(False,1),(False,1),(True,2)])
  kinds={r['kind'] for r in row['requests']};self.assertEqual(kinds,{'review_quoted_mapping','bind_rule_input','locate_source_account'})
  self.assertTrue(all(c['request_ids'] for c in cases));self.assertFalse(row['clinical_eligibility_established'])
  self.assertTrue(all(not r['request_sent'] for r in row['requests']))

 def test_known_negative_does_not_request_irrelevant_patient_evidence(self):
  self.f.save([self.f.screen(difficulty_paying_medical_bills=False)])
  row=self.row('P-1');self.assertEqual(row['resolution']['status'],'already_resolved')
  self.assertEqual(row['resolution']['cases'][0]['source_values'],{})
  self.assertFalse(any(r['relevant_to_unresolved_predicate'] for r in row['requests']))
  self.assertTrue(all(r['scope']=='specification' for r in row['requests']))

 def test_shared_source_aliases_cannot_resolve_to_incompatible_polarities(self):
  spec=package('aliases');spec['bindings']['opposite']=copy.deepcopy(spec['bindings']['report'])
  spec['input_bindings']['opposite']={**spec['input_bindings']['report'],'expected':'contradicted'}
  spec['spec']['predicate']={'all':[{'eq':['report',True]},{'eq':['opposite',True]}]}
  row=self.row(spec=spec);self.assertEqual(len(row['variables']),1)
  self.assertEqual([c['program_value'] for c in row['resolution']['cases']],[False,False])

 def test_known_mapping_never_proposes_impossible_false_knownness(self):
  row=self.row(spec=package('known',known=True))
  self.assertEqual([c['program_value'] for c in row['resolution']['cases']],[True,True])
  self.assertTrue(all(c['resulting_input_values']['report'] for c in row['resolution']['cases']))

 def test_typed_domains_and_effective_blockers_are_explicit(self):
  spec=package('number');spec['input_bindings']['report']={'kind':'unbound','label':'Unknown amount','value_type':'number','reason':'No numeric source mapping'};spec['spec']['predicate']={'gt':['report',10]}
  row=self.row(spec=spec);self.assertFalse(row['resolution']['complete']);self.assertEqual(row['resolution']['status'],'unsupported_conditional_domain')
  spec=package('expired');spec['spec']['effective_end']='2028-01-02';row=self.row(spec=spec)
  self.assertEqual(row['resolution']['status'],'specification_blocked');self.assertEqual(row['resolution']['cases'],[])

 def test_boolean_comparison_translation_matches_program_for_every_assignment(self):
  from clinical_core.agere.policy import expression
  from clinical_core.agere.clinical.evidence_requirements import logic
  exprs=[{'eq':['a',False]},{'ne':['a',False]},{'in':['a',[True,False]]},{'not':{'all':[{'eq':['a',True]},{'ne':['b',True]}]}}]
  for predicate in exprs:
   translated=P.boolean_expression(predicate,{'a':'x','b':'y'})
   for a,b in itertools.product((False,True,None),repeat=2):self.assertIs(logic(translated,{'x':a,'y':b}),expression(predicate,{'a':a,'b':b}))

 def test_shared_spec_tasks_are_not_multiplied_and_exact_replay_is_cached(self):
  out=A.prepare(self.root,['P-1','P-2'],self.parameters);self.assertEqual(out['counts']['specification_requests'],2);self.assertEqual(out['counts']['patient_requests'],1)
  page=A.list_items(self.root,out['id']);binding=next(r for r in page['items'] if r['kind']=='bind_rule_input')
  self.assertEqual(binding['patients_with_dependency'],['P-1','P-2']);self.assertEqual(binding['patients_with_conditional_impact'],['P-1','P-2'])
  self.assertEqual(A.inspect_patient(self.root,out['id'],'P-1')['resolution']['status'],'unresolved')
  again=A.prepare(self.root,['P-1','P-2'],self.parameters);self.assertTrue(again['cache_hit']);self.assertEqual(again['processing_counts'],{'patient_projection_cache_hits':2})
  with self.assertRaises(ValueError):A.inspect_patient(self.root,out['id'],'outside')
  self.assertEqual(A.prepare(self.root,[],self.parameters)['counts']['requests'],0)

 def test_source_correction_invalidates_only_affected_patient_inspection(self):
  out=A.prepare(self.root,['P-1','P-2'],self.parameters);self.f.save([self.f.screen(difficulty_paying_medical_bills=False)])
  with self.assertRaises(ValueError):A.list_items(self.root,out['id'])
  with self.assertRaises(ValueError):A.inspect_patient(self.root,out['id'],'P-1')
  self.assertEqual(A.inspect_patient(self.root,out['id'],'P-2')['resolution']['status'],'unresolved')
  new=A.prepare(self.root,['P-1','P-2'],self.parameters);self.assertEqual(new['processing_counts'],{'patient_projections_computed':1,'patient_projection_cache_hits':1})

 def test_acquisition_attaches_to_work_and_review_revision_blocks_advancement(self):
  out=A.prepare(self.root,['P-1','P-2'],self.parameters);acq=A.contexts(self.root,out['id'],['P-1'])
  contexts=S.contexts(self.root,['P-1'],[],{},evaluated_patients={},program_acquisition=acq)
  work=W.prepare(self.root,'protocol:affordability_action_review@2',['P-1'],decision_contexts=contexts)
  D.review(self.root,{'package':self.key,'expected_revision':0,'actor':'ISOLATED TEST FIXTURE','status':'changes_requested','note':'Fixture only'})
  with self.assertRaises(ValueError):W.materialize(self.root,work['proposal_id'],'ISOLATED TEST FIXTURE')
  row=self.row('P-1');self.assertEqual(row['resolution']['status'],'specification_blocked')

 def test_local_question_preserves_complete_population_and_optional_work(self):
  plan,route=local_plan(A.question(self.key,'protocol:affordability_action_review@2'),'2029-02-15')
  self.assertEqual(route,'exact_imported_program_acquisition');self.assertEqual(plan['nodes'][-1]['inputs'],['population','requests'])
  self.assertIsNone(A.local_question(A.question(self.key)+' Only adults.'))

if __name__=='__main__':unittest.main()
