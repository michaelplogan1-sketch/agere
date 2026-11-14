"""A bare empty documentary list from an incomplete review is located natively, before any model."""
import copy,json,os,unittest
from unittest.mock import Mock,patch
from clinical_core.agere.clinical import backstop_agent as B
from clinical_core.contracts import digest


def packet(pid='A',*,findings='[]',status='needs_review',carrier=None,producer='review_documents'):
    final={'patient_id':pid,'referral_json':'structured','documentary_findings':findings}
    if carrier is not None:final['document_review']=carrier
    review={'patient_id':pid,'review_status':status,'documents_reviewed':3,'source_findings_json':findings}
    evidence=[review,final]
    state={'id':'patient_'+pid,'patient_id':pid,'final_result_view':'final','answer_rows':[final],'evidence':evidence,
        'evidence_provenance':[{'row_digest':digest(e),'origins':[{'kind':'executed_native_result','result_view':v,'tool':t}]}
            for e,v,t in ((review,'review',producer),(final,'final','compose_table'))],
        'native_calculation_contracts':[{'result_view':'review','tool':producer,'input':{}},
            {'result_view':'final','tool':'compose_table','input':{'base':'base','keys':['patient_id'],
                'joins':[{'result':'review','as':'documentary_findings','items_from':'source_findings_json','mode':'collect','on':{'patient_id':'patient_id'}}]}}]}
    claims=[{'id':'claim_'+str(i),'field':k,'path':['answer_rows',0,k]}for i,k in enumerate(k for k in final if k!='patient_id')]
    from clinical_core.agere.deliverables.receipts import readable_json
    text=json.dumps(readable_json(state),ensure_ascii=False,separators=(',',':'),default=str)
    return {'id':'patient_'+pid,'date':'2030-05-27','text':text,'bytes':len(text),'claim_manifest':claims,'result_view':'final',
        'patient_dependency_bound':True,'dependency_complete':True,'source_context_complete':True,'source_dependency_patient_id':pid}


class NativeAbsence(unittest.TestCase):
    def test_only_a_bare_empty_documentary_list_from_an_incomplete_review_is_located(self):
        faults=B.documentary_absence_faults(packet())
        self.assertEqual([(f['claim_id'],f['field'],f['label'],f['decided_by'])for f in faults],
            [('claim_1','documentary_findings','missing_evidence','native_documentary_absence_check')])
        self.assertEqual(faults[0]['verification_observation']['review_status'],'needs_review')
        for status in ('reviewed_candidates','no_candidates'):self.assertEqual(len(B.documentary_absence_faults(packet(status=status))),1)
        clean=[packet(carrier='{"review_status": "needs_review", "documents_reviewed": 3}'),   # the row states the status
               packet(status='reviewed'),packet(status='no_documents'),                         # a complete review
               packet(findings='[{"record_id": "R1", "quote": "attended"}]'),                   # a finding is not judged here
               packet(producer='query_sql'),                                                    # not a document-review field
               {**packet(),'dependency_complete':False},{**packet(),'patient_dependency_bound':False},{**packet(),'id':'summary'},
               {**packet(),'text':'not json'}]
        for case in clean:self.assertEqual(B.documentary_absence_faults(case),[])

    def test_candidate_returns_located_faults_for_every_patient_without_any_model_request(self):
        summary={**packet('S'),'id':'summary','claim_manifest':[],'patient_dependency_bound':False}
        patients=[packet('A'),packet('B'),packet('C',carrier='{"review_status": "needs_review"}')]
        decider=Mock(return_value={'label':'unknown','decided_by':None,'steps':[]});frontier=Mock()
        with patch.dict(os.environ,{'AGERE_NATIVE_DOCUMENTARY_ABSENCE':'on'}):
            out=B.check_support_packets([summary,*patients],decider,as_of='2030-05-27',ledger=None,frontier_decider=frontier)
        decider.assert_not_called();frontier.assert_not_called()
        self.assertEqual((out['label'],out['decided_by']),('not_established','all_dependency_checks'))
        self.assertEqual(out['failed_packet_ids'],['patient_A','patient_B']);self.assertEqual(out['unchecked_packet_ids'],['summary','patient_C'])
        self.assertFalse(out['all_dependencies_checked']);self.assertEqual(len(out['claim_feedback']),2)
        self.assertEqual(out['native_documentary_absence']['provider_requests'],0)
        by={r['id']:r for r in out['packets']}
        self.assertEqual(by['patient_C']['status'],'awaiting_dependency_repair');self.assertIsNone(by['patient_C']['decided_by'])
        self.assertTrue(all(f['decided_by']=='native_documentary_absence_check'for f in by['patient_A']['claim_feedback']))

    def test_flag_off_and_no_located_fault_leave_the_existing_review_unchanged(self):
        summary={**packet('S'),'id':'summary','claim_manifest':[],'patient_dependency_bound':False}
        def decider(q,options,evidence,**kw):return {'label':'supported','decided_by':'authored','steps':[]}
        for flag,patients in (('off',[packet('A')]),('on',[packet('A',carrier='{"review_status": "needs_review"}')])):
            with patch.dict(os.environ,{'AGERE_NATIVE_DOCUMENTARY_ABSENCE':flag}):
                out=B.check_support_packets([summary,*copy.deepcopy(patients)],decider,as_of='2030-05-27',ledger=None)
            self.assertNotIn('native_documentary_absence',out);self.assertEqual(out['label'],'supported')


class NativeCopy(unittest.TestCase):
    CARRIER='{"review_status": "needs_review", "documents_reviewed": 3}'
    def proof(self,p,index=1):
        from clinical_core.agere.deliverables.receipts import uncompact_json
        return B.document_review_copy_proof(p,p['claim_manifest'][index],uncompact_json(json.loads(p['text'])))

    def test_exact_copy_with_disclosed_or_complete_status_is_proven_and_nothing_else(self):
        found='[{"record_id": "R1", "quote": "attended cardiology clinic"}]'
        for case in (packet(carrier=self.CARRIER),packet(status='reviewed'),packet(status='no_documents'),packet(findings=found,carrier=self.CARRIER)):
            proof=self.proof(case)
            self.assertEqual(proof['schema'],'native-document-review-copy/1');self.assertFalse(proof['clinical_qualification'])
            self.assertEqual(proof,self.proof(case))  # deterministic: the composition re-derives and compares it
        self.assertEqual(self.proof(packet(carrier=self.CARRIER))['review_status_stated_in'],['document_review'])
        self.assertIsNone(self.proof(packet()))                                     # unfinished review not stated in the row
        self.assertIsNone(self.proof(packet(carrier=self.CARRIER),index=0))         # a structured field is not a review copy
        self.assertIsNone(self.proof(packet(carrier=self.CARRIER,producer='query_sql')))
        self.assertIsNone(self.proof({**packet(carrier=self.CARRIER),'dependency_complete':False}))
        def altered(change):
            p=packet(findings=found,carrier=self.CARRIER)
            from clinical_core.agere.deliverables.receipts import uncompact_json,readable_json
            state=uncompact_json(json.loads(p['text']));change(state)
            return B.document_review_copy_proof(p,p['claim_manifest'][1],state)
        def edited(state):state['answer_rows'][0]['documentary_findings']='[{"record_id": "R1", "quote": "attended"}]'
        def domain(state):state['native_calculation_contracts'][1]['input']['joins'][0]['domain']={'column':'x'}
        def renamed(state):state['native_calculation_contracts'][1]['input']['rename_columns']={'documentary_findings':'other'}
        def second_review(state):
            extra={**state['evidence'][0],'documents_reviewed':9};state['evidence'].append(extra)
            state['evidence_provenance'].append({'row_digest':digest(extra),'origins':[{'kind':'executed_native_result','result_view':'review','tool':'review_documents'}]})
        for change in (edited,domain,renamed,second_review):self.assertIsNone(altered(change))

    def test_flag_gates_the_proof_inside_the_existing_native_field_proof(self):
        from clinical_core.agere.deliverables.receipts import uncompact_json
        p=packet(carrier=self.CARRIER);state=uncompact_json(json.loads(p['text']));claim=p['claim_manifest'][1]
        with patch.dict(os.environ,{'AGERE_NATIVE_DOCUMENTARY_COPY':'off'}):self.assertIsNone(B.native_field_proof(p,claim,state))
        with patch.dict(os.environ,{'AGERE_NATIVE_DOCUMENTARY_COPY':'on'}):
            self.assertEqual(B.native_field_proof(p,claim,state)['authority'],'native_exact_copy_of_system_one_document_review')
            self.assertIsNone(B.native_field_proof(p,p['claim_manifest'][0],state))

if __name__=='__main__':unittest.main()


class ReviewCarrierProof(unittest.TestCase):
    def packet(self,carrier,columns=('review_status','documents_reviewed')):
        final={'patient_id':'A','referral_json':'structured','document_review':carrier}
        review={'patient_id':'A','review_status':'needs_review','documents_reviewed':3,'source_findings_json':'[]'}
        state={'id':'patient_A','patient_id':'A','final_result_view':'final','answer_rows':[final],'evidence':[review,final],
            'evidence_provenance':[{'row_digest':digest(e),'origins':[{'kind':'executed_native_result','result_view':v,'tool':t}]}
                for e,v,t in ((review,'review','review_documents'),(final,'final','compose_table'))],
            'native_calculation_contracts':[{'result_view':'review','tool':'review_documents','input':{}},
                {'result_view':'final','tool':'compose_table','input':{'base':'base','keys':['patient_id'],
                    'joins':[{'result':'review','as':'document_review','mode':'one','columns':list(columns),'on':{'patient_id':'patient_id'}}]}}]}
        claims=[{'id':'claim_'+str(i),'field':k,'path':['answer_rows',0,k]}for i,k in enumerate(k for k in final if k!='patient_id')]
        return ({'id':'patient_A','date':'2030-05-27','claim_manifest':claims,'patient_dependency_bound':True,'dependency_complete':True,
                 'source_dependency_patient_id':'A'},claims[1],state)

    def test_exact_projection_of_the_review_row_is_proven_and_any_difference_is_not(self):
        proof=B.document_review_copy_proof(*self.packet({'review_status':'needs_review','documents_reviewed':3}))
        self.assertEqual((proof['schema'],proof['review_status'],proof['columns']),('native-document-review-carrier/1','needs_review',['review_status','documents_reviewed']))
        self.assertIsNotNone(B.document_review_copy_proof(*self.packet('{"review_status": "needs_review", "documents_reviewed": 3}')))
        self.assertIsNone(B.document_review_copy_proof(*self.packet({'review_status':'reviewed','documents_reviewed':3})))
        self.assertIsNone(B.document_review_copy_proof(*self.packet({'review_status':'needs_review'})))
        self.assertIsNone(B.document_review_copy_proof(*self.packet({'documents_reviewed':3},columns=('documents_reviewed',))))
