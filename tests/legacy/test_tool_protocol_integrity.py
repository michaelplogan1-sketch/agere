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
    def test_compact_protocol_repair_keeps_mixed_draft_without_accepting_actions(self):
        draft={'status':'partial','row_prose':{'rows':[{'key':{'patient_id':'A'},
            'fields':{'explanation':'Retained source interpretation'}}]}}
        mixed='<invoke name="query_sql"><parameter name="sql">SELECT 1</parameter></invoke>\n'+json.dumps(draft)
        native=[{'name':'query_sql','input':{'sql':'SELECT patient_id FROM cohort'},
                 'result':{'result_view':'cohort','columns':['patient_id'],'rows':[['A']]}}]
        sent=[];reviewed=[];executed=[]
        def send(body):
            sent.append(body)
            if len(sent)==1:return response(mixed),0.,True
            state=json.loads(body['messages'][0]['content'])
            if len(sent)==3:
                self.assertNotIn('pending_protocol_response',state)
                return response(json.dumps(draft)),0.,True
            retained=state['pending_protocol_response']
            self.assertEqual(retained['content'],response(mixed)['content'])
            self.assertFalse(retained['tool_actions_executed']);self.assertFalse(retained['accepted_answer'])
            self.assertEqual(len(state['executed_results']),1)
            return response(json.dumps({'tool_use':{'name':'query_sql','input':{'sql':'SELECT 2'}}})),0.,True
        result=S.tool_loop('Explain cohort',[],TOOLS,
            lambda use:executed.append(use['input'])or{'result_view':'v2','columns':['v'],'rows':[[2]]},
            send=send,model='authored',max_tools=None,initial_calls=native,compact_execution_state=True,
            review=lambda answer,calls:reviewed.append(answer)or{'status':'confirmed'})
        self.assertEqual(executed,[{'sql':'SELECT 2'}]);self.assertEqual(reviewed,[draft])
        self.assertEqual(result['answer'],draft);self.assertEqual(result['tool_calls'][:1],native)
        self.assertEqual(result['protocol_repair_responses'][0]['content'],response(mixed)['content'])

    def test_protocol_draft_survives_provider_failure_without_becoming_answer(self):
        mixed='<invoke name="unknown"><parameter name="x">1</parameter></invoke>\n{"status":"answered","value":99}'
        sent=[]
        def send(body):
            sent.append(body)
            if len(sent)==1:return response(mixed),.01,False
            self.assertIn(mixed,json.loads(body['messages'][0]['content'])['pending_protocol_response']['content'][0]['text'])
            raise RuntimeError('fixture provider unavailable')
        result=S.tool_loop('Question',[],TOOLS,lambda use:self.fail('Unknown action must remain inert'),
            send=send,model='authored',max_tools=None,compact_execution_state=True)
        self.assertIn('fixture provider unavailable',result['error'])
        self.assertFalse(result.get('answer'));self.assertFalse(result['tool_calls'])
        self.assertEqual(result['protocol_repair_responses'][0]['content'],response(mixed)['content'])

    def test_empty_unknown_prefix_preserves_complete_final_draft_for_full_review(self):
        draft={'status':'partial','headline':'Retained interpretation','row_prose':{'keys':['patient_id'],
            'rows':[{'key':{'patient_id':'A'},'basis':[],'fields':{'explanation':'Current role remains unknown.'}}]}}
        text='<invoke name="noop">\n</invoke>\n```json\n'+json.dumps(draft)+'\n```'
        reviewed=[]
        result=S.tool_loop('Explain the patient',[],TOOLS,lambda use:self.fail('Empty tag is not executed'),
            send=lambda body:(response(text),0.,False),model='authored',max_tools=None,
            review=lambda a,c:reviewed.append(a)or{'status':'confirmed'})
        self.assertEqual(reviewed,[draft]);self.assertEqual(result['answer'],draft)
        self.assertEqual(result['format_repair'],'empty_unregistered_prefix')
        self.assertEqual(result['final_text'],text)

    def test_empty_prefix_normalization_keeps_actions_malformed_markup_and_literal_data(self):
        answer=json.dumps({'status':'partial','value':'<invoke name="noop"></invoke>'})
        for prefix in ('<invoke name="query_sql"></invoke>', '<invoke name="tool_use"></invoke>',
                       '<invoke name="noop">x</invoke>', '<invoke name="noop">',
                       '<invoke name="noop"><parameter name="x">1</parameter></invoke>'):
            text=prefix+answer
            with self.subTest(prefix=prefix):self.assertEqual(T.strip_empty_unregistered_prefix(text,TOOLS),text)
        self.assertEqual(T.strip_empty_unregistered_prefix(answer,TOOLS),answer)
        self.assertEqual(T.strip_empty_unregistered_prefix(answer+'<invoke name="noop"></invoke>',TOOLS),answer+'<invoke name="noop"></invoke>')

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

    def test_same_xml_and_json_request_executes_once_per_actor_turn(self):
        action={'name':'query_sql','input':{'sql':'SELECT * FROM agere_result_32'}}
        xml='<invoke name="query_sql">\n<parameter name="sql">SELECT * FROM agere_result_32</parameter>\n</invoke>'
        companion=json.dumps({'tool_use':action})
        for text in (xml+'\n\n'+companion,companion+'\n\n'+xml):
            with self.subTest(text=text):
                self.assertEqual(T._tool_calls(text,TOOLS),[action])
                first=T.normalize_tool_response(response(text),TOOLS)
                second=T.normalize_tool_response(response(text),TOOLS)
                self.assertNotEqual(first['content'][0]['id'],second['content'][0]['id'])
                emitted=iter([response(text),response('{"status":"answered","value":1}')])
                executed=[]
                result=S.tool_loop('Inspect the native result',[],TOOLS,
                    lambda use:executed.append(use['input']) or {'value':1},
                    send=lambda body:(next(emitted),.01,False),model='authored',max_tools=None)
                self.assertEqual(executed,[action['input']])
                self.assertEqual(len(result['tool_calls']),1)
                self.assertEqual(result['answer']['value'],1)

    def test_equivalent_mixed_batch_retains_order_and_distinct_actions(self):
        actions=[{'name':'query_sql','input':{'sql':'SELECT '+str(n)}} for n in (1,2)]
        xml=''.join('<invoke name="query_sql"><parameter name="sql">'+a['input']['sql']+'</parameter></invoke>' for a in actions)
        self.assertEqual(T._tool_calls(xml+json.dumps({'tool_uses':actions}),TOOLS),actions)
        # Repeated actions in the same declared encoding remain separate calls.
        self.assertEqual(T._tool_calls(json.dumps({'tool_uses':[actions[0],actions[0]]}),TOOLS),[actions[0],actions[0]])

    def test_mixed_companion_cannot_change_arguments_permissions_or_final_answer(self):
        xml='<invoke name="query_sql"><parameter name="sql">SELECT 1</parameter></invoke>'
        for companion in (
            {'tool_use':{'name':'query_sql','input':{'sql':'SELECT 2'}}},
            {'tool_use':{'name':'unregistered','input':{'sql':'SELECT 1'}}},
            {'status':'answered','value':1},
        ):
            text=xml+json.dumps(companion)
            self.assertEqual(T._tool_calls(text,TOOLS),[])
            self.assertEqual(T._tool_calls(json.dumps(companion)+xml,TOOLS),[])
        out=T.normalize_tool_response(response(xml+json.dumps({'tool_use':{'name':'query_sql','input':{'sql':'SELECT 1'}}})),TOOLS,{'type':'none'})
        self.assertEqual(out['content'][0]['type'],'text')
        numeric='<invoke name="query_sql"><parameter name="complete">1</parameter></invoke>'
        self.assertEqual(T._tool_calls(numeric+json.dumps({'tool_use':{'name':'query_sql','input':{'complete':True}}}),TOOLS),[])

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
