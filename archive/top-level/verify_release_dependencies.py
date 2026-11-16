"""Requalify current saved-question dependencies using a consistent private copy.

Only received financial fields/rows in the disposable copy are changed. This
does not replay clinical interpretation, modify the source warehouse or fit models.
"""
import json,sqlite3,subprocess,tempfile,shutil,time
from pathlib import Path
from types import SimpleNamespace
from clinical_core.contracts import digest
from clinical_core.agere.clinical import graph_decision as G,question_decision as Q,decision_acquisition as D,financial_accounts as F
ROOT=Path(__file__).resolve().parent;SOURCE=ROOT/'artifacts/agere-healthcare-v123/stack';OUT=ROOT/'artifacts/agere-release-closure-v1'
RECORD='WHF-R-af9d0eb8c9e0461f6251c3';CLONE='AGERE-RELEASE-PRIVATE-CONFLICT'

def main():
 s=json.loads((ROOT/'artifacts/agere-graph-accountability-v1/final-summary.json').read_text())
 with tempfile.TemporaryDirectory(prefix='agere-release-dependencies-') as tmp:
  path=Path(tmp)
  for p in SOURCE.iterdir():
   if p.is_file() and p.suffix in ('.json','.sqlite3'):
    if p.suffix=='.sqlite3':subprocess.run(['/bin/cp','-c',str(p),str(path/p.name)],check=True)
    else:shutil.copy2(p,path/p.name)
  print('consistent_private_copy_ready',flush=True)
  retriever=SimpleNamespace(path=path,meta=json.loads((path/'run.json').read_text()));stack=SimpleNamespace(path=path,retriever=retriever,allow_live=False)
  old=Q.load(path,s['decision_id']);member=Q.load(path,s['member_id']);assert G.reopen(retriever,old)['dependencies_current']
  initial=D.reopen(retriever,{'request_id':s['acquisition_id']});assert initial['dependency_unchanged'] and initial['work_binding_unchanged']
  old_states={m['patient_id']:digest(m) for m in old['members']}
  with sqlite3.connect(path/'stack.sqlite3') as db:raw=db.execute('SELECT payload FROM records WHERE id=?',(RECORD,)).fetchone()[0]
  original=json.loads(raw);account_id=original['account_id'];base_finance=next(f['report'] for f in member['findings'] if f['operation']=='financial_accounts')
  results=[]
  for scenario in ('corrected_balance','late_receipt','conflicting_snapshot'):
   start=time.perf_counter()
   with sqlite3.connect(path/'stack.sqlite3') as db:
    if scenario=='conflicting_snapshot':
     change={**original,'record_id':CLONE,'balance_cents':original['balance_cents']+1}
     db.execute('INSERT INTO records VALUES(?,?,?,?)',(CLONE,original['patient_id'],original['source_table'],json.dumps(change)))
    else:
     change={**original,'balance_cents':original['balance_cents']+1} if scenario=='corrected_balance' else {**original,'available_date':'2029-08-15','available_time':'2029-08-15T18:02:00+00:00'}
     db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(change),RECORD))
   stale=G.reopen(retriever,old);assert not stale['dependencies_current']
   refresh=G.refresh(stack,{'decision_id':old['id']});fresh=Q.load(path,refresh['current_decision_id'])
   assert fresh['question']==old['question'] and fresh['graph']==old['graph'] and fresh['population']==old['population']
   assert refresh['changed_member_count']==1 and {c['patient_id'] for c in refresh['changes']}=={original['patient_id']}
   unchanged=[p for p,h in old_states.items() if p!=original['patient_id'] and h==digest(next(m for m in fresh['members'] if m['patient_id']==p))];assert len(unchanged)==1613
   bound=G.evidence(path,fresh,original['patient_id']);finance=next(f['report'] for f in bound['findings'] if f['operation']=='financial_accounts');account=next(a for a in finance['accounts'] if a['account_id']==account_id)
   if scenario=='corrected_balance':
    assert account['snapshot_amounts']['balance_cents']==original['balance_cents']+1
    assert next(e['residual_cents'] for e in account['equations'] if e['id']=='patient_balance')==-1
   elif scenario=='late_receipt':
    assert account['selected_snapshots'][0]['record_id']==RECORD and account['selected_snapshots'][0]['arrival_date']=='2029-08-15'
    cutoff={'available_by':'2029-08-14'};earlier_prep=F.prepare(path,cutoff)
    earlier_result=F.execute(path,[original['patient_id']],cutoff,prepared=earlier_prep)
    earlier=F.inspect(path,F.receipt_id(earlier_prep,original['patient_id']))
    earlier_account=next(a for a in earlier['accounts'] if a['account_id']==account_id)
    assert RECORD not in earlier_account['source_record_ids'] and all(r['record_id']!=RECORD for r in earlier_account['selected_snapshots'])
    assert [r['record_id'] for r in earlier_account['selected_snapshots']]==['WHF-R-4ca44d163d14c852f93092']
    assert earlier_account['snapshot_amounts']['balance_cents']==6375
    assert earlier['ignored_counts']['not_received_by_cutoff']>=1
    (OUT/'late-receipt-earlier-cutoff.json').write_text(json.dumps({'status':'passed','parameters':cutoff,'result':earlier_result,'patient_receipt':earlier},indent=2)+'\n')
   else:
    assert account['status']=='unresolved_snapshot' and account['snapshot_amounts'] is None and account['selected_snapshots']==[]
    assert any(i['reason']=='conflicting_latest_snapshot' for i in account['issues'])
   assert any(g.get('binding')=='patient_attribution' for g in bound['evidence_gaps'])
   current=D.check(retriever,{'request_id':s['acquisition_id']})
   assert current['status']=='graph_evidence_changed_requires_reanswer' and current['work_binding_unchanged']
   assert not current['task_state_changed'] and not current['request_sent'] and not current['clinical_resolution_established']
   assert current['current_dependency']['observation']['original_gap']['binding']=='patient_attribution'
   assert current['current_dependency']['observation']['original_gap']['target_record_id'] is None
   assert G.reopen(retriever,fresh)['dependencies_current']
   result={'scenario':scenario,'status':'passed','original_question':old['question'],'old_decision_id':old['id'],'current_decision_id':fresh['id'],'stale_reason':stale['stale_reason'],'changed_members':1,'unchanged_members':len(unchanged),'unchanged_member_digest':digest(unchanged),'original_scope_retained':True,'selected_account':account,'ignored_counts':finance['ignored_counts'],'source_change':{'before':original,'after':change},'recomputation':refresh,'acquisition_check':current,'seconds':time.perf_counter()-start,'new_paid_api_calls':refresh['new_paid_api_calls']}
   (OUT/(scenario+'.json')).write_text(json.dumps(result,indent=2)+'\n');results.append({k:result[k] for k in ('scenario','status','changed_members','unchanged_members','seconds','new_paid_api_calls')});print(json.dumps(results[-1]),flush=True)
   with sqlite3.connect(path/'stack.sqlite3') as db:
    db.execute('UPDATE records SET payload=? WHERE id=?',(raw,RECORD));db.execute('DELETE FROM records WHERE id=?',(CLONE,))
   assert G.reopen(retriever,old)['dependencies_current']
  report={'status':'passed','scenarios':results,'population':1614,'source':'Consistent disposable APFS copy captured while owned server was stopped','old_question_task_and_acquisition_preserved':True,'unaffected_member_states_identical':True,'received_cutoff_enforced':True,'conflict_retained_as_unresolved':True,'new_paid_api_calls':0,'limitations':'Engineering on declared received-record financial changes; no new clinical interpretation, raw-source ingestion automation, provider attribution, payment entitlement or causal inference.'}
  (OUT/'dependency-verification.json').write_text(json.dumps(report,indent=2)+'\n');print('dependency_verification_passed',flush=True)
if __name__=='__main__':main()
