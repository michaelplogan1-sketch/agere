import importlib.util,os,unittest
from pathlib import Path
from unittest.mock import patch
from clinical_core.agere.clinical import semantic_cache as native
from clinical_core.agere.clinical import semantic_cache as S,template_decoding as T
class CacheBoundary(unittest.TestCase):
 def test_isolated_runtime_never_reaches_benchmark_or_legacy_store_read(self):
  for env in [{'AGERE_COMPONENT_RELEASE_ROOT':'/tmp/explicit-new-registry'}, {'AGERE_COMPONENT_LEARNING':'on'}]:
   with self.subTest(env=env),patch.dict(os.environ,env,clear=True),patch.object(S,'is_benchmark',side_effect=AssertionError('No reserved corpus')),patch.object(S,'_verified',side_effect=AssertionError('No legacy learning rows')),patch.object(T,'store',side_effect=AssertionError('No legacy templates')):
    for m in [S,T]:
     value,receipt=m.lookup('/not-a-warehouse','Count received clinical observations',object());self.assertIsNone(value);self.assertIn('excluded',receipt['reason'])
 def test_no_false_claim_that_any_question_is_a_benchmark(self):
  with patch.dict(os.environ,{'AGERE_COMPONENT_LEARNING':'on'},clear=True):
   for m in [S,T]:
    _,r=m.lookup('/unused','Entirely new clinician wording',None);self.assertNotIn('benchmark question',r['reason'])
if __name__=='__main__':unittest.main()
