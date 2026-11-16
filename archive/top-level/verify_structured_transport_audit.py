"""Received-source roundtrip audit, separate from clinical interpretation quality."""
import json,collections
from pathlib import Path
from clinical_core.agere.active import stack_path
from clinical_core.contracts import digest
from clinical_core.agere.clinical import structured_transport as t,received_reference_graph as graph,evidence_threads,context_builder as c
OUT=Path('artifacts/agere-structured-transport-v1');db,meta=evidence_threads.connect(stack_path());counts=collections.Counter();streams={};failures=[]
try:
 for stream in graph.EXTRA:
  stats=collections.Counter()
  for (payload,) in db.execute('SELECT payload FROM records WHERE stream=? ORDER BY id',(stream,)):
   r=json.loads(payload);stats['received_records']+=1
   params=c.validate({'question':'Engineering audit of exact received structured fields','streams':[stream],'concepts':[],'contract':'','program_ids':[],'max_bytes':60000})
   units=c.units(r['patient_id'],[r],[],params,meta['snapshot']['as_of']);parent=c.pack(r['patient_id'],params,units,meta)
   if parent['omissions'] or not parent['dispatch']['source_review_allowed']:stats['original_context_incomplete']+=1;continue
   parent.update(parameters=params,record_scope={'record_ids':[r['record_id']]});parent['missing_evidence']['record_scope']={'kind':'explicit_received_records','selected_records':1};parent['id']=digest(parent)
   try:new=t.transform(parent)
   except ValueError as e:stats['explicit_transport_limit']+=1;failures.append({'record_id':r['record_id'],'reason':str(e)});continue
   original={u['id']:u for u in parent['model_context']['evidence']};transported={u['id']:u for u in new['model_context']['evidence']}
   for m in new['structured_transport']['parents']:
    split={'containers':m['containers'],'parts':[{'path':v['path'],'value':transported[f['id']]['reported_fields'][v['json_pointer']]} for f in m['fragments'] for v in f['paths']]}
    assert c.encode(t.restore(split))==c.encode(original[m['parent_unit_id']]['reported_fields'])
   assert [u for u in parent['model_context']['evidence'] if u['kind']=='narrative']==[u for u in new['model_context']['evidence'] if u['kind']=='narrative']
   stats['roundtrip_verified']+=1;stats['partitioned_parent_units']+=len(new['structured_transport']['parents']);stats['original_units']+=len(original);stats['transport_units']+=len(transported)
  streams[stream]=dict(stats);counts.update(stats)
finally:db.close()
result={'scope':'Eight received diagnostic categories; exact source/tree transport only, no evaluation truth, model output or clinical validity labels','source_digest':meta['source_digest'],'snapshot':meta['snapshot'],'counts':dict(counts),'by_source':streams,'explicit_limits':failures,'provider_calls':0,'clinical_validation':False}
(OUT/'received-roundtrip-audit.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result['counts'],indent=2))
