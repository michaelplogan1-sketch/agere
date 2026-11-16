import json,hashlib
from pathlib import Path
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import program_work,reference_applicability as r,resource_capacity,reference_lifecycle as lifecycle,reference_reassessment as reassessment,care_option_comparison as comparison,care_option_context as option_context,option_evidence_registry
from clinical_core.agere.clinical import answer_retirement,patient_context,literal_reference_runtime,structured_transport
from clinical_core.agere.gateway import BudgetLedger
answer_retirement.install_origin_guard();patient_context.install_plan_guard();literal_reference_runtime.install();structured_transport.install();r.install();lifecycle.install();option_evidence_registry.install()
out=Path('artifacts/agere-option-delivery-v1');prior=json.loads(Path('artifacts/agere-behavioral-options-v1/protected-for-continuation.json').read_text())
for name,h in prior['protected_files'].items():assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==h,name
protected={**prior['protected_files']}
for name in ['clinical_core/agere/clinical/care_option_delivery.py','clinical_core/agere/clinical/care_delivery_components.json']:protected[name]=hashlib.sha256(Path(name).read_bytes()).hexdigest()
(out/'protected-for-continuation.json').write_text(json.dumps({'schema':'agere-protected-continuation/1','protected_files':protected},indent=2)+'\n')
baseline=json.loads(Path('artifacts/agere-patient-fit-v1/history-baseline.json').read_text());db=program_work.database(stack_path());counts={}
for table in ('work_items','work_events','capability_tasks','capability_events'):
 rows=[list(x) for x in db.execute('SELECT * FROM '+table+' ORDER BY 1,2')];assert rows==baseline[table],table;counts[table]=len(rows)
db.close()
external=[x for x in json.loads(Path('artifacts/agere-multipass-synthesis-v1/research-recovery-index.json').read_text())['files'] if x['path'].startswith('/Users/mikel/Documents/')]
for x in external:assert hashlib.sha256(Path(x['path']).read_bytes()).hexdigest()==x['sha256']
ledger=BudgetLedger('artifacts/agere-full/provider-budget.sqlite3');receipt=ledger.receipt();ledger.db.close()
assert receipt==json.loads(Path('artifacts/agere-care-options-v1/ledger-after.json').read_text());(out/'ledger-after.json').write_text(json.dumps(receipt,indent=2)+'\n')
original=json.loads(Path('artifacts/agere-work-capacity-v1/scenario.json').read_text());assert resource_capacity.parent(stack_path(),original['id'])==original
scenarios=resource_capacity.history(stack_path(),original['id']);assert len(scenarios['scenarios'])==3 and all(x['current'] for x in scenarios['scenarios'])
http=json.loads(Path('artifacts/agere-reference-reassessment-v1/http-verification.json').read_text());old=r.load(stack_path(),http['old_manifest_id']);state=r.inspect(stack_path(),old['task_id'])
assert len(state['sets'])==2 and [x['current'] for x in state['sets']]==[False,True]
assert state['sets'][0]['manifest']==json.loads(Path('artifacts/agere-reference-applicability-v1/manifest.json').read_text())
assert not state['sets'][0]['questions'][0]['acquisitions'][0]['current']
assert len(state['sets'][1]['questions'][0]['acquisitions'])==1 and state['sets'][1]['questions'][0]['acquisitions'][0]['current']
assert all(not q['acquisitions'] for q in state['sets'][1]['questions'][1:])
recheck=reassessment.listing(stack_path(),{'target':http['target']})['preparations'];assert len(recheck)==1 and recheck[0]['current'] and recheck[0]['exact_context_reused']
assert len([t for t in r.packet_followup.targets(stack_path(),old['task_id']) if t['target']['kind']==r.KIND])==6
rows=comparison.listing(stack_path(),{'manifest_id':http['new_manifest_id']})['comparisons'];assert len(rows)==1 and rows[0]['current']
assert rows[0]['id']==json.loads(Path('artifacts/agere-care-option-context-v1/http-verification.json').read_text())['comparison_id']
attached=option_context.listing(stack_path(),{'comparison_id':rows[0]['id']})['contexts'];assert len(attached)==1 and attached[0]['current']
assert attached[0]['summary']['coverage_reports']==50 and attached[0]['summary']['patient_reports']==4
result={'status':'passed','prior_protected_count':99,'protected_count':len(protected),'all_program_and_buyer_histories_unchanged':counts,'external_research_files_preserved':len(external),'ledger_unchanged':True,'original_capacity_scenario_unchanged':True,'current_resource_scenarios':3,'reference_sets':2,'current_reference_sets':1,'historical_reference_sets':1,'original_context_preserved':True,'automatic_context_or_answer_transfers':0,'explicit_successor_source_rechecks':1,'exact_context_reused':True,'current_packet_followup_targets':6,'current_care_option_comparisons':1,'received_comparison_contexts':1,'attached_coverage_reports':50,'attached_patient_reports':4,'ranked_or_clinically_selected_options':0}
result['prior_respiratory_comparison_and_context_preserved']=True
from clinical_core.agere.clinical import care_option_delivery
behavioral=json.loads(Path('artifacts/agere-behavioral-options-v1/comparison.json').read_text())
current=comparison.listing(stack_path(),{'manifest_id':behavioral['manifest_id']})['comparisons'];assert len(current)==1 and current[0]['current']
assert {k:v for k,v in current[0].items() if k not in ('current','stale_reasons')}==behavioral
contexts=option_context.listing(stack_path(),{'comparison_id':behavioral['id']})['contexts'];assert len(contexts)==1 and contexts[0]['current']
rows=care_option_delivery.listing(stack_path(),{'comparison_id':behavioral['id']})['proposals'];assert len(rows)==1 and rows[0]['current']
result['prior_behavioral_comparison_and_received_context_preserved']=True
result['current_component_reviews']=1
result['all_counts_above_scope']='Respiratory preservation plus the new behavioral comparison/component checks; not global counts of every store'
(out/'preservation.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
