"""Cascade v2 (2026-10-24): System One choosers and free planners before the spec path, tiers off the default path, the spec path's paid
fillers started early, and the analyst's definitions audit as its acceptance check. No model or provider is called."""
import os
import time
import unittest
from concurrent.futures import Future
from unittest.mock import patch

from clinical_core.agere.clinical import proposers as P, spec_fill, answer_checks


class Order(unittest.TestCase):
    def test_system_one_choosers_and_free_planners_precede_the_spec_path_which_precedes_claude(self):
        order = list(P.ORDER)
        spec = order.index('spec_path')
        for t in ('s1_metric_router', 's1_operator_router', 'local_compiler', 'chart_reader'):
            self.assertLess(order.index(t), spec, t)
        for t in ('semantic_analyst', 'hosted_typed_interpreter', 'atomic_system_one'):
            self.assertGreater(order.index(t), spec, t)

    def test_staged_keeps_the_stage_boundaries(self):
        shuffled = ['semantic_analyst', 'spec_path', 'atomic_system_one', 's1_metric_router', 'chart_reader', 'local_compiler']
        got = list(P.staged(shuffled))
        self.assertEqual(got[0], 'chart_reader')
        self.assertLess(got.index('local_compiler'), got.index('spec_path'))
        self.assertLess(got.index('spec_path'), got.index('atomic_system_one'))
        self.assertEqual(got[-1], 'semantic_analyst')

    def test_off_default_tiers_are_registered_but_not_run_unless_switched_on(self):
        stack = type('S', (), {'allow_live': True})()
        for t in P.OFF_DEFAULT:
            self.assertIn(t, P.TIERS)
            with patch.dict(os.environ, {'AGERE_PROPOSER_ON': ''}):
                self.assertFalse(P.enabled(t, stack))
            with patch.dict(os.environ, {'AGERE_PROPOSER_ON': t}):
                self.assertTrue(P.enabled(t, stack))
        self.assertTrue(P.enabled('spec_path', stack))


class EarlyStart(unittest.TestCase):
    def setUp(self):
        spec_fill._EARLY.clear()

    def test_started_fillers_are_taken_once_by_question(self):
        stack = type('S', (), {'path': '/tmp/x', 'allow_live': True})()
        calls = []

        def fake_run_filler(name, fn, ctx):
            calls.append(name)
            return {'name': name, 'status': 'invalid', 'seconds': 0.0, 'plan': None, 'contract': None, 'error': 'fake', 'notes': {}}
        catalog = type('C', (), {'identity': {'as_of': '2030-05-27'}})()
        with patch.object(spec_fill, 'run_filler', fake_run_filler):
            spec_fill.start_early(stack, catalog, 'How many  patients have X?', names=('haiku',))
            spec_fill.start_early(stack, catalog, 'How many patients have X?', names=('haiku',))  # same question: started once
            ctx, futures = spec_fill.take_early(stack, 'How many patients have X?', None)
            self.assertEqual(list(futures), ['haiku'])
            self.assertIsNotNone(ctx)
            self.assertEqual(futures['haiku'].result(timeout=5)['name'], 'haiku')
        self.assertEqual(calls, ['grammar', 'haiku'])  # the spec grammar runs first on the early context
        self.assertEqual(spec_fill.take_early(stack, 'How many patients have X?', None), (None, {}))

    def test_an_expired_start_is_dropped(self):
        stack = type('S', (), {'path': '/tmp/y'})()
        f = Future(); f.set_result({})
        spec_fill._EARLY[spec_fill._early_key(stack, 'q', None)] = (None, {'haiku': f}, time.monotonic() - spec_fill.EARLY_TTL - 1)
        catalog = type('C', (), {'identity': {}})()
        with patch.object(spec_fill, 'enabled_fillers', lambda: ()):
            spec_fill.start_early(stack, catalog, 'other')  # pruning runs on every start
        self.assertNotIn(spec_fill._early_key(stack, 'q', None), spec_fill._EARLY)


class AnalystGate(unittest.TestCase):
    def test_only_an_audited_analyst_answer_takes_the_audit_as_its_check(self):
        ok = {'definitions_audit': {'action': 'ok'}}
        self.assertTrue(answer_checks.audited_analyst('semantic_analyst', ok))
        self.assertTrue(answer_checks.audited_analyst('semantic_analyst', {'definitions_audit': {'action': 'rewritten'}}))
        self.assertFalse(answer_checks.audited_analyst('semantic_analyst', {'definitions_audit': {'action': 'declined'}}))
        self.assertFalse(answer_checks.audited_analyst('semantic_analyst', {}))
        self.assertFalse(answer_checks.audited_analyst('hosted_verified_sql', ok))


if __name__ == '__main__':
    unittest.main()


class ReservedAnalyst(unittest.TestCase):
    def cascade(self, log):
        c = P.Cascade.__new__(P.Cascade)
        c.log, c.deferred, c.claude_down, c.population = log, [{'tier': 'semantic_analyst'}], None, None
        c.stack = type('S', (), {'allow_live': True})()
        return c

    def test_the_analyst_gets_a_reserved_turn_when_the_budget_skipped_it(self):
        c = self.cascade([{'proposer': 'semantic_analyst', 'outcome': 'skipped'}])
        asked = []
        with patch.object(P, 'claude_available', lambda: True), \
             patch.object(P.Cascade, 'attempt', lambda self, name, limit: asked.append((name, limit)) or ('contract', {})), \
             patch.object(P.Cascade, 'accept', lambda self, order, name, found: ('accepted', name)):
            self.assertEqual(c.reserved_analyst(['chart_reader', 'semantic_analyst']), ('accepted', 'semantic_analyst'))
        self.assertEqual(asked, [('semantic_analyst', 25.0)])
        self.assertEqual(c.deferred, [])

    def test_no_second_turn_and_off_switch(self):
        with patch.object(P, 'claude_available', lambda: True), \
             patch.object(P.Cascade, 'attempt', lambda *a: self.fail('asked twice')):
            self.assertIsNone(self.cascade([{'proposer': 'semantic_analyst', 'outcome': 'declined'}]).reserved_analyst(['semantic_analyst']))
            with patch.dict(os.environ, {'AGERE_ANALYST_RESERVE_SECONDS': '0'}):
                self.assertIsNone(self.cascade([]).reserved_analyst(['semantic_analyst']))
