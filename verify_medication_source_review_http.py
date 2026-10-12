"""Two received patients: exact-plan mismatch and a local pharmacy handoff.

No patient contact, provider calls, verified benefit or clinical review.
"""
import json,sqlite3,urllib.request,urllib.error,sys
from pathlib import Path
from clinical_core.contracts import digest
from clinical_core.agere.clinical import medication_source_review as M,record_context as RC
ROOT=Path(__file__).resolve().parent;OUT=ROOT/'artifacts/agere-medication-source-review-v1';OUT.mkdir(exist_ok=True)
STACK=ROOT/'artifacts/agere-healthcare-v123/stack'
P7193='72a14f250440632549946c522f97e1f87ee17a911518d2571104a6e483e3ae44'

def call(endpoint,data=None):
 req=urllib.request.Request('http://localhost:8885/api/stack/'+endpoint,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=240) as r:return json.load(r)
def write(name,value):(OUT/name).write_text(json.dumps(value,indent=2)+'\n')
def visit(value):
 if isinstance(value,dict):
  yield value
  for v in value.values():yield from visit(v)
 elif isinstance(value,list):
  for v in value:yield from visit(v)
def prepare():
 for patient,portfolio in [('P-7193',P7193),('P-1001',None)]:
  if portfolio is None:
   question='Compare evidence-review bundles for patient P-1001 using contracts medication_regimen_confirmation,native_medication_nonuse with 3 review slots.'
   graph=call('compose',{'question':question});write(patient+'-question.json',graph)
   found=[v for v in visit(graph) if v.get('schema')=='evidence-review-portfolio/1' and v.get('id')]
   assert found,'Planner did not return evidence portfolio';portfolio=found[0]['id']
  req={'portfolio_id':portfolio,'patient_id':patient};r=call('medication-source-review/prepare',req);write(patient+'-review.json',r)
  assert not r['original_questions_changed'] and not r['clinical_execution_authorized']
  records,_=RC.received(STACK,patient);ix={x['record_id']:x for x in records};fields=0
  for account in r['medication_accounts']+r['narrative_accounts']:
   for e in account['evidence']:
    raw=ix[e['record_id']];assert digest(raw)==e['record_hash'];assert M.M.value_at(raw,e['field_path'])==e['value'];fields+=1
  write(patient+'-source-audit.json',{'medication_records':r['medication_records'],'nested_items':r['nested_medication_items'],'narrative_records':r['narrative_records'],'exact_field_occurrences':fields,'unmapped_native_records':len(r['additional_unmapped_native_records']),'no_clinical_truth_asserted':True})
  if patient=='P-7193':assert (r['medication_records'],r['nested_medication_items'],r['narrative_records'])==(11,2,16);continue
  coverage=call('coverage/context',{'patient_id':patient,'as_of':r['as_of']});write(patient+'-coverage.json',coverage)
  matches=[v for v in coverage['reports'] if v['exact_plan_id']=='WH-FICTIONAL-PLAN-9d129a8824e631']
  assert matches,'Existing pharmacy declaration has no matching received plan report'
  # Explicit most recent dated report in this exact received plan, not verified membership.
  report=max(matches,key=lambda v:(v['source_date'] or '',v['record_id']))
  selected=call('coverage/select',{'context_id':coverage['id'],'record_id':report['record_id']});write(patient+'-coverage-selection.json',selected)
  pc=call('patient-context',{'patient_id':patient,'as_of':r['as_of']});write(patient+'-patient-context.json',pc)
  match={'buyer_id':'synthetic-coverage-buyer','origin':{'kind':M.KIND,'id':r['id'],'finding_ids':[]},'need_tags':['medication_review'],
   'rationale':'Local engineering review of received medication accounts and exact unresolved documentary questions; no titration indication or treatment gap established.',
   'coverage':{**selected['routing_fields'],'region':'','preferences':''},'coverage_binding':{'context_id':coverage['id'],'record_id':report['record_id']},'patient_context_id':pc['id']}
  write(patient+'-match-request.json',match);plan=call('capabilities/prepare',match);write(patient+'-plan.json',plan)
  option=next(o for o in plan['options'] if o['capability']['id']=='coverage-linked-pharmacy');assert option['status']=='requires_verification'
 print('Prepared two source-bound medication reviews and the available declared pharmacy match; no task queued yet.')
def handoff():
 db=sqlite3.connect(STACK/'program-work.sqlite3')
 candidates=[json.loads(r[0]) for r in db.execute('SELECT payload FROM capability_plans ORDER BY rowid')];db.close()
 blocked=next(p for p in reversed(candidates) if p['patient_id']=='P-7193' and p['origin']['kind']==M.KIND)
 assert all(o['status']=='blocked' for o in blocked['options']);write('P-7193-blocked-plan.json',blocked)
 try:
  call('capabilities/queue',{'plan_id':blocked['id'],'option_id':blocked['options'][0]['id'],'actor':'Automated synthetic medication source verification'})
  raise AssertionError('Exact-plan mismatch incorrectly queued')
 except urllib.error.HTTPError as e:assert e.code in (400,409)
 p=json.loads((OUT/'P-1001-plan.json').read_text());o=next(o for o in p['options'] if o['capability']['id']=='coverage-linked-pharmacy')
 queue={'plan_id':p['id'],'option_id':o['id'],'actor':'Automated synthetic medication source verification','participation_context_id':p['participation_contexts'][o['id']]['id']}
 task=call('capabilities/queue',queue)['task'];write('queue.json',task)
 event={'task_id':task['id'],'expected_revision':task['revision'],'request_id':'medication-source-review-v1-handoff-'+p['id'],'action':'prepare_handoff','actor':queue['actor'],'note':'Prepare local medication evidence review only. No clinician review, prescribing, patient contact or external dispatch.'}
 # Exact repeat must replay the same request, not advance task history.
 if (OUT/'handoff-request.json').exists():event=json.loads((OUT/'handoff-request.json').read_text())
 else:write('handoff-request.json',event)
 result=call('capabilities/event',event);write('handoff.json',result)
 assert result['task']['handoff']['origin']['review']==json.loads((OUT/'P-1001-review.json').read_text())
 assert not result['task']['handoff']['human_review_completed'] and not result['task']['handoff']['external_dispatch_performed']
 assert call('capabilities/event',event)['idempotent_replay']
 cov=call('coverage/task?id='+task['id']);assert cov['handoff_coverage_current'];write('handoff-coverage.json',cov)
 pc=call('patient-context/task?id='+task['id']);assert pc['handoff_context_current'];write('handoff-patient-context.json',pc)
 rejected=[]
 for changes in [{'patient_id':'P-NOT-IN-PORTFOLIO'},{'clinical_execution_authorized':True}]:
  try:call('medication-source-review/prepare',{'portfolio_id':P7193,'patient_id':'P-7193',**changes});raise AssertionError('Unexpected acceptance')
  except urllib.error.HTTPError as e:assert e.code in (400,409);rejected.append(e.code)
 write('http-verification.json',{'passed':True,'task_id':task['id'],'task_revision':result['task']['revision'],'patient_id':'P-1001','source_review_in_internal_handoff':True,'coverage_attachment_current':True,'patient_context_attachment_current':True,'idempotent_replay':True,'rejected':rejected,'new_paid_api_calls':0,'clinical_execution_authorized':False,'external_actions_executed':0})
 print('Verified exact medication-review handoff, coverage/patient attachments, replay and invalid scope rejection.')
if __name__=='__main__':{'prepare':prepare,'handoff':handoff}[sys.argv[1]]()
