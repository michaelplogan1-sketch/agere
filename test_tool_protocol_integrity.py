"""Literal tool protocol recovery and final-answer separation, using authored inputs."""
import json,unittest
from unittest.mock import patch
from clinical_core.agere import subscription_transport as T
from clinical_core.agere.clinical import semantic_analyst as S,backstop_agent as B

TOOLS=[{'name':'query_sql'},{'name':'review_documents'}]
CALLS=[{'name':'query_sql','input':{'sql':'SELECT 1','complete':True}},
       {'name':'review_documents','input':{'patient_query_sql':'SELECT patient_id FROM selected_patients','questions':{'reason':'Quote a documented reason, if any.'}}}]

def response(text,stop='end_turn'):
    return {'content':[{'type':'text','text':text}], 'stop_reason':stop,'usage':{'input_tokens':10,'output_tokens':10}}


class Protocol(unittest.TestCase):
    def test_empty_unregistered_padding_preserves_every_literal_batch_argument(self):
        text='<invoke name="unused_padding">\n</invoke>\n<invoke name="another_empty_tag"></invoke>\n'+json.dumps({'tool_uses':CALLS})
        self.assertEqual(T._tool_calls(text,TOOLS),CALLS)
        out=T.normalize_tool_response(response(text),TOOLS)
        self.assertEqual([{k:b[k] for k in ('name','input')} for b in out['content']],CALLS)
        self.assertEqual(len({b['id'] for b in out['content']}),2)
        self.assertEqual(out['usage'],{'input_tokens':10,'output_tokens':10})

    def test_nonempty_unknown_action_is_not_silently_discarded(self):
        text='<invoke name="unregistered"><parameter name="value">1</parameter></invoke>\n'+json.dumps({'tool_uses':CALLS})
        self.assertEqual(T._tool_calls(text,TOOLS),[])

    def test_registered_xml_and_second_json_envelope_are_ambiguous(self):
        text='<invoke name="query_sql"><parameter name="sql">SELECT 9</parameter></invoke>\n'+json.dumps({'tool_uses':CALLS})
        self.assertEqual(T._tool_calls(text,TOOLS),[])

    def test_unknown_batch_member_rejects_whole_batch(self):
        self.assertEqual(T._tool_calls(json.dumps({'tool_uses':[CALLS[0],{'name':'unknown','input':{}}]}),TOOLS),[])

    def test_answer_data_is_not_reinterpreted_as_an_action(self):
        data={'value':json.dumps({'tool_uses':CALLS})}
        out=response(json.dumps(data))
        self.assertIs(T.normalize_tool_response(out,TOOLS),out)
        self.assertEqual(T._tool_calls(json.dumps({'tool_uses':CALLS,'value':'not an action envelope'}),TOOLS),[])

    def test_nonfinite_batch_arguments_are_not_executed(self):
        self.assertEqual(T._tool_calls('{"tool_uses":[{"name":"query_sql","input":{"sql":NaN}}]}',TOOLS),[])

    def test_truncated_tool_response_never_executes_any_action(self):
        out=response(json.dumps({'tool_uses':CALLS}),'max_tokens')
        self.assertIs(T.normalize_tool_response(out,TOOLS),out)
        execute=[]
        r=S.tool_loop('authored question',[],TOOLS,lambda use:execute.append(use),send=lambda body:(out,.01,False),model='authored',max_tools=None)
        self.assertEqual(execute,[]);self.assertIn('truncated',r['error'])

    def test_plain_text_padded_batch_uses_same_native_guarded_executor(self):
        emitted=iter([response('<invoke name="empty_format_padding"></invoke>\n'+json.dumps({'tool_uses':CALLS})),response(json.dumps({'status':'answered','value':'authored result'}))])
        executed=[];sent=[]
        def send(body):sent.append(body);return next(emitted),.01,False
        def execute(use):executed.append({'name':use['name'],'input':use['input']});return {'value':'received authored data'}
        r=S.tool_loop('authored question',[],TOOLS,execute,send=send,model='authored',max_tools=None,continuation_guard=lambda:{'source':'one','scope':['p1']})
        self.assertEqual(executed,CALLS)
        self.assertEqual(len(r['tool_calls']),2);self.assertEqual(len(r['guarded_continuations']),1)
        self.assertEqual(len(sent),2);self.assertEqual(r['new_paid_api_calls'],2)
        self.assertEqual(r['answer']['value'],'authored result')

    def test_changed_scope_still_blocks_later_resolved_action(self):
        emitted=iter([response(json.dumps({'tool_uses':CALLS})),response(json.dumps({'status':'partial','value':'scope changed'}))])
        scope=['p1'];executed=[]
        def execute(use):executed.append(use['name']);scope.append('p2');return {'value':1}
        r=S.tool_loop('authored question',[],TOOLS,execute,send=lambda body:(next(emitted),.01,False),model='authored',max_tools=None,continuation_guard=lambda:{'scope':scope.copy()})
        self.assertEqual(executed,['query_sql'])
        self.assertIn('Resolved continuation not executed',r['tool_calls'][1]['result']['error'])

    def test_bad_tool_envelope_gets_protocol_feedback_without_clinical_review(self):
        emitted=iter([response(json.dumps({'tool_uses':[{'name':'unknown','input':{}}]})),response(json.dumps({'status':'partial','value':'missing supported tool'}))])
        review=[];executed=[];sent=[]
        def send(body):sent.append(body);return next(emitted),.01,False
        def check(answer,calls):review.append(answer);return {'status':'confirmed'}
        r=S.tool_loop('authored question',[],TOOLS,lambda use:executed.append(use),send=send,model='authored',max_tools=None,review=check)
        self.assertEqual(executed,[]);self.assertEqual(len(review),1)
        self.assertNotIn('tool_uses',review[0]);self.assertEqual(len(r['protocol_repairs']),1)
        self.assertIn('not a valid executable tool envelope',sent[1]['messages'][-1]['content'])

    def test_repeated_unexecuted_payload_stops_without_publishing_a_false_answer(self):
        out=response(json.dumps({'tool_uses':[{'name':'unknown','input':{}}]}));sent=[]
        def send(body):sent.append(body);return out,.01,False
        r=S.tool_loop('authored question',[],TOOLS,lambda use:None,send=send,model='authored',max_tools=None)
        self.assertEqual(len(sent),2);self.assertIn('repeated unexecuted tool request',r['error'])
        self.assertFalse(r.get('answer'))

    def test_review_cannot_accept_pending_actions_as_a_clinical_answer(self):
        with patch.object(B,'materialize_table',side_effect=AssertionError('No clinical evidence review for an action envelope')):
            r=B.review_final(None,'question',{'tool_uses':CALLS},[],None,ledger=None,as_of='2026-09-30')
        self.assertEqual(r['status'],'repair');self.assertIn('Unexecuted tool requests',r['feedback']);self.assertEqual(r['calls'],0)


if __name__=='__main__':unittest.main()
