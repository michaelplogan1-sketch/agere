"""Read-only checks of frozen engines, existing answers/work and research."""
import hashlib,json
from pathlib import Path
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import source_synthesis,program_work,care_capabilities,functional_review

OUT=Path('artifacts/agere-domain-review-v1')
prior=Path('artifacts/agere-functional-work-v1')
old=json.loads((prior/'preservation.json').read_text())
receipt=json.loads((prior/'implementation-receipt.json').read_text())
protected=old['verified_unchanged_files'].copy()
for name in ('program_work_v1.py','program_work_current.py','functional_programs.py'):
    p='clinical_core/agere/clinical/'+name;protected[p]=receipt['implementation_files'][p]
for p,expected in protected.items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==expected,p
answers=old['earlier_paid_answers_current']+[old['new_answer_current']]
for key in answers:source_synthesis.inspect(stack_path(),key)
research=json.loads(Path('artifacts/agere-multipass-synthesis-v1/research-recovery-index.json').read_text())['files']
external=[r for r in research if r['path'].startswith('/Users/mikel/Documents/')]
for r in external:assert hashlib.sha256(Path(r['path']).read_bytes()).hexdigest()==r['sha256'],r['path']
baseline=json.loads((prior/'legacy-work-baseline.json').read_text())
assert program_work.code_identity()==baseline['code_identity']
for value in baseline['definitions']:assert program_work.definition(value['id'])==value
functional_review.inspect(stack_path(),'37421dee92cdae5cb293423bcdd9cc31bb36ba61f3652898b00b33b5013cf730')
db=program_work.database(stack_path())
try:
    item=json.loads(db.execute('SELECT payload FROM work_items WHERE id=?',(receipt['http']['work_item_id'],)).fetchone()[0])
    assert item==json.loads((prior/'demonstration-work.json').read_text())['item']
    task_ids=['85228500e2b2d835114e7eb80c762b0c7c129763a794a68e659b4aa4ba6a3c7c',receipt['http']['buyer_task_id']]
    for key in task_ids:
        task=care_capabilities.load(db,'capability_tasks',key)
        assert task['state']=='handoff_prepared_for_review'
        care_capabilities.inspect_plan(stack_path(),task['plan_id'])
finally:db.close()
result={'verified_unchanged_files':protected,'protected_count':len(protected),'earlier_paid_answers_current':answers,
    'external_research_unchanged':[r['path'] for r in external],'external_research_count':len(external),
    'legacy_work_identity_preserved':True,'functional_work_item_unchanged':item['id'],'existing_buyer_tasks_current':task_ids,
    'active_warehouse_changed':False}
(OUT/'preservation.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k not in ('verified_unchanged_files','external_research_unchanged')},indent=2))
