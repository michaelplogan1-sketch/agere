"""Shared contracts reject measured failures without clinical target fitting."""
import copy
import unittest
from clinical_core.agere.clinical import graph_contracts as C, planner_scope as S
from clinical_core.agere.clinical import analysis_graph as A, cohorts, program_work


def catalog():
    return {'cohort_fields': {'record_fields': {'notes': [], 'medication_orders': []}},
        'source_streams': ['notes', 'medication_orders', 'appointments', 'coverage_verifications'],
        'evidence_catalog': {'assertion_concepts': ['medication bb', 'care nephrology', 'financial hardship'],
            'work_programs': program_work.registered_programs(), 'program_documents': [{'id': 'test-package'}]}}


def population():
    return {'id': 'p', 'operation': 'cohort', 'inputs': [], 'purpose': 'Named synthetic patient',
        'parameters': {'specification': {'all': [{'kind': 'patient', 'field': 'patient_id', 'op': 'eq', 'value': 'P-1001'}],
                                        'any': [], 'exclude': []}}}


class Contracts(unittest.TestCase):
    def test_financial_cutoff_schema_and_runtime_require_an_actual_date(self):
        from clinical_core.agere.clinical.financial_accounts import validate
        field=C.parameter_schema('financial_accounts',catalog())['properties']['available_by']
        self.assertEqual(field['format'],'date')
        for bad in ['snapshot as_of','tomorrow','2030-01-01','2029-13-01']:
            with self.assertRaises(ValueError):validate({'available_by':bad},'2029-08-15')
        self.assertEqual(validate({'available_by':'2028-01-01'},'2029-08-15'),{'available_by':'2028-01-01'})

    def test_open_context_contract_uses_runtime_ids_not_schema_version(self):
        from clinical_core.agere.clinical.state_contracts import CONTRACTS
        schema=C.parameter_schema('context',catalog())
        allowed=schema['properties']['contract']['enum']
        self.assertEqual(set(allowed),{'',*CONTRACTS})
        p={'question':'What explains this admission?', 'streams':['notes'], 'concepts':[],
           'contract':'','program_ids':[],'max_bytes':24000}
        for contract in ['', *CONTRACTS]:
            p['contract']=contract
            C.validate_parameters('context',p)
        for contract in ['graph-operator-contracts/5','invented_admission_contract']:
            self.assertNotIn(contract,allowed)
            with self.assertRaises(ValueError):C.validate_parameters('context',{**p,'contract':contract})
        self.assertEqual(schema['properties']['program_ids']['items']['enum'],['test-package'])

    def test_measure_required_roles_cannot_be_unbound_or_swapped(self):
        c=catalog();c['evidence_catalog']['program_documents']=[
            {'id':'a'*64,'role':'denominator'},{'id':'b'*64,'role':'numerator'}]
        schema=C.parameter_schema('measure_review',c)
        p={'title':'Quoted example','components':{'denominator':{'package':'a'*64},
            'numerator':{'package':'b'*64},'exclusion':{'not_applied':'Descriptive'},
            'attribution':{'unbound':'No attribution definition'}}}
        components=schema['properties']['components']['properties']
        for role,letter in [('denominator','a'),('numerator','b')]:
            self.assertEqual(components[role]['required'],['package'])
            self.assertEqual(components[role]['properties']['package']['enum'],[letter*64])
        from clinical_core.agere.clinical.measure_composition import validate
        validate(p)
        bad=copy.deepcopy(p);bad['components']['denominator']={'unbound':'Missing denominator'}
        with self.assertRaisesRegex(ValueError,'Quoted denominator/numerator'):validate(bad)
        c['evidence_catalog']['program_documents']=[]
        self.assertEqual(C.parameter_schema('measure_review',c)['properties']['components']['properties']['denominator']['properties']['package'],{'not':{}})

    def test_evidence_clause_contract_matches_runtime_bounds(self):
        clauses=C.parameter_schema('evidence',catalog())['properties']['plan']['properties']['clauses']
        self.assertEqual((clauses['minItems'],clauses['maxItems']),(1,8))
        atom={'source':'text','concept':'financial hardship'}
        for n in (0,9):
            with self.assertRaisesRegex(ValueError,'One to eight'):C.validate_parameters('evidence',{'plan':{'match':'all','clauses':[atom]*n}})
        self.assertEqual(len(C.validate_parameters('evidence',{'plan':{'match':'all','clauses':[atom]*8}})['plan']['clauses']),8)

    def test_missing_assertion_concepts_remain_invalid(self):
        for params in ({'streams': ['notes']}, {'streams': ['notes'], 'concepts': []}):
            with self.assertRaises(ValueError): C.validate_parameters('assertion_review', params)
        spec=C.parameter_schema('assertion_review', catalog())
        self.assertEqual(set(spec['required']), {'streams', 'concepts'})
        self.assertEqual(spec['properties']['concepts']['items']['enum'],sorted(catalog()['evidence_catalog']['assertion_concepts']))

    def test_interpretation_uses_selected_streams_not_legacy_examples(self):
        c=catalog();c['source_streams']=['financial_screens','sdoh','social_screens']
        schema=C.parameter_schema('interpretation',c)
        self.assertEqual(schema['properties']['streams']['items']['enum'],c['source_streams'])
        self.assertNotIn('notes',schema['properties']['streams']['items']['enum'])
        from clinical_core.agere.clinical.interpretation_state import FILTERS,validate
        self.assertEqual(set(schema['properties']['filter']['enum']),set(FILTERS))
        validate({'streams':['financial_screens'],'concepts':['financial hardship'],'filter':'needs_review'})
        self.assertEqual(set(schema['required']),{'streams','concepts','filter'})

    def test_source_state_schema_binds_concepts_and_relations_together(self):
        from clinical_core.agere.clinical.source_state import RELATIONS
        props=C.parameter_schema('source_state',catalog())['properties']
        for concept in props['concept']['enum']:
            family=concept.split(' ',1)[0] if concept.startswith(('medication ','care ')) else concept
            for relation in props['relation']['enum']:
                params={'streams':['notes'],'concept':concept,'relation':relation,'clock':'source_statement_date','filter':'all'}
                if relation in RELATIONS[family]:C.validate_parameters('source_state',params)
                else:
                    with self.assertRaises(ValueError):C.validate_parameters('source_state',params)
        with self.assertRaisesRegex(ValueError,'Unmapped concept/relation'):
            C.validate_parameters('source_state',{'streams':['notes'],'concept':'referral','relation':'care_completion',
                'clock':'source_statement_date','filter':'all'})

    def test_record_reading_does_not_invent_predicate_fields(self):
        context=C.parameter_schema('context',catalog())
        self.assertIn('appointments',context['properties']['streams']['items']['enum'])
        spec=C.parameter_schema('cohort',catalog())['properties']['specification']
        streams=spec['properties']['all']['items']['anyOf'][1]['properties']['stream']['enum']
        self.assertNotIn('appointments',streams)
        self.assertIn('medication_orders',streams)

    def test_work_programs_are_runtime_registry(self):
        names=C.parameter_schema('worklist',catalog())['properties']['program']['enum']
        self.assertEqual(set(names),{p['id'] for p in program_work.registered_programs()})
        self.assertIn('appointment_followthrough_source_review',names)
        self.assertIn('cardiometabolic_source_review',names)
        for name in names:program_work.definition(name)

    def test_reports_cannot_feed_population_operators(self):
        seen={'p':'cohort','r':'longitudinal','s':'context','m':'outcome','w':'accountability_review'}
        for op,args in [('union',['p','r']),('worklist',['s']),('context',['r']),('baseline',['p'])]:
            with self.assertRaises(ValueError):C.validate_inputs(op,args,seen)
        C.validate_inputs('baseline',['m'],seen)
        C.validate_inputs('worklist',['p','w'],seen)
        with self.assertRaises(ValueError):C.validate_inputs('worklist',['p','m'],seen)

    def test_direct_execution_enforces_same_input_contract(self):
        graph={'interpretation':'Source review','coverage':'supported_observation','unmet_requirements':[],
            'nodes':[population(),{'id':'c','operation':'context','inputs':['p'],'purpose':'Source context',
                'parameters':{'question':'Review sources','streams':['notes'],'concepts':[],'contract':'','program_ids':[],'max_bytes':24000}},
                {'id':'u','operation':'union','inputs':['p','c'],'parameters':{},'purpose':'Invalid union'}]}
        with self.assertRaisesRegex(ValueError,'result-as-population'):A.validate(graph,'2029-08-15')
        graph['nodes'].pop();self.assertEqual(len(A.validate(graph,'2029-08-15')['nodes']),2)

    def test_one_requirement_declaration_preserves_selector_and_additional_gaps(self):
        scope={'segments':[{'text':'Request','operations':['cohort'],'unmet':'Unverified eligibility'}]}
        wire={'interpretation':'Review only','nodes':[population()],
              'requirement_coverage':[{'segment':0,'node_ids':['p'],'unmet':'Current preferences unavailable'}]}
        graph,rows=S.compile_requirements(wire,scope)
        self.assertEqual(graph['unmet_requirements'],['Unverified eligibility','Current preferences unavailable'])
        self.assertEqual(graph['coverage'],'partial')
        S.coverage(A.validate(graph,'2029-08-15'),rows,scope,inherited_unmet=True)
        with self.assertRaisesRegex(ValueError,'authoritative'):
            S.compile_requirements({**wire,'unmet_requirements':[]},scope)

    def test_coverage_counts_only_reachable_dependencies(self):
        scope={'segments':[{'operations':['cohort','domain_review'],'unmet':''}]}
        graph={'coverage':'supported_observation','unmet_requirements':[],'nodes':[
            {'id':'p','operation':'cohort','inputs':[]}, {'id':'r','operation':'domain_review','inputs':['p']}]}
        rows=[{'segment':0,'node_ids':['r'],'unmet':''}]
        S.coverage(graph,rows,scope)
        # Selected operators are candidates: the review alone still delivers its segment.
        graph['nodes'][1]['inputs']=[];S.coverage(graph,rows,scope)
        # A cohort segment is covered through the review only while the cohort feeds it.
        scope={'segments':[{'operations':['cohort'],'unmet':''},{'operations':['domain_review'],'unmet':''}]}
        rows=[{'segment':0,'node_ids':['r'],'unmet':''},{'segment':1,'node_ids':['r'],'unmet':''}]
        with self.assertRaisesRegex(ValueError,'delivering'):S.coverage(graph,rows,scope)
        graph['nodes'][1]['inputs']=['p'];S.coverage(graph,rows,scope)
        with self.assertRaisesRegex(ValueError,'delivering'):S.coverage(graph,[{**rows[0],'node_ids':[]},rows[1]],scope)


if __name__=='__main__':unittest.main()
