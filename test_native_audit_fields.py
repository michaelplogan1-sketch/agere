"""Native received metadata never validates source/clinical interpretations."""
import copy,json,unittest
from unittest.mock import patch
from clinical_core.agere.clinical import backstop_agent as B,evidence_projection as E
from clinical_core.agere.deliverables.receipts import uncompact_json,readable_json
from clinical_core.contracts import digest
from test_aggregate_explain import AS_OF
import test_native_audit_coverage as fixtures


class AuditFields(unittest.TestCase):
    def setUp(self):
        self.audit=fixtures.NativeAuditCoverage();self.audit.setUp();self.addCleanup(self.audit.doCleanups)
        self.proof=self.audit.proof();self.records=self.audit.received_contexts(self.proof)

    def packets(self):
        return B.support_packets('Interpret the source assertions',self.audit.answer,self.audit.tools.native_calls,[],AS_OF,
                                 native_audit=self.proof,source_records=self.records)

    def test_metadata_avoids_model_fields_but_every_semantic_axis_stays_a_judgment(self):
        packets=self.packets();packet=next(p for p in packets if p.get('native_audit_fields'))
        requested=[]
        def unknown(subject,questions,evidence,**kwargs):
            requested.extend(q['instructions']['claim']for q in questions.values())
            return {'judgments':{k:{'label':'unknown','decided_by':None}for k in questions},'steps':[]}
        result=B.localize_support_failure(packet,unknown,as_of=AS_OF,ledger=None)
        native=[r for r in result['field_review']['supported_claims']if r['verification_observation'].get('native_provenance')]
        self.assertTrue(native);self.assertTrue(requested)
        self.assertTrue(all(r['field']in B.AUDIT_LITERAL_FIELDS for r in native))
        fields={c['field']for c in requested}
        self.assertTrue({'concept','relation','value','unit','value_domain','polarity','subject','temporality','clinical_date'}<=fields)
        self.assertIn('analytical_effects',fields);self.assertIn('account_group',fields)
        self.assertEqual(len(requested)+len(native),len(packet['claim_manifest']))
        self.assertIsNone(B.complete_claim_composition(packet,result['field_review']))
        self.assertFalse(result['field_review']['all_fields_supported'])

    def test_native_proof_does_not_change_any_provider_text_or_source_value(self):
        with patch.object(B,'audit_literal_columns',return_value=[]):before=self.packets()
        after=self.packets();self.assertEqual(len(before),len(after))
        for a,b in zip(before,after):
            self.assertEqual(a['text'],b['text']);self.assertEqual(a['bytes'],b['bytes'])
            self.assertEqual(a['claim_manifest'],b['claim_manifest'])

    def test_semantic_fields_cannot_be_blessed_by_copy_provenance(self):
        packet=next(p for p in self.packets()if p.get('native_audit_fields'))
        state=uncompact_json(json.loads(packet['text']))
        for claim in packet['claim_manifest']:
            if claim['field']in ('value','unit','subject','polarity','temporality','clinical_date','analytical_effects','account_group'):
                with self.subTest(field=claim['field']):self.assertIsNone(B.native_field_proof(packet,claim,state))

    def test_changed_claim_source_patient_clock_and_native_role_fail_closed(self):
        original=next(p for p in self.packets()if p.get('native_audit_fields'))
        claim=next(c for c in original['claim_manifest']if c['field']=='source_record_id')
        for change in ('value','source','patient','clock','role','manifest'):
            packet=copy.deepcopy(original);state=uncompact_json(json.loads(packet['text']))
            if change=='value':state['answer_rows'][0]['source_record_id']='another-source'
            elif change=='source':state['source_contexts'][0]['structured_fields']['unreceived']='invented'
            elif change=='patient':state['patient_id']='another-patient'
            elif change=='clock':packet['date']='2031-01-01'
            elif change=='role':
                packet['native_audit_fields']['columns'].append('polarity')
                proof=packet['native_audit_fields'];proof['binding_digest']=digest({k:v for k,v in proof.items()if k!='binding_digest'})
            else:packet['claim_manifest']=[]
            with self.subTest(change=change):self.assertIsNone(B.native_field_proof(packet,claim,state))

    def test_claim_partitions_keep_only_literal_proofs_against_the_same_complete_source(self):
        packet=next(p for p in self.packets()if p.get('native_audit_fields'))
        state=uncompact_json(json.loads(packet['text']))
        claim=next(c for c in packet['claim_manifest']if c['field']=='source_record_id')
        selected=copy.deepcopy(state);selected['claim_manifest']=[claim]
        selected['claim_partition']={'parent_fingerprint':packet['native_audit_fields']['state_digest'],
            'parent_packet_id':packet['id'],'source_inputs_digest':packet['native_audit_fields']['protected_inputs_digest']}
        part={**packet,'parent_packet_id':packet['id'],'id':packet['id']+'__claims_0','claim_manifest':[claim],
              'text':json.dumps(readable_json(selected))}
        self.assertIsNotNone(B.native_field_proof(part,claim,selected))
        selected['source_contexts'][0]['structured_fields']['unreceived']='invented'
        self.assertIsNone(B.native_field_proof(part,claim,selected))

    def test_omitting_a_semantic_axis_from_a_valid_native_projection_disables_literal_proofs(self):
        root=self.audit.result['result_view'];columns=[c for c in self.audit.result['columns']if c!='polarity']
        self.audit.project('SELECT '+','.join(columns)+' FROM '+root)
        proof=self.audit.proof()
        self.assertEqual(B.audit_literal_columns(self.audit.answer,self.audit.tools.native_calls,proof),[])

    def test_only_deciding_complete_source_judgments_compose_with_native_literals(self):
        packet=next(p for p in self.packets()if p.get('native_audit_fields'))
        def supported(subject,questions,evidence,**kwargs):
            return {'judgments':{k:{'label':'supported','decided_by':'authored-source-judge','probability':1.,'threshold':.8}
                                  for k in questions},'steps':[]}
        review=B.localize_support_failure(packet,supported,as_of=AS_OF,ledger=None)['field_review']
        self.assertTrue(review['all_fields_supported']);self.assertGreater(review['native_supported_count'],0)
        composition=B.complete_claim_composition(packet,review)
        self.assertIsNotNone(composition);self.assertFalse(composition['clinical_qualification'])
        clinical=next(r for r in review['supported_claims']if r['field']=='polarity')
        clinical['verification_observation']['model_judgment']['decided_by']=None
        self.assertIsNone(B.complete_claim_composition(packet,review))

    def test_a_decided_domain_fault_still_blocks_complete_composition(self):
        packet=next(p for p in self.packets()if p.get('native_audit_fields'))
        def domain_fault(subject,questions,evidence,**kwargs):
            return {'judgments':{key:{'label':'wrong_domain'if q['instructions']['claim']['field']=='value_domain'else'supported',
                'decided_by':'authored-source-judge','probability':1.,'threshold':.8}for key,q in questions.items()},'steps':[]}
        result=B.localize_support_failure(packet,domain_fault,as_of=AS_OF,ledger=None)
        self.assertTrue(result['feedback']);self.assertTrue(all(f['field']=='value_domain'for f in result['feedback']))
        self.assertGreater(result['field_review']['native_supported_count'],0)
        self.assertGreater(result['field_review']['fault_count'],0)
        self.assertIsNone(B.complete_claim_composition(packet,result['field_review']))


if __name__=='__main__':unittest.main()
