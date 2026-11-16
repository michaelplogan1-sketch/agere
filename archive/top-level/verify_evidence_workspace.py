"""Received-source integration audit; no hidden labels or hosted model calls."""
import json
from pathlib import Path
import sqlite3
from clinical_core.contracts import digest
from clinical_core.agere.clinical import evidence_threads as threads, semantic_reader
from clinical_core.agere.narrative_sources import text_fields

root=Path(__file__).resolve().parent
config=json.loads((root/'config/agere/active_warehouse.json').read_text());path=Path(config['build'])/'stack'
out=root/'artifacts/agere-evidence-workspace-v1';out.mkdir(exist_ok=True)
atlas=threads.atlas(path);(out/'source-atlas.json').write_text(json.dumps(atlas,indent=2))
db=sqlite3.connect(path/'stack.sqlite3')
checks={'source_categories_opened':0,'displayed_nodes_verified':0,'links_verified':0,'paragraphs_exact':0,'reader_targets_verified':0}
examples=[]
for category in atlas['sources']:
 r=threads.records(path,category['stream'])['records'][0]
 x=threads.inspect(path,r['patient_id'],r['record_id']);checks['source_categories_opened']+=1
 raw={n['record_id']:json.loads(db.execute('SELECT payload FROM records WHERE id=?',(n['record_id'],)).fetchone()[0]) for n in x['nodes']}
 for node in x['nodes']:
  source=raw[node['record_id']];assert source['patient_id']==x['patient_id'];assert node['record_hash']==digest(source);checks['displayed_nodes_verified']+=1
  if node['root']:
   parts={p['field']:p['text'] for p in text_fields(source)}
   for p in node['narrative']['paragraphs']:
    assert parts[p['field']][p['start']:p['end']]==p['text'];checks['paragraphs_exact']+=1
    if 0<len(p['text'])<=150 and len(p['field'])<=150:
     target={'patient_id':x['patient_id'],'record_id':node['record_id'],'field':p['field'],'start':p['start'],'end':p['end'],'concept':p['text']}
     semantic_reader.source(path,target);checks['reader_targets_verified']+=1
 for e in x['edges']:
  value=raw[e['from']][e['field'].split('[')[0]];assert e['to'] in (value if isinstance(value,list) else [value]);assert raw[e['to']]['patient_id']==raw[e['from']]['patient_id'];checks['links_verified']+=1
 assert x['displayed_records']+x['omitted_records']==x['component_records']
 if category['stream']=='pharmacy_renewal_requests':(out/'renewal-thread.json').write_text(json.dumps(x,indent=2))
 examples.append({'stream':category['stream'],'patient':r['patient_id'],'root':r['record_id'],'displayed':x['displayed_records'],'component':x['component_records'],'unresolved_references':len(x['unresolved_references'])})
db.close()
result={'status':'passed','synthetic':True,'checks':checks,'snapshot':config['snapshot'],'examples':examples,'new_paid_api_calls':0,'scope':'One received root per category; structural/exact-source integration, not clinical accuracy or complete semantic coverage.'}
(out/'source-verification.json').write_text(json.dumps(result,indent=2));print(json.dumps(checks));print('passed')
