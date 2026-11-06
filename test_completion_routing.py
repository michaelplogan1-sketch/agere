"""Incomplete composition and uncertain evidence have different next actions."""
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from clinical_core.agere import model_policy as M
from clinical_core.agere.clinical import backstop_agent as B, semantic_analyst as S, system_one_gate as G
from clinical_core.contracts import digest
from clinical_core.agere.deliverables.receipts import compact_json,uncompact_json


def joint_fixture():
    claim={'id':'claim_1','field':'reason','path':['answer_rows',0,'reason']}
    state={'patient_id':'A','answer_rows':[{'patient_id':'A','documentary':{'required_source_dependency':'doc'},'reason':'Review the received record.'}],
        'claim_manifest':[claim],'source_dependency_contract':{'native_roundtrip_verified':True,'parent_fingerprint':'source',
            'required_packet_ids':['child_A'],'deferred_source_claim_ids':['claim_0']}}
    text=json.dumps(compact_json(state))
    parent={'id':'patient_A','text':text,'claim_manifest':[claim],'patient_dependency_bound':True,
        'dependency_complete':True,'source_context_complete':True,'source_dependency_roundtrip_verified':True,
        'source_dependency_fingerprint':'source','source_dependency_patient_id':'A','required_source_packet_ids':['child_A']}
    child={'id':'child_A','source_dependency_parent_id':'patient_A','source_dependency_fingerprint':'source',
        'source_context_complete':True,'dependency_complete':True}
    field={'claim_id':'claim_1','field':'reason','path':claim['path'],'value_digest':digest('Review the received record.'),
        'verification_observation':{'state':'supported','model_judgment':{'label':'supported','decided_by':'fixture','probability':.9,'threshold':.8}}}
    review={'all_fields_supported':True,'remaining_obligation':'complete_dependency_support','unresolved_count':0,'fault_count':0,
        'packet_digest':digest(text),'manifest_digest':digest([claim]),'expected_count':1,'supported_count':1,'supported_claims':[field]}
    return [parent,child],[{'id':'patient_A','label':'unknown','decided_by':None,'field_review':review},
        {'id':'child_A','label':'supported','decided_by':'fixture'}]


class CompletionRouting(unittest.TestCase):
    def setUp(self):
        self.calls = [{'name':'query_sql','input':{'sql':'SELECT patient_id, observation FROM received'},
            'result':{'result_view':'agere_result_1','columns':['patient_id','observation'],
                      'rows':[['A','A dated request; current assignment is unknown.']], 'truncated':False}}]
        self.answer = {'status':'partial','answer_type':'table','headline':'Received observations for review.',
                       'table_from_tool':'agere_result_1','missing':['Current assignment is unknown.']}
        self.support = {'label':'unknown','decided_by':None,'steps':[{'provider':'fixture','status':'answered'}], 'failed_packet_ids':[],
                        'packets':[{'id':'patient_A','label':'unknown','decided_by':None}]}

    def review(self, label='answers_partially', *, decided='fixture', p=.95, support=None):
        coverage={'label':label,'p':p,'provider':'fixture','decided_by':decided,'calls':1,'cost_usd':.01}
        s1=SimpleNamespace(decide=lambda *a,**k:None)
        with patch.dict('os.environ',{'AGERE_QUANTITATIVE_ROLE_CHECK':'off'}), \
                patch.object(G,'ready',return_value=True),patch.object(G,'s1',return_value=s1), \
                patch.object(G,'check',return_value=coverage) as check, \
                patch.object(B,'check_support_packets',return_value=support or self.support) as source_check:
            out=B.review_final(SimpleNamespace(),'Explain each patient and the proposed next review.',
                               copy.deepcopy(self.answer),copy.deepcopy(self.calls),SimpleNamespace(read={}),
                               ledger=None,as_of='2030-05-27')
        return out,source_check.call_count,check.call_args

    def test_joint_unknown_keeps_checks_but_stops_unlocated_prose_rewrites(self):
        packets,results=joint_fixture();before=copy.deepcopy(results)
        wait=B.joint_dependency_wait(packets,results)
        self.assertEqual(wait,['patient_A']);self.assertEqual(results,before)
        support={**self.support,'packets':results,'all_dependencies_checked':True,'joint_verification_wait':wait,'claim_feedback':[]}
        assessment,_,_=self.review('answers_fully',decided=None,support=support)
        self.assertEqual(assessment['status'],'repair');self.assertEqual(assessment['support']['label'],'unknown')
        self.assertEqual(assessment['retry_disposition'],'retain_partial_until_new_evidence')
        self.assertEqual(assessment['review_stop_reason'],'unresolved_source_support')
        self.assertNotIn('prose_repair',assessment)
        for initial in (True,False):
            sent=[]
            def send(body):
                sent.append(body)
                return {'content':[{'type':'text','text':json.dumps(self.answer)}]},0,False
            out=S.tool_loop('Explain each patient.',[],[],lambda use:self.fail('Unexpected source acquisition'),
                send=send,model=M.HAIKU,escalation_model=M.SONNET,max_tools=None,initial_calls=self.calls,
                initial_answer=copy.deepcopy(self.answer)if initial else None,review=lambda a,c:copy.deepcopy(assessment))
            self.assertEqual(len(sent),0 if initial else 1)
            self.assertEqual(out['review_stopped'],'unresolved_source_support')
            self.assertEqual(out['answer']['status'],'partial');self.assertEqual(out['answer']['verification'],'unverified')
            self.assertFalse(out.get('model_escalations'))
            from clinical_core.agere.clinical.harness_improvement import partial_prefix_evidence
            self.assertIsNone(partial_prefix_evidence(out))
        for changed in ({**support,'all_dependencies_checked':False},{**support,'joint_verification_wait':['different_packet']}):
            self.assertNotIn('retry_disposition',self.review('answers_fully',decided=None,support=changed)[0])
        self.assertNotIn('retry_disposition',self.review('answers_partially',decided='fixture',support=support)[0])

    def test_joint_wait_requires_exact_fields_current_children_and_no_fault(self):
        mutations=('missing_child','unknown_child','fault','field_unknown','stale_packet','stale_manifest','value',
            'low_probability','nonfinite_probability','missing_field','child_family','incomplete_source','missing_roundtrip','deferred_contract')
        for kind in mutations:
            packets,results=joint_fixture();parent=packets[0];review=results[0]['field_review'];field=review['supported_claims'][0]
            if kind=='missing_child':results.pop()
            elif kind=='unknown_child':results[1].update(label='unknown',decided_by=None)
            elif kind=='fault':results[0].update(label='contradicted',decided_by='fixture')
            elif kind=='field_unknown':review.update(all_fields_supported=False,unresolved_count=1)
            elif kind=='stale_packet':parent['text']+=' '
            elif kind=='stale_manifest':review['manifest_digest']='changed'
            elif kind=='value':field['value_digest']='changed'
            elif kind=='low_probability':field['verification_observation']['model_judgment']['probability']=.79
            elif kind=='nonfinite_probability':field['verification_observation']['model_judgment']['probability']=float('nan')
            elif kind=='missing_field':review['supported_claims']=[]
            elif kind=='child_family':packets[1]['source_dependency_fingerprint']='changed'
            elif kind=='incomplete_source':packets[1]['source_context_complete']=False
            elif kind=='missing_roundtrip':parent['source_dependency_roundtrip_verified']=False
            elif kind=='deferred_contract':
                state=uncompact_json(json.loads(parent['text']));state['source_dependency_contract']['deferred_source_claim_ids']=['invented']
                parent['text']=json.dumps(compact_json(state));review['packet_digest']=digest(parent['text'])
            with self.subTest(kind=kind):self.assertEqual(B.joint_dependency_wait(packets,results),[])

    def test_repeated_current_review_does_not_grow_the_same_limitation(self):
        packets,results=joint_fixture()
        support={**self.support,'packets':results,'all_dependencies_checked':True,
            'joint_verification_wait':B.joint_dependency_wait(packets,results),'claim_feedback':[]}
        assessment,_,_=self.review('answers_fully',decided=None,support=support)
        reviewed=[]
        def review(answer,calls):
            reviewed.append(copy.deepcopy(answer));return copy.deepcopy(assessment)
        forbidden=lambda *a,**k:self.fail('Unchanged review uncertainty must not invoke the actor')
        first=S.tool_loop('Explain each patient.',[],[],forbidden,send=forbidden,model=M.HAIKU,
            max_tools=None,initial_calls=self.calls,initial_answer=self.answer,review=review)
        second=S.tool_loop('Explain each patient.',[],[],forbidden,send=forbidden,model=M.HAIKU,
            max_tools=None,initial_calls=self.calls,initial_answer=first['answer'],review=review)
        self.assertEqual(len(reviewed),2);self.assertEqual(first['answer'],second['answer'])
        self.assertEqual(second['answer']['limitations'].count(assessment['feedback']),1)
        self.assertEqual(second['review_stopped'],'unresolved_source_support')

    def test_known_incomplete_draft_does_not_dispatch_patient_support(self):
        for label in ('answers_partially','answers_a_different_question'):
            with self.subTest(label=label):
                out,count,args=self.review(label)
                self.assertEqual(count,0)
                self.assertEqual(out['status'],'repair')
                self.assertEqual(out['repair_kind'],'incomplete_coverage')
                self.assertEqual(out['calls'],1)
                self.assertEqual(out['support']['label'],'not_run')
                self.assertFalse(out['support']['all_dependencies_checked'])
                self.assertIn('unfinished reading or composition',out['feedback'])
                self.assertIn('A source-scoped unknown',args.args[0])

    def test_an_unsettled_or_below_threshold_coverage_vote_cannot_skip_support(self):
        for decided,p in ((None,.95),('fixture',.55)):
            with self.subTest(decided=decided,p=p):
                out,count,_=self.review(decided=decided,p=p)
                self.assertEqual(count,1)
                self.assertNotEqual(out.get('repair_kind'),'incomplete_coverage')
                self.assertEqual(out.get('repair_kind')=='unresolved_verification',decided is None)
                self.assertNotEqual(out['status'],'confirmed')

    def test_undecided_coverage_does_not_claim_missing_work_or_complete_acceptance(self):
        supported={'label':'supported','decided_by':'fixture','steps':[],
                   'packets':[{'id':'patient_A','label':'supported','decided_by':'fixture'}]}
        for label in G.CHECK_OPTIONS:
            with self.subTest(label=label):
                out,count,_=self.review(label,decided=None,support=supported)
                self.assertEqual(count,1)
                self.assertEqual(out['status'],'repair')
                self.assertEqual(out['repair_kind'],'unresolved_verification')
                self.assertEqual(out['progress_state'],{'coverage':'unknown','unresolved_verification':[]})
                self.assertIn('establishes neither',out['feedback'])
                self.assertNotIn('supply the missing results',out['feedback'])

    def test_undecided_coverage_cannot_hide_a_located_source_fault(self):
        bad={'label':'contradicted','decided_by':'fixture','steps':[],
             'failed_packet_ids':['patient_A'],'packets':[{'id':'patient_A','label':'contradicted','decided_by':'fixture'}]}
        out,count,_=self.review(decided=None,support=bad)
        self.assertEqual(count,1)
        self.assertNotEqual(out.get('repair_kind'),'unresolved_verification')
        self.assertIn('Evidence check: contradicted',out['feedback'])

    def test_complete_source_support_stops_uncertain_coverage_rewrites_without_acceptance(self):
        supported={'label':'supported','decided_by':'fixture','steps':[], 'all_dependencies_checked':True,
            'packets':[{'id':'patient_A','label':'supported','decided_by':'fixture'}]}
        assessment=self.review(decided=None,support=supported)[0]
        self.assertEqual(assessment['retry_disposition'],'retain_partial_until_new_evidence')
        for initial in (False,True):
            sent=[]
            def send(body):
                sent.append(body)
                return {'content':[{'type':'text','text':json.dumps(self.answer)}]},0,False
            out=S.tool_loop('Explain each patient.',[],[],lambda use:self.fail('No source rerun'),send=send,
                model=M.HAIKU,escalation_model=M.SONNET,max_tools=None,initial_calls=self.calls,
                initial_answer=self.answer if initial else None,review=lambda a,c:copy.deepcopy(assessment))
            with self.subTest(initial=initial):
                self.assertEqual(len(sent),0 if initial else 1)
                self.assertEqual(out['review_stopped'],'unresolved_coverage')
                self.assertEqual(out['answer']['status'],'partial')
                self.assertEqual(out['answer']['verification'],'unverified')
                self.assertEqual(out['answer']['missing'],self.answer['missing'])
                self.assertFalse(out.get('model_escalations'))
        for kind in ('unchecked','empty','fault','uncertain'):
            changed=copy.deepcopy(supported)
            if kind=='unchecked':changed['all_dependencies_checked']=False
            elif kind=='empty':changed['packets']=[]
            elif kind=='fault':
                changed['label']='contradicted';changed['packets'][0]['label']='contradicted'
            else:
                changed['label']='unknown';changed['decided_by']=None;changed['packets'][0]['decided_by']=None
            with self.subTest(kind=kind):
                self.assertNotIn('retry_disposition',self.review(decided=None,support=changed)[0])
        self.assertNotIn('retry_disposition',self.review(decided='fixture',support=supported)[0])

    def test_admitted_pending_work_prevents_uncertain_coverage_stop_and_keeps_current_actor(self):
        supported={'label':'supported','decided_by':'fixture','steps':[], 'all_dependencies_checked':True,
            'packets':[{'id':'patient_A','label':'supported','decided_by':'fixture'}]}
        target={'path':['answer','missing',0],'text':'Contract review not executed.'}
        with patch.object(G,'check_pending_work',return_value={'targets':[target],'calls':1,'cost_usd':.001}):
            assessment=self.review(decided=None,support=supported)[0]
        self.assertEqual(assessment['repair_kind'],'incomplete_work')
        self.assertNotIn('retry_disposition',assessment)
        self.assertIn('list_analyses/describe_analysis',assessment['feedback'])
        for initial in (True,False):
            invoked=[];sent=[]
            def execute(use):invoked.append(use);return {'analyses':[{'operation':'accountability_review'}]}
            def send(body):
                sent.append(body)
                if not initial and len(sent)==1:
                    return {'content':[{'type':'text','text':json.dumps(self.answer)}]},0,False
                if len(sent)==(1 if initial else 2):
                    return {'content':[{'type':'tool_use','id':'discover','name':'list_analyses','input':{}}]},0,False
                return {'content':[{'type':'text','text':json.dumps({**self.answer,'missing':['No contract document supplied.']})}]},0,False
            def review(answer,calls):
                return copy.deepcopy(assessment)if len(calls)==len(self.calls)else {'status':'confirmed','feedback':None}
            out=S.tool_loop('Review the contract.',[],B.TOOLS,execute,send=send,model=M.HAIKU,
                escalation_model=M.SONNET,max_tools=None,initial_calls=self.calls,
                initial_answer=self.answer if initial else None,review=review)
            with self.subTest(initial=initial):
                self.assertEqual([u['name']for u in invoked],['list_analyses'],str(out)[:1800])
                self.assertEqual(out['answer']['missing'],['No contract document supplied.'])
                self.assertFalse(out.get('model_escalations'))

    def test_uncertain_coverage_and_source_outage_do_not_request_a_rewrite(self):
        unavailable={'label':'unknown','decided_by':None,'steps':[{'provider':'fixture','status':'error'}],
                     'packets':[{'id':'patient_A','label':None,'decided_by':None}]}
        out,count,_=self.review(decided=None,support=unavailable)
        self.assertEqual(count,1)
        self.assertEqual(out['status'],'unavailable')
        self.assertIsNone(out['feedback'])
        self.assertIn('unavailable',out['reason'])

    def test_coverage_probability_churn_cannot_force_more_rewrites(self):
        assessments=[self.review(label,decided=None,p=p)[0] for label,p in
                     [('answers_partially',.68),('answers_a_different_question',.57)]]
        self.assertEqual(assessments[0]['progress_state'],assessments[1]['progress_state'])
        sent=[];reviews=iter(assessments)
        def send(body):
            sent.append(body)
            return {'content':[{'type':'text','text':json.dumps({**self.answer,'headline':'Draft '+str(len(sent))})}]},.001,False
        out=S.tool_loop('Explain each patient.',[],[],lambda use:self.fail('Unexpected new source acquisition'),
            send=send,model=M.HAIKU,escalation_model=M.SONNET,max_tools=None,initial_calls=self.calls,
            review=lambda a,c:next(reviews))
        self.assertEqual(len(sent),2)
        self.assertEqual(out['review_stopped'],'no_progress')
        self.assertEqual(out['answer']['status'],'partial')
        self.assertEqual(out['answer']['verification'],'unverified')
        self.assertFalse(out.get('model_escalations'))
        self.assertEqual(out['tool_calls'],self.calls)

    def test_complete_unknown_answer_receives_support_and_preserves_uncertainty(self):
        out,count,_=self.review('answers_fully')
        self.assertEqual(count,1)
        self.assertEqual(out['repair_kind'],'unresolved_verification')
        self.assertEqual(out['support']['label'],'unknown')
        self.assertNotEqual(out['status'],'confirmed')

    def test_complete_checks_with_uncertain_fields_stop_without_rewriting_or_accepting(self):
        feedback={'packet_id':'patient_A','claim_id':'claim_0','field':'observation',
            'path':['answer_rows',0,'observation'],'label':'unknown','decided_by':None,
            'reason':'The model favored supported below its acceptance threshold.'}
        support={**self.support,'all_dependencies_checked':True,'packet_manifest_complete':True,
            'unchecked_packet_ids':[],'claim_feedback':[feedback],
            'packets':[{'id':'patient_A','label':'unknown','decided_by':None,'claim_feedback':[feedback],
                'field_review':{'expected_count':2,'supported_count':1,'unresolved_count':1,'fault_count':0}}]}
        for coverage,decided,work in (('answers_fully','fixture','complete'),('answers_partially',None,'undetermined')):
            assessment=self.review(coverage,decided=decided,support=support)[0]
            self.assertEqual(assessment['retry_disposition'],'retain_partial_until_new_evidence')
            self.assertEqual(assessment['completion']['work'],work)
            self.assertEqual(assessment['completion']['verification'],'uncertain')
            self.assertEqual(assessment['completion']['unverified_fields'][0]['field'],'observation')
            self.assertEqual(assessment['support']['claim_feedback'],[feedback])
            forbidden=lambda *a,**k:self.fail('Unknown checks must not purchase another rewrite')
            result=S.tool_loop('Explain each patient.',[],[],forbidden,send=forbidden,
                model=M.HAIKU,escalation_model=M.SONNET,max_tools=None,
                initial_calls=self.calls,initial_answer=self.answer,
                review=lambda *a:copy.deepcopy(assessment))
            self.assertEqual(result['review_stopped'],'unresolved_verification')
            self.assertEqual(result['answer']['status'],'partial')
            for field in ('headline','table_from_tool','missing'):
                self.assertEqual(result['answer'][field],self.answer[field])
            self.assertEqual(result['tool_calls'],self.calls)
            self.assertFalse(result.get('model_escalations'))
        for mutation in ('unchecked','source_gap','fault','missing_count','bad_count','blocked','coverage_incomplete'):
            changed=copy.deepcopy(support);label='answers_fully'
            if mutation=='unchecked':changed['all_dependencies_checked']=False
            elif mutation=='source_gap':changed['packets'][0].pop('field_review')
            elif mutation=='fault':
                changed['claim_feedback'][0].update(label='wrong_entity',decided_by='fixture')
                changed['packets'][0]['field_review']['fault_count']=1
            elif mutation=='missing_count':changed['packets'][0]['field_review'].pop('unresolved_count')
            elif mutation=='bad_count':changed['packets'][0]['field_review']['expected_count']=3
            elif mutation=='blocked':changed['blocked_family_ids']=['patient_A']
            else:label='answers_partially'
            with self.subTest(mutation=mutation):
                self.assertFalse(B.unlocated_verification_wait({'label':label,'decided_by':'fixture'},changed))

    def test_complete_coverage_cannot_bypass_a_located_source_fault(self):
        bad={'label':'contradicted','decided_by':'fixture','steps':[],
             'failed_packet_ids':['patient_A'],'packets':[{'id':'patient_A','label':'contradicted','decided_by':'fixture'}]}
        out,count,_=self.review('answers_fully',support=bad)
        self.assertEqual(count,1)
        self.assertEqual(out['status'],'repair')
        self.assertNotEqual(out.get('repair_kind'),'unresolved_verification')
        self.assertEqual(out['support']['failed_packet_ids'],['patient_A'])

    def test_partial_coverage_never_becomes_uncertainty_only(self):
        self.assertIsNone(B.verification_repair_state({'label':'answers_partially'},self.support))
        self.assertIsNotNone(B.verification_repair_state({'label':'answers_fully'},self.support))

    def test_incomplete_work_continues_same_evidence_on_stronger_actor_then_checks_sources(self):
        sent=[];executed=[];outcomes=iter([self.answer,{**self.answer,'headline':'Each received observation is explained.'}])
        def send(body):
            sent.append(copy.deepcopy(body))
            return {'content':[{'type':'text','text':json.dumps(next(outcomes))}]},.001,False
        assessments=iter([self.review()[0],self.review('answers_fully')[0]])
        def review(answer,calls):
            self.assertEqual(calls,self.calls)
            value=next(assessments)
            # A service-unavailable terminal review cannot create another paid
            # retry; the source evidence and unknown remain preserved.
            if value.get('repair_kind')=='unresolved_verification':
                value={**value,'status':'unavailable','feedback':None}
            return value
        out=S.tool_loop('Explain each patient.',[],[],lambda use:executed.append(use),send=send,
            model=M.HAIKU,escalation_model=M.SONNET,max_tools=None,initial_calls=self.calls,review=review)
        self.assertEqual([x['model']for x in sent],[M.HAIKU,M.SONNET])
        self.assertEqual(executed,[])
        self.assertEqual(out['model_escalations'][0]['retained_tool_calls'],1)
        self.assertEqual(out['tool_calls'],self.calls)
        self.assertEqual(out['answer']['status'],'partial')


if __name__=='__main__':unittest.main()
