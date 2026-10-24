import os,sqlite3,unittest
from unittest.mock import patch
import test_analysis_graph as graph_fixture
from clinical_core.agere.clinical import analytic_registry as A,evaluation as E

class OptionalCacheEvaluation(unittest.TestCase):
 def setUp(self):
  self.fixture=graph_fixture.GraphExecution();self.fixture.setUp();self.addCleanup(self.fixture.tearDown)
 def test_analytics_return_results_without_saving_or_refusing_under_strict_audit(self):
  root=self.fixture.root;E.install_audit();self.addCleanup(E.uninstall_audit)
  with patch.dict(os.environ,{'AGERE_EVALUATION_STRICT':'1'}),E.scope('dev-cache-test',root):
   result=A.execute(root,'source_completeness')
   self.assertEqual(result['operator'],'source_completeness')
   self.assertFalse(result['cache_hit'])
   self.assertEqual(E.receipt()['write_attempts_by_store'],{})
  with sqlite3.connect(root/'stack.sqlite3') as db:self.assertEqual(db.execute('SELECT count(*) FROM result_cache').fetchone()[0],0)
  A.execute(root,'source_completeness')
  self.assertTrue(A.execute(root,'source_completeness')['cache_hit'])
