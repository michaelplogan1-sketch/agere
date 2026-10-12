"""Actual synthetic HTTP question -> graph -> decision -> task -> acquisition.

Existing free-form questions are regressions. No model or clinical qualification
is claimed, and no external action is performed.
"""
import argparse,json,time,uuid
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from clinical_core.contracts import digest
from verify_native_care_state import QUESTIONS
OUT=Path(__file__).resolve().parent/'artifacts/agere-graph-decision-v1'
BUYER='g3-buyer-documentary-development'

def call(route,body=None):
 t=time.perf_counter()
 req=Request('http://127.0.0.1:8885/api/stack/'+route,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
 try:
  with urlopen(req,timeout=600) as r:code,data=r.status,json.load(r)
 except HTTPError as e:code,data=e.code,e.read().decode()
 return {'status':code,'seconds':time.perf_counter()-t,'request':body,'response':data}

def keep(name,route,body=None):
 p=OUT/(name+'.json')
 if p.exists():raise ValueError('Capture exists: '+str(p))
 value=call(route,body);p.write_text(json.dumps(value,indent=2)+'\n')
 print(json.dumps({'stage':name,'status':value['status'],'seconds':round(value['seconds'],3),'calls':value['response'].get('new_paid_api_calls') if isinstance(value['response'],dict) else None}),flush=True)
 if value['status']!=200:raise ValueError(str(value['response'])[:1200])
 return value['response']

def main(stage):
 summary={}
 for name in ('patient','population','prior_population'):
  result=keep(stage+'-question-'+name,'ask',{'question':QUESTIONS[name],'live':True})
  decision=keep(stage+'-decision-'+name,'decision/graph-prepare',{'result_id':result['result_id'],'buyer_id':BUYER})
  assert decision['question']==QUESTIONS[name]
  assert decision['population']['evaluated']==(1 if name=='patient' else 1614)
  assert decision['population']['roster']==1614
  assert all(len(r['result']['review_members'])==r['result']['patients_evaluated'] for r in result['results'] if r['node']['operation'] in ('source_state','pre_event_state'))
  patient='SYN-A63E644A-06E' if name=='prior_population' else 'P-1001'
  member=keep(stage+'-member-'+name,'decision/graph-member',{'decision_id':decision['id'],'patient_id':patient})
  assert member['question']==decision['question'] and member['population']==decision['population']
  assert member['source_versions']['records'] and member['findings'] and member['evidence_gaps']
  if name=='prior_population':
   f=next(f for f in member['findings'] if f['operation']=='pre_event_state')
   assert f['temporal_scope']['before_date_exclusive']=='2029-05-15'
   assert f['temporal_scope']['available_by']=='2029-06-30'
  else:
   f=next(f for f in member['findings'] if f['care_accounts'])
   assert len(f['care_accounts'])==4
   assert f['care_account_counts']=={'completion_not_observed':3,'performed_service_documented':1}
  options=[o for o in (member['matching'] or {}).get('options',[]) if o['status']!='blocked']
  assert options,'No unblocked service review option'
  queued=keep(stage+'-queue-'+name,'decision/queue',{'decision_id':member['id'],'option_id':options[0]['id'],'actor':'Synthetic graph workflow verifier'})
  task=queued['task']
  assert queued['decision_binding']['graph_digest']==decision['graph_digest'] and 'answer_digest' not in queued['decision_binding']
  queued_again=keep(stage+'-queue-replay-'+name,'decision/queue',{'decision_id':member['id'],'option_id':options[0]['id'],'actor':'Synthetic graph workflow verifier'})
  assert queued_again['idempotent_replay'] and queued_again['task']==task
  handoff=keep(stage+'-handoff-'+name,'decision/event',{'decision_id':member['id'],'task_id':task['id'],'expected_revision':task['revision'],'request_id':str(uuid.uuid4()),'action':'prepare_handoff','actor':'Synthetic graph workflow verifier','note':'Review documentary evidence and request-specific unknowns; do not dispatch external action.'})
  assert handoff['task']['handoff']['origin']['kind']=='graph_member'
  assert not handoff['task']['handoff']['external_dispatch_performed']
  preview=keep(stage+'-acquisition-preview-'+name,'decision/acquisition-preview',{'decision_id':member['id'],'task_id':task['id']})
  for dep in preview['dependencies']:
   assert dep['identity']['kind']=='graph_gap'
   assert dep['observation']['original_gap']['target_record_id'] is None
  dependency=next(d for d in preview['dependencies'] if d['observation']['original_gap']['request_id'])
  request=keep(stage+'-acquisition-create-'+name,'decision/acquisition-create',{'decision_id':member['id'],'task_id':task['id'],'preview_id':preview['id'],'dependency_id':dependency['id'],'actor':'Synthetic graph workflow verifier'})
  check=keep(stage+'-acquisition-check-'+name,'decision/acquisition-check',{'request_id':request['result_id']})
  assert check['status']==dependency['observation']['status'] and not check['changes']
  assert not check['task_state_changed'] and not check['request_sent'] and not check['clinical_resolution_established']
  reopened=keep(stage+'-reopen-'+name,'decision?id='+member['id'])
  population=keep(stage+'-reopen-population-'+name,'decision?id='+decision['id'])
  assert reopened['dependencies_current'] and len(reopened['tasks'])==1 and reopened['tasks'][0]['binding_current']
  assert population['dependencies_current'] and len(population['task_links'])==1
  assert len(reopened['followups'])==2
  summary[name]={'graph_result_id':result['result_id'],'decision_id':decision['id'],'member_id':member['id'],'task_id':task['id'],'acquisition_id':request['result_id'],'population':decision['population'],'care_account_counts':f['care_account_counts'],'dependencies':len(preview['dependencies']),'new_paid_api_calls':result['new_paid_api_calls']}
 (OUT/(stage+'-workflow-summary.json')).write_text(json.dumps(summary,indent=2)+'\n')

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--stage',default='candidate');a=p.parse_args();main(a.stage)
