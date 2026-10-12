"""Changed received financial evidence in a disposable copy; no source mutation."""
import json,sqlite3,subprocess,tempfile,shutil,time
from pathlib import Path
from types import SimpleNamespace
from clinical_core.contracts import digest
from clinical_core.agere.clinical import graph_decision as G,question_decision as Q
ROOT=Path(__file__).resolve().parent;SOURCE=ROOT/'artifacts/agere-healthcare-v123/stack';OUT=ROOT/'artifacts/agere-graph-accountability-v1'

def main():
 summary=json.loads((OUT/'final-summary.json').read_text());old_id=summary['decision_id'];pid=summary['patient_id']
 with tempfile.TemporaryDirectory(prefix='agere-accountability-change-') as tmp:
  path=Path(tmp)
  for p in SOURCE.iterdir():
   if p.is_file() and p.suffix in ('.json','.sqlite3'):
    if p.suffix=='.sqlite3':subprocess.run(['/bin/cp','-c',str(p),str(path/p.name)],check=True)
    else:shutil.copy2(p,path/p.name)
  print('isolated_copy_ready',flush=True)
  retriever=SimpleNamespace(path=path,meta=json.loads((path/'run.json').read_text()));stack=SimpleNamespace(path=path,retriever=retriever,allow_live=False)
  old=Q.load(path,old_id);assert G.reopen(retriever,old)['dependencies_current']
  member=Q.load(path,summary['member_id']);finance=next(f for f in member['findings'] if f['operation']=='financial_accounts')['report']
  account=next(a for a in finance['accounts'] if len(a['selected_snapshots'])==1 and type(a['snapshot_amounts'].get('balance_cents')) is int)
  rid=account['selected_snapshots'][0]['record_id'];db=sqlite3.connect(path/'stack.sqlite3');raw=db.execute('SELECT payload FROM records WHERE id=?',(rid,)).fetchone()[0];before=json.loads(raw);after={**before,'balance_cents':before['balance_cents']+1}
  with db:db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(after),rid))
  db.close();assert not G.reopen(retriever,old)['dependencies_current']
  start=time.perf_counter();result=G.refresh(stack,{'decision_id':old_id});seconds=time.perf_counter()-start
  assert result['changed_member_count']==1 and {r['patient_id'] for r in result['changes']}=={pid}
  fresh=Q.load(path,result['current_decision_id']);assert G.reopen(retriever,fresh)['dependencies_current']
  bound=G.open_member(retriever,{'decision_id':fresh['id'],'patient_id':pid})
  current=next(f for f in bound['findings'] if f['operation']=='financial_accounts')['report'];updated=next(a for a in current['accounts'] if a['account_id']==account['account_id'])
  assert updated['snapshot_amounts']['balance_cents']==account['snapshot_amounts']['balance_cents']+1
  eq=lambda a:next(e['residual_cents'] for e in a['equations'] if e['id']=='patient_balance')
  assert eq(updated)==eq(account)-1
  history=Q.reopen(retriever,member['id']);assert any(x['task']['id']==summary['task_id'] for x in history['tasks']) and not history['dependencies_current']
  assert any(g.get('binding')=='patient_attribution' for g in bound['evidence_gaps'])
  report={'status':'passed','source':'Disposable consistent APFS copy; original warehouse/runtime unchanged','patient_id':pid,'record_id':rid,'before_hash':digest(before),'after_hash':digest(after),'before_balance_cents':before['balance_cents'],'after_balance_cents':after['balance_cents'],'before_equation_residual':eq(account),'after_equation_residual':eq(updated),'changed_members':1,'unchanged_members':1613,'recomputation_seconds':seconds,'recomputation':result,'old_task_preserved':True,'attribution_still_unresolved':True,'new_paid_api_calls':result['new_paid_api_calls'],'clinical_or_payment_resolution_claimed':False}
  (OUT/'changed-source-recomputation.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:report[k] for k in ('status','changed_members','unchanged_members','recomputation_seconds')}),flush=True)
if __name__=='__main__':main()
