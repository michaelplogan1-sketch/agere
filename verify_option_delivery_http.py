import json,sys
from pathlib import Path
from urllib.error import HTTPError
from verify_reference_lifecycle_http import api,tables
OUT=Path('artifacts/agere-option-delivery-v1')
COMPARISON=json.loads(Path('artifacts/agere-behavioral-options-v1/comparison.json').read_text())
NOTE='The buyer declares evidence and clinician review only. No CBT or prescribing capability is established. Keep both components unassigned pending clinical suitability, patient preference, exact service scope, benefits and coordination review.'
REQUEST={'comparison_id':COMPARISON['id'],'alternative_key':'combined','component_bindings':[],'coordination_note':NOTE}
def save(name,row):(OUT/(name+'.json')).write_text(json.dumps(row,indent=2)+'\n')
def main():
 if sys.argv[-1]=='prepare':
  c=api('care-options/delivery-choices',{'comparison_id':COMPARISON['id']});p=api('care-options/delivery-preview',REQUEST)
  assert len(c['alternatives'])==3 and len(c['services'])==2
  assert p['coverage_of_proposal']['components_without_proposals']==['cbt_service','medication_service']
  assert not api('care-options/delivery-list',{'comparison_id':COMPARISON['id']})['proposals']
  for n,v in [('choices',c),('request',REQUEST),('preview',p),('history-before',tables())]:save(n,v)
  print(json.dumps({'preview_id':p['id'],'unassigned_components':2,'buyer_services':2}));return
 rows=api('care-options/delivery-list',{'comparison_id':COMPARISON['id']})['proposals'];assert len(rows)==1 and rows[0]['current']
 raw={k:v for k,v in rows[0].items() if k not in ('current','stale_reasons')}
 assert raw['request']==REQUEST and raw==api('care-options/delivery-prepare',{'proposal':REQUEST,'preview_id':raw['id']})
 for req in [{'proposal':REQUEST,'preview_id':'bad'},{'proposal':{**REQUEST,'clinical_alternative_selected':True},'preview_id':raw['id']},
    {'proposal':{**REQUEST,'component_bindings':[{'component_key':'cbt_service','option_id':'foreign','rationale':'Wrong service fixture'}]},'preview_id':raw['id']}]:
  try:api('care-options/delivery-prepare',req);raise AssertionError('Invalid proposal accepted')
  except HTTPError as e:assert e.code in (400,409)
 assert raw['patient_id']==COMPARISON['patient_id'] and raw['task_id']==COMPARISON['task_id']
 assert not raw['clinical_alternative_selected'] and raw['care_tasks_created']==0 and raw['service_slots_reserved']==0
 assert raw['coverage_of_proposal']['components_with_proposals']==0 and raw['coverage_of_proposal']['required_components']==2
 current=api('care-options/list',{'manifest_id':COMPARISON['manifest_id']})['comparisons'];assert len(current)==1 and current[0]['current']
 assert {k:v for k,v in current[0].items() if k not in ('current','stale_reasons')}==COMPARISON
 assert tables()==json.loads((OUT/'history-before.json').read_text())
 save('proposal',raw);save('state-after',rows)
 report={'status':'passed','proposal_id':raw['id'],'comparison_id':COMPARISON['id'],'patient_id':raw['patient_id'],'task_id':raw['task_id'],
   'components':2,'components_without_proposals':2,'buyer_service_declarations':2,'service_mappings_invented':0,'clinical_alternative_selected':False,
   'original_comparison_unchanged_and_current':True,'all_program_and_task_history_unchanged':True,'idempotency_invalid_authority_and_foreign_service_verified':True,
   'provider_calls':0,'external_actions':0,'clinical_validation':False}
 save('http-verification',report);print(json.dumps(report,indent=2))
if __name__=='__main__':main()
