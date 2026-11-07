"""A native audit copy can shrink coverage; it cannot certify source meaning."""
import copy,json,unittest
from unittest.mock import patch
from clinical_core.agere.clinical import analysis_tools as A,backstop_agent as B,evidence_projection as E
from clinical_core.agere.clinical import harness_improvement as H,system_one_gate as G
from clinical_core.contracts import digest
from test_aggregate_explain import AS_OF
import test_documentary_tool_handoff as fixtures


class NativeAuditCoverage(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.DocumentaryHandoff();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.tools=self.fixture.tools()
        self.result=self.fixture.execute(self.tools,output='qualifier_accounts')
        self.answer={'status':'partial','answer_type':'table','table_from_tool':self.result['result_view'],
            'headline':'Received-label audit; clinical meaning remains unresolved.',
            'missing':['Whether the machine labels correctly interpret each original source.']}
        B.materialize_table(self.answer,self.tools.native_calls)

    def proof(self):return A.qualifier_coverage_review(self.answer,self.tools.native_calls,self.tools,AS_OF)

    def test_received_account_scope_survives_the_actual_graph_and_native_coverage(self):
        import sqlite3
        from clinical_core.agere.clinical import decision_context as D
        with sqlite3.connect(self.fixture.path/'semantic.sqlite3')as db:
            db.execute("INSERT INTO death(patient_id,death_date) VALUES('p04','2029-01-01')")
            db.execute("UPDATE patient SET data_segment='duplicate_chart' WHERE patient_id='p05'")
        question='Identify received 2029 clinical accounts whose interpretation changes when qualifiers are respected.'
        with D.scope(question,AS_OF):
            tools=self.fixture.tools()
            result=tools({'name':'execute_analysis','input':{'operation':'assertion_review',
                'parameters':{'streams':['financial','notes'],'concepts':['financial hardship'],
                    'assertion_window':{'basis':'arrival','start':'2029-01-01','end':AS_OF}},
                'output':'qualifier_accounts'}})
            answer={'answer_type':'table','table_from_tool':result['result_view']}
            B.materialize_table(answer,tools.native_calls)
            proof=A.qualifier_coverage_review(answer,tools.native_calls,tools,AS_OF)
        self.assertEqual(proof['population']['input_patients'],12)
        self.assertTrue({'p04','p05'}<={r['patient_id']for r in result['rows']})
        report=result['engine_result']['results'][-1]['result']
        self.assertEqual({p['patient_id']for p in report['review_members']},set(self.fixture.ids))

    def test_full_cached_native_output_gets_coverage_only_proof_and_exact_reuse(self):
        original=copy.deepcopy(self.answer)
        with patch.object(A,'documentary_rows',wraps=A.documentary_rows)as originals:
            first=self.proof();second=self.proof()
        self.assertEqual(originals.call_count,1);self.assertEqual(first,second)
        self.assertEqual(first['rows'],len(self.result['rows']));self.assertEqual(first['population']['input_patients'],12)
        self.assertEqual(first['original_patient_receipts']['count'],12)
        self.assertTrue(first['separate_source_support_required']);self.assertFalse(first['clinical_semantic_acceptance'])
        self.assertEqual(self.answer,original)
        view=E.coverage_answer(self.answer,native_audit=first)
        self.assertEqual(view['table'],[]);self.assertEqual(view['missing'],original['missing'])
        self.assertEqual(view['native_audit']['columns'],self.result['columns'])
        self.assertIn('faithful provisional audit',view['native_audit']['scope'])
        self.assertEqual(self.answer['table'],original['table'])

    def test_changed_omitted_duplicated_reordered_or_interpreted_cells_fail_closed(self):
        original=copy.deepcopy(self.answer)
        for kind in ('value','omit','duplicate','order','interpretation','type'):
            self.answer=copy.deepcopy(original)
            if kind=='value':self.answer['table'][0]['source_date']='2030-01-01'
            if kind=='omit':self.answer['table'].pop()
            if kind=='duplicate':self.answer['table'].append(self.answer['table'][0])
            if kind=='order':self.answer['table'].reverse()
            if kind=='interpretation':self.answer['table'][0]['clinical_reason']='Established clinical consequence.'
            if kind=='type':self.answer['table'][0]['current_clinical_state_established']=0
            with self.subTest(kind=kind),self.assertRaises(ValueError):self.proof()

    def test_mutated_native_origin_and_registered_fake_projection_cannot_get_proof(self):
        original=copy.deepcopy(self.result)
        for registered in (False,True):
            self.result.clear();self.result.update(copy.deepcopy(original))
            self.result['rows'][0]['current_clinical_state_established']=True
            if registered:self.tools.semantic.register_native_result(self.result['result_view'],self.result)
            B.materialize_table(self.answer,self.tools.native_calls)
            with self.subTest(registered=registered),self.assertRaises(ValueError):self.proof()

    def test_source_clock_population_engine_or_original_receipt_change_is_rejected(self):
        with self.assertRaises(ValueError):A.qualifier_coverage_review(self.answer,self.tools.native_calls,self.tools,'2030-05-27')
        original=copy.deepcopy(self.result)
        for kind in ('scope','engine','receipt'):
            self.result.clear();self.result.update(copy.deepcopy(original))
            if kind=='scope':self.result['input_patient_ids']=['p03']
            if kind=='engine':self.result['engine_result']['results'][-1]['result']['identity']='changed'
            if kind=='receipt':self.result['engine_result']['results'][-1]['result']['review_members'][0]['receipt_digest']='changed'
            self.result['receipt_digest']=digest(H.semantic_result(self.result['engine_result']))
            self.tools.semantic.register_native_result(self.result['result_view'],self.result)
            with self.subTest(kind=kind),self.assertRaises(ValueError):self.proof()

    def test_changed_inherited_scope_and_duplicate_origin_are_not_coverage_proofs(self):
        self.proof();self.tools.cohort=['p03']
        with self.assertRaises(ValueError):self.proof()
        self.tools.cohort=None
        with self.assertRaises(ValueError):A.qualifier_coverage_review(self.answer,self.tools.native_calls*2,self.tools,AS_OF)

    def test_coverage_cannot_bind_another_table_or_claim_source_semantics_passed(self):
        proof=self.proof()
        for key,value in [('table_digest','changed'),('result_view','another'),
            ('clinical_semantic_acceptance',True),('separate_source_support_required',False)]:
            changed=copy.deepcopy(proof);changed[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):E.coverage_answer(self.answer,native_audit=changed)

    def test_source_support_still_receives_every_original_patient_row(self):
        proof=self.proof();self.assertIsNotNone(proof)
        packets=B.support_packets('Inspect source meanings',self.answer,self.tools.native_calls,[],AS_OF)
        from clinical_core.agere.deliverables.receipts import uncompact_json
        patients={p['id']:uncompact_json(json.loads(p['text']))for p in packets if p['id'].startswith('patient_')}
        for pid in {r['patient_id']for r in self.answer['table']}:
            expected=[r for r in self.answer['table']if r['patient_id']==pid]
            self.assertEqual(patients['patient_'+pid]['answer_rows'],expected)
        self.assertGreater(len(packets),1)

    def test_audit_global_contract_keeps_every_native_patient_value_and_requires_context(self):
        from clinical_core.agere.deliverables.receipts import uncompact_json
        proof=self.proof();before=copy.deepcopy(self.answer)
        original=B.support_packets('Inspect source meanings',self.answer,self.tools.native_calls,[],AS_OF)
        qualified=B.support_packets('Inspect source meanings',self.answer,self.tools.native_calls,[],AS_OF,native_audit=proof)
        old={p['id']:p for p in original};new={p['id']:p for p in qualified}
        self.assertEqual(set(old),set(new));self.assertEqual(self.answer,before)
        for packet in old:
            if packet!='summary':
                prior=uncompact_json(json.loads(old[packet]['text']))
                current=uncompact_json(json.loads(new[packet]['text']))
                for key,value in prior.items():
                    if key not in ('source_contexts','source_context_gaps'):self.assertEqual(current[key],value)
                if current['source_context_gaps']:self.assertFalse(new[packet]['source_context_complete'])
        summary=uncompact_json(json.loads(new['summary']['text']))
        self.assertEqual(summary['all_final_rows'],[])
        self.assertEqual(summary['required_clinical_dependencies'],[])
        self.assertEqual(summary['native_audit']['table_digest'],digest(self.answer['table']))
        self.assertFalse(summary['native_audit']['clinical_semantic_acceptance'])
        self.assertTrue(summary['native_audit']['separate_source_support_required'])
        manifest=summary['required_patient_packets']
        self.assertEqual(manifest['schema'],'native-audit-patient-packets/1')
        entries=[dict(zip(manifest['columns'],row))for row in manifest['rows']]
        for item in entries:
            rows=uncompact_json(json.loads(new[manifest['packet_id_prefix']+item['patient_id']]['text']))['answer_rows']
            self.assertEqual(item['rows'],len(rows));self.assertEqual(item['table_digest'],digest(rows))
        self.assertEqual({manifest['packet_id_prefix']+x['patient_id']for x in entries},set(new)-{'summary'})

    def received_contexts(self,proof):
        required=B.audit_source_requirements(self.answer,self.tools.native_calls,proof)
        return {(pid,rid):self.tools._source_record(pid,rid)for pid,records in required.items()for rid in records}

    def test_complete_native_record_contexts_preserve_all_structured_and_narrative_fields(self):
        from clinical_core.agere.deliverables.receipts import uncompact_json
        proof=self.proof();records=self.received_contexts(proof)
        packets=B.support_packets('Interpret the original sources',self.answer,self.tools.native_calls,[],AS_OF,
                                  native_audit=proof,source_records=records)
        supplied={}
        for packet in packets:
            if packet['id']=='summary':continue
            state=uncompact_json(json.loads(packet['text']))
            self.assertTrue(packet['source_context_complete']);self.assertEqual(state['source_context_gaps'],[])
            for context in state['source_contexts']:
                self.assertEqual(context['schema'],'native-received-record-context/1')
                key=(context['patient_id'],context['record_id']);supplied[key]=context
                self.assertEqual(context['record_hash'],digest(records[key]))
                if 'difficulty_paying_medical_bills'in records[key]:
                    self.assertEqual(context['structured_fields']['difficulty_paying_medical_bills'],records[key]['difficulty_paying_medical_bills'])
                if 'text'in records[key]:
                    self.assertIn({'field':'text','text':records[key]['text']},context['received_text_fields'])
        self.assertEqual(set(supplied),set(records))
        self.assertTrue(any('difficulty_paying_medical_bills'in row['structured_fields']for row in supplied.values()))
        self.assertTrue(any(row['received_text_fields']for row in supplied.values()))
        self.assertFalse(proof['clinical_semantic_acceptance'])

    def test_record_context_uses_existing_forbidden_field_and_received_ocr_policy(self):
        record={'patient_id':'authored','record_id':'doc','source_table':'documents',
            'event_date':'2029-01-01','available_date':'2029-01-02','synthetic':True,
            'original_text':'AUTHORED_FORBIDDEN_PRE_OCR','text':'AUTHORED_UNRECEIVED_TEXT',
            'truth':{'diagnosis':'AUTHORED_FORBIDDEN_TRUTH'},'nested':{'original_text':'AUTHORED_FORBIDDEN_NESTED'},
            'extracted_text':'Received OCR observation; interpretation remains unknown.'}
        source={k:record[k]for k in ('patient_id','record_id','source_table')};source['record_hash']=digest(record)
        context=B.received_record_context(record,source,AS_OF);wire=json.dumps(context)
        self.assertNotIn('AUTHORED_FORBIDDEN',wire);self.assertNotIn('AUTHORED_UNRECEIVED_TEXT',wire)
        self.assertEqual(context['received_text_fields'],[{'field':'extracted_text','text':record['extracted_text']}])
        self.assertEqual(context['record_hash'],digest(record))
        self.assertFalse(any(k in context['structured_fields']for k in ('truth','original_text','text')))

    def test_missing_changed_foreign_or_retimed_context_cannot_buy_a_source_vote(self):
        proof=self.proof();original=self.received_contexts(proof);key=next(iter(original))
        for change in ('missing','value','patient','clock'):
            records=copy.deepcopy(original)
            if change=='missing':records.pop(key)
            elif change=='value':records[key]['invented_value']='A new unreceived source field'
            elif change=='patient':records[key]['patient_id']='another-patient'
            else:records[key]['event_date']='2030-01-01'
            packets=B.support_packets('Interpret original sources',self.answer,self.tools.native_calls,[],AS_OF,
                                      native_audit=proof,source_records=records)
            packet=next(p for p in packets if p.get('source_dependency_patient_id')==key[0])
            self.assertFalse(packet['source_context_complete'])
            with self.subTest(change=change):
                result=B.check_support_packets([packet],lambda *a,**k:(_ for _ in ()).throw(AssertionError('No model can replace missing context')),
                                               as_of=AS_OF,ledger=None)
                self.assertIsNone(result.get('decided_by'));self.assertNotEqual(result['label'],'supported')

    def test_received_record_reader_keeps_inherited_patient_and_snapshot_scope(self):
        proof=self.proof();records=self.received_contexts(proof);pid,rid=next(iter(records))
        with self.assertRaises(ValueError):self.tools._source_record('another-patient',rid)
        scoped=self.fixture.tools(cohort=[p for p in self.fixture.ids if p!=pid])
        with self.assertRaises(ValueError):scoped._source_record(pid,rid)
        import sqlite3
        with sqlite3.connect(self.fixture.path/'semantic.sqlite3')as db:
            db.execute('DELETE FROM record WHERE patient_id=? AND record_id=?',(pid,rid))
        with self.assertRaises(ValueError):self.tools._source_record(pid,rid)
        self.assertNotIn((pid,rid),self.tools.source_records)

    def test_received_future_plan_is_visible_but_future_arrival_is_not(self):
        import sqlite3
        proof=self.proof();records=self.received_contexts(proof);pid,rid=next(iter(records))
        raw=copy.deepcopy(records[(pid,rid)]);raw['event_date']='2031-01-01'
        with sqlite3.connect(self.fixture.path/'semantic.sqlite3')as db:
            db.execute('UPDATE record SET payload=?WHERE patient_id=?AND record_id=?',(json.dumps(raw),pid,rid))
        self.assertEqual(self.tools._source_record(pid,rid),raw)
        raw['available_date']='2031-01-02'
        with sqlite3.connect(self.fixture.path/'semantic.sqlite3')as db:
            db.execute('UPDATE record SET payload=?WHERE patient_id=?AND record_id=?',(json.dumps(raw),pid,rid))
        with self.assertRaises(ValueError):self.tools._source_record(pid,rid)
        self.assertNotIn((pid,rid),self.tools.source_records)

    def test_ordinary_source_review_acquires_exact_records_before_the_support_boundary(self):
        from clinical_core.agere.deliverables.receipts import uncompact_json
        seen=[]
        def support(packets,*args,**kwargs):
            seen.extend(packets)
            for packet in packets:
                if packet['id']=='summary':continue
                state=uncompact_json(json.loads(packet['text']))
                self.assertTrue(packet['source_context_complete'])
                self.assertTrue(state['source_contexts']);self.assertEqual(state['source_context_gaps'],[])
            return {'label':'unknown','decided_by':None,'steps':[],'packets':[],
                    'all_dependencies_checked':False,'scope':'Authored boundary observation; no semantic judgment.'}
        # Script coverage/work solely to reach the changed source boundary.
        # Neither these callbacks nor record acquisition validate the answer.
        with patch.object(G,'ready',return_value=True),\
             patch.object(G,'check',return_value={'label':'answers_fully','decided_by':'authored-routing','calls':0,'cost_usd':0.}),\
             patch.object(G,'check_pending_work',return_value={'targets':[],'calls':0,'cost_usd':0.}),\
             patch.object(B,'check_support_packets',side_effect=support):
            review=B.review_final(self.fixture.stack,'Interpret source-bound assertions',self.answer,
                self.tools.native_calls,self.tools,ledger=None,as_of=AS_OF)
        self.assertGreater(len(seen),1);self.assertTrue(self.tools.source_records)
        self.assertNotEqual(review['status'],'accepted')

    def test_audit_support_contract_rejects_stale_forged_or_interpreted_tables(self):
        proof=self.proof()
        for key,value in [('table_digest','changed'),('registered_origin_digest','changed'),
                          ('clinical_semantic_acceptance',True),('separate_source_support_required',False)]:
            changed=copy.deepcopy(proof);changed[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):
                B.support_packets('Inspect source meanings',self.answer,self.tools.native_calls,[],AS_OF,native_audit=changed)
        self.answer['table_binding']['interpreted_columns']=['explanation']
        with self.assertRaises(ValueError):
            B.support_packets('Inspect source meanings',self.answer,self.tools.native_calls,[],AS_OF,native_audit=proof)

    def test_final_review_rejects_changed_origin_before_any_provider_check(self):
        self.result['rows'][0]['polarity']='invented'
        with patch('clinical_core.agere.clinical.system_one_gate.check',side_effect=AssertionError('No paid review of a changed origin')):
            review=B.review_final(self.fixture.stack,'Audit accounts',self.answer,self.tools.native_calls,self.tools,ledger=None,as_of=AS_OF)
        self.assertEqual(review['repair_kind'],'native_audit_binding');self.assertEqual(review['calls'],0)

    def test_ordinary_review_uses_coverage_contract_and_retains_partial_meaning(self):
        seen=[]
        def coverage(question,graph,evidence,**kwargs):
            seen.append(evidence['answer'])
            return {'label':'answers_partially','decided_by':'authored-coverage-review','p':1.,'calls':1,'cost_usd':0.}
        with patch.object(G,'ready',return_value=True),patch.object(G,'check',side_effect=coverage),\
             patch.object(G,'check_pending_work',return_value={'targets':[],'calls':0,'cost_usd':0.}),\
             patch.object(B,'check_support_packets',side_effect=AssertionError('Partial coverage must finish composition before full source dispatch')):
            review=B.review_final(self.fixture.stack,'Identify qualifier-sensitive accounts and their clinical consequences.',
                self.answer,self.tools.native_calls,self.tools,ledger=None,as_of=AS_OF)
        self.assertEqual(len(seen),1);self.assertEqual(seen[0]['table'],[])
        self.assertFalse(seen[0]['native_audit']['clinical_semantic_acceptance'])
        self.assertEqual(len(self.answer['table']),len(self.result['rows']))
        self.assertEqual(review['status'],'repair');self.assertEqual(review['repair_kind'],'incomplete_coverage')
        self.assertEqual(review['support']['label'],'not_run')

    def test_compiled_row_interpretations_remain_visible_to_coverage(self):
        root=self.result['result_view']
        self.answer['row_prose']={'keys':['audit_account_id'],'rows':[
            {'key':{'audit_account_id':row['audit_account_id']},'basis':[{'result_view':root,'row':i}],
             'fields':{'explanation':'Clinical meaning is not established by the received label.'}}
            for i,row in enumerate(self.result['rows'])]}
        self.answer.pop('missing');seen=[]
        def coverage(question,graph,evidence,**kwargs):
            seen.append(evidence['answer'])
            return {'label':'answers_partially','decided_by':'authored-review','p':1.,'calls':1,'cost_usd':0.}
        with patch.object(G,'ready',return_value=True),patch.object(G,'check',side_effect=coverage):
            review=B.review_final(self.fixture.stack,'Interpret every account.',self.answer,self.tools.native_calls,
                self.tools,ledger=None,as_of=AS_OF)
        self.assertEqual(review['repair_kind'],'incomplete_coverage')
        self.assertNotIn('native_audit',seen[0]);self.assertEqual(len(seen[0]['table']),len(self.result['rows']))
        self.assertTrue(all(r['explanation']=='Clinical meaning is not established by the received label.'for r in seen[0]['table']))

    def test_undecided_native_audit_coverage_retains_partial_without_source_fanout(self):
        original=copy.deepcopy(self.answer['table']);self.answer.pop('missing')
        verdict={'label':'answers_partially','decided_by':None,'p':.68,'calls':2,'cost_usd':.00035}
        with patch.object(G,'ready',return_value=True),patch.object(G,'check',return_value=verdict),\
             patch.object(B,'support_packets',side_effect=AssertionError('No full source campaign for an unlocated coverage uncertainty')):
            review=B.review_final(self.fixture.stack,'Interpret source accounts.',self.answer,self.tools.native_calls,
                self.tools,ledger=None,as_of=AS_OF)
        self.assertEqual(review['status'],'unavailable');self.assertEqual(review['calls'],2)
        self.assertEqual(review['retry_disposition'],'retain_partial_until_new_evidence')
        self.assertFalse(review['support']['all_dependencies_checked']);self.assertEqual(review['support']['label'],'not_run')
        self.assertEqual(self.answer['table'],original)

    def test_unqualified_native_sql_audit_projection_is_repaired_before_paid_coverage(self):
        root=self.result['result_view']
        projected=self.tools({'name':'query_sql','input':{'sql':f'SELECT *,LENGTH(concept)AS new_interpretation FROM {root}'}})
        self.answer['table_from_tool']=projected['result_view']
        with patch.object(G,'check',side_effect=AssertionError('No large general-review fallback')):
            review=B.review_final(self.fixture.stack,'Show all source accounts.',self.answer,self.tools.native_calls,
                self.tools,ledger=None,as_of=AS_OF)
        self.assertEqual(review['repair_kind'],'native_audit_binding');self.assertEqual(review['calls'],0)
        self.assertIn('requires its own contract',review['feedback'])
        self.assertEqual(len(self.answer['table']),len(self.result['rows']))

    def project(self,sql):
        result=self.tools({'name':'query_sql','input':{'sql':sql}})
        self.answer['table_from_tool']=result['result_view'];B.materialize_table(self.answer,self.tools.native_calls)
        return result

    def test_complete_sql_copy_and_row_order_are_bound_without_source_engine_replay(self):
        root=self.result['result_view'];projected=self.project(f'SELECT * FROM {root} ORDER BY patient_id DESC,audit_account_id')
        original=copy.deepcopy(self.answer);n=len(self.tools.native_calls)
        with patch.object(A,'documentary_rows',wraps=A.documentary_rows)as receipts:
            first=self.proof();second=self.proof()
        self.assertEqual(first,second);self.assertEqual(receipts.call_count,1);self.assertEqual(len(self.tools.native_calls),n)
        self.assertEqual(first['rows'],len(projected['rows']));self.assertEqual(self.answer,original)
        self.assertTrue(first['native_projection']['all_output_cells_replayed_exactly'])
        self.assertFalse(first['clinical_semantic_acceptance']);self.assertTrue(first['separate_source_support_required'])
        self.assertEqual(E.coverage_answer(self.answer,native_audit=first)['table'],[])

    def test_received_quote_and_conditional_labels_are_explicit_unverified_computations(self):
        root=self.result['result_view']
        self.project(f"SELECT audit_account_id,patient_id,source_record_id,source_date,value,unit,value_domain,"
            "polarity,subject,temporality,current_clinical_state_established,json_extract(evidence,'$.quote')AS source_quote,"
            f"CASE WHEN polarity='negated'THEN'conditional negative account'ELSE'unresolved'END AS account_role FROM {root}")
        proof=self.proof();projection=proof['native_projection']
        self.assertEqual(projection['fields']['source_quote']['kind'],'received_json_field')
        self.assertEqual(projection['fields']['account_role']['kind'],'conditional_received_label')
        self.assertEqual(sum(v['accounts']for v in projection['computed_literal_outcomes']['account_role']),len(self.answer['table']))
        self.assertTrue(projection['all_omitted_parent_evidence_retained']);self.assertIn('reference_date',projection['parent_columns'])
        self.assertIn('not source observations',projection['meaning']);self.assertFalse(proof['clinical_semantic_acceptance'])

    def test_clock_role_swaps_clinical_text_conditions_and_query_shape_fail_closed(self):
        root=self.result['result_view']
        queries=[f'SELECT audit_account_id,patient_id,arrival_date AS source_date FROM {root}',
            f"SELECT *,CASE WHEN concept LIKE '%hardship%'THEN'clinical fact'ELSE'other'END AS diagnosis FROM {root}",
            f'SELECT * FROM {root} WHERE 1=1',
            f'SELECT a.* FROM {root} a JOIN {root} b ON a.audit_account_id=b.audit_account_id',
            f'SELECT *,ROW_NUMBER()OVER(ORDER BY patient_id)AS ordinal FROM {root}']
        for sql in queries:
            self.project(sql)
            with self.subTest(sql=sql),self.assertRaises(ValueError):self.proof()

    def test_registered_forged_projection_and_changed_native_parent_are_rejected(self):
        root=self.result['result_view'];projected=self.project(f"SELECT *,CASE WHEN polarity='negated'THEN'negative'ELSE'other'END AS role FROM {root}")
        projected['rows'][0]['role']='invented'
        self.tools.semantic.register_native_result(projected['result_view'],projected)
        B.materialize_table(self.answer,self.tools.native_calls)
        with self.assertRaisesRegex(ValueError,'projection replay differs'):self.proof()
        self.result['rows'][0]['source_date']='2030-01-01'
        with self.assertRaisesRegex(ValueError,'origins changed'):self.proof()

    def test_missing_bound_sql_receipt_and_renamed_identity_cannot_qualify(self):
        root=self.result['result_view'];projected=self.project(f'SELECT * FROM {root}')
        projected.pop('native_query_receipt');self.tools.semantic.register_native_result(projected['result_view'],projected)
        with self.assertRaises(ValueError):self.proof()
        self.project(f'SELECT patient_id AS audit_account_id,audit_account_id AS patient_id FROM {root}')
        with self.assertRaises(ValueError):self.proof()

    def structural_candidate(self):
        self.tools=self.fixture.tools()
        self.result=self.fixture.execute(self.tools,output='qualifier_accounts',parameters={
            'streams':['financial','notes'],'concepts':['financial hardship'],
            'assertion_window':{'basis':'source_event','start':'2029-01-01','end':AS_OF}})
        self.answer={'status':'partial','answer_type':'table','table_from_tool':self.result['result_view']}
        root=self.result['result_view']
        self.project(f"SELECT *,CASE WHEN polarity='negated'THEN'negative'ELSE'other'END AS role_a,"
            f"CASE WHEN subject='patient'THEN'patient label'ELSE'unknown'END AS role_b,"
            f"CASE WHEN temporality='current'THEN'current label'ELSE'unknown'END AS role_c FROM {root}")
        proof=self.proof()
        total=self.tools({'name':'query_sql','input':{'sql':f"SELECT COUNT(*)AS accounts FROM {self.answer['table_from_tool']}"}})
        self.answer['headline']={'template':'On the 2029 source-event clock, {n} accounts are retained.',
            'bindings':{'n':{'result_view':total['result_view'],'row':0,'column':'accounts'}}}
        self.answer['facts']={'table_contents':'The table has one row per native audit account. Three derived columns are added: role_a, role_b and role_c.'}
        return proof

    def test_method_year_unique_grain_and_named_fields_use_executed_structural_witnesses(self):
        proof=self.structural_candidate();original=self.answer['facts']['table_contents']
        B.bind_computed_prose(self.answer,self.tools.native_calls,question='Inspect 2029 accounts.',native_structure=proof)
        self.assertEqual(self.answer['facts']['table_contents'],original)
        kinds={p['kind']for f in self.answer['computed_prose']for p in f.get('parameters',[])}
        self.assertTrue({'executed_window_year','verified_native_key_grain','executed_named_output_field_count'}<=kinds)
        self.assertIn('2029 source-event clock',self.answer['headline'])

    def test_wrong_window_clock_grain_field_count_and_observed_quantities_still_bind(self):
        proof=self.structural_candidate();original=copy.deepcopy(self.answer)
        cases=[('headline','2029 source-event','2028 source-event'),('headline','source-event','arrival'),
            ('facts','one row per native audit account','one row per patient'),
            ('facts','Three derived','Four derived'),('facts','role_b','missing_field'),
            ('facts','role_b','role_a')]
        for field,old,new in cases:
            self.answer=copy.deepcopy(original)
            if field=='headline':self.answer['headline']['template']=self.answer['headline']['template'].replace(old,new)
            else:self.answer['facts']['table_contents']=self.answer['facts']['table_contents'].replace(old,new)
            with self.subTest(case=(field,old,new)),self.assertRaises(ValueError):
                B.bind_computed_prose(self.answer,self.tools.native_calls,question='Inspect 2029 accounts.',native_structure=proof)
        for observation in ('Three patients have the condition.','A dose of 2029 mg was observed.','One visit was completed.'):
            self.answer=copy.deepcopy(original);self.answer['facts']['observation']=observation
            with self.subTest(observation=observation),self.assertRaises(ValueError):
                B.bind_computed_prose(self.answer,self.tools.native_calls,question='Inspect 2029 accounts.',native_structure=proof)

    def test_structural_proof_cannot_be_forged_or_used_without_current_native_origin(self):
        proof=self.structural_candidate()
        for changed in (None,{**proof,'table_digest':'changed'},{**proof,'registered_origin_digest':'changed'}):
            with self.subTest(proof=changed is None),self.assertRaises(ValueError):
                B.bind_computed_prose(copy.deepcopy(self.answer),self.tools.native_calls,question='Inspect 2029 accounts.',native_structure=changed)


if __name__=='__main__':unittest.main()
