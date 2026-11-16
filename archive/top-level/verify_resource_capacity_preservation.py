import json,hashlib
from pathlib import Path
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import program_work,resource_capacity as c
from clinical_core.agere.gateway import BudgetLedger
out=Path('artifacts/agere-resource-capacity-v1');prior=json.loads(Path('artifacts/agere-work-capacity-v1/protected-for-continuation.json').read_text())
for name,h in prior['protected_files'].items():assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==h,name
protected={**prior['protected_files'],'clinical_core/agere/clinical/resource_capacity.py':hashlib.sha256(Path(c.__file__).read_bytes()).hexdigest()}
(out/'protected-for-continuation.json').write_text(json.dumps({'schema':'agere-protected-continuation/1','protected_files':protected},indent=2)+'\n')
baseline=json.loads(Path('artifacts/agere-patient-fit-v1/history-baseline.json').read_text());db=program_work.database(stack_path());counts={}
for table in ('work_items','work_events','capability_tasks','capability_events'):
 rows=[list(r) for r in db.execute('SELECT * FROM '+table+' ORDER BY 1,2')];assert rows==baseline[table],table;counts[table]=len(rows)
db.close()
external=[r for r in json.loads(Path('artifacts/agere-multipass-synthesis-v1/research-recovery-index.json').read_text())['files'] if r['path'].startswith('/Users/mikel/Documents/')]
for r in external:assert hashlib.sha256(Path(r['path']).read_bytes()).hexdigest()==r['sha256']
ledger=BudgetLedger('artifacts/agere-full/provider-budget.sqlite3');receipt=ledger.receipt();ledger.db.close()
assert receipt==json.loads(Path('artifacts/agere-work-capacity-v1/ledger-after.json').read_text());(out/'ledger-after.json').write_text(json.dumps(receipt,indent=2)+'\n')
original=json.loads(Path('artifacts/agere-work-capacity-v1/scenario.json').read_text());assert c.parent(stack_path(),original['id'])==original
state=c.history(stack_path(),original['id']);assert len(state['scenarios'])==3 and all(r['current'] for r in state['scenarios'])
(out/'final-history.json').write_text(json.dumps(state,indent=2)+'\n')
result={'status':'passed','prior_protected_count':89,'protected_count':90,'all_program_and_buyer_histories_unchanged':counts,'external_research_files_preserved':len(external),'ledger_unchanged':True,'original_scenario_unchanged':True,'current_saved_scenarios':len(state['scenarios'])}
(out/'preservation.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
