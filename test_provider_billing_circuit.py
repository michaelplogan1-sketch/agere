"""A billing failure blocks new inference, never exact cached evidence or fallback."""
import io,json,os,tempfile,unittest,urllib.error
from pathlib import Path
from unittest.mock import patch
from clinical_core.agere import gateway as G
from clinical_core.providers import EvidenceTask
from clinical_core.agere.clinical import system_one as S,system_one_gate as SG

class Response:
    def __init__(self,provider):self.raw={'model':G.PersistentGateway.MODELS[provider],'usage':{'input_tokens':1,'output_tokens':1}}
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self):return json.dumps(self.raw).encode()

class BillingCircuit(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.ledger=G.BudgetLedger(Path(self.tmp.name)/'provider.sqlite3');self.addCleanup(self.ledger.close)
        for p in [patch.dict(G._BILLING_FAILURES,{},clear=True),patch.dict(os.environ,{'TYPESAFE_API_KEY':'authored-billing-key','PERPLEXITY_API_KEY':'authored-fallback-key'}),
                  patch.object(G.PersistentGateway,'_parse',return_value={'label':'yes','probabilities':{'yes':.95,'unknown':.05},'evidence_ids':['e'],'uncertain':False})]:
            p.start();self.addCleanup(p.stop)
        self.calls=[];self.code=None
        def transport(request,**kwargs):
            provider='jev'if 'typesafe.ai'in request.full_url else 'perplexity';self.calls.append(provider)
            if provider=='jev' and self.code:
                raise urllib.error.HTTPError(request.full_url,self.code,'Authored failure',{},io.BytesIO(b'{"detail":{"error_type":"billing_error","message":"No credits"}}'))
            return Response(provider)
        self.gateway=G.PersistentGateway(self.ledger,allow_live=True,transport=transport)
    def task(self,name):return EvidenceTask(name,'2030-05-27',{'yes':'Supported','unknown':'Unknown'},[{'id':'e','date':'2030-01-01','text':'Authored source.','note_id':'e'}])
    def test_exact_success_cache_survives_billing_and_new_requests_are_not_reserved(self):
        task=self.task('Cached supported source');self.gateway.classify('jev',task)
        self.code=402
        with self.assertRaises(urllib.error.HTTPError):self.gateway.classify('jev',self.task('Billing rejection'))
        with self.assertRaises(G.ProviderUnavailable)as failure:self.gateway.classify('jev',self.task('Different live request'))
        self.assertEqual(failure.exception.kind,'billing');self.assertFalse(failure.exception.provider_receipt['attempted'])
        self.assertTrue(self.gateway.classify('jev',task)['cache_hit']);self.assertEqual(self.calls,['jev','jev'])
        self.assertEqual(self.ledger.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0],2)
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM calls WHERE status='failed'").fetchone()[0],1)
    def test_existing_fallback_runs_and_skipped_billing_calls_are_not_paid_attempts(self):
        self.code=402
        with patch.object(S,'_gateway',return_value=self.gateway):
            first=S.decide('First task',{'yes':'Supported'},[{'id':'e','text':'Source'}],as_of='2030-05-27',order=['jev','perplexity'],ledger=self.ledger)
            second=S.decide('Different task',{'yes':'Supported'},[{'id':'e','text':'Source'}],as_of='2030-05-27',order=['jev','perplexity'],ledger=self.ledger)
        self.assertEqual(first['decided_by'],'perplexity');self.assertEqual(second['decided_by'],'perplexity')
        self.assertFalse(second['steps'][0]['attempted']);self.assertEqual(SG.cost(second)[0],1)
        self.assertEqual(self.calls,['jev','perplexity','perplexity'])
    def test_new_credential_can_probe_service_and_request_errors_do_not_trip_circuit(self):
        self.code=402
        with self.assertRaises(urllib.error.HTTPError):self.gateway.classify('jev',self.task('Old account'))
        self.code=None
        with patch.dict(os.environ,{'TYPESAFE_API_KEY':'different-authored-account'}):
            self.assertEqual(self.gateway.classify('jev',self.task('New account'))['label'],'yes')
        with patch.dict(G._BILLING_FAILURES,{},clear=True):
            self.code=400
            with self.assertRaises(urllib.error.HTTPError):self.gateway.classify('jev',self.task('Bad request'))
            self.code=None;self.assertEqual(self.gateway.classify('jev',self.task('Valid request'))['label'],'yes')

if __name__=='__main__':unittest.main()
