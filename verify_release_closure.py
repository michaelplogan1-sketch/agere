"""Current-runtime development checks; no provider dispatch or external action."""
import argparse,json,time,datetime
from clinical_core.contracts import digest
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError
ROOT=Path(__file__).resolve().parent;OUT=ROOT/'artifacts/agere-release-closure-v1'

def capture(name,route,body=None):
 p=OUT/(name+'.json')
 if p.exists():raise ValueError('Capture already exists: '+str(p))
 start=time.perf_counter();req=Request('http://127.0.0.1:8885'+route,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
 try:
  with urlopen(req,timeout=300) as r:
   status=r.status;raw=r.read().decode();response=json.loads(raw) if 'json' in r.headers.get('Content-Type','') else raw
 except HTTPError as e:status=e.code;response=e.read().decode()
 except (URLError,ConnectionError) as e:status='transport_error';response=str(e)
 row={'route':route,'request':body,'status':status,'seconds':time.perf_counter()-start,'response':response};p.write_text(json.dumps(row,indent=2)+'\n')
 print(json.dumps({'capture':name,'status':status,'seconds':round(row['seconds'],3)}),flush=True);return row

def matrix(stage):
 saved=json.loads((ROOT/'artifacts/agere-workflow-evaluation-v1/scorecard.json').read_text());rows=[]
 for case in saved['cases']:
  ref=case['trace_links'].get('decision_context')
  if not ref:
   rows.append({'case':case['id'],'historical_acceptance':case['acceptance']['status'],'current_status':'unassessed','reason':'No complete saved decision; medication source-group completion remains unfinished.'});continue
  decision=json.loads((ROOT/ref).read_text())['response'];result=capture(stage+'-'+case['id'],'/api/stack/decision?id='+decision['id']);r=result['response']
  assert result['status']==200 and r['decision']['question']==case['question']
  tasks=r.get('tasks',[])
  rows.append({'case':case['id'],'decision_id':decision['id'],'historical_acceptance':case['acceptance']['status'],'current_status':'current' if r['dependencies_current'] else 'historical','reasons':[r['stale_reason']] if r.get('stale_reason') else [],'owned_tasks':len(tasks),'current_task_bindings':sum(x.get('binding_current') is True for x in tasks),'seconds':result['seconds']})
 for name,folder in [('mixed_criteria','agere-graph-criteria-v1'),('aco_accountability','agere-graph-accountability-v1')]:
  s=json.loads((ROOT/'artifacts'/folder/'final-summary.json').read_text());key=s['decision_id'];result=capture(stage+'-'+name,'/api/stack/decision?id='+key);r=result['response']
  assert result['status']==200
  rows.append({'case':name,'decision_id':key,'current_status':'current' if r['dependencies_current'] else 'historical','reasons':[r['stale_reason']] if r.get('stale_reason') else [],'population':r['decision']['population']['evaluated'],'seconds':result['seconds']})
 s=json.loads((ROOT/'artifacts/agere-graph-accountability-v1/final-summary.json').read_text())
 member=capture(stage+'-accountability-member','/api/stack/decision?id='+s['member_id']);m=member['response'];assert member['status']==200 and m['dependencies_current'] and any(x['task']['id']==s['task_id'] for x in m['tasks'])
 acquisition=capture(stage+'-accountability-acquisition','/api/stack/decision/acquisition-open',{'request_id':s['acquisition_id']});a=acquisition['response'];assert acquisition['status']==200 and a['policy_current'] and a['current_dependency']['observation']['status']=='accountability_input_requires_review'
 result={'schema':'agere-current-workflow-matrix/1','snapshot_digest':digest(json.loads((ROOT/'config/agere/active_warehouse.json').read_text())['snapshot']),'recorded_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'completed_with_historical_dependencies','rows':rows,'analytical_member_and_owned_work_current':True,'exact_attribution_acquisition_current':True,'no_new_provider_dispatch_requested':True,'limits':'Recorded read-only reopening of declared saved workflows, not a rerun of their interpretations or broad query/clinical qualification. Historical currentness failures remain visible. This is the last verification, not a live freshness monitor.'}
 (OUT/(stage+'-matrix.json')).write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'rows':rows}),flush=True)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--stage',required=True);a=p.parse_args();matrix(a.stage)
