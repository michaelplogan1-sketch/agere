"""Normal HTTP replay on actual saved evidence, hosted calls disabled."""
import json,sqlite3,urllib.request,urllib.error,time
from pathlib import Path
from clinical_core.contracts import digest
R=Path('artifacts/agere-request-lineage-v1')
BASE='http://localhost:8887/api/stack/'
def call(route,body=None):
 req=urllib.request.Request(BASE+route,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=180) as f:return json.load(f)
def save(name,row):
 p=R/(name+'.json');assert not p.exists();p.write_text(json.dumps(row,indent=2)+'\n');return row
def ledger():
 with sqlite3.connect('artifacts/agere-full/provider-budget.sqlite3') as db:return digest([list(r) for r in db.execute('SELECT * FROM calls ORDER BY id')])
def main():
 before=ledger();cases=[]
 for name,file in [('baseline','artifacts/agere-remaining-questions-v1/cardiometabolic/decision-baseline.json'),('candidate','artifacts/agere-remaining-questions-v1/cardiometabolic/decision-buyer_documentary_candidate.json'),('population','artifacts/agere-population-decision-v1/verified-v2/respiratory-decision.json')]:
  old=json.loads(Path(file).read_text());prior=call('decision?id='+old['id']);assert prior['dependencies_current']
  row=save(name+'-replay',call('decision/'+('population-prepare' if name=='population' else 'prepare'),old['request']))
  trace=save(name+'-lineage',call('decision/lineage',{'decision_id':row['id']}))
  executions={r['execution_id']:r for r in trace['traces']};assert row['request_execution_id'] in executions
  assert executions[row['request_execution_id']]['status']=='completed'
  assert not trace['requests'];assert ledger()==before
  view=call('decision?id='+row['id']);assert view['dependencies_current'];assert call('decision?id='+old['id'])['tasks']==prior['tasks']
  if name=='candidate':assert len(executions[row['request_execution_id']]['events'][-1]['returned_receipt_keys'])==10
  cases.append({'case':name,'decision_id':row['id'],'execution_id':row['request_execution_id'],'status':'passed','work_unchanged':True})
 local=save('local-composition',call('compose',{'question':'Inspect source completeness'}));assert local['planner_receipt']['route']=='local_compiler';assert local['new_paid_api_calls']==0
 try:call('compose',{'question':'Compare a new unsupported complex outcome and clinical subgroup'})
 except urllib.error.HTTPError as exc:
  error=json.load(exc);save('unsupported-route',{'status':exc.code,'response':error});assert exc.code==400 and 'disabled' in error['error']
 else:raise AssertionError('Expected supported disabled-hosted fallback')
 assert ledger()==before
 save('http-verification',{'status':'passed','cases':cases,'actual_local_compiler':True,'unsupported_hosted_route_stays_disabled':True,'full_provider_ledger_unchanged':True,'ledger_digest':before,'new_paid_calls':0,'hosted_failure_escalation':'isolated transport fixtures only; not new live provider qualification'})
 print(json.dumps({'status':'passed','cases':cases,'ledger_unchanged':True}))
if __name__=='__main__':main()
