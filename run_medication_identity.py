from pathlib import Path
from collections import Counter
import json,sqlite3
from unittest.mock import patch
from clinical_core.agere.clinical.stack_runtime import Stack
from clinical_core.agere.clinical.graph_workbench import medication_examples
from clinical_core.agere.clinical.graph_planner import propose
from clinical_core.agere.clinical.analysis_graph import catalog
from clinical_core.agere.clinical.context_student import REQUEST_MAX_BYTES  # shared provider request bound

def main():
 root=Path('artifacts/agere-population-stack-v3');out=root/'examples/medication-identity-v1';out.mkdir(parents=True,exist_ok=True)
 stack=Stack(root,allow_live=False)
 try:
  example=medication_examples()[0]
  # The exact registered question compiles and runs with hosted inference disabled.
  result=stack.compose(example['question']);replay=stack.compose(example['question'])
  assert result['new_paid_api_calls']==replay['new_paid_api_calls']==0 and replay['cache_hit']
  data=result['results'][-1]['result'];db=sqlite3.connect((root/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
  raw=Counter();arb=set();metoprolol=set()
  for (payload,) in db.execute('SELECT payload FROM records'):
   r=json.loads(payload)
   if isinstance(r.get('drug'),str):raw[r['drug']]+=1
   if r.get('drug_class')=='arb' and isinstance(r.get('drug'),str):arb.add(r['patient_id'])
   if r.get('drug')=='metoprolol succinate' and not r.get('formulation') and not r.get('release_type'):metoprolol.add(r['patient_id'])
  db.close();observed={r['name']:r['occurrences'] for r in data['names'] if r['kind']=='structured_name'}
  assert dict(raw)==observed
  assert len(arb)==data['patient_counts'].get('recorded_raas_arb',0)
  assert len(metoprolol)==data['patient_counts'].get('formulation_unresolved',0)
  size={}
  def measure(body,*a,**kw):size['maximum_ascii_question_request_bytes']=len(json.dumps(body).encode());assert size['maximum_ascii_question_request_bytes']<=REQUEST_MAX_BYTES
  with patch('clinical_core.agere.clinical.graph_planner.send',side_effect=measure):
   propose('x'*1500,root,None,snapshot=stack.retriever.meta['snapshot'],graph_catalog=catalog(root),observed_values=json.loads((root/'cohort-values.json').read_text()))
  report={'synthetic':True,'source_identity':data['source_identity'],'patients_evaluated':data['patients_evaluated'],
   'record_counts':data['record_counts'],'patient_counts':data['patient_counts'],'identity_status_counts':data['identity_status_counts'],
   'independent_structured_name_counts':dict(raw),'terminology_version':data['terminology_version'],
   'new_paid_api_calls':0,'replay_new_paid_api_calls':0,'planner':result['planner_receipt'],**size,'limits':data['limits']}
  (out/'result.json').write_text(json.dumps(result,indent=2));(out/'execution.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
 finally:stack.close()

if __name__=='__main__':main()
