import importlib.util,unittest,tempfile,json
from pathlib import Path
from unittest.mock import patch
from clinical_core.agere.clinical import chief_staff as C
class Briefing(unittest.TestCase):
 def setUp(self):
  t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.path=Path(t.name);self.source={'as_of':'2030-01-01','received':'fixture'};self.contexts={};self.n=0
  def compose(stack,q,**kw):return {'answer':{'status':'answered','patient_ids':['A'],'table':[{'count':1,'sum_allowed_cents':100}]},'question':q}
  def capture(path,q,result):
   self.n+=1;key='ANS-'+format(self.n,'024x');self.contexts[key]={'question':q,'source':self.source};return {'id':key}
  def load(path,key,**kw):
   if key not in self.contexts:raise ValueError('Missing saved context')
   return self.contexts[key]
  self.ps=[patch.object(C.A,'source_identity',return_value=self.source),patch.object(C.S,'compose',side_effect=compose),patch.object(C.A,'capture',side_effect=capture),patch.object(C.A,'load',side_effect=load)]
  _,self.compose,self.capture,self.load=[p.start()for p in self.ps]
  for p in self.ps:self.addCleanup(p.stop)
 def test_intact_contexts_reuse_without_recompute(self):
  a=C.cached(self.path);b=C.cached(self.path);self.assertFalse(a['cache_hit']);self.assertTrue(b['cache_hit']);self.assertEqual(self.compose.call_count,5);self.assertEqual(b['cards'],a['cards'])
 def test_missing_answer_rebuilds_cards_and_all_links_reopen(self):
  a=C.cached(self.path);self.contexts.pop(a['cards'][0]['context_id']);b=C.cached(self.path);self.assertFalse(b['cache_hit']);self.assertEqual(self.compose.call_count,10)
  for c in b['cards']:self.assertIn(c['context_id'],self.contexts)
 def test_wrong_answer_link_rebuilds_instead_of_serving_it(self):
  a=C.cached(self.path);self.contexts[a['cards'][0]['context_id']]['question']='Different question';b=C.cached(self.path);self.assertFalse(b['cache_hit'])
if __name__=='__main__':unittest.main()
