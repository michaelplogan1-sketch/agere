"""Real received scope with deliberately unresolved fixture model dispositions."""
import json
from pathlib import Path
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import structured_transport as t,case_passes as p,synthesis_availability as a,literal_reference_runtime,stable_reconciliation as stable,availability_reconciliation as rec
literal_reference_runtime.install();root=Path('artifacts/agere-structured-transport-v1');original=json.loads(Path('artifacts/agere-reference-scope-v1/context.json').read_text());row=t.transform(original);received=a.snapshot(stack_path(),row);b,links=a.plan(row,p.dependencies(stack_path(),row),received)
checks=[]
for length in [24,150]:
 stage=[]
 for page in b['passes']:
  body,units=a.pass_request(page,links,row,received)
  result={'findings':[],'unanswered':['Engineering fixture, no interpreted meaning.'],'review_steps':[],'unit_reviews':[{'unit':name,'status':'unresolved','reason':'x'*length} for name in units]}
  raw={'model':body['model'],'stop_reason':'end_turn','content':[{'type':'text','text':json.dumps(result)}]}
  stage.append({'pass_number':page['pass_number'],'result':p.parse_pass(raw,body,units,row)})
 refs=stable.manifest({'passes':stage},row)
 try:
  jobs=rec.jobs(row,stage,links,refs,received);checks.append({'fixture_reason_length':length,'groups':len(jobs),'request_bytes':[p.request_bytes(j['body']) for j in jobs],'status':'fully_planned'})
 except ValueError as e:checks.append({'fixture_reason_length':length,'status':'explicitly_blocked','reason':str(e)})
result={'scope':'No model output: exact real source units with deliberately unresolved fake dispositions for request-bound testing only','source_passes':len(stage),'unit_manifest':len(refs),'checks':checks,'provider_calls':0}
assert all(r['status']=='fully_planned' for r in checks),checks
(root/'actual-context-reconciliation-fixture.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
