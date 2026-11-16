"""Run real pass/reconciliation/answer engines with a labeled fake provider."""
import json,sqlite3
from pathlib import Path
from unittest.mock import patch
from datetime import datetime,timedelta,timezone
from clinical_core.agere.clinical import source_synthesis as engine,case_synthesis as single,reference_scope_work as scope,reference_scope_packets as packets,internal_execution as execution,structured_transport as transport
from clinical_core.agere.gateway import BudgetLedger
REAL_INSPECT=engine.inspect
import test_structured_transport as fixture
OUT=Path('artifacts/agere-structured-transport-v1')
def main():
 f=fixture.Work();f.setUp()
 try:
  f.records[-1]['documents'] *= 2
  preview=scope.preview(f.path,f.request);f.base=scope.select(f.path,{'preview_id':preview['id'],'record_ids':['c','o','pkt']});f.req={'selection_id':f.base['id']}
  db=sqlite3.connect(f.path/'stack.sqlite3');db.execute('CREATE TABLE patients(id TEXT PRIMARY KEY)');db.execute('INSERT INTO patients VALUES (?)',('p1',));db.execute('CREATE TABLE records(id TEXT PRIMARY KEY,patient TEXT,stream TEXT,payload TEXT)')
  db.executemany('INSERT INTO records VALUES (?,?,?,?)',[(r['record_id'],r['patient_id'],r['source_table'],json.dumps(r)) for r in f.records]);db.commit();db.close()
  selected=f.prepare();oldinspect=engine.inspect;calls=[]
  def inspect(path,key):return oldinspect(path,key) if key=='a'*64 else REAL_INSPECT(path,key)
  def send(body,provider,ledger,out,*,parse,**kwargs):
   wire=json.loads(body['messages'][0]['content']);value={'findings':[],'unanswered':['Disposable provider fixture: meaning remains unassessed.'],'review_steps':[]}
   if wire.get('stage')=='source_pass':value['unit_reviews']=[{'unit':u['id'],'status':'unresolved','reason':'Disposable fixture only'} for u in wire['units']]
   calls.append({'stage':wire.get('stage'),'request_bytes':len(json.dumps(body,allow_nan=False).encode())})
   assert calls[-1]['request_bytes']<=20000
   raw={'model':body['model'],'stop_reason':'end_turn','content':[{'type':'text','text':json.dumps(value)}]}
   return parse(raw),{'cache_hit':False,'fixture':True,'usage':{'input_tokens':0,'output_tokens':0}}
  ledger=BudgetLedger(f.path/'fixture-budget.sqlite3')
  with patch.object(engine,'inspect',side_effect=inspect),patch.object(single,'send',side_effect=send),patch.object(single,'token_check',return_value={'provider_count_cached':True,'input_tokens':0,'fixture':True}):
   answer=engine.synthesize(f.path,selected['context_id'],ledger,allow_live=True)
   assert answer['status']=='complete' and answer['reconciliation']['groups'];assert len(answer['reference_manifest'])==selected['structured_transport']['transport_units']
   assert not answer['clinical_state_established'];count=len(calls);replay=engine.synthesize(f.path,selected['context_id'],ledger,allow_live=False);assert replay['id']==answer['id'] and len(calls)==count
   question=scope.attach(f.path,{'selection_id':selected['id'],'answer_id':answer['id']});assert not question['requirement_resolved']
   def credential(role):return execution.provision(f.path,{'principal_id':'Disposable test only','identity_kind':'synthetic_test','buyer_id':'buyer1','service_id':'pharmacy','roles':[role],'expires_at':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()})['token']
   worker=credential('packet_worker');reviewer=credential('packet_reviewer');run=execution.run(f.path,{'task_id':f.task['id'],'expected_revision':f.task['revision'],'request_id':'fixture-run'},'Bearer '+worker)['run']
   execution.review(f.path,{'run_id':run['id'],'expected_revision':0,'request_id':'fixture-review','disposition':'needs_more_evidence','note':'Disposable engineering fixture, no clinical review','follow_up_on':datetime.now(timezone.utc).date().isoformat()},'Bearer '+reviewer)
   follow=packets.prepare(f.path,{'run_id':run['id'],'review_revision':1,'selection_id':selected['id'],'note':'Disposable structured evidence test','request_id':'fixture-follow'},'Bearer '+reviewer)
   returned=packets.attach(f.path,{'followup_id':follow['id'],'answer_id':answer['id']});assert returned['question_answer_id']==question['id'];assert packets.inspect(f.path,f.task['id'])['answers'][0]['current']
   result={'status':'passed','scope':'Disposable fixture, fake provider output explicitly unresolved. Actual context adapters, source passes, stable reconciliation, exact coverage, saved replay, question return and authenticated packet return. No live model or clinical quality claim.',
    'transport_units':selected['structured_transport']['transport_units'],'source_passes':len(answer['passes']),'reconciliation_groups':len(answer['reconciliation']['groups']),
    'fixture_engine_requests':calls,'all_units_accounted':True,'offline_replay_without_calls':True,'original_question_and_packet_return':True,'real_provider_calls':0,'clinical_validation':False}
   (OUT/'engine-fixture-verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
  ledger.close()
 finally:f.doCleanups()
if __name__=='__main__':main()
