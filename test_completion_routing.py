"""Incomplete composition and uncertain evidence have different next actions."""
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from clinical_core.agere import model_policy as M
from clinical_core.agere.clinical import backstop_agent as B, semantic_analyst as S, system_one_gate as G


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
