"""Live behavioral task/reference/source/comparison verification, no clinical action."""
import json,sys
from pathlib import Path
from urllib.error import HTTPError
from verify_reference_lifecycle_http import api,tables
OUT=Path('artifacts/agere-behavioral-options-v1')
TASK='fda1b414416968697bc1e71fdfed36d0556ac13990316bd5aaef9a4645aa829c'
QUESTION='Which initial depression-treatment alternatives merit review if current assessment confirms the reference population, and what clinical, patient, service and benefit questions remain?'
RATIONALE='The saved source answer contains condition labels only. Compare dated reference alternatives without inferring current episode, severity, prior treatment, indication, patient preference or an available therapy service. Reconcile the known ACP update before clinical use.'
def save(name,row):(OUT/(name+'.json')).write_text(json.dumps(row,indent=2)+'\n')
def read(name):return json.loads((OUT/(name+'.json')).read_text())
def main():
 if sys.argv[-1]=='prepare':
  state=api('reference-applicability/inspect',{'task_id':TASK});tasks=api('capabilities/tasks?buyer=synthetic-domain-source-review')
  task=next(t for t in tasks['tasks'] if t['id']==TASK)
  ref=next(r for r in state['references'] if r['citation']['doi']=='10.7326/M22-2056')
  target=next(t for t in state['original_targets'] if t['question']=='Verify patient goals and exact service applicability')
  save('history-before',tables());save('task-before',task)
  req={'task_id':TASK,'expected_revision':task['revision'],'target':target['target'],'reference_id':ref['id'],
       'reason':'Connect the saved depression/anxiety condition-label review to initial MDD treatment alternatives for possible applicability review. Diagnosis, current episode/severity, initial-treatment status and current guideline wording are unresolved; no anxiety recommendation or treatment choice is made.'}
  m=api('reference-applicability/prepare',req);assert m==api('reference-applicability/prepare',req)
  q=m['questions'][0];target={'kind':'reference_applicability_question','manifest_id':m['id'],'question_id':q['id']}
  source_req={'target':target,'record_ids':['WHR-86cfd56a6cf3ddf61cdedf1e','WHR-fa89fe93e19ed2abb014683d']}
  a=api('readiness/context',source_req);ctx=api('context?id='+a['context_id'])
  assert ctx['patient_id']=='P-7193' and ctx['parameters']['question']==q['question']
  assert not api('readiness/inspect',{'target':target})['documentary_question_addressed']
  comp={'manifest_id':m['id'],'question':QUESTION,'rationale':RATIONALE,'service_bindings':[]}
  preview=api('care-options/preview',comp);assert len(preview['alternatives'])==3 and len(preview['questions'])==6
  assert preview['questions'][0]['source_contexts'][0]['current'] and not preview['reported_comparative_estimates']
  for n,v in [('reference-request',req),('manifest',m),('source-request',source_req),('acquisition',a),('context',ctx),('comparison-request',comp),('comparison-preview',preview)]:save(n,v)
  print(json.dumps({'prepared_reference':m['id'],'source_records':2,'comparison_preview':preview['id'],'patient_id':m['patient_id']}));return
 m=read('manifest');req=read('comparison-request');hist=api('care-options/list',{'manifest_id':m['id']})
 assert len(hist['comparisons'])==1 and hist['comparisons'][0]['current']
 row=hist['comparisons'][0];raw={k:v for k,v in row.items() if k not in ('current','stale_reasons')}
 assert raw==api('care-options/prepare',{'comparison':req,'preview_id':raw['id']})
 attached=api('care-options/context-list',{'comparison_id':raw['id']})['contexts'];assert len(attached)==1 and attached[0]['current']
 a=attached[0];araw={k:v for k,v in a.items() if k not in ('current','stale_reasons')}
 assert araw==api('care-options/context-prepare',{'comparison_id':raw['id'],'preview_id':araw['id']})
 for route,body in [('care-options/prepare',{'comparison':req,'preview_id':'bad'}),('care-options/preview',{**req,'selected_alternative':'cbt'}),('reference-applicability/prepare',{**read('reference-request'),'clinical_execution_authorized':True})]:
  try:api(route,body);raise AssertionError('Invalid authority or stale preview accepted')
  except HTTPError as e:assert e.code in (400,409)
 assert row['patient_id']=='P-7193' and row['task_id']==TASK and row['manifest_id']==m['id']
 assert not row['clinical_applicability_established'] and row['baseline']['selected_alternative'] is None
 assert all(x['proposed_service'] is None and x['rank'] is None and x['individual_benefit'] is None for x in row['alternatives'])
 assert a['selected_coverage_record'] is None and not a['patient_preference_confirmed']
 assert tables()==read('history-before')
 save('comparison',raw);save('received-context',araw);save('state-after',hist)
 report={'status':'passed','task_id':TASK,'patient_id':'P-7193','manifest_id':m['id'],'comparison_id':raw['id'],'received_context_id':araw['id'],
   'alternatives':3,'applicability_questions':6,'source_records':2,'received_context_summary':a['summary'],
   'known_update_content':'unreconciled','numerical_effects_invented':0,'clinical_alternatives_selected':0,'buyer_services_automatically_mapped':0,
   'idempotency_and_invalid_authority_verified':True,'all_program_and_buyer_history_unchanged':True,'provider_calls':0,'external_actions':0,'clinical_validation':False}
 save('http-verification',report);print(json.dumps(report,indent=2))
if __name__=='__main__':main()
