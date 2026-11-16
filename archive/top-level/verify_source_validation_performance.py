"""Measure real local routes and preserve exact saved evidence values."""
import hashlib,json,sqlite3,time
from pathlib import Path
from urllib.request import Request,urlopen
from clinical_core.agere.gateway import BudgetLedger
from verify_domain_programs_http import snapshot

OUT=Path('artifacts/agere-source-validation-performance-v1')
PRIOR=Path('artifacts/agere-evidence-portfolio-v1')
timings=[]
def api(label,route,body=None):
 req=Request('http://localhost:8885'+route,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
 t=time.perf_counter()
 with urlopen(req,timeout=300) as response:result=json.load(response)
 row={'label':label,'elapsed_seconds':time.perf_counter()-t,'route':route,'cache_hit':result.get('cache_hit')}
 timings.append(row);print(json.dumps(row),flush=True)
 return result
def save(name,value):(OUT/(name+'.json')).write_text(json.dumps(value,indent=2)+'\n')
def evidence_only(value):
 if isinstance(value,dict):return {k:evidence_only(v) for k,v in value.items() if k not in ('cache_hit','selection_cache_hit')}
 if isinstance(value,list):return [evidence_only(v) for v in value]
 return value
def main():
 expected=json.loads((OUT/'before-inspection.json').read_text())['portfolio']
 for label in ('warm_inspection_1','warm_inspection_2','warm_inspection_3'):
  assert api(label,'/api/stack/evidence-portfolio?id='+expected['id'])==expected
 for pid in ('P-1001','P-7193'):
  prior=json.loads((PRIOR/(pid+'-question.json')).read_text())
  result=api(pid+'_question','/api/stack/compose',{'question':prior['question']})
  assert result['result_id']==prior['result_id'] and result['results']==prior['results']
  assert result['cache_hit'] and result['new_paid_api_calls']==0
  save(pid+'-question',result)
  for request_id in result['results'][-1]['result']['optimal']['request_ids']:
   item=api(pid+'_source','/api/stack/evidence-acquisition/item?id='+expected['proposal_id']+'&item='+request_id) if pid=='P-1001' else api(pid+'_source','/api/stack/evidence-acquisition/item?id='+result['results'][-1]['result']['proposal_id']+'&item='+request_id)
   assert item['item']['patient_id']==pid and item['requests_sent']==0
  graph=prior['graph'];graph['interpretation']+=' Runtime source-validation measurement v2; same source question.'
  fresh=api(pid+'_uncached_graph','/api/stack/execute-plan',graph)
  save(pid+'-fresh-graph',fresh)
  assert not fresh['cache_hit'] and evidence_only(fresh['results'])==evidence_only(prior['results']) and fresh['new_paid_api_calls']==0
 save('runtime-meta',api('runtime_meta','/api/stack/meta'))
 assert snapshot()==json.loads((OUT/'functional-before.json').read_text())
 ledger=BudgetLedger(Path('artifacts/agere-full/provider-budget.sqlite3')).receipt();save('ledger-after',ledger)
 assert ledger==json.loads((OUT/'ledger-before.json').read_text())
 h=hashlib.sha256();count=0
 with sqlite3.connect('file:artifacts/agere-healthcare-v123/longitudinal/events.sqlite3?mode=ro',uri=True) as db:
  for row in db.execute('SELECT * FROM events ORDER BY id'):
   h.update(json.dumps(row,separators=(',',':')).encode());h.update(b'\n');count+=1
 event_rows={'rows':count,'sha256':h.hexdigest()};save('event-rows-after',event_rows)
 assert event_rows==json.loads((OUT/'event-rows-before.json').read_text())
 save('http-after',timings)
 print('Exact portfolio/graph result equality, source inspection, event rows, functional work and ledger preservation passed.',flush=True)
if __name__=='__main__':main()
