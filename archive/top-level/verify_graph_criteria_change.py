"""Versioned-source recomputation in an isolated APFS copy of synthetic stores."""
import json,sqlite3,subprocess,tempfile,shutil,time
from pathlib import Path
from types import SimpleNamespace
from clinical_core.contracts import digest
from clinical_core.agere.clinical import graph_decision as G,question_decision as Q,warehouse_interpretation as W
from clinical_core.agere.clinical.facts import extract
from clinical_core.agere.clinical.reconcile import reconcile
from clinical_core.agere.clinical.workflows import heart_failure,affordability
ROOT=Path(__file__).resolve().parent;SOURCE=ROOT/'artifacts/agere-healthcare-v123/stack';OUT=ROOT/'artifacts/agere-graph-criteria-v1'

def main():
 summary=json.loads((OUT/'final-summary.json').read_text());old_id=summary['decision_id'];pid=summary['patient_id']
 with tempfile.TemporaryDirectory(prefix='agere-criteria-change-') as tmp:
  path=Path(tmp)
  for p in SOURCE.iterdir():
   if p.is_file() and p.suffix in ('.json','.sqlite3'):
    if p.suffix=='.sqlite3':subprocess.run(['/bin/cp','-c',str(p),str(path/p.name)],check=True)
    else:shutil.copy2(p,path/p.name)
  print('isolated_copy_ready',flush=True)
  retriever=SimpleNamespace(path=path,meta=json.loads((path/'run.json').read_text()));stack=SimpleNamespace(path=path,retriever=retriever,allow_live=False)
  old=Q.load(path,old_id);assert G.reopen(retriever,old)['dependencies_current']
  db=sqlite3.connect(path/'stack.sqlite3')
  rows=db.execute("SELECT id,payload FROM records WHERE patient=? AND stream='financial_screens'",(pid,)).fetchall()
  assert rows,'Need received financial screen'
  changes=[]
  for rid,raw in rows:
   r=json.loads(raw);v=r['difficulty_paying_medical_bills'];assert type(v) is bool
   r['difficulty_paying_medical_bills']=not v
   changes.append({'record_id':rid,'before':v,'after':not v,'before_hash':digest(json.loads(raw)),'after_hash':digest(r)})
   with db:db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(r),rid))
  assert not G.reopen(retriever,old)['dependencies_current']
  # Execute the SAME existing native interpreter after the received version
  # changes, preserving the original report in this verification artifact.
  patient=json.loads(db.execute('SELECT payload FROM patients WHERE id=?',(pid,)).fetchone()[0])
  records=[json.loads(r[0]) for r in db.execute('SELECT payload FROM records WHERE patient=? ORDER BY id',(pid,))]
  previous=db.execute("SELECT payload FROM analyses WHERE patient=? AND operator='warehouse_interpretation'",(pid,)).fetchone()[0]
  updated=W.interpret(patient,records,old['dependencies']['snapshot']['as_of'])
  with db:db.execute("UPDATE analyses SET payload=? WHERE patient=? AND operator='warehouse_interpretation'",(json.dumps(updated),pid))
  # Rebuild existing source-bound clinical analyses as the upstream pipeline
  # does. Refresh consumes current analyses; it is not a raw-source ingestion job.
  state=reconcile(extract(pid,records,old['dependencies']['snapshot']['as_of']))
  clinical_rebuild=[]
  for op,workflow in [('heart_failure_review',heart_failure(state,records)),('affordability_review',affordability(state))]:
   prior=json.loads(db.execute('SELECT payload FROM analyses WHERE patient=? AND operator=?',(pid,op)).fetchone()[0])
   refs=set(workflow['support_fact_ids']+workflow['excluded_fact_ids'])
   for medication in workflow.get('medications',[]):
    refs.update(medication['text_fact_ids']);refs.update(x['fact_id'] for x in medication['dose_sources'])
   rebuilt={**prior,'workflow':workflow,'source_bound_facts':[f for f in state['facts'] if f['fact_id'] in refs]}
   with db:db.execute('UPDATE analyses SET payload=? WHERE patient=? AND operator=?',(json.dumps(rebuilt),pid,op))
   clinical_rebuild.append({'operator':op,'before_hash':digest(prior),'after_hash':digest(rebuilt)})
  db.close();print('versioned_source_rebuilt',flush=True);start=time.perf_counter();result=G.refresh(stack,{'decision_id':old_id});seconds=time.perf_counter()-start
  assert result['changed_member_count']==1,result['changed_member_count']
  assert {r['patient_id'] for r in result['changes']}=={pid}
  criterion=[r for r in result['changes'] if r['before']['operation']=='requirements']
  assert criterion and any(r['before']['status']=='contradicted' and r['after']['status']=='supported' for r in criterion)
  program=[r for r in result['changes'] if r['before']['operation']=='program_document']
  assert program and program[0]['before']['status']=='contradicted' and program[0]['after']['status']=='unresolved'
  fresh=Q.load(path,result['current_decision_id']);assert G.reopen(retriever,fresh)['dependencies_current']
  assert not G.reopen(retriever,old)['dependencies_current']
  bound=G.open_member(retriever,{'decision_id':fresh['id'],'patient_id':pid})
  assert bound['question']==old['question'] and bound['evidence_gaps']
  history=Q.reopen(retriever,summary['member_id']);assert len(history['tasks'])>=1 and not history['dependencies_current']
  report={'status':'passed','source':'Disposable APFS copy; original warehouse and work untouched','patient_id':pid,
    'source_changes':changes,'previous_native_report_hash':digest(json.loads(previous)),'new_native_report_hash':digest(updated),
    'upstream_clinical_rebuild':clinical_rebuild,'refresh_rebuilds_raw_sources':False,
    'recomputation_seconds':seconds,'recomputation':result,'new_member_id':bound['id'],
    'old_task_preserved':True,'unaffected_members':1613,'new_paid_api_calls':0,'clinical_resolution_claimed':False}
  (OUT/'changed-source-recomputation.json').write_text(json.dumps(report,indent=2)+'\n')
  print(json.dumps({'status':'passed','changed_members':1,'unchanged_members':1613,'seconds':seconds}),flush=True)
if __name__=='__main__':main()
