import copy,json,sqlite3,tempfile,threading,unittest
from pathlib import Path
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
 def test_explicit_single_model_keeps_actual_reader_identity_and_unresolved_fields(self):
  from test_document_relations import question
  docs,pending,_=self.fixture();questions={'therapy':question()};seen=[]
  docs[0]['text']='MRA stopped because of cost.'
  pending[0].update(quote=docs[0]['text'],document_text_digest=digest(docs[0]['text']),span_end=len(docs[0]['text']))
  def send(body):
   seen.append(body['model']);state=json.loads(body['messages'][0]['content']);keys=list(state['decisions'])
   answers={keys[0]:{'label':'finding','polarity':'affirmed','temporality':'historical','subject':'patient'},keys[1]:'unknown'}
   return {'content':[{'type':'text','text':json.dumps({'answers':answers})}]},0,False
  out=S.repair(SimpleNamespace(as_of='2030-05-27'),'A',pending,docs,questions,send,threading.Event(),[],models=(S.model_policy.SONNET,))
  self.assertEqual(seen,[S.model_policy.SONNET]);self.assertEqual(len(out['failures']),1)
  finding=out['findings']['therapy'][0];self.assertEqual(finding['provider'],S.model_policy.SONNET)
  self.assertEqual(finding['assertion']['reader_model_identity'],S.model_policy.SONNET)
  self.assertEqual(finding['quote'],docs[0]['text']);self.assertFalse(finding['assertion']['current_clinical_state_established'])
 def test_invalid_explicit_model_does_not_dispatch(self):
  docs,pending,questions=self.fixture();send=lambda _:self.fail('Must not call a provider')
  for models in ([],['new-model']):
   with self.subTest(models=models),self.assertRaises(ValueError):S.repair(SimpleNamespace(as_of='2030-05-27'),'A',pending,docs,questions,send,threading.Event(),[],models=models)

class SourceObservations(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.path=Path(self.tmp.name);self.receipts=[];self.sent=[]
  self.tools=SimpleNamespace(as_of='2030-05-27',stack=SimpleNamespace(path=self.path))
 def doc(self,ident):
  return {'patient_id':'A','record_id':ident,'date':'2030-01-01','available_date':'2030-01-02',
          'text':'Complete context. MRA held for potassium. Follow up is planned.'}
 def pending(self,docs,field='reason'):
  quote='MRA held for potassium.'
  return [{'record_id':d['record_id'],'field':field,'quote':quote,'document_text_digest':digest(d['text']),
           'span_start':d['text'].index(quote),'span_end':d['text'].index(quote)+len(quote)}for d in docs]
 def sender(self,body):
  self.assertNotIn('max_tokens',body)
  state=json.loads(body['messages'][0]['content']);self.sent.append(state)
  self.receipts.append({'request_hash':digest(body),'cost_estimate':{'estimated_usd':.001}})
  return {'content':[{'type':'text','text':json.dumps({'answers':{k:{'label':'finding','polarity':'affirmed',
      'temporality':'historical','subject':'patient'}for k in state['decisions']}})}]},.001,False
 def run_read(self,docs,*,questions=None,pending=None,send=None,model=None,pid='A'):
  from test_document_relations import question
  return S.repair(self.tools,pid,self.pending(docs)if pending is None else pending,docs,
      questions or {'reason':question()},send or self.sender,threading.Event(),self.receipts,
      models=(model or S.model_policy.HAIKU,))
 def test_changed_neighbors_reuse_exact_reading_and_retain_complete_context(self):
  a,b,c=map(self.doc,['a','b','c']);first=self.run_read([a,b]);second=self.run_read([a,c])
  self.assertEqual(len(self.sent),2)
  self.assertEqual(list(self.sent[1]['whole_source_contexts'].values()),[c])
  self.assertEqual(len(self.sent[1]['decisions']),1)
  self.assertEqual(first['findings']['reason'][0],second['findings']['reason'][0])
  self.assertFalse(second['findings']['reason'][0]['assertion']['current_clinical_state_established'])
  self.assertEqual(second['receipt']['source_observation_cache_hits'],1)
  again=self.run_read([a,c]);self.assertEqual(len(self.sent),2);self.assertEqual(again['findings'],second['findings'])
  self.assertEqual(again['receipt']['source_observation_cache_hits'],2)
  hits=[r for r in self.receipts if r.get('source_observation_id')]
  self.assertTrue(all(r['source_request_hashes']and r['new_paid_api_calls']==0 and not r['clinical_acceptance_inherited']for r in hits))
 def test_new_field_reuses_same_semantics_but_changed_question_is_fresh(self):
  from test_document_relations import question
  d=self.doc('a');first=self.run_read([d]);q=question()
  renamed=self.run_read([d],questions={'new_alias':q},pending=self.pending([d],'new_alias'))
  self.assertEqual(len(self.sent),1);self.assertEqual(first['findings']['reason'],renamed['findings']['new_alias'])
  changed={**q,'question':q['question']+' Include the documented review schedule.'}
  self.run_read([d],questions={'reason':q,'schedule':changed},pending=self.pending([d])+self.pending([d],'schedule'))
  self.assertEqual(len(self.sent),2);self.assertEqual(len(self.sent[-1]['decisions']),1)
 def test_source_clock_patient_model_and_policy_changes_cannot_inherit_observations(self):
  a,b=self.doc('a'),self.doc('b');self.run_read([a,b])
  for key,value in [('text',a['text']+' Additional complete source context.'),('date','2030-02-01'),('available_date','2030-02-02')]:
   changed={**a,key:value};out=self.run_read([changed,b]);self.assertEqual(out['receipt']['source_observation_cache_hits'],1)
   self.assertEqual(list(self.sent[-1]['whole_source_contexts'].values()),[changed])
  before=len(self.sent);self.run_read([a],pid='B');self.tools.as_of='2030-05-28';self.run_read([a]);self.tools.as_of='2030-05-27'
  self.run_read([a],model=S.model_policy.SONNET)
  with patch.object(S,'SYSTEM',S.SYSTEM+' Revised policy.'):self.run_read([a])
  self.assertEqual(len(self.sent)-before,4)
 def test_unknown_is_reused_at_each_rung_without_becoming_absence(self):
  d=self.doc('a')
  def unknown(body):
   raw,c,h=self.sender(body);state=self.sent[-1];raw['content'][0]['text']=json.dumps({'answers':{k:'unknown'for k in state['decisions']}});return raw,c,h
  def read():return S.repair(self.tools,'A',self.pending([d]),[d],{'reason':'What is stated?'},unknown,threading.Event(),self.receipts)
  first=read();second=read();self.assertEqual(len(self.sent),2)
  for out in [first,second]:self.assertEqual(len(out['failures']),1);self.assertEqual(out['findings']['reason'],[])
  self.assertEqual(second['receipt']['source_observation_cache_hits'],2)
 def test_missing_invalid_truncated_or_unmetered_answers_are_never_bottled(self):
  d=self.doc('a')
  for mode in ['missing','invalid','truncated','unmetered']:
   with self.subTest(mode=mode):
    with tempfile.TemporaryDirectory()as folder:
     self.tools.stack.path=Path(folder);seen=[]
     def send(body):
      raw,c,h=self.sender(body);seen.append(body)
      if mode=='missing':raw['content'][0]['text']='{"answers":{}}'
      if mode=='invalid':raw['content'][0]['text']='{"answers":{"s0":{"label":"finding","subject":"other"}}}'
      if mode=='truncated':raw['stop_reason']='max_tokens'
      if mode=='unmetered':self.receipts.pop()
      return raw,c,h
     self.run_read([d],send=send);self.run_read([d],send=send);self.assertEqual(len(seen),2)
     db=S.observation_store(Path(folder));self.assertEqual(db.execute('SELECT COUNT(*) FROM span_observations').fetchone()[0],0);db.close()
 def test_quarantine_preserves_original_and_only_disables_disputed_reading(self):
  a,b=self.doc('a'),self.doc('b');first=self.run_read([a,b]);ident=first['findings']['reason'][0]['assertion']['id']
  self.assertEqual(S.quarantine_assertions(self.path,[ident],'Unadjudicated wrong time feedback'),1)
  self.assertEqual(S.quarantine_assertions(self.path,[ident],'Unadjudicated wrong time feedback'),0)
  got=self.run_read([a,b]);self.assertEqual(len(self.sent),1);self.assertEqual(len(got['failures']),1)
  self.assertEqual([v['record_id']for v in got['findings']['reason']],['b'])
  db=S.observation_store(self.path)
  self.assertEqual(db.execute('SELECT COUNT(*) FROM span_observations').fetchone()[0],2)
  event=json.loads(db.execute('SELECT payload FROM span_observation_events').fetchone()[0]);self.assertFalse(event['feedback_is_clinical_truth'])
  for sql in ['UPDATE span_observations SET payload=payload','DELETE FROM span_observations','UPDATE span_observation_events SET payload=payload','DELETE FROM span_observation_events']:
   with self.assertRaises(sqlite3.IntegrityError):db.execute(sql)
  db.close();self.run_read([{**a,'available_date':'2030-02-02'},b]);self.assertEqual(len(self.sent),2)
 def test_integrity_failure_stops_before_any_provider_request(self):
  d=self.doc('a');self.run_read([d]);db=S.observation_store(self.path)
  db.execute('DROP TRIGGER span_observations_immutable');db.execute("UPDATE span_observations SET payload=json_set(payload,'$.answer.label','irrelevant')");db.commit();db.close()
  with self.assertRaises(S.ObservationIntegrityError):self.run_read([d],send=lambda _:self.fail('Integrity failure must not buy a replacement'))
 def test_paid_reading_survives_cache_storage_failure(self):
  with patch.object(S,'remember_observation',side_effect=sqlite3.OperationalError('disk unavailable')):
   out=self.run_read([self.doc('a')])
  self.assertEqual(len(out['findings']['reason']),1);self.assertEqual(out['failures'],[])
  self.assertIn('disk unavailable',out['receipt']['source_observation_cache_errors'][0])
 def test_cached_sibling_survives_new_neighbor_outage(self):
  a,b=self.doc('a'),self.doc('b');self.run_read([a])
  def outage(body):raise RuntimeError('Provider unavailable')
  out=self.run_read([a,b],send=outage)
  self.assertEqual([v['record_id']for v in out['findings']['reason']],['a']);self.assertEqual(len(out['failures']),1)
 def test_disagreeing_concurrent_readings_suspend_reuse_and_preserve_both_receipts(self):
  S.remember_observation(self.path,'same',{'label':'unknown'},['first'])
  self.assertFalse(S.remember_observation(self.path,'same',{'label':'irrelevant'},['second']))
  saved=S.cached_observation(self.path,'same');self.assertTrue(saved['quarantined'])
  self.assertEqual(saved['answer'],{'label':'unknown'});self.assertEqual(saved['source_request_hashes'],['first'])
  db=S.observation_store(self.path);event=json.loads(db.execute('SELECT payload FROM span_observation_events').fetchone()[0]);db.close()
  self.assertEqual(event['source_request_hashes'],['second']);self.assertEqual(event['answer'],{'label':'irrelevant'})

if __name__=='__main__':unittest.main()
