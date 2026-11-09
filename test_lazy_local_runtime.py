"""Pinned local models stay unloaded for native metadata and exact cache hits."""
import builtins
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from clinical_core.contracts import digest
from clinical_core.local_encoders import LocalEncoder, LocalReranker


class LazyRuntime(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        (self.path/'config.json').write_text(json.dumps({'hidden_size': 3, 'max_position_embeddings': 128}))
        (self.path/'model.safetensors').write_bytes(b'authored-loader-fixture-not-model-weights')
        self.manifest = {'repo': 'fixture', 'files': {p.name: {
            'bytes': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in self.path.iterdir()}}
        (self.path/'agere-model.json').write_text(json.dumps(self.manifest))
        self.encoder = LocalEncoder(self.path, pooling='mean', cache=self.path/'cache.sqlite3')
        self.ranker = LocalReranker(self.path, cache=self.path/'cache.sqlite3')
        self.addCleanup(self.encoder.close); self.addCleanup(self.ranker.close)

    def without_tensor_imports(self):
        original = builtins.__import__
        def guarded(name, *a, **kw):
            if name.split('.')[0] in ('torch', 'transformers'):
                raise AssertionError('Native/cache path imported tensor runtime: ' + name)
            return original(name, *a, **kw)
        return patch('builtins.__import__', side_effect=guarded)

    def test_constructor_identities_and_empty_encode_require_no_tensor_runtime(self):
        with self.without_tensor_imports():
            encoder = LocalEncoder(self.path, pooling='mean', cache=self.path/'other.sqlite3')
            ranker = LocalReranker(self.path, cache=self.path/'other.sqlite3')
            try:
                self.assertEqual(encoder.identity, digest({'manifest': self.manifest, 'pooling': 'mean', 'version': 1}))
                self.assertEqual(ranker.identity, digest({'manifest': self.manifest, 'runtime': 'raw-logit-no-truncation/1'}))
                self.assertEqual(encoder.encode([]).shape, (0, 3))
                self.assertFalse(encoder.weights_loaded or ranker.weights_loaded)
            finally: encoder.close(); ranker.close()

    def test_existing_vector_and_ranking_cache_values_are_exact_without_model_load(self):
        vector = np.array([.2, .4, .6], dtype=np.float32)
        key = digest([self.encoder.identity, 'exact source'])
        self.encoder.db.execute('INSERT INTO encoder_vectors VALUES (?,?,?)', (key, 3, vector.tobytes()))
        self.encoder.db.commit()
        row = {'text': 'exact source', 'note_id': 'r1', 'start': 0, 'protected': False}
        self.ranker.db.execute('INSERT INTO reranker_scores VALUES (?,?)',
                               (digest([self.ranker.identity, 'question', row['text']]), .75))
        self.ranker.db.commit()
        with self.without_tensor_imports():
            np.testing.assert_array_equal(self.encoder.encode(['exact source', 'exact source']), np.stack([vector, vector]))
            self.assertEqual(self.ranker.rank('question', [row]), [{**row, 'reranker_logit': .75}])
        self.assertFalse(self.encoder.weights_loaded or self.ranker.weights_loaded)
        self.assertEqual(self.encoder.stats['texts_encoded'], 0)
        self.assertEqual(self.ranker.stats['pairs_scored'], 0)

    def test_delayed_model_access_loads_once_with_same_pinned_shape(self):
        model = SimpleNamespace(config=SimpleNamespace(hidden_size=3, max_position_embeddings=128))
        for runtime in (self.encoder, self.ranker):
            with patch.object(runtime, '_load_model', return_value=model) as load:
                self.assertFalse(runtime.weights_loaded)
                self.assertIs(runtime.model, model); self.assertIs(runtime.model, model)
                self.assertTrue(runtime.weights_loaded); load.assert_called_once_with()

    def test_delayed_access_rejects_changed_source_before_factory(self):
        with (self.path/'model.safetensors').open('ab') as f: f.write(b'changed')
        with patch.object(self.encoder, '_load_model', side_effect=AssertionError('No changed weights may load')) as load:
            with self.assertRaisesRegex(ValueError, 'Installed model content changed'):
                self.encoder.model
            load.assert_not_called()

    def test_cache_dimension_corruption_does_not_trigger_an_unnecessary_model_load(self):
        key = digest([self.encoder.identity, 'bad cached source'])
        self.encoder.db.execute('INSERT INTO encoder_vectors VALUES (?,?,?)',
                                (key, 2, np.ones(2, dtype=np.float32).tobytes())); self.encoder.db.commit()
        with self.without_tensor_imports(), self.assertRaisesRegex(ValueError, 'Invalid cached vector'):
            self.encoder.encode(['bad cached source'])
        self.assertFalse(self.encoder.weights_loaded)


if __name__ == '__main__': unittest.main()
