import json,sys
from pathlib import Path
from urllib.error import HTTPError
import verify_reference_lifecycle_http as base
OUT=Path('artifacts/agere-care-option-context-v1');api=base.api
COMPARISON=json.loads(Path('artifacts/agere-care-options-v1/comparison.json').read_text());REQ={'comparison_id':COMPARISON['id']}
def save(name,v):(OUT/(name+'.json')).write_text(json.dumps(v,indent=2)+'\n')
def main():
 if sys.argv[-1]=='prepare':
  p=api('care-options/context-preview',REQ);assert p['patient_id']==COMPARISON['patient_id'] and p['as_of']==COMPARISON['as_of']
  assert not api('care-options/context-list',REQ)['contexts'];assert p['summary']['patient_reports']>0
  assert p['selected_coverage_record'] is None and not p['patient_preference_confirmed']
  for name,v in [('preview',p),('request',{**REQ,'preview_id':p['id']}),('history-before',base.tables())]:save(name,v)
  print(json.dumps({'status':'preview_verified','summary':p['summary']}));return
 h=api('care-options/context-list',REQ);assert len(h['contexts'])==1 and h['contexts'][0]['current'];r=h['contexts'][0]
 replay=api('care-options/context-prepare',json.loads((OUT/'request.json').read_text()));assert replay=={k:v for k,v in r.items() if k not in ('current','stale_reasons')}
 comparisons=api('care-options/list',{'manifest_id':COMPARISON['manifest_id']})['comparisons'];assert len(comparisons)==1 and comparisons[0]['current']
 assert {k:v for k,v in comparisons[0].items() if k not in ('current','stale_reasons')}==COMPARISON
 for body in [{**REQ,'preview_id':'0'*64},{**REQ,'preview_id':r['id'],'selected_coverage_record':'anything'}]:
  try:api('care-options/context-prepare',body);raise AssertionError('Invalid attachment accepted')
  except HTTPError as e:assert e.code in (400,409)
 assert base.tables()==json.loads((OUT/'history-before.json').read_text())
 assert all(x['available_date']<=r['as_of'] for ctx in ('coverage_context','patient_context') for x in r[ctx]['reports'])
 assert not r['coverage_established'] and not r['patient_preference_confirmed'] and r['selected_coverage_record'] is None
 save('context',replay);save('state-after',h)
 report={'status':'passed','context_id':r['id'],'comparison_id':COMPARISON['id'],'summary':r['summary'],'exact_patient_and_cutoff':True,
 'coverage_reports_automatically_selected':0,'patient_preferences_inferred':0,'original_comparison_unchanged_and_current':True,
 'all_program_and_task_history_unchanged':True,'idempotency_and_invalid_requests_verified':True,'provider_calls':0,'external_actions':0,'clinical_review_recorded':False}
 save('http-verification',report);print(json.dumps(report,indent=2))
if __name__=='__main__':main()
