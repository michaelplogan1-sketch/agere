"""Authored graph/reference identity equivalence, not scientific applicability qualification."""
import copy
import unittest
from unittest.mock import Mock, patch
from clinical_core.agere.clinical import plan_optimizer as O, graph_references as R, analysis_graph as G


def node(ident, operation, parameters, inputs=()):
    return {'id':ident,'operation':operation,'parameters':parameters,'inputs':list(inputs),'purpose':'Authored identity control'}


def fixture():
    state={'streams':['notes'],'concept':'medication metformin','relation':'reported_use','clock':'source_statement_date','filter':'all'}
    params={'reference_id':'a'*64,'available_by':'2030-06-30','bindings':[{'question_key':'q','node_ids':['evidence_b']}]}
    return {'interpretation':'Bind original documentary evidence','coverage':'supported_observation','unmet_requirements':[], 'nodes':[
        node('population','cohort',{'specification':{'all':[],'any':[],'exclude':[]}}),
        node('evidence_a','source_state',copy.deepcopy(state),['population']),
        node('evidence_b','source_state',copy.deepcopy(state),['population']),
        node('reference','reference_review',params,['population','evidence_b'])]}


def project(plan, reference='reference', patient='A'):
    consumer=next(n for n in plan['nodes'] if n['id']==reference)
    prep={'reference':{'id':'a'*64,'checked_on':'2030-06-01','questions':[{'key':'q','question':'Authored question','basis':'authored'}]},
          'id':'authored-dependency','lifecycle':{'status':'available_unreviewed'}}
    receipt={'patient_id':'A','receipt_id':'authored-receipt','receipt_digest':'authored-digest','status':'received'}
    evidence={n['id']:(n,{'A':receipt}) for n in plan['nodes'] if n['id'] in consumer['inputs'][1:]}
    return R.project(patient,prep,consumer['parameters'],evidence,'Original question, not a rewritten ID')


class GraphIdentity(unittest.TestCase):
    def checked(self,p): return G.validate(p,'2030-06-30')

    def test_duplicate_reference_evidence_preserves_exact_projection_and_unknowns(self):
        original=self.checked(fixture());saved=copy.deepcopy(original)
        with patch.dict('os.environ',{'AGERE_PLAN_OPTIMIZER':'on'}):
            optimized,receipt=O.optimize_plan(original,validator=self.checked)
        self.assertEqual(receipt['fused'],[]);self.assertIn('evidence_b',receipt['identity_preserved'])
        self.assertEqual(original,saved)
        for patient in ('A','B'):
            self.assertEqual(project(original,patient=patient),project(optimized,patient=patient))
            row=project(optimized,patient=patient)
            self.assertEqual(row['status'],'applicability_unresolved')
            self.assertFalse(row['clinical_applicability_established'])
        self.assertEqual(project(optimized,patient='B')['questions'][0]['evidence_links'][0]['status'],'patient_not_evaluated_by_evidence_node')

    def test_multiple_consumers_and_on_off_evidence_identity_match(self):
        p=fixture();extra=copy.deepcopy(p['nodes'][-1]);extra['id']='reference_second';p['nodes'].append(extra)
        p=self.checked(p)
        with patch.dict('os.environ',{'AGERE_PLAN_OPTIMIZER':'off'}):off,_=O.optimize_plan(p)
        with patch.dict('os.environ',{'AGERE_PLAN_OPTIMIZER':'on'}):on,_=O.optimize_plan(p,validator=self.checked)
        for ref in ['reference','reference_second']:self.assertEqual(project(off,ref),project(on,ref))

    def test_referenced_population_lineage_is_not_silently_rebound(self):
        p=fixture();population=copy.deepcopy(p['nodes'][0]);population['id']='population_alias';p['nodes'].insert(1,population)
        for n in p['nodes'][2:]:n['inputs']=['population_alias' if x=='population' else x for x in n['inputs']]
        p=self.checked(p)
        optimized,receipt=O.optimize_plan(p,validator=self.checked)
        self.assertIn('population_alias',receipt['identity_preserved'])
        self.assertEqual(project(p),project(optimized));self.assertEqual(optimized,p)

    def test_both_alias_inputs_keep_distinct_declared_reference_links(self):
        p=fixture();r=p['nodes'][-1];r['inputs']=['population','evidence_a','evidence_b'];r['parameters']['bindings'][0]['node_ids']=['evidence_a','evidence_b']
        p=self.checked(p);optimized,receipt=O.optimize_plan(p,validator=self.checked)
        self.assertEqual(receipt['fused'],[]);self.assertEqual(project(p),project(optimized))

    def test_opaque_reference_forms_are_kept_without_rewriting_data_strings(self):
        p=fixture();r=p['nodes'][-1];r['parameters']={'opaque':{'uses':{'evidence_b':True}},'patient_id':'evidence_b','query':"SELECT 'evidence_b' AS value"}
        p['interpretation']='evidence_b';saved=copy.deepcopy(p)
        optimized,receipt=O.optimize_plan(p)
        self.assertEqual(receipt['fused'],[]);self.assertEqual(optimized,saved)
        # Unsupported parameter forms are kept, not declared valid or sent to execution.

    def simple(self):
        p=fixture();p['nodes']=[p['nodes'][0],node('population_alias','cohort',copy.deepcopy(p['nodes'][0]['parameters'])),
                              node('answer','sql',{'query':'SELECT COUNT(*) AS n FROM patient','returns':'value'},['population_alias'])]
        return self.checked(p)

    def test_safe_plain_graph_sharing_still_runs_complete_validation(self):
        p=self.simple();validator=Mock(side_effect=self.checked)
        optimized,receipt=O.optimize_plan(p,validator=validator)
        self.assertEqual(receipt['fused'],[{'node':'population_alias','reads':'population'}]);validator.assert_called_once()
        self.assertEqual(optimized['nodes'][-1]['inputs'],['population'])

    def test_failed_post_fusion_validation_heals_to_original_graph(self):
        p=self.simple();saved=copy.deepcopy(p)
        optimized,receipt=O.optimize_plan(p,validator=Mock(side_effect=ValueError('Authored dangling typed reference')))
        self.assertIs(optimized,p);self.assertEqual(p,saved);self.assertEqual(receipt['fused'],[])
        self.assertTrue(receipt['rejected_fusion']['fused']);self.assertIn('complete validation',receipt['fallback_reason'])


if __name__=='__main__':unittest.main()
