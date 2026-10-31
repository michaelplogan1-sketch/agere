"""General role routing and verifier failures; authored sources, no live inference."""
import copy
import json
import unittest
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import patch

from clinical_core.agere import model_policy as M
from clinical_core.agere.clinical import semantic_analyst as S, document_review as D
from clinical_core.agere.clinical import backstop_agent as B, proposers as P, system_one as O
from clinical_core.providers import EvidenceTask, JevProvider


def reply(value):
    return {'content':[{'type':'text','text':json.dumps(value)}], 'stop_reason':'end_turn',
            'usage':{'input_tokens':10,'output_tokens':5}}


TOOL={'type':'tool_use','name':'query_sql','id':'one','input':{'sql':'SELECT 7 AS patients'}}
TOOL_REPLY={'content':[TOOL],'stop_reason':'tool_use','usage':{'input_tokens':10,'output_tokens':5}}


class Investigator(unittest.TestCase):
    def run_loop(self,responses,review=None):
        self.sent=[];self.executed=[]
        replies=iter(responses)
        def send(body):
            self.sent.append(copy.deepcopy(body))
            value=next(replies)
            if isinstance(value,Exception):raise value
            return value,.001,False
        def execute(use):
            self.executed.append(use)
            return {'rows':[{'patients':7}]}
        return S.tool_loop('A general warehouse question',[],[{'name':'query_sql'}],execute,send=send,
                           model=M.HAIKU,escalation_model=M.SONNET,max_tools=None,review=review,
                           format_retries=1,continuation_guard=lambda:{'source':'same','scope':['A','B']})

    def test_supported_haiku_answer_never_calls_sonnet(self):
        r=self.run_loop([TOOL_REPLY,reply({'status':'answered','count':7})],lambda a,c:{'status':'confirmed'})
        self.assertEqual([b['model'] for b in self.sent],[M.HAIKU,M.HAIKU])
        self.assertEqual(r['answer']['count'],7);self.assertEqual(len(self.executed),1)
        self.assertFalse(r.get('model_escalations'));self.assertEqual(r['new_paid_api_calls'],2)

    def test_deterministic_compiler_repair_keeps_haiku_and_existing_evidence(self):
        def check(a,c):
            return {'status':'confirmed'} if a.get('bound') else {'status':'repair','repair_kind':'deterministic_compiler',
                                                               'feedback':'Bind the visits placeholder to the executed scalar column.'}
        r=self.run_loop([TOOL_REPLY,reply({'status':'answered'}),reply({'status':'answered','bound':True})],check)
        self.assertEqual([b['model'] for b in self.sent],[M.HAIKU]*3)
        self.assertEqual(len(self.executed),1);self.assertFalse(r.get('model_escalations'))
        self.assertTrue(r['answer']['bound'])

    def test_repeated_compiler_failure_still_escalates_and_never_bypasses_review(self):
        bad=reply({'status':'answered','headline':'An unbound quantity'})
        r=self.run_loop([bad,bad,bad],lambda a,c:{'status':'repair','repair_kind':'deterministic_compiler','feedback':'Same unbound scalar cell.'})
        self.assertEqual([b['model'] for b in self.sent],[M.HAIKU,M.HAIKU,M.SONNET])
        self.assertEqual(r['review_stopped'],'no_progress');self.assertEqual(r['answer']['status'],'partial')

    def test_changed_compiler_failures_can_be_repaired_without_a_fixed_retry_quota(self):
        stages=iter(['Bind visits','Bind dollars','Bind percentage',None])
        def review(a,c):
            issue=next(stages)
            return {'status':'confirmed'} if issue is None else {'status':'repair','repair_kind':'deterministic_compiler','feedback':issue}
        r=self.run_loop([reply({'status':'answered'})]*4,review)
        self.assertEqual([b['model'] for b in self.sent],[M.HAIKU]*4);self.assertFalse(r.get('model_escalations'))
        self.assertFalse(r.get('review_stopped'))

    def test_failed_meaning_check_escalates_same_conversation_without_reexecuting_tools(self):
        def check(a,c):
            self.assertEqual(len(c),1)
            return {'status':'confirmed'} if a['count']==7 else {'status':'repair','feedback':'Computed result is seven, not nine.'}
        r=self.run_loop([TOOL_REPLY,reply({'status':'answered','count':9}),reply({'status':'answered','count':7})],check)
        self.assertEqual([b['model'] for b in self.sent],[M.HAIKU,M.HAIKU,M.SONNET])
        self.assertEqual(len(self.executed),1);self.assertEqual(len(r['tool_calls']),1)
        self.assertIn('seven',self.sent[-1]['messages'][-1]['content'])
        self.assertTrue(any(isinstance(m['content'],list) and any(b.get('type')=='tool_result' for b in m['content'])
                            for m in self.sent[-1]['messages']))
        self.assertEqual(r['model_escalations'][0]['retained_tool_calls'],1)
        self.assertEqual(r['model'],M.SONNET);self.assertEqual(r['answer']['count'],7)

    def test_sonnet_still_cannot_pass_a_rejected_answer(self):
        bad=reply({'status':'answered','count':9})
        r=self.run_loop([TOOL_REPLY,bad,bad],lambda a,c:{'status':'repair','feedback':'Wrong computed value.'})
        self.assertEqual(r['review_stopped'],'no_progress');self.assertEqual(r['answer']['status'],'partial')
        self.assertEqual(len(self.executed),1);self.assertEqual(len(r['model_escalations']),1)

    def test_rewording_with_identical_failure_and_evidence_does_not_start_another_paid_turn(self):
        first=reply({'status':'answered','count':9,'headline':'Nine received patients'})
        second=reply({'status':'answered','count':9,'headline':'There are nine patients'})
        r=self.run_loop([TOOL_REPLY,first,second],lambda a,c:{'status':'repair','feedback':'Wrong computed value.'})
        self.assertEqual(r['review_stopped'],'no_progress')
        self.assertEqual([b['model'] for b in self.sent],[M.HAIKU,M.HAIKU,M.SONNET])
        self.assertEqual(len(self.executed),1);self.assertEqual(r['answer']['status'],'partial')

    def test_new_native_evidence_allows_continuation_despite_same_feedback(self):
        more=copy.deepcopy(TOOL_REPLY);more['content'][0]['id']='two';more['content'][0]['input']['sql']='SELECT 7 AS corrected_patients'
        wrong=reply({'status':'answered','count':9})
        r=self.run_loop([TOOL_REPLY,wrong,more,wrong,reply({'status':'answered','count':7})],
                        lambda a,c:{'status':'confirmed'} if a['count']==7 else {'status':'repair','feedback':'Wrong computed value.'})
        self.assertEqual(len(self.executed),2);self.assertEqual(r['answer']['count'],7)
        self.assertFalse(r.get('review_stopped'));self.assertEqual(len(self.sent),5)

    def test_new_query_views_with_identical_answer_dependencies_do_not_reset_review(self):
        sent=[];responses=iter([TOOL_REPLY,reply({'status':'partial','table_from_tool':'agere_result_1'}),TOOL_REPLY,reply({'status':'partial','table_from_tool':'agere_result_2'})])
        def send(body):sent.append(body);return next(responses),.001,False
        executed=[]
        def execute(use):
            executed.append(use);return {'result_view':'agere_result_'+str(len(executed)),'rows':[{'patient_id':'A'}],'columns':['patient_id'],'accounting':{'cost_usd':len(executed)}}
        r=S.tool_loop('Inspect this evidence',[],[{'name':'query_sql'}],execute,send=send,model=M.HAIKU,escalation_model=M.SONNET,max_tools=None,review=lambda a,c:{'status':'repair','feedback':'Current treatment unknown'})
        self.assertEqual(len(sent),4);self.assertEqual(r['review_stopped'],'no_progress')
        self.assertEqual(r['answer']['status'],'partial');self.assertEqual(len(r['tool_calls']),2)

    def test_verifier_outage_with_no_repairable_feedback_does_not_call_sonnet(self):
        r=self.run_loop([reply({'status':'partial','missing':['Support unavailable']})],lambda a,c:{'status':'unavailable','reason':'Verifier offline'})
        self.assertEqual([b['model'] for b in self.sent],[M.HAIKU])
        self.assertFalse(r.get('model_escalations'));self.assertEqual(r['answer']['status'],'partial')

    def test_uncertain_source_check_without_a_fault_does_not_promote_actor_or_pass_answer(self):
        support={'label':'unknown','failed_packet_ids':[],'claim_feedback':[],
                 'packets':[{'id':'patient_A','label':'unknown','decided_by':None}]}
        state=B.verification_repair_state({'label':'answers_partially'},support)
        r=self.run_loop([TOOL_REPLY,reply({'status':'answered','count':7}),reply({'status':'answered','count':7})],
            lambda a,c:{'status':'repair','repair_kind':'unresolved_verification','feedback':'Source check remains unresolved',
                        'progress_state':state})
        self.assertEqual([b['model']for b in self.sent],[M.HAIKU]*3)
        self.assertFalse(r.get('model_escalations'));self.assertEqual(len(self.executed),1)
        self.assertEqual(r['review_stopped'],'no_progress');self.assertEqual(r['answer']['verification'],'unverified')

    def test_resolved_source_check_can_pass_on_the_original_actor(self):
        reviews=iter([{'status':'repair','repair_kind':'unresolved_verification','feedback':'Inspect the pending source'},
                      {'status':'confirmed'}])
        r=self.run_loop([reply({'status':'partial'}),reply({'status':'answered'})],lambda a,c:next(reviews))
        self.assertEqual([b['model']for b in self.sent],[M.HAIKU]*2)
        self.assertEqual(r['answer']['status'],'answered');self.assertFalse(r.get('model_escalations'))

    def test_repeating_a_tool_after_unlocated_verifier_uncertainty_keeps_the_partial_result(self):
        r=self.run_loop([TOOL_REPLY,reply({'status':'answered','count':7}),TOOL_REPLY],
            lambda a,c:{'status':'repair','repair_kind':'unresolved_verification','feedback':'Source check unresolved'})
        self.assertFalse(r.get('model_escalations'));self.assertEqual(r['review_stopped'],'no_progress')
        self.assertEqual(r['answer']['verification'],'unverified');self.assertEqual(len(r['tool_calls']),2)

    def test_located_fault_or_different_question_is_not_an_indeterminate_check(self):
        clean={'label':'unknown','packets':[{'id':'patient_A','label':'unknown','decided_by':None}]}
        self.assertIsNotNone(B.verification_repair_state({'label':'answers_partially'},clean))
        for bad in [{**clean,'claim_feedback':[{'field':'dose','label':'contradicted'}]},
                    {**clean,'failed_packet_ids':['patient_A']},
                    {**clean,'packets':[{'id':'patient_A','label':'contradicted','decided_by':'jev'}]}]:
            self.assertIsNone(B.verification_repair_state({'label':'answers_partially'},bad))
        self.assertIsNone(B.verification_repair_state({'label':'answers_a_different_question'},clean))

    def test_provider_outage_is_not_a_reason_for_paid_model_retry(self):
        r=self.run_loop([TimeoutError('Provider usage unavailable')])
        self.assertEqual(len(self.sent),1);self.assertFalse(r.get('model_escalations'))
        self.assertIsNone(r['estimated_cost_usd']);self.assertIn('TimeoutError',r['error'])

    def test_invalid_answer_format_can_escalate_without_accepting_bad_json(self):
        bad={'content':[{'type':'text','text':'unparseable'}],'stop_reason':'end_turn','usage':{'input_tokens':1,'output_tokens':1}}
        r=self.run_loop([bad,bad,reply({'status':'answered','count':7})],lambda a,c:{'status':'confirmed'})
        self.assertEqual([b['model'] for b in self.sent],[M.HAIKU,M.HAIKU,M.SONNET])
        self.assertEqual(r['answer']['count'],7);self.assertEqual(self.executed,[])


class RoleBoundary(unittest.TestCase):
    def test_child_role_first_rung_restores_parent_after_return(self):
        with M.question_path(),M.escalation():
            self.assertEqual(M.claude_model(M.HAIKU),M.SONNET)
            with M.first_rung():self.assertEqual(M.claude_model(M.SONNET),M.HAIKU)
            self.assertEqual(M.claude_model(M.HAIKU),M.SONNET)

    def test_existing_cascade_starts_investigator_on_haiku_even_inside_escalated_parent(self):
        c=P.Cascade.__new__(P.Cascade)
        c.stack,c.question,c.population,c.log,c.deferred=SimpleNamespace(allow_live=True),'Explain the received evidence',None,[],[]
        c.catalog=SimpleNamespace(identity={'as_of':'2030-05-27'});c.usd,c.calls,c.cost_known=0.,0,True;c._ledger,c._owned=object(),False
        seen=[]
        def answer(*a,**kw):
            seen.append(M.claude_model(M.SONNET))
            return {'answer':{'status':'partial','answer_type':'note','headline':'Unresolved','investigation_review':{'status':'unavailable'}},
                    'model':seen[-1],'new_paid_api_calls':0,'estimated_cost_usd':0,'tool_calls':[]}
        with patch.object(P,'claude_available',return_value=True),patch.object(B,'answer',side_effect=answer),M.question_path(),M.escalation():
            c.tier_backstop_agent()
        self.assertEqual(seen,[M.HAIKU])


class ImmediateExecutor:
    """Execute on the parent context to expose inherited-rung bugs deterministically."""
    def __init__(self,*a,**kw):pass
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def submit(self,fn,*args):
        f=Future()
        try:f.set_result(fn(*args))
        except Exception as e:f.set_exception(e)
        return f


class Reader(unittest.TestCase):
    def setUp(self):
        from test_document_review import Review
        self.f=Review('test_all_whole_documents_and_all_patients_retained_with_quote_provenance')
        self.f.setUp();self.addCleanup(self.f.doCleanups)

    def test_verifier_failure_preserves_haiku_findings_and_unknown_without_sonnet(self):
        failed={'label':'unknown','decided_by':None,'status':'providers_failed','steps':[{'provider':'jev','status':'error','error':'HTTP400'}]}
        with patch('clinical_core.agere.clinical.together._ledgered_send',side_effect=self.f.sender),patch.object(O,'decide',return_value=failed):
            r=D.review(self.f.tools,['A','B'],{'barriers':'What financial barriers and changes are documented?'})
        self.assertEqual(len(self.f.bodies),1)
        self.assertEqual([x['review_status'] for x in r['rows']],['needs_review','no_documents'])
        self.assertTrue(json.loads(r['rows'][0]['review_json'])['barriers'])
        self.assertFalse(r['accounting']['api_cost_known'])
        self.assertEqual(sum(len(d['text']) for d in json.loads(self.f.bodies[0]['messages'][0]['content'])['whole_documents']),
                         len(self.f.docs[0][5])+len(self.f.docs[2][5]))

    def test_real_model_uncertainty_still_escalates(self):
        with patch('clinical_core.agere.clinical.together._ledgered_send',side_effect=self.f.sender),patch.object(O,'decide',return_value={'label':'supported','decided_by':None,'steps':[]}):
            r=D.review(self.f.tools,['A'],{'barriers':'What financial barriers are documented?'})
        self.assertEqual([x['model'] for x in self.f.bodies],[M.HAIKU,M.SONNET]);self.assertEqual(r['rows'][0]['review_status'],'needs_review')

    def test_nested_reader_first_request_is_really_haiku_and_restores_parent(self):
        observed=[]
        def factory(stack,ledger,receipts):
            send=self.f.sender(stack,ledger,receipts)
            def inner(body):observed.append(M.claude_model(body['model']));return send(body)
            return inner
        with patch.object(D,'ThreadPoolExecutor',ImmediateExecutor),patch('clinical_core.agere.clinical.together._ledgered_send',side_effect=factory),patch.object(O,'decide',return_value={'label':'supported','decided_by':'fixture','steps':[]}),M.question_path(),M.escalation():
            D.review(self.f.tools,['A'],{'barriers':'What financial barriers are documented?'})
            self.assertEqual(M.claude_model(M.HAIKU),M.SONNET)
        self.assertEqual(observed,[M.HAIKU])

    def test_verification_does_not_require_selecting_one_citation_for_a_multi_document_judgment(self):
        captured=[]
        def verify(*a,**kw):captured.append(kw);return {'label':'supported','decided_by':'fixture','steps':[]}
        with patch('clinical_core.agere.clinical.together._ledgered_send',side_effect=self.f.sender),patch.object(O,'decide',side_effect=verify):
            D.review(self.f.tools,['A'],{'barriers':'What barriers are documented?'})
        self.assertEqual([k.get('select_source') for k in captured],[False])


class TypedVerification(unittest.TestCase):
    def task(self,select_source=True):
        return EvidenceTask('Do these documents support the received closed claim?','2030-05-27',
                            {'supported':'Supported','unknown':'Unknown'},
                            [{'id':f'r{i}','note_id':f'n{i}','date':'2030-01-01','text':f'Complete source {i}'} for i in range(314)],
                            select_source=select_source)

    def test_closed_verifier_keeps_every_source_without_a_315_option_citation_question(self):
        p=JevProvider(None);task=self.task(False);request=p.request(task)
        self.assertEqual(set(request['questions']),{'reason'})
        self.assertEqual(request['state']['evidence'],task.evidence)
        class Gateway:
            def send(self,*a):return {'model':p.model,'answers':{'reason':{'type':'choice','choice':'supported','probabilities':{'supported':1.,'unknown':0.}}}}
        p.gateway=Gateway();r=p.classify(task)
        self.assertEqual(r['evidence_ids'],[]);self.assertFalse(r['uncertain']);self.assertEqual(r['label'],'supported')

    def test_source_selecting_callers_still_require_valid_citations(self):
        p=JevProvider(None);task=self.task();request=p.request(task)
        self.assertIn('support',request['questions'])
        class Gateway:
            def send(self,*a):return {'model':p.model,'answers':{'reason':{'type':'choice','choice':'supported','probabilities':{'supported':1.,'unknown':0.}}}}
        p.gateway=Gateway()
        with self.assertRaises(KeyError):p.classify(task)


if __name__=='__main__':unittest.main()
