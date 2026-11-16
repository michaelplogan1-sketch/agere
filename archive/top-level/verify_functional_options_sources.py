"""Audit the actual selected context against received runtime records only."""
import json,sqlite3
from pathlib import Path
from clinical_core.agere.active import stack_path
from clinical_core.contracts import digest
out=Path('artifacts/agere-functional-options-v1')
c=json.loads((out/'context.json').read_text());m=c['model_context'];a=json.loads((out/'acquisition.json').read_text())
records={};fields=0;narratives=0
with sqlite3.connect(f'file:{stack_path() / "stack.sqlite3"}?mode=ro',uri=True) as db:
 for key,source in m['sources'].items():
  r=json.loads(db.execute('SELECT payload FROM records WHERE id=?',(key,)).fetchone()[0]);records[key]=r
  assert r['patient_id']==c['patient_id'] and r['synthetic'] is True
  assert digest(r)==source['source']['record_hash']
  for k in ('event_date','recorded_date','available_date','source_table'):
   assert r.get(k)==source['source'].get(k)
 for e in m['evidence']:
  r=records[e['source_ref']]
  if e['kind']=='narrative':
   assert r[e['field']][e['start']:e['end']]==e['text'];narratives+=1
  elif e['kind']=='structured':
   for k,v in e['reported_fields'].items():
    assert k in r and type(v)==type(r[k]) and v==r[k],k;fields+=1
  else:raise AssertionError(e['kind'])
 assert set(records)==set(a['selection']['connected_record_ids'])
 assert len(a['selection']['retrieved_record_ids'])==3 and len(records)==5
 assert all(records[x['from']][x['field']]==x['to'] for x in a['selection']['reference_edges'])
result={'status':'passed','selected_records':3,'connected_records':5,'typed_source_reference_edges':len(a['selection']['reference_edges']),
 'source_identities_and_dates_verified':5,'exact_narrative_spans':narratives,'exact_structured_fields':fields,
 'hidden_truth_used':False,'clinical_meaning_validated':False,'provider_calls':0}
(out/'source-audit.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
