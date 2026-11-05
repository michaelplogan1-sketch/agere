"""Executed source meanings reach verification without changing SQL or authority."""
import copy,json,sqlite3,tempfile,unittest
from pathlib import Path
from clinical_core.agere.clinical.semantic_analyst import SemanticWarehouse
from clinical_core.agere.clinical import backstop_agent as B
from clinical_core.agere.deliverables.receipts import uncompact_json
from clinical_core.contracts import digest


class SourceContracts(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'source.sqlite3'
        with sqlite3.connect(self.path)as db:
            db.executescript("""
                CREATE TABLE meta(key,value);
                INSERT INTO meta VALUES('source_digest','authored-source'),('as_of','2030-05-27'),('version','fixture/1');
                CREATE TABLE patient(patient_id,birth_date,coverage_type,data_segment);
                INSERT INTO patient VALUES('A','1970-01-01','medicare','received'),('B','1970-01-01','medicare','received'),('C','1970-01-01','medicare','duplicate_chart');
                CREATE TABLE death(patient_id,death_date);
                INSERT INTO death VALUES('B','2030-01-01');
                CREATE TABLE fill(patient_id,fill_date,drug_name);
                INSERT INTO fill VALUES('A','2030-05-01','Authored medication'),('B','2030-05-01','Authored medication');
            """)
        self.w=SemanticWarehouse(self.path);self.addCleanup(self.w.close)

    def test_real_cte_and_inherited_view_reads_carry_exact_definition_and_fill_meaning(self):
        self.w.define('2030-05-27')
        sql='WITH chosen AS (SELECT patient_id FROM living_patient) SELECT f.* FROM fill f JOIN chosen USING(patient_id)'
        out=self.w.query(sql)
        self.assertEqual(out['rows'],[{'patient_id':'A','fill_date':'2030-05-01','drug_name':'Authored medication'}])
        c=out['source_contracts'];self.assertEqual(c['source_digest'],'authored-source')
        definitions={x['name']:x for x in c['definitions']}
        self.assertIn('living_patient',definitions)
        self.assertEqual(definitions['living_patient']['query'],self.w._local.defined_views['living_patient'])
        self.assertEqual(definitions['living_patient']['query_digest'],digest(definitions['living_patient']['query']))
        meanings={x['name']:x['meaning'] for x in c['relations']}
        self.assertIn('picked_up = 1',meanings['fill']);self.assertIn('patient',meanings);self.assertIn('death',meanings)

    def test_strings_comments_and_cte_names_are_not_received_source_relations(self):
        self.w.define('2030-05-27')
        out=self.w.query("WITH fill AS (SELECT 'living_patient' AS text) SELECT * FROM fill /* death */")
        self.assertEqual(out['source_contracts']['definitions'],[])
        self.assertEqual(out['source_contracts']['relations'],[])

    def test_authority_is_preserved_after_failure_and_success(self):
        self.w.define('2030-05-27')
        with self.assertRaises(sqlite3.DatabaseError):self.w.query('DELETE FROM patient')
        self.assertEqual(len(self.w.query('SELECT * FROM patient')['rows']),3)
        with self.assertRaises(sqlite3.DatabaseError):self.w.db.execute('DELETE FROM patient')

    def test_plain_semantic_reader_does_not_inherit_product_definitions(self):
        self.assertNotIn('source_contracts',self.w.query('SELECT * FROM fill'))

    def test_support_packet_deduplicates_complete_definitions_and_retains_bindings(self):
        self.w.define('2030-05-27')
        out=self.w.query('SELECT * FROM living_patient');rows=out['rows']
        calls=[{'name':'query_sql','input':{'sql':'SELECT * FROM living_patient'},'result':{**out,'result_view':'base'}},
            {'name':'query_sql','input':{'sql':'SELECT * FROM base'},'result':{**out,'result_view':'final'}}]
        for call in calls:call['sql']=call['input']['sql']
        answer={'table':rows,'table_binding':{'result_view':'final'},'headline':'Received people','facts':{}}
        packets=B.support_packets('List living people',answer,calls,[],'2030-05-27')
        for packet in packets:
            state=uncompact_json(json.loads(packet['text']));definitions=state['executed_source_definitions']
            self.assertEqual(len(definitions),len(out['source_contracts']['definitions'])+len(out['source_contracts']['relations']))
            for c in state['native_calculation_contracts']:
                self.assertEqual(set(c['source_definition_keys']),set(definitions))
            for key,value in definitions.items():self.assertEqual(key,digest(value))
        graph=B.execution_review_graph(calls)
        self.assertEqual(graph['executed_source_definitions'],definitions)
        for node in graph['nodes']:
            self.assertEqual(set(node['parameters']['source_definition_keys']),set(definitions))
        before={p['id']:p['text']for p in packets}
        calls.append({'name':'query_sql','input':{'sql':'SELECT 7'},'result':{'result_view':'unrelated','rows':[{'n':7}],
            'source_contracts':{**out['source_contracts'],'relations':[{'name':'unrelated_source','meaning':'Unrelated context'}]}}})
        after={p['id']:p['text']for p in B.support_packets('List living people',answer,calls,[],'2030-05-27')}
        self.assertEqual(before['patient_A'],after['patient_A']);self.assertNotEqual(before['summary'],after['summary'])


class CompleteNativeCoverageGraph(unittest.TestCase):
    def calls(self):
        return [
            {'name':'query_sql','input':{'sql':'SELECT * FROM patient'},'result':{
                'result_view':'base','executed_query':'SELECT * FROM living_patient','columns':['patient_id'],'rows':[{'patient_id':'A'}]}},
            {'name':'review_documents','input':{'patient_query_sql':'SELECT patient_id FROM base',
                'questions':{'reason':{'question':'Why was this service deferred?'}},'source_scope':'focused'},
                'result':{'result_view':'notes','columns':['patient_id','review_json'],'rows':[{'patient_id':'A','review_json':'{}'}],
                    'coverage':[{'patient_id':'A','unique_whole_documents':2,'absence_established':False}]}},
            {'name':'compose_table','input':{'base':'base','keys':['patient_id'],
                'joins':[{'result':'notes','as':'source_observations','on':{'patient_id':'patient_id'},'mode':'collect'}]},
                'result':{'result_view':'final','columns':['patient_id','source_observations'],'rows':[{'patient_id':'A'}],
                    'assembly':{'base_result_view':'base','keys':['patient_id']}}},
            {'name':'query_sql','input':{'sql':'SELECT count(*) AS n FROM base'},
                'result':{'result_view':'metrics','columns':['n'],'rows':[{'n':1}]}},
            {'name':'query_sql','input':{'sql':'SELECT * FROM unrelated'},'sql':'SELECT * FROM unrelated',
                'result':{'result_view':'old_attempt','columns':['other'],'rows':[]}},
            {'name':'query_sql','input':{'sql':'SELECT invented'},'sql':'SELECT invented','result':{'error':'No such column'}}]

    def test_source_reviews_composition_and_prose_roots_are_visible_without_replaying_history(self):
        calls=self.calls();before=copy.deepcopy(calls)
        answer={'table_binding':{'result_view':'final'},'computed_prose':[{'sources':{
            'count':{'binding':{'result_view':'metrics','row':0,'column':'n'}}}}]}
        graph=B.execution_review_graph(calls,answer);nodes=graph['nodes']
        self.assertEqual([n['operation']for n in nodes],['sql','review_documents','compose_table','sql'])
        self.assertEqual(nodes[0]['parameters']['query'],'SELECT * FROM living_patient')
        self.assertEqual(nodes[0]['parameters']['requested_query'],'SELECT * FROM patient')
        self.assertEqual(nodes[1]['parameters'],calls[1]['input'])
        self.assertFalse(nodes[1]['output']['coverage'][0]['absence_established'])
        self.assertEqual(nodes[2]['output']['assembly'],calls[2]['result']['assembly'])
        self.assertEqual(set(graph['dependency_roots']),{'final','metrics'})
        self.assertEqual(graph['history']['failed_attempts_retained_natively'],1)
        self.assertEqual(graph['history']['successful_results_outside_active_dependencies'],1)
        self.assertEqual(calls,before)

    def test_unknown_root_keeps_every_successful_native_dependency(self):
        graph=B.execution_review_graph(self.calls(),{'table_from_tool':'missing'})
        self.assertTrue(graph['history']['unknown_dependency_scope'])
        self.assertEqual(len(graph['nodes']),5)
        self.assertTrue(all('invented'not in n['parameters'].get('query','')for n in graph['nodes']))

    def test_inline_native_rows_keep_exact_provenance_without_duplicate_coverage_payload(self):
        calls=self.calls();expanded="SELECT * FROM json_each('[{\"source_text\":\"exact native value\"}]')"
        calls[3]['result']['executed_query']=expanded
        graph=B.execution_review_graph(calls,{'table_from_tool':'metrics'})
        node=next(n for n in graph['nodes']if n['output']['result_view']=='metrics')
        self.assertEqual(node['parameters']['query'],calls[3]['input']['sql'])
        self.assertEqual(node['parameters']['native_result_bindings'],['base'])
        self.assertEqual(node['parameters']['native_executable_provenance']['query_digest'],digest(expanded))
        self.assertEqual(calls[3]['result']['executed_query'],expanded)
        self.assertNotIn('exact native value',json.dumps(graph))

    def test_full_retrieval_inventory_keeps_scope_counts_and_a_native_reference(self):
        calls=self.calls();sources=[{'record_id':'source-A','metadata':'inventory bytes'}, {'record_id':'source-B'}]
        selection={'queries':{'reason':'Exact question'},'registered_class_members':{},'sources':sources,
            'inventory_documents':12,'selected_documents':2,'field_documents':{'reason':2},
            'field_applicability':{'reason':True},'complete_chart_review':False,'absence_established':False}
        calls[1]['result']['coverage'][0]['source_selection']=selection;before=copy.deepcopy(calls)
        graph=B.execution_review_graph(calls,{'table_from_tool':'final'})
        node=next(n for n in graph['nodes']if n['operation']=='review_documents')
        scope=node['output']['coverage'][0]['source_selection']
        self.assertEqual(scope['field_documents'],{'reason':2});self.assertFalse(scope['absence_established'])
        self.assertEqual(scope['native_source_inventory']['digest'],digest(sources))
        self.assertEqual(scope['native_source_inventory']['records'],2)
        self.assertEqual(graph['source_selection_definitions'][scope['selection_definition_key']]['queries'],selection['queries'])
        self.assertNotIn('inventory bytes',json.dumps(graph));self.assertEqual(calls,before)


class PatientProseDependencies(unittest.TestCase):
    def setUp(self):
        self.calls=[{'name':'query_sql','input':{'sql':'SELECT patient_id, value FROM received'},
            'result':{'result_view':'base','columns':['patient_id','value'],
                      'rows':[{'patient_id':'A','value':4},{'patient_id':'B','value':7}]}},
            {'name':'query_sql','input':{'sql':'SELECT patient_id, value AS window_months FROM base'},
             'result':{'result_view':'repair','columns':['patient_id','window_months'],
                       'rows':[{'patient_id':'A','window_months':4},{'patient_id':'B','window_months':7}]}}]
        self.answer={'table':[{'patient_id':'A','value':4},{'patient_id':'B','value':7}],
                     'table_from_tool':'base','headline':'Received observations','computed_prose':[]}

    def packets(self,answer,calls=None):
        return {p['id']:p['text']for p in B.support_packets('Inspect each person',answer,
            self.calls if calls is None else calls,[],'2030-05-27')}

    def claim(self,patient='A',view='repair'):
        return {'field':'review','template':'{n} months','rendered':'4 months','entity_key':{'patient_id':patient},
                'sources':{'n':{'binding':{'result_view':view,'row':0,'column':'window_months'},'value':4}},
                'basis':[{'result_view':view,'row':0}]}

    def test_repair_query_returning_all_people_changes_only_the_bound_patient_packet(self):
        before=self.packets(self.answer)
        self.answer['computed_prose']=[self.claim()]
        self.answer['table'][0]['review']='4 months'
        after=self.packets(self.answer)
        self.assertEqual(before['patient_B'],after['patient_B'])
        self.assertNotEqual(before['patient_A'],after['patient_A'])
        self.assertNotEqual(before['summary'],after['summary'])
        a=uncompact_json(json.loads(after['patient_A']))
        b=uncompact_json(json.loads(after['patient_B']))
        self.assertTrue(any(r.get('window_months')==4 for r in a['evidence']))
        self.assertFalse(any('window_months'in r for r in b['evidence']))

    def test_actual_patient_binding_changes_remain_identity_bearing(self):
        self.answer['computed_prose']=[self.claim()]
        before=self.packets(self.answer)
        self.calls[1]['result']['rows'][0]['window_months']=5
        after=self.packets(self.answer)
        self.assertNotEqual(before['patient_A'],after['patient_A'])
        self.assertEqual(before['patient_B'],after['patient_B'])

    def test_final_table_dependencies_are_still_required_for_every_patient(self):
        self.answer['table_from_tool']='repair'
        a=uncompact_json(json.loads(self.packets(self.answer)['patient_B']))
        self.assertTrue(any(r.get('window_months')==7 for r in a['evidence']))
        self.assertEqual({c['result_view']for c in a['native_calculation_contracts']},{'base','repair'})

    def test_unknown_local_root_keeps_conservative_evidence(self):
        self.answer['computed_prose']=[self.claim(patient='B',view='missing')]
        b=uncompact_json(json.loads(self.packets(self.answer)['patient_B']))
        self.assertTrue(any(r.get('window_months')==7 for r in b['evidence']))

    def test_shared_scalar_is_retained_only_where_the_patient_claim_uses_it(self):
        self.calls[1]['result'].update(columns=['window_months'],rows=[{'window_months':4}])
        self.calls[1]['input']['sql']='SELECT count(*) AS window_months FROM base'
        before=self.packets(self.answer)
        self.answer['computed_prose']=[self.claim()]
        after=self.packets(self.answer)
        self.assertEqual(before['patient_B'],after['patient_B'])
        a=uncompact_json(json.loads(after['patient_A']))
        self.assertIn({'window_months':4},a['shared_evidence'])

    def test_global_only_prose_keeps_its_evidence_in_global_review(self):
        before=self.packets(self.answer)
        claim=self.claim();claim.pop('entity_key');self.answer['computed_prose']=[claim]
        after=self.packets(self.answer)
        self.assertEqual(before['patient_A'],after['patient_A'])
        self.assertEqual(before['patient_B'],after['patient_B'])
        self.assertNotEqual(before['summary'],after['summary'])


if __name__=='__main__':unittest.main()
