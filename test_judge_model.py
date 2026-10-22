"""Planner/judge model split: Sonnet drafts and reviews; Opus re-plans and re-reviews only after a review fails."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clinical_core.agere import gateway as G
from clinical_core.agere.clinical import nl2sql, shared_planner as S, request_program, general_analysis

CLEAR = {k: '' for k in ('AGERE_PLANNER_MODEL', 'AGERE_SHARED_JUDGE_MODEL', 'AGERE_ESCALATION_MODEL', 'AGERE_FALLBACK_MODEL')}


class Tiers(unittest.TestCase):
    def test_defaults_are_economical(self):
        with patch.dict(os.environ, CLEAR):
            self.assertEqual(G.model_tiers(), {'planner': 'claude-sonnet-5-5', 'judge': 'claude-sonnet-5-5', 'escalation': 'claude-opus-5-5'})
        for m in G.model_tiers().values():
            self.assertIn(m, G.CLAUDE_FAMILY)
        self.assertEqual(G.PersistentGateway.MODELS['claude'], G.ESCALATION_MODEL)

    def test_env_overrides(self):
        with patch.dict(os.environ, {**CLEAR, 'AGERE_SHARED_JUDGE_MODEL': 'claude-opus-5-5', 'AGERE_PLANNER_MODEL': 'claude-haiku-5-5'}):
            self.assertEqual(G.model_tiers(), {'planner': 'claude-haiku-5-5', 'judge': 'claude-opus-5-5', 'escalation': 'claude-opus-5-5'})

    def test_review_failed(self):
        self.assertFalse(nl2sql.review_failed('{"equivalent": true, "issues": []}'))
        self.assertFalse(nl2sql.review_failed('```json\n{"equivalent": true, "issues": [], "nodes": [{"equivalent": true, "issues": []}]}\n```'))
        for text in ('{"equivalent": false, "issues": []}', '{"equivalent": true, "issues": ["window"]}', 'not json',
                     '{"equivalent": true, "issues": [], "nodes": [{"equivalent": false, "issues": []}]}'):
            self.assertTrue(nl2sql.review_failed(text), text)

    def test_judge_systems_cover_every_review_prompt(self):
        self.assertEqual(nl2sql.judge_systems(), {S.AUDIT_SYSTEM, request_program.AUDIT_SYSTEM, general_analysis.AUDIT_SYSTEM})
        self.assertNotIn(S.SYSTEM, nl2sql.judge_systems())


class TieredHostedModel(unittest.TestCase):
    def setUp(self):
        e = patch.dict(os.environ, {**CLEAR, 'AGERE_PROVIDER_BACKOFF_SECONDS': '0'}); e.start(); self.addCleanup(e.stop)
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)

    def run_calls(self, script, env=None):
        """script: [(system, user, reply)] -> models used."""
        used, replies = [], [r for _, _, r in script]
        def fake_send(body, provider, ledger, out, **kw):
            used.append(body['model']); return replies[len(used) - 1], {'cost_estimate': {'estimated_usd': 0.0}}
        with patch('clinical_core.agere.clinical.context_student.send', fake_send), patch.dict(os.environ, env or {}):
            m = nl2sql.HostedModel(None, Path(self.tmp.name), model='tiered')
            stats = [m(system, user)[1] for system, user, _ in script]
        return used, stats, m

    def test_passing_review_never_touches_opus(self):
        used, stats, m = self.run_calls([(S.SYSTEM, '{"question":"q"}', '{"request":{}}'),
                                         (S.AUDIT_SYSTEM, '{"original_question":"q"}', '{"equivalent": true, "issues": []}')])
        self.assertEqual(used, ['claude-sonnet-5-5', 'claude-sonnet-5-5'])
        self.assertEqual([s['role'] for s in stats], ['planner', 'judge']); self.assertFalse(m.escalated)

    def test_failed_review_escalates_replan_and_rereview(self):
        used, _, m = self.run_calls([(S.SYSTEM, '{"question":"q"}', '{}'),
                                     (request_program.AUDIT_SYSTEM, '{}', '{"equivalent": false, "issues": ["dropped 2029"]}'),
                                     (S.SYSTEM, '{"question":"q"}', '{}'),
                                     (general_analysis.AUDIT_SYSTEM, '{}', '{"equivalent": true, "issues": []}')])
        self.assertEqual(used, ['claude-sonnet-5-5', 'claude-sonnet-5-5', 'claude-opus-5-5', 'claude-opus-5-5'])
        self.assertEqual(m.identity, 'claude-opus-5-5')

    def test_review_feedback_in_the_prompt_escalates(self):
        feedback = json.dumps({'question': 'q', 'validator_feedback': {'semantic_review': {'equivalent': False, 'issues': ['x']}}})
        used, _, _ = self.run_calls([(S.SYSTEM, feedback, '{}')])
        self.assertEqual(used, ['claude-opus-5-5'])

    def test_shared_judge_model_is_separate(self):
        used, _, _ = self.run_calls([(S.SYSTEM, '{}', '{}'), (S.AUDIT_SYSTEM, '{}', '{"equivalent": true, "issues": []}')],
                                    env={'AGERE_SHARED_JUDGE_MODEL': 'claude-haiku-5-5'})
        self.assertEqual(used, ['claude-sonnet-5-5', 'claude-haiku-5-5'])

    def test_explicit_model_keeps_current_behavior(self):
        used = []
        def fake_send(body, *a, **kw):
            used.append(body['model']); return '{"equivalent": false, "issues": ["x"]}', {}
        with patch('clinical_core.agere.clinical.context_student.send', fake_send):
            m = nl2sql.HostedModel(None, Path(self.tmp.name), model='claude-opus-5-5')
            m(S.AUDIT_SYSTEM, '{}'); m(S.SYSTEM, '{}')
        self.assertEqual(used, ['claude-opus-5-5'] * 2)
        self.assertEqual(nl2sql.HostedModel(None, Path(self.tmp.name)).identity, 'claude-haiku-5-5')


if __name__ == '__main__':
    unittest.main()
