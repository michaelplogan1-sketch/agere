"""Native retrieval startup keeps exact index guards without whole-file copies."""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from clinical_core.agere.clinical import warehouse_retrieval as R


class ModelFixture:
    identity = 'model-fixture-no-weights-loaded'
    def __init__(self, path, *, cache, **kwargs): self.db = sqlite3.connect(cache)
    def close(self): self.db.close()


class StreamedIntegrity(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.vectors = np.arange(24, dtype=np.float32).reshape(8, 3)
        np.save(self.path/'vectors.npy', self.vectors)
        np.save(self.path/'dense-content-indices.npy', np.arange(8, dtype=np.int64))
        self.meta = {'schema': R.VERSION, 'synthetic': True,
                     'encoder_path': 'fixture', 'encoder_identity': ModelFixture.identity,
                     'unique_passage_texts': 8}
        for name, field in [('vectors.npy', 'vector_file_sha256'),
                            ('dense-content-indices.npy', 'dense_indices_sha256')]:
            self.meta[field] = hashlib.sha256((self.path/name).read_bytes()).hexdigest()
        self.save()

    def save(self): (self.path/'run.json').write_text(json.dumps(self.meta))

    def open(self):
        # Constructor and teardown execute normally; only expensive model
        # loading is replaced. A whole-file-copy regression fails this path.
        with patch.object(R, 'LocalEncoder', ModelFixture), patch.object(R, 'LocalReranker', ModelFixture), \
                patch.object(Path, 'read_bytes', side_effect=AssertionError('Whole-file allocation forbidden')):
            r = R.Retriever.__new__(R.Retriever)
            try:
                R.Retriever.__init__(r, self.path)
                self.assertIsInstance(r.vectors, np.memmap)
                np.testing.assert_array_equal(r.vectors, self.vectors)
                return r.meta
            finally:
                if hasattr(r, 'db'): r.db.close()
                if hasattr(r, 'encoder'): r.encoder.close()
                if getattr(r, 'reranker', None): r.reranker.close()

    def test_real_constructor_preserves_every_vector_without_whole_file_read(self):
        self.assertEqual(self.open(), self.meta)

    def test_changed_index_bytes_still_reject_before_use(self):
        for name, message in [('vectors.npy', 'Vector file content changed'),
                              ('dense-content-indices.npy', 'Dense content mapping changed')]:
            with self.subTest(file=name):
                path = self.path/name
                with path.open('r+b') as f:
                    f.seek(-1, 2); original = f.read(1); f.seek(-1, 2)
                    f.write(bytes([original[0] ^ 1]))
                with self.assertRaisesRegex(ValueError, message): self.open()
                with path.open('r+b') as f: f.seek(-1, 2); f.write(original)

    def test_missing_pinned_hash_still_rejects(self):
        del self.meta['vector_file_sha256']; self.save()
        with self.assertRaisesRegex(ValueError, 'Vector file content changed'): self.open()


if __name__ == '__main__': unittest.main()
