"""Real current buyer work, explicit engineering assumptions, no queue mutation."""
import json,sqlite3
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import work_capacity as c,program_work
OUT=Path('artifacts/agere-work-capacity-v1');BUYER='synthetic-domain-source-review'
def api(route,body):
 with urlopen(Request('http://localhost:8885/api/stack/work-capacity/'+route,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'}),timeout=180) as r:return json.load(r)
def save(name,obj):(OUT/(name+'.json')).write_text(json.dumps(obj,indent=2)+'\n')
def history():
 db=program_work.database(stack_path())
 try:return {t:[list(r) for r in db.execute('SELECT * FROM '+t+' ORDER BY 1,2')] for t in ('work_items','work_events','capability_tasks','capability_events')}
 finally:db.close()
def main():
 before=history();snap=api('preview',{'buyer_id':BUYER});selected=[t for t in snap['tasks'] if t['selectable']]
 assert len(selected)>=2
 services=sorted({t['service_id'] for t in selected})
 durations={key:{'low':10+5*i,'central':20+10*i,'high':30+15*i} for i,key in enumerate(services)}
 request={'buyer_id':BUYER,'snapshot_id':snap['id'],'task_ids':[t['id'] for t in selected],
 'question':'How would two pooled internal reviewers change waiting and completion for these selected source-review tasks?',
 'assumption_source':'Synthetic engineering demonstration only: durations supplied by the build, not observed or clinically validated; assumes all selected services can share identical reviewers.',
 'workers':2,'horizon_minutes':30,'durations':durations}
 result=api('compare',request);assert result==api('compare',request)
 current=api('inspect',{'id':result['id']});assert current['current'];assert {k:v for k,v in current.items() if k not in ('current','stale_reason')}==result
 saved=api('history',{'buyer_id':BUYER});assert any(r['id']==result['id'] and r['current'] for r in saved['scenarios'])
 for bad in ({**request,'snapshot_id':'stale'}, {**request,'durations':{}}, {**request,'clinical_priority':'urgent'}):
  try:api('compare',bad);raise AssertionError('Invalid scenario accepted')
  except HTTPError as e:assert e.code==400
 assert history()==before
 for key,obj in [('snapshot',snap),('request',request),('scenario',result),('history',saved)]:save(key,obj)
 r=result['comparisons']['central'];report={'status':'passed','buyer_id':BUYER,'scenario_id':result['id'],
 'task_records':len(snap['tasks']),'selected_tasks':len(selected),'unselected_tasks':len(result['unselected_task_ids']),
 'central_baseline_clearance_minutes':r['single_worker_baseline']['clearance_minutes'],'central_proposed_clearance_minutes':r['proposed']['clearance_minutes'],
 'central_baseline_mean_wait_minutes':r['single_worker_baseline']['mean_wait_minutes'],'central_proposed_mean_wait_minutes':r['proposed']['mean_wait_minutes'],
 'central_baseline_completed_by_horizon':r['single_worker_baseline']['completed_by_horizon'],'central_proposed_completed_by_horizon':r['proposed']['completed_by_horizon'],
 'idempotent_and_history_verified':True,'invalid_stale_missing_authority_rejected':True,'all_program_and_buyer_task_histories_unchanged':True,
 'assumptions':'Supplied synthetic engineering durations and pooled worker feasibility, not measured clinical capacity or patient waiting time',
 'provider_calls':0,'clinical_validation':False,'service_slots_reserved':0}
 save('http-verification',report);print(json.dumps(report,indent=2))
if __name__=='__main__':main()
