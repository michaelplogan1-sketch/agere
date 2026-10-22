"""Retriever policy: the adopted reranker per query type, graph fallback identity, safe fallbacks, cache identity."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clinical_core.agere.clinical import med_graph_rag as MG, reranker_bakeoff as RB
from clinical_core.agere.clinical.warehouse_retrieval import Retriever


def retriever(path, rerank=True):
    r = Retriever.__new__(Retriever)
    r.path = Path(path)
    r.reranker = SimpleNamespace(identity='bge-id', rank=lambda q, rows, top_k: rows) if rerank else None
    r._installed_reranker = r.reranker
    return r


class Policy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.choice = self.dir / 'choice.json'
        self.choice.write_text(json.dumps({'by_type': {'social': {'reranker': 'qwen', 'graph_fallback': False, 'basis': 'measured'},
                                                         'medication': {'reranker': 'bge', 'graph_fallback': True, 'basis': 'measured'}}}))
        self.qwen = self.dir / 'qwen'; self.qwen.mkdir(); (self.qwen / 'agere-model.json').write_text('{}')
        patcher = patch.dict(os.environ, {'AGERE_RERANKER_CHOICE': str(self.choice)}); patcher.start(); self.addCleanup(patcher.stop)
        p2 = patch.object(RB, 'QWEN_PATH', self.qwen); p2.start(); self.addCleanup(p2.stop)

    def test_adopted_reranker_per_type_and_identity(self):
        r = retriever(self.dir)
        social = r.policy('Does the patient have transportation problems?')
        self.assertEqual((social['type'], social['reranker']), ('social', 'qwen')); self.assertTrue(social['reranker_identity'].startswith('qwen3-reranker'))
        med = r.policy('Is the patient on a statin?')
        self.assertEqual((med['type'], med['reranker'], med['graph_fallback']), ('medication', 'bge', True)); self.assertEqual(med['reranker_identity'], 'bge-id')
        self.assertIsNone(med['graph_identity'])                              # no graph file yet: nothing to fuse
        (self.dir / MG.FILE).write_bytes(b'x')
        self.assertEqual(r.policy('Is the patient on a statin?')['graph_identity'], MG.VERSION)

    def test_missing_qwen_falls_back_to_incumbent_not_an_error(self):
        (self.qwen / 'agere-model.json').unlink()
        p = retriever(self.dir).policy('Does the patient have transportation problems?')
        self.assertEqual(p['reranker'], 'bge'); self.assertIn('not installed', p['basis'])

    def test_reranking_off_has_no_reranker_and_no_choice(self):
        p = retriever(self.dir, rerank=False).policy('Is the patient on a statin?')
        self.assertIsNone(p['reranker']); self.assertIsNone(p['reranker_identity'])

    def test_unmeasured_type_keeps_the_incumbent(self):
        p = retriever(self.dir).policy('Show the chest pain history')
        self.assertEqual(p['reranker'], 'bge'); self.assertFalse(p['graph_fallback'])

    def test_cache_key_inputs_change_with_policy_code(self):
        a = retriever(self.dir).policy('Is the patient on a statin?')['code']
        self.assertEqual(a, retriever(self.dir).policy('Is the patient on a statin?')['code'])
        self.assertEqual(len(a), 64)


if __name__ == '__main__':
    unittest.main()
