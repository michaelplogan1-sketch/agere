"""Actual synthetic task -> unresolved records -> new selected review -> attachment."""
import json,sys,urllib.request,urllib.error
from pathlib import Path
OUT=Path('artifacts/agere-source-meaning-v1');PRIOR=Path('artifacts/agere-linked-reconciliation-v1')
OLD=Path('artifacts/agere-medication-interpretation-v1')
TASK='972c00cfb436e95844ab5b7d46f338938e1d40f96522c812d4669bb61d0801db'
def call(endpoint,body=None):
 req=urllib.request.Request('http://localhost:8885/api/stack/'+endpoint,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=300) as r:return json.load(r)
def write(name,value):(OUT/name).write_text(json.dumps(value,indent=2)+'\n')
def read(name):return json.loads((OUT/name).read_text())
def prepare():
 prior=json.loads((PRIOR/'P-1001-answer.json').read_text());selection=json.loads((OLD/'P-1001-selection.json').read_text())
 gaps=call('medication-interpretation/gaps',{'review_id':selection['review_id'],'answer_id':prior['id']});write('gaps.json',gaps)
 assert gaps['counts']=={'unassessed_units':12,'records_to_review':4}
 groups={'historical':[], 'followup':[]}
 for r in gaps['records']:
  group='followup' if any('quarantine' in u['reason'] for u in r['unassessed_units']) else 'historical';groups[group].append(r['record_id'])
 for group,ids in groups.items():
  req={**gaps['selection_request'],'record_ids':ids};s=call('medication-interpretation/select',req);write(group+'-selection.json',s)
  assert s['context_id'] not in ['8cf5a23c65c52da0dc815e87b69e90926c203adf476fdd5cd986c566ae909356','799a395da065b65f4d14c8451f5c5e2bbe955068e894f230027faf360325b5','27268fae1f4fc78cc4c9117b001965e85f183538e0849470f6ee38c21506bc9b']
  p=call('context/plan',{'context_id':s['context_id']});write(group+'-plan.json',p)
  assert p['source_meaning_contract']=='source-meaning/1'
  print(group,s['context_id'],'passes',p['source_passes'],'bytes',p['request_bytes'],flush=True)
def run(group):
 s=read(group+'-selection.json');a=call('context/synthesize',{'context_id':s['context_id']});write(group+'-dispatch.json',a)
 a=call('case-synthesis?id='+a['id']);write(group+'-answer.json',a)
 assert a['source_meaning_contract']=='source-meaning/1' and not a['meaning_judgments_validated']
 att=call('medication-interpretation/answer',{'selection_id':s['id'],'answer_id':a['id']});write(group+'-attachment.json',att)
 print(group,a['id'],'findings',len(a['findings']),'units',a['coverage']['model_accounted_units'],flush=True)
def verify():
 original=json.loads((PRIOR/'P-1001-answer.json').read_text());s=json.loads((OLD/'P-1001-selection.json').read_text())
 assert call('case-synthesis?id='+original['id'])==original
 for group in ('historical','followup'):
  selection=read(group+'-selection.json');a=read(group+'-answer.json')
  replay=call('context/synthesize',{'context_id':selection['context_id']});assert replay['cache_hit'] and replay['new_paid_api_calls']==0
  assert replay['id']==a['id'];write(group+'-replay.json',{'id':a['id'],'new_paid_api_calls':0,'cache_hit':True})
  att=call('medication-interpretation/answer',{'selection_id':selection['id'],'answer_id':a['id']});assert att==read(group+'-attachment.json')
 state=call('medication-interpretation/task',{'task_id':TASK});write('task-state.json',state)
 assert state['task_revision']==2 and all(x['current'] for x in state['answers'])
 gaps=call('medication-interpretation/gaps',{'review_id':s['review_id'],'answer_id':original['id']});assert gaps==read('gaps.json')
 try:call('medication-interpretation/gaps',{'review_id':s['review_id'],'answer_id':json.loads((PRIOR/'P-7193-answer.json').read_text())['id']});raise AssertionError('Foreign review accepted')
 except urllib.error.HTTPError as e:assert e.code in (400,409)
 write('http-verification.json',{'status':'passed','exact_task_revision':2,'new_selected_answers':2,'prior_unassessed_units_unchanged':12,'old_quarantine_unchanged':True,'foreign_review_rejected':True,'all_current_answers_preserved':True,'replays_new_provider_calls':0,'documentary_question_results_changed':False})
 print('Exact task attachments, zero-call replay, old gaps and cross-review rejection passed')
def verify_prepared():
 original=json.loads((PRIOR/'P-1001-answer.json').read_text());s=json.loads((OLD/'P-1001-selection.json').read_text())
 assert call('case-synthesis?id='+original['id'])==original
 for group in ('historical','followup'):
  selection=read(group+'-selection.json');p=call('context/plan',{'context_id':selection['context_id']})
  assert p==read(group+'-plan.json') and p['source_meaning_contract']=='source-meaning/1'
  assert p['source_passes']==1 and all(x<=20000 for x in p['request_bytes'])
  assert call('medication-interpretation/select',selection['request'])==selection
 state=call('medication-interpretation/task',{'task_id':TASK});write('task-state.json',state)
 assert state['task_revision']==2 and all(x['current'] for x in state['answers']) and all(x['current'] for x in state['selections'])
 assert call('medication-interpretation/gaps',{'review_id':s['review_id'],'answer_id':original['id']})==read('gaps.json')
 try:call('medication-interpretation/gaps',{'review_id':s['review_id'],'answer_id':json.loads((PRIOR/'P-7193-answer.json').read_text())['id']});raise AssertionError('Foreign review accepted')
 except urllib.error.HTTPError as e:assert e.code in (400,409)
 write('http-verification.json',{'status':'passed_preparation_only','exact_task_revision':2,'new_selected_contexts':2,'original_unassessed_units':12,'distinct_received_records':4,'source_passes_per_selection':1,'request_bytes':[14310,14526],'new_answers':0,'historical_interpretation':'pending_payload_approval','followup_interpretation':'not_submitted','prior_answer_and_quarantine_unchanged':True,'all_saved_answers_current':True,'selection_replay_exact':True,'cross_review_rejected':True})
 print('Two bounded selected contexts, exact replay, unchanged answer/task and cross-review rejection passed; no inference attempted.')

if __name__=='__main__':
 if sys.argv[1]=='prepare':prepare()
 elif sys.argv[1]=='verify-prepared':verify_prepared()
 elif sys.argv[1]=='run':run(sys.argv[2])
 else:verify()
