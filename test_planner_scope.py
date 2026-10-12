"""Catalog completeness, requirement coverage, request bounds and accounting."""
import copy
import json
import unittest
from unittest.mock import patch
from clinical_core.agere.clinical import planner_scope as S
from clinical_core.agere.clinical.planner_catalog import serialize
from clinical_core.agere.clinical.graph_planner import SCHEMA
from clinical_core.agere.clinical.economical_planner import economics
from test_planner_catalog import catalog


def raw(v):return {'stop_reason':'end_turn','content':[{'type':'text','text':json.dumps(v)}]}


def fixture():
    c=catalog();c['question']='Inspect the population.'
    c['node_contracts']={f'op{i}':{'parameters':{'full_contract':str(i)*1600},'meaning':f'Operation {i} can describe its registered observations.'} for i in range(20)}
    b={'model':'fixture','system':'All restrictions required.','messages':[{'role':'user','content':serialize(c)}],
        'output_config':{'format':{'type':'json_schema','schema':copy.deepcopy(SCHEMA)}}}
    return c,b


class ScopeTests(unittest.TestCase):
    # These fixtures exercise deferral/paging at a 20 KB bound; the production
    # bound is larger, but the mechanism under test is the same.
    def setUp(self):
        original=S.bounded
        for p in (patch.object(S,'MAX_BYTES',20000),patch.object(S,'PLAN_MAX_BYTES',20000),
                  patch.object(S,'bounded',lambda body,limit=20000:original(body,min(limit,20000)))):
            p.start();self.addCleanup(p.stop)

    def test_catalog_loading_follows_consumers_without_dropping_required_values(self):
        import yaml
        c=catalog();c['source_streams']=['notes','labs']
        c['evidence_catalog'].update(text_axis_values={'assertion':['affirmed','negated']},
            structured_scalar_values={'financial_hardship':['true','false']},
            boolean_valued_concepts=['financial_hardship'],program_documents=[])
        c['node_contracts']={'analyze':c['node_contracts']['analyze']}
        c['source_contract_loading']={'loaded':['notes','labs'],'not_loaded':['unloaded'],
            'full_field_catalog_identity':'audit-only'}
        c['graph_contract_version']='audit-contract-version'
        b={'system':'Registered observations only','output_config':{'format':{'schema':copy.deepcopy(SCHEMA)}}}
        before=copy.deepcopy(c)
        wire=yaml.safe_load(S.catalog_text(S.typed_request(b,c,scoped=True)['messages'][0]).split('\n',1)[1])
        self.assertEqual(c,before)
        self.assertIn('structured_scalar_values',wire['operator_catalog_loading']['deferred_dictionaries'])
        self.assertNotIn('graph_contract_version',wire)
        self.assertEqual(wire['source_streams'],c['source_streams'])
        self.assertEqual(wire['cohort_fields']['record_fields'],__import__('clinical_core.agere.clinical.planner_catalog',fromlist=['factor']).factor(c)['cohort_fields']['record_fields'])
        c['node_contracts']['evidence']={'parameters':{},'meaning':'Observed evidence'}
        wire=yaml.safe_load(S.catalog_text(S.typed_request(b,c,scoped=True)['messages'][0]).split('\n',1)[1])
        for key in ('text_axis_values','structured_scalar_values','boolean_valued_concepts'):
            self.assertEqual(wire['evidence_catalog'][key],c['evidence_catalog'][key])

    def test_selector_schema_names_exact_contract_namespace(self):
        schema=S.selection_schema(['cohort'],{'notes':[],'labs':[]})
        streams=schema['properties']['segments']['items']['properties']['source_streams']['items']
        self.assertEqual(streams,{'type':'string','enum':['labs','notes']})

    def test_schema_reference_does_not_hide_deferred_source_names(self):
        import yaml
        c,_=fixture();c['cohort_fields']={'available_streams':['notes','labs'],'identity':'declared'}
        wire=yaml.safe_load(S.serialize_index(c,['notes','labs']).split('\n',1)[1])
        self.assertIn('schema',str(wire['cohort_fields']['record_fields']))
        with self.assertRaises(ValueError):S.serialize_index(c,['notes'])

    def test_question_partition_must_cover_every_character(self):
        c,_=fixture();s={'segments':[{'text':c['question'],'operations':['op0'],'unmet':''}]}
        self.assertEqual(S.selection(raw(s),c['question'],c['node_contracts']),s)
        for text in ('Inspect','Inspect  the population.','Inspect the population'):
            t=copy.deepcopy(s);t['segments'][0]['text']=text
            with self.assertRaisesRegex(ValueError,'entire question'):S.selection(raw(t),c['question'],c['node_contracts'])

    def test_dispatch_loads_selected_contracts_and_preserves_all_operator_meanings(self):
        c,b=fixture();self.assertFalse(S.bounded(b));calls=[]
        def send(body,*,parse):
            calls.append(body);self.assertTrue(S.bounded(body))
            if len(calls)==1:
                for op in c['node_contracts']:self.assertIn(op,S.text(body['messages'][0]))
                result={'segments':[{'text':c['question'],'operations':['op0'],'unmet':''}]}
            else:
                self.assertEqual(body['output_config']['format']['schema']['properties']['nodes']['items']['properties']['operation']['enum'],['op0'])
                result={'nodes':[{'id':'a','operation':'op0'}],'coverage':'supported_observation','unmet_requirements':[],
                    'requirement_coverage':[{'segment':0,'node_ids':['a'],'unmet':''}]}
            return parse(raw(result)),{'cache_hit':len(calls)==1,'usage':{'input_tokens':5,'output_tokens':3},'cost_estimate':{'estimated_usd':.02}}
        graph,receipt=S.dispatch(b,c,send,parse=S.value)
        self.assertEqual(len(calls),2);self.assertEqual(receipt['new_paid_api_calls'],1)
        self.assertEqual(receipt['cost_estimate']['estimated_usd'],.02)
        self.assertEqual(economics({'steps':[{'receipt':receipt}]})['new_input_tokens'],5)
        self.assertFalse(receipt['scope']['supplied_catalog_vocabulary_truncated']);self.assertFalse(receipt['scope']['semantic_completeness_validated'])
        self.assertNotIn('requirement_coverage',graph)

    def test_unloaded_operators_and_silently_dropped_requirements_rejected(self):
        scope={'segments':[{'text':'a','operations':['op0'],'unmet':''},{'text':'b','operations':['op1'],'unmet':''}]}
        graph={'nodes':[{'id':'a','operation':'op0'}],'coverage':'supported_observation','unmet_requirements':[]}
        rows=[{'segment':0,'node_ids':['a'],'unmet':''},{'segment':1,'node_ids':[],'unmet':''}]
        with self.assertRaisesRegex(ValueError,'delivering'):S.coverage(graph,rows,scope)
        rows[1]['unmet']='Cannot answer b'
        with self.assertRaisesRegex(ValueError,'visible'):S.coverage(graph,rows,scope)
        graph.update(coverage='partial',unmet_requirements=['Cannot answer b']);S.coverage(graph,rows,scope)
        graph['nodes'].append({'id':'x','operation':'op2'})
        with self.assertRaisesRegex(ValueError,'unloaded'):S.coverage(graph,rows,scope)

    def test_unmet_selector_requirement_must_survive(self):
        scope={'segments':[{'text':'risk','operations':['op0'],'unmet':'Clinical risk unsupported'}]}
        g={'nodes':[{'id':'a','operation':'op0'}],'coverage':'supported_observation','unmet_requirements':[]}
        with self.assertRaisesRegex(ValueError,'disappear'):S.coverage(g,[{'segment':0,'node_ids':['a'],'unmet':''}],scope)

    def test_overbroad_scope_stops_explicitly_after_selection(self):
        c,b=fixture();calls=[]
        def send(body,*,parse):
            calls.append(body)
            return parse(raw({'segments':[{'text':c['question'],'operations':list(c['node_contracts'])[:16],'unmet':''}]})),{'cache_hit':False,'cost_estimate':{'estimated_usd':.01}}
        g,r=S.dispatch(b,c,send,parse=S.value)
        self.assertEqual(len(calls),1);self.assertEqual(g['coverage'],'unsupported');self.assertFalse(g['nodes'])
        self.assertIn('narrower question',g['unmet_requirements'][0]);self.assertEqual(r['new_paid_api_calls'],1)

    def test_mixed_unpriced_stages_preserve_known_cost_and_usage(self):
        receipts=[{'usage':{'input_tokens':7}}, {'cost_estimate':{'estimated_usd':.02},'usage':{'output_tokens':5}}]
        r=S.combine_receipts(receipts,{})
        measured=economics({'steps':[{'receipt':r}]})
        self.assertEqual(r['new_paid_api_calls'],2)
        self.assertIsNone(measured['new_api_estimate_usd']);self.assertEqual(measured['known_api_estimate_usd'],.02)
        self.assertEqual(measured['new_input_tokens'],7);self.assertEqual(measured['new_output_tokens'],5)

    def test_selected_source_contracts_load_exactly_and_reject_unloaded_use(self):
        import yaml
        c,b=fixture();c['cohort_fields']['record_fields']['labs'].append('rare_laboratory_field')
        calls=[]
        def send(body,*,parse):
            calls.append(body)
            if len(calls)%2:
                result={'segments':[{'text':c['question'],'operations':['op0'],'source_streams':['notes'],'unmet':''}]}
            else:
                wire=yaml.safe_load(S.catalog_text(body['messages'][0]).split('\n',1)[1]);fields=wire['cohort_fields']['record_fields'];self.assertEqual(set(fields['additional_by_stream']),{'notes'})
                self.assertEqual(wire['source_contract_loading']['not_loaded'],['labs'])
                stream='notes' if len(calls)==2 else 'labs'
                result={'nodes':[{'id':'a','operation':'op0','parameters':{'stream':stream}}],'coverage':'supported_observation','unmet_requirements':[],
                    'requirement_coverage':[{'segment':0,'node_ids':['a'],'unmet':''}]}
            return parse(raw(result)),{'cache_hit':True}
        graph,r=S.dispatch(b,c,send,parse=S.value)
        self.assertEqual(r['scope']['loaded_source_field_contracts'],['notes']);self.assertEqual(r['scope']['unloaded_source_field_contracts'],['labs'])
        self.assertFalse(r['scope']['all_supplied_source_vocabulary_in_plan'])
        with self.assertRaisesRegex(ValueError,'unloaded source'):S.dispatch(b,c,send,parse=S.value)

    def test_source_scope_cannot_invent_or_duplicate_streams(self):
        c,b=fixture()
        for streams in (['private_truth'],['notes','notes'],['notes',None]):
            with self.assertRaises(ValueError):S.selection(raw({'segments':[{'text':c['question'],'operations':['op0'],'source_streams':streams,'unmet':''}]}),c['question'],c['node_contracts'],c['cohort_fields']['record_fields'])

    def test_large_field_catalog_is_explicitly_loaded_in_planning(self):
        c,b=fixture()
        c['cohort_fields']['record_fields']['notes']=['field_'+str(i)+'_'+'v'*30 for i in range(140)]
        seen=[]
        def send(body,*,parse):
            seen.append(body);self.assertTrue(S.bounded(body))
            if len(seen)==1:
                self.assertIn('deferred_cohort_fields',S.text(body['messages'][0]))
                self.assertNotIn('field_139_',S.text(body['messages'][0]))
                result={'segments':[{'text':c['question'],'operations':['op0'],'unmet':''}]}
            else:
                self.assertIn('field_139_',S.text(body['messages'][0]))
                result={'nodes':[{'id':'a','operation':'op0'}],'coverage':'supported_observation','unmet_requirements':[],
                    'requirement_coverage':[{'segment':0,'node_ids':['a'],'unmet':''}]}
            return parse(raw(result)),{'cache_hit':True}
        # Force only the selection-stage size guard using a real oversized field
        # vocabulary; planning must still retain every field under the same bound.
        c['node_contracts']={k:{**v,'meaning':v['meaning']+' Complete observations only.'*26} for k,v in c['node_contracts'].items()}
        graph,r=S.dispatch(b,c,send,parse=S.value)
        self.assertEqual(r['scope']['source_vocabulary_deferred_to_planning'],['cohort_fields'])
        self.assertTrue(r['scope']['all_supplied_source_vocabulary_in_plan'])
        self.assertEqual(len(seen),2)

    def test_small_request_uses_one_call_and_complete_catalog(self):
        c,b=fixture();b['messages'][0]['content']='small full catalog';calls=[]
        def send(body,**kw):calls.append(body);return 'plan','receipt'
        self.assertEqual(S.dispatch(b,c,send,parse=S.value),('plan','receipt'));self.assertEqual(calls,[b])

    def test_large_value_dictionary_deferred_then_restored_exactly(self):
        c,b=fixture();c['observed_values']={'names':['specific_observed_value_'+str(i)+'_z'*25 for i in range(210)]}
        c['evidence_catalog']['concepts']['structured']=['unusual:clinical_'+str(i) for i in range(70)]
        calls=[]
        # Select one small operator contract. The large original request is
        # replaced by explicit staged loading, never a raised provider limit.
        def send(body,*,parse):
            import yaml
            calls.append(body);self.assertTrue(S.bounded(body))
            wire=yaml.safe_load(S.catalog_text(body['messages'][0]).split('\n',1)[1])
            if len(calls)==1:
                self.assertIn('deferred_value_catalogs',wire);self.assertIsNone(wire['observed_values'])
                result={'segments':[{'text':c['question'],'operations':['op0'],'source_streams':[],'unmet':''}]}
            else:
                self.assertEqual(wire['observed_values'],c['observed_values'])
                self.assertEqual(wire['evidence_catalog']['concepts']['structured_by_prefix']['unusual:'],['clinical_'+str(i) for i in range(70)])
                result={'nodes':[{'id':'a','operation':'op0'}],'coverage':'supported_observation','unmet_requirements':[],
                        'requirement_coverage':[{'segment':0,'node_ids':['a'],'unmet':''}]}
            return parse(raw(result)),{'cache_hit':True}
        # A dictionary can still be too large for a final bounded plan. Use
        # shorter values while making the selector's all-operator index larger.
        c['observed_values']={'names':['specific_observed_value_'+str(i) for i in range(180)]}
        c['node_contracts']={k:{**v,'meaning':v['meaning']+' Exact source capabilities only.'*17} for k,v in c['node_contracts'].items()}
        g,r=S.dispatch(b,c,send,parse=S.value)
        self.assertEqual(len(calls),2);self.assertIn('observed_values',r['scope']['source_vocabulary_deferred_to_planning'])


if __name__=='__main__':unittest.main()
