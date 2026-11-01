"""Real persistent retry identities preserve failed attempts and block duplicates."""
import io,json,os,sqlite3,tempfile,unittest,urllib.error
from pathlib import Path
from unittest.mock import patch
from clinical_core.agere import gateway as G
from clinical_core.agere.clinical import context_student as C
from clinical_core.providers import EvidenceTask
from clinical_core.contracts import digest

class ExplicitRetries(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)/'ledger.sqlite3';self.ledger=G.BudgetLedger(self.path);self.addCleanup(self.ledger.close)
 def row(self,key):return self.ledger.db.execute('SELECT * FROM calls WHERE id=?',(key,)).fetchone()
 def test_only_terminal_failures_get_separate_attempts_and_authorizations_are_immutable(self):
  self.ledger.reserve('root','jev','m');pending=self.row('root')
  with self.assertRaises(ValueError):self.ledger.authorize_retry('root','Explicit recovery')
  self.ledger.finish('root',error='DNS unavailable');failed=self.row('root');new=self.ledger.authorize_retry('root','Approved synthetic transport recovery')
  self.assertNotEqual(new['request_hash'],'root');self.assertEqual(self.row('root'),failed)
  with self.assertRaises(ValueError):self.ledger.authorize_retry('root','Would duplicate unused authorization')
  self.ledger.reserve(new['request_hash'],'jev','m')
  with self.assertRaises(ValueError):self.ledger.authorize_retry('root','Would duplicate pending request')
  self.ledger.finish(new['request_hash'],response={'ok':True})
  with self.assertRaises(ValueError):self.ledger.authorize_retry('root','Cannot replace a validated request')
  self.assertEqual(self.row('root'),failed);self.assertEqual(self.ledger.resolve_request_key('root'),new['request_hash'])
  for sql in ('UPDATE request_retries SET payload=payload','DELETE FROM request_retries'):
   with self.assertRaises(sqlite3.IntegrityError):
    with self.ledger.db:self.ledger.db.execute(sql)
 def test_typed_gateway_retries_only_after_authorization_and_reopens_exact_cache(self):
  task=EvidenceTask('Review authored evidence','2030-05-27',{'yes':'Supported','unknown':'Unknown'},[]);raw={'model':G.PersistentGateway.MODELS['jev'],'usage':{'input_tokens':1,'output_tokens':1}};calls=[]
  def transport(*args,**kwargs):
   calls.append(1)
   if len(calls)==1:raise urllib.error.URLError('Authored DNS failure')
   return io.BytesIO(json.dumps(raw).encode())
  with patch.dict(os.environ,{'TYPESAFE_API_KEY':'authored'}),patch.object(G.PersistentGateway,'_parse',return_value={'label':'yes'}):
   gateway=G.PersistentGateway(self.ledger,allow_live=True,transport=transport)
   with self.assertRaises(urllib.error.URLError):gateway.classify('jev',task)
   failed=self.ledger.db.execute('SELECT * FROM calls').fetchone();key=failed[0]
   with self.assertRaises(ValueError):gateway.classify('jev',task)
   self.assertEqual(len(calls),1)
   new=self.ledger.authorize_retry(key,'Explicit synthetic recovery');actual=gateway.classify('jev',task)
   self.assertEqual(actual['request_hash'],new['request_hash']);self.assertEqual(self.row(key),failed)
   other=G.BudgetLedger(self.path)
   try:
    hit=G.PersistentGateway(other,allow_live=True,transport=lambda *a,**k:self.fail('Validated retry must replay')).classify('jev',task)
    self.assertTrue(hit['cache_hit']);self.assertEqual(hit['request_hash'],new['request_hash'])
   finally:other.close()
   self.assertEqual(len(calls),2);self.assertEqual(self.ledger.receipt()['calls'],{'failed':1,'validated':1})
 def test_existing_transport_only_continuation_preserves_original_context_student_attempt(self):
  body={'model':G.PersistentGateway.MODELS['perplexity'],'messages':[]};out=Path(self.tmp.name)/'receipts';raw={'model':body['model'],'usage':{'input_tokens':1,'output_tokens':1}}
  with patch.dict(os.environ,{'PERPLEXITY_API_KEY':'authored'}):
   with self.assertRaisesRegex(RuntimeError,'DNS failure'):C.send(body,'perplexity',self.ledger,out,parse=lambda r:r,transport=lambda *a,**k:(_ for _ in()).throw(urllib.error.URLError('Authored DNS failure')))
   old=self.ledger.db.execute('SELECT * FROM calls').fetchone()
   value,receipt=C.send(body,'perplexity',self.ledger,out,parse=lambda r:r,transport=lambda *a,**k:io.BytesIO(json.dumps(raw).encode()))
   self.assertEqual(self.row(old[0]),old);self.assertNotEqual(receipt['request_hash'],old[0]);self.assertEqual(value,raw)
   value,hit=C.send(body,'perplexity',self.ledger,out,parse=lambda r:r,transport=lambda *a,**k:self.fail('No additional provider call'))
   self.assertTrue(hit['cache_hit']);self.assertEqual(self.ledger.receipt()['calls'],{'failed':1,'validated':1})
 def test_response_bearing_failure_is_not_automatically_retried(self):
  body={'model':G.PersistentGateway.MODELS['perplexity'],'messages':[]};out=Path(self.tmp.name)/'receipts';raw={'model':body['model']}
  with patch.dict(os.environ,{'PERPLEXITY_API_KEY':'authored'}):
   with self.assertRaisesRegex(RuntimeError,'Invalid response'):C.send(body,'perplexity',self.ledger,out,parse=lambda r:(_ for _ in()).throw(ValueError('Invalid response')),transport=lambda *a,**k:io.BytesIO(json.dumps(raw).encode()))
   old=self.ledger.db.execute('SELECT * FROM calls').fetchone()
   with self.assertRaises(ValueError):C.send(body,'perplexity',self.ledger,out,parse=lambda r:r,transport=lambda *a,**k:self.fail('Failed response stays protected'))
   self.assertEqual(self.row(old[0]),old);self.assertEqual(self.ledger.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0],1)

if __name__=='__main__':unittest.main()
