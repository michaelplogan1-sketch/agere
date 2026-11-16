"""Actual buyer work, explicit resource/ready-time assumptions, no dispatch."""
import copy,json
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import program_work
OUT=Path('artifacts/agere-resource-capacity-v1');BASE=Path('artifacts/agere-work-capacity-v1/scenario.json')
def api(group,route,body):
 with urlopen(Request('http://localhost:8885/api/stack/'+group+'/'+route,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'}),timeout=180) as r:return json.load(r)
def tables():
 db=program_work.database(stack_path())
 try:return {t:[list(r) for r in db.execute('SELECT * FROM '+t+' ORDER BY 1,2')] for t in ('work_items','work_events','capability_tasks','capability_events')}
 finally:db.close()
def save(name,obj):(OUT/(name+'.json')).write_text(json.dumps(obj,indent=2)+'\n')
def main():
 before=tables();original=json.loads(BASE.read_text());current=api('work-capacity','inspect',{'id':original['id']});assert current['current']
 ready={};seen=0
 for key in original['selected_task_ids']:
  t=next(t for t in original['snapshot']['tasks'] if t['id']==key)
  if t['service_id']=='behavioral-evidence-review':ready[key]=10 if seen==0 else 60;seen+=1
  else:ready[key]=0
 request={'parent_id':original['id'],'question':'How do separate respiratory and behavioral teams, delayed availability and task readiness change this selected work?',
 'assumption_source':'Synthetic engineering example: behavioral task readiness at minutes 10 and 60 is supplied, not measured clinical readiness.',
 'ready_minutes':ready,'pools':[
 {'id':'behavioral','name':'Synthetic behavioral review pool','workers':1,'available_from_minute':15,'service_ids':['behavioral-evidence-review'],'assumption_source':'Engineering assumption: one behavioral reviewer from minute 15; no staff identity or licensure verified.'},
 {'id':'respiratory','name':'Synthetic respiratory review pool','workers':2,'available_from_minute':0,'service_ids':['respiratory-evidence-review'],'assumption_source':'Engineering assumption: two respiratory reviewers from minute zero; no staff identity or licensure verified.'}]}
 result=api('resource-capacity','compare',request);assert result==api('resource-capacity','compare',request)
 inspected=api('resource-capacity','inspect',{'id':result['id']});assert inspected['current']
 r=result['comparisons']['central'];p=r['proposed'];assert p['whole_batch_clearance_minutes']==80 and p['completed_by_horizon']==2 and p['not_ready_at_horizon']==1 and p['in_progress_at_horizon']==1
 assert p['mean_queue_wait_minutes_schedulable']==1.25
 assert r['one_worker_per_active_pool']['completed_by_horizon']==1
 blocked_request=copy.deepcopy(request);blocked_request['question']='What remains blocked if the supplied behavioral review pool has no staff?';blocked_request['pools'][0]['workers']=0
 blocked=api('resource-capacity','compare',blocked_request);b=blocked['comparisons']['central']['proposed'];assert b['blocked_count']==2 and b['whole_batch_clearance_minutes'] is None
 for bad in ({**request,'ready_minutes':{}},{**request,'priority':'urgent'},{**request,'pools':[{**request['pools'][0],'service_ids':['unknown-service']}]}):
  try:api('resource-capacity','compare',bad);raise AssertionError('Invalid scenario accepted')
  except HTTPError as e:assert e.code==400
 history=api('resource-capacity','history',{'parent_id':original['id']});assert {r['id'] for r in history['scenarios']}=={result['id'],blocked['id']} and all(r['current'] for r in history['scenarios'])
 assert current==api('work-capacity','inspect',{'id':original['id']});assert before==tables()
 for name,obj in [('request',request),('scenario',result),('blocked-request',blocked_request),('blocked-scenario',blocked),('history',history)]:save(name,obj)
 report={'status':'passed','parent_scenario_id':original['id'],'scenario_id':result['id'],'blocked_scenario_id':blocked['id'],'selected_tasks':4,'unique_patients':2,'resource_pools':2,
 'central_whole_batch_clearance_minutes':80,'central_schedulable_mean_queue_wait_minutes':1.25,'central_completed_by_30_minutes':2,'central_not_ready_at_30_minutes':1,'central_in_progress_at_30_minutes':1,
 'one_worker_per_pool_completed_by_30_minutes':1,'unrestricted_single_worker_completed_by_30_minutes':1,'zero_behavioral_staff_blocks_tasks':2,
 'original_comparison_unchanged':True,'all_program_and_buyer_task_histories_unchanged':True,'idempotency_and_current_history_verified':True,'invalid_request_rejections_verified':True,
 'assumptions':'Explicit synthetic resource compatibility, availability and ready times. Not empirical clinical capacity, priority or outcomes.',
 'provider_calls':0,'clinical_validation':False,'external_actions':0,'service_slots_reserved':0}
 save('http-verification',report);print(json.dumps(report,indent=2))
if __name__=='__main__':main()
