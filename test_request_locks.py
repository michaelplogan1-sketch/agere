"""Per-resource request locks replace the service-wide one-question-at-a-time lock (request_locks.py, service.py)."""
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest

from clinical_core.agere import request_locks as RL
from clinical_core.agere.clinical.shared_planner import interpretation_window
from clinical_core.agere.clinical.together import _released


def run_threads(*targets):
    threads = [threading.Thread(target=t) for t in targets]
    for t in threads: t.start()
    for t in threads: t.join(10)
    return all(not t.is_alive() for t in threads)


class Classification(unittest.TestCase):
    def test_route_kinds(self):
        self.assertEqual(RL.resources('/api/stack/search', {'question': 'x'}), ('read', ()))
        self.assertEqual(RL.resources('/api/stack/ask', {'question': 'x', 'live': False}), ('read', ()))
        kind, names = RL.resources('/api/stack/compose', {'question': 'How many diabetics?', 'conversation_id': 'c1'})
        self.assertEqual((kind, names), ('question', ('conversation:c1',)))
        _, a = RL.resources('/api/stack/compose', {'question': 'How many  diabetics?'})
        _, b = RL.resources('/api/stack/compose', {'question': 'how many diabetics'})
        self.assertEqual(a, b)  # the same standalone question shares one key
        kind, names = RL.resources('/api/stack/work/save', {})
        self.assertEqual((kind, names), ('state', ('family:work', 'saved_state')))
        self.assertNotIn('saved_state', RL.resources('/api/stack/ask', {'question': 'x', 'live': True})[1])


class Concurrency(unittest.TestCase):
    def setUp(self):
        self.locks = RL.RequestLocks()

    def test_different_questions_run_at_the_same_time(self):
        barrier = threading.Barrier(3, timeout=5)  # three questions must all be inside their locks together
        def ask(conv):
            with self.locks.for_route('/api/stack/compose', {'question': 'q', 'conversation_id': conv}):
                barrier.wait()
        self.assertTrue(run_threads(*(lambda c=c: ask(c) for c in ('a', 'b', 'c'))))
        self.assertEqual(self.locks.status()['counters']['max_concurrent'], 3)
        self.assertFalse(self.locks.status()['global_lock'])

    def test_question_is_not_blocked_by_a_running_mutation(self):
        inside, done = threading.Event(), threading.Event()
        def mutation():
            with self.locks.for_route('/api/stack/work/save', {}):
                inside.set(); done.wait(5)
        t = threading.Thread(target=mutation); t.start(); inside.wait(5)
        started = time.perf_counter()
        with self.locks.for_route('/api/stack/compose', {'question': 'how many patients'}):
            pass
        self.assertLess(time.perf_counter() - started, 0.5)
        done.set(); t.join(5)

    def test_same_conversation_and_mutations_stay_ordered(self):
        for path, body in (('/api/stack/compose', {'question': 'q', 'conversation_id': 'same'}), ('/api/stack/decision/event', {})):
            order, inside, go = [], threading.Event(), threading.Event()
            def first():
                with self.locks.for_route(path, body):
                    order.append('first-in'); inside.set(); go.wait(5); order.append('first-out')
            def second():
                inside.wait(5)
                with self.locks.for_route('/api/stack/capabilities/event' if 'decision' in path else path, body):
                    order.append('second-in')
            t1, t2 = threading.Thread(target=first), threading.Thread(target=second)
            t1.start(); t2.start(); time.sleep(0.2); go.set(); t1.join(5); t2.join(5)
            self.assertEqual(order, ['first-in', 'first-out', 'second-in'], path)


class AnswerLockProxy(unittest.TestCase):
    def setUp(self):
        self.locks = RL.RequestLocks()
        self.stack = SimpleNamespace(answer_lock=RL.ThreadAnswerLock(self.locks))

    def test_interpretation_window_releases_only_this_threads_write_lock(self):
        inside, other_ran = threading.Event(), []
        def other_mutation():
            inside.wait(5)
            with self.locks.for_route('/api/stack/correct', {}):
                other_ran.append(True)
        t = threading.Thread(target=other_mutation); t.start()
        with self.locks.for_route('/api/stack/decision/event', {}):
            self.assertTrue(self.stack.answer_lock._is_owned())
            with interpretation_window(self.stack):
                inside.set(); t.join(5)
                self.assertEqual(other_ran, [True])  # saved_state released while only interpreting
            self.assertTrue(self.stack.answer_lock._is_owned())

    def test_questions_hold_no_write_lock_and_release_is_safe(self):
        with self.locks.for_route('/api/stack/compose', {'question': 'q'}):
            self.assertFalse(self.stack.answer_lock._is_owned())
            with interpretation_window(self.stack): pass
            with _released(self.stack): pass  # together._released tolerates a question holding no write lock


class SharedObjects(unittest.TestCase):
    def test_thread_local_connection_and_locked_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'stack.sqlite3'
            db = sqlite3.connect(target); db.executescript('CREATE TABLE patients(id); INSERT INTO patients VALUES (1),(2);'); db.close()

            class Encoder:
                active = 0; peak = 0
                def encode(self, texts):
                    Encoder.active += 1; Encoder.peak = max(Encoder.peak, Encoder.active)
                    time.sleep(0.05); Encoder.active -= 1
                    return [len(t) for t in texts]
            retriever = SimpleNamespace(db=sqlite3.connect(target, check_same_thread=False), encoder=Encoder(), reranker=None)
            RL.thread_safe_retriever(retriever)
            seen = set()
            def work():
                seen.add(id(retriever.db._conn()))
                self.assertEqual(retriever.db.execute('SELECT count(*) FROM patients').fetchone()[0], 2)
                retriever.encoder.encode(['abc'])
            self.assertTrue(run_threads(*([work] * 4)))
            self.assertEqual(len(seen), 4)  # one connection per thread
            self.assertEqual(Encoder.peak, 1)  # model calls serialized by the model lock
            import copy
            clone = copy.copy(retriever.encoder)
            self.assertIs(clone._agere_model_lock, retriever.encoder._agere_model_lock)
            retriever.db.close()

    def test_state_wal(self):
        with tempfile.TemporaryDirectory() as tmp:
            sqlite3.connect(Path(tmp) / 'answers.sqlite3').execute('CREATE TABLE t(x)').connection.close()
            self.assertEqual(RL.state_wal(tmp), {'answers.sqlite3': 'wal'})


class ServiceHasNoGlobalLock(unittest.TestCase):
    def test_service_source(self):
        src = (Path(RL.__file__).parent / 'service.py').read_text()
        self.assertNotIn('RLock', src)
        self.assertNotIn('stack_lock', src)
        self.assertIn('request_locks.for_route(path,body)', src)


if __name__ == '__main__':
    unittest.main()
