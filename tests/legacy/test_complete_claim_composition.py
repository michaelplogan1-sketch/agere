"""Claim conjunction requires exact coverage, source context and deciding checks."""
import copy,json,unittest
from clinical_core.agere.clinical import backstop_agent as B
from clinical_core.agere.deliverables.receipts import readable_json,uncompact_json


def packet(row=None,source=None):
    row=row or {'patient_id':'A','relationship':'Consultation only; current assignment unknown.',
                'reason':'Lisinopril was held for cough in the dated note.'}
    source=source or 'Patient A, 2029-03-01: Dr Gray saw the patient for a one-time cardiology consultation. Ongoing assignment is not documented. Lisinopril was held for cough.'
    claims=[{'id':'claim_'+str(i),'field':k,'path':['answer_rows',0,k]}
            for i,k in enumerate(k for k in row if k!='patient_id')]
    state={'id':'patient_A','patient_id':'A','as_of':'2030-05-27','scope':'Check every final field against the complete received source. Do not infer current care from historical statements.',
        'answer_rows':[row],'claim_manifest':claims,'source_context_gaps':[],
        'source_contexts':[{'patient_id':'A','record_id':'note1','date':'2029-03-01','text':source}]}
    text=json.dumps(readable_json(state),separators=(',',':'))
    return {'id':'patient_A','date':'2030-05-27','text':text,'bytes':len(text.encode()),
        'claim_manifest':claims,'source_context_complete':True,'dependency_complete':True,
        'patient_dependency_bound':True,'source_dependency_patient_id':'A','result_view':'final'}


def fields(task,named,evidence,**kw):
    return {'steps':[],'judgments':{k:{'label':'supported','provider':'fixture','model':'authored',
        'decided_by':'fixture','probability':.9,'threshold':.8,'probabilities':{'supported':.9,'unknown':.1}}
        for k in named}}


class ClaimComposition(unittest.TestCase):
    def review(self,p):return B.localize_support_failure(p,fields,as_of=p['date'],ledger=None)['field_review']

    def test_fields_are_composed_before_redundant_joint_vote_and_global_unknown_stays(self):
        p=packet();summary={**p,'id':'summary','claim_manifest':[], 'patient_dependency_bound':False}
        for global_label in ('supported','unknown'):
            def whole(q,options,evidence,**kw):
                self.assertEqual(evidence[0]['id'],'summary','Source fields compose without a second joint vote')
                label=global_label
                return {'label':label,'decided_by':'fixture'if label=='supported'else None,
                    'probabilities':{label:.9},'steps':[]}
            r=B.check_support_packets([summary,p],whole,as_of=p['date'],ledger=None,field_decider=fields)
            self.assertEqual(r['label'],global_label)
            self.assertEqual(r['packets'][1]['decided_by'],'complete_typed_claim_composition')
            self.assertTrue(r['packets'][1]['field_review']['complete_dependency_accepted'])
            self.assertFalse(r['packets'][1].get('claim_composition',{}).get('clinical_qualification',False))

    def test_omitted_duplicate_changed_or_unsettled_judgment_cannot_close_packet(self):
        p=packet();r=self.review(p)
        self.assertIsNotNone(B.complete_claim_composition(p,r))
        for change in ['missing','duplicate','wrong_value','wrong_field','wrong_path','below_threshold','no_decider','packet_digest','manifest_digest','count']:
            x=copy.deepcopy(r)
            if change=='missing':x['supported_claims'].pop()
            elif change=='duplicate':x['supported_claims'][1]=x['supported_claims'][0]
            elif change=='wrong_value':x['supported_claims'][0]['value_digest']='wrong'
            elif change=='wrong_field':x['supported_claims'][0]['field']='other'
            elif change=='wrong_path':x['supported_claims'][0]['path']=['answer_rows',1,'reason']
            elif change=='below_threshold':x['supported_claims'][0]['verification_observation']['model_judgment']['probability']=.79
            elif change=='no_decider':x['supported_claims'][0]['verification_observation']['model_judgment']['decided_by']=None
            elif change=='count':x['expected_count']=1
            else:x[change]='wrong'
            with self.subTest(change=change):self.assertIsNone(B.complete_claim_composition(p,x))

    def test_missing_context_identity_or_a_hidden_final_field_cannot_be_composed(self):
        for change in ['hidden_field','no_context','wrong_patient','context_gap','partial_source','operational_dependency','child','changed_text','hidden_claim']:
            p=packet();s=uncompact_json(json.loads(p['text']))
            if change=='hidden_field':s['answer_rows'][0]['extra']='Unsupported extra claim'
            elif change=='no_context':s['source_contexts']=[]
            elif change=='wrong_patient':s['source_contexts'][0]['patient_id']='B'
            elif change=='context_gap':s['source_context_gaps']=['Missing source']
            elif change=='partial_source':p['source_context_complete']=False
            elif change=='operational_dependency':p['dependency_complete']=False
            elif change=='child':p['parent_packet_id']='other'
            elif change=='hidden_claim':p['claim_manifest']=p['claim_manifest'][:-1]
            p['text']=json.dumps(readable_json(s),separators=(',',':'));p['bytes']=len(p['text'].encode());r=self.review(p)
            if change=='changed_text':p['text']+=' '
            with self.subTest(change=change):self.assertIsNone(B.complete_claim_composition(p,r))

    def test_a_field_fault_or_missing_response_never_becomes_support(self):
        p=packet()
        for label in ['wrong_entity','wrong_domain','wrong_subject','wrong_time','wrong_role','missing_evidence','unknown']:
            def diagnose(task,named,evidence,**kw):
                r=fields(task,named,evidence,**kw);r['judgments']['claim_1'].update(label=label,decided_by=None if label=='unknown'else'fixture');return r
            r=B.check_support_packets([p],lambda *a,**kw:{'label':'unknown','decided_by':None,'probabilities':{'unknown':1},'steps':[]},
                as_of=p['date'],ledger=None,field_decider=diagnose)
            self.assertNotEqual(r['label'],'supported')


if __name__=='__main__':unittest.main()
