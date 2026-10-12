"""Actual saved decisions and independent raw-ledger arithmetic, no inference."""
import json,sqlite3,time,urllib.request
from pathlib import Path
from clinical_core.contracts import digest

R=Path('artifacts/agere-decision-economics-v1/verified-v2')
def read(p):return json.loads(Path(p).read_text())
def save(name,row):
 p=R/(name+'.json');p.parent.mkdir(parents=True,exist_ok=True);assert not p.exists(),p;p.write_text(json.dumps(row,indent=2)+'\n');return row
def call(route,body=None):
 req=urllib.request.Request('http://localhost:8885/api/stack/'+route,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=180) as f:return json.load(f)
def ledger():
 with sqlite3.connect('artifacts/agere-full/provider-budget.sqlite3') as db:return digest([list(r) for r in db.execute('SELECT * FROM calls ORDER BY id')])
def audit(report):
 prices=read('config/agere/pricing.json');known=0;unknown=0;checks=0
 with sqlite3.connect('artifacts/agere-full/provider-budget.sqlite3') as db:
  for r in report['requests']:
   raw=db.execute('SELECT model,status,response,reserved FROM calls WHERE id=?',(r['request_hash'],)).fetchone()
   if raw is None:
    assert r['status'] in ('not_dispatched','missing_ledger_entry')
    unknown+=int(r['status']=='missing_ledger_entry')
    continue
   model,status,response,reserved=raw;assert status==r['status'] and reserved==r['accounting_reservation_usd'];checks+=1
   usage=json.loads(response).get('usage') if response else r['recorded_usage']
   expected=None
   if isinstance(usage,dict) and model in prices and all(type(usage.get(k)) is int and usage[k]>=0 for k in ('input_tokens','output_tokens')):
    p=prices[model];total=usage['input_tokens']*p['input']+usage['output_tokens']*p['output']
    if model.startswith('claude'):
     cache=usage.get('cache_creation',{});total+=cache.get('ephemeral_5m_input_tokens',usage.get('cache_creation_input_tokens',0))*p['cache_write_5m']+cache.get('ephemeral_1h_input_tokens',0)*p['cache_write_1h']+usage.get('cache_read_input_tokens',0)*p['cache_read']
    expected=total/1e6
   assert expected==r['estimated_api_usd']
   if expected is None:unknown+=1
   else:known+=expected
 assert len(report['requests'])==len({r['request_hash'] for r in report['requests']})
 assert abs(known-report['cost']['known_linked_api_estimate_usd'])<1e-12 and unknown==report['cost']['unpriced_dispatched_requests']
 assert report['cost']['total_cost_usd'] is None and report['cost']['new_paid_api_calls']==0
 assert not report['full_cost_accounting_complete']
 return {'status':'passed','unique_dispatched_requests_audited':checks,'known_api_estimate_usd':known,'unpriced_dispatched_requests':unknown,'total_cost_unknown':True}
def main():
 before=ledger();results=[]
 cases=[('baseline','artifacts/agere-remaining-questions-v1/cardiometabolic/decision-baseline.json'),('candidate','artifacts/agere-remaining-questions-v1/cardiometabolic/decision-buyer_documentary_candidate.json'),('population','artifacts/agere-population-decision-v1/verified-v2/respiratory-decision.json')]
 for name,file in cases:
  d=read(file);view=call('decision?id='+d['id']);assert view['dependencies_current'];start=time.monotonic()
  r=save(name+'/report',call('decision/economics',{'decision_id':d['id']}));elapsed=time.monotonic()-start
  assert call('decision/economics',{'decision_id':d['id']})==r
  reopened=save(name+'/reopen',call('decision/economics-open',{'decision_id':d['id'],'result_id':r['result_id']}));assert reopened['current']
  assert r['question']==d['question'] and r['work']==view['tasks']
  a=save(name+'/independent-audit',audit(r));assert call('decision?id='+d['id'])['tasks']==view['tasks']
  results.append({'case':name,'decision_id':d['id'],'result_id':r['result_id'],'seconds':elapsed,'requests':len(r['requests']),'reconstruction_gaps':len(r['reconstruction_gaps']),'cost':r['cost'],'audit':a})
 baseline=read(R/'baseline/report.json');candidate=read(R/'candidate/report.json')
 assert baseline['route']['input_digest']==candidate['route']['input_digest'] and baseline['question']==candidate['question']
 assert baseline['route']['baseline_mapped_findings']==candidate['route']['baseline_mapped_findings']
 assert ledger()==before
 save('verification',{'status':'passed','cases':results,'same_answer_baseline_candidate':True,'full_ledger_unchanged':True,'ledger_digest':before,'new_model_calls':0,'new_tasks':0,'release_gate_not_closed_by_accounting_alone':True})
 print(json.dumps(results,indent=2))
if __name__=='__main__':main()
