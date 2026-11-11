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

    def test_adaptive_selection_does_not_launch_a_competing_spec_writer(self):
        from clinical_core.agere.clinical.shared_planner import start_spec_early
        with patch.dict(os.environ, {'AGERE_NONFRONTIER_FIRST': 'adaptive'}), patch.object(spec_fill, 'start_early') as start:
            start_spec_early(None, None, 'Explain the utilization change', None)
        start.assert_not_called()

    def test_started_fillers_are_taken_once_by_question(self):
        stack = type('S', (), {'path': '/tmp/x', 'allow_live': True})()
        calls = []

        def fake_run_filler(name, fn, ctx):
            calls.append(name)
            return {'name': name, 'status': 'invalid', 'seconds': 0.0, 'plan': None, 'contract': None, 'error': 'fake', 'notes': {}}
        catalog = type('C', (), {'identity': {'as_of': '2030-05-27'}})()
        with patch.object(spec_fill, 'run_filler', fake_run_filler), patch.object(spec_fill, 'enabled_fillers', return_value=('haiku',)):
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


class AdaptiveOrder(unittest.TestCase):
    def test_unfamiliar_question_reaches_one_investigator_before_scans_and_rewriters(self):
        order, receipt = P.adaptive_order(P.ORDER)
        self.assertCountEqual(order, P.ORDER)
        pivot = order.index('backstop_agent')
        for tier in ('exact_local_plan', 'semantic_cache', 'local_compiler', 'chart_reader'):
            self.assertLess(order.index(tier), pivot)
        for tier in ('evidence_review', 'atomic_system_one', 'spec_path', 'local_normalizer', 'hosted_typed_interpreter'):
            self.assertGreater(order.index(tier), pivot)
        self.assertFalse(receipt['cost_optimality_established'])

    def test_fitting_typed_choice_gets_first_opportunity_but_broad_class_is_not_a_scan_certificate(self):
        metric, _ = P.adaptive_order(P.ORDER, routing={'applied': True, 'label': 'metric'})
        self.assertLess(metric.index('s1_metric_router'), metric.index('backstop_agent'))
        chart, _ = P.adaptive_order(P.ORDER, routing={'applied': True, 'label': 'chart_evidence'})
        self.assertGreater(chart.index('atomic_system_one'), chart.index('backstop_agent'))
        uncertain, _ = P.adaptive_order(P.ORDER, routing={'applied': False, 'label': 'metric'})
        self.assertGreater(uncertain.index('s1_metric_router'), uncertain.index('backstop_agent'))

    def test_qualified_specialist_is_preserved_without_overriding_enablement_or_corrections(self):
        order, receipt = P.adaptive_order(P.ORDER, tier_routing={'applied': True, 'tier': 'bottled_sql'}, corrected=True)
        self.assertEqual(order[0], 'hosted_typed_interpreter')
        self.assertLess(order.index('bottled_sql'), order.index('backstop_agent'))
        self.assertEqual(receipt['qualified_specialist'], 'bottled_sql')
        with patch.dict(os.environ, {'AGERE_PROPOSER_SKIP': 'bottled_sql'}):
            self.assertFalse(P.enabled('bottled_sql', type('S', (), {'allow_live': True})()))

    def test_declined_investigator_keeps_every_specialist_fallback(self):
        # Reachability under a failed first planner: no cost/time/population cap
        # and no route removes a component merely because its class differs.
        for label in ('metric', 'chart_evidence', 'engine_analysis', 'unknown'):
            order, _ = P.adaptive_order(tuple(reversed(P.ORDER)), routing={'applied': True, 'label': label})
            self.assertCountEqual(order, P.ORDER)
            self.assertEqual(len(order), len(set(order)))


class LazyDispatch(unittest.TestCase):
    def test_native_answer_can_finish_without_question_classification(self):
        cascade = P.Cascade(type('S', (), {'path': '/unused'})(), None, 'a native question')
        with patch.dict(os.environ, {'AGERE_NONFRONTIER_FIRST': 'adaptive'}), \
             patch('clinical_core.agere.clinical.learning.correction_context', return_value=None), \
             patch('clinical_core.agere.clinical.harness_improvement.recovering', return_value=False), \
             patch.object(cascade, 'order', side_effect=AssertionError('Unnecessary classifier')):
            tiers = cascade.ordered_tiers()
            self.assertEqual(next(tiers), 'exact_local_plan')
            self.assertIn('backstop_agent', cascade.order_used)
            tiers.close()

    def test_declined_native_prefix_routes_once_without_repeating_or_losing_a_tier(self):
        cascade = P.Cascade(type('S', (), {'path': '/unused'})(), None, 'an unresolved question')
        with patch.dict(os.environ, {'AGERE_NONFRONTIER_FIRST': 'adaptive'}), \
             patch('clinical_core.agere.clinical.learning.correction_context', return_value=None), \
             patch('clinical_core.agere.clinical.harness_improvement.recovering', return_value=False), \
             patch.object(cascade, 'order', return_value=tuple(reversed(P.ORDER))) as route:
            tiers = cascade.ordered_tiers()
            prefix = [next(tiers) for _ in P.NATIVE_REUSE_FIRST]
            route.assert_not_called()
            actual = prefix + list(tiers)
            route.assert_called_once()
            self.assertCountEqual(actual, P.ORDER)
            self.assertEqual(len(actual), len(set(actual)))

    def test_correction_and_owned_continuation_precede_reused_answers(self):
        for corrected, recovering in ((True, False), (False, True)):
            with self.subTest(corrected=corrected, recovering=recovering):
                cascade = P.Cascade(type('S', (), {'path': '/unused'})(), None, 'a prior question')
                with patch.dict(os.environ, {'AGERE_NONFRONTIER_FIRST': 'adaptive'}), \
                     patch('clinical_core.agere.clinical.learning.correction_context', return_value=corrected), \
                     patch('clinical_core.agere.clinical.harness_improvement.recovering', return_value=recovering), \
                     patch.object(cascade, 'order', return_value=('backstop_agent',)):
                    self.assertEqual(list(cascade.ordered_tiers()), ['backstop_agent'])


class AnalystGate(unittest.TestCase):
    def test_only_an_audited_analyst_answer_takes_the_audit_as_its_check(self):
        from clinical_core.agere.clinical import acceptance_contract as AC
        graph = {'nodes': [{'id': 'answer', 'operation': 'sql', 'parameters': {'query': 'SELECT COUNT(*) FROM living_patient', 'returns': 'count'}}]}
        bound = {'final_sql_digest': AC.sql_digest(graph), 'final_sql_findings': []}  # the audit covered the sql that executes
        # the audit alone no longer authorizes: the acceptance contract over the final executed sql must also pass (acceptance_contract.py)
        ok = {'definitions_audit': {'action': 'ok', **bound}, 'acceptance_contract': {'ok': True}}
        self.assertTrue(answer_checks.audited_analyst('semantic_analyst', ok, graph))
        self.assertFalse(answer_checks.audited_analyst('semantic_analyst', {'definitions_audit': {'action': 'ok', **bound}}, graph))
        self.assertFalse(answer_checks.audited_analyst('semantic_analyst', {**ok, 'acceptance_contract': {'ok': False}}, graph))
        self.assertTrue(answer_checks.audited_analyst('semantic_analyst', {**ok, 'definitions_audit': {'action': 'rewritten', **bound}}, graph))
        self.assertFalse(answer_checks.audited_analyst('semantic_analyst', {**ok, 'definitions_audit': {'action': 'declined', **bound}}, graph))
        self.assertFalse(answer_checks.audited_analyst('semantic_analyst', {}, graph))
        self.assertFalse(answer_checks.audited_analyst('hosted_verified_sql', ok, graph))


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
