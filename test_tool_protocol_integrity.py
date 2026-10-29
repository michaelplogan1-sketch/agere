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
    def test_prose_and_trailing_transport_marker_do_not_hide_a_literal_action(self):
        prefaced='I will inspect the completed results.\n\n'+json.dumps({'tool_use':CALLS[0]})
        self.assertEqual(T._tool_calls(prefaced,TOOLS),[CALLS[0]])
        native='<invoke name="query_sql"><parameter name="sql">SELECT 1</parameter></invoke>\n_end'
        self.assertEqual(T._tool_calls(native,TOOLS),[{'name':'query_sql','input':{'sql':'SELECT 1'}}])
        self.assertEqual(T._tool_calls(prefaced+'\n'+json.dumps({'tool_use':CALLS[1]}),TOOLS),CALLS)

    def test_native_metadata_is_inert_and_markup_inside_literal_arguments_is_data(self):
        action={'name':'query_sql','input':{'sql':'SELECT \'<invoke name="unknown">literal source</invoke>\' AS text'},'type':'tool_use','id':'model_call_1'}
        self.assertEqual(T._tool_calls(json.dumps({'tool_use':action}),TOOLS),[{k:action[k]for k in ('name','input')}])
        self.assertEqual(T._tool_calls(json.dumps({'tool_use':{**action,'type':'different'}}),TOOLS),[])
        self.assertEqual(T._tool_calls(json.dumps({'tool_use':CALLS[0]})+'\n'+json.dumps({'status':'answered','value':1}),TOOLS),[])

    def test_final_json_followed_by_pending_xml_is_repaired_without_silently_dropping_it(self):
        text=json.dumps({'status':'answered','value':1})+'\n<invoke name="query_sql"><parameter name="sql">SELECT 2</parameter></invoke>'
        replies=iter([response(text),response(json.dumps({'status':'partial','missing':['Pending work remains']}))]);reviewed=[]
        result=S.tool_loop('Annual payments',[],TOOLS,lambda use:self.fail('Ambiguous mixed response must not execute'),send=lambda body:(next(replies),.01,False),model='authored',max_tools=None,
            review=lambda answer,calls:reviewed.append(answer)or{'status':'confirmed'})
        self.assertEqual(len(reviewed),1);self.assertEqual(reviewed[0]['status'],'partial')
        self.assertEqual(len(result['protocol_repairs']),1)

    def test_wrapped_xml_tool_request_preserves_exact_literal_arguments(self):
        call={'name':'query_sql','input':{'complete':True,'sql':"SELECT '; ' AS label, 2030 AS year"}}
        text='<invoke name="tool_use"><parameter name="name">query_sql</parameter><parameter name="input">'+json.dumps(call['input'])+'</parameter></invoke>'
        self.assertEqual(T._tool_calls(text,TOOLS),[call])
        emitted=iter([response(text),response(json.dumps({'status':'answered','value':2030}))]);executed=[]
        result=S.tool_loop('Annual plan payments',[],TOOLS,lambda use:executed.append(use['input'])or{'value':2030},send=lambda body:(next(emitted),.01,False),model='authored',max_tools=None)
        self.assertEqual(executed,[call['input']]);self.assertEqual(result['answer']['value'],2030)
        self.assertEqual(len(result['tool_calls']),1);self.assertEqual(result['new_paid_api_calls'],2)

    def test_every_literal_xml_action_is_retained_in_a_batch(self):
        text=''.join('<invoke name="query_sql"><parameter name="sql">SELECT '+str(v)+'</parameter></invoke>'for v in (1,2))
        self.assertEqual(T._tool_calls(text,TOOLS),[{'name':'query_sql','input':{'sql':'SELECT 1'}},{'name':'query_sql','input':{'sql':'SELECT 2'}}])

    def test_unknown_xml_never_extracts_its_inner_sql_as_a_final_answer(self):
        text='<invoke name="unknown"><parameter name="input">{"sql":"SELECT 1","complete":true}</parameter></invoke>'
        replies=iter([response(text),response(json.dumps({'status':'partial','missing':['Supported action needed']}))]);reviewed=[];executed=[]
        result=S.tool_loop('Admissions and cost',[],TOOLS,lambda use:executed.append(use),send=lambda body:(next(replies),.01,False),model='authored',max_tools=None,
            review=lambda answer,calls:reviewed.append(answer)or{'status':'confirmed'})
        self.assertEqual(executed,[]);self.assertEqual(len(reviewed),1);self.assertNotIn('sql',reviewed[0])
        self.assertEqual(len(result['protocol_repairs']),1);self.assertEqual(result['answer']['status'],'partial')

    def test_duplicate_parameters_unclosed_or_unregistered_wrapped_actions_decline(self):
        samples=[
            '<invoke name="query_sql"><parameter name="sql">SELECT 1</parameter><parameter name="sql">SELECT 2</parameter></invoke>',
            '<invoke name="query_sql"><parameter name="sql">SELECT 1</parameter>',
            '<invoke name="tool_use"><parameter name="name">unknown</parameter><parameter name="input">{}</parameter></invoke>',
            '<invoke name="tool_use"><parameter name="name">[]</parameter><parameter name="input">{}</parameter></invoke>']
        for text in samples:
            with self.subTest(text=text):self.assertEqual(T._tool_calls(text,TOOLS),[])

    def test_xml_inside_a_final_answer_string_is_data_not_an_action(self):
        text=json.dumps({'status':'answered','value':'Literal source: <invoke name="query_sql"><parameter name="sql">SELECT 1</parameter></invoke>'})
        self.assertEqual(T._tool_calls(text,TOOLS),[])
        self.assertEqual(T._tool_calls(json.dumps({'tool_use':CALLS[0],'status':'answered'}),TOOLS),[])

    def test_bare_tool_arguments_are_not_confirmed_as_an_answer(self):
        with patch.object(B,'materialize_table',side_effect=AssertionError('Not a clinical answer')):
            result=B.review_final(None,'question',{'sql':'SELECT 1','complete':True},[],None,ledger=None,as_of='2030-01-01')
        self.assertEqual(result['status'],'repair');self.assertIn('Tool arguments alone',result['feedback'])

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

    def test_complete_cli_wrapper_preserves_literal_actions_and_current_permissions(self):
        text='<invoke_tool>\n'+json.dumps({'tool_use':CALLS[0]})+'\n</invoke_tool>'
        self.assertEqual(T._tool_calls(text,TOOLS),[CALLS[0]])
        self.assertEqual(T.normalize_tool_response(response(text),TOOLS)['content'][0]['input'],CALLS[0]['input'])
        self.assertEqual(T.normalize_tool_response(response(text),TOOLS,{'type':'none'}).get('stop_reason'),'end_turn')
        for bad in (text+'{"value":1}',text.replace('</invoke_tool>',''),'<invoke_tool><unregistered>'+json.dumps({'tool_use':CALLS[0]})+'</unregistered></invoke_tool>'):
            with self.subTest(text=bad):self.assertEqual(T._tool_calls(bad,TOOLS),[])
        data=json.dumps({'value':text});self.assertEqual(T._tool_calls(data,TOOLS),[])

    def test_unknown_batch_member_rejects_whole_batch(self):
        self.assertEqual(T._tool_calls(json.dumps({'tool_uses':[CALLS[0],{'name':'unknown','input':{}}]}),TOOLS),[])

    def test_answer_data_is_not_reinterpreted_as_an_action(self):
        data={'value':json.dumps({'tool_uses':CALLS})}
        out=response(json.dumps(data))
        self.assertIs(T.normalize_tool_response(out,TOOLS),out)
        self.assertEqual(T._tool_calls(json.dumps({'tool_uses':CALLS,'value':'not an action envelope'}),TOOLS),[])

    def test_disabled_tools_remain_inert_for_text_and_native_cached_responses(self):
        disabled={'type':'none'}
        text=response(json.dumps({'tool_uses':CALLS}))
        self.assertIs(T.normalize_tool_response(text,TOOLS,disabled),text)
        native={**text,'content':[{'type':'tool_use','id':'a','name':CALLS[0]['name'],'input':CALLS[0]['input']}]}
        out=T.normalize_tool_response(native,TOOLS,disabled)
        self.assertTrue(out['tool_permission_rejected']);self.assertTrue(all(b['type']=='text'for b in out['content']))

    def test_format_repair_cannot_execute_a_tool_when_choice_is_none(self):
        emitted=iter([response('invalid answer'),response(json.dumps({'tool_use':CALLS[0]})),response('{"status":"partial"}')])
        sent=[];executed=[]
        def send(body):sent.append(body);return next(emitted),.01,False
        r=S.tool_loop('authored question',[],TOOLS,lambda use:executed.append(use),send=send,model='authored',max_tools=None,format_retries=1)
        self.assertEqual(sent[1]['tool_choice'],{'type':'none'});self.assertEqual(executed,[])
        self.assertEqual(r['answer']['status'],'partial')

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
