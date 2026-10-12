"""Read-only identity checks after registering domain programs."""
import hashlib,json
from pathlib import Path
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import source_synthesis,program_work,care_capabilities,domain_review,answer_retirement
answer_retirement.install_origin_guard()
from clinical_core.agere.clinical import patient_context
patient_context.install_plan_guard()
from clinical_core.agere.clinical import literal_reference_runtime
literal_reference_runtime.install()

OUT=Path('artifacts/agere-patient-context-v1')
protected=json.loads(Path('artifacts/agere-answer-retirement-v1/protected-for-continuation.json').read_text())['protected_files']
for p,sha in protected.items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==sha,p
old=json.loads(Path('artifacts/agere-domain-review-v1/preservation.json').read_text())
domain=json.loads(Path('artifacts/agere-domain-review-v1/http-verification.json').read_text())
answers=old['earlier_paid_answers_current']+[r['answer_id'] for r in domain]
for key in answers:source_synthesis.inspect(stack_path(),key)
for row in domain:domain_review.inspect(stack_path(),row['review_id'])
baseline=json.loads(Path('artifacts/agere-functional-work-v1/legacy-work-baseline.json').read_text())
assert program_work.code_identity()==baseline['code_identity']
for value in baseline['definitions']:assert program_work.definition(value['id'])==value
db=program_work.database(stack_path())
tasks=old['existing_buyer_tasks_current']+[r['buyer_task_id'] for r in domain]
new=json.loads(Path('artifacts/agere-domain-programs-v1/http-verification.json').read_text())
tasks += [r['buyer_task_id'] for r in new]
try:
    for key in tasks:
        task=care_capabilities.load(db,'capability_tasks',key)
        assert task['state']=='handoff_prepared_for_review'
        care_capabilities.inspect_plan(stack_path(),task['plan_id'])
finally:db.close()
research=json.loads(Path('artifacts/agere-multipass-synthesis-v1/research-recovery-index.json').read_text())['files']
external=[r for r in research if r['path'].startswith('/Users/mikel/Documents/')]
for r in external:assert hashlib.sha256(Path(r['path']).read_bytes()).hexdigest()==r['sha256'],r['path']
result={'verified_unchanged_files':protected,'protected_count':len(protected),'earlier_paid_answers_current':answers,
    'existing_buyer_tasks_current':tasks,'external_research_count':len(external),'external_research_unchanged':[r['path'] for r in external],
    'legacy_work_identity_preserved':True,'domain_review_receipts_current':[r['review_id'] for r in domain],'active_warehouse_changed':False}
(OUT/'preservation.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k not in ('verified_unchanged_files','external_research_unchanged')},indent=2))
