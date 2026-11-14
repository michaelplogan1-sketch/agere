"""Observed rate limits pace novel work without losing attempts or cache reads."""
import io,json,os,tempfile,unittest,urllib.error
from pathlib import Path
from unittest.mock import patch
from clinical_core.agere import gateway as G
from clinical_core.providers import EvidenceTask
from clinical_core.agere.clinical import system_one as S

class Response:
    def __init__(self,provider):self.provider=provider
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self):return json.dumps({'model':G.PersistentGateway.MODELS[self.provider],'usage':{'input_tokens':1,'output_tokens':1}}).encode()

class Throughput(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.ledger=G.BudgetLedger(Path(tmp.name)/'calls.sqlite3');self.addCleanup(self.ledger.close)
        self.now=100.;self.http=[];self.code=None;self.headers={};self.during=None
        for p in [patch.dict(G._THROUGHPUT_FAILURES,{},clear=True),patch.dict(G._BILLING_FAILURES,{},clear=True),
                  patch.dict(os.environ,{'PERPLEXITY_API_KEY':'authored-throughput','TYPESAFE_API_KEY':'authored-alternative','AGERE_PROVIDER_BACKOFF_SECONDS':'2'}),
                  patch.object(G.time,'monotonic',side_effect=lambda:self.now),
                  patch.object(G.PersistentGateway,'_parse',return_value={'label':'yes','probabilities':{'yes':.95,'unknown':.05},'evidence_ids':['e'],'uncertain':False})]:
            p.start();self.addCleanup(p.stop)
        def transport(request,**kwargs):
            provider='jev'if 'typesafe.ai'in request.full_url else 'perplexity';self.http.append(provider)
            if self.during:
                action=self.during;self.during=None;action()
            if self.code and provider=='perplexity':
                raise urllib.error.HTTPError(request.full_url,self.code,'Authored rejection',self.headers,io.BytesIO(b'{"error":{"type":"too_many_requests"}}'))
            return Response(provider)
        self.transport=transport;self.gateway=G.PersistentGateway(self.ledger,allow_live=True,transport=transport)
    def task(self,name):return EvidenceTask(name,'2030-05-27',{'yes':'Supported','unknown':'Unknown'},[{'id':'e','date':'2030-01-01','text':'Authored source.','note_id':'e'}])
    def fail(self,name='Rejected'):
        self.code=429
        with self.assertRaises(urllib.error.HTTPError)as e:self.gateway.classify('perplexity',self.task(name))
        return e.exception
    def blocked(self,name='Different request'):
        with self.assertRaises(G.ProviderUnavailable)as e:self.gateway.classify('perplexity',self.task(name))
        self.assertFalse(e.exception.provider_receipt['attempted'])
        self.assertEqual(e.exception.kind,'rate_limit')
        return e.exception.provider_receipt
    def test_many_follow_on_requests_skip_transport_and_preserve_cache_and_original_attempt(self):
        task=self.task('Cached');self.gateway.classify('perplexity',task)
        failure=self.fail();before=self.ledger.db.execute('SELECT * FROM calls ORDER BY rowid').fetchall()
        for i in range(603):
            receipt=self.blocked(str(i));self.assertEqual(receipt['blocked_by_request_hash'],failure.provider_receipt['request_hash'])
        self.assertTrue(self.gateway.classify('perplexity',task)['cache_hit'])
        self.assertEqual(self.http,['perplexity','perplexity'])
        self.assertEqual(before,self.ledger.db.execute('SELECT * FROM calls ORDER BY rowid').fetchall())
    def test_retry_after_seconds_and_http_date_are_retained_and_obeyed(self):
        for header in ('10','Thu, 01 Jan 1970 00:16:50 GMT'):
            with self.subTest(header=header),patch.dict(G._THROUGHPUT_FAILURES,{},clear=True),patch.object(G.time,'time',return_value=1000.):
                self.headers={'Retry-After':header};self.now=100.;failure=self.fail(header)
                self.assertEqual(self.blocked()['retry_after_seconds'],10.)
                raw=json.loads(self.ledger.db.execute('SELECT response FROM calls WHERE id=?',(failure.provider_receipt['request_hash'],)).fetchone()[0])
                self.assertEqual(raw['provider_error']['retry_after'],header)
                self.now=110.;self.code=None
                self.assertEqual(self.gateway.classify('perplexity',self.task('Recovery '+header))['label'],'yes')
    def test_repeated_recovery_failure_backs_off_and_success_resets(self):
        self.fail();self.assertEqual(self.blocked()['retry_after_seconds'],2.)
        self.now=102.;self.fail('Recovery failed');self.assertEqual(self.blocked()['retry_after_seconds'],4.)
        self.now=106.;self.code=None;self.gateway.classify('perplexity',self.task('Recovery succeeded'))
        self.fail('New rate limit');self.assertEqual(self.blocked()['retry_after_seconds'],2.)
    def test_one_probe_shared_between_gateway_instances_and_nonrate_failure_releases_it(self):
        self.fail();self.now=102.;self.code=400
        other=G.PersistentGateway(self.ledger,allow_live=True,transport=self.transport)
        def overlap():
            with self.assertRaises(G.ProviderUnavailable)as e:other.classify('perplexity',self.task('Overlapping probe'))
            self.assertTrue(e.exception.provider_receipt['recovery_probe_in_flight'])
        self.during=overlap
        with self.assertRaises(urllib.error.HTTPError):self.gateway.classify('perplexity',self.task('Invalid recovery request'))
        self.code=None;self.gateway.classify('perplexity',self.task('Next recovery'))
        self.assertEqual(len(self.http),3)
    def test_new_credential_can_probe_and_nonrate_errors_do_not_cool_down(self):
        self.fail()
        with patch.dict(os.environ,{'PERPLEXITY_API_KEY':'different-authored-account'}):
            self.code=400
            with self.assertRaises(urllib.error.HTTPError):self.gateway.classify('perplexity',self.task('Bad schema'))
            self.code=None;self.assertEqual(self.gateway.classify('perplexity',self.task('Good schema'))['label'],'yes')
        self.blocked()
    def test_skipped_calls_retain_dispositions_and_use_existing_typed_fallback(self):
        self.fail()
        with patch.object(S,'_gateway',return_value=self.gateway):
            result=S.decide('Alternative request',{'yes':'Supported'},[{'id':'e','text':'Source'}],as_of='2030-05-27',order=['perplexity','jev'],ledger=self.ledger)
        self.assertEqual(result['decided_by'],'jev');self.assertFalse(result['steps'][0]['attempted'])
        self.assertEqual(result['steps'][0]['reason'],'throughput_backpressure')
        self.assertEqual(self.http,['perplexity','jev'])
    def test_failed_key_protection_is_not_bypassed_by_cooldown_expiry(self):
        self.fail();self.now=102.;self.code=None
        with self.assertRaises(ValueError):self.gateway.classify('perplexity',self.task('Rejected'))
        self.assertEqual(len(self.http),1)
        self.gateway.classify('perplexity',self.task('Novel recovery'))
        self.assertEqual(len(self.http),2)
    def test_invalid_retry_after_uses_backoff_without_losing_failure(self):
        for header in ('NaN','garbage','-1','0'):
            with self.subTest(header=header),patch.dict(G._THROUGHPUT_FAILURES,{},clear=True):
                self.headers={'Retry-After':header};self.fail(header)
                self.assertEqual(self.blocked()['retry_after_seconds'],2.)

if __name__=='__main__':unittest.main()
