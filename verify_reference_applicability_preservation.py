import json,hashlib
from pathlib import Path
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import program_work,reference_applicability as r,resource_capacity
from clinical_core.agere.clinical import answer_retirement,patient_context,literal_reference_runtime,structured_transport
from clinical_core.agere.gateway import BudgetLedger
answer_retirement.install_origin_guard();patient_context.install_plan_guard();literal_reference_runtime.install();structured_transport.install();r.install()
out=Path('artifacts/agere-reference-applicability-v1');prior=json.loads(Path('artifacts/agere-resource-capacity-v1/protected-for-continuation.json').read_text())
for name,h in prior['protected_files'].items():assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==h,name
protected={**prior['protected_files']}
for name in ['clinical_core/agere/clinical/reference_applicability.py','clinical_core/agere/clinical/reference_assertions.json']:protected[name]=hashlib.sha256(Path(name).read_bytes()).hexdigest()
(out/'protected-for-continuation.json').write_text(json.dumps({'schema':'agere-protected-continuation/1','protected_files':protected},indent=2)+'\n')
baseline=json.loads(Path('artifacts/agere-patient-fit-v1/history-baseline.json').read_text());db=program_work.database(stack_path());counts={}
for table in ('work_items','work_events','capability_tasks','capability_events'):
 rows=[list(x) for x in db.execute('SELECT * FROM '+table+' ORDER BY 1,2')];assert rows==baseline[table],table;counts[table]=len(rows)
db.close()
external=[x for x in json.loads(Path('artifacts/agere-multipass-synthesis-v1/research-recovery-index.json').read_text())['files'] if x['path'].startswith('/Users/mikel/Documents/')]
for x in external:assert hashlib.sha256(Path(x['path']).read_bytes()).hexdigest()==x['sha256']
ledger=BudgetLedger('artifacts/agere-full/provider-budget.sqlite3');receipt=ledger.receipt();ledger.db.close()
assert receipt==json.loads(Path('artifacts/agere-resource-capacity-v1/ledger-after.json').read_text());(out/'ledger-after.json').write_text(json.dumps(receipt,indent=2)+'\n')
original=json.loads(Path('artifacts/agere-work-capacity-v1/scenario.json').read_text());assert resource_capacity.parent(stack_path(),original['id'])==original
scenarios=resource_capacity.history(stack_path(),original['id']);assert len(scenarios['scenarios'])==3 and all(x['current'] for x in scenarios['scenarios'])
http=json.loads((out/'http-verification.json').read_text());state=r.inspect(stack_path(),http['task_id']);assert len(state['sets'])==1 and state['sets'][0]['current']
assert len(state['sets'][0]['questions'][0]['acquisitions'])==1
assert all(q['applicability']=='unresolved' for q in state['sets'][0]['questions'])
assert len([t for t in r.packet_followup.targets(stack_path(),http['task_id']) if t['target']['kind']==r.KIND])==6
(out/'final-history.json').write_text(json.dumps(state,indent=2)+'\n')
report={'status':'passed','prior_protected_count':90,'protected_count':len(protected),'all_program_and_buyer_histories_unchanged':counts,'external_research_files_preserved':len(external),'ledger_unchanged':True,'original_capacity_scenario_unchanged':True,'current_resource_scenarios':3,'current_reference_question_sets':1,'unresolved_questions':6,'prepared_contexts':1,'packet_followup_targets':6}
(out/'preservation.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
