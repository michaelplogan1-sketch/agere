"""Saved original question -> complete literal comparisons -> immutable history."""
import json,time
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from clinical_core.agere.gateway import BudgetLedger
from verify_domain_programs_http import snapshot
OUT=Path('artifacts/agere-reference-comparison-v1')
def save(name,value):(OUT/(name+'.json')).write_text(json.dumps(value,indent=2)+'\n')
def api(route,body):
 with urlopen(Request('http://localhost:8885'+route,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'}),timeout=180) as response:return json.load(response)
def main():
 ledger=BudgetLedger(Path('artifacts/agere-full/provider-budget.sqlite3')).receipt();work=snapshot();save('ledger-before',ledger);save('functional-before',work)
 source=json.loads(Path('artifacts/agere-need-references-v1/stable-references.json').read_text());key=source['id'];names=['p1:u6','p2:u3']
 preview=api('/api/stack/evidence-needs/comparison-preview',{'reference_id':key,'reference_names':names});save('preview',preview)
 assert all('text' in {f['field'] for f in item['fields']} for item in preview['sources'])
 rows=[]
 for label,scope,field in [('complete_text','received_field','text'),('submitted_unit','source_unit','text'),('source_date','received_field','event_date')]:
  request={'reference_id':key,'left':{'reference_name':names[0],'scope':scope,'field':field},'right':{'reference_name':names[1],'scope':scope,'field':field}}
  started=time.perf_counter();r=api('/api/stack/evidence-needs/compare',request);elapsed=time.perf_counter()-started;save(label,r)
  assert r['comparison']['literal_equal']==(label!='source_date') and not r['same_source_location']
  assert not r['question_resolved'] and not r['human_review_recorded'] and not r['clinical_execution_authorized']
  assert r['original_question']==source['original_question'] and r['new_paid_api_calls']==0
  assert api('/api/stack/evidence-needs/compare',request)==r
  if label!='source_date':assert r['comparison']['text']['complete_values_compared'] and r['left']['span']['complete_received_field'] and len(r['left']['value'])==208
  rows.append({'case':label,'id':r['id'],'literal_equal':r['comparison']['literal_equal'],'elapsed_seconds':elapsed,'left_complete_field':r['left']['span']['complete_received_field'],'right_complete_field':r['right']['span']['complete_received_field']});print(json.dumps(rows[-1]),flush=True)
  try:api('/api/stack/evidence-needs/compare',{**request,'left':{**request['left'],'field':'truth'}});raise AssertionError('Forbidden source field compared')
  except HTTPError as e:assert e.code==400
 original={k:source['binding'][k] for k in ('receipt_id','need_id')};listing=api('/api/stack/evidence-needs/comparisons',original);save('question-comparisons',listing)
 assert {r['id'] for r in listing['comparisons']}=={r['id'] for r in rows} and all(r['current'] for r in listing['comparisons'])
 assert BudgetLedger(Path('artifacts/agere-full/provider-budget.sqlite3')).receipt()==ledger and snapshot()==work
 save('http-verification',rows);print('Complete source-text equality, unit equality, differing source dates, exact replay, immutable listing and unchanged ledger/work passed.',flush=True)
if __name__=='__main__':main()
