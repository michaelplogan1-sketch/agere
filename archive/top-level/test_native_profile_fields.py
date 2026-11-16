"""Exact profile provenance can settle a scalar, never a clinical conclusion."""
import copy,json,unittest
from clinical_core.agere.clinical import backstop_agent as B
from clinical_core.agere.deliverables.receipts import readable_json,uncompact_json
from test_complete_claim_composition import packet,fields
from clinical_core.contracts import digest


def fixture(row=None):
    p=packet(row or {'patient_id':'A','reason':'A dated hold; current reason unknown.',
                     'age':55,'clinical_eligible':1,'dose_mg':55})
    profile={'patient_id':'A','patient_name':'Alex','birth_date':'1975-05-12',
        'age_at_snapshot':55,'age_reported':50,'sex_reported':'F',
        'profile_status':'received_profile','profile_source_date':'2026-01-01',
        'profile_source_fingerprint':'authored-profile-fingerprint'}
    source={'source_digest':'authored-source','release':'authored','as_of':p['date']}
    profile_call={'name':'read_patient_profiles','input':{},'result':{
        'result_view':'profiles','kind':'received_patient_profiles','columns':list(profile),
        'rows':[profile],'source':source,'input_patient_ids':['A'],'truncated':False}}
    output=uncompact_json(json.loads(p['text']))['answer_rows'][0]
    root={'name':'compose_table','input':{'source':'profiles'},'result':{
        'result_view':'final','columns':list(output),'rows':[output],'truncated':False}}
    return p,{'table_from_tool':'final'},[profile_call,root]


def mark(p,a,calls):
    B.mark_native_profile_fields([p],a,calls)
    return p


def state(p):return uncompact_json(json.loads(p['text']))


def write_state(p,s):p['text']=json.dumps(readable_json(s));p['bytes']=len(p['text'].encode())


class NativeProfileFields(unittest.TestCase):
    def proof(self,p,field='age'):
        claim=next(c for c in p['claim_manifest']if c['field']==field)
        return B.native_profile_field_proof(p,claim,state(p))

    def test_mixed_answer_checks_exact_age_natively_and_only_sends_semantics(self):
        p,a,calls=fixture();mark(p,a,calls);asked=[]
        def judge(task,named,evidence,**kw):
            asked.extend(v['instructions']['claim']['field']for v in named.values())
            return fields(task,named,evidence,**kw)
        r=B.localize_support_failure(p,judge,as_of=p['date'],ledger=None)
        self.assertEqual(asked,['reason','clinical_eligible','dose_mg'])
        self.assertEqual(r['field_review']['native_supported_count'],1)
        self.assertEqual(r['field_review']['supported_count'],4)
        age=r['field_review']['supported_claims'][1]['verification_observation']
        self.assertEqual(age['native_provenance']['source_field'],'age_at_snapshot')
        self.assertNotIn('model_judgment',age)
        self.assertIsNotNone(B.complete_claim_composition(p,r['field_review']))
        self.assertFalse(B.complete_claim_composition(p,r['field_review'])['clinical_qualification'])
        self.assertTrue(B.complete_claim_composition(p,r['field_review'])['global_question_review_still_required'])

    def test_profile_only_field_review_dispatches_no_provider(self):
        p,a,calls=fixture({'patient_id':'A','age':55});mark(p,a,calls)
        r=B.localize_support_failure(p,lambda *a,**k:self.fail('No semantic field'),as_of=p['date'],ledger=None)
        self.assertEqual(r['steps'],[]);self.assertEqual(r['feedback'],[])
        self.assertIsNotNone(B.complete_claim_composition(p,r['field_review']))

    def test_global_unknown_or_a_clinical_fault_still_blocks_the_answer(self):
        p,a,calls=fixture();mark(p,a,calls)
        summary={**p,'id':'summary','claim_manifest':[],'patient_dependency_bound':False}
        for global_label,clinical_fault in [('unknown',False),('supported',True)]:
            def whole(*args,**kw):
                label=global_label if args[2][0]['id']=='summary'else'unknown'
                return {'label':label,'decided_by':'fixture'if label=='supported'else None,'steps':[]}
            def judge(task,named,evidence,**kw):
                r=fields(task,named,evidence,**kw)
                if clinical_fault:r['judgments']['claim_2'].update(label='wrong_domain',decided_by='fixture')
                return r
            r=B.check_support_packets([summary,p],whole,as_of=p['date'],ledger=None,field_decider=judge)
            self.assertNotEqual(r['label'],'supported')

    def test_identity_source_clock_value_and_producer_changes_invalidate(self):
        for change in ['entity','state_entity','row_entity','value','date','as_of','source','fingerprint',
                       'producer','birth_date','source_date','source_field','claim_path','binding']:
            p,a,calls=fixture();mark(p,a,calls);s=state(p);proof=p['native_profile_fields']
            if change=='entity':p['source_dependency_patient_id']='B'
            elif change=='state_entity':s['patient_id']='B'
            elif change=='row_entity':s['answer_rows'][0]['patient_id']='B'
            elif change=='value':s['answer_rows'][0]['age']=56
            elif change=='date':p['date']='2030-05-26'
            elif change=='as_of':proof['source']['as_of']='2030-05-26'
            elif change=='source':proof['source']['source_digest']='different'
            elif change=='fingerprint':proof['profile']['profile_source_fingerprint']='different'
            elif change=='producer':proof['producer']['receipt_digest']='different'
            elif change=='birth_date':proof['profile']['birth_date']='1976-05-12'
            elif change=='source_date':proof['profile']['profile_source_date']='2031-01-01'
            elif change=='source_field':proof['claims']['claim_1']['source_field']='age_reported'
            elif change=='claim_path':p['claim_manifest'][1]['path']=['answer_rows',1,'age']
            else:proof['binding_digest']='different'
            write_state(p,s)
            with self.subTest(change=change):self.assertIsNone(self.proof(p))

    def test_unrelated_prose_edits_preserve_scalar_but_old_full_review_expires(self):
        p,a,calls=fixture();mark(p,a,calls)
        r=B.localize_support_failure(p,fields,as_of=p['date'],ledger=None)['field_review']
        before=self.proof(p);s=state(p);s['answer_rows'][0]['reason']='A changed narrative needing review.';write_state(p,s)
        self.assertEqual(self.proof(p),before)
        self.assertIsNone(B.complete_claim_composition(p,r))

    def test_nonancestor_partial_duplicate_or_foreign_clock_producers_do_not_qualify(self):
        for change in ['nonancestor','duplicate_profile','duplicate_final','partial_profile','partial_final',
                       'missing_input','foreign_sql','profile_status','source_date','no_fingerprint','no_digest','no_clock']:
            p,a,calls=fixture();profile=calls[0]['result'];root=calls[1]
            if change=='nonancestor':root['input']={}
            elif change=='duplicate_profile':profile['rows']*=2
            elif change=='duplicate_final':root['result']['rows']*=2
            elif change=='partial_profile':profile['truncated']=True
            elif change=='partial_final':root['result']['truncated']=True
            elif change=='missing_input':profile['input_patient_ids']=[]
            elif change=='foreign_sql':
                calls.insert(0,{'name':'query_sql','input':{},'result':{
                    'result_view':'other','source_contracts':{'source_digest':'different','as_of':p['date']}}})
                root['input']['other']='other'
            elif change=='profile_status':profile['rows'][0]['profile_status']='profile_after_snapshot'
            elif change=='source_date':profile['rows'][0]['profile_source_date']='2031-01-01'
            elif change=='no_fingerprint':profile['rows'][0]['profile_source_fingerprint']=None
            elif change=='no_digest':profile['source'].pop('source_digest')
            else:profile['source'].pop('as_of')
            mark(p,a,calls)
            with self.subTest(change=change):self.assertIsNone(self.proof(p))

    def test_reapplying_builder_clears_stale_metadata(self):
        p,a,calls=fixture();mark(p,a,calls);self.assertIsNotNone(self.proof(p))
        calls[1]['input']={};mark(p,a,calls);self.assertNotIn('native_profile_fields',p)

    def test_reported_age_and_dob_age_keep_separate_contracts(self):
        p,a,calls=fixture({'patient_id':'A','age':50,'age_reported':50,'age_at_snapshot':55})
        mark(p,a,calls)
        self.assertIsNone(self.proof(p));self.assertIsNotNone(self.proof(p,'age_reported'))
        self.assertIsNotNone(self.proof(p,'age_at_snapshot'))
        for born in ['not-a-date','2031-01-01',None]:
            p,a,calls=fixture();calls[0]['result']['rows'][0]['birth_date']=born;mark(p,a,calls)
            self.assertIsNone(self.proof(p))

    def test_composition_revalidates_native_provenance_instead_of_trusting_support_label(self):
        p,a,calls=fixture();mark(p,a,calls)
        r=B.localize_support_failure(p,fields,as_of=p['date'],ledger=None)['field_review']
        for change in ['proof','wrong_field','value','no_proof']:
            review=copy.deepcopy(r);reading=review['supported_claims'][1]
            if change=='proof':reading['verification_observation']['native_provenance']['as_of']='different'
            elif change=='wrong_field':reading['field']='clinical_eligible'
            elif change=='value':reading['value_digest']='different'
            else:reading['verification_observation'].pop('native_provenance')
            with self.subTest(change=change):self.assertIsNone(B.complete_claim_composition(p,review))

    def test_partition_parent_can_keep_scalar_proof_but_source_child_cannot_borrow_it(self):
        from test_completion_routing import joint_fixture
        p,a,calls=fixture({'patient_id':'A','age':55});mark(p,a,calls)
        packets,results=joint_fixture();parent=packets[0];s=state(parent)
        s['answer_rows'][0]['age']=s['answer_rows'][0].pop('reason')
        s['answer_rows'][0]['age']=55
        claim={'id':'claim_1','field':'age','path':['answer_rows',0,'age']}
        s['claim_manifest']=[claim];parent['claim_manifest']=[claim]
        # Same exact scalar retains the original producer binding after the
        # documentary claim moves to a separately checked dependency.
        proof=copy.deepcopy(p['native_profile_fields']);proof['claims']={'claim_1':{
            **proof['claims']['claim_0'],**claim}}
        proof['binding_digest']=digest({k:v for k,v in proof.items()if k!='binding_digest'})
        parent.update(native_profile_fields=proof,date=p['date']);write_state(parent,s)
        review=B.localize_support_failure(parent,lambda *a,**k:self.fail('Scalar needs no model'),as_of=p['date'],ledger=None)['field_review']
        review['remaining_obligation']='complete_dependency_support';results[0]['field_review']=review
        self.assertEqual(B.joint_dependency_wait(packets,results),['patient_A'])
        child={**parent,'source_dependency_parent_id':'patient_A'}
        child_state=state(child);child_state['answer_rows']=[{'patient_id':'B','age':55}]
        self.assertIsNone(B.native_profile_field_proof(child,claim,child_state))
        review['supported_claims'][0]['verification_observation']['native_provenance']['as_of']='stale'
        self.assertEqual(B.joint_dependency_wait(packets,results),[])


if __name__=='__main__':unittest.main()
