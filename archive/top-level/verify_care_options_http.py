import json,sys
from pathlib import Path
from urllib.error import HTTPError
import verify_reference_lifecycle_http as base
OUT=Path('artifacts/agere-care-options-v1');api=base.api
MANIFEST=json.loads(Path('artifacts/agere-reference-lifecycle-v1/successor.json').read_text())
QUESTION='Which pulmonary rehabilitation delivery alternatives are supported for review, and what patient, buyer-service and plan prerequisites remain unresolved?'
RATIONALE='Compare the dated public reference with this exact respiratory service question. The buyer currently declares evidence-review services; do not infer that either rehabilitation program is available or clinically appropriate.'
def save(name,v):(OUT/(name+'.json')).write_text(json.dumps(v,indent=2)+'\n')
def main():
 req={'manifest_id':MANIFEST['id'],'question':QUESTION,'rationale':RATIONALE,'service_bindings':[]}
 if sys.argv[-1]=='prepare':
  choices=api('care-options/choices',{'manifest_id':MANIFEST['id']});p=api('care-options/preview',req)
  assert len(choices['alternatives'])==2 and choices['services']
  assert len(p['questions'])==6 and len(p['questions'][0]['source_contexts'])==1
  assert all(a['mapping_status']=='no_service_proposed' for a in p['alternatives'])
  assert p['reported_comparative_estimates'][0]['interval_contains_null']
  assert not api('care-options/list',{'manifest_id':MANIFEST['id']})['comparisons']
  for name,v in [('request',req),('preview',p),('choices',choices),('history-before',base.tables())]:save(name,v)
  print(json.dumps({'status':'preview_verified','alternatives':2,'declared_plan_options':len(choices['services']),'clinical_applicability':False}));return
 history=api('care-options/list',{'manifest_id':MANIFEST['id']});assert len(history['comparisons'])==1
 row=history['comparisons'][0];assert row['current'] and row['request']==req
 result=api('care-options/prepare',{'comparison':req,'preview_id':row['id']});assert result=={k:v for k,v in row.items() if k not in ('current','stale_reasons')}
 for change in [{'preview_id':'0'*64},{'comparison':{**req,'selected_alternative':'remote'}},{'comparison':{**req,'manifest_id':base.OLD['id']}}]:
  try:api('care-options/prepare',{'comparison':req,'preview_id':row['id'],**change});raise AssertionError('Invalid request accepted')
  except HTTPError as e:assert e.code in (400,409)
 assert base.tables()==json.loads((OUT/'history-before.json').read_text())
 assert not row['clinical_applicability_established'] and not row['coverage_established'] and row['baseline']['selected_alternative'] is None
 assert row['task_id']==MANIFEST['task_id'] and row['patient_id']==MANIFEST['patient_id']
 assert row['original_service_question']==MANIFEST['original_target']['question']
 assert all(a['rank'] is None and a['proposed_service'] is None for a in row['alternatives'])
 save('comparison',result);save('state-after',history)
 report={'status':'passed','comparison_id':row['id'],'manifest_id':MANIFEST['id'],'task_id':row['task_id'],'patient_id':row['patient_id'],'alternatives':2,
 'linked_applicability_questions':6,'current_source_contexts':sum(c['current'] for q in row['questions'] for c in q['source_contexts']),
 'services_automatically_mapped':0,'ranked_alternatives':0,'individual_benefit_estimates':0,'evidence_families':len(row['evidence_families']),
 'reported_study_counts_pooled':False,'original_question_retained':True,'idempotency_and_invalid_requests_verified':True,
 'all_program_and_task_histories_unchanged':True,'provider_calls':0,'external_actions':0,'clinical_review_recorded':False}
 save('http-verification',report);print(json.dumps(report,indent=2))
if __name__=='__main__':main()
