"""Source transport capacity and exact cached judgments; mocked HTTP only."""
import io,json,os,tempfile,unittest,urllib.error
from pathlib import Path
from unittest.mock import patch
from clinical_core.providers import EvidenceTask
from clinical_core.agere import gateway as G
from clinical_core.agere.clinical import system_one as S

class Response:
    def __init__(self,value):self.value=value
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self):return json.dumps(self.value).encode()

class SourceContext(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.ledger=G.BudgetLedger(Path(self.tmp.name)/'calls.sqlite3');self.addCleanup(self.ledger.close)
        self.requests=[];self.reject_standard=False;self.unknown_perplexity=set()
        for p in [patch.dict(os.environ,{'TYPESAFE_API_KEY':'authored-context-key','PERPLEXITY_API_KEY':'authored-context-key'}),
                  patch.dict(G._BILLING_FAILURES,{},clear=True),patch.dict(G._THROUGHPUT_FAILURES,{},clear=True)]:
            p.start();self.addCleanup(p.stop)
        self.gateway=G.PersistentGateway(self.ledger,allow_live=True,transport=self.transport)
    def transport(self,request,**kw):
        body=json.loads(request.data);provider='jev'if'typesafe.ai'in request.full_url else'perplexity'
        headers={k.lower():v for k,v in request.header_items()};self.requests.append((provider,headers,body))
        if provider=='jev'and self.reject_standard and 'typesafe-danger-zone-long-context'not in headers:
            raise urllib.error.HTTPError(request.full_url,400,'Too long',{},io.BytesIO(b'{"detail":{"error_type":"max_tokens_exceeded"}}'))
        answers={}
        for key,q in body['questions'].items():
            choice='unknown'if provider=='perplexity'and key in self.unknown_perplexity else'supported'
            answers[key]={'type':'choice','choice':choice,'probabilities':{k:float(k==choice)for k in q['criteria']}}
        return Response({'model':body['model'],'answers':answers,'usage':{'input_tokens':1,'output_tokens':0}})
    def task(self,text,mode=None,questions=None):
        return EvidenceTask('source task','2030-05-27',{'unknown':S.UNKNOWN},
            [{'id':'e','date':'2030-01-01','note_id':'e','text':text}],select_source=False,long_context=mode,
            questions=questions or{'a':{'type':'choice','instructions':'Check this complete source.','criteria':{'supported':'Supported','unknown':'Unknown'}}})
    def test_automatic_mode_preserves_complete_payload_and_small_standard_cache(self):
        task=self.task('Complete authored source.');first=self.gateway.classify('jev',task)
        self.assertNotIn('typesafe-danger-zone-long-context',self.requests[-1][1])
        self.assertTrue(self.gateway.classify('jev',task)['cache_hit']);self.assertEqual(len(self.requests),1)
        task=self.task('Complete source '+('x'*35000));self.gateway.classify('jev',task)
        provider,headers,body=self.requests[-1]
        self.assertEqual(headers['typesafe-danger-zone-long-context'],'true')
        self.assertEqual(body['state'],task.state());self.assertEqual(body['questions'],task.questions)
    def test_context_rejection_uses_a_distinct_supported_transport_not_failed_key_retry(self):
        self.reject_standard=True;text='Complete authored source '+('x'*35000)
        with self.assertRaises(urllib.error.HTTPError):self.gateway.classify('jev',self.task(text,False))
        good=self.gateway.classify('jev',self.task(text,None))
        self.assertEqual(len(self.requests),2);self.assertEqual(self.requests[0][2],self.requests[1][2])
        self.assertNotIn('typesafe-danger-zone-long-context',self.requests[0][1])
        self.assertEqual(self.requests[1][1]['typesafe-danger-zone-long-context'],'true')
        self.assertTrue(self.gateway.classify('jev',self.task(text,None))['cache_hit'])
        self.assertEqual(self.ledger.db.execute('SELECT COUNT(*)FROM calls').fetchone()[0],2)
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*)FROM calls WHERE status='failed'").fetchone()[0],1)
    def test_an_existing_valid_standard_result_is_not_reasked_with_an_expanded_header(self):
        text='Source '+('x'*35000);first=self.gateway.classify('jev',self.task(text,False))
        next_result=self.gateway.classify('jev',self.task(text,None))
        self.assertTrue(next_result['cache_hit']);self.assertEqual(first['request_hash'],next_result['request_hash'])
        self.assertEqual(len(self.requests),1)
    def test_cached_fallback_avoids_new_primary_votes_and_only_pending_fields_are_acquired(self):
        text='Complete authored source.';questions=self.task(text).questions
        self.gateway.classify('perplexity',self.task(text,False,questions));self.requests.clear()
        factory=lambda ledger,allow_live:G.PersistentGateway(ledger,allow_live=allow_live,transport=self.transport)
        with patch.object(S,'_gateway',side_effect=factory):
            result=S.judge_fields('source task',questions,[{'id':'e','date':'2030-01-01','text':text}],
                as_of='2030-05-27',ledger=self.ledger,long_context=None)
        self.assertEqual(self.requests,[]);self.assertEqual(result['judgments']['a']['decided_by'],'perplexity')
        questions={**questions,'b':{**questions['a'],'instructions':'A distinct unresolved field.'}}
        self.unknown_perplexity={'b'};self.gateway.classify('perplexity',self.task(text,False,questions));self.requests.clear()
        with patch.object(S,'_gateway',side_effect=factory):
            result=S.judge_fields('source task',questions,[{'id':'e','date':'2030-01-01','text':text}],
                as_of='2030-05-27',ledger=self.ledger,long_context=None)
        self.assertEqual(len(self.requests),1);self.assertEqual(self.requests[0][0],'jev')
        self.assertEqual(set(self.requests[0][2]['questions']),{'b'})
        self.assertEqual(result['judgments']['a']['decided_by'],'perplexity');self.assertEqual(result['judgments']['b']['decided_by'],'jev')

if __name__=='__main__':unittest.main()
