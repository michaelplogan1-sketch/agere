"""The subscription transport (test runs on the Claude Code CLI): request translation, emulated tool use, and the urlopen intercept. No CLI is run."""
import json
import os
import subprocess
import unittest
import urllib.request
from unittest.mock import patch

from clinical_core.agere import subscription_transport as T


class Translation(unittest.TestCase):
    def test_compiled_state_is_sent_as_exact_blocks_with_one_boundary_only_when_enabled(self):
        from clinical_core.agere.clinical import execution_state as E
        def state(n):
            return json.dumps({'schema':E.VERSION,'original_request':'Keep every patient; né 2030-01-01.',
                'executed_results':[{'call':i,'observation':{'rows':[{'patient_id':str(i),'unknown':None}]}}for i in range(n)],
                'latest_results':[{'name':'query_sql','turn':n}],'instruction':'unchanged'},default=str,allow_nan=False)
        first,second=E.cache_blocks(state(3)),E.cache_blocks(state(5))
        for text,blocks in ((state(3),first),(state(5),second)):
            self.assertEqual(''.join(b['text']for b in blocks),text)
            self.assertEqual([i for i,b in enumerate(blocks)if'cache_control'in b],[len(blocks)-2])
        # A longer catalog starts with the earlier turn's blocks up to its boundary: that prefix is what the provider reads from cache.
        self.assertEqual([b['text']for b in second[:3]],[b['text']for b in first[:3]])
        for other in ('not JSON',json.dumps({'schema':'other/1','executed_results':[{}]}),json.dumps({'schema':E.VERSION,'executed_results':[]}),
                      json.dumps(json.loads(state(2)),separators=(',',':'))):
            self.assertIsNone(E.cache_blocks(other))
        body={'model':'claude-sonnet-5-5','messages':[{'role':'user','content':state(3)}]}
        raw={'result':'done','usage':{'input_tokens':2,'output_tokens':1,'cache_read_input_tokens':9}}
        with patch.object(T,'run_cli',return_value=raw)as call:
            off=T.respond(body)
            self.assertNotIn('user_blocks',call.call_args.kwargs);self.assertNotIn('prefix_cache',off['transport_details'])
            with patch.dict(os.environ,{'AGERE_SUBSCRIPTION_PREFIX_CACHE':'on'}):
                on=T.respond(body)
                self.assertEqual(call.call_args.kwargs['user_blocks'],first);self.assertEqual(call.call_args.args[2],state(3))
                self.assertEqual(on['transport_details']['prefix_cache']['text_blocks'],4);self.assertEqual(on['usage']['cache_read_input_tokens'],9)
                T.respond({**body,'messages':[*body['messages'],{'role':'assistant','content':'x'},{'role':'user','content':'y'}]})
                self.assertNotIn('user_blocks',call.call_args.kwargs)
        done=subprocess.CompletedProcess([],0,'{"type":"system"}\n'+json.dumps({'type':'result','result':'done'})+'\n','')
        with patch.object(T,'cli_path',return_value='scripted-cli'),patch.object(T,'cli_env',return_value={}),patch.object(T.subprocess,'run',return_value=done)as call:
            self.assertEqual(T.run_cli('claude-sonnet-5-5','rules',state(3),user_blocks=first)['result'],'done')
            args=call.call_args.args[0];self.assertEqual(args[args.index('--output-format')+1],'stream-json')
            self.assertEqual(args[args.index('--input-format')+1],'stream-json')
            sent=json.loads(call.call_args.kwargs['input']);self.assertEqual(sent['message']['content'],first)
            with self.assertRaisesRegex(ValueError,'reproduce'):T.run_cli('claude-sonnet-5-5','rules',state(3)+' ',user_blocks=first)

    def test_native_json_schema_is_forwarded_without_an_output_ceiling(self):
        schema={'type':'object','properties':{'label':{'enum':['supported','unknown']}},'required':['label']}
        done=subprocess.CompletedProcess([],0,json.dumps({'structured_output':{'label':'supported'},'result':''}),'')
        with patch.object(T,'cli_path',return_value='scripted-cli'),patch.object(T,'cli_env',return_value={}),patch.object(T.subprocess,'run',return_value=done)as call:
            T.run_cli('claude-haiku-5-5','rules','question',json_schema=schema)
        args=call.call_args.args[0];self.assertEqual(json.loads(args[args.index('--json-schema')+1]),schema)
        self.assertNotIn('CLAUDE_CODE_MAX_OUTPUT_TOKENS',call.call_args.kwargs['env'])

    def test_structured_cli_output_is_the_text_contract_with_original_output_retained(self):
        schema={'type':'object'};body={'output_config':{'format':{'type':'json_schema','schema':schema}},'tools':[{'name':'sql'}],'tool_choice':{'type':'none'}}
        native={'result':'Native commentary','structured_output':{'label':'supported'},'usage':{'input_tokens':2,'output_tokens':7}}
        with patch.object(T,'run_cli',return_value=native)as call:response=T.respond(body)
        self.assertEqual(call.call_args.kwargs['json_schema'],schema)
        self.assertEqual(json.loads(response['content'][0]['text']),native['structured_output'])
        self.assertEqual(response['transport_details']['native_text'],'Native commentary')
        self.assertEqual(response['usage']['output_tokens'],7)
        self.assertEqual(response['stop_reason'],'end_turn')
        self.assertIn('JSON Schema',call.call_args.args[1])
        self.assertIn('submit the required value once through the provided StructuredOutput tool',call.call_args.args[1])
        self.assertNotIn('final text answer must be ONLY',call.call_args.args[1])
        body['tool_choice']={'type':'auto'}
        with patch.object(T,'run_cli',return_value={'result':'{"tool_use":{"name":"sql","input":{}}}'})as call:response=T.respond(body)
        self.assertEqual(call.call_args.kwargs['json_schema'],T.decision_output_schema(body))
        self.assertEqual(response['stop_reason'],'tool_use')

    def test_native_decision_schema_preserves_tool_choice_and_literal_arguments(self):
        final={'type':'object','properties':{'label':{'enum':['unknown']}},'required':['label'],'additionalProperties':False}
        tools=[{'name':name,'input_schema':{'type':'object','properties':{'sql':{'type':'string'}},
                'required':['sql'],'additionalProperties':False}}for name in ('first','second')]
        body={'tools':tools,'output_config':{'format':{'type':'json_schema','schema':final}}}
        for choice in ('auto','any','tool','none'):
            with self.subTest(choice=choice):
                body['tool_choice']={'type':choice,**({'name':'first'}if choice=='tool'else{})}
                schema=T.decision_output_schema(body)
                self.assertEqual(schema['type'],'object')
                self.assertNotIn('anyOf',schema);self.assertNotIn('oneOf',schema);self.assertNotIn('allOf',schema)
                variants=schema['properties']['decision']['anyOf']if choice!='none'else[schema]
                self.assertEqual(final in variants,choice in ('auto','none'))
                permitted=T.allowed_tools(tools,body['tool_choice'])
                if permitted:
                    action_schema=next(v for v in variants if 'tool_use'in v['properties'])
                    options=action_schema['properties']['tool_use']['anyOf']
                    self.assertEqual([o['properties']['name']['enum'][0]for o in options],[t['name']for t in permitted])
                    self.assertEqual([o['properties']['input']for o in options],[t['input_schema']for t in permitted])
                    self.assertTrue(all(o['additionalProperties']is False for o in options))
                for name in ('first','second','unregistered'):
                    action={'tool_use':{'name':name,'input':{'sql':'SELECT original FROM received'}}}
                    self.assertEqual(bool(T._tool_calls(json.dumps(action),permitted)),name in [t['name']for t in permitted])
                self.assertEqual(T._tool_calls(json.dumps({'tool_use':{'name':'first','input':{'sql':'SELECT 1'}},'label':'unknown'}),permitted),[])
        body['tool_choice']={'type':'tool','name':'first'}
        action={'tool_use':{'name':'first','input':{'sql':"SELECT 'literal source' AS observation"}}}
        with patch.object(T,'run_cli',return_value={'result':'Retained original','structured_output':{'decision':action}})as call:
            response=T.respond(body)
        self.assertEqual(response['content'][0]['name'],'first')
        self.assertEqual(response['content'][0]['input'],action['tool_use']['input'])
        self.assertEqual(call.call_args.kwargs['json_schema'],T.decision_output_schema(body))
        self.assertEqual(response['transport_details']['native_text'],'Retained original')
        self.assertEqual(response['transport_details']['cli']['structured_output'],{'decision':action})
        self.assertEqual(response['transport_details']['structured_output_mode'],'native_cli_decision_schema')
        # A malformed wrapper or a mixed action remains inert; no extra action
        # is dropped to make the response executable.
        for bad in ({'decision':action,'extra':action},{'decision':{**action,'label':'unknown'}}):
            with patch.object(T,'run_cli',return_value={'structured_output':bad}):result=T.respond(body)
            self.assertEqual(result['stop_reason'],'end_turn')

    def test_incomplete_structured_output_stays_inert_and_invalid_format_does_not_generate(self):
        body={'output_config':{'format':{'type':'json_schema','schema':{'type':'object'}}}}
        with patch.object(T,'run_cli',return_value={'result':'partial text','structured_output':{'label':'supported'},'stop_reason':'max_tokens'}):response=T.respond(body)
        self.assertEqual(response['content'][0]['text'],'partial text');self.assertEqual(response['stop_reason'],'max_tokens')
        self.assertEqual(response['transport_details']['cli']['structured_output'],{'label':'supported'})
        with patch.object(T,'run_cli')as call:
            with self.assertRaises(ValueError):T.respond({'output_config':{'format':{'type':'json_schema','schema':'bad'}}})
        call.assert_not_called()

    def test_legacy_and_inherited_output_ceilings_are_not_enforced(self):
        done = subprocess.CompletedProcess([], 0, json.dumps({'result': '{}'}), '')
        inherited = {'CLAUDE_CODE_MAX_OUTPUT_TOKENS': '128000',
                     'CLAUDE_CODE_EXTRA_BODY': '{"metadata":{"user_id":"local"},"max_tokens":4096}',
                     'CLAUDE_CODE_EFFORT_LEVEL': 'max'}
        with patch.object(T, 'cli_path', return_value='scripted-cli'), \
             patch.object(T, 'cli_env', return_value=inherited.copy()), \
             patch.object(T.subprocess, 'run', return_value=done) as call:
            T.run_cli('claude-haiku-5-5', 'rules', 'question', max_tokens=8192, effort='medium')
        env = call.call_args.kwargs['env']
        self.assertNotIn('CLAUDE_CODE_MAX_OUTPUT_TOKENS',env)
        self.assertEqual(json.loads(env['CLAUDE_CODE_EXTRA_BODY']),
                         {'metadata': {'user_id': 'local'}})
        self.assertEqual(env['CLAUDE_CODE_EFFORT_LEVEL'], 'medium')
        self.assertEqual(call.call_count, 1)

    def test_no_output_quota_or_effort_is_invented(self):
        done = subprocess.CompletedProcess([], 0, '{"result":"{}"}', '')
        with patch.object(T, 'cli_path', return_value='scripted-cli'), patch.object(T, 'cli_env', return_value={}), \
             patch.object(T.subprocess, 'run', return_value=done) as call:
            T.run_cli('claude-haiku-5-5', 'rules', 'question')
        self.assertNotIn('CLAUDE_CODE_MAX_OUTPUT_TOKENS', call.call_args.kwargs['env'])
        self.assertNotIn('CLAUDE_CODE_EXTRA_BODY', call.call_args.kwargs['env'])
        self.assertNotIn('CLAUDE_CODE_EFFORT_LEVEL', call.call_args.kwargs['env'])

    def test_invalid_output_contract_never_starts_a_generation(self):
        with patch.object(T, 'cli_env', return_value={}), patch.object(T.subprocess, 'run') as call:
            for value in (True, 0, -1, '8192'):
                with self.assertRaises(ValueError):
                    T.run_cli('claude-haiku-5-5', 'rules', 'question', max_tokens=value)
            with self.assertRaises(ValueError):
                T.run_cli('claude-haiku-5-5', 'rules', 'question', effort='invented')
        call.assert_not_called()

    def test_internal_cli_work_remains_visible_without_becoming_an_invoice(self):
        usage = {'input_tokens': 6, 'output_tokens': 128199}
        raw = {'result': '{}', 'usage': usage, 'num_turns': 3,
               'duration_ms': 1017869, 'subtype': 'success',
               'modelUsage': {'claude-haiku-5-5': {'outputTokens': 128199}}}
        with patch.object(T, 'run_cli', return_value=raw) as call:
            response = T.respond({'max_tokens': 8192, 'output_config': {'effort': 'high'}})
        self.assertEqual(call.call_args.kwargs['max_tokens'], 8192)
        self.assertEqual(call.call_args.kwargs['effort'], 'high')
        self.assertEqual(response['usage']['output_tokens'], 128199)
        details = response['transport_details']
        self.assertEqual(details['cli']['num_turns'], 3)
        self.assertEqual(details['cli']['modelUsage'], raw['modelUsage'])
        self.assertNotIn('result', details['cli'])
        self.assertNotIn('total_cost_usd', details)
        self.assertFalse(details['caller_max_tokens_enforced'])

    def test_reported_truncated_action_is_not_executable(self):
        out = {'result': '{"tool_use":{"name":"sql","input":{"query":"SELECT 1"}}}',
               'stop_reason': 'max_tokens', 'usage': {'output_tokens': 8192}}
        with patch.object(T, 'run_cli', return_value=out):
            response = T.respond({'max_tokens': 8192, 'tools': [{'name': 'sql'}]})
        self.assertEqual(response['stop_reason'], 'max_tokens')
        self.assertTrue(all(x['type'] == 'text' for x in response['content']))

    def test_session_limit_is_typed_and_never_retried_by_transport(self):
        from clinical_core.agere.gateway import ProviderUnavailable, classify_provider_error
        message = "You've hit your session limit \u00b7 resets 11:50am (America/Chicago)"
        done = subprocess.CompletedProcess([], 1, json.dumps({'is_error': True, 'result': message}), '')
        with patch.object(T, 'cli_path', return_value='scripted-cli'), patch.object(T, 'cli_env', return_value={}), \
             patch.object(T.subprocess, 'run', return_value=done) as call:
            with self.assertRaises(ProviderUnavailable) as raised:
                T.run_cli('claude-haiku-5-5', 'rules', 'question')
        self.assertEqual(raised.exception.kind, 'rate_limit')
        self.assertEqual(call.call_count, 1)
        self.assertEqual(classify_provider_error(RuntimeError('claude CLI error: '+message)), 'rate_limit')
        self.assertEqual(classify_provider_error(RuntimeError(message.replace("'", '\u2019'))), 'rate_limit')
        for request_error in ('context window limit reached', 'max output tokens limit exceeded'):
            self.assertIsNone(classify_provider_error(RuntimeError(request_error)))

    def test_literal_action_batch_preserves_arguments_and_has_distinct_native_ids(self):
        calls=[{'name':'sql','input':{'query':'SELECT 1'}},{'name':'sql','input':{'query':'SELECT 2'}}]
        with patch.object(T,'run_cli',return_value={'result':json.dumps({'tool_uses':calls}),'usage':{}}):
            response=T.respond({'tools':[{'name':'sql'}]})
        self.assertEqual(response['stop_reason'],'tool_use')
        self.assertEqual([{'name':x['name'],'input':x['input']} for x in response['content']],calls)
        self.assertEqual(len({x['id'] for x in response['content']}),2)
        self.assertEqual(T._tool_calls(json.dumps({'tool_uses':[calls[0],{'name':'unknown','input':{}}]}),[{'name':'sql'}]),[])

    def test_caller_timeout_is_preserved_without_an_automatic_second_generation(self):
        with patch.dict(os.environ, {'AGERE_SUBSCRIPTION_TIMEOUT': ''}), \
             patch.object(T, 'run_cli', side_effect=subprocess.TimeoutExpired('scripted', 900)) as call:
            with self.assertRaises(subprocess.TimeoutExpired): T.respond({'messages': []}, timeout=900)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(call.call_args.kwargs['timeout'], 900)

    def test_no_timeout_is_invented_for_an_unbounded_caller(self):
        with patch.dict(os.environ, {'AGERE_SUBSCRIPTION_TIMEOUT': ''}), \
             patch.object(T, 'run_cli', return_value={'result': '{}', 'usage': {}}) as call:
            T.respond({'messages': []})
        self.assertIsNone(call.call_args.kwargs['timeout'])

    def test_system_tools_and_transcript_become_one_prompt(self):
        body = {'model': 'claude-haiku-5-5', 'system': [{'type': 'text', 'text': 'Be exact.'}],
                'tools': [{'name': 'sql', 'description': 'run SQL', 'input_schema': {'type': 'object'}}],
                'messages': [{'role': 'user', 'content': 'How many?'},
                             {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 't1', 'name': 'sql', 'input': {'query': 'SELECT 1'}}]},
                             {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 't1', 'content': '[[1]]'}]}]}
        system, user = T.build_prompt(body)
        self.assertIn('Be exact.', system); self.assertIn('"name": "sql"', system); self.assertIn('tool_use', system)
        self.assertIn('How many?', user); self.assertIn('[tool result for t1] [[1]]', user)

    def test_disabled_or_named_tools_are_honored_in_prompt_and_response(self):
        tools=[{'name':'sql','description':'Unique SQL tool'},{'name':'review','description':'Unique review tool'}]
        body={'tools':tools,'tool_choice':{'type':'none'},'messages':[{'role':'user','content':'Return the final answer'}]}
        system,_=T.build_prompt(body)
        self.assertNotIn('Unique SQL tool',system);self.assertIn('Tools are disabled',system)
        action='{"tool_use":{"name":"sql","input":{"query":"SELECT 1"}}}'
        with patch.object(T,'run_cli',return_value={'result':action}):result=T.respond(body)
        self.assertEqual(result['content'],[{'type':'text','text':action}]);self.assertEqual(result['stop_reason'],'end_turn')
        selected=T.allowed_tools(tools,{'type':'tool','name':'review'})
        self.assertEqual(selected,[tools[1]])
        body['tool_choice']={'type':'tool','name':'review'}
        system,_=T.build_prompt(body)
        self.assertIn('This turn requires a tool call',system)
        self.assertIn('naming review with literal input arguments',system)
        self.assertNotIn('Unique SQL tool',system)

    def test_failed_cli_output_and_usage_survive_on_the_exception(self):
        raw={'is_error':True,'result':'Partial output before terminal failure','usage':{'output_tokens':19}}
        done=subprocess.CompletedProcess([],1,json.dumps(raw),'')
        with patch.object(T,'cli_path',return_value='scripted-cli'),patch.object(T,'cli_env',return_value={}),patch.object(T.subprocess,'run',return_value=done):
            with self.assertRaises(RuntimeError) as raised:T.respond({'model':'authored'})
        self.assertEqual(raised.exception.provider_response['subscription_cli_result'],raw)
        self.assertEqual(raised.exception.provider_response['usage'],raw['usage'])

    def test_aborted_stream_preserves_raw_zero_counters_without_claiming_zero_cost(self):
        from clinical_core.agere.gateway import ProviderUnavailable
        from clinical_core.agere.economics import usage_cost
        raw={'is_error':True,'terminal_reason':'aborted_streaming','result':'Retained partial text',
             'errors':['stream diagnostic'],'usage':{'input_tokens':0,'output_tokens':0},'modelUsage':{},'num_turns':2}
        done=subprocess.CompletedProcess([],1,json.dumps(raw),'')
        with patch.object(T,'cli_path',return_value='scripted-cli'),patch.object(T,'cli_env',return_value={}),patch.object(T.subprocess,'run',return_value=done) as call:
            with self.assertRaises(ProviderUnavailable) as raised:T.respond({'model':'claude-haiku-5-5'})
        self.assertEqual(raised.exception.kind,'cancelled');self.assertEqual(call.call_count,1)
        receipt=raised.exception.provider_response
        self.assertEqual(receipt['subscription_cli_result'],raw)
        self.assertIsNone(receipt['usage']);self.assertIsNone(usage_cost(receipt['model'],receipt['usage']))

    def test_signal_exit_retains_partial_stdout_and_stops_without_a_complete_json_result(self):
        from clinical_core.agere.gateway import ProviderUnavailable
        done=subprocess.CompletedProcess([],-2,'partial visible stdout','')
        with patch.object(T,'cli_path',return_value='scripted-cli'),patch.object(T,'cli_env',return_value={}),patch.object(T.subprocess,'run',return_value=done):
            with self.assertRaises(ProviderUnavailable) as raised:T.respond({'model':'claude-haiku-5-5'})
        self.assertEqual(raised.exception.kind,'cancelled')
        self.assertEqual(raised.exception.provider_response['subscription_cli_result']['result'],'partial visible stdout')
        self.assertIsNone(raised.exception.provider_response['usage'])

    def test_subscription_limit_in_error_array_is_not_lost_when_result_is_empty(self):
        from clinical_core.agere.gateway import ProviderUnavailable
        raw={'is_error':True,'terminal_reason':'api_error','result':'','errors':["You've hit your limit · resets 5pm"]}
        done=subprocess.CompletedProcess([],1,json.dumps(raw),'')
        with patch.object(T,'cli_path',return_value='scripted-cli'),patch.object(T,'cli_env',return_value={}),patch.object(T.subprocess,'run',return_value=done):
            with self.assertRaises(ProviderUnavailable) as raised:T.respond({})
        self.assertEqual(raised.exception.kind,'rate_limit')
        self.assertEqual(raised.exception.provider_response['subscription_cli_result'],raw)

    def test_invalid_cli_result_and_timeout_preserve_partial_stdout_without_invented_usage(self):
        outcomes=[subprocess.CompletedProcess([],1,'{"result":"unfinished',''),
                  subprocess.TimeoutExpired('scripted',900,output=b'partial stdout')]
        for outcome in outcomes:
            kwargs={'side_effect':outcome} if isinstance(outcome,Exception) else {'return_value':outcome}
            with self.subTest(outcome=type(outcome).__name__),patch.object(T,'cli_path',return_value='scripted-cli'),patch.object(T,'cli_env',return_value={}),patch.object(T.subprocess,'run',**kwargs) as call:
                with self.assertRaises(Exception) as raised:T.respond({'model':'authored'})
                self.assertIsNone(raised.exception.provider_response['usage'])
                self.assertTrue(raised.exception.provider_response['subscription_cli_result']['result'])
                self.assertEqual(call.call_count,1)

    def test_missing_token_usage_is_unknown_and_cannot_be_priced_as_zero(self):
        from clinical_core.agere.economics import usage_cost
        with patch.object(T,'run_cli',return_value={'result':'{}'}):out=T.respond({})
        self.assertIsNone(out['usage']['input_tokens']);self.assertIsNone(out['usage']['output_tokens'])
        self.assertIsNone(usage_cost(out['model'],out['usage']))

    def test_a_tool_call_reply_becomes_a_tool_use_block_and_text_stays_text(self):
        tools = [{'name': 'sql'}]
        out = {'result': '{"tool_use": {"name": "sql", "input": {"query": "SELECT 2"}}}', 'usage': {'input_tokens': 10, 'output_tokens': 5}}
        with patch.object(T, 'run_cli', return_value=out):
            r = T.respond({'model': 'claude-haiku-5-5', 'tools': tools, 'messages': [{'role': 'user', 'content': 'q'}]})
        self.assertEqual(r['stop_reason'], 'tool_use'); self.assertEqual(r['content'][0]['input'], {'query': 'SELECT 2'})
        self.assertEqual(r['usage']['input_tokens'], 10)
        cached={'input_tokens':10,'output_tokens':5,'cache_creation_input_tokens':100,
                'cache_creation':{'ephemeral_5m_input_tokens':20,'ephemeral_1h_input_tokens':80}}
        with patch.object(T,'run_cli',return_value={'result':'{}','usage':cached}):
            priced=T.respond({'model':'claude-haiku-5-5','messages':[{'role':'user','content':'q'}]})
        self.assertEqual(priced['usage']['cache_creation'],cached['cache_creation'])
        with patch.object(T, 'run_cli', return_value={'result': '{"answer": 3}', 'usage': {}}):
            r = T.respond({'model': 'claude-haiku-5-5', 'messages': [{'role': 'user', 'content': 'q'}]})
        self.assertEqual(r['stop_reason'], 'end_turn'); self.assertEqual(r['content'][0]['text'], '{"answer": 3}')


class Intercept(unittest.TestCase):
    def test_only_messages_requests_are_diverted(self):
        T._original.clear()
        real = urllib.request.urlopen
        try:
            with patch.dict(os.environ, {'AGERE_CLAUDE_TRANSPORT': 'subscription'}), \
                 patch.object(T, 'respond', return_value={'content': [{'type': 'text', 'text': 'hi'}]}) as respond:
                self.assertTrue(T.install())
                req = urllib.request.Request('https://api.anthropic.com/v1/messages', data=json.dumps({'messages': []}).encode(), method='POST')
                with urllib.request.urlopen(req, timeout=5) as resp:
                    self.assertEqual(json.load(resp)['content'][0]['text'], 'hi')
                self.assertEqual(respond.call_args.kwargs['timeout'], 5)
        finally:
            urllib.request.urlopen = real; T._original.clear()

    def test_off_by_default(self):
        with patch.dict(os.environ, {'AGERE_CLAUDE_TRANSPORT': ''}):
            self.assertFalse(T.install())


if __name__ == '__main__':
    unittest.main()
