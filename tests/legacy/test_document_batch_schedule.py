import contextvars
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import Future
from unittest.mock import patch

from clinical_core.agere.clinical import typed_document_review as T
from clinical_core.agere.gateway import BudgetLedger


def batch(*ids):
    return [('field', 'What is documented?', {'start': 0, 'end': 4, 'text': 'Text'},
             {'record_id': key, 'date': '2030-01-01', 'text': 'Text'}) for key in ids]


class DocumentBatchSchedule(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = BudgetLedger(Path(self.tmp.name) / 'ledger.sqlite3')
        self.addCleanup(self.ledger.close)
        self.env = patch.dict(os.environ, {'AGERE_DOCUMENT_BATCH_WORKERS': '3'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_disjoint_batches_overlap_with_separate_closed_ledgers_and_context(self):
        barrier = threading.Barrier(3)
        local = contextvars.ContextVar('batch_test_context', default=None)
        token = local.set('original_question')
        self.addCleanup(local.reset, token)
        workers = []
        def run(items, ledger):
            self.assertIsNot(ledger, self.ledger)
            self.assertEqual(local.get(), 'original_question')
            ledger.db.execute('SELECT COUNT(*) FROM calls').fetchone()
            workers.append(ledger)
            barrier.wait(timeout=5)
            return items[0][3]['record_id']
        self.assertEqual(T._run_source_batches([batch('A'), batch('B'), batch('C')], self.ledger, run),
                         ['A', 'B', 'C'])
        self.assertEqual(len({id(item.db) for item in workers}), 3)
        for worker in workers:
            with self.assertRaises(sqlite3.ProgrammingError):
                worker.db.execute('SELECT 1')
        self.assertEqual(self.ledger.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0], 0)

    def test_blocked_overlapping_batches_cannot_overtake_each_other(self):
        gate = threading.Barrier(2)
        completed = set()
        lock = threading.Lock()
        jobs = [batch('A'), batch('A', 'B'), batch('B'), batch('C')]
        by_id = {id(item): index for index, item in enumerate(jobs)}
        def run(items, ledger):
            index = by_id[id(items)]
            if index in (0, 3):
                gate.wait(timeout=5)
            with lock:
                if index == 1:
                    self.assertIn(0, completed)
                if index == 2:
                    self.assertIn(1, completed)
                completed.add(index)
            return index
        self.assertEqual(T._run_source_batches(jobs, self.ledger, run), list(range(4)))

    def test_failure_preserves_inflight_receipt_and_stops_unsent_batches(self):
        barrier = threading.Barrier(2)
        failure = threading.Event()
        seen = []
        class SourceFailure(RuntimeError):
            pass
        def run(items, ledger):
            key = items[0][3]['record_id']
            seen.append(key)
            barrier.wait(timeout=5)
            if key == '0':
                failure.set()
                raise SourceFailure('preserve source failure')
            self.assertTrue(failure.wait(timeout=5))
            # Finish the already-dispatched attempt even when its neighbor fails.
            import time
            time.sleep(.03)
            ledger.reserve('inflight_receipt', 'fixture', 'fixture')
            ledger.finish('inflight_receipt', error='synthetic terminal attempt')
            return key
        with patch.dict(os.environ, {'AGERE_DOCUMENT_BATCH_WORKERS': '2'}):
            with self.assertRaisesRegex(SourceFailure, 'preserve source failure'):
                T._run_source_batches([batch(str(i)) for i in range(8)], self.ledger, run)
        self.assertCountEqual(seen, ['0', '1'])
        self.assertEqual(self.ledger.db.execute("SELECT status FROM calls WHERE id='inflight_receipt'").fetchone()[0], 'failed')

    def test_duplicate_source_batches_reuse_in_original_order(self):
        seen = []
        def run(items, ledger):
            before = ledger.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0]
            if not before:
                ledger.reserve('same_task', 'fixture', 'fixture')
                ledger.finish('same_task', response={'answer': 'retained'})
            else:
                self.assertEqual(ledger.reserve('same_task', 'fixture', 'fixture'), {'answer': 'retained'})
            seen.append(before)
            return before
        self.assertEqual(T._run_source_batches([batch('A')] * 5, self.ledger, run), [0, 1, 1, 1, 1])
        self.assertEqual(seen, [0, 1, 1, 1, 1])

    def test_same_record_clock_with_different_segments_is_serial(self):
        first, second = batch('A'), batch('A')
        second[0][3]['text'] = 'Another complete segment'
        first_done = threading.Event()
        def run(items, ledger):
            if items is second:
                self.assertTrue(first_done.is_set())
            first_done.set()
            return items[0][3]['text']
        self.assertEqual(T._run_source_batches([first, second], self.ledger, run),
                         ['Text', 'Another complete segment'])

    def test_worker_failure_during_completion_handling_prevents_later_dispatch(self):
        trigger = threading.Event()
        failed_future_done = threading.Event()
        release = threading.Event()
        started = []
        class SourceFailure(RuntimeError):
            pass
        original_result, original_exception = Future.result, Future.set_exception
        def failed(future, exception):
            original_exception(future, exception)
            if isinstance(exception, SourceFailure):
                failed_future_done.set()
        def consume(future, *args, **kwargs):
            value = original_result(future, *args, **kwargs)
            if value == 'A':
                trigger.set()
                self.assertTrue(failed_future_done.wait(timeout=5))
                release.set()
            return value
        def run(items, ledger):
            key = items[0][3]['record_id']
            started.append(key)
            if key == 'B':
                self.assertTrue(trigger.wait(timeout=5))
                raise SourceFailure('failed while another completion was consumed')
            if key == 'C':
                self.assertTrue(release.wait(timeout=5))
            return key
        with patch.object(Future, 'result', consume), patch.object(Future, 'set_exception', failed):
            with self.assertRaisesRegex(SourceFailure, 'another completion'):
                T._run_source_batches([batch(k) for k in 'ABCD'], self.ledger, run)
        self.assertCountEqual(started, list('ABC'))

    def test_concurrent_errors_retain_primary_type_and_additional_failure(self):
        gate = threading.Barrier(2)
        class SourceFailure(RuntimeError):
            kind = 'rate_limit'
        def run(items, ledger):
            gate.wait(timeout=5)
            raise SourceFailure(items[0][3]['record_id'])
        with self.assertRaises(SourceFailure) as caught:
            T._run_source_batches([batch('A'), batch('B')], self.ledger, run)
        self.assertEqual(caught.exception.kind, 'rate_limit')
        self.assertEqual(len(caught.exception.__notes__), 1)
        self.assertIn('Concurrent source batch', caught.exception.__notes__[0])

    def test_caller_ledger_subclass_preserves_custom_behavior_serially(self):
        class CustomLedger(BudgetLedger):
            pass
        ledger = CustomLedger(Path(self.tmp.name) / 'custom.sqlite3')
        self.addCleanup(ledger.close)
        def run(items, passed):
            self.assertIs(passed, ledger)
            return items[0][3]['record_id']
        self.assertEqual(T._run_source_batches([batch('A'), batch('B')], ledger, run), ['A', 'B'])

    def test_in_memory_and_customized_instances_keep_original_connection(self):
        memory = BudgetLedger(':memory:')
        self.addCleanup(memory.close)
        self.ledger.custom_hook = 'caller-owned behavior'
        for ledger in (memory, self.ledger):
            with self.subTest(ledger=ledger.path):
                def run(items, passed):
                    self.assertIs(passed, ledger)
                    return items[0][3]['record_id']
                self.assertEqual(T._run_source_batches([batch('A'), batch('B')], ledger, run), ['A', 'B'])

    def test_ledger_close_failure_does_not_mask_original_source_failure(self):
        original = BudgetLedger.close
        class SourceFailure(RuntimeError):
            kind = 'rate_limit'
        def close(ledger):
            original(ledger)
            if ledger is not self.ledger:
                raise OSError('close fixture')
        def run(items, ledger):
            raise SourceFailure('original source fixture')
        with patch.object(BudgetLedger, 'close', close):
            with self.assertRaisesRegex(SourceFailure, 'original source fixture') as caught:
                T._run_source_batches([batch('A'), batch('B')], self.ledger, run)
        self.assertEqual(caught.exception.kind, 'rate_limit')
        self.assertTrue(any('ledger close' in note for note in caught.exception.__notes__))

    def test_existing_capped_ledger_authorization_is_atomic_across_workers(self):
        ledger = BudgetLedger(Path(self.tmp.name) / 'explicit-capped-fixture.sqlite3', cap_usd=1.1)
        self.addCleanup(ledger.close)
        gate = threading.Barrier(3)
        def run(items, worker):
            gate.wait(timeout=5)
            try:
                worker.reserve(items[0][3]['record_id'], 'fixture', 'fixture', reservation=.5)
                return 'reserved'
            except ValueError as exc:
                self.assertIn('authorization exhausted', str(exc))
                return 'rejected'
        result = T._run_source_batches([batch(k) for k in 'ABC'], ledger, run)
        self.assertCountEqual(result, ['reserved', 'reserved', 'rejected'])
        self.assertEqual(ledger.db.execute('SELECT SUM(reserved) FROM calls').fetchone()[0], 1.0)
        self.assertEqual(ledger.db.execute('SELECT cap FROM authorization WHERE id=1').fetchone()[0], 1.1)

    def test_parallel_request_events_and_question_cost_tags_are_complete(self):
        from clinical_core.agere import request_lineage as L
        from clinical_core.agere.clinical import cost_telemetry as C
        root = Path(self.tmp.name)
        gate = threading.Barrier(3)
        token = C.CURRENT.set('exact_question')
        self.addCleanup(C.CURRENT.reset, token)
        def run(items, ledger):
            gate.wait(timeout=5)
            key = items[0][3]['record_id']
            with L.component('source_fixture', source_id=key):
                ledger.reserve(key, 'fixture', 'fixture')
                ledger.finish(key, response={'answer': key})
            return key
        with L.Execution(root, '/api/stack/compose', {'question': 'source fixture'}) as execution:
            self.assertEqual(T._run_source_batches([batch(k) for k in 'ABC'], self.ledger, run), list('ABC'))
        db = L.database(root)
        self.addCleanup(db.close)
        rows = db.execute('SELECT sequence,payload FROM question_request_events WHERE execution=? ORDER BY sequence',
                          (execution.id,)).fetchall()
        self.assertEqual([row[0] for row in rows], list(range(len(rows))))
        events = [json.loads(row[1]) for row in rows]
        for key in 'ABC':
            self.assertEqual(sum(e['kind'] == 'reserved' and e.get('request_hash') == key for e in events), 1)
            self.assertEqual(sum(e['kind'] == 'request_finished' and e.get('request_hash') == key for e in events), 1)
        self.assertEqual(self.ledger.db.execute('SELECT COUNT(*) FROM call_questions WHERE question_id=?',
                                              ('exact_question',)).fetchone()[0], 3)

    def test_identical_text_in_distinct_records_has_distinct_cache_and_request_keys(self):
        from types import SimpleNamespace
        from clinical_core.contracts import digest
        sources = [{'record_id': key, 'date': '2030-01-01', 'text': 'The same literal source text.'} for key in 'AB']
        tools = SimpleNamespace(as_of='2030-05-27', stack=SimpleNamespace(path=Path(self.tmp.name), allow_live=False))
        def judge(subject, named, evidence, **kwargs):
            key = digest([subject, named, evidence])
            kwargs['ledger'].reserve(key, 'fixture', 'fixture')
            kwargs['ledger'].finish(key, response={'source': evidence[0]['record_id']})
            return {'judgments': {k: {'label': 'irrelevant', 'decided_by': 'fixture', 'provider': 'fixture',
                                     'probabilities': {'irrelevant': 1.0}} for k in named},
                    'steps': [{'request_hash': key}], 'cost_usd': 0.0}
        with patch.dict(os.environ, {'AGERE_DOCUMENT_CROSS_BATCH': 'off'}):
            result = T.read(tools, 'P', sources, {'field': 'What is documented?'}, self.ledger,
                            judge=judge, ranking=False)
        self.assertEqual(result['provider_batches'], 2)
        self.assertEqual(result['findings'], {'field': []})
        self.assertEqual(self.ledger.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0], 2)
        db = T.observation_store(tools.stack.path)
        self.addCleanup(db.close)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM observations').fetchone()[0], 2)

    def test_unknown_source_identity_and_explicit_serial_mode_use_original_ledger(self):
        def run(items, ledger):
            self.assertIs(ledger, self.ledger)
            return items
        unsafe = batch(None)
        self.assertEqual(T._run_source_batches([unsafe, batch('B')], self.ledger, run), [unsafe, batch('B')])
        with patch.dict(os.environ, {'AGERE_DOCUMENT_BATCH_WORKERS': '1'}):
            jobs = [batch('A'), batch('B')]
            self.assertEqual(T._run_source_batches(jobs, self.ledger, run), jobs)

    def test_invalid_worker_configuration_fails_without_dispatch(self):
        for value in ('0', '-1', 'invalid'):
            with self.subTest(value=value), patch.dict(os.environ, {'AGERE_DOCUMENT_BATCH_WORKERS': value}):
                with self.assertRaises(ValueError):
                    T._run_source_batches([batch('A')], self.ledger, lambda *a: self.fail('unsent'))

    def test_real_reader_keeps_exact_inputs_findings_unknowns_and_order(self):
        from types import SimpleNamespace
        from clinical_core.contracts import digest
        docs = [{'record_id': key, 'date': '2030-01-01', 'text': text} for key, text in (
            ('A', 'Treatment was withheld because of an explicitly documented barrier.'),
            ('B', 'Treatment history is unclear.'),
            ('C', 'Treatment was declined by the patient.'))]
        question = {'question': 'What reason for treatment nonuse is documented?',
                    'entity': {'kind': 'therapy', 'id': 'treatment', 'aliases': ['treatment']},
                    'relation': 'reason_for_nonuse', 'subject': 'patient'}
        requests = []
        lock = threading.Lock()
        def judge(subject, named, evidence, **kw):
            with lock:
                requests.append(digest([subject, named, evidence, kw['as_of'], kw['long_context']]))
            context = evidence[0]
            self.assertEqual(context['text'], next(d['text'] for d in docs if d['record_id'] == context['record_id']))
            axes = {'polarity': 'affirmed', 'temporality': 'historical', 'subject': 'patient',
                    'relation_support': 'supported'}
            return {'judgments': {key: {
                'label': 'unknown' if context['record_id'] == 'B' else axes.get(q['instructions'].get('axis'), 'finding'),
                'decided_by': None if context['record_id'] == 'B' else 'fixture', 'provider': 'fixture'}
                for key, q in named.items()}, 'steps': [], 'cost_usd': 0.0}
        tools = SimpleNamespace(as_of='2030-05-27', stack=SimpleNamespace(allow_live=False))
        with patch.dict(os.environ, {'AGERE_DOCUMENT_CROSS_BATCH': 'off', 'AGERE_DOCUMENT_LOCAL_READERS': '',
                                     'AGERE_DOCUMENT_BATCH_WORKERS': '1'}):
            serial = T.read(tools, 'P', docs, {'reason': question}, self.ledger, judge=judge, ranking=False)
        expected = sorted(requests)
        requests.clear()
        with patch.dict(os.environ, {'AGERE_DOCUMENT_CROSS_BATCH': 'off', 'AGERE_DOCUMENT_LOCAL_READERS': ''}):
            parallel = T.read(tools, 'P', docs, {'reason': question}, self.ledger, judge=judge, ranking=False)
        self.assertEqual(parallel, serial)
        self.assertEqual(sorted(requests), expected)
        self.assertEqual([v['record_id'] for v in parallel['findings']['reason']], ['A', 'C'])
        self.assertEqual(parallel['unresolved'][0]['record_id'], 'B')


if __name__ == '__main__':
    unittest.main()
