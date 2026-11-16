"""Actual appointment -> declared buyer -> internal handoff -> responsibility packet."""
import copy,json
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
OUT=Path('artifacts/agere-appointment-navigation-v1');ACTOR='Agere automated synthetic build';BUYER='synthetic-appointment-navigation';PID='P-3002'
def api(route,body=None):
 with urlopen(Request('http://localhost:8885/api/stack/'+route,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'}),timeout=240) as r:return json.load(r)
def save(n,r):(OUT/(n+'.json')).write_text(json.dumps(r,indent=2)+'\n')
def reject(route,body):
 try:api(route,body)
 except HTTPError as e:
  payload=e.read().decode();assert e.code in (400,409),(e.code,payload);return payload
 raise AssertionError('Invalid request accepted')
def main():
 full=json.loads(Path('artifacts/agere-appointment-followthrough-v1/population-summary.json').read_text())
 n=api('appointment-navigation/prepare',{'review_id':full['id'],'patient_id':PID});save('source-review',n)
 assert n['appointment']['status']=='explicit_no_show' and len(n['appointment']['sources'])==4
 assert n['original_work_questions']==json.loads(Path('artifacts/agere-appointment-followthrough-v1/work-proposal.json').read_text())['patients'][0]['missing_information']
 print('Exact original source review and questions preserved',flush=True)
 from clinical_core.agere.active import stack_path
 from clinical_core.agere.clinical import coverage_sources as S,patient_context as P,care_capabilities as C
 coverage=S.prepare(stack_path(),{'patient_id':PID,'as_of':n['as_of']});save('coverage-context',coverage)
 selected=S.select(coverage,coverage['reports'][0]['record_id']);save('coverage-selection',selected)
 patient=P.prepare(stack_path(),{'patient_id':PID,'as_of':n['as_of']});save('patient-context',patient)
 fields=selected['routing_fields']
 portfolio=C.portfolio(stack_path(),BUYER)
 if portfolio['revision']==0:
  cap={'id':'internal-navigation','name':'Synthetic appointment evidence navigation','description':'Local synthetic evidence review only; no patient outreach, clinical care or scheduling capability is claimed.','need_tags':['care_navigation'],'modality':'virtual','regions':[],'payer_types':[],'plan_ids':[],'pathway_ids':[],'owner':{'kind':'agent','id':'synthetic-navigation-evidence-reviewer'},'available':True,'capacity_slots':None,'capacity_as_of':n['as_of'],'capacity_until':n['as_of'],'effective_start':'2029-01-01','effective_end':'2029-12-31','prerequisites':['Verify current need, patient agreement, service scope, exact benefits and participation before any care action.'],'provenance':'Engineering synthetic buyer fixture. No real service, clinician availability or contract is represented.'}
  unavailable={**copy.deepcopy(cap),'id':'unavailable-navigation','name':'Synthetic unavailable navigation','available':False}
  mismatch={**copy.deepcopy(cap),'id':'different-plan','name':'Synthetic other-plan navigation','plan_ids':['SYNTHETIC-OTHER-PLAN']}
  portfolio=api('capabilities/save',{'buyer_id':BUYER,'expected_revision':0,'actor':ACTOR,'capabilities':[cap,unavailable,mismatch]})
 save('portfolio',portfolio)
 req={'buyer_id':BUYER,'origin':{'kind':'appointment_navigation_review','id':n['id'],'finding_ids':[]},'need_tags':['care_navigation'],'rationale':'Reconcile received historical appointment evidence and present navigation questions; no cause, current need or care priority inferred.','coverage':{**fields,'region':'','preferences':'Current goals, need and consent require confirmation.'},'coverage_binding':{'context_id':coverage['id'],'record_id':selected['record_id']},'patient_context_id':patient['id']}
 save('match-request',req);p=api('capabilities/prepare',req);save('plan',p)
 options={o['capability']['id']:o for o in p['options']}
 assert options['internal-navigation']['status']=='requires_verification'
 assert options['unavailable-navigation']['status']=='blocked'
 assert options['different-plan']['status']=='blocked',fields
 for name in ('unavailable-navigation','different-plan'):
  reject('capabilities/queue',{'plan_id':p['id'],'option_id':options[name]['id'],'actor':ACTOR})
 wrong=copy.deepcopy(req);wrong['need_tags']=['medication_review'];reject('capabilities/prepare',wrong)
 wrong=copy.deepcopy(req);wrong['coverage']['plan_id']='FORGED';reject('capabilities/prepare',wrong)
 print('Matching, exact source bindings, mismatch and unavailable path verified',flush=True)
 queue={'plan_id':p['id'],'option_id':options['internal-navigation']['id'],'actor':ACTOR,'participation_context_id':p['participation_contexts'][options['internal-navigation']['id']]['id']}
 t=api('capabilities/queue',queue)['task'];assert api('capabilities/queue',queue)['idempotent_replay']
 if t['state']=='queued_for_internal_review':t=api('capabilities/event',{'task_id':t['id'],'expected_revision':t['revision'],'request_id':'appointment-navigation-handoff-v1','action':'prepare_handoff','actor':ACTOR,'note':'Prepare a local synthetic evidence packet. No contact, scheduling, clinical approval or real service assignment.'})['task']
 save('task',t);assert t['handoff']['origin']['review']==n
 state=api('appointment-navigation/task',{'task_id':t['id']});save('task-context',state)
 assert state['patient_context']['handoff_context_current'] and state['coverage_context']['handoff_coverage_current'] and state['participation_context']['handoff_participation_current']
 proposal={'task_id':t['id'],'expected_revision':t['revision'],'component_bindings':[{'component_key':'source_reconciliation','option_id':options['internal-navigation']['id'],'rationale':'Propose the synthetic local evidence reviewer for source reconciliation only. No accepted clinical or patient-facing assignment.'}],'coordination_note':'The other three responsibilities remain unassigned. Historical no-show does not establish present need, its cause, consent or entitlement.'}
 preview=api('appointment-navigation/preview',proposal);saved=api('appointment-navigation/save',{'proposal':proposal,'preview_id':preview['id']});save('proposal',saved)
 assert api('appointment-navigation/save',{'proposal':proposal,'preview_id':preview['id']})==saved
 assert len(saved['unassigned_components'])==3 and all(not x['question_resolved'] for x in saved['components'])
 wrong=copy.deepcopy(proposal);wrong['expected_revision']=1;reject('appointment-navigation/preview',wrong)
 wrong=copy.deepcopy(proposal);wrong['component_bindings'][0]['option_id']='other-plan';reject('appointment-navigation/preview',wrong)
 final=api('appointment-navigation/task',{'task_id':t['id']});assert len(final['proposals'])==1 and final['proposals'][0]['current']
 result={'status':'passed','patient_id':PID,'review_id':n['id'],'original_appointment_review_id':full['id'],'source_records':len(n['appointment']['sources']),'exact_history_records':len(n['appointment']['history_record_ids']),'original_work_questions_preserved':True,'received_coverage_reports':len(coverage['reports']),'received_patient_reports':len(patient['reports']),'plan_id':p['id'],'task_id':t['id'],'task_revision':t['revision'],'proposal_id':saved['id'],'blocked_services':['unavailable-navigation','different-plan'],'unassigned_components':saved['unassigned_components'],'bound_contexts_current':True,'idempotent_replay':True,'questions_resolved':0,'patient_contact_or_scheduling':0,'new_provider_calls':0,'external_actions':0}
 save('http-verification',result);print(json.dumps(result,indent=2))
if __name__=='__main__':main()
