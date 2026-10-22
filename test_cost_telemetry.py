"""Per-question cost telemetry: attempt attribution through the ledger, tokens, pricing version, stage timings, the
answers.sqlite3 rows and the compose wiring. No network, no warehouse."""
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

os.environ.setdefault('AGERE_LOCAL_TYPED', 'off')
os.environ.setdefault('AGERE_LOCAL_NORMALIZER', 'off')

from clinical_core.agere.clinical import cost_telemetry as T, conversation as C  # noqa: E402
from clinical_core.agere.gateway import BudgetLedger  # noqa: E402


def claude_response(model, text, inp=1000, out=200, read=0):
    return {'model': model, 'content': [{'type': 'text', 'text': text}],
            'usage': {'input_tokens': inp, 'output_tokens': out, 'cache_read_input_tokens': read}}


def paid(ledger, key, model, text, provider='claude', **usage):
    ledger.reserve(key, provider, model)
    ledger.finish(key, response=claude_response(model, text, **usage), seconds=0.5)


class Telemetry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger_path = Path(self.tmp.name) / 'ledger.sqlite3'
        self.ledger = BudgetLedger(self.ledger_path)

    def tearDown(self):
        self.ledger.close(); self.tmp.cleanup()

    def test_attempts_are_attributed_to_their_question_only(self):
        paid(self.ledger, 'before', 'claude-sonnet-5-5', 'x')  # before the question: never counted
        probe = T.start('How many patients have diabetes?', ledger_path=self.ledger_path)
        paid(self.ledger, 'mine', 'claude-sonnet-5-5', '{"equivalent": false, "issues": ["window"]}', inp=40000, out=300, read=30000)
        other = T.Probe('another question', ledger_path=self.ledger_path)
        token = T.CURRENT.set(other.question_id)
        paid(self.ledger, 'theirs', 'claude-opus-5-5', 'y')  # tagged by a concurrent question: excluded
        T.CURRENT.reset(token)
        t = threading.Thread(target=lambda: paid(BudgetLedger(self.ledger_path), 'thread', 'claude-opus-5-5', '{"plan": 1}'))
        t.start(); t.join()  # a worker thread carries no context: window attribution
        self.ledger.reserve('before', 'claude', 'claude-sonnet-5-5')  # validated reuse of an earlier answer: a free cache hit
        tel = probe.finish({'answer': {'status': 'answered'}, 'planner_receipt': {'interpretation': {
            'interpreter': 'hosted_typed_interpreter', 'proposers': [{'proposer': 'semantic_cache', 'outcome': 'no proposal', 'seconds': 0.1}]}}})
        ids = [a['attempt_id'] for a in tel['attempts']]
        self.assertEqual(ids[:2], ['mine', 'thread'])
        self.assertNotIn('theirs', ids); self.assertEqual(ids.count('before'), 1)
        self.assertEqual(tel['api_calls'], 2)
        self.assertEqual(tel['calls_by_model'], {'claude-sonnet-5-5': 1, 'claude-opus-5-5': 1})
        mine = tel['attempts'][0]
        self.assertEqual((mine['input_tokens'], mine['output_tokens'], mine['cache_read_tokens']), (40000, 300, 30000))
        self.assertEqual(mine['review'], 'failed'); self.assertEqual(mine['attribution'], 'tagged')
        self.assertEqual(tel['attempts'][1]['attribution'], 'window')
        self.assertEqual(tel['attempts'][-1]['attempt_id'], 'before')
        self.assertTrue(tel['attempts'][-1]['cache_hit']); self.assertEqual(tel['attempts'][-1]['usd'], 0.0)
        self.assertGreater(tel['api_usd'], 0); self.assertTrue(tel['usd_known'])
        self.assertEqual(tel['opus_without_failed_sonnet'], [])  # Opus followed a failed Sonnet review
        self.assertTrue(tel['pricing_version'].startswith('2026-'))
        self.assertEqual(tel['stages']['cascade'][0]['tier'], 'semantic_cache')
        self.assertFalse(tel['llm_free'])

    def test_opus_without_failed_sonnet_is_flagged(self):
        probe = T.start('q', ledger_path=self.ledger_path)
        paid(self.ledger, 'o1', 'claude-opus-5-5', '{"request": 1}')
        paid(self.ledger, 's1', 'claude-sonnet-5-5', '{"equivalent": true, "issues": []}')
        paid(self.ledger, 'o2', 'claude-opus-5-5', '{"request": 2}')
        tel = probe.finish({'answer': {'status': 'answered'}})
        self.assertEqual(tel['opus_without_failed_sonnet'], ['o1', 'o2'])

    def test_unpriced_failure_is_unknown_not_zero(self):
        probe = T.start('q', ledger_path=self.ledger_path)
        self.ledger.reserve('f', 'claude', 'claude-opus-5-5'); self.ledger.finish('f', error='HTTP 400')
        tel = probe.finish(None, error='ProviderUnavailable: billing')
        self.assertEqual(tel['api_calls'], 1); self.assertFalse(tel['usd_known']); self.assertIsNone(tel['attempts'][0]['usd'])
        self.assertEqual(tel['status'], 'error')

    def test_free_answer_and_record(self):
        probe = T.start('How many patients have diabetes?', ledger_path=self.ledger_path)
        probe.mark('compose')
        tel = probe.finish({'answer': {'status': 'answered'}, 'planner_receipt': {'interpretation': {'interpreter': 'compositional_grammar'}}})
        self.assertTrue(tel['llm_free']); self.assertEqual(tel['api_calls'], 0); self.assertIn('compose', tel['stages'])
        stack = Path(self.tmp.name) / 'stack'; stack.mkdir()
        self.assertTrue(T.record(stack, tel))
        self.assertEqual(T.rows(stack)[0]['question_id'], tel['question_id'])

    def test_missing_ledger_is_empty_not_an_error(self):
        tel = T.start('q', ledger_path=Path(self.tmp.name) / 'absent.sqlite3').finish({'answer': {'status': 'answered'}})
        self.assertEqual(tel['attempts'], [])


class FakeStack:
    def __init__(self, path, ledger_path):
        self.path, self.allow_live, self.receipts, self.ledger_path = Path(path), True, Path(path), ledger_path

    def compose(self, question, planning='auto'):
        if 'boom' in question:
            exc = RuntimeError('planner failed'); exc.planner_receipt = {'new_paid_api_calls': 1, 'known_api_estimate_usd': 0.1}
            ledger = BudgetLedger(self.ledger_path)
            try: paid(ledger, 'fail-' + question, 'claude-sonnet-5-5', '{"x":1}')
            finally: ledger.close()
            raise exc
        ledger = BudgetLedger(self.ledger_path)
        try: paid(ledger, 'q-' + question, 'jev-1.13.0', '{}', provider='jev')
        finally: ledger.close()
        return {'question': question, 'graph': {}, 'planner_receipt': {'route': 'shared_planner', 'interpretation': {
                'interpreter': 'atomic_system_one', 'execution_accepted': True}}, 'planning_economics': {'known_api_estimate_usd': 0.0002},
                'new_paid_api_calls': 1, 'answer': {'status': 'answered', 'answer_type': 'count', 'headline': '3'}}


class ComposeWiring(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger_path = Path(self.tmp.name) / 'ledger.sqlite3'
        os.environ['AGERE_LEDGER_PATH'] = str(self.ledger_path)
        os.environ['AGERE_TRAINING_DIR'] = str(Path(self.tmp.name) / 'training')
        self.stack = FakeStack(self.tmp.name, self.ledger_path)

    def tearDown(self):
        os.environ.pop('AGERE_LEDGER_PATH', None); os.environ.pop('AGERE_TRAINING_DIR', None)
        self.tmp.cleanup()

    def test_every_answer_carries_and_records_telemetry(self):
        r = C.handle_compose(self.stack, {'question': 'How many patients with CKD had an eGFR in 2029?'})
        tel = r['cost_telemetry']
        self.assertEqual(tel['attempt_ids'], ['q-How many patients with CKD had an eGFR in 2029?'])
        self.assertEqual(tel['system_one_calls'], 1); self.assertEqual(tel['system_one_decided'], ['answer:atomic_system_one'])
        for stage in ('compose', 'capture', 'learning', 'total'): self.assertIn(stage, tel['stages'])
        with sqlite3.connect(Path(self.tmp.name) / 'answers.sqlite3') as db:
            joined = db.execute('SELECT a.route, c.api_calls, c.pricing_version FROM answers a JOIN answer_costs c '
                                'ON a.question_id=c.question_id').fetchall()
        self.assertEqual(len(joined), 1); self.assertEqual(joined[0][1], 1)

    def test_failures_record_telemetry_too(self):
        with self.assertRaises(RuntimeError) as cm:
            C.handle_compose(self.stack, {'question': 'boom question'})
        tel = cm.exception.cost_telemetry
        self.assertEqual(tel['api_calls'], 1); self.assertEqual(tel['status'], 'internal_error'); self.assertIn('error', tel)
        with sqlite3.connect(Path(self.tmp.name) / 'answers.sqlite3') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM answer_costs c JOIN answers a ON a.question_id=c.question_id').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
