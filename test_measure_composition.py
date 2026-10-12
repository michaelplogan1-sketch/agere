import copy,itertools,unittest
from clinical_core.agere.clinical import measure_composition as M,program_documents as D,selection_context as S,program_work as W,analysis_graph as G
from clinical_core.agere.clinical.economical_planner import local_plan
from test_program_documents import package
import test_protocol_requirements as fixtures


def component_package(role,known=False,opposite=False):
 spec=package('1',known=known);spec['spec']['id']='synthetic_measure_'+role;spec['role']=role
 spec['spec']['purpose']='Synthetic engineering '+role+' for received affordability reports; not a payer measure.'
 if opposite:spec['input_bindings']['report']['expected']='contradicted'
 return spec


class MeasureComposition(unittest.TestCase):
 def setUp(self):
  self.f=fixtures.RequirementBindings();self.f.setUp();self.addCleanup(self.f.doCleanups);self.root=self.f.root
  self.den=D.install(self.root,component_package('denominator',known=True))['id'];self.num=D.install(self.root,component_package('numerator'))['id']
  self.params={'title':'Synthetic descriptive received-report composition','components':{'denominator':{'package':self.den},'numerator':{'package':self.num},
   'exclusion':{'not_applied':'Descriptive engineering statistic, no exclusion applied.'},'attribution':{'not_applied':'Descriptive engineering statistic, not payer/provider attribution.'}}}

 def test_full_unknown_population_preserved_and_shared_sources_correlated(self):
  result=M.execute(self.root,['P-1','P-2'],self.params)
  self.assertEqual(result['counts'],{'known_denominator':1,'known_numerator':1,'unresolved_measure_patients':1,'status_counts':{'numerator_met':1,'denominator_unresolved':1}})
  self.assertIsNone(result['rate']);self.assertEqual(result['sensitivity_bounds']['minimum'],.5);self.assertEqual(result['sensitivity_bounds']['maximum'],1)
  row=M.inspect(self.root,result['id'],'P-2')['patient']
  self.assertEqual(row['sensitivity']['possible_denominator_numerator_pairs'],[[1,0],[1,1]])
  self.assertFalse(result['certified_measure']);self.assertFalse(row['clinical_execution_authorized'])

 def test_missing_optional_components_never_become_false_or_true(self):
  p=copy.deepcopy(self.params);p['components']['attribution']={'unbound':'No attribution source supplied.'};p['components']['exclusion']={'unbound':'No exclusion specification supplied.'}
  result=M.execute(self.root,['P-1','P-2'],p);self.assertEqual(result['counts']['known_denominator'],0);self.assertEqual(result['counts']['unresolved_measure_patients'],2)
  self.assertTrue(result['sensitivity_bounds']['zero_denominator_possible'])

 def test_shared_positive_and_opposite_role_cannot_create_positive_ratio(self):
  den=component_package('denominator');den['spec']['version']='2';p=copy.deepcopy(self.params);p['components']['denominator']={'package':D.install(self.root,den)['id']}
  num=component_package('numerator',opposite=True);num['spec']['version']='2';p['components']['numerator']={'package':D.install(self.root,num)['id']}
  result=M.execute(self.root,['P-2'],p);row=M.inspect(self.root,result['id'],'P-2')['patient']
  self.assertEqual(row['sensitivity']['possible_denominator_numerator_pairs'],[[0,0],[1,0]])
  self.assertEqual(result['sensitivity_bounds']['maximum'],0)

 def test_known_exclusion_removes_patient_despite_missing_attribution(self):
  p=copy.deepcopy(self.params);p['components']['exclusion']={'package':D.install(self.root,component_package('exclusion'))['id']};p['components']['attribution']={'unbound':'Unknown attribution'}
  result=M.execute(self.root,['P-1'],p);self.assertEqual(result['counts']['status_counts'],{'outside_denominator':1});self.assertEqual(result['rate_status'],'zero_denominator')

 def test_change_invalidates_affected_only_and_blocks_work(self):
  r=M.execute(self.root,['P-1','P-2'],self.params);again=M.execute(self.root,['P-1','P-2'],self.params);self.assertTrue(again['cache_hit']);self.assertEqual(again['processing_counts'],{'patient_cache_hits':2})
  ctx=S.contexts(self.root,['P-1'],[],{},evaluated_patients={},measure=M.contexts(self.root,r['id'],['P-1']))
  work=W.prepare(self.root,'protocol:affordability_action_review@2',['P-1'],decision_contexts=ctx)
  self.f.save([self.f.screen(difficulty_paying_medical_bills=False)])
  with self.assertRaises(ValueError):M.inspect(self.root,r['id'],'P-1')
  self.assertEqual(M.inspect(self.root,r['id'],'P-2')['patient']['status'],'denominator_unresolved')
  with self.assertRaises(ValueError):W.materialize(self.root,work['proposal_id'],'ISOLATED TEST FIXTURE')
  new=M.execute(self.root,['P-1','P-2'],self.params);self.assertEqual(new['processing_counts'],{'patients_computed':1,'patient_cache_hits':1})

 def test_role_mismatch_unknown_patient_and_empty_population(self):
  p=copy.deepcopy(self.params);p['components']['denominator']['package']=self.num
  with self.assertRaises(ValueError):M.prepare(self.root,p)
  r=M.execute(self.root,[],self.params);self.assertEqual(r['patients_evaluated'],0);self.assertIsNone(r['rate']);self.assertIsNone(r['sensitivity_bounds']['maximum'])
  with self.assertRaises(ValueError):M.inspect(self.root,r['id'],'outside')

 def test_review_correction_blocks_existing_measure_and_work(self):
  r=M.execute(self.root,['P-1'],self.params);D.review(self.root,{'package':self.num,'expected_revision':0,'actor':'ISOLATED TEST FIXTURE','status':'changes_requested','note':'Fixture only'})
  with self.assertRaises(ValueError):M.inspect(self.root,r['id'])
  r=M.execute(self.root,['P-1'],self.params);self.assertEqual(r['counts']['status_counts'],{'numerator_unresolved':1})

 def test_question_contract_and_measure_support_cannot_be_population(self):
  plan,route=local_plan(M.question(self.den,self.num),'2029-02-15');self.assertEqual(route,'exact_documentary_measure')
  plan['nodes'].append({'id':'work','operation':'worklist','inputs':['population','measure'],'parameters':{'program':'protocol:affordability_action_review@2'},'purpose':'Separate source review'})
  G.validate(plan,'2029-02-15');plan['nodes'][-1]['inputs']=['measure']
  with self.assertRaises(ValueError):G.validate(plan,'2029-02-15')

 def test_logical_rate_bounds_match_exhaustive_possible_count_pairs(self):
  pairs=[(0,0),(1,0),(1,1)];domains=[list(c) for n in (1,2,3) for c in itertools.combinations(pairs,n)]
  for patient_sets in itertools.product(domains,repeat=3):
   rows=[{'sensitivity':{'possible_denominator_numerator_pairs':p}} for p in patient_sets];got=M.rate_bounds(rows)
   totals=[(sum(v[0] for v in choice),sum(v[1] for v in choice)) for choice in itertools.product(*patient_sets)]
   rates=[n/d for d,n in totals if d];self.assertEqual(got['minimum'],min(rates) if rates else None);self.assertEqual(got['maximum'],max(rates) if rates else None)
   self.assertEqual(got['zero_denominator_possible'],any(d==0 for d,n in totals))

if __name__=='__main__':unittest.main()
