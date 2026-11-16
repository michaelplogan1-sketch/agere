"""Authored workflow checks; no clinical labels or live providers."""
import copy,json,unittest
from unittest.mock import patch

from clinical_core.agere.clinical import assertion_source_review as R,backstop_agent as B,system_one
from clinical_core.agere.clinical import cost_telemetry as T
from clinical_core.contracts import digest
import test_native_audit_coverage as fixtures


def judgment(questions,label='supported',probability=1.,decided_by='authored-judge'):
    return {'judgments':{key:{'label':label,'probabilities':{label:probability},'probability':probability,
                             'threshold':.8,'decided_by':decided_by,'provider':'authored-judge'}for key in questions},
            'steps':[{'provider':'authored-judge','status':'answered','cost_usd':.001,'request_hash':'authored-call'}]}


class AssertionReview(unittest.TestCase):
    def setUp(self):
        self.audit=fixtures.NativeAuditCoverage();self.audit.setUp();self.addCleanup(self.audit.doCleanups)
        self.tools=self.audit.tools;self.view=self.audit.result['result_view']

    def call(self):return self.tools({'name':'review_documents','input':{'assertion_result_view':self.view}})

    def test_complete_tuple_source_review_is_an_executable_sql_result_without_changing_annotations(self):
        original=copy.deepcopy(self.audit.result)
        def judge(subject,questions,evidence,**kw):
            state=json.loads(evidence[0]['text']);self.assertEqual(kw['as_of'],self.tools.as_of)
            self.assertEqual(set(questions),set(state['proposed_assertions']))
            for item in state['proposed_assertions'].values():
                self.assertEqual(set(item['proposal']),set(R.FIELDS))
                original_row=next(r for r in original['rows']if r['audit_account_id']==item['audit_account_id'])
                self.assertEqual(item['source_evidence'],original_row['evidence'])
                source=state['source_records'][item['source_key']]
                self.assertEqual(source['patient_id'],item['patient_id'])
                self.assertEqual(source['record_hash'],digest(self.tools.source_records[(item['patient_id'],source['record_id'])]))
            return judgment(questions)
        with patch.object(system_one,'judge_fields',side_effect=judge):out=self.call()
        self.assertEqual(self.audit.result,original)
        self.assertEqual([r['audit_account_id']for r in out['rows']],[r['audit_account_id']for r in original['rows']])
        self.assertTrue(any(r['review_performed']for r in out['rows']))
        self.assertTrue(all(r['source_support']=='not_reviewed'for r in out['rows']if r['source_record_id']is None))
        self.assertFalse(out['assertion_source_review']['clinical_qualification'])
        sql=f'SELECT a.audit_account_id,a.patient_id,a.subject,r.source_support,r.proposed_assertion FROM {self.view} a JOIN {out["result_view"]} r USING(audit_account_id)'
        table=self.tools({'name':'query_sql','input':{'sql':sql}})
        self.assertEqual(len(table['rows']),len(original['rows']))
        self.assertEqual([r['subject']for r in table['rows']],[r['subject']for r in original['rows']])
        rejected=next(r for r in original['rows']if r['subject']=='family')
        self.assertEqual(next(r for r in table['rows']if r['audit_account_id']==rejected['audit_account_id'])['subject'],'family')

    def test_uncertain_or_undecided_rejection_cannot_become_support(self):
        for label,probability,deciding in [('unknown',1.,None),('not_supported',.79,None),('supported',.99,None),('supported',.79,'judge')]:
            with self.subTest(label=label,probability=probability,deciding=deciding):
                with patch.object(system_one,'judge_fields',side_effect=lambda s,q,e,**kw:judgment(q,label,probability,deciding)):
                    out=self.call()
                self.assertTrue(all(r['source_support']=='unknown'for r in out['rows']if r['review_performed']))

    def test_rejected_tuple_stays_rejected_and_does_not_rewrite_a_single_axis(self):
        with patch.object(system_one,'judge_fields',side_effect=lambda s,q,e,**kw:judgment(q,'not_supported')):out=self.call()
        for row in out['rows']:
            old=next(r for r in self.audit.result['rows']if r['audit_account_id']==row['audit_account_id'])
            self.assertEqual(row['proposed_assertion'],{f:old.get(f)for f in R.FIELDS})
            if row['review_performed']:self.assertEqual(row['source_support'],'not_supported')
            self.assertFalse(row['current_clinical_state_established'])

    def test_changed_missing_or_cross_patient_sources_never_dispatch_their_tuples(self):
        for mutation in ('changed','missing','cross_patient'):
            original=self.tools._source_record
            def read(pid,rid):
                if mutation=='missing':raise ValueError('Authored unavailable source')
                raw=copy.deepcopy(original(pid,rid))
                if mutation=='changed':raw['unreceived']='changed'
                else:raw['patient_id']='different-patient'
                return raw
            with self.subTest(mutation=mutation),patch.object(self.tools,'_source_record',side_effect=read),patch.object(system_one,'judge_fields')as judge:
                out=self.call();judge.assert_not_called()
                self.assertTrue(all(r['source_support']=='not_reviewed'for r in out['rows']))
                self.assertTrue(all(r['review_gap']for r in out['rows']))

    def test_input_scope_overrides_and_unqualified_views_fail_before_provider(self):
        with patch.object(system_one,'judge_fields')as judge:
            for args in ({'assertion_result_view':self.view,'patient_ids':['p06']},{'assertion_result_view':'missing'}):
                with self.assertRaises(ValueError):self.tools({'name':'review_documents','input':args})
            judge.assert_not_called()

    def test_native_projection_cannot_omit_semantics_from_review(self):
        self.audit.project('SELECT audit_account_id,patient_id,subject FROM '+self.view)
        prepared=R.prepare(self.tools,self.audit.answer['table_from_tool'])
        self.assertEqual(len(prepared['rows']),len(self.audit.result['rows']))
        self.assertTrue(all(set(r['proposed_assertion'])==set(R.FIELDS)for r in prepared['rows']))

    def test_complete_record_batches_retain_every_source_once_even_when_oversized(self):
        prepared=R.prepare(self.tools,self.view)
        with patch.object(R,'BATCH_BYTES',1):parts=list(R.batches(prepared['items'],prepared['sources']))
        self.assertEqual(sum(len(s['proposed_assertions'])for s,q in parts),len(prepared['items']))
        keys=[k for s,q in parts for k in s['source_records']]
        self.assertEqual(len(keys),len(set(keys)));self.assertEqual(set(keys),set(prepared['sources']))
        for state,questions in parts:
            for key,value in state['source_records'].items():self.assertEqual(value,prepared['sources'][key])
            for item in state['proposed_assertions'].values():
                self.assertIn('ONLY',state['review_task'])
                self.assertIn(item['source_key'],str(questions[item['audit_account_id']]))

    def test_worker_owns_question_without_sharing_ledger_connection(self):
        token=T.CURRENT.set('authored-tuple-review-question');self.addCleanup(T.CURRENT.reset,token)
        seen=[]
        def judge(s,q,e,**kw):
            seen.append((T.CURRENT.get(),kw['ledger']));return judgment(q)
        with patch.object(system_one,'judge_fields',side_effect=judge):self.call()
        self.assertTrue(seen)
        self.assertTrue(all(owner=='authored-tuple-review-question'and isinstance(path,T.QuestionLedgerPath)and
                            path.question_id==owner for owner,path in seen))

    def test_prepared_work_is_not_reported_as_completed_source_review(self):
        with patch.object(system_one,'judge_fields',return_value={'judgments':{},'steps':[{'status':'prepared','provider':'jev','cost_usd':0.}]}):out=self.call()
        for row in out['rows']:
            self.assertFalse(row['review_performed']);self.assertTrue(row['review_gap'])

    def source_packets(self,label='supported'):
        with patch.object(system_one,'judge_fields',side_effect=lambda s,q,e,**kw:judgment(q,label)):out=self.call()
        proof=self.audit.proof()
        observations=R.qualified_observations(self.tools,self.audit.answer,self.tools.native_calls,proof)
        records=self.audit.received_contexts(proof)
        packets=B.support_packets('Inspect source meanings',self.audit.answer,self.tools.native_calls,[],self.tools.as_of,
            native_audit=proof,source_records=records,assertion_observations=observations)
        return out,observations,packets

    def test_final_field_review_consumes_tuple_judgments_with_model_authority_without_reasking(self):
        out,observations,packets=self.source_packets();self.assertTrue(observations)
        from clinical_core.agere.deliverables.receipts import uncompact_json
        reused=0;asked=[]
        def unknown(s,q,e,**kw):
            asked.extend(v['instructions']['claim']for v in q.values());return judgment(q,'unknown',1.,None)
        for packet in packets:
            if not packet.get('native_audit_fields'):continue
            localized=B.localize_support_failure(packet,unknown,as_of=self.tools.as_of,ledger=None)['field_review']
            reused+=localized['source_tuple_supported_count']
            state=uncompact_json(json.loads(packet['text']))
            for claim in packet['claim_manifest']:
                observed=B.source_tuple_field_observation(packet,claim,state)
                if observed:
                    self.assertNotIn('native_provenance',observed)
                    self.assertEqual(observed['source_tuple_support']['observation']['authority'],'machine_source_observation')
                    self.assertFalse(observed['clinical_qualification'])
                    self.assertNotIn(claim,asked)
        self.assertGreater(reused,0);self.assertFalse(asked)
        self.assertTrue(all(c['field']not in R.FIELDS for c in asked))

    def test_rejected_joint_tuple_cannot_support_any_constituent_field(self):
        out,observations,packets=self.source_packets('not_supported');self.assertEqual(observations,{})
        from clinical_core.agere.deliverables.receipts import uncompact_json
        for packet in packets:
            state=uncompact_json(json.loads(packet['text']))
            for claim in packet['claim_manifest']:
                self.assertIsNone(B.source_tuple_field_observation(packet,claim,state))

    def test_changed_registered_judgment_or_input_binding_is_rejected(self):
        out,observations,packets=self.source_packets();proof=self.audit.proof()
        obs=next(r['machine_source_review']for r in out['rows']if r['machine_source_review'])
        obs['judgment']={**obs['judgment'],'probabilities':{'supported':.999},'probability':.999}
        # Re-registering a mutated object cannot manufacture a typed observation.
        self.tools.semantic.register_native_result(out['result_view'],out)
        with self.assertRaisesRegex(ValueError,'judgment changed'):
            R.qualified_observations(self.tools,self.audit.answer,self.tools.native_calls,proof)

    def test_complete_composition_rechecks_bound_tuple_observations_and_source_changes(self):
        out,observations,packets=self.source_packets()
        from clinical_core.agere.deliverables.receipts import uncompact_json
        packet=next(p for p in packets if p.get('native_audit_fields',{}).get('tuple_observations'))
        def positive(s,q,e,**kw):return judgment(q)
        review=B.localize_support_failure(packet,positive,as_of=self.tools.as_of,ledger=None)['field_review']
        self.assertIsNotNone(B.complete_claim_composition(packet,review))
        row=next(r for r in review['supported_claims']if r['verification_observation'].get('source_tuple_support'))
        row['verification_observation']['source_tuple_support']['value_digest']='changed'
        self.assertIsNone(B.complete_claim_composition(packet,review))
        state=uncompact_json(json.loads(packet['text']));claim=next(c for c in packet['claim_manifest']if c['field']=='subject')
        state['source_contexts'][0]['structured_fields']['invented']='not received'
        self.assertIsNone(B.source_tuple_field_observation(packet,claim,state))


if __name__=='__main__':unittest.main()
