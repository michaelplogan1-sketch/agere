"""Actual question -> exact bundle -> existing source-review requests, local only."""
import hashlib,json,sqlite3
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from clinical_core.agere.clinical import evidence_portfolio as ep
from clinical_core.agere.active import stack_path
from clinical_core.agere.gateway import BudgetLedger
from verify_domain_programs_http import snapshot
OUT=Path('artifacts/agere-evidence-portfolio-v1')
def api(route,body=None):
 req=Request('http://localhost:8885'+route,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
 with urlopen(req,timeout=300) as r:return json.load(r)
def write(name,data):(OUT/(name+'.json')).write_text(json.dumps(data,indent=2)+'\n')
def main():
 results=[]
 for patient in ['P-1001','P-7193']:
  q=ep.question(patient,['native_medication_nonuse','medication_report_readiness','medication_regimen_confirmation'],3)
  r=api('/api/stack/compose',{'question':q});write(patient+'-question',r)
  assert r['planner_receipt']['route']=='local_compiler' and r['planner_receipt']['recipe']=='exact_evidence_portfolio' and r['new_paid_api_calls']==0
  d=r['results'][-1]['result'];assert d['complete'] and d['patient_contracts_evaluated']==3 and d['clinical_execution_authorized'] is False
  saved=api('/api/stack/evidence-portfolio?id='+d['id']);assert saved['proposal_id']==d['proposal_id']
  assert api('/api/stack/compose',{'question':q})['cache_hit']
  for request in d['optimal']['request_ids']:
   item=api('/api/stack/evidence-acquisition/item?id='+d['proposal_id']+'&item='+request)
   assert item['item']['patient_id']==patient and item['requests_sent']==0 and item['requirements']
  zero=api('/api/stack/evidence-portfolio/compare',{'proposal_id':d['proposal_id'],'review_slots':0});assert zero['optimal']['covered_checks']==0
  full=api('/api/stack/evidence-portfolio/compare',{'proposal_id':d['proposal_id'],'review_slots':16});assert full['optimal']['covered_checks']==d['unresolved_check_count']
  try:api('/api/stack/evidence-portfolio/compare',{'proposal_id':d['proposal_id'],'review_slots':True});raise AssertionError('Boolean capacity accepted')
  except HTTPError as e:assert e.code==400
  row={'patient':patient,'question':q,'portfolio_id':d['id'],'proposal_id':d['proposal_id'],'request_count':d['request_count'],'unresolved_checks':d['unresolved_check_count'],'requests_selected':d['optimal']['slots_used'],'checks_covered':d['optimal']['covered_checks'],'greedy_checks':d['greedy_baseline']['covered_checks'],'replay':True,'all_selected_sources_inspected':True}
  results.append(row);print(json.dumps(row),flush=True)
 assert snapshot()==json.loads((OUT/'functional-before.json').read_text())
 assert BudgetLedger(Path('artifacts/agere-full/provider-budget.sqlite3')).receipt()==json.loads((OUT/'ledger-before.json').read_text())
 protected=json.loads((OUT/'protected-before.json').read_text())['protected_files'];assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in protected.items())
 write('http-verification',results)
 print('HTTP source-review path, unchanged ledger, functional history and 75 protected files passed',flush=True)
if __name__=='__main__':main()
