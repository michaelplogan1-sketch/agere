"""Independent received-source joins for the additive reference contracts."""
import json,collections
from pathlib import Path
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import evidence_threads,received_reference_graph as graph
from clinical_core.contracts import digest
OUT=Path('artifacts/agere-reference-scope-v1')
db,meta=evidence_threads.connect(stack_path());counts=collections.Counter();issues=[];patients=set();records=0;occurrences=0
try:
 for stream,fields in graph.EXTRA.items():
  for (payload,) in db.execute('SELECT payload FROM records WHERE stream=? ORDER BY id',(stream,)):
   r=json.loads(payload);records+=1;patients.add(r['patient_id'])
   for field,allowed in fields.items():
    if field not in r.get('_warehouse_fields',[]):continue
    value=r.get(field)
    if value is None:continue
    values=value if isinstance(value,list) else [value]
    for key in values:
     occurrences+=1
     target=db.execute('SELECT payload FROM records WHERE id=?',(key,)).fetchone() if isinstance(key,str) else None
     target=json.loads(target[0]) if target else None
     reason=('missing' if not target else 'wrong_patient' if target['patient_id']!=r['patient_id'] else 'wrong_type' if target['source_table'] not in allowed else 'not_received' if not target.get('available_date') or target['available_date']>meta['snapshot']['as_of'] else 'self_reference' if key==r['record_id'] else 'exact_received_reference')
     counts[(stream,field,target['source_table'] if target else '',reason)]+=1
     if reason!='exact_received_reference':issues.append({'source_id':r['record_id'],'target_id':key,'field':field,'reason':reason})
finally:db.close()
result={'source_digest':meta['source_digest'],'snapshot':meta['snapshot'],'received_records_audited':records,'patients':len(patients),'reference_occurrences':occurrences,
 'counts':[dict(zip(('source_table','field','target_source_table','status'),key),count=n) for key,n in sorted(counts.items())],'issues':issues,
 'provenance':'Read-only original received stack.sqlite3 exact joins; no generator, evaluation truth or clinical relationship labels','clinical_validation':False}
assert all(i['reason']=='self_reference' for i in issues),issues
(OUT/'received-reference-audit.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k not in ('counts','issues')},indent=2));print('self_references',len(issues))
