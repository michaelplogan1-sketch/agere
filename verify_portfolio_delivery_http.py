import json,sys
from pathlib import Path
from urllib.error import HTTPError
from verify_reference_lifecycle_http import api,tables
OUT=Path('artifacts/agere-portfolio-delivery-v1')
DELIVERY=json.loads(Path('artifacts/agere-option-delivery-v1/proposal.json').read_text())
NOTE='Review all four buyer declarations, including those outside the original behavioral tags. The additional declarations are respiratory review services and do not establish CBT or prescribing delivery. Both components remain unassigned pending exact capability and clinical review.'
REQUEST={'delivery_id':DELIVERY['id'],'component_bindings':[],'coordination_note':NOTE}
def save(n,x):(OUT/(n+'.json')).write_text(json.dumps(x,indent=2)+'\n')
def main():
 if sys.argv[-1]=='prepare':
  c=api('care-options/portfolio-choices',{'delivery_id':DELIVERY['id']});p=api('care-options/portfolio-preview',REQUEST)
  assert (c['portfolio_services'],c['original_matched_services'])==(4,2)
  assert sum(not s['in_original_match'] for s in c['services'])==2
  assert not api('care-options/portfolio-list',{'delivery_id':DELIVERY['id']})['proposals']
  for n,x in [('choices',c),('request',REQUEST),('preview',p),('history-before',tables())]:save(n,x)
  print(json.dumps({'preview_id':p['id'],'same_buyer_services':4,'original_match_services':2,'unassigned_components':2}));return
 rows=api('care-options/portfolio-list',{'delivery_id':DELIVERY['id']})['proposals'];assert len(rows)==1 and rows[0]['current']
 row={k:v for k,v in rows[0].items() if k not in ('current','stale_reasons')}
 assert row['request']==REQUEST and row==api('care-options/portfolio-prepare',{'proposal':REQUEST,'preview_id':row['id']})
 invalid=[{**REQUEST,'buyer_id':'different'},{**REQUEST,'component_bindings':[{'component_key':'cbt_service','capability_id':'foreign-service','need_tags':['behavioral_health'],'rationale':'Invalid fixture'}]},
  {**REQUEST,'component_bindings':[{'component_key':'cbt_service','capability_id':'behavioral-evidence-review','need_tags':['not_declared'],'rationale':'Invalid fixture'}]}]
 for req in invalid:
  try:api('care-options/portfolio-preview',req);raise AssertionError('Invalid scope accepted')
  except HTTPError as e:assert e.code in (400,409)
 try:api('care-options/portfolio-prepare',{'proposal':REQUEST,'preview_id':'bad'});raise AssertionError('Stale preview accepted')
 except HTTPError as e:assert e.code in (400,409)
 assert row['coverage_of_proposal']['components_without_proposals']==['cbt_service','medication_service']
 old=api('care-options/delivery-list',{'comparison_id':DELIVERY['comparison_id']})['proposals'];assert len(old)==1 and old[0]['current']
 assert {k:v for k,v in old[0].items() if k not in ('current','stale_reasons')}==DELIVERY
 assert tables()==json.loads((OUT/'history-before.json').read_text())
 save('proposal',row);save('state-after',rows)
 report={'status':'passed','proposal_id':row['id'],'delivery_id':DELIVERY['id'],'patient_id':row['patient_id'],'task_id':row['task_id'],
  'same_buyer_services_available_for_explicit_selection':4,'services_in_original_tag_match':2,'component_services_invented':0,'unassigned_components':2,
  'original_component_review_unchanged_and_current':True,'all_program_and_buyer_history_unchanged':True,'idempotency_and_invalid_scope_verified':True,
  'positive_cross_tag_service_selection':'verified_in_disposable_integration_fixtures; actual extra services are not appropriate component declarations',
  'provider_calls':0,'external_actions':0,'clinical_validation':False}
 save('http-verification',report);print(json.dumps(report,indent=2))
if __name__=='__main__':main()
