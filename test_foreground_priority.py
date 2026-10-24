"""Foreground questions go first: the background worker starts no job or tier while a foreground question is in flight, a waiting
foreground caller gets the local-model lock before a queued background one, and the cohort-family local slot fill starts only when it
fits the budget (and at most once per question). Fake tiers, fake clocks, fake model; no model is loaded."""
import os
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from clinical_core.agere.clinical import answer_budget as AB, background_continuation as BG, bottled_cohort_hook as hook
from clinical_core.agere.clinical import foreground_priority as FP, proposers as P, tier_timing as TT
from test_answer_latency import Clock, QUESTION, answer_contract


class Counter(unittest.TestCase):
    def test_wait_returns_when_the_last_foreground_question_ends(self):
        with FP.foreground():
            self.assertEqual(FP.foreground_in_flight(), 1)
            self.assertGreaterEqual(FP.wait_for_foreground(0.05), 0.05)  # still in flight: waits out the timeout
        self.assertEqual(FP.foreground_in_flight(), 0)
        self.assertEqual(FP.wait_for_foreground(1), 0.0)

    def test_cascade_run_counts_as_foreground_and_releases_on_failure(self):
        seen = []
        stack = SimpleNamespace(allow_live=False, path='/nonexistent', retriever=SimpleNamespace(meta={'snapshot': {'as_of': '2030-05-27'}}))
        c = P.Cascade(stack, SimpleNamespace(identity={}), QUESTION, ledger=SimpleNamespace(close=lambda: None))
        with patch.object(P.Cascade, '_run', lambda self: seen.append(FP.foreground_in_flight()) or (_ for _ in ()).throw(RuntimeError('x'))):
            with self.assertRaises(RuntimeError): c.run()
        self.assertEqual(seen, [1])
        self.assertEqual(FP.foreground_in_flight(), 0)


class Lock(unittest.TestCase):
    def test_a_foreground_waiter_is_served_before_a_queued_background_one(self):
        lock, order, release = FP.PriorityLock(), [], threading.Event()
        holder_in = threading.Event()

        def holder():
            with lock:
                holder_in.set(); release.wait(5)

        def background():
            with FP.background_run():
                with lock: order.append('background')

        def foreground():
            with lock: order.append('foreground')
        h = threading.Thread(target=holder); h.start(); self.assertTrue(holder_in.wait(5))
        b = threading.Thread(target=background); b.start()
        time.sleep(0.1)  # the background caller queues first
        f = threading.Thread(target=foreground); f.start()
        time.sleep(0.1)
        self.assertEqual(lock.waiting_foreground(), 1)
        release.set()
        for t in (h, b, f): t.join(5)
        self.assertEqual(order, ['foreground', 'background'])

    def test_background_acquires_a_free_lock_when_no_foreground_waits(self):
        lock = FP.PriorityLock()
        self.assertTrue(lock.acquire(background=True, timeout=0.1))
        self.assertFalse(lock.acquire(background=False, timeout=0.05))  # held: a timed foreground acquire gives up
        lock.release()
        self.assertEqual(lock.waiting_foreground(), 0)

    def test_the_cascade_lock_is_the_priority_lock(self):
        self.assertIs(P.LOCAL_MODEL_LOCK, FP.LOCAL_MODEL_LOCK)
        self.assertNotIn('cohort_family_early', P.LOCAL_MODEL_TIERS)  # these tiers lock only around the slot fill itself
        self.assertNotIn('cohort_family_late', P.LOCAL_MODEL_TIERS)


class BackgroundYields(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(); self.addCleanup(self.dir.cleanup)
        env = patch.dict(os.environ, {'AGERE_PROPOSER_ON': 'bottled_sql,hybrid_analysts,qwen_rewrite_compiler'})  # the heavy tiers these tests exercise are off the default path: switched on here
        env.start(); self.addCleanup(env.stop)
        TT._cache.clear(); BG._queue.clear(); BG._recent.clear()
        self.assertTrue(BG.wait_idle(5))

    def job_cascade(self):
        stack = SimpleNamespace(allow_live=True, path=self.dir.name, retriever=SimpleNamespace(meta={'snapshot': {'as_of': '2030-05-27'}}))
        return P.Cascade(stack, SimpleNamespace(identity={}), QUESTION, ledger=SimpleNamespace(close=lambda: None))

    def test_a_background_job_never_starts_a_tier_while_a_foreground_question_runs(self):
        started, events = [], []
        c = self.job_cascade()
        with patch.object(P.Cascade, 'tier_bottled_sql', lambda s: started.append(FP.foreground_in_flight()) or events.append('tier') or None), \
                patch.object(P.Cascade, 'tier_atomic_system_one', lambda s: started.append(FP.foreground_in_flight()) or None), \
                patch.dict(os.environ, {'AGERE_LOCAL_TYPED': 'off', 'AGERE_LOCAL_NORMALIZER': 'off'}):
            with FP.foreground():
                t = threading.Thread(target=lambda: c.continue_run(['bottled_sql', 'atomic_system_one'], 60)); t.start()
                time.sleep(0.4)
                self.assertEqual(started, [])  # a foreground question is in flight: no tier has started
            t.join(10)
        self.assertEqual(started, [0, 0])  # both ran only after it ended
        self.assertGreater(c.foreground_wait, 0.3)

    def test_it_re_checks_between_its_own_tiers(self):
        c = self.job_cascade()
        first_done, may_finish, gate = threading.Event(), threading.Event(), []

        def first(s):
            first_done.set(); may_finish.wait(5); return None

        def second(s):
            gate.append(FP.foreground_in_flight()); return None
        with patch.object(P.Cascade, 'tier_bottled_sql', first), patch.object(P.Cascade, 'tier_atomic_system_one', second):
            t = threading.Thread(target=lambda: c.continue_run(['bottled_sql', 'atomic_system_one'], 60)); t.start()
            self.assertTrue(first_done.wait(5))
            with FP.foreground():  # a foreground question arrives while the first tier is finishing
                may_finish.set()
                time.sleep(0.4)
                self.assertEqual(gate, [])
            t.join(10)
        self.assertEqual(gate, [0])

    def test_the_worker_waits_before_a_job_and_reports_the_wait(self):
        done = threading.Event()
        fake = SimpleNamespace(deferred=[{'tier': 'bottled_sql'}], stack=SimpleNamespace(path=self.dir.name), catalog=None, question='q',
                               population=None, hosted_model=None, memory_identity=None, order_used=('bottled_sql',), log=[], claude_down=None)
        with patch.dict(os.environ, {'AGERE_BACKGROUND_CONTINUE': 'on', 'AGERE_BACKGROUND_DELAY_SECONDS': '0'}), \
                patch.object(BG, 'run_job', lambda job: done.set() or {'answered': False}):
            with FP.foreground():
                self.assertTrue(BG.enqueue(fake)['queued'])
                time.sleep(0.4)
                self.assertFalse(done.is_set())  # the job has not started
            self.assertTrue(BG.wait_idle(10))
        self.assertGreater(BG.recent()[-1]['foreground_wait_seconds'], 0.3)

    def test_a_foreground_local_tier_is_not_blocked_behind_a_queued_background_tier(self):
        order, release, bg_started = [], threading.Event(), threading.Event()
        c = self.job_cascade()
        holder_in = threading.Event()

        def hold():
            with P.LOCAL_MODEL_LOCK:
                holder_in.set(); release.wait(5)

        def bg_tier(s):
            order.append('background'); return None
        h = threading.Thread(target=hold); h.start(); self.assertTrue(holder_in.wait(5))
        with patch.object(P.Cascade, 'tier_bottled_sql', bg_tier), patch.object(P.Cascade, 'tier_qwen_typed_interpreter', lambda s: order.append('foreground') and None):
            def background_call():
                with FP.background_run(): c.call_tier('bottled_sql', None)
            b = threading.Thread(target=background_call); b.start(); time.sleep(0.1)
            fg = self.job_cascade()
            f = threading.Thread(target=lambda: fg.call_tier('qwen_typed_interpreter', None)); f.start(); time.sleep(0.1)
            release.set()
            for t in (h, b, f): t.join(5)
        self.assertEqual(order, ['foreground', 'background'])


class SlotFillGate(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(); self.addCleanup(self.dir.cleanup)
        TT._cache.clear()
        self.clock = Clock()
        self.fake = patch.object(AB, 'current', lambda: self.budget)
        self.fake.start(); self.addCleanup(self.fake.stop)

    def gate(self, remaining=30, estimate=18.0):
        self.budget = AB.AnswerBudget(clock=self.clock).start(30)
        self.clock.t += 30 - remaining
        TT.record(self.dir.name, {hook.SLOT_FILLER: [estimate] * 3})
        return hook.SlotFillGate(self.dir.name, lock=FP.PriorityLock())

    def test_it_runs_when_the_budget_covers_its_measured_cost_and_records_the_seconds(self):
        g = self.gate(remaining=25)
        self.assertEqual(g.run(lambda: ('ok', {})), ('ok', {}))
        self.assertTrue(g.ran)
        self.assertEqual(g.receipt()['skipped'], [])
        self.assertEqual(len(TT._load(self.dir.name)[hook.SLOT_FILLER]), 4)

    def test_it_is_skipped_when_the_remaining_budget_is_short(self):
        g = self.gate(remaining=10)
        called = []
        self.assertIsNone(g.run(lambda: called.append(1)))
        self.assertEqual(called, [])
        self.assertFalse(g.ran)
        self.assertIn('expected ~18s', g.receipt()['skipped'][0])

    def test_it_runs_at_most_once_per_question(self):
        g = self.gate()
        g.run(lambda: 1)
        self.assertIsNone(g.run(lambda: 2))
        self.assertEqual(g.receipt()['skipped'], ['slot filler already ran for this question'])

    def test_it_is_skipped_when_the_local_model_stays_busy(self):
        g = self.gate(remaining=20, estimate=18.0)  # may wait 2 s for the lock
        g.lock.acquire(background=True)
        with patch.object(g.lock, 'acquire', lambda **k: False):
            self.assertIsNone(g.run(lambda: 1))
        self.assertIn('local model busy', g.receipt()['skipped'][0])

    def test_hook_declines_quickly_with_the_reason_and_never_loads_a_model(self):
        g = self.gate(remaining=5)
        model = SimpleNamespace(identity='fake', __call__=None)
        with patch.dict(os.environ, {'AGERE_BOTTLED_COHORT': 'on'}):
            self.assertIsNone(hook.plan(None, 'xyzzy plugh nothing parseable', '2030-05-27', model=model, gate=g))
        self.assertTrue(g.receipt()['skipped'])
        self.assertFalse(g.ran)


class Generation(unittest.TestCase):
    def test_cannot_finish_reports_why(self):
        clock = Clock(); budget = AB.AnswerBudget(clock=clock).start(30)
        with patch.object(AB, 'current', lambda: budget):
            self.assertIsNone(AB.cannot_finish(5))
            self.assertIn('left', AB.cannot_finish(40))
            clock.t += 31
            self.assertEqual(AB.cannot_finish(), 'answer budget used up')


if __name__ == '__main__':
    unittest.main()
