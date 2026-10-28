import json,unittest,tempfile
from pathlib import Path
from unittest.mock import patch
from clinical_core.agere import economics as E
class Pricing(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup);self.root=Path(self.t.name);p=self.root/'config/agere';p.mkdir(parents=True)
  raw=(Path(__file__).parent/'config/agere/pricing.json').read_text();(p/'pricing.json').write_text(raw)
  self.patch=patch.object(E,'ROOT',self.root);self.patch.start();self.addCleanup(self.patch.stop)
 def price(self,**extra):return E.usage_cost('claude-haiku-5-5',{'input_tokens':100000,'output_tokens':1000,**extra})
 def test_threshold_is_strictly_above_and_changes_output_and_all_input_rates(self):
  self.assertAlmostEqual(self.price()['estimated_usd'],.0105)
  self.assertEqual(self.price()['rate_tier'],'standard')
  p=self.price(input_tokens=100001);self.assertEqual(p['rate_tier'],'long_context');self.assertAlmostEqual(p['estimated_usd'],.0525005)
 def test_cached_reads_count_toward_full_prompt_threshold(self):
  p=self.price(input_tokens=1,cache_read_input_tokens=100000);self.assertAlmostEqual(p['estimated_usd'],.0075005)
 def test_cached_writes_count_toward_threshold(self):
  p=self.price(input_tokens=1,cache_creation_input_tokens=100000);self.assertAlmostEqual(p['estimated_usd'],.0650005)
 def test_hour_cache_is_not_counted_twice(self):
  p=self.price(input_tokens=1,cache_creation_input_tokens=100000,cache_creation={'ephemeral_1h_input_tokens':100000})
  self.assertAlmostEqual(p['estimated_usd'],.1025005)
 def test_split_cache_uses_full_length_and_correct_each_ttl(self):
  p=self.price(input_tokens=1,cache_creation_input_tokens=100000,cache_creation={'ephemeral_5m_input_tokens':50000,'ephemeral_1h_input_tokens':50000})
  self.assertAlmostEqual(p['estimated_usd'],.0837505)
 def test_unknown_or_inconsistent_counts_do_not_invent_a_price(self):
  self.assertIsNone(self.price(cache_creation_input_tokens=2,cache_creation={'ephemeral_1h_input_tokens':1}))
  for key in ['input_tokens','output_tokens','cache_read_input_tokens','cache_creation_input_tokens']:
   for value in [-1,True,'3',float('nan')]:self.assertIsNone(self.price(**{key:value}))
 def test_current_sonnet_read_rate(self):
  p=E.usage_cost('claude-sonnet-5-5',{'input_tokens':0,'output_tokens':0,'cache_read_input_tokens':1000000});self.assertAlmostEqual(p['estimated_usd'],.1)
 def test_typed_provider_and_reported_unknown_model_costs_preserved(self):
  p=E.usage_cost('jev-1.13.0',{'input_tokens':1000000,'output_tokens':0});self.assertAlmostEqual(p['estimated_usd'],.042)
  self.assertEqual(E.usage_cost('preset:fast',{'cost':{'total_cost':.01}})['estimated_usd'],.01)
  self.assertIsNone(E.usage_cost('unknown',{'input_tokens':1,'output_tokens':1}))
if __name__=='__main__':unittest.main()
