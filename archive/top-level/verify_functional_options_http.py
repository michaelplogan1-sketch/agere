"""Actual functional-work integration; no clinical, payer or outbound action."""
import json,sys
from pathlib import Path
from urllib.error import HTTPError
from verify_reference_lifecycle_http import api,tables
OUT=Path('artifacts/agere-functional-options-v1');DEL=Path('artifacts/agere-functional-delivery-v1')
TASK='693081077ff08e07f21bdb3203a57511d9b0e45b754c5612a50b4674809de1a4'
BUYER='synthetic-patient-access-review';PID='SYN-69283717-971'
QUESTION='Which falls-prevention options merit review, and what current assessment, patient, service and benefit evidence is still needed?'
RATIONALE='The dated function report and support contact raise review questions. They do not confirm current age/residence/risk applicability, clinical assessment, consent or treatment indication. Keep separate trial comparisons unranked and leave buyer capabilities unverified.'
def save(name,row,out=OUT):(out/(name+'.json')).write_text(json.dumps(row,indent=2)+'\n')
def read(name,out=OUT):return json.loads((out/(name+'.json')).read_text())
def strip(r):return {k:v for k,v in r.items() if k not in ('current','stale_reasons')}
def reject(route,body):
 try:api(route,body)
 except HTTPError as e:assert e.code in (400,409);return
 raise AssertionError('Unsupported authority/input accepted')

if sys.argv[-1]=='prepare':
 state=api('reference-applicability/inspect',{'task_id':TASK});task=next(t for t in api('capabilities/tasks?buyer='+BUYER)['tasks'] if t['id']==TASK)
 ref=next(r for r in state['references'] if r['citation']['doi']=='10.1001/jama.2024.8481')
 target=next(t for t in state['original_targets'] if t['question']=='Determine present support needs and patient goals before any clinical referral')
 save('history-before',tables());save('task-before',task)
 req={'task_id':TASK,'expected_revision':task['revision'],'target':target['target'],'reference_id':ref['id'],
  'reason':'Connect existing dated functional and support evidence to fall-prevention applicability review. Current age/residence/risk, assessment, preference and program capability remain unresolved; no care selection or action.'}
 manifest=api('reference-applicability/prepare',req);assert manifest==api('reference-applicability/prepare',req);assert len(manifest['questions'])==6
 q=manifest['questions'][0];selection={'target':{'kind':'reference_applicability_question','manifest_id':manifest['id'],'question_id':q['id']},
  'record_ids':['WHL-R-4161dc2f4159ac11c819aa','WHL-R-c3f535803e8b47bf0b8c06','WHL-R-0bb9ee548fed1ae8ee2bb0']}
 acquired=api('readiness/context',selection);context=api('context?id='+acquired['context_id'])
 assert context['patient_id']==PID and context['parameters']['question']==q['question']
 assert not api('readiness/inspect',{'target':selection['target']})['documentary_question_addressed']
 request={'manifest_id':manifest['id'],'question':QUESTION,'rationale':RATIONALE,'service_bindings':[]};preview=api('care-options/preview',request)
 assert len(preview['alternatives'])==2 and len(preview['questions'])==6 and preview['questions'][0]['source_contexts'][0]['current']
 assert [x['point'] for x in preview['reported_comparative_estimates']]==[.85,.84]
 for n,row in [('reference-request',req),('manifest',manifest),('source-request',selection),('acquisition',acquired),('context',context),('comparison-request',request),('comparison-preview',preview)]:save(n,row)
 print(json.dumps({'status':'prepared','manifest_id':manifest['id'],'patient':PID,'source_scope':acquired['record_scope'],'comparison_preview':preview['id']}))
elif sys.argv[-1]=='verify':
 m=read('manifest');rows=api('care-options/list',{'manifest_id':m['id']})['comparisons'];assert len(rows)==1 and rows[0]['current'];c=strip(rows[0])
 assert c==api('care-options/prepare',{'comparison':read('comparison-request'),'preview_id':c['id']})
 contexts=api('care-options/context-list',{'comparison_id':c['id']})['contexts'];assert len(contexts)==1 and contexts[0]['current'];context=strip(contexts[0])
 deliveries=api('care-options/delivery-list',{'comparison_id':c['id']})['proposals'];assert len(deliveries)==1 and deliveries[0]['current'];delivery=strip(deliveries[0])
 assert delivery['review_alternative']['key']=='multifactorial';assert delivery['coverage_of_proposal']['components_without_proposals']==['risk_assessment','tailored_followthrough']
 assert delivery==api('care-options/delivery-prepare',{'proposal':delivery['request'],'preview_id':delivery['id']})
 portfolio=api('care-options/portfolio-choices',{'delivery_id':delivery['id']});save('portfolio-choices',portfolio,DEL)
 assert context['patient_context']['patient_id']==PID
 assert context['selected_coverage_record'] is None and not context['patient_preference_confirmed']
 assert c['patient_context'] is not None and c['baseline']['selected_alternative'] is None
 assert all(a['rank'] is None and a['individual_benefit'] is None and a['proposed_service'] is None for a in c['alternatives'])
 assert all(q['state']=='applicability_unresolved' for q in c['questions'])
 reject('care-options/preview',{**read('comparison-request'),'clinical_applicability_established':True})
 reject('care-options/delivery-preview',{**delivery['request'],'component_bindings':[{'component_key':'cbt_service','option_id':'foreign','rationale':'Invalid component'}]})
 reject('care-options/delivery-prepare',{'proposal':delivery['request'],'preview_id':'changed'})
 assert tables()==read('history-before')
 assert api('context?id='+read('context')['id'])==read('context')
 save('comparison',c);save('received-context',context);save('state-after',rows);save('proposal',delivery,DEL)
 report={'status':'passed','task_id':TASK,'patient_id':PID,'manifest_id':m['id'],'comparison_id':c['id'],'received_context_id':context['id'],'delivery_id':delivery['id'],
  'alternatives':2,'applicability_questions':6,'prepared_source_records':len(read('acquisition')['record_scope']['record_ids']),
  'received_context_summary':context['summary'],'required_delivery_components':2,'proposed_component_services':0,'portfolio_services':portfolio['portfolio_services'],
  'all_program_and_task_history_unchanged':True,'clinical_alternatives_selected':0,'head_to_head_estimate_invented':False,'payer_or_patient_preference_inferred':False,
  'provider_calls':0,'external_actions':0,'clinical_validation':False}
 save('http-verification',report);save('http-verification',report,DEL);print(json.dumps(report,indent=2))
