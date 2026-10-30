"""Subscription source preflight never generates, borrows a key or invents counts."""
import copy,io,json,os,tempfile,unittest,urllib.request
from pathlib import Path
from unittest.mock import patch
from clinical_core.agere import subscription_transport as T
from clinical_core.agere.clinical import case_synthesis as C,linked_claim_gateway as G
from clinical_core.agere.gateway import BudgetLedger,PersistentGateway
from clinical_core.contracts import digest

class SubscriptionPreflight(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)
  self.body={'model':G.claude_model(PersistentGateway.MODELS['claude']),'messages':[{'role':'user','content':json.dumps({'schema':G.CONTRACT,'stage':'saved_claims_and_exact_excerpts','synthetic':True})}],
   'system':'Authored synthetic test.','output_config':{},'max_tokens':8192}
  env=patch.dict(os.environ,{'AGERE_CLAUDE_TRANSPORT':'subscription','ANTHROPIC_API_KEY':'subscription-transport'});env.start();self.addCleanup(env.stop)
 def test_unknown_count_is_local_and_replays_without_generation_or_network(self):
  with patch.object(C.urllib.request,'urlopen',side_effect=AssertionError('No network')),patch.object(T,'run_cli',side_effect=AssertionError('No generation')):
   first=C.token_check(self.body,self.path,allow_live=True)
   before=next(self.path.glob('*-tokens.json')).read_bytes()
   again=C.token_check(self.body,self.path,allow_live=False)
  self.assertIsNone(first['input_tokens']);self.assertEqual(C.token_count_calls(first),0)
  self.assertFalse(first['provider_count_cached']);self.assertTrue(again['provider_count_cached'])
  self.assertEqual(C.token_count_calls(again),0);self.assertIsNone(first['reserved_output_tokens'])
  self.assertEqual(next(self.path.glob('*-tokens.json')).read_bytes(),before)
  self.assertTrue(C.valid_token_preflight(self.body,first))
 def test_exact_request_bytes_and_subscription_are_required_for_unknown_count(self):
  receipt=C.token_check(self.body,self.path,allow_live=True)
  changed=copy.deepcopy(self.body);changed['messages'][0]['content']+=' changed'
  self.assertFalse(C.valid_token_preflight(changed,receipt))
  for field,value in [('request_bytes',1),('schema','invented'),('transport','api'),('input_tokens',True)]:
   self.assertFalse(C.valid_token_preflight(self.body,{**receipt,field:value}))
  with patch.dict(os.environ,{'AGERE_CLAUDE_TRANSPORT':''}):
   self.assertFalse(C.valid_token_preflight(self.body,receipt))
   with self.assertRaisesRegex(ValueError,'token receipt'):C.token_check(self.body,self.path,allow_live=False)
 def test_unknowns_propagate_across_passes_and_invalid_measurements_fail(self):
  self.assertIsNone(C.input_token_total([{'input_tokens':7},{'input_tokens':None}]))
  self.assertEqual(C.input_token_total([{'input_tokens':7},{'input_tokens':5}]),12)
  self.assertEqual(C.input_token_total([]),0)
  for value in [True,-1,3.5]:
   with self.assertRaises(ValueError):C.input_token_total([{'input_tokens':None},{'input_tokens':value}])
  self.assertEqual(C.token_count_calls({'provider_count_cached':False}),1)
  self.assertEqual(C.token_count_calls({'provider_count_cached':False,'new_token_count_calls':0}),0)
 def test_claim_gateway_accepts_bound_subscription_preflight_and_meters_generation_once(self):
  C.token_check(self.body,self.path,allow_live=True)
  raw={'model':self.body['model'],'content':[{'type':'text','text':'synthetic accepted'}],
   'stop_reason':'end_turn','transport':'claude-code-subscription','usage':{'input_tokens':11,'output_tokens':7}}
  ledger=BudgetLedger(self.path/'ledger.sqlite3');self.addCleanup(ledger.close)
  with patch.object(G.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(raw).encode()))as wire:
   first,receipt=G.send(self.body,ledger,self.path,parse=lambda r:r['content'][0]['text'])
   again,cached=G.send(self.body,ledger,self.path,parse=lambda r:r['content'][0]['text'])
  self.assertEqual(first,again);self.assertEqual(wire.call_count,1)
  self.assertEqual(receipt['usage'],raw['usage']);self.assertTrue(cached['cache_hit'])
  self.assertEqual(ledger.db.execute('SELECT count(*) FROM calls').fetchone()[0],1)
 def test_complete_large_claim_request_uses_subscription_context_without_trimming(self):
  self.body['system']+=' Literal synthetic content.'*2000
  self.assertGreater(len(json.dumps(self.body).encode()),G.MAX_BYTES)
  before=copy.deepcopy(self.body);self.assertIsNone(G.maximum_request_bytes());G.validate(self.body)
  receipt=C.token_check(self.body,self.path,allow_live=True)
  self.assertIsNone(receipt['input_tokens']);self.assertTrue(C.valid_token_preflight(self.body,receipt))
  raw={'model':self.body['model'],'content':[{'type':'text','text':'complete synthetic response'}],
       'stop_reason':'end_turn','usage':{'input_tokens':11000,'output_tokens':11}}
  ledger=BudgetLedger(self.path/'large-ledger.sqlite3');self.addCleanup(ledger.close)
  with patch.object(G.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(raw).encode()))as send:
   result,_=G.send(self.body,ledger,self.path,parse=lambda r:r['content'][0]['text'])
   self.assertEqual(result,'complete synthetic response');self.assertEqual(send.call_count,1)
  self.assertEqual(self.body,before)
  with patch.dict(os.environ,{'AGERE_CLAUDE_TRANSPORT':''}),patch.object(G.urllib.request,'urlopen')as send:
   self.assertEqual(G.maximum_request_bytes(),G.MAX_BYTES)
   with self.assertRaisesRegex(ValueError,'32768'):G.validate(self.body)
   send.assert_not_called()
 def test_count_endpoint_is_never_translated_to_generation_or_api_fallback(self):
  real=urllib.request.urlopen;prior=T._original.copy();T._original.clear()
  try:
   with patch.object(T,'respond',side_effect=AssertionError('No generation'))as generate:
    T.install()
    req=urllib.request.Request('https://api.anthropic.com/v1/messages/count_tokens',data=b'{}')
    with self.assertRaisesRegex(ValueError,'count_tokens'):urllib.request.urlopen(req)
    generate.assert_not_called()
  finally:
   urllib.request.urlopen=real;T._original.clear();T._original.update(prior)

if __name__=='__main__':unittest.main()
