"""Durable dialogue over actual native reads; authored model responses only."""
import copy,json,os,sqlite3,unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from unittest.mock import patch,Mock
from types import SimpleNamespace
from clinical_core.agere.clinical import clarification as I,conversation as C,backstop_agent as B,semantic_analyst as S,decision_context as D
from clinical_core.agere import model_policy as M
import test_shared_planner as fixture
REAL_RESUME=I.resume_investigator

QUESTION='Review the recorded patient roster and help me choose the presentation.'
SPEC={'slot':'presentation_goal','question':'Would you like a patient roster or an aggregate summary?',
      'reason':'The requested presentation is unclear; the received roster is already available.',
      'options':[{'id':'roster','label':'Patient roster','meaning':'Show the received patient rows.'},
                 {'id':'summary','label':'Aggregate summary','meaning':'Show a summary of the same recorded population.'}]}
def tool(ident,name,args):
    return {'content':[{'type':'tool_use','id':ident,'name':name,'input':args}],'stop_reason':'tool_use'}


class IntentClarification(unittest.TestCase):
    setUpBase=fixture.SharedPlanner.setUp

    def setUp(self):
        self.setUpBase();self.stack.retriever=SimpleNamespace(meta={'snapshot':{'as_of':'2030-05-27'}})
        self.stack.ledger_path=self.path/'provider-budget.sqlite3';self.stack.receipts=self.path/'provider-receipts'
        self.stack.allow_live=True;self.bodies=[];self.queries=[];self.review_calls=[]
        self.patches=ExitStack();self.addCleanup(self.patches.close)
        for target in ('socket.socket.connect','socket.create_connection','subprocess.Popen'):
            self.patches.enter_context(patch(target,side_effect=AssertionError('No network or model process in authored engineering fixture')))
        self.patches.enter_context(patch.dict(os.environ,{'AGERE_RECURSIVE_IMPROVEMENT':'off','AGERE_ANALYST_STATE':'off','AGERE_COMPONENT_LEARNING':'off'}))
        self.patches.enter_context(patch('clinical_core.agere.clinical.analyst_definitions.agere_context',return_value=('',None)))
        self.patches.enter_context(patch('clinical_core.agere.clinical.semantic_analyst.semantic_context',return_value=''))
        self.patches.enter_context(patch('clinical_core.agere.clinical.analyst_context.install',return_value=''))
        self.patches.enter_context(patch('clinical_core.agere.clinical.component_learning.shape_hint',return_value=None))
        self.patches.enter_context(patch.object(B,'conversation_turns',return_value=[]))
        self.patches.enter_context(patch.object(B,'review_final',side_effect=self.review))
        # A paused run is not a successful example or a learned bottling rule.
        self.propose=self.patches.enter_context(patch('clinical_core.agere.clinical.harness_improvement.attempt_propose',return_value=None))
        self.response_index=0
        call=B.Tools._call
        def observe(tools,use):
            if use['name']=='query_sql':self.queries.append(copy.deepcopy(use['input']))
            return call(tools,use)
        self.patches.enter_context(patch.object(B.Tools,'_call',observe))
        self.stack.compose=lambda q,**kw:self.run_actor(q)
        self.patches.enter_context(patch.object(I,'resume_investigator',side_effect=lambda stack,saved,reply,**kw:self.run_actor(saved['question'],saved,reply)))

    def review(self,*args,**kwargs):
        self.review_calls.append((args,kwargs))
        B.materialize_table(args[2],args[3])
        return {'status':'confirmed','calls':0,'cost_usd':0,'fixture':'Authored review acceptance; not a qualified model or clinical result'}

    def sender(self,body):
        self.bodies.append(copy.deepcopy(body));self.response_index+=1
        if self.response_index==1:
            response=tool('read','query_sql',{'sql':"SELECT patient_id FROM patient WHERE patient_id IN ('A','B') ORDER BY patient_id"})
        elif self.response_index==2:response=tool('intent','request_clarification',SPEC)
        else:
            response={'content':[{'type':'text','text':json.dumps({'status':'answered','answer_type':'table',
                'headline':'The retained roster is available for review.','facts':{},'table_from_tool':'agere_result_1','sources':[]})}],'stop_reason':'end_turn'}
        return response,.01,False

    def run_actor(self,question,saved=None,reply=None):
        with D.scope(question,'2030-05-27'):
            return B.answer(self.stack,question,send=self.sender,as_of='2030-05-27',model=M.HAIKU,
                            continuation=saved,clarification_reply=reply)

    def pause(self):
        result=C.handle_compose(self.stack,{'question':QUESTION,'conversation_id':'intent-thread','interaction_mode':'interactive'})
        self.assertEqual(result['answer']['status'],'clarify')
        self.assertFalse(result['clinical_execution_authorized']if'clinical_execution_authorized'in result else False)
        return result

    def respond(self,paused,**reply):
        return C.handle_compose(self.stack,{'conversation_id':'intent-thread','clarification_response':{'id':paused['clarification']['id'],**reply}})

    def test_pause_restart_resume_reuses_native_read_and_reviews_the_new_answer(self):
        paused=self.pause();self.assertEqual(len(self.queries),1);self.assertEqual(len(self.bodies),2)
        self.assertFalse(self.review_calls);self.propose.assert_not_called()
        self.assertNotIn('clarification_checkpoint',paused)
        public=paused['clarification'];self.assertFalse(public['source_facts_established'])
        self.assertEqual(public['retained_native_calls'],2)
        # Read a new database connection, as after a process restart.
        history=C.turns(self.path,'intent-thread');self.assertEqual(history[-1]['answer_summary']['clarification']['id'],public['id'])
        with patch('clinical_core.agere.clinical.learning_hooks.after_answer')as learned:
            resumed=self.respond(paused,option_id='roster')
        learned.assert_not_called();self.propose.assert_not_called()
        self.assertEqual(resumed['answer']['status'],'answered')
        self.assertEqual(resumed['answer']['table'],[{'patient_id':'A'},{'patient_id':'B'}])
        self.assertEqual(len(self.queries),1,'Completed source SQL must not execute again')
        self.assertEqual(len(self.bodies),3);self.assertEqual(len(self.review_calls),1)
        last=self.bodies[-1]['messages'][-1]['content'][0]
        self.assertEqual(last['tool_use_id'],'intent')
        choice=json.loads(last['content']);self.assertEqual(choice['response']['option']['id'],'roster')
        self.assertEqual(choice['authority'],'user_intent_only');self.assertFalse(choice['source_facts_established'])
        self.assertEqual(C.turns(self.path,'intent-thread')[0]['answer_summary']['clarification']['status'],'complete')
        # Same answer is idempotent even when a client resends after a timeout.
        duplicate=self.respond(paused,option_id='roster')
        self.assertEqual(duplicate['answer'],resumed['answer']);self.assertEqual(len(self.bodies),3)
        self.assertEqual(duplicate['clarification_replay']['new_paid_api_calls'],0)
        self.assertEqual(duplicate['new_paid_api_calls'],0)

    def test_source_change_is_rejected_before_any_provider_or_native_reexecution(self):
        paused=self.pause()
        with sqlite3.connect(self.path/'semantic.sqlite3')as db:db.execute("UPDATE meta SET value='changed-source' WHERE key='source_digest'")
        with self.assertRaisesRegex(ValueError,'changed; start a new analysis'):self.respond(paused,option_id='roster')
        self.assertEqual(len(self.bodies),2);self.assertEqual(len(self.queries),1)

    def test_reply_cannot_change_conversation_or_inject_native_patient_membership(self):
        paused=self.pause();ident=paused['clarification']['id']
        with self.assertRaisesRegex(ValueError,'another conversation'):
            C.handle_compose(self.stack,{'conversation_id':'foreign','clarification_response':{'id':ident,'option_id':'roster'}})
        with self.assertRaises(ValueError):
            C.handle_compose(self.stack,{'conversation_id':'intent-thread','clarification_response':{'id':ident,'option_id':'roster'},'patient_ids':['OUTSIDE']})
        with self.assertRaisesRegex(ValueError,'Unknown clarification option'):self.respond(paused,option_id='invented')
        self.assertEqual(len(self.bodies),2)

    def test_free_text_is_preserved_as_user_intent_not_warehouse_evidence(self):
        paused=self.pause();resumed=self.respond(paused,text='Please show the received roster.')
        self.assertFalse(resumed['clarification_history'][0]['source_facts_established'])
        self.assertEqual(resumed['clarification_history'][0]['response'],{'text':'Please show the received roster.'})
        with self.assertRaisesRegex(ValueError,'different response'):self.respond(paused,option_id='summary')

    def test_autonomous_mode_keeps_going_and_creates_no_pending_question(self):
        result=C.handle_compose(self.stack,{'question':QUESTION,'conversation_id':'away','interaction_mode':'autonomous'})
        self.assertEqual(result['answer']['status'],'partial')
        self.assertNotIn('clarification',result)
        self.assertEqual(len(self.bodies),3);self.assertEqual(len(self.queries),1)
        content=self.bodies[-1]['messages'][-1]['content'][0]
        intent=json.loads(content['content']);self.assertEqual(intent['interaction_mode'],'autonomous')
        self.assertIn('Continue independent work',intent['instruction'])
        self.propose.assert_not_called()

    def test_independent_work_completes_before_pause_and_only_affected_work_continues(self):
        # Two independent actual reads, one unanswered presentation choice,
        # then only the affected read. All earlier native rows survive restart.
        final={'content':[{'type':'text','text':json.dumps({'status':'answered','answer_type':'table',
            'headline':'The requested roster is available.','table_from_tool':'agere_result_4','sources':[]})}]}
        responses=[tool('roster','query_sql',{'sql':"SELECT patient_id FROM patient WHERE patient_id IN ('A','B') ORDER BY patient_id"}),
            tool('independent','query_sql',{'sql':'SELECT COUNT(*) AS roster_size FROM patient'}),
            tool('choice','request_clarification',SPEC),
            tool('affected','query_sql',{'sql':"SELECT patient_id FROM agere_result_1 WHERE patient_id='A'"}),final]
        def send(body):
            self.bodies.append(copy.deepcopy(body));return responses.pop(0),.01,False
        with patch.object(self,'sender',side_effect=send):
            paused=self.pause();self.assertEqual(len(self.queries),2)
            resumed=self.respond(paused,text='Show only A from the retained roster.')
        self.assertEqual(len(self.queries),3)
        self.assertEqual(resumed['answer']['table'],[{'patient_id':'A'}])
        self.assertEqual(len(self.bodies),5);self.assertFalse(responses)

    def test_changed_execution_policy_cannot_resume_old_evidence(self):
        paused=self.pause()
        with patch.dict(os.environ,{'AGERE_DOCUMENT_FIELD_SCREEN':'changed-policy'}):
            with self.assertRaisesRegex(ValueError,'changed; start a new analysis'):self.respond(paused,option_id='roster')
        self.assertEqual(len(self.bodies),2)

    def test_original_delivery_preference_survives_the_reply(self):
        from clinical_core.agere.deliverables import workflow
        paused=C.handle_compose(self.stack,{'question':QUESTION,'conversation_id':'intent-thread','delivery_generation':'frontier'})
        with patch.object(workflow,'fulfill',wraps=workflow.fulfill)as fulfill:
            self.respond(paused,option_id='roster')
        self.assertEqual(fulfill.call_args.kwargs['generation'],'frontier')

    def test_sequential_intent_choices_retain_history_and_phase_accounting(self):
        original=self.sender
        spec=copy.deepcopy(SPEC);spec['slot']='detail_level';spec['question']='Which level of detail do you want?'
        def send(body):
            if self.response_index==2:
                self.response_index+=1;self.bodies.append(copy.deepcopy(body))
                return tool('detail','request_clarification',spec),.01,False
            return original(body)
        with patch.object(self,'sender',side_effect=send):
            first=self.pause();second=self.respond(first,option_id='roster')
            final=self.respond(second,text='Retain the original roster detail.')
        self.assertEqual([h['slot']for h in final['clarification_history']],['presentation_goal','detail_level'])
        self.assertEqual(len(self.queries),1)
        self.assertEqual(len(self.bodies),4)
        self.assertTrue(all('previous_phase_accounting'in h for h in final['clarification_history']))

    def test_a_resolved_intent_slot_cannot_interrupt_the_same_analysis_again(self):
        original=self.sender
        def send(body):
            if self.response_index==2:
                self.response_index+=1;self.bodies.append(copy.deepcopy(body))
                return tool('again','request_clarification',SPEC),.01,False
            return original(body)
        with patch.object(self,'sender',side_effect=send):
            first=self.pause();final=self.respond(first,option_id='roster')
        self.assertEqual(final['answer']['status'],'answered')
        self.assertNotIn('clarification',final)
        self.assertIn('already clarified',self.bodies[-1]['messages'][-1]['content'][0]['content'])
        self.assertEqual(len(self.queries),1)

    def test_failed_continuation_preserves_partial_and_never_resends(self):
        paused=self.pause();failure=RuntimeError('Authored subscription-limit failure')
        failure.partial_result={'retained_native_prefix':True,'partial_text':'Unfinished reasoning is preserved.'}
        with patch.object(I,'resume_investigator',side_effect=failure):
            with self.assertRaisesRegex(RuntimeError,'subscription-limit'):self.respond(paused,option_id='roster')
        with self.assertRaisesRegex(ValueError,'already running or failed'):self.respond(paused,option_id='roster')
        self.assertEqual(len(self.bodies),2)
        with sqlite3.connect(C._store(self.path))as db:
            status,result,error=db.execute('SELECT status,result,error FROM intent_clarification_runs').fetchone()
        self.assertEqual(status,'failed');self.assertEqual(json.loads(result),failure.partial_result)
        self.assertIn('subscription-limit',error)
        self.assertEqual(C.turns(self.path,'intent-thread')[0]['answer_summary']['clarification']['status'],'failed')

    def test_concurrent_duplicate_reply_claims_only_one_execution(self):
        paused=self.pause();ident=paused['clarification']['id']
        def claim(_):
            try:I.claim(self.path,'intent-thread',ident,{'option_id':'roster'});return 'claimed'
            except ValueError:return 'preserved'
        with ThreadPoolExecutor(max_workers=2)as pool:results=list(pool.map(claim,range(2)))
        self.assertEqual(sorted(results),['claimed','preserved'])

    def test_actual_cascade_resume_reaches_existing_final_answer_gate(self):
        from clinical_core.agere.clinical import proposers as P
        paused=self.pause();original=B.answer;gate=P.answer_gate
        def answer(stack,question,**kw):return original(stack,question,send=self.sender,model=M.HAIKU,as_of='2030-05-27',**kw)
        with patch.object(I,'resume_investigator',side_effect=REAL_RESUME),patch.object(B,'answer',side_effect=answer),\
             patch.object(P,'claude_available',return_value=True),patch.object(P,'answer_gate',wraps=gate)as checked:
            resumed=self.respond(paused,option_id='roster')
        self.assertTrue(checked.called,'Existing final answer gate must run on a clarified answer')
        self.assertEqual(len(self.queries),1)
        self.assertEqual(resumed['answer']['table'],[{'patient_id':'A'},{'patient_id':'B'}])


class ReceivedInputGuard(unittest.TestCase):
    def test_raw_source_changes_invalidate_exact_scope_and_derived_cache_does_not(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory()as tmp:
            path=Path(tmp)
            with sqlite3.connect(path/'stack.sqlite3')as db:
                db.executescript('CREATE TABLE patients(id TEXT PRIMARY KEY,payload TEXT);CREATE TABLE records(id TEXT PRIMARY KEY,patient TEXT,payload TEXT);CREATE TABLE analyses(patient TEXT,operator TEXT,payload TEXT);')
                db.executemany('INSERT INTO patients VALUES (?,?)',[('A','{}'),('B','{}')])
                db.execute("INSERT INTO records VALUES ('RA','A','original')")
            before=I.input_guard(path,['A']);whole=I.input_guard(path,None)
            with sqlite3.connect(path/'stack.sqlite3')as db:
                db.execute("INSERT INTO analyses VALUES ('A','unrelated_cache','derived')")
                db.execute("INSERT INTO records VALUES ('RB','B','independent source')")
            self.assertEqual(before,I.input_guard(path,['A']))
            self.assertNotEqual(whole,I.input_guard(path,None))
            with sqlite3.connect(path/'stack.sqlite3')as db:db.execute("UPDATE records SET payload='changed' WHERE id='RA'")
            self.assertNotEqual(before,I.input_guard(path,['A']))


class TypedIntentTool(unittest.TestCase):
    def test_invalid_requests_do_not_create_an_interrupt(self):
        for changed in [{},dict(SPEC,options=[]),dict(SPEC,options=[SPEC['options'][0]]*2),dict(SPEC,slot='')]:
            with self.subTest(changed=changed),self.assertRaises(ValueError):I.request(changed)

    def test_mixed_clarification_batch_executes_neither_the_question_nor_other_tools(self):
        execute=Mock(side_effect=AssertionError('Mixed ask/read must not execute'))
        calls=0
        def send(body):
            nonlocal calls;calls+=1
            if calls==1:return {'content':[tool('c','request_clarification',SPEC)['content'][0],tool('q','query_sql',{'sql':'SELECT 1'})['content'][0]]},0,False
            return {'content':[{'type':'text','text':json.dumps({'status':'partial','headline':'Intent remains unresolved.'})}]},0,False
        out=S.tool_loop('request','instructions',[I.TOOL],execute,send=send,model=M.HAIKU,max_tools=None)
        execute.assert_not_called();self.assertNotIn('clarification_pause',out)
        self.assertTrue(all(c['result'].get('error')for c in out['tool_calls']))


if __name__=='__main__':unittest.main()
