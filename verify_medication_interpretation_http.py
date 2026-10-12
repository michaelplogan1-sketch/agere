"""Actual selected source contexts, bounded provider execution, and answer return.

Modes are deliberate: prepare never calls a provider; interpret uses only the
saved new medication contexts. Prior pending financial/helper/diagnostic scopes
are excluded. All patients and received records are synthetic.
"""
import json, sys, urllib.request, urllib.error
from pathlib import Path
ROOT=Path(__file__).resolve().parent
OUT=ROOT/'artifacts/agere-medication-interpretation-v1'
OLD=ROOT/'artifacts/agere-medication-source-review-v1'
TASK='972c00cfb436e95844ab5b7d46f338938e1d40f96522c812d4669bb61d0801db'

def call(endpoint,data=None):
 request=urllib.request.Request('http://localhost:8885/api/stack/'+endpoint,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(request,timeout=900) as response:return json.load(response)
def write(name,value):(OUT/name).write_text(json.dumps(value,indent=2)+'\n')
def read(name):return json.loads((OUT/name).read_text())

def prepare():
 for patient in ('P-7193','P-1001'):
  r=json.loads((OLD/(patient+'-review.json')).read_text())
  if patient=='P-7193':
   ids=sorted({a['record_id'] for a in r['medication_accounts']})+['WHR-8d06d2c05848b12a8b7f88ab','WHL-R-a318da47c7b93467240ff1']
   questions=[q['receipt_id'] for q in r['questions'] if q['title']!='Medication use and hold report readiness']
  else:
   ids=sorted({a['record_id'] for a in r['medication_accounts'] if a['source_table'] in ('medication_review_decisions','reported_medication_entries','medication_history_interviews','received_pharmacy_information','medication_lists')})
   ids+=['WHL-R-23391c5192e5b555ab79fb','WHL-R-9110f11e3ceae61f2b2b70','WHL-R-9dd5a85b09a42201653c89','WHR-3bfc47b530d7232c6be62723']
   questions=[q['receipt_id'] for q in r['questions']]
  req={'review_id':r['id'],'question_receipt_ids':questions,'record_ids':ids}
  s=call('medication-interpretation/select',req);write(patient+'-selection.json',s)
  assert s==call('medication-interpretation/select',req),'Selection replay differs'
  assert s['status']=='source_context_prepared' and not s['omissions']
  assert len(s['selected_records'])<24 and s['unselected_narrative_record_ids']
  c=call('context?id='+s['context_id']);write(patient+'-context.json',c)
  assert not c['record_scope']['complete_chart'] and set(c['model_context']['sources'])==set(ids)
  p=call('context/plan',{'context_id':s['context_id']});write(patient+'-plan.json',p)
  assert max(p['request_bytes'])<=20000
  print(patient,'context',s['context_id'],'records',len(ids),'excluded narratives',len(s['unselected_narrative_record_ids']),'pass bytes',p['request_bytes'],flush=True)
  for bad in [{**req,'question_receipt_ids':['other']},{**req,'record_ids':['other']},{**req,'original_questions_changed':True}]:
   try:call('medication-interpretation/select',bad);raise AssertionError('Bad scope accepted')
   except urllib.error.HTTPError as e:assert e.code in (400,409)
 write('prepare-verification.json',{'passed':True,'new_paid_api_calls':0,'exact_selection_replay':True,'invalid_scopes_rejected':True})

def interpret(patient,recover=False):
 assert patient in ('P-7193','P-1001')
 s=read(patient+'-selection.json')
 # Runtime previews carry only selected source records, never a whole chart.
 a=call('context/recover' if recover else 'context/synthesize',{'context_id':s['context_id']});write(patient+'-answer-response.json',a)
 assert a['status']=='complete' and a['context_id']==s['context_id']
 attached=call('medication-interpretation/answer',{'selection_id':s['id'],'answer_id':a['id']});write(patient+'-attachment.json',attached)
 assert attached==call('medication-interpretation/answer',{'selection_id':s['id'],'answer_id':a['id']})
 assert not attached['original_questions_changed'] and not attached['clinical_execution_authorized']
 print(patient,'answer',a['id'],'new calls',a['new_paid_api_calls'],'findings',len(a['findings']),flush=True)

def partial():
 s=read('P-1001-selection.json');r=call('medication-interpretation/partial',{'selection_id':s['id']});write('P-1001-partial.json',r)
 assert r==call('medication-interpretation/partial',{'selection_id':s['id']})
 a=call('case-synthesis?id='+r['answer_id']);write('P-1001-source-answer.json',a)
 assert a['coverage']['system_unassessed_units']==12 and len(a['quarantined_source_passes'])==1
 print('P-1001 partial evidence attached, with 12/30 units explicitly unassessed and one quarantined output.')

def verify():
 s=read('P-7193-selection.json');a=read('P-7193-answer-response.json')
 state=call('medication-interpretation/list',{'review_id':s['review_id']});write('P-7193-state.json',state)
 assert len(state['answers'])==1 and state['answers'][0]['current']
 replay=call('context/recover',{'context_id':s['context_id']});assert replay['cache_hit'] and replay['new_paid_api_calls']==0
 write('P-7193-replay.json',{'id':replay['id'],'cache_hit':replay['cache_hit'],'new_paid_api_calls':replay['new_paid_api_calls']})
 p=read('P-1001-partial.json');s2=read('P-1001-selection.json')
 partials=call('medication-interpretation/partials',{'review_id':s2['review_id']});assert len(partials['partials'])==1 and partials['partials'][0]['current'];write('P-1001-partial-state.json',partials)
 for bad in [{'selection_id':s2['id'],'answer_id':a['id']},{'selection_id':s2['id'],'answer_id':p['answer_id']}]:
  try:call('medication-interpretation/answer',bad);raise AssertionError('Cross-patient or partial answer accepted as complete')
  except urllib.error.HTTPError as e:assert e.code in (400,409)
 state=call('medication-interpretation/task',{'task_id':TASK});write('task-state.json',state)
 assert state['answers']==[], 'Partial output must not appear among complete attachments'
 availability=call('capabilities/availability?task_id='+TASK);write('task-availability.json',availability)
 assert len(availability['answers'])==1 and availability['task_revision']==2
 assert availability['answers'][0]['answer_id']==p['answer_id'] and availability['answers'][0]['status']=='partial_source_passes_for_review'
 old=call('capabilities/availability?task_id=a102c3a9d7c9b460a2e6665c048b78ba6e6c76c9d3ff3705fbfeeecacd3b6443');assert old['answers'];write('prior-task-availability.json',old)
 write('http-verification.json',{'passed':True,'complete_interpretations':1,'explicit_partial_interpretations':1,'cache_replay_no_new_calls':True,'cross_patient_answer_rejected':True,'partial_as_complete_rejected':True,'task_revision_unchanged':2,'original_questions_changed':False,'existing_task_answer_coverage_preserved':True,'partial_task_evidence_visible':True})
 print('Exact answer return, explicit partial task coverage, original revision and cached replay verified.')

if __name__=='__main__':
 if sys.argv[1] in ('interpret','recover'):interpret(sys.argv[2],recover=sys.argv[1]=='recover')
 else:{'prepare':prepare,'partial':partial,'verify':verify}[sys.argv[1]]()
