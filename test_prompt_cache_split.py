"""Static/dynamic prompt cache split: every hosted path sends a byte-identical, cached question-independent prefix."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_shared_planner as fixtures
from clinical_core.agere import gateway
from clinical_core.agere.clinical import nl2sql, graph_planner, procedural_memory, shared_planner as S
from clinical_core.agere.hybrid import live

HERE = Path(__file__).resolve().parent
Q1, Q2 = 'How many patients have diabetes?', 'Average length of stay by payer in 2029'


def cached_prefix(body):
    """Everything up to and including the last cache_control block (system, then user content), as sent."""
    blocks = (body['system'] if isinstance(body['system'], list) else []) + [
        b for m in body['messages'] for b in (m['content'] if isinstance(m['content'], list) else [])]
    last = max(i for i, b in enumerate(blocks) if 'cache_control' in b)
    return json.dumps(blocks[:last + 1], sort_keys=True)


class HostedSplit(unittest.TestCase):
    def setUp(self):
        fixtures.SharedPlanner.setUp(self)
        self.env = patch.dict(os.environ, {'AGERE_PROMPT_CACHE_TTL': '1h'}); self.env.start(); self.addCleanup(self.env.stop)

    def send_body(self, system, user, model='claude-haiku-5-5'):
        sent = {}
        def fake_send(body, provider, ledger, out, **kw):
            sent.update(body)
            return '{}', {'cache_hit': False, 'cost_estimate': {'estimated_usd': 0.0}}
        with tempfile.TemporaryDirectory() as d, patch('clinical_core.agere.clinical.context_student.send', fake_send):
            nl2sql.HostedModel(None, Path(d), model=model)(system, user, max_tokens=10)
        return sent

    def user(self, question, feedback=''):
        return S.planner_user(S.schema_slice(self.catalog, question), question=question, validator_feedback=feedback)

    def test_two_questions_share_a_byte_identical_cached_prefix(self):
        a, b = self.send_body(S.SYSTEM, self.user(Q1)), self.send_body(S.SYSTEM, self.user(Q2))
        self.assertEqual(cached_prefix(a), cached_prefix(b))
        for body, q in ((a, Q1), (b, Q2)):
            self.assertEqual(body['system'][0]['cache_control'], {'type': 'ephemeral', 'ttl': '1h'})
            self.assertNotIn(q, cached_prefix(body))
            blocks = body['messages'][0]['content']
            self.assertNotIn('cache_control', blocks[-1]); self.assertIn(q, blocks[-1]['text'])
            self.assertEqual(json.loads(''.join(x['text'] for x in blocks))['question'], q)

    def test_reserialized_planner_json_still_splits_canonically(self):
        # procedural lessons re-dump the planner JSON with default separators, which used to defeat the boundary
        # and cache the whole per-question message.
        guided = json.dumps({**json.loads(self.user(Q1)), 'procedural_lessons': {'items': []}}, ensure_ascii=False)
        self.assertNotIn(S.CACHE_BOUNDARY, guided)
        a, b = self.send_body(S.SYSTEM, guided), self.send_body(S.SYSTEM, self.user(Q2))
        self.assertEqual(cached_prefix(a), cached_prefix(b))
        sent = json.loads(''.join(x['text'] for x in a['messages'][0]['content']))
        self.assertEqual(sent['procedural_lessons'], {'items': []}); self.assertEqual(sent['question'], Q1)

    def test_guided_model_wrapper_keeps_the_cached_prefix(self):
        seen = []
        class Inner:
            def __call__(self, system, user, **kw):
                seen.append(nl2sql.hosted_user_blocks(user)); return '{}', {}
        with patch.object(procedural_memory, 'begin_use', return_value='use-1'):
            for q in (Q1, Q2):
                procedural_memory.GuidedModel(Inner(), self.path, q, [])(S.SYSTEM, self.user(q))
        self.assertEqual(seen[0][0], seen[1][0]); self.assertIn('cache_control', seen[0][0])

    def test_per_question_message_without_a_static_part_is_never_cached(self):
        body = self.send_body('Review this.', json.dumps({'original_question': Q1, 'catalog': {'x': 1}}))
        self.assertEqual(body['messages'][0]['content'], [{'type': 'text', 'text': json.dumps({'original_question': Q1, 'catalog': {'x': 1}})}])
        self.assertIn('cache_control', body['system'][0])

    def test_examples_split_keeps_exact_text(self):
        user = 'STATIC RULES\n\nEXAMPLES\nFollow-up: hmm'
        blocks = self.send_body('sys', user)['messages'][0]['content']
        self.assertEqual(''.join(b['text'] for b in blocks), user)
        self.assertEqual(blocks[0]['text'], 'STATIC RULES'); self.assertNotIn('cache_control', blocks[1])

    def test_five_minute_ttl_is_consistent_across_breakpoints(self):
        with patch.dict(os.environ, {'AGERE_PROMPT_CACHE_TTL': '5m'}):
            body = self.send_body(S.SYSTEM, self.user(Q1))
        marks = [b['cache_control'] for b in body['system'] + body['messages'][0]['content'] if 'cache_control' in b]
        self.assertEqual(marks, [{'type': 'ephemeral'}] * 2)

    def test_static_prefix_is_identical_across_hash_seeds(self):
        script = ('import hashlib,sys,test_shared_planner as f\n'
                  'from clinical_core.agere.clinical import shared_planner as S, request_program, general_analysis\n'
                  't=f.SharedPlanner("setUp");t.setUp()\n'
                  'u=S.planner_user(S.schema_slice(t.catalog,"How many patients have diabetes?"),question="q")\n'
                  'parts=[S.SYSTEM,S.AUDIT_SYSTEM,request_program.AUDIT_SYSTEM,general_analysis.AUDIT_SYSTEM,S.cache_split(u)[0]]\n'
                  'print(hashlib.sha256("\\x00".join(parts).encode()).hexdigest())\n')
        out = set()
        for seed in ('1', '2', '987'):
            env = {**os.environ, 'PYTHONHASHSEED': seed, 'AGERE_LOCAL_TYPED': 'off', 'AGERE_LOCAL_NORMALIZER': 'off'}
            res = subprocess.run([sys.executable, '-c', script], cwd=HERE, env=env, capture_output=True, text=True, timeout=300)
            self.assertEqual(res.returncode, 0, res.stderr[-2000:])
            out.add(res.stdout.strip().splitlines()[-1])
        self.assertEqual(len(out), 1, out)


class OtherPaths(unittest.TestCase):
    def test_stable_json_sorts_keys_and_sets(self):
        self.assertEqual(gateway.stable_json({'b': {3, 1, 2}, 'a': ('x', 'y')}), '{"a":["x","y"],"b":[1,2,3]}')

    def test_graph_planner_system_is_cached_and_feedback_follows_the_catalog(self):
        body = {'model': 'claude-opus-5-5', 'system': 'STABLE INSTRUCTIONS',
                'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': 'CATALOG', 'cache_control': {'type': 'ephemeral', 'ttl': '1h'}},
                                                          {'type': 'text', 'text': 'question: q'}]}]}
        a = graph_planner.cache_layout(body, ['A previous plan failed: x'])
        b = graph_planner.cache_layout(body, [])
        self.assertEqual(cached_prefix(a), cached_prefix(b))
        self.assertEqual(a['system'], [{'type': 'text', 'text': 'STABLE INSTRUCTIONS', 'cache_control': {'type': 'ephemeral', 'ttl': '1h'}}])
        self.assertEqual(a['messages'][0]['content'][-1], {'type': 'text', 'text': 'A previous plan failed: x'})
        self.assertEqual(body['system'], 'STABLE INSTRUCTIONS')  # the caller's body is not mutated
        self.assertEqual(len(body['messages'][0]['content']), 2)

    def test_graph_planner_feedback_never_reaches_the_system_prompt(self):
        source = (HERE / 'clinical_core/agere/clinical/graph_planner.py').read_text()
        self.assertNotIn("instructions += f' A previous plan", source)
        self.assertNotIn("'system': body['system'] + f' A previous plan", source)

    def test_hybrid_analyst_prefix_is_one_hour_and_question_free(self):
        blocks = live.system_blocks()
        self.assertEqual(blocks[-1]['cache_control'], {'type': 'ephemeral', 'ttl': '1h'})
        self.assertEqual(live.system_blocks(), blocks)


if __name__ == '__main__':
    unittest.main()
