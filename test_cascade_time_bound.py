"""Cascade ordering and time bound: the free compiler plan goes ahead of the heavy tiers, one slow tier cannot use the whole answer budget,
and a paid tier whose plan the literal check will reject is declined before its first paid call."""
import os
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from clinical_core.agere.clinical import atomic_questions, proposers as P
from clinical_core.agere.clinical.answer_budget import cancelled
from clinical_core.agere.clinical.question_input import QuestionNotUnderstood

QUESTION = 'how many patients have diabetes'
GOOD = {'coverage': 'supported_observation', 'nodes': [{'id': 'a', 'operation': 'sql', 'parameters': {'query': "select count(*) from patients where condition = 'diabetes'"}}]}
BAD = {'coverage': 'supported_observation', 'nodes': [{'id': 'a', 'operation': 'sql', 'parameters': {'query': 'select count(*) from patients'}}]}


def cascade(q=QUESTION, live=True):
    stack = SimpleNamespace(allow_live=live, path='/nonexistent', retriever=SimpleNamespace(meta={'snapshot': {'as_of': '2030-05-27'}}))
    return P.Cascade(stack, SimpleNamespace(identity={}), q, ledger=SimpleNamespace(close=lambda: None))


def with_plan(c, graph):
    c._ladder = SimpleNamespace(compiled=lambda: (graph, {'route': 'local_compiler'}))
    return c


class FreeFirst(unittest.TestCase):
    def test_compiler_plan_that_passes_the_literal_check_goes_before_the_heavy_tiers(self):
        order = tuple(P.ORDER)
        moved = with_plan(cascade(), GOOD).free_first(order)
        self.assertEqual(sorted(moved), sorted(order))  # a permutation: no tier dropped
        heavy = [moved.index(t) for t in P.HEAVY]
        self.assertLess(moved.index('local_compiler'), min(heavy))
        self.assertEqual({'specialist_families_late', 'cohort_family_late'}, set(moved[moved.index('local_compiler') + 1:min(heavy)]))  # only the cheap free families sit between
        self.assertLess(moved.index('exact_local_plan'), moved.index('local_compiler'))  # cheaper free tiers still go first
        self.assertLess(moved.index('local_compiler'), moved.index('hosted_typed_interpreter'))  # System One / Claude order unchanged

    def test_plan_that_fails_the_check_or_is_missing_leaves_the_order(self):
        order = tuple(P.ORDER)
        self.assertEqual(with_plan(cascade(), BAD).free_first(order), order)
        self.assertEqual(with_plan(cascade(), None).free_first(order), order)
        self.assertEqual(cascade().free_first(order), order)  # no profile / ladder on a fixture stack


class TimeShare(unittest.TestCase):
    def run_cascade(self, slow, budget='3'):
        reached, late = [], []

        def stub(name):
            def tier(self):
                reached.append(name)
                if name == slow:
                    while not cancelled() and time.monotonic() < deadline: time.sleep(0.02)
                    time.sleep(0.2)
                    late.append(self.reject(name, ['late rejection']))  # an abandoned tier records nothing
                return None
            return tier
        deadline = time.monotonic() + 20
        with patch.dict(os.environ, {'AGERE_ANSWER_BUDGET_SECONDS': budget, 'AGERE_S1_ROUTER': 'off', 'AGERE_LOCAL_TYPED': 'off', 'AGERE_BACKGROUND_CONTINUE': 'off'}), \
                patch.dict(P.TIER_SHARE, {'heavy': 0.01}), patch.object(P.Cascade, 'expected', lambda self, name: 0.0), \
                patch.multiple(P.Cascade, **{'tier_' + n: stub(n) for n in P.ORDER}):  # tiny heavy share (1.2 s), no expected-cost gate
            c = cascade(live=True)
            started = time.perf_counter()
            with self.assertRaises(QuestionNotUnderstood):
                c.run()
            return c, reached, time.perf_counter() - started

    def test_a_slow_tier_yields_like_a_decline_and_the_cascade_continues(self):
        c, reached, took = self.run_cascade('atomic_system_one')
        share = 120 * 0.01
        self.assertLess(took, share + 3)  # nowhere near the 20 s the fake tier would take
        entry = next(e for e in c.log if e['proposer'] == 'atomic_system_one')
        self.assertEqual(entry['outcome'], 'declined')
        self.assertIn('time share exhausted', entry['detail'])
        self.assertTrue(set(P.ORDER) - {'typed_decider'} <= set(reached) | {'typed_decider'})  # every later tier still ran
        self.assertIn('legacy_cost_ladder', reached)
        time.sleep(0.5)
        self.assertFalse([e for e in c.log if e['outcome'] == 'rejected' and e['detail'] == 'late rejection'])

    def test_no_time_limit_runs_the_tier_inline(self):
        threads = []
        def tier(self):
            threads.append(threading.current_thread().name); return None
        with patch.dict(os.environ, {'AGERE_ANSWER_BUDGET_SECONDS': 'off'}):
            c = cascade()
            c.budget = SimpleNamespace(seconds=float('inf'))
            self.assertIsNone(c.tier_seconds('bottled_sql', c.budget))
            with patch.object(P.Cascade, 'tier_bottled_sql', tier):
                c.call_tier('bottled_sql', None)
        self.assertEqual(threads, [threading.current_thread().name])


class EarlyLiteralCheck(unittest.TestCase):
    def test_atomic_tier_declines_before_any_system_one_call(self):
        called = []
        plan = {'planner': 'pattern', 'scope': {'sources': []}}
        c = cascade('Who should our care managers call this week? We have 40 slots')
        with patch.object(atomic_questions, 'plan', lambda *a, **k: plan), patch.object(atomic_questions, 'graph_of', lambda q, p: BAD), \
                patch('clinical_core.agere.clinical.analysis_graph.validate', lambda g, a: g), \
                patch.object(atomic_questions, 'execute', lambda *a, **k: called.append(1)):
            with self.assertRaises(QuestionNotUnderstood) as ctx:
                c.tier_atomic_system_one()
        self.assertEqual(called, [])  # no paid per-patient decision was started
        self.assertIn('literal pre-check', str(ctx.exception))
        self.assertIn('40', str(ctx.exception))

    def test_a_plan_that_passes_goes_on_to_system_one(self):
        called = []
        plan = {'planner': 'pattern', 'scope': {'sources': []}}
        c = cascade()
        report = {'report': {'cells': [], 'system_one': {'errors': 0, 'estimated_usd': 0, 'new_paid_api_calls': 0, 'decided_by': {}, 'decisions': 0},
                             'population_patients': 0, 'evaluated_patient_ids': [], 'capped': False}}
        with patch.object(atomic_questions, 'plan', lambda *a, **k: plan), patch.object(atomic_questions, 'graph_of', lambda q, p: GOOD), \
                patch('clinical_core.agere.clinical.analysis_graph.validate', lambda g, a: g), \
                patch.object(atomic_questions, 'execute', lambda *a, **k: called.append(1) or report):
            found = atomic_questions.tier(c.stack, QUESTION, None, early_check=lambda g: None if not c.gaps(P.executable_text([n['parameters'] for n in g['nodes']])) else 1 / 0)
        self.assertEqual(called, [1])
        self.assertIsNotNone(found)


if __name__ == '__main__':
    unittest.main()


class LocalModelTurns(unittest.TestCase):
    def test_local_model_tiers_never_overlap_across_concurrent_questions(self):
        active, peak, lock = [0], [0], threading.Lock()

        def tier(self):
            with lock: active[0] += 1; peak[0] = max(peak[0], active[0])
            time.sleep(0.05)
            with lock: active[0] -= 1

        with patch.object(P.Cascade, 'tier_bottled_sql', tier, create=True), patch.object(P.Cascade, 'tier_qwen_typed_interpreter', tier, create=True):
            runs = [threading.Thread(target=cascade().call_tier, args=(n, None)) for n in ('bottled_sql', 'qwen_typed_interpreter') * 3]
            for t in runs: t.start()
            for t in runs: t.join()
        self.assertEqual(peak[0], 1)


class SqliteDeadline(unittest.TestCase):
    def test_a_runaway_statement_stops_at_the_tier_deadline(self):
        import sqlite3
        from clinical_core.agere.clinical import answer_budget as B
        db = sqlite3.connect(':memory:')
        runaway = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c) SELECT count(*) FROM (SELECT x FROM c LIMIT 1000000000)"
        token = B._deadline.set(time.monotonic() + 0.3)
        try:
            started = time.monotonic()
            with self.assertRaises(sqlite3.OperationalError): db.execute(runaway).fetchone()
            self.assertLess(time.monotonic() - started, 3)
        finally:
            B._deadline.reset(token)
        self.assertEqual(db.execute('SELECT 1').fetchone(), (1,))  # outside a tier nothing is interrupted


class DataCurrencyClause(unittest.TestCase):
    def test_use_data_received_as_of_is_context_not_a_literal(self):  # every benchmark plan failed on 2030, 05, 27
        from clinical_core.agere.clinical import requirement_ledger as R
        plan = '"query": "SELECT count(DISTINCT patient_id) FROM concept_patient WHERE concept = \'diabetes\'"'
        self.assertEqual(R.gaps('How many patients have diabetes? Use data received as of 2030-05-27.', plan), [])
        self.assertTrue(R.gaps('How many patients with diabetes as of 2029-12-31?', plan))  # a point-in-time limit stays required


class Stages(unittest.TestCase):
    def test_routers_reorder_only_inside_the_stage_boundaries(self):
        order = tuple(reversed(P.ORDER))  # a router that wants Claude first and reuse last
        s = P.staged(order)
        self.assertEqual(sorted(s), sorted(P.ORDER))  # nothing dropped
        last = len(P.STAGES) - 1
        kinds = [0 if P.stage_of(t) == 0 else 2 if P.stage_of(t) == last else 1 for t in s]
        self.assertEqual(kinds, sorted(kinds))  # reuse/certified first, Claude last
        self.assertEqual(P.staged(P.ORDER), P.ORDER)
        self.assertEqual(P.ORDER[:3], ('exact_local_plan', 'paraphrase_reuse', 'semantic_cache'))
        self.assertLess(P.ORDER.index('local_compiler'), P.ORDER.index('atomic_system_one'))
        self.assertLess(P.ORDER.index('atomic_system_one'), P.ORDER.index('metric_router'))


class WhyNotAnswered(unittest.TestCase):
    def test_a_refusal_lists_the_tiers_that_tried(self):
        from clinical_core.agere.clinical.question_input import failure
        exc = QuestionNotUnderstood('nothing kept the whole question')
        exc.planner_receipt = {'new_paid_api_calls': 0, 'proposers': [
            {'proposer': 'exact_local_plan', 'outcome': 'no proposal', 'detail': None, 'seconds': 0.0},
            {'proposer': 'local_free_sql', 'outcome': 'rejected', 'detail': 'value check: made-up label', 'seconds': None}]}
        with patch('clinical_core.agere.clinical.shared_planner.economics', return_value={}):
            out = failure(exc)
        self.assertEqual([t['tier'] for t in out['tiers_tried']], ['local_free_sql'])
        self.assertIn('made-up label', out['tiers_tried'][0]['detail'])
