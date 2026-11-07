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


if __name__=='__main__':unittest.main()
