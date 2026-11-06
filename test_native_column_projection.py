"""Executed column reads narrow arithmetic context without accepting meaning."""
import copy,json,sqlite3,tempfile,unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
from clinical_core.agere.clinical import backstop_agent as B,semantic_analyst as S
from clinical_core.agere.clinical.evidence_projection import native_column_projections,replayed_column_dependencies
from clinical_core.agere.clinical.sql_query import native_result_source
from clinical_core.contracts import digest
from clinical_core.agere.deliverables.receipts import uncompact_json


class NativeColumnProjection(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        path=Path(self.temp.name)/'semantic.sqlite3'
        with sqlite3.connect(path)as db:
            db.executescript("CREATE TABLE meta(key,value);INSERT INTO meta VALUES('as_of','2030-05-27'),('source_digest','authored');CREATE TABLE patient(patient_id);")
        self.wh=S.SemanticWarehouse(path);self.wh.define('2030-05-27');self.addCleanup(self.wh.close)
        self.producer={'name':'execute_analysis','input':{'operation':'authored_review','parameters':{'available_by':'2030-05-27'}},'result':{
            'columns':['patient_id','status','documentary_receipt'],
            'rows':[['A','unresolved',{'quote':'A complete source interpretation is still required.'}],
                    ['A','unresolved',{'quote':'A complete source interpretation is still required.'}],
                    ['B',None,{'quote':'No current eligibility is established.'}]]}}
        B.define_result(self.wh,'agere_result_1',self.producer['result']['columns'],self.producer['result']['rows'],receipt=self.producer['result'])
        self.producer['result']['result_view']='agere_result_1'
        self.query="SELECT COUNT(*) AS n, SUM(CASE WHEN status='unresolved' THEN 1 ELSE 0 END) AS unresolved FROM agere_result_1"
        self.consumer=self.execute(self.query)
        self.calls=[self.producer,self.consumer]
        self.answer={'status':'partial','sources':[],'headline':{'template':'{n} machine receipts were produced.',
            'bindings':{'n':{'result_view':'agere_result_2','row':0,'column':'n'}}},'facts':{'status':{
            'template':'{n} machine receipts remain unresolved.',
            'bindings':{'n':{'result_view':'agere_result_2','row':0,'column':'unresolved'}}}}}
        B.bind_computed_prose(self.answer,self.calls,question='Count machine receipts and unresolved statuses.')

    def execute(self,sql):
        result=self.wh.query(sql,max_rows=None,max_chars=None)
        result.update(result_view='agere_result_2',executed_query=sql,native_sql_contract=B.NATIVE_SQL_CONTRACT)
        return {'name':'query_sql','input':{'sql':sql},'result':result}

    def project(self,calls=None,roots=None):
        return native_column_projections(calls or self.calls,roots or ['agere_result_2'],{'agere_result_1','agere_result_2'})

    def test_sqlite_reads_capture_filter_group_order_and_join_columns(self):
        query="SELECT status,COUNT(*) AS n FROM agere_result_1 WHERE patient_id<>'C' GROUP BY status ORDER BY status"
        result=self.execute(query)['result'];read=result['native_read_columns']['sources']['agere_result_1']
        self.assertTrue(read['complete']);self.assertEqual(read['columns'],['patient_id','status'])
        B.define_result(self.wh,'agere_result_3',['patient_id'],[['A'],['B']])
        result=self.execute('SELECT COUNT(*) AS n FROM agere_result_1 a JOIN agere_result_3 b ON a.patient_id=b.patient_id')['result']
        self.assertEqual(set(result['native_read_columns']['sources']),{'agere_result_1','agere_result_3'})
        self.assertEqual(result['native_read_columns']['sources']['agere_result_1']['columns'],['patient_id'])

    def test_all_rows_duplicates_nulls_and_native_receipts_survive(self):
        before=copy.deepcopy(self.calls);projection=self.project()['agere_result_1']
        self.assertEqual(projection['columns'],['patient_id','status'])
        self.assertEqual(projection['rows'],[['A','unresolved'],['A','unresolved'],['B',None]])
        self.assertEqual(projection['row_count'],3);self.assertEqual(self.calls,before)
        self.assertIn('no support for clinical interpretation',projection['scope'])

    def test_count_star_retains_row_multiplicity_even_without_column_reads(self):
        self.consumer=self.execute('SELECT COUNT(*) AS n FROM agere_result_1');self.calls=[self.producer,self.consumer]
        projection=self.project()['agere_result_1']
        self.assertEqual(projection['columns'],['patient_id']);self.assertEqual(projection['rows'],[['A'],['A'],['B']])

    def sql_intermediate(self):
        producer=self.execute('SELECT * FROM agere_result_1')
        producer['result']['result_view']='agere_result_4'
        columns,rows=B.output_table(producer['result'])
        B.define_result(self.wh,'agere_result_4',columns,rows,receipt=producer['result'])
        consumer=self.execute("SELECT COUNT(*) AS n FROM agere_result_4 WHERE status='unresolved'")
        return [self.producer,producer,consumer]

    def test_native_sql_intermediate_preserves_every_selected_cell_and_original_receipt(self):
        calls=self.sql_intermediate();before=copy.deepcopy(calls)
        projected=native_column_projections(calls,['agere_result_2'],{'agere_result_1','agere_result_2','agere_result_4'})['agere_result_4']
        self.assertEqual(projected['columns'],['patient_id','status'])
        self.assertEqual(projected['rows'],[['A','unresolved'],['A','unresolved'],['B',None]])
        self.assertEqual(calls,before)

    def test_unqualified_sql_intermediate_keeps_complete_context(self):
        for mutation in ('contract','query','incomplete'):
            if mutation=='contract':calls=self.sql_intermediate()
            else:calls=copy.deepcopy(original)
            if mutation=='contract':original=copy.deepcopy(calls);calls[1]['result'].pop('native_sql_contract')
            elif mutation=='query':calls[1]['result']['executed_query']=''
            else:calls[1]['result']['truncated']=True
            with self.subTest(mutation=mutation):
                self.assertNotIn('agere_result_4',native_column_projections(calls,['agere_result_2'],{'agere_result_1','agere_result_2','agere_result_4'}))

    def test_text_matching_intermediate_keeps_its_full_source_context(self):
        producer=self.execute("SELECT * FROM agere_result_1 WHERE documentary_receipt LIKE '%source%'")
        producer['result']['result_view']='agere_result_4';columns,rows=B.output_table(producer['result'])
        B.define_result(self.wh,'agere_result_4',columns,rows,receipt=producer['result'])
        consumer=self.execute('SELECT COUNT(*) AS n FROM agere_result_4')
        self.assertNotIn('agere_result_4',native_column_projections([self.producer,producer,consumer],
            ['agere_result_2'],{'agere_result_1','agere_result_2','agere_result_4'}))

    def test_stale_source_query_missing_metadata_and_incomplete_inputs_keep_full_context(self):
        for kind in ('source','query','missing','incomplete','read_map','consumer'):
            calls=copy.deepcopy(self.calls)
            if kind=='source':calls[0]['result']['rows'][0][1]='supported'
            if kind=='query':calls[1]['result']['executed_query']+=" WHERE status='eligible'"
            if kind=='missing':calls[1]['result'].pop('native_read_columns')
            if kind=='incomplete':calls[0]['result']['truncated']=True
            if kind=='read_map':calls[1]['result']['native_read_columns']['sources']['agere_result_1']['complete']=False
            if kind=='consumer':calls[1]['name']='execute_analysis'
            with self.subTest(kind=kind):self.assertEqual(self.project(calls),{})

    def test_unknown_consumers_direct_bindings_and_document_readers_never_project(self):
        self.assertEqual(self.project(roots=['agere_result_1','agere_result_2']),{})
        calls=copy.deepcopy(self.calls);calls[0]['name']='review_documents'
        self.assertEqual(self.project(calls),{})
        calls=copy.deepcopy(self.calls);calls[1]['result'].pop('read_views')
        self.assertEqual(self.project(calls),{})

    def test_source_note_read_is_not_hidden_by_column_projection(self):
        calls=[self.producer,self.execute('SELECT documentary_receipt FROM agere_result_1')]
        self.assertEqual(self.project(calls),{})

    def test_shadowed_cte_is_not_an_actual_native_read(self):
        result=self.execute("WITH agere_result_1 AS (SELECT 'unresolved' AS status) SELECT COUNT(*) AS n FROM agere_result_1")['result']
        self.assertEqual(result['read_views'],[]);self.assertNotIn('native_read_columns',result)

    def test_tableless_bound_summary_uses_exact_projection_and_still_requires_meaning_review(self):
        before=copy.deepcopy(self.calls)
        packets=B.support_packets('Count machine receipts and unresolved statuses.',self.answer,self.calls,[],'2030-05-27')
        state=uncompact_json(json.loads(packets[0]['text']))
        self.assertEqual(state['native_dependency_scope']['required_result_views'],['agere_result_1','agere_result_2'])
        self.assertEqual(state['native_column_projections'][0]['row_count'],3)
        self.assertNotIn('global_patient_evidence',state)
        self.assertEqual(self.calls,before)
        rejected=B.check_support_packets(packets,lambda *a,**k:{'label':'contradicted','decided_by':'authored-meaning-check','steps':[]},
            field_decider=lambda subject,questions,*a,**k:{'judgments':{key:{'label':'wrong_population','decided_by':'authored-meaning-check','probability':1.}for key in questions},'steps':[]},
            as_of='2030-05-27',ledger=None)
        self.assertNotEqual(rejected['label'],'supported')

    def test_unbound_global_claim_keeps_complete_source_observations(self):
        self.answer['headline']='Clinical eligibility has not been established.'
        packets=B.support_packets('Review clinical eligibility.',self.answer,self.calls,[],'2030-05-27')
        state=uncompact_json(json.loads(packets[0]['text']))
        self.assertNotIn('native_column_projections',state)
        self.assertIn('A complete source interpretation is still required.',json.dumps(state))

    def test_source_column_named_result_view_is_data_not_a_proof_address(self):
        self.consumer=self.execute(self.query.replace(' FROM', ", 'external_events' AS result_view FROM",1))
        self.calls=[self.producer,self.consumer]
        B.bind_computed_prose(self.answer,self.calls,question='Count machine receipts and unresolved statuses.')
        before=copy.deepcopy(self.calls)
        state=uncompact_json(json.loads(B.support_packets('Count machine receipts and unresolved statuses.',
            self.answer,self.calls,[],'2030-05-27')[0]['text']))
        self.assertEqual(state['native_dependency_scope']['required_result_views'],['agere_result_1','agere_result_2'])
        self.assertEqual(state['native_column_projections'][0]['rows'],[['A','unresolved'],['A','unresolved'],['B',None]])
        self.assertIn('external_events',json.dumps(state))
        self.assertEqual(self.calls,before)

    def test_measure_population_and_basis_addresses_survive_source_marker_collisions(self):
        proofs=[{'basis':[{'result_view':'basis','row':0}], 'sources':{
            'n':{'binding':{'measure':{'result_view':'events','population':{'result_view':'population'}}},
                 'source_row':{'result_view':'literal_business_data'},'value':{'result_view':'also_literal'}}}}]
        self.assertEqual(B.computed_prose_views(proofs),['basis','events','population'])
        calls=[{'input':{},'result':{'result_view':v}}for v in ['basis','events']]
        self.assertIsNone(B.native_result_dependencies(calls,B.computed_prose_views(proofs)))
        calls.append({'input':{},'result':{'result_view':'population'}})
        self.assertEqual(B.native_result_dependencies(calls,B.computed_prose_views(proofs)),{'basis','events','population'})

    def json_fixture(self,expression="json_extract(r.value,'$.id') AS rule_id",path="'$.contract.requirements'",malformed=False):
        rows=[['A','unresolved',{'contract':{'requirements':[{'id':'rule','binding':None}]},'unread_notes':'Full original context stays native.'}],
              ['A','unresolved',{'contract':{'requirements':[{'id':'rule','binding':None}]},'unread_notes':'Another complete record.'}],
              ['B',None,{'contract':{'requirements':None}}],['C','unknown',{}]]
        if malformed:rows[-1][-1]='not valid JSON'
        producer={'name':'execute_analysis','input':{'operation':'authored_contract_review'},'result':{
            'columns':['patient_id','status','documentary_receipt'],'rows':rows}}
        B.define_result(self.wh,'agere_result_4',producer['result']['columns'],rows,receipt=producer['result'])
        producer['result']['result_view']='agere_result_4'
        count=self.execute('SELECT status,COUNT(*) AS n FROM agere_result_4 GROUP BY status ORDER BY status')
        query='SELECT '+expression+' FROM (SELECT documentary_receipt AS d FROM agere_result_4 LIMIT 1) x, json_each(x.d,'+path+') r'
        document=self.execute(query);document['result']['result_view']='agere_result_3'
        return [producer,count,document]

    def test_static_json_branches_preserve_every_row_and_reexecute_the_complete_consumer(self):
        calls=self.json_fixture();before=copy.deepcopy(calls)
        result=native_column_projections(calls,['agere_result_2','agere_result_3'],{'agere_result_2','agere_result_3','agere_result_4'})['agere_result_4']
        self.assertEqual(result['columns'],['patient_id','status'])
        self.assertEqual(result['rows'],[['A','unresolved'],['A','unresolved'],['B',None],['C','unknown']])
        branch=result['json_path_observations'][0]
        self.assertEqual(branch['path'],'$.contract.requirements')
        self.assertEqual(branch['value_encoding'],'canonical-json')
        self.assertEqual(branch['row_values'],[[True,'[{"id":"rule","binding":null}]'],[True,'[{"id":"rule","binding":null}]'],[True,'null'],[False,None]])
        self.assertTrue(result['json_path_output_checks'][0]['full_output_cells_types_order_duplicates_nulls_exact'])
        self.assertEqual(calls,before)

    def test_dynamic_paths_hidden_document_and_parse_position_fields_keep_full_inputs(self):
        for expression,path in [('r.json',"'$.contract.requirements'"),('r.id',"'$.contract.requirements'"),
                                ('r.parent',"'$.contract.requirements'"),('r.value',"'$.' || 'contract.requirements'"),
                                ('r.value',"'$.contract.requirements[0]'"),('x.d',"'$.contract.requirements'")]:
            # Each iteration owns an independent immutable native source.
            self.wh.db.set_authorizer(None);self.wh.db.execute('PRAGMA query_only=OFF')
            self.wh.db.execute('DROP TABLE IF EXISTS temp.agere_result_4')
            self.wh.db.execute('PRAGMA query_only=ON');self.wh.db.set_authorizer(self.wh._authorize)
            calls=self.json_fixture(expression,path)
            with self.subTest(expression=expression,path=path):
                self.assertNotIn('agere_result_4',native_column_projections(calls,['agere_result_2','agere_result_3'],{'agere_result_2','agere_result_3','agere_result_4'}))

    def test_json_projection_rejects_changed_outputs_and_malformed_unread_rows(self):
        calls=self.json_fixture();calls[-1]['result']['rows'][0]['rule_id']='changed'
        self.assertNotIn('agere_result_4',native_column_projections(calls,['agere_result_2','agere_result_3'],{'agere_result_2','agere_result_3','agere_result_4'}))
        self.wh.db.set_authorizer(None);self.wh.db.execute('PRAGMA query_only=OFF')
        self.wh.db.execute('DROP TABLE temp.agere_result_4')
        self.wh.db.execute('PRAGMA query_only=ON');self.wh.db.set_authorizer(self.wh._authorize)
        calls=self.json_fixture(malformed=True)
        self.assertNotIn('agere_result_4',native_column_projections(calls,['agere_result_2','agere_result_3'],{'agere_result_2','agere_result_3','agere_result_4'}))

    def test_a_raw_json_consumer_prevents_subfield_projection(self):
        calls=self.json_fixture();raw=self.execute('SELECT documentary_receipt FROM agere_result_4')
        raw['result']['result_view']='agere_result_5';calls.append(raw)
        self.assertNotIn('agere_result_4',native_column_projections(calls,['agere_result_2','agere_result_3','agere_result_5'],{'agere_result_2','agere_result_3','agere_result_4','agere_result_5'}))

    def test_json_cell_lookup_with_an_entity_filter_keeps_complete_source(self):
        calls=self.json_fixture()
        query=calls[-1]['input']['sql'].replace('FROM agere_result_4 LIMIT 1',"FROM agere_result_4 WHERE patient_id='B' LIMIT 1")
        filtered=self.execute(query);filtered['result']['result_view']='agere_result_3';calls[-1]=filtered
        self.assertNotIn('agere_result_4',native_column_projections(calls,['agere_result_2','agere_result_3'],{'agere_result_2','agere_result_3','agere_result_4'}))

    def test_complete_json_object_output_keeps_its_exact_native_string(self):
        calls=self.json_fixture('r.value AS complete_object')
        projected=native_column_projections(calls,['agere_result_2','agere_result_3'],{'agere_result_2','agere_result_3','agere_result_4'})['agere_result_4']
        self.assertTrue(projected['json_path_output_checks'][0]['full_output_cells_types_order_duplicates_nulls_exact'])

    def legacy_fixture(self):
        columns,rows=B.output_table(self.producer['result'])
        query='SELECT * FROM ('+native_result_source(columns,rows)+') x WHERE patient_id IN (SELECT value FROM json_each(\'["A","B"]\')) ORDER BY patient_id'
        legacy=self.execute(query);legacy['result']['result_view']='agere_result_3'
        legacy['result']['read_views']=[];legacy['result'].pop('native_read_columns',None)
        legacy['input']['sql']='SELECT * FROM agere_result_1 ORDER BY patient_id'
        cols,data=B.output_table(legacy['result']);B.define_result(self.wh,'agere_result_3',cols,data,receipt=legacy['result'])
        consumer=self.execute("SELECT COUNT(*) AS n FROM agere_result_3 WHERE status='unresolved'")
        return [self.producer,legacy,consumer]

    def legacy_projection(self,calls):
        return native_column_projections(calls,['agere_result_2'],{'agere_result_1','agere_result_2','agere_result_3'})

    def test_legacy_replay_derives_reads_without_replacing_original_receipts_or_scope(self):
        calls=self.legacy_fixture();before=copy.deepcopy(calls);projected=self.legacy_projection(calls)
        self.assertEqual(projected['agere_result_1']['rows'],[['A','unresolved'],['A','unresolved'],['B',None]])
        self.assertEqual(projected['agere_result_3']['rows'],[['A','unresolved'],['A','unresolved'],['B',None]])
        proof=projected['agere_result_1']['consumers'][0]['derived_read_proof']
        self.assertEqual(json.loads(next(iter(proof['parameters'].values()))),['A','B'])
        self.assertEqual(proof['selected_columns'],['patient_id','status'])
        self.assertTrue(proof['selected_cells_types_order_duplicates_nulls_exact'])
        self.assertEqual(proof['sources']['agere_result_1']['columns'],['patient_id','status'])
        self.assertEqual(proof['intrinsic_order_columns'],{'agere_result_1':['_rowid_']})
        self.assertEqual(calls,before)

    def test_legacy_replay_fails_closed_for_changed_source_output_scope_and_partial_receipts(self):
        original=self.legacy_fixture()
        for kind in ('source','output','scope','partial','contract','text_filter'):
            calls=copy.deepcopy(original)
            if kind=='source':calls[0]['result']['rows'][0][1]='changed'
            elif kind=='output':calls[1]['result']['rows'][0]['status']='changed'
            elif kind=='scope':calls[1]['result']['executed_query']=calls[1]['result']['executed_query'].replace('["A","B"]','["A"]')
            elif kind=='partial':calls[0]['result']['truncated']=True
            elif kind=='contract':calls[1]['result']['native_sql_contract']='unknown'
            else:calls[1]['result']['executed_query']=calls[1]['result']['executed_query'].replace('ORDER BY',"AND documentary_receipt LIKE '%complete%' ORDER BY")
            with self.subTest(kind=kind):self.assertNotIn('agere_result_1',self.legacy_projection(calls))

    def test_profile_projection_requires_the_received_snapshot_contract(self):
        calls=self.legacy_fixture();calls[0]['name']='read_patient_profiles'
        self.assertNotIn('agere_result_1',self.legacy_projection(calls))
        calls[0]['result'].update(kind='received_patient_profiles',source={'source_digest':'authored','as_of':'2030-05-27'})
        # New provenance requires a matching literal receipt and matching
        # downstream source map, rather than reusing the older execution.
        columns,rows=B.output_table(calls[0]['result'])
        sql='SELECT * FROM ('+native_result_source(columns,rows)+') x ORDER BY patient_id'
        calls[1]['result']['executed_query']=sql
        calls[2]['result']['native_read_columns']['sources']['agere_result_3']['receipt_digest']=digest({k:v for k,v in calls[1]['result'].items()if k!='result_view'})
        self.assertIn('agere_result_1',self.legacy_projection(calls))

    def test_legacy_join_keeps_join_keys_duplicate_multiplicity_filters_and_order(self):
        columns,rows=B.output_table(self.producer['result'])
        other={'name':'execute_analysis','input':{},'result':{'result_view':'agere_result_4',
            'columns':['patient_id','flag'],'rows':[['A',1],['A',2],['B',None]]}}
        query=('SELECT a.patient_id,a.status,a.documentary_receipt,b.flag FROM ('+native_result_source(columns,rows)+') a LEFT JOIN ('+native_result_source(other['result']['columns'],other['result']['rows'])+
            ') b ON b.patient_id=a.patient_id WHERE a.patient_id IN (SELECT value FROM json_each(\'["A","B"]\')) ORDER BY a.patient_id,b.flag')
        legacy=self.execute(query);legacy['result']['result_view']='agere_result_3';legacy['result']['read_views']=[]
        legacy['result'].pop('native_read_columns',None);legacy['input']['sql']='SELECT * FROM agere_result_1 a LEFT JOIN agere_result_4 b ON b.patient_id=a.patient_id'
        cols,data=B.output_table(legacy['result']);B.define_result(self.wh,'agere_result_3',cols,data,receipt=legacy['result'])
        consumer=self.execute("SELECT COUNT(*) AS n FROM agere_result_3 WHERE status='unresolved'")
        calls=[self.producer,other,legacy,consumer];before=copy.deepcopy(calls)
        result=native_column_projections(calls,['agere_result_2'],{'agere_result_1','agere_result_2','agere_result_3','agere_result_4'})
        self.assertEqual(result['agere_result_3']['rows'],[['A','unresolved']]*4+[['B',None]])
        proof=result['agere_result_1']['consumers'][0]['derived_read_proof']
        self.assertEqual(proof['sources']['agere_result_4']['columns'],['flag','patient_id'])
        self.assertEqual(calls,before)

    def test_profile_population_consumer_keeps_the_full_received_ids_and_other_read_columns(self):
        calls=self.legacy_fixture()
        profile={'name':'read_patient_profiles','input':{'patient_query_sql':'SELECT DISTINCT patient_id FROM agere_result_1'},
            'result':{'result_view':'agere_result_4','kind':'received_patient_profiles',
                'source':{'source_digest':'authored','as_of':'2030-05-27'},'input_patient_ids':['A','B'],
                'columns':['patient_id','patient_name'],'rows':[['A','Alpha'],['B','Beta']]}}
        calls.insert(1,profile)
        result=native_column_projections(calls,['agere_result_2'],{'agere_result_1','agere_result_2','agere_result_3','agere_result_4'})
        self.assertEqual(result['agere_result_1']['columns'],['patient_id','status'])
        proof=next(c['derived_read_proof']for c in result['agere_result_1']['consumers']if c['result_view']=='agere_result_4')
        self.assertEqual(proof['row_count'],2)
        for ids in (['A'],['B','A'],['A','A','B']):
            changed=copy.deepcopy(calls);changed[1]['result']['input_patient_ids']=ids
            self.assertNotIn('agere_result_1',native_column_projections(changed,['agere_result_2'],
                {'agere_result_1','agere_result_2','agere_result_3','agere_result_4'}))

    def global_note(self):
        self.answer['facts']['note']='These are machine-state receipts; clinical eligibility remains unestablished.'
        self.answer['global_prose_basis']=[{'path':['facts','note'],'basis':[{'result_view':'agere_result_2','row':0}]}]

    def test_ordinary_review_locates_missing_global_basis_before_building_source_packets(self):
        self.global_note();self.answer.pop('global_prose_basis');before=copy.deepcopy(self.calls)
        with patch.object(B,'support_packets',side_effect=AssertionError('Do not build broad context before locating the dependency')):
            review=B.review_final(SimpleNamespace(),'Count literal machine states.',self.answer,self.calls,
                SimpleNamespace(read={},document_reviews=[]),ledger=None,as_of='2030-05-27')
        self.assertEqual(review['status'],'repair');self.assertEqual(review['calls'],0)
        self.assertEqual(review['repair_kind'],'global_source_dependencies')
        self.assertEqual(review['progress_state']['unbound_global_fields'][0]['path'],['facts','note'])
        self.assertEqual(self.calls,before);self.assertIn('global_prose_basis',review['feedback'])

    def test_explicit_global_basis_preserves_all_rows_and_the_unmodified_statement(self):
        self.global_note();before=copy.deepcopy(self.calls);note=self.answer['facts']['note']
        B.bind_computed_prose(self.answer,self.calls,question='Count literal machine states.')
        self.assertEqual(B.global_dependency_request(self.answer,self.calls),[])
        packets=B.support_packets('Count literal machine states.',self.answer,self.calls,[],'2030-05-27')
        state=uncompact_json(json.loads(packets[0]['text']))
        self.assertEqual(state['answer']['facts']['note'],note)
        self.assertEqual(state['native_column_projections'][0]['rows'],[['A','unresolved'],['A','unresolved'],['B',None]])
        proof=next(p for p in self.answer['computed_prose']if p['field']=='facts.note')
        self.assertEqual(proof['global_basis_contract'],'executed-global-basis/1')
        self.assertEqual(self.calls,before)

    def test_located_basis_cannot_override_an_unsupported_clinical_meaning(self):
        self.global_note();self.answer['facts']['note']='Every patient qualifies for the clinical program.'
        B.bind_computed_prose(self.answer,self.calls,question='Review clinical eligibility.')
        packets=B.support_packets('Review clinical eligibility.',self.answer,self.calls,[],'2030-05-27')
        fault=next(c['id']for c in packets[0]['claim_manifest']if c['field']=='facts.note')
        def fields(subject,questions,*a,**kw):
            return {'judgments':{key:{'label':'not_established'if key==fault else'supported',
                'decided_by':'authored-source-review','probability':1.,'threshold':.8}for key in questions},'steps':[]}
        reviewed=B.check_support_packets(packets,lambda *a,**kw:{'label':'supported','decided_by':'authored-joint','steps':[]},
            field_decider=fields,as_of='2030-05-27',ledger=None)
        self.assertNotEqual(reviewed['label'],'supported')
        self.assertIn(['answer','facts','note'],[f['path']for f in reviewed['claim_feedback']])

    def test_bad_or_stale_global_basis_cannot_reuse_old_proofs(self):
        for kind in ('row','bool','unknown','duplicate','incomplete','source_free'):
            answer=copy.deepcopy(self.answer);calls=copy.deepcopy(self.calls)
            answer['facts']['note']='A retained machine observation.'
            address={'result_view':'agere_result_2','row':0}
            answer['global_prose_basis']=[{'path':['facts','note'],'basis':[address]}]
            if kind=='row':address['row']=1
            elif kind=='bool':address['row']=False
            elif kind=='unknown':address['result_view']='invented'
            elif kind=='duplicate':answer['global_prose_basis'].append(copy.deepcopy(answer['global_prose_basis'][0]))
            elif kind=='incomplete':calls[1]['result']['truncated']=True
            else:calls[1]['result']['executed_query']="SELECT 'invented support' AS n";calls[1]['input']['sql']=calls[1]['result']['executed_query']
            with self.subTest(kind=kind),self.assertRaises(ValueError):B.bind_computed_prose(answer,calls)

    def test_basis_never_waives_numeric_binding_or_hides_broader_source_rows(self):
        self.global_note();self.answer['facts']['note']='There are 999 eligible patients.'
        self.answer['table_from_tool']='agere_result_2';B.materialize_table(self.answer,self.calls)
        with self.assertRaisesRegex(ValueError,'unbound'):B.bind_computed_prose(self.answer,self.calls)
        self.answer['facts']['note']='The original documentary receipt remains relevant.'
        self.answer['global_prose_basis'][0]['basis']=[{'result_view':'agere_result_1','row':0}]
        B.bind_computed_prose(self.answer,self.calls)
        state=uncompact_json(json.loads(B.support_packets('Read the original receipts.',self.answer,self.calls,[],'2030-05-27')[0]['text']))
        self.assertNotIn('native_column_projections',state)
        self.assertIn('A complete source interpretation is still required.',json.dumps(state))


if __name__=='__main__':unittest.main()
