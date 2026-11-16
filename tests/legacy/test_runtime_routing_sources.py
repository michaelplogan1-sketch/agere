"""Runtime routing uses versioned capabilities, never evaluation/legacy example stores."""
import unittest
from unittest.mock import patch
from clinical_core.agere.clinical import route_arbiter as R,engine_suite


class ForbiddenCorpusRoot:
    def __truediv__(self,value):return self
    def glob(self,*args):raise AssertionError('No evaluation corpus may be enumerated')
    def exists(self):raise AssertionError('No legacy templates may be inspected')


class RuntimeRoutingSources(unittest.TestCase):
    def setUp(self):
        self.examples=R._EXAMPLES.copy();self.index=R._INDEX.copy()
        R._EXAMPLES.clear();R._INDEX.clear()
        self.addCleanup(self.restore)

    def restore(self):
        R._EXAMPLES.clear();R._EXAMPLES.update(self.examples)
        R._INDEX.clear();R._INDEX.update(self.index)

    def test_registry_examples_do_not_read_legacy_or_evaluation_roots(self):
        with patch.object(R,'ROOT',ForbiddenCorpusRoot()):
            examples=R.examples()
        self.assertTrue(examples)
        self.assertTrue(all(kind in ('template','description') for rows in examples.values() for _,kind in rows))

    def test_retired_hooks_are_inert_without_filesystem_access(self):
        with patch.object(R,'ROOT',ForbiddenCorpusRoot()):
            self.assertEqual(R._acceptance(),[])
            self.assertEqual(R._learned_templates(),[])

    def test_registered_operation_templates_are_retained(self):
        examples=R.examples()
        for op,module in engine_suite.available().items():
            for template in getattr(module,'TEMPLATES',()):
                self.assertIn((template,'template'),examples[op])

    def test_native_denial_route_is_available_with_corpus_forbidden(self):
        with patch.object(R,'ROOT',ForbiddenCorpusRoot()):
            graph,receipt=engine_suite.compile_route('Predict which claims will get denied',{},'2026-09-30')
        self.assertEqual(graph['nodes'][0]['operation'],'denial_prediction')
        self.assertTrue(receipt)

    def test_missing_schema_reranker_does_not_create_a_corpus_dependency(self):
        with patch.object(R,'ROOT',ForbiddenCorpusRoot()):
            self.assertTrue(R._index())


if __name__=='__main__':unittest.main()
