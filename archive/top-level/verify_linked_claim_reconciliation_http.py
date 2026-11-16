"""Live local synthetic selected-medication claim review; no blocked scopes."""
import json,sys,urllib.request,urllib.error
from pathlib import Path
OUT=Path('artifacts/agere-linked-reconciliation-v1');OLD=Path('artifacts/agere-medication-interpretation-v1')
TASK='972c00cfb436e95844ab5b7d46f338938e1d40f96522c812d4669bb61d0801db'
PARENTS={'P-1001':'e1fc1464ceda1700ba39baeb4fa7193cb6112d8416fdc0f00a3ece2b9011727a','P-7193':'d3539cbb4705798ae26c49f8ffccada74d98320e5c73f28c09abc4b0e776c131'}
def call(endpoint,body=None):
 req=urllib.request.Request('http://localhost:8885/api/stack/'+endpoint,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=300) as response:return json.load(response)
def write(name,r):(OUT/name).write_text(json.dumps(r,indent=2)+'\n')
def run(patient):
 assert patient in PARENTS
 parent=PARENTS[patient];selection=json.loads((OLD/(patient+'-selection.json')).read_text())
 plan=call('claims/plan',{'answer_id':parent});write(patient+'-plan.json',plan);print(patient,'plan',plan['request_bytes'],'bytes',plan['source_claims'],'claims',flush=True)
 a=call('claims/reconcile',{'answer_id':parent});write(patient+'-answer.json',a)
 assert a['status']=='complete' and a['context_id']==selection['context_id'] and a['coverage']['all_source_claims_accounted']
 assert len(a['claim_reviews'])==plan['source_claims'];assert len(a['findings'])<=4
 attach=call('medication-interpretation/answer',{'selection_id':selection['id'],'answer_id':a['id']});write(patient+'-attachment.json',attach)
 assert not attach['original_questions_changed'] and not attach['clinical_execution_authorized']
 print(patient,'answer',a['id'],'calls',a['new_paid_api_calls'],'claims',len(a['claim_reviews']),'conclusions',len(a['findings']),flush=True)
def verify():
 for patient,parent in PARENTS.items():
  a=json.loads((OUT/(patient+'-answer.json')).read_text());old=call('case-synthesis?id='+parent)
  assert a['passes']==old['passes'] and a['coverage']['model_accounted_units']==old['coverage']['model_accounted_units']
  assert a['coverage'].get('system_unassessed_units',0)==old['coverage'].get('system_unassessed_units',0)
  replay=call('claims/reconcile',{'answer_id':parent});assert replay['cache_hit'] and replay['new_paid_api_calls']==0
  write(patient+'-replay.json',{'id':replay['id'],'cache_hit':replay['cache_hit'],'new_paid_api_calls':replay['new_paid_api_calls']})
  selection=json.loads((OLD/(patient+'-selection.json')).read_text());state=call('medication-interpretation/list',{'review_id':selection['review_id']});write(patient+'-state.json',state)
  assert all(x['current'] for x in state['answers'])
  attach=call('medication-interpretation/answer',{'selection_id':selection['id'],'answer_id':a['id']});assert attach==json.loads((OUT/(patient+'-attachment.json')).read_text())
 s=json.loads((OLD/'P-1001-selection.json').read_text());p=call('medication-interpretation/partials',{'review_id':s['review_id']});assert p['partials'][0]['current'];write('prior-partial-state.json',p)
 availability=call('capabilities/availability?task_id='+TASK);write('task-availability.json',availability)
 assert availability['task_revision']==2 and len(availability['answers'])==2
 assert sum(x.get('status')=='partial_source_passes_for_review' for x in availability['answers'])==1
 assert any(x['coverage'].get('all_source_claims_accounted') and x['coverage']['system_unassessed_units']==12 for x in availability['answers'])
 wrong=json.loads((OUT/'P-7193-answer.json').read_text())
 try:call('medication-interpretation/answer',{'selection_id':s['id'],'answer_id':wrong['id']});raise AssertionError('Cross-patient attachment allowed')
 except urllib.error.HTTPError as e:assert e.code in (400,409)
 write('http-verification.json',{'passed':True,'new_exact_claim_review_answers':2,'source_claims_accounted':29,'old_complete_and_partial_answers_preserved':True,'original_task_revision':2,'original_question_results_unchanged':True,'source_unit_coverage_unchanged':True,'replay_zero_new_calls':True,'cross_patient_rejected':True})
 print('Both claim reviews, exact attachments, unchanged histories/coverage and zero-call replays verified.')
if __name__=='__main__':
 if sys.argv[1]=='run':run(sys.argv[2])
 else:verify()
