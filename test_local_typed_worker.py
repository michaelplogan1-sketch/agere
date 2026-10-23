"""Local typed interpreter worker: a promoted typed adapter is served with the short training-time prompt
(datasets.typed_messages), compiled and literal-checked; anything else falls back to the long-prompt interpreter.
No MLX (generation is injected)."""
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault('AGERE_LOCAL_TYPED', 'off')
os.environ.setdefault('AGERE_LOCAL_NORMALIZER', 'off')

from clinical_core.agere.clinical import local_request_worker as W, request_language as L  # noqa: E402
from test_schema_prefetch import warehouse  # noqa: E402

Q = 'Count claims where ever_denied = 1 in 2029 by payer'


class Worker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.catalog = warehouse(self.tmp.name)
        self.adapter = Path(self.tmp.name) / 'adapter'; self.adapter.mkdir()
        (self.adapter / 'adapters.safetensors').write_bytes(b'x')
        self.item = {'catalog': {'target': str(self.catalog.target), 'tables': self.catalog.tables, 'identity': self.catalog.identity,
                                 'stream_fields': self.catalog.stream_fields}, 'question': Q, 'population': None}

    def test_adapter_discovery(self):
        with mock.patch.dict(os.environ, {'AGERE_TYPED_ADAPTER': str(self.adapter)}):
            self.assertEqual(W.typed_adapter(), self.adapter)
        with mock.patch.dict(os.environ, {'AGERE_TYPED_ADAPTER': str(Path(self.tmp.name) / 'none')}):
            self.assertIsNone(W.typed_adapter())

    def test_short_prompt_compiles_and_checks(self):
        right = L.parse(self.catalog, Q)
        seen = []
        def gen(messages):
            seen.append(messages); return '```json\n' + json.dumps(right) + '\n```'
        contract, interp = W.interpret_short(self.catalog, Q, None, gen, self.adapter)
        self.assertTrue(interp['semantic_review']['equivalent'], interp['semantic_review'])
        self.assertIn('GROUP BY', contract['query'].upper())
        system, user = seen[0][0]['content'], json.loads(seen[0][1]['content'])
        from tools.training.datasets import TYPED_SYSTEM
        self.assertEqual(system, TYPED_SYSTEM)  # training == serving contract
        self.assertIn('claim', user['tables'])
        self.assertLess(interp['attempts'][0]['prompt_chars'], 6000)

    def test_handle_serves_adapter_then_falls_back(self):
        right = L.parse(self.catalog, Q)
        fallback = mock.Mock(return_value=({'kind': 'request'}, {'interpreter': 'long'}))
        with mock.patch.dict(os.environ, {'AGERE_TYPED_ADAPTER': str(self.adapter)}):
            contract, interp = W.handle(self.item, generate_factory=lambda a: (lambda m: json.dumps(right)), fallback=fallback)
            self.assertEqual(interp['interpreter'], 'qwen_typed_adapter'); fallback.assert_not_called()
            contract, interp = W.handle(self.item, generate_factory=lambda a: (lambda m: 'not json'), fallback=fallback)
            self.assertEqual(interp['interpreter'], 'long'); self.assertIn('typed_adapter_declined', interp)
        with mock.patch.object(W, 'typed_adapter', return_value=None):
            self.assertEqual(W.handle(self.item, fallback=fallback)[1]['interpreter'], 'long')  # no adapter: long prompt


FAKE = """
import json, os, sys, time
print(json.dumps({'ready': 'fake'}), flush=True)
loads = calls = 0
for line in sys.stdin:
    item = json.loads(line)
    if not loads: loads = 1  # the 'model load' happens once per process
    calls += 1
    q = item['question']
    if q == 'sleep': time.sleep(60)
    if q == 'die': os._exit(1)
    if q == 'over': print(json.dumps({'declined': 'local model declined: over budget'}), flush=True); continue
    print('stray library output', flush=True)
    print(json.dumps({'ok': [{'pid': os.getpid(), 'loads': loads, 'calls': calls}, {'env': item.get('env')}]}), flush=True)
"""


class Persistent(unittest.TestCase):
    def setUp(self):
        from clinical_core.agere.clinical import shared_planner as SP
        self.SP = SP
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        script = Path(self.tmp.name) / 'fake_worker.py'; script.write_text(FAKE)
        self.worker = SP.PersistentWorker([sys.executable, str(script)]); self.addCleanup(self.worker.stop)
        self.catalog = warehouse(self.tmp.name)

    def ask(self, q, limit='30'):
        with mock.patch.dict(os.environ, {'AGERE_LOCAL_INTERPRET_SECONDS': limit, 'AGERE_PROBE': 'x'}):
            return self.SP.isolated_local(self.catalog, q, None, worker=self.worker)

    def test_one_process_one_load_across_calls(self):
        a, b, c = (self.ask('q')[0] for _ in range(3))
        self.assertEqual({a['pid'], b['pid'], c['pid']}, {a['pid']})
        self.assertEqual((a['loads'], c['loads'], c['calls']), (1, 1, 3))
        self.assertEqual(self.worker.spawns, 1)

    def test_environment_switches_travel_with_the_request(self):
        self.assertEqual(self.ask('q')[1]['env']['AGERE_PROBE'], 'x')

    def test_timeout_yields_kills_child_and_next_call_works(self):
        first = self.ask('q')[0]
        started = time.monotonic()
        with self.assertRaises(self.SP.QuestionNotUnderstood) as cm: self.ask('sleep', limit='1')
        self.assertLess(time.monotonic() - started, 10); self.assertIn('time limit', str(cm.exception))
        self.assertIsNone(self.worker.proc)  # no half-finished generation keeps the GPU
        after = self.ask('q')[0]
        self.assertNotEqual(after['pid'], first['pid']); self.assertEqual(after['calls'], 1)

    def test_dead_worker_is_restarted(self):
        first = self.ask('q')[0]
        with self.assertRaises(self.SP.QuestionNotUnderstood): self.ask('die')
        self.assertNotEqual(self.ask('q')[0]['pid'], first['pid'])
        self.worker.proc.kill(); self.worker.proc.wait()  # killed between calls
        self.assertEqual(self.ask('q')[0]['calls'], 1)
        self.assertEqual(self.worker.spawns, 3)

    def test_decline_carries_its_reason(self):
        with self.assertRaises(self.SP.QuestionNotUnderstood) as cm: self.ask('over')
        self.assertIn('over budget', str(cm.exception)); self.assertIn('no reduced question', str(cm.exception))
        self.assertEqual(self.ask('q')[0]['calls'], 2)  # a decline leaves the worker up


class WorkerLoop(unittest.TestCase):
    def test_serve_replies_per_line_and_declines_over_budget(self):
        def handler(item):
            if item['question'] == 'big': W.fit_check(30_000, 700, 200_000)  # 6 GB > 4 GB
            if item['question'] == 'boom': raise RuntimeError('x')
            return {'a': 1}, {'b': 2}
        lines = '\n'.join(json.dumps({'question': q, 'env': {'AGERE_X': '1'}}) for q in ('ok', 'big', 'boom', 'ok')) + '\n'
        out = io.StringIO(); W.serve(io.StringIO(lines), out, handler)
        r = [json.loads(x) for x in out.getvalue().splitlines()]
        self.assertEqual(r[0], {'ok': [{'a': 1}, {'b': 2}]})
        self.assertIn('attention-cache budget', r[1]['declined']); self.assertIn('RuntimeError', r[2]['error'])
        self.assertIn('ok', r[3]); self.assertEqual(os.environ.get('AGERE_X'), '1')
        os.environ.pop('AGERE_X', None)

    def test_fit_check(self):
        W.fit_check(1000, 700, 144 * 1024)  # ~0.24 GB fits
        with self.assertRaises(W.LocalDeclined): W.fit_check(30_000, 700, 144 * 1024)  # ~4.2 GB does not
        W.fit_check(10**7, 700, None)  # unknown model geometry: no guess

    def test_resident_loads_once_and_evicts_on_new_key(self):
        built = []
        W._RESIDENT.clear()
        for key in ('a', 'a', 'a', 'b', 'b'): W.resident(key, lambda key=key: built.append(key) or key)
        self.assertEqual(built, ['a', 'b']); W._RESIDENT.clear()


if __name__ == '__main__':
    unittest.main()
