import json,sys
from pathlib import Path
from urllib.error import HTTPError
from verify_reference_lifecycle_http import api,tables
OUT=Path('artifacts/agere-precedence-capacity-v1')
ORIGINAL=json.loads(Path('artifacts/agere-resource-capacity-v1/scenario.json').read_text())
PARENT=json.loads((OUT/'selected-parent.json').read_text()) if (OUT/'selected-parent.json').exists() else ORIGINAL
QUESTION='How would a respiratory review prerequisite and a ten-minute handoff affect this selected internal work?'
SOURCE='Synthetic engineering sensitivity: the task dependency and ten-minute lag are supplied assumptions, not a measured process or a clinical prerequisite.'
EDGE={'before_task_id':'55cf7f85a614fec76345b9d7e84ff860070c4fe4ce373c5add127e2ff7e9896b','after_task_id':'c3c213ae30badc57604a3d8adc11b30de9ecb727da267b9a3561040b093035d3','lag_minutes':10,'reason':'Hypothetically require the first respiratory source-review task to finish before the second respiratory task, with ten minutes for handoff. This does not change actual task readiness or infer a care sequence.'}
REQUEST={'parent_id':PARENT['id'],'question':QUESTION,'assumption_source':SOURCE,'dependencies':[EDGE]}
def save(n,x):(OUT/(n+'.json')).write_text(json.dumps(x,indent=2)+'\n')
def main():
 old=api('resource-capacity/inspect',{'id':PARENT['id']});assert old['current']
 assert {k:v for k,v in old.items() if k not in ('current','stale_reason')}==PARENT
 if sys.argv[-1]=='reconcile-browser-parent':
  chosen=api('resource-capacity/inspect',{'id':'a1efc49d84c48acb34f54d7402f3d145d4b0b882e3212b5f94b411f8da0ee891'});assert chosen['current']
  clean={k:v for k,v in chosen.items() if k not in ('current','stale_reason')}
  assert clean['parent_id']==ORIGINAL['parent_id'] and clean['selected_task_ids']==ORIGINAL['selected_task_ids']
  assert clean['request']['ready_minutes']==ORIGINAL['request']['ready_minutes']
  assert [{k:v for k,v in p.items() if k!='id'} for p in clean['request']['pools']]==[{k:v for k,v in p.items() if k!='id'} for p in ORIGINAL['request']['pools']]
  save('selected-parent',clean);save('selected-parent-state',chosen)
  save('initial-verification-scope-correction',{'initial_http_and_preservation_checks':'failed: inspected the API-created parent while browser used the equivalent prior browser-created parent','actual_browser_parent_id':clean['id'],'initial_parent_id':ORIGINAL['id'],'difference':'pool identifier names only; same task scope, readiness, resource declarations and durations; no saved result changed'})
  save('request-for-browser-parent',{**REQUEST,'parent_id':clean['id']});print('Exact existing browser parent reconciled; prior artifacts retained.');return
 if sys.argv[-1]=='prepare':
  assert api('precedence-capacity/history',{'parent_id':PARENT['id']})['scenarios']==[]
  save('request',REQUEST);save('history-before',tables());save('parent-before',old);print(json.dumps({'parent_id':PARENT['id'],'selected_tasks':4,'explicit_dependencies':1}));return
 rows=api('precedence-capacity/history',{'parent_id':PARENT['id']})['scenarios'];assert len(rows)==1 and rows[0]['current']
 row={k:v for k,v in rows[0].items() if k not in ('current','stale_reason')};assert row['request']==REQUEST
 assert row==api('precedence-capacity/compare',REQUEST)
 assert api('precedence-capacity/inspect',{'id':row['id']})==rows[0]
 for bad in ({**REQUEST,'actor':'clinician'},{**REQUEST,'dependencies':[EDGE,{**EDGE,'before_task_id':EDGE['after_task_id'],'after_task_id':EDGE['before_task_id']}]},{**REQUEST,'dependencies':[{**EDGE,'after_task_id':'foreign-task'}]}):
  try:api('precedence-capacity/compare',bad);raise AssertionError('Invalid dependency accepted')
  except HTTPError as e:assert e.code in (400,409)
 central=row['comparisons']['central'];a=central['without_dependencies'];b=central['with_dependencies']
 assert (a['completed_by_horizon'],b['completed_by_horizon'])==(2,1)
 assert (a['whole_batch_clearance_minutes'],b['whole_batch_clearance_minutes'])==(80,80)
 assert (b['dependency_waiting_at_horizon'],b['not_ready_at_horizon'],b['in_progress_at_horizon'])==(1,1,1)
 assert b['mean_dependency_delay_minutes_schedulable']==10 and b['mean_queue_wait_minutes_schedulable']==1.25 and b['mean_total_delay_after_ready_minutes_schedulable']==11.25
 assert all(row['comparisons'][case]['without_dependencies']==PARENT['comparisons'][case]['proposed'] for case in ('low','central','high'))
 assert old==json.loads((OUT/'selected-parent-state.json').read_text()) and tables()==json.loads((OUT/'history-before.json').read_text())
 save('scenario',row);save('state-after',rows)
 result={'status':'passed','scenario_id':row['id'],'parent_id':PARENT['id'],'selected_tasks':4,'patients':2,'explicit_dependencies':1,'central_completed_by_30_without_dependencies':2,'central_completed_by_30_with_dependencies':1,'central_whole_batch_minutes_without_dependencies':80,'central_whole_batch_minutes_with_dependencies':80,'central_dependency_waiting_at_30':1,'central_mean_dependency_delay_minutes':10,'central_mean_resource_wait_minutes':1.25,'central_mean_total_delay_minutes':11.25,'all_original_duration_baselines_preserved':True,'actual_browser_parent_reconciled':True,'idempotency_and_invalid_cycle_scope_authority_verified':True,'all_work_histories_unchanged':True,'provider_calls':0,'external_actions':0,'clinical_validation':False}
 save('http-verification',result);print(json.dumps(result,indent=2))
if __name__=='__main__':main()
