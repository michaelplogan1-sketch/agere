import json, sqlite3, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from clinical_core.contracts import digest
from clinical_core.agere import request_lineage as L
from clinical_core.agere.gateway import BudgetLedger
from clinical_core.agere.clinical.context_student import send, REQUEST_MAX_BYTES
from clinical_core.agere.clinical import economical_planner as planner

class Lineage(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  self.ledger=BudgetLedger(self.root/'ledger.sqlite3',cap_usd=30)
 def tearDown(self):self.ledger.close();self.tmp.cleanup()
 def events(self,execution):
  with L.database(self.root) as db:return [json.loads(r[0]) for r in db.execute('SELECT payload FROM question_request_events WHERE execution=? ORDER BY sequence',(execution.id,))]
 def test_pending_survives_scope_and_reset(self):
  e=L.begin(self.root,'/api/stack/compose',{'question':'q'});self.ledger.reserve('a'*64,'claude','model');e.close()
  self.assertEqual([r['kind'] for r in self.events(e)],['started','reservation_requested','reserved']);self.assertIsNone(L.ACTIVE.get())
  with self.assertRaises(sqlite3.IntegrityError):
   with L.database(self.root) as db:db.execute('DELETE FROM question_request_events')
 def test_failed_key_blocked_no_silent_retry(self):
  e=L.begin(self.root,'/api/stack/context/synthesize',{'context_id':'b'*64})
  self.ledger.reserve('a'*64,'claude','model');self.ledger.finish('a'*64,error='synthetic failure')
  with self.assertRaises(ValueError):self.ledger.reserve('a'*64,'claude','model')
  e.finish();e.close()
  self.assertEqual([r['kind'] for r in self.events(e)],['started','reservation_requested','reserved','request_finished','reservation_requested','retry_blocked','operation_failed'])
  self.assertEqual(self.ledger.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0],1)
 def test_actual_send_cache_and_bounds_remain_unchanged(self):
  body={'model':'fixture-model','messages':[{'content':'synthetic fixture'}]};key=digest({'contract':'context-student/1','provider':'claude','body':body})
  raw={'model':'fixture-model','usage':{'input_tokens':2,'output_tokens':1}}
  class Response:
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def read(self):return json.dumps(raw).encode()
  e=L.begin(self.root,'/api/stack/context/synthesize',{'context_id':'b'*64})
  with patch.dict('os.environ',{'ANTHROPIC_API_KEY':'local-fixture'}),patch('urllib.request.urlopen',return_value=Response()) as transport:
   _,usage=send(body,'claude',self.ledger,self.root,parse=lambda r:r)
   _,cached=send(body,'claude',self.ledger,self.root,parse=lambda r:r)
   with self.assertRaisesRegex(ValueError,'bound'):send({**body,'oversize':'x'*REQUEST_MAX_BYTES},'claude',self.ledger,self.root,parse=lambda r:r)
   self.assertEqual(transport.call_count,1)
  e.finish({'usage':[usage,cached]});e.close()
  events=self.events(e);self.assertEqual(sum(r['kind']=='reserved' for r in events),1)
  self.assertEqual(events[-1]['returned_receipt_keys'],[key]);self.assertTrue(cached['cache_hit'])
  self.assertFalse(events[-1]['historical_receipts_are_new_dispatches'])
  self.assertNotIn('local-fixture',json.dumps(events));self.assertNotIn('synthetic fixture',json.dumps(events))
 def test_scope_isolation_across_threads_and_nested_context(self):
  def run(i):
   e=L.begin(self.root,'/api/stack/compose',{'question':str(i)});L.observe('marker',marker=i);e.finish({'route':'local'});e.close();return e
  with ThreadPoolExecutor(max_workers=2) as pool:rows=list(pool.map(run,range(2)))
  for i,e in enumerate(rows):self.assertEqual([r['marker'] for r in self.events(e) if r['kind']=='marker'],[i])
  outer=L.begin(self.root,'/api/stack/compose',{'question':'outer'});inner=run(3)
  self.assertIs(L.ACTIVE.get(),outer);outer.close();self.assertIsNone(L.ACTIVE.get())
 def test_exact_links_no_same_patient_or_question_guess(self):
  row={'id':'a'*64,'schema':'population-question-decision/1'}
  e=L.begin(self.root,'/api/stack/decision/population-prepare',{'question':'same'})
  e.finish(row);e.close()
  other=L.begin(self.root,'/api/stack/compose',{'question':'same','patient_id':'same'});other.finish({'route':'local'});other.close()
  with patch('clinical_core.agere.clinical.question_decision.load',return_value=row):
   r=L.for_decision(SimpleNamespace(path=self.root),{'decision_id':row['id']},ledger_path=self.root/'ledger.sqlite3')
  self.assertEqual([r['execution_id'] for r in r['traces']],[e.id]);self.assertIsNone(r['cost']['total_cost_usd'])
 def test_pending_then_failed_ledger_accounting_is_current(self):
  row={'id':'c'*64,'schema':'population-question-decision/1'};e=L.begin(self.root,'/api/stack/compose',{'decision_id':row['id']})
  self.ledger.reserve('a'*64,'claude','model');e.close()
  with patch('clinical_core.agere.clinical.question_decision.load',return_value=row):
   r=L.for_decision(SimpleNamespace(path=self.root),{'decision_id':row['id']},ledger_path=self.root/'ledger.sqlite3')
   self.assertEqual(r['traces'][0]['status'],'interrupted_or_running');self.assertEqual(r['requests'][0]['status'],'pending')
   self.ledger.finish('a'*64,error='fixture')
   changed=L.for_decision(SimpleNamespace(path=self.root),{'decision_id':row['id']},ledger_path=self.root/'ledger.sqlite3')
   self.assertEqual(changed['requests'][0]['status'],'failed');self.assertIsNone(changed['cost']['linked_api_estimate_usd'])
 def test_router_failure_escalation_and_cache_in_one_execution(self):
  from test_economical_planner import Encoder
  stack=SimpleNamespace(path=self.root,receipts=self.root,allow_live=True,retriever=SimpleNamespace(encoder=Encoder(),meta={'source_digest':'s','snapshot':{'as_of':'2026-09-30'}}))
  def failed(body,provider,ledger,*args,**kwargs):
   key=digest({'provider':provider,'body':body});ledger.reserve(key,provider,body['model']);ledger.finish(key,error='fixture');raise RuntimeError('fixture')
  def frontier(capabilities):
   self.ledger.reserve('f'*64,'claude','fixture');self.ledger.finish('f'*64,response={'usage':{}})
   return planner.recipes()['sources']['graph'],{'request_hash':'f'*64,'new_paid_api_calls':1}
  with patch('clinical_core.agere.clinical.analysis_graph.catalog',return_value={}),patch('clinical_core.agere.clinical.analysis_graph.code_identity',return_value='fixture'),patch.object(planner,'send',side_effect=failed):
   e=L.begin(self.root,'/api/stack/compose',{'question':'Describe contributing feeds'})
   _,receipt=planner.plan(stack,'Describe contributing feeds',self.ledger,frontier);e.finish({'planner_receipt':receipt});e.close()
   replay=L.begin(self.root,'/api/stack/compose',{'question':'Describe contributing feeds'})
   _,cached=planner.plan(stack,'Describe contributing feeds',self.ledger,frontier);replay.finish({'planner_receipt':cached});replay.close()
  events=self.events(e);self.assertEqual([r['provider'] for r in events if r['kind']=='reserved'],['perplexity','claude'])
  self.assertEqual(events[-1]['route']['escalation_reason'],'cheap_route_failed_or_unavailable')
  self.assertFalse(any(r['kind']=='reserved' for r in self.events(replay)));self.assertEqual(cached['new_paid_api_calls'],0)
 def test_finished_event_does_not_collect_token_count_keys(self):
  e=L.begin(self.root,'/api/stack/compose',{'question':'q'})
  e.finish({'planning':{'receipts':[{'usage':{'request_hash':'a'*64},'token_check':{'request_hash':'b'*64}}]}});e.close()
  self.assertEqual(self.events(e)[-1]['returned_receipt_keys'],['a'*64])

class RoutingAcceptance(unittest.TestCase):
 setUp=Lineage.setUp
 tearDown=Lineage.tearDown
 def query(self,row):
  with patch('clinical_core.agere.clinical.question_decision.load',return_value=row):
   return L.for_decision(SimpleNamespace(path=self.root),{'decision_id':row['id']},ledger_path=self.root/'ledger.sqlite3')
 def test_alternative_plan_cost_is_not_charged_to_baseline(self):
  baseline={'id':'c'*64,'answer_id':'b'*64};candidate={'id':'d'*64,'answer_id':'b'*64}
  self.ledger.reserve('a'*64,'claude','fixture');self.ledger.finish('a'*64,response={'usage':{}})
  for row in [baseline,candidate]:
   e=L.begin(self.root,'/api/stack/decision/prepare',{'answer_id':row['answer_id']})
   e.finish({**row,'decision_id':row['id'],'usage':[] if row==baseline else [{'request_hash':'a'*64}]});e.close()
  with patch('clinical_core.agere.clinical.question_decision.source_binding',return_value=({'context_id':'f'*64},{},{})):
   r=self.query(baseline);other=self.query(candidate)
  self.assertEqual(r['requests'],[]);self.assertEqual(len(r['alternative_routes']['requests']),1)
  self.assertEqual(len(other['requests']),1);self.assertEqual(other['alternative_routes']['requests'],[])
  self.assertEqual(r['execution_observations']['execution_relations']['alternative_decision_same_evidence'],1)
 def test_historical_keys_join_and_deduplicate_across_replays(self):
  row={'id':'c'*64,'schema':'population-question-decision/1'}
  self.ledger.reserve('a'*64,'claude','fixture')
  self.ledger.finish('a'*64,response={'model':'fixture','usage':{'input_tokens':10,'output_tokens':2}})
  for _ in range(2):
   e=L.begin(self.root,'/api/stack/compose',{'decision_id':row['id']})
   e.finish({'planner_receipt':{'route':'exact_question_cache','receipt':{'request_hash':'a'*64},'token_check':{'request_hash':'b'*64}}});e.close()
  r=self.query(row)
  self.assertEqual(len(r['requests']),1)
  self.assertEqual(r['requests'][0]['request_hash'],'a'*64)
  self.assertEqual(r['requests'][0]['lineage_origins'],['returned_historical_receipt'])
  self.assertEqual(r['requests'][0]['recorded_usage']['input_tokens'],10)
  self.assertEqual(r['execution_observations']['observed_reservations'],0)
  self.assertEqual(r['execution_observations']['unique_returned_historical_keys'],1)
  self.assertIsNone(r['cost']['total_cost_usd'])
 def test_observed_and_returned_same_key_count_once(self):
  row={'id':'c'*64,'schema':'population-question-decision/1'}
  e=L.begin(self.root,'/api/stack/compose',{'decision_id':row['id']})
  self.ledger.reserve('a'*64,'claude','fixture');self.ledger.finish('a'*64,response={'usage':{}})
  e.finish({'usage':[{'request_hash':'a'*64}]});e.close()
  r=self.query(row);self.assertEqual(len(r['requests']),1)
  self.assertEqual(set(r['requests'][0]['lineage_origins']),{'observed_execution','returned_historical_receipt'})
  self.assertEqual(r['execution_observations']['observed_reservations'],1)
 def test_missing_historical_key_is_unknown_not_free(self):
  row={'id':'c'*64,'schema':'population-question-decision/1'}
  e=L.begin(self.root,'/api/stack/compose',{'decision_id':row['id']});e.finish({'usage':[{'request_hash':'a'*64}]});e.close()
  r=self.query(row);self.assertEqual(r['requests'][0]['status'],'missing_ledger_entry')
  self.assertEqual(r['cost']['unpriced_dispatched_requests'],1);self.assertIsNone(r['cost']['linked_api_estimate_usd'])
 def test_local_statement_abstention_and_retained_identity(self):
  row={'id':'c'*64,'schema':'population-question-decision/1'}
  for role,status,reading in [('cohort_lexical','abstained_unsupported_contract',None),('kev','experimental_proposal_only','b'*64)]:
   e=L.begin(self.root,'/api/stack/decision/statement-review',{'decision_id':row['id']})
   e.finish({'schema':'decision-statement-contract/1','decision_id':row['id'],'role':role,'status':status,'reading_id':reading,'human_review_created':False});e.close()
  r=self.query(row);self.assertEqual(len(r['traces']),2);self.assertEqual(r['requests'],[])
  self.assertEqual(r['execution_observations']['recorded_no_review_created_executions'],2)
  self.assertEqual(r['traces'][1]['events'][-1]['local_reading_id'],'b'*64)
  self.assertIsNone(r['cost']['human_review_minutes'])
  for t in r['traces']:
   self.assertGreaterEqual(t['events'][-1]['effort']['elapsed_seconds'],0)
   self.assertGreaterEqual(t['events'][-1]['effort']['server_thread_cpu_seconds'],0)
 def test_failed_scope_effort_recorded_and_unfinished_stays_unknown(self):
  row={'id':'c'*64,'schema':'population-question-decision/1'}
  e=L.begin(self.root,'/api/stack/compose',{'decision_id':row['id']});e.finish();e.close()
  pending=L.begin(self.root,'/api/stack/compose',{'decision_id':row['id']});pending.close()
  r=self.query(row);obs=r['execution_observations']
  self.assertEqual(obs['executions_with_measured_effort'],1);self.assertEqual(obs['executions_without_measured_effort'],1)
  self.assertFalse(obs['full_cost_and_review_accounting_complete'])

if __name__=='__main__':unittest.main()
