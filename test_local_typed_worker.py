"""Local typed interpreter worker: a promoted typed adapter is served with the short training-time prompt
(datasets.typed_messages), compiled and literal-checked; anything else falls back to the long-prompt interpreter.
No MLX (generation is injected)."""
import json
import os
import tempfile
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


if __name__ == '__main__':
    unittest.main()
