"""The subscription transport (test runs on the Claude Code CLI): request translation, emulated tool use, and the urlopen intercept. No CLI is run."""
import json
import os
import subprocess
import unittest
import urllib.request
from unittest.mock import patch

from clinical_core.agere import subscription_transport as T


class Translation(unittest.TestCase):
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

    def test_a_tool_call_reply_becomes_a_tool_use_block_and_text_stays_text(self):
        tools = [{'name': 'sql'}]
        out = {'result': '{"tool_use": {"name": "sql", "input": {"query": "SELECT 2"}}}', 'usage': {'input_tokens': 10, 'output_tokens': 5}}
        with patch.object(T, 'run_cli', return_value=out):
            r = T.respond({'model': 'claude-haiku-5-5', 'tools': tools, 'messages': [{'role': 'user', 'content': 'q'}]})
        self.assertEqual(r['stop_reason'], 'tool_use'); self.assertEqual(r['content'][0]['input'], {'query': 'SELECT 2'})
        self.assertEqual(r['usage']['input_tokens'], 10)
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
