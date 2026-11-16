"""Private APFS-copy source-change checks; original warehouse/stores untouched."""
import json,sqlite3,subprocess,tempfile,shutil,time
from pathlib import Path
from types import SimpleNamespace
from clinical_core.agere.clinical import question_decision as Q, decision_acquisition as A, graph_decision as G
ROOT=Path(__file__).resolve().parent;SOURCE=ROOT/'artifacts/agere-healthcare-v123/stack';OUT=ROOT/'artifacts/agere-graph-decision-v1'

def main():
 summary=json.loads((OUT/'final-workflow-summary.json').read_text());results=[]
 with tempfile.TemporaryDirectory(prefix='agere-graph-change-') as tmp:
  path=Path(tmp)
  for p in SOURCE.iterdir():
   if p.is_file() and (p.suffix in ('.json','.sqlite3') or p.name=='operators.json'):
    if p.suffix=='.sqlite3':subprocess.run(['/bin/cp','-c',str(p),str(path/p.name)],check=True)
    else:shutil.copy2(p,path/p.name)
  retriever=SimpleNamespace(path=path);G.install()
  p=summary['patient'];pop=summary['population']
  assert Q.reopen(retriever,p['decision_id'])['dependencies_current']
  assert Q.reopen(retriever,p['member_id'])['dependencies_current']
  db=sqlite3.connect(path/'stack.sqlite3');rid='WHL-R-20eea00d8ffd88969e6542';original=db.execute('SELECT payload FROM records WHERE id=?',(rid,)).fetchone()[0]
  changed=json.loads(original);changed['status']='synthetic_dependency_probe'
  with db:db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(changed),rid))
  parent=Q.reopen(retriever,p['decision_id']);member=Q.reopen(retriever,p['member_id']);check=A.check(retriever,{'request_id':p['acquisition_id']})
  assert not parent['dependencies_current'] and not member['dependencies_current']
  assert check['status']=='graph_evidence_changed_requires_reanswer' and check['changes'] and not check['task_state_changed']
  results.append({'case':'changed_request_source','parent_current':False,'member_current':False,'acquisition_status':check['status'],'old_task_preserved':True})
  with db:db.execute('UPDATE records SET payload=? WHERE id=?',(original,rid))
  assert Q.reopen(retriever,p['decision_id'])['dependencies_current']
  other=db.execute("SELECT id,payload FROM records WHERE stream='referrals' AND patient!='P-1001' ORDER BY id LIMIT 1").fetchone();r=json.loads(other[1]);r['status']='synthetic_outside_member_probe'
  with db:db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(r),other[0]))
  assert Q.reopen(retriever,p['decision_id'])['dependencies_current']
  assert not Q.reopen(retriever,pop['decision_id'])['dependencies_current']
  results.append({'case':'different_patient_same_stream','individual_current':True,'population_current':False,'affected_scope_only':True})
  with db:db.execute('UPDATE records SET payload=? WHERE id=?',(other[1],other[0]))
  assert Q.reopen(retriever,pop['decision_id'])['dependencies_current']
  db.close()
 (OUT/'invalidation.json').write_text(json.dumps({'status':'passed','source':'Disposable APFS copy of synthetic runtime stores; no original source or work history modified','checks':results,'restoration_exact':True,'new_paid_api_calls':0},indent=2)+'\n')
 print(json.dumps({'status':'passed','checks':results}),flush=True)
if __name__=='__main__':main()
