import copy,json,threading,unittest
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere.clinical import span_repair as S
from clinical_core.contracts import digest

class SpanRepair(unittest.TestCase):
 def fixture(self):
  docs=[{'record_id':'r1','date':'2030-01-01','text':'Patient stopped treatment because of cost.'},{'record_id':'r2','date':'2030-02-01','text':'Patient now has a paid pharmacy fill.'}]
  unresolved=[{'record_id':d['record_id'],'field':'therapy','quote':d['text'],'document_text_digest':digest(d['text']),'span_start':0,'span_end':len(d['text'])}for d in docs]
  return docs,unresolved,{'therapy':'What does this source say about medication use or access?'}
 def run_repair(self,send,docs=None,pending=None):
  d,u,q=self.fixture();return S.repair(SimpleNamespace(as_of='2030-05-27'),'A',pending or u,docs or d,q,send,threading.Event(),[])
 def test_all_contexts_are_whole_and_code_copies_exact_dated_sources(self):
  seen=[]
  def send(body):
   state=json.loads(body['messages'][0]['content']);seen.append(state)
   return {'content':[{'type':'text','text':json.dumps({'answers':{k:'finding'for k in state['decisions']}})}]},0,False
  got=self.run_repair(send);docs,_,_=self.fixture();self.assertEqual(len(seen),1);self.assertEqual([r['quote']for r in got['findings']['therapy']],[d['text']for d in docs]);self.assertEqual([r['date']for r in got['findings']['therapy']],['2030-01-01','2030-02-01']);self.assertEqual(got['failures'],[])
  self.assertEqual({d['text']for d in seen[0]['whole_source_contexts'].values()},{d['text']for d in docs})
 def test_valid_siblings_remain_and_only_unknowns_escalate(self):
  seen=[]
  def send(body):
   state=json.loads(body['messages'][0]['content']);seen.append(state);keys=list(state['decisions']);answers={k:('finding'if len(seen)>1 or i==0 else'unknown')for i,k in enumerate(keys)}
   return {'content':[{'type':'text','text':json.dumps({'answers':answers})}]},0,False
  got=self.run_repair(send);self.assertEqual(len(seen[0]['decisions']),2);self.assertEqual(len(seen[1]['decisions']),1);self.assertEqual(len(got['findings']['therapy']),2)
 def test_unknown_is_preserved_as_unresolved_not_absence(self):
  def send(body):
   state=json.loads(body['messages'][0]['content']);return {'content':[{'type':'text','text':json.dumps({'answers':{k:'unknown'for k in state['decisions']}})}]},0,False
  got=self.run_repair(send);self.assertEqual(len(got['failures']),2);self.assertEqual(got['findings']['therapy'],[])
 def test_fabricated_decision_ids_and_invalid_types_cannot_add_findings(self):
  def send(body):return {'content':[{'type':'text','text':'{"answers":{"foreign":"finding","s0":["finding"]}}'}]},0,False
  got=self.run_repair(send);self.assertEqual(got['findings']['therapy'],[]);self.assertEqual(len(got['failures']),2)
 def test_altered_span_or_foreign_source_is_rejected_before_provider(self):
  docs,pending,q=self.fixture();pending[0]['quote']='Invented.'
  with self.assertRaises(ValueError):S.batches(pending,docs,q)
 def test_source_limit_stops_before_sonnet(self):
  calls=[];stop=threading.Event();docs,u,q=self.fixture()
  def send(body):calls.append(body);raise RuntimeError('Claude subscription usage limit')
  with self.assertRaisesRegex(RuntimeError,'subscription'):S.repair(SimpleNamespace(as_of='2030-05-27'),'A',u,docs,q,send,stop,[])
  self.assertTrue(stop.is_set());self.assertEqual(len(calls),1)
 def test_all_jobs_scheduled_without_population_or_source_loss(self):
  docs,u,q=self.fixture();docs=docs[:1];u=[{**u[0],'field':'f'+str(i)}for i in range(150)];q={r['field']:'What is documented?'for r in u};b=S.batches(u,docs,q);self.assertEqual(sum(map(len,b)),150);self.assertEqual(len(b),3)

if __name__=='__main__':unittest.main()
