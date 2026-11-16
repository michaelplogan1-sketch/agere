"""Verify pinned prior engines, paid answers and the separate research corpus."""
import hashlib,json
from pathlib import Path
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import source_synthesis
OUT=Path('artifacts/agere-availability-synthesis-v1')
old=json.loads(Path('artifacts/agere-cohort-spans-v1/preservation.json').read_text())
protected=old['verified_unchanged_files'].copy()
receipt=json.loads(Path('artifacts/agere-cohort-spans-v1/implementation-receipt.json').read_text())
for name in ['cohort_span_acquisition.py','source_availability.py','question_span_availability.py']:
 p='clinical_core/agere/clinical/'+name;protected[p]=receipt['implementation_files'][p]
for path,expected in protected.items():
 assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==expected,path
answers=receipt['frozen_answers_current']
for key in answers:source_synthesis.inspect(stack_path(),key)
new=json.loads((OUT/'answer.json').read_text());source_synthesis.inspect(stack_path(),new['id'])
research=json.loads(Path('artifacts/agere-multipass-synthesis-v1/research-recovery-index.json').read_text())['files']
external=[r for r in research if r['path'].startswith('/Users/mikel/Documents/')]
for r in external:assert hashlib.sha256(Path(r['path']).read_bytes()).hexdigest()==r['sha256'],r['path']
result={'verified_unchanged_files':protected,'protected_count':len(protected),'earlier_paid_answers_current':answers,'new_answer_current':new['id'],'external_research_unchanged':[r['path'] for r in external],'external_research_count':len(external),'active_warehouse_changed':False}
(OUT/'preservation.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k not in ['verified_unchanged_files','external_research_unchanged','earlier_paid_answers_current']},indent=2))
