"""Agere "together" (clinical_core/agere/clinical/together.py): fast path + semantic analyst + reconcile.

Fake transports only: no paid API calls. A temporary semantic layer, ledger and receipts directory per test."""
import glob
import json
import os
import re
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from clinical_core.agere.clinical import semantic_analyst, semantic_docs, together
from clinical_core.agere.clinical.stack_runtime import Stack

ROOT = Path(__file__).resolve().parent


def answer(kind='count', status='answered', **values):
    out = {'status': status, 'answer_type': kind, 'count': None, 'patient_ids': None, 'value': None, 'table': None,
           'unknown_count': 0, 'missing': [], 'grounding': []}
    out.update(values)
    return out


def make_semantic(path):
    db = sqlite3.connect(path)
    db.executescript('''CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE patient(patient_id TEXT PRIMARY KEY, age_reported INTEGER, sex_reported TEXT);
        CREATE TABLE record(record_id TEXT PRIMARY KEY, patient_id TEXT, stream TEXT, event_date TEXT, recorded_date TEXT,
          available_date TEXT, organization_id TEXT, payload TEXT);
        CREATE TABLE measurement(record_id TEXT, patient_id TEXT, source_table TEXT, concept TEXT, name TEXT, value REAL,
          unit TEXT, event_date TEXT, available_date TEXT);
        CREATE TABLE drug_class_vocabulary(drug_class TEXT, class_group TEXT);''')
    db.executemany('INSERT INTO meta VALUES (?,?)', [('version', 'semantic-layer/17'), ('source_digest', 'together-test'),
                                                      ('as_of', '2030-05-27')])
    db.executemany('INSERT INTO patient VALUES (?,?,?)', [('P-1', 70, 'F'), ('P-2', 40, 'M')])
    db.executemany('INSERT INTO record VALUES (?,?,?,?,?,?,?,?)', [
        ('l1', 'P-1', 'labs', '2028-01-01', None, '2028-01-02', 'O1', '{"name": "HbA1c", "value": 9.5}'),
        ('l2', 'P-2', 'labs', '2030-01-01', None, '2030-05-01', 'O1', '{"name": "HbA1c", "value": 7.0}')])
    db.executemany('INSERT INTO measurement VALUES (?,?,?,?,?,?,?,?,?)', [
        ('l1', 'P-1', 'labs', 'hba1c', 'HbA1c', 9.5, '%', '2028-01-01', '2028-01-02'),
        ('l2', 'P-2', 'labs', 'hba1c', 'HbA1c', 7.0, '%', '2030-01-01', '2030-05-01')])
    db.executemany('INSERT INTO drug_class_vocabulary VALUES (?,?)', [('statin', None)])
    db.commit()
    db.close()


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.payload


class FakeProvider:
    """urlopen-compatible fake for context_student.send. analyst(body) / reconciler(body) return the final answer object
    (or a tool_use turn); every request body is recorded."""

    def __init__(self, analyst, reconciler=None):
        self.analyst, self.reconciler, self.bodies = analyst, reconciler, []
        self.lock = threading.Lock()

    def __call__(self, request, timeout=None):
        body = json.loads(request.data)
        headers = {k.lower(): v for k, v in request.header_items()}
        assert '@' not in json.dumps(headers), 'no e-mail address in HTTP headers'
        with self.lock:
            self.bodies.append(body)
        reconcile = 'reconciling two independent analysts' in body['system'][0]['text']
        out = (self.reconciler if reconcile else self.analyst)(body)
        content = out if isinstance(out, list) else [{'type': 'text', 'text': json.dumps(out)}]
        return FakeResponse({'model': body['model'], 'content': content, 'stop_reason': 'end_turn',
                             'usage': {'input_tokens': 100, 'output_tokens': 20}})

    def calls(self, kind):
        return [b for b in self.bodies if ('reconciling two independent analysts' in b['system'][0]['text']) == (kind == 'reconcile')]


class FakeStack:
    def __init__(self, root, fast=None, error=None, on_compose=None):
        self.path = Path(root) / 'stack'
        self.path.mkdir()
        make_semantic(self.path / 'semantic.sqlite3')
        self.receipts = self.path / 'provider-receipts'
        self.receipts.mkdir()
        self.ledger_path = Path(root) / 'ledger.sqlite3'
        self.allow_live = True
        self.answer_lock = threading.RLock()
        self.retriever = None
        self.fast, self.error, self.on_compose = fast, error, on_compose

    def _compose(self, question, *, planning='auto'):
        if self.on_compose:
            self.on_compose()
        if self.error:
            raise self.error
        return json.loads(json.dumps(self.fast))


def fast_result(ans, operation='sql', confidence='unverified'):
    ans = {**ans, 'confidence': confidence, 'confidence_detail': {'level': confidence, 'basis': 'gate policy'},
           'query': 'SELECT COUNT(*) AS count FROM measurement WHERE value > 9'}
    receipt = {'route': 'hosted_verified_sql', 'new_paid_api_calls': 1,
               'steps': [{'receipt': {'cost_estimate': {'estimated_usd': 0.01}, 'usage': {'input_tokens': 5, 'output_tokens': 5}}}]}
    return {'answer': ans, 'graph': {'nodes': [{'id': 'n1', 'operation': operation, 'parameters': {}}]},
            'planner_receipt': receipt, 'planning_economics': {'new_api_estimate_usd': 0.01}, 'new_paid_api_calls': 1,
            'question': 'q', 'route': 'economical_composition_local_execution'}


def choose(target_count):
    """A fake reconciler that picks the candidate whose count equals target_count (whatever the blind order)."""
    def reconciler(body):
        user = json.loads(body['messages'][0]['content'])
        for i in ('1', '2'):
            if (user[f'candidate_{i}']['answer'] or {}).get('count') == target_count:
                return {'choice': i, 'reason': 'the question counts patients', 'answer': user[f'candidate_{i}']['answer']}
        return {'choice': 'abstain', 'reason': 'cannot tell', 'answer': None}
    return reconciler


class TogetherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'fake-test-key', 'AGERE_TOGETHER': 'on',
                                           'AGERE_TOGETHER_BUDGET_S': '20', 'AGERE_RECONCILE_BUDGET_S': '20',
                                           # these tests cover the reconcile path; the shipped default (analyst) is
                                           # tested in DefaultPolicyTests
                                           'AGERE_DISAGREE_POLICY': 'reconcile',
                                           # these tests mock the single semantic analyst; HybridSlotTests covers the
                                           # hybrid core in the analyst slot
                                           'AGERE_HYBRID': 'off'})
        self.env.start()
        for k in ('AGERE_SEMANTIC_MODEL', 'AGERE_RECONCILE_MODEL'):
            os.environ.pop(k, None)

    def tearDown(self):
        together.TRANSPORT = None
        self.env.stop()
        with together._WAREHOUSE_LOCK:
            for wh in together._WAREHOUSES.values():
                wh.close()
            together._WAREHOUSES.clear()
        self.temp.cleanup()

    def run_together(self, stack, provider):
        together.TRANSPORT = provider
        return together.compose_together(stack, 'How many patients have any HbA1c above 9 percent?')

    def test_agree_is_verified_with_two_paths_evidence_and_accounted_cost(self):
        stack = FakeStack(self.temp.name, fast_result(answer(count=1)))
        sql_then_answer = iter([
            [{'type': 'tool_use', 'id': 't1', 'name': 'query_sql', 'input': {
                'sql': "SELECT COUNT(DISTINCT patient_id) AS count FROM measurement WHERE concept='hba1c' AND value > 9"}}],
            answer(count=1)])
        provider = FakeProvider(lambda body: next(sql_then_answer))
        result = self.run_together(stack, provider)
        a = result['answer']
        self.assertEqual(a['count'], 1)
        self.assertEqual(a['confidence'], 'verified')
        self.assertEqual(a['confidence_detail']['evidence'], 'two_independent_paths_agree')
        self.assertEqual(result['together']['decision'], 'agree')
        self.assertEqual(result['together']['analyst']['model'], 'claude-haiku-5-5')
        self.assertEqual(provider.calls('reconcile'), [])
        # the analyst's SQL ran against the semantic layer and its result went back to the model
        tool_result = provider.bodies[1]['messages'][-1]['content'][0]
        self.assertEqual(json.loads(tool_result['content'])['rows'], [{'count': 1}])
        # cost completeness: both counters carry the two analyst calls; economics includes their priced usage
        self.assertEqual(result['new_paid_api_calls'], 3)
        self.assertEqual(result['planner_receipt']['new_paid_api_calls'], 3)
        self.assertGreater(result['planning_economics']['new_api_estimate_usd'], 0.01)
        ledger = sqlite3.connect(stack.ledger_path)
        self.assertEqual(ledger.execute("SELECT COUNT(*) FROM calls WHERE model='claude-haiku-5-5' AND status='validated'")
                         .fetchone()[0], 2)
        ledger.close()

    def test_disagree_reconcile_picks_the_analyst(self):
        stack = FakeStack(self.temp.name, fast_result(answer(count=5)))
        provider = FakeProvider(lambda body: answer(count=1), choose(1))
        result = self.run_together(stack, provider)
        a = result['answer']
        self.assertEqual(a['count'], 1)
        self.assertEqual(a['confidence'], 'unverified')
        self.assertEqual(result['together']['winner'], 'analyst')
        self.assertEqual(result['together']['reconcile']['model'], 'claude-opus-5-5')
        rec = provider.calls('reconcile')
        self.assertEqual(len(rec), 1)
        self.assertEqual(rec[0]['model'], 'claude-opus-5-5')
        user = json.loads(rec[0]['messages'][0]['content'])
        self.assertEqual({user['candidate_1']['answer']['count'], user['candidate_2']['answer']['count']}, {1, 5})
        fast_candidate = next(user[k] for k in ('candidate_1', 'candidate_2') if user[k]['answer']['count'] == 5)
        self.assertEqual(fast_candidate['sql_run'], ['SELECT COUNT(*) AS count FROM measurement WHERE value > 9'])
        self.assertIn("question's own stated definitions", rec[0]['system'][0]['text'])
        self.assertTrue(any(g.get('reading') == 'Agere fast path' for g in a['grounding'] if isinstance(g, dict)))
        self.assertEqual(result['new_paid_api_calls'], 3)
        self.assertEqual(result['planner_receipt']['new_paid_api_calls'], 3)

    def test_disagree_reconcile_keeps_the_fast_answer(self):
        stack = FakeStack(self.temp.name, fast_result(answer(count=5)))
        result = self.run_together(stack, FakeProvider(lambda body: answer(count=1), choose(5)))
        self.assertEqual(result['answer']['count'], 5)
        self.assertEqual(result['answer']['status'], 'answered')
        self.assertEqual(result['together']['winner'], 'fast')

    def test_reconcile_abstain_is_partial_low_with_both_readings(self):
        stack = FakeStack(self.temp.name, fast_result(answer(count=5)))
        provider = FakeProvider(lambda body: answer(count=1),
                                lambda body: {'choice': 'abstain', 'reason': 'cannot tell', 'answer': None})
        result = self.run_together(stack, provider)
        a = result['answer']
        self.assertEqual((a['status'], a['confidence'], a['count']), ('partial', 'low', 5))
        readings = {g['reading']: g['answer']['count'] for g in a['grounding'] if isinstance(g, dict) and g.get('reading')}
        self.assertEqual(readings, {'Agere fast path': 5, 'Semantic analyst': 1})
        self.assertEqual(result['together']['decision'], 'unresolved')

    def test_engine_answer_is_never_replaced(self):
        table = [{'group': 'a', 'median_days': 30}]
        stack = FakeStack(self.temp.name, fast_result(answer('table', table=table), operation='survival'))
        provider = FakeProvider(lambda body: answer('table', table=[{'group': 'a', 'median_days': 45}]), choose(None))
        result = self.run_together(stack, provider)
        self.assertEqual(result['answer']['table'], table)
        self.assertEqual(result['answer']['status'], 'answered')
        self.assertEqual(result['answer']['confidence'], 'low')
        self.assertEqual(result['together']['decision'], 'engine_kept_lowered')
        self.assertEqual(provider.calls('reconcile'), [])
        # agreement or an engine answer the analyst also gives: confidence stays what the gate policy said
        stack2 = FakeStack(tempfile.mkdtemp(dir=self.temp.name), fast_result(answer('table', table=table), operation='survival'))
        result = self.run_together(stack2, FakeProvider(lambda body: answer('table', table=table)))
        self.assertEqual(result['answer']['confidence'], 'unverified')
        self.assertEqual(result['together']['decision'], 'engine_kept')
        # the analyst saying unsupported is a note, not a downgrade: it has no engines (BUILD_LOG step 14, 084d592)
        stack3 = FakeStack(tempfile.mkdtemp(dir=self.temp.name), fast_result(answer('table', table=table), operation='trajectory'))
        result = self.run_together(stack3, FakeProvider(lambda body: answer('unsupported', 'unsupported', missing=['x'])))
        self.assertEqual((result['answer']['table'], result['answer']['confidence']), (table, 'unverified'))
        self.assertEqual(result['together']['decision'], 'engine_only')

    def test_both_unsupported_abstains(self):
        stack = FakeStack(self.temp.name, fast_result(answer('unsupported', 'unsupported', missing=['no causal design'])))
        provider = FakeProvider(lambda body: answer('unsupported', 'unsupported', missing=['no counterfactual']))
        result = self.run_together(stack, provider)
        self.assertEqual(result['answer']['status'], 'unsupported')
        self.assertEqual(result['answer']['missing'], ['no causal design', 'no counterfactual'])
        self.assertEqual(result['together']['decision'], 'both_unsupported')
        self.assertEqual(provider.calls('reconcile'), [])

    def test_fast_error_ships_the_analyst_answer_unverified(self):
        stack = FakeStack(self.temp.name, error=ValueError('planner failed'))
        result = self.run_together(stack, FakeProvider(lambda body: answer(count=1)))
        self.assertEqual(result['answer']['count'], 1)
        self.assertEqual(result['answer']['confidence'], 'unverified')
        self.assertEqual(result['together']['winner'], 'analyst')
        self.assertEqual((result['new_paid_api_calls'], result['planner_receipt']['new_paid_api_calls']), (1, 1))
        # no analyst answer either: the fast path's error propagates
        stack2 = FakeStack(tempfile.mkdtemp(dir=self.temp.name), error=ValueError('planner failed'))
        with self.assertRaises(ValueError):
            self.run_together(stack2, FakeProvider(lambda body: 'not json'))

    def test_analyst_timeout_ships_the_fast_answer_unchanged(self):
        os.environ['AGERE_TOGETHER_BUDGET_S'] = '0.3'
        release = threading.Event()

        def slow(body):
            release.wait(10)
            return answer(count=2)
        fast = fast_result(answer(count=5))
        stack = FakeStack(self.temp.name, fast)
        try:
            start = time.perf_counter()
            result = self.run_together(stack, FakeProvider(slow))
            self.assertLess(time.perf_counter() - start, 5)
        finally:
            release.set()
            for _ in range(100):  # let the background analyst finish its ledger write before the temp dir goes
                if stack.ledger_path.exists():
                    db = sqlite3.connect(stack.ledger_path)
                    done = db.execute("SELECT COUNT(*) FROM calls WHERE status='validated'").fetchone()[0]
                    db.close()
                    if done:
                        break
                time.sleep(.05)
            time.sleep(.1)
        self.assertEqual(result['answer'], fast['answer'])
        self.assertEqual(result['together']['decision'], 'fast_unchanged')
        self.assertEqual(result['together']['analyst']['state'], 'timed_out')
        self.assertIsNone(result['planning_economics']['new_api_estimate_usd'])  # in-flight analyst cost is unknown
        self.assertEqual(result['new_paid_api_calls'], result['planner_receipt']['new_paid_api_calls'])

    def test_lock_released_while_waiting_and_reacquired(self):
        stack = FakeStack(self.temp.name, fast_result(answer(count=1)))
        acquired_by_other = []

        def analyst(body):
            # Another request thread can take the service lock while this request waits on the analyst.
            got = stack.answer_lock.acquire(timeout=5)
            acquired_by_other.append(got)
            if got:
                stack.answer_lock.release()
            return answer(count=1)
        stack.answer_lock.acquire()  # as service.do_POST does
        try:
            result = self.run_together(stack, FakeProvider(analyst))
            self.assertTrue(stack.answer_lock._is_owned())  # re-acquired before handle_compose's writes
        finally:
            stack.answer_lock.release()
        self.assertEqual(acquired_by_other, [True])
        self.assertEqual(result['together']['decision'], 'agree')

    def test_no_semantic_layer_ships_fast_unchanged(self):
        stack = FakeStack(self.temp.name, fast_result(answer(count=5)))
        (stack.path / 'semantic.sqlite3').unlink()
        provider = FakeProvider(lambda body: answer(count=1))
        result = self.run_together(stack, provider)
        self.assertEqual(result['answer']['count'], 5)
        self.assertEqual(result['together']['analyst']['state'], 'unavailable')
        self.assertEqual(provider.bodies, [])

    def test_model_defaults_and_overrides(self):
        self.assertEqual((together.semantic_model(), together.reconcile_model()), ('claude-haiku-5-5', 'claude-opus-5-5'))
        self.assertEqual((together.budget_s(), together.reconcile_budget_s()), (20.0, 20.0))
        with patch.dict(os.environ, {'AGERE_SEMANTIC_MODEL': 'claude-sonnet-5-5', 'AGERE_TOGETHER_BUDGET_S': 'nan'}):
            self.assertEqual(together.semantic_model(), 'claude-sonnet-5-5')
            self.assertEqual(together.budget_s(), together.DEFAULT_BUDGET_S)


class DispatchTests(unittest.TestCase):
    """Stack.compose: AGERE_TOGETHER=off (or no live models) is exactly the previous fast path."""

    class S(Stack):
        def __init__(self, allow_live):
            self.allow_live, self.calls, self.sentinel = allow_live, [], {'answer': {'count': 1}}

        def _compose(self, question, *, planning='auto'):
            self.calls.append((question, planning))
            return self.sentinel

    def test_flag_off_is_identical(self):
        with patch.dict(os.environ, {'AGERE_TOGETHER': 'off'}), patch.object(together, 'compose_together') as ct:
            s = self.S(True)
            self.assertIs(s.compose('q'), s.sentinel)
            self.assertIs(s.compose('q', planning='frontier'), s.sentinel)
            self.assertFalse(ct.called)
            self.assertEqual(s.calls, [('q', 'auto'), ('q', 'frontier')])

    def test_no_live_models_is_identical(self):
        with patch.dict(os.environ, {'AGERE_TOGETHER': 'on'}), patch.object(together, 'compose_together') as ct:
            s = self.S(False)
            self.assertIs(s.compose('q'), s.sentinel)
            self.assertFalse(ct.called)

    def test_flag_on_dispatches_auto_planning(self):
        with patch.dict(os.environ, {}, clear=False), patch.object(together, 'compose_together', return_value='t') as ct:
            os.environ.pop('AGERE_TOGETHER', None)  # default on
            s = self.S(True)
            self.assertEqual(s.compose('q'), 't')
            ct.assert_called_once_with(s, 'q', planning='auto')
            self.assertIs(s.compose('q', planning='scoped_local_candidate'), s.sentinel)


class WarehouseThreadTests(unittest.TestCase):
    def test_each_thread_has_its_own_read_only_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'semantic.sqlite3'
            make_semantic(path)
            wh = semantic_analyst.SemanticWarehouse(path, timeout_s=2)
            try:
                seen, barrier = {}, threading.Barrier(4)

                def work(i):
                    barrier.wait()
                    seen[i] = (id(wh.db), wh.query('SELECT COUNT(*) AS n FROM record')['rows'][0]['n'])
                threads = [threading.Thread(target=work, args=(i,)) for i in range(4)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()
                ids = [v[0] for v in seen.values()]
                self.assertEqual(len(set(ids)), 4)
                self.assertNotIn(id(wh.db), ids)
                self.assertEqual({v[1] for v in seen.values()}, {2})
                with self.assertRaises((sqlite3.Error, ValueError)):
                    wh.query('DELETE FROM record')
            finally:
                wh.close()
            self.assertEqual(wh._connections, [])


class ExampleProvenanceTests(unittest.TestCase):
    """Verified examples for the analyst never come from heldout sets, bench/capability or acceptance questions."""

    @staticmethod
    def norm(text):
        return re.sub(r'\W+', ' ', str(text or '').lower()).strip()

    def test_examples_exclude_heldout_and_capability_questions(self):
        examples = semantic_docs.load_examples()
        self.assertTrue(examples)
        self.assertEqual(semantic_docs.EXAMPLES_PATH, ROOT / 'config/agere/semantic_examples.json')
        protected_ids, protected_text = set(), set()
        files = sorted(glob.glob(str(ROOT / 'bench/questions/heldout*.json')))
        files += sorted(glob.glob(str(ROOT / 'bench/capability/**/*.json'), recursive=True))
        files += sorted(glob.glob(str(ROOT / 'bench/acceptance/*.json')))
        self.assertTrue(any('heldout10' in f for f in files))
        for f in files:
            data = json.loads(Path(f).read_text())
            items = data if isinstance(data, list) else data.get('questions', [data]) if isinstance(data, dict) else []
            for q in items:
                if not isinstance(q, dict):
                    continue
                if q.get('id') and 'acceptance' not in f:
                    protected_ids.add(q['id'])
                if q.get('question'):
                    protected_text.add(self.norm(q['question']))
        for e in examples:
            self.assertIn(e['source_split'], ('development', 'challenge'))
            self.assertNotIn(e['source_id'], protected_ids)
            self.assertNotIn(self.norm(e['question']), protected_text)
        for split in ('heldout10', 'heldout6', 'capability', 'acceptance'):
            with tempfile.TemporaryDirectory() as tmp:
                bad = Path(tmp) / 'bad.json'
                bad.write_text(json.dumps({'examples': [{'source_id': 'X1', 'source_split': split, 'sql': 'SELECT 1'}]}))
                with self.assertRaises(ValueError):
                    semantic_docs.load_examples(bad)


if __name__ == '__main__':
    unittest.main()


class DefaultPolicyTests(unittest.TestCase):
    """The shipped defaults (build log steps 6 and 10)."""

    def test_default_disagreement_policy_is_analyst_without_a_reconcile_call(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('AGERE_DISAGREE_POLICY', None)
            fast = {'answer': {'status': 'answered', 'answer_type': 'count', 'count': 5, 'unknown_count': 0, 'missing': [],
                               'grounding': []}, 'planner_receipt': {'steps': []}}
            analyst = {'answer': {'status': 'answered', 'answer_type': 'count', 'count': 7, 'unknown_count': 0,
                                  'missing': [], 'grounding': []}, 'tool_calls': []}
            with patch.object(together, '_reconcile', side_effect=AssertionError('no reconcile call by default')):
                result, receipt, rec = together.decide(None, 'q', fast, None, analyst, 'done')
            self.assertEqual(result['answer']['count'], 7)
            self.assertEqual(receipt['winner'], 'analyst')

    def test_unchecked_zero_is_withheld(self):
        os.environ.pop('AGERE_UNCHECKED_ZERO', None)
        fast = {'answer': {'status': 'answered', 'answer_type': 'count', 'count': 0, 'unknown_count': 0, 'missing': [],
                           'grounding': []}, 'planner_receipt': {'steps': []}}
        result, receipt, _ = together.decide(None, 'q', fast, None, {'error': 'timeout'}, 'timed_out')
        self.assertEqual(result['answer']['status'], 'partial')
        self.assertEqual(receipt['decision'], 'unchecked_zero')


class FormatRepairTests(unittest.TestCase):
    """Build log step 12: the product analyst repairs a non-JSON final reply; the bench default (0) still fails."""

    def _send_seq(self, texts):
        it = iter(texts)
        def send(body):
            return {'content': [{'type': 'text', 'text': next(it)}], 'stop_reason': 'end_turn', 'usage': {}}, 0.0, False
        return send

    def test_extracts_embedded_json_for_free(self):
        r = semantic_analyst.tool_loop('q', 's', [], None, send=self._send_seq(['Here you go: {"status": "answered", "answer_type": "count", "count": 3} done']),
                                       model='m', max_tools=2, format_retries=1)
        self.assertEqual(r['answer']['count'], 3)
        self.assertEqual(r['format_repair'], 'extracted')

    def test_asks_again_once(self):
        r = semantic_analyst.tool_loop('q', 's', [], None, send=self._send_seq(['no json here', '{"status": "answered", "answer_type": "count", "count": 4}']),
                                       model='m', max_tools=2, format_retries=1)
        self.assertEqual(r['answer']['count'], 4)
        self.assertEqual(r['format_repair'], 'asked_again')

    def test_bench_default_does_not_repair(self):
        r = semantic_analyst.tool_loop('q', 's', [], None, send=self._send_seq(['Here: {"count": 3}']), model='m', max_tools=2)
        self.assertIsNone(r['answer'])
        self.assertIn('JSONDecodeError', r['error'])


class HybridSlotTests(unittest.TestCase):
    """The hybrid core in the analyst slot (AGERE_HYBRID on, the default)."""

    def test_hybrid_is_the_default_analyst_with_a_longer_budget(self):
        with patch.dict(os.environ, {}, clear=False):
            for k in ('AGERE_HYBRID', 'AGERE_TOGETHER_BUDGET_S'):
                os.environ.pop(k, None)
            self.assertTrue(together.hybrid_on())
            self.assertEqual(together.budget_s(), together.DEFAULT_HYBRID_BUDGET_S)

    def test_hybrid_answers_map_to_the_shared_contract(self):
        base = {'interpretation': 'hybrid unanimous; 3/3 agree; 3 passed checks'}
        c = together._hybrid_contract(base | {'answer_type': 'count', 'answer': {'count': 7}})
        self.assertEqual((c['status'], c['count']), ('answered', 7))
        c = together._hybrid_contract(base | {'answer_type': 'patient_set', 'answer': {'patient_ids': ['b', 'a', 'a']}})
        self.assertEqual((c['patient_ids'], c['count']), (['a', 'b'], 2))
        c = together._hybrid_contract(base | {'answer_type': 'table', 'contracts': {'run1': {'group_by': ['product']}},
                                              'answer': {'table': [{'keys': ['medicaid'], 'value': 12}]}})
        self.assertEqual(c['table'], [{'product': 'medicaid', 'value': 12}])
        self.assertIsNone(together._hybrid_contract(base | {'answer_type': 'count', 'answer': None}))


class DeclineTests(unittest.TestCase):
    """The hybrid core says WHY it did not answer: unsupported (what is missing) or clarify (the readings)."""

    def test_declines_map_to_contract_statuses(self):
        base = {'interpretation': 'hybrid abstain', 'answer': None, 'answer_type': 'count'}
        u = together._hybrid_contract(base | {'decline': {'kind': 'unsupported', 'missing': ['no death records']}})
        self.assertEqual((u['status'], u['missing']), ('unsupported', ['no death records']))
        c = together._hybrid_contract(base | {'decline': {'kind': 'clarify', 'readings': ['by product', 'by line of business']}})
        self.assertEqual((c['status'], c['readings']), ('clarify', ['by product', 'by line of business']))
        self.assertIsNone(together._hybrid_contract(base | {'decline': None}))

    def test_clarify_from_analysts_is_asked_not_guessed(self):
        fast = {'answer': {'status': 'answered', 'answer_type': 'count', 'count': 5, 'unknown_count': 0, 'missing': [],
                           'grounding': []}, 'planner_receipt': {'steps': []}}
        analyst = {'answer': {'status': 'clarify', 'answer_type': 'count', 'readings': ['a', 'b'], 'unknown_count': 0,
                              'missing': [], 'grounding': []}, 'tool_calls': []}
        result, receipt, _ = together.decide(None, 'q', fast, None, analyst, 'done')
        self.assertEqual(result['answer']['status'], 'clarify')
        self.assertEqual(result['answer']['readings'], ['a', 'b'])
        self.assertEqual(receipt['decision'], 'clarify')


class DeferralTests(unittest.TestCase):
    """While the analysts answer, the fast path skips the paid hosted ladder; engine questions skip the analysts."""

    def test_deferral_scope_is_per_thread_and_restored(self):
        from clinical_core.agere.clinical import economical_planner as ep
        self.assertFalse(ep.deferring())
        with ep.defer_to_analyst():
            self.assertTrue(ep.deferring())
            with ep.defer_to_analyst(False):
                self.assertFalse(ep.deferring())
            self.assertTrue(ep.deferring())
        self.assertFalse(ep.deferring())

    def test_deferred_fast_path_ships_the_analysts_answer(self):
        fast = {'route': 'deferred_to_analyst', 'answer': {'status': 'unsupported', 'answer_type': 'unsupported', 'unknown_count': 0,
                'missing': [], 'grounding': []}, 'planner_receipt': {'steps': []}}
        analyst = {'answer': {'status': 'answered', 'answer_type': 'count', 'count': 7, 'unknown_count': 0, 'missing': [],
                              'grounding': []}, 'tool_calls': [], 'hybrid': {'interpretation': 'hybrid unanimous; 3/3 agree'}}
        result, receipt, _ = together.decide(None, 'q', fast, None, analyst, 'done')
        self.assertEqual((result['answer']['count'], receipt['decision']), (7, 'analysts_answered'))

    def test_usable_requires_an_answer_or_decline(self):
        self.assertTrue(together._usable({'answer': {'status': 'clarify'}}, 'done'))
        self.assertFalse(together._usable({'answer': None, 'error': 'hybrid abstained'}, 'done'))
        self.assertFalse(together._usable(None, 'timed_out'))


class PatientListTests(unittest.TestCase):
    def test_requested_patient_list_comes_from_the_analysts(self):
        fast = {'answer': {'status': 'answered', 'answer_type': 'table', 'table': [{'patient_id': 'p1'}], 'unknown_count': 0,
                           'missing': [], 'grounding': []}, 'planner_receipt': {'steps': []},
                'graph': {'nodes': [{'operation': 'outreach'}]}}
        analyst = {'answer': {'status': 'answered', 'answer_type': 'patient_set', 'patient_ids': ['p1', 'p2', 'p3'], 'count': 3,
                              'unknown_count': 0, 'missing': [], 'grounding': []}, 'tool_calls': []}
        result, receipt, _ = together.decide(None, 'q', fast, None, analyst, 'done', answer_type='patient_set')
        self.assertEqual((result['answer']['patient_ids'], receipt['decision']), (['p1', 'p2', 'p3'], 'analysts_answered_list'))
        result, receipt, _ = together.decide(None, 'q', fast, None, analyst, 'done')
        self.assertNotEqual(receipt['decision'], 'analysts_answered_list')
