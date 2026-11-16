import json,sys
from pathlib import Path
from urllib.error import HTTPError
from verify_reference_lifecycle_http import api,tables
OUT=Path('artifacts/agere-work-cost-v1');CTX=Path('artifacts/agere-work-cost-context-v1')
PARENT=json.loads(Path('artifacts/agere-precedence-capacity-v1/scenario.json').read_text())
QUESTION='How do a respiratory handoff and available staffing change the cost of this selected work?'
SOURCE='Synthetic engineering comparison using explicitly supplied USD labor rates and one batch setup cost. These are not observed payroll, program payments or outcome savings.'
REQUEST={'parent_type':'dependency','parent_id':PARENT['id'],'question':QUESTION,'assumption_source':SOURCE,'currency':'USD',
 'rates':[{'pool_id':'pool-1','hourly_rate':'120.00','source':'Hypothetical loaded rate for the behavioral pool; no payroll or staff credential verified.'},{'pool_id':'pool-2','hourly_rate':'60.00','source':'Hypothetical loaded rate for the respiratory pool; no payroll or staff credential verified.'}],
 'fixed_costs':[{'id':'fixed-1','label':'Selected batch setup','amount':'20.00','source':'Hypothetical one-time setup cost for the selected batch; not an actual charge.'}]}
def save(out,n,x):(out/(n+'.json')).write_text(json.dumps(x,indent=2)+'\n')
def main():
 phase=sys.argv[-1]
 if phase=='prepare':
  old=api('precedence-capacity/inspect',{'id':PARENT['id']});assert old['current'];assert {k:v for k,v in old.items() if k not in ('current','stale_reason')}==PARENT
  assert api('work-cost/history',{'parent_type':'dependency','parent_id':PARENT['id']})['scenarios']==[]
  save(OUT,'request',REQUEST);save(OUT,'history-before',tables());save(OUT,'parent-before',old);print('Exact dependency scenario ready for browser cost entry.');return
 if phase=='verify-cost':
  rows=api('work-cost/history',{'parent_type':'dependency','parent_id':PARENT['id']})['scenarios'];assert len(rows)==1 and rows[0]['current'];r={k:v for k,v in rows[0].items() if k not in ('current','stale_reason')};assert r['request']==REQUEST and api('work-cost/compare',REQUEST)==r
  for bad in ({**REQUEST,'currency':'EUR'},{**REQUEST,'rates':[]},{**REQUEST,'payer_approved':True},{**REQUEST,'rates':[{**REQUEST['rates'][0],'hourly_rate':'0.001'},REQUEST['rates'][1]]}):
   try:api('work-cost/compare',bad);raise AssertionError('Invalid costs accepted')
   except HTTPError as e:assert e.code in (400,409)
  central=r['comparisons']['central'];val=lambda v:v['display_amount']
  assert val(central['baseline']['horizon']['cost_views']['occupied_time']['total'])=='110.00'
  assert val(central['proposed']['horizon']['cost_views']['occupied_time']['total'])=='80.00'
  assert val(central['proposed']['horizon']['cost_views']['available_staff_time']['total'])=='110.00'
  assert val(central['proposed']['whole_batch']['cost_views']['occupied_time']['total'])=='160.00'
  assert val(central['proposed']['whole_batch']['cost_views']['available_staff_time']['total'])=='310.00'
  assert val(r['comparisons']['high']['proposed_minus_baseline']['whole_batch']['available_staff_time'])=='40.00'
  assert tables()==json.loads((OUT/'history-before.json').read_text());assert api('precedence-capacity/inspect',{'id':PARENT['id']})==json.loads((OUT/'parent-before.json').read_text())
  save(OUT,'scenario',r);save(OUT,'state-after',rows);report={'status':'passed','cost_id':r['id'],'parent_id':PARENT['id'],'central_horizon_occupied_baseline_usd':'110.00','central_horizon_occupied_proposed_usd':'80.00','central_horizon_available_staff_usd':'110.00','central_batch_occupied_usd':'160.00','central_batch_available_staff_usd':'310.00','high_batch_available_staff_increment_usd':'40.00','exact_replay_and_invalid_scope_verified':True,'work_and_parent_unchanged':True,'measured_costs':False,'realized_savings_established':False,'provider_calls':0};save(OUT,'http-verification',report);print(json.dumps(report,indent=2));return
 cost=json.loads((OUT/'scenario.json').read_text());req={'cost_id':cost['id'],'available_by':'2029-08-15'}
 if phase=='context-preview':
  assert api('work-cost-context/history',{'cost_id':cost['id']})['contexts']==[]
  preview=api('work-cost-context/preview',req);save(CTX,'request',req);save(CTX,'preview',preview);print(json.dumps({'context_id':preview['id'],**preview['summary']},indent=2));return
 rows=api('work-cost-context/history',{'cost_id':cost['id']})['contexts'];assert len(rows)==1 and rows[0]['current'];r={k:v for k,v in rows[0].items() if k not in ('current','stale_reason')}
 assert r==json.loads((CTX/'preview.json').read_text());assert api('work-cost-context/prepare',{**req,'preview_id':r['id']})==r
 for bad in ({**req,'patient_ids':['foreign']},{**req,'available_by':'2031-01-01'},{**req,'revenue_approved':True}):
  try:api('work-cost-context/preview',bad);raise AssertionError('Invalid context accepted')
  except HTTPError as e:assert e.code in (400,409)
 assert r['patient_ids']==['P-7193','SYN-3C114EFC-C0C'];assert not r['program_account_attribution_established'] and not r['amounts_netted_against_cost']
 assert {k:v for k,v in api('work-cost/inspect',{'id':cost['id']}).items() if k not in ('current','stale_reason')}==cost
 assert tables()==json.loads((OUT/'history-before.json').read_text())
 save(CTX,'context',r);save(CTX,'state-after',rows);report={'status':'passed','context_id':r['id'],'cost_id':cost['id'],**r['summary'],'exact_task_population_and_financial_receipts':True,'cutoff_patient_override_authority_rejected':True,'original_cost_and_work_unchanged':True,'program_revenue_established':False,'amounts_netted_against_cost':False,'provider_calls':0,'external_actions':0};save(CTX,'http-verification',report);print(json.dumps(report,indent=2))
if __name__=='__main__':main()
