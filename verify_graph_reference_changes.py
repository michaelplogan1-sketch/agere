"""Actual source and lifecycle changes in a stopped-runtime private copy."""
import json,sqlite3,subprocess,tempfile,shutil,time,uuid
from pathlib import Path
from types import SimpleNamespace
from clinical_core.contracts import digest
from clinical_core.agere.clinical import graph_decision as G,question_decision as Q,decision_acquisition as D,graph_references as R,reference_lifecycle as L,care_readiness
ROOT=Path(__file__).resolve().parent;SOURCE=ROOT/'artifacts/agere-healthcare-v123/stack';OUT=ROOT/'artifacts/agere-graph-references-v1'

def main():
 s=json.loads((OUT/'final-summary.json').read_text());results=[]
 with tempfile.TemporaryDirectory(prefix='agere-reference-changes-') as tmp:
  path=Path(tmp)
  for p in SOURCE.iterdir():
   if p.is_file() and p.suffix in ('.json','.sqlite3'):
    if p.suffix=='.sqlite3':subprocess.run(['/bin/cp','-c',str(p),str(path/p.name)],check=True)
    else:shutil.copy2(p,path/p.name)
  print('consistent_private_copy_ready',flush=True)
  retriever=SimpleNamespace(path=path,meta=json.loads((path/'run.json').read_text()));stack=SimpleNamespace(path=path,retriever=retriever,allow_live=False)
  G.install()
  old=Q.load(path,s['decision_id']);member=Q.load(path,s['member_id']);assert G.reopen(retriever,old)['dependencies_current']
  old_states={(m['patient_id'],r['node_id']):r for m in old['members'] for r in m['states']}
  original_task=care_readiness.task(path,s['task_id'])
  finance=next(f['report'] for f in member['findings'] if f['operation']=='financial_accounts')
  account=next(a for a in finance['accounts'] if len(a['selected_snapshots'])==1 and type(a['snapshot_amounts'].get('balance_cents')) is int)
  rid=account['selected_snapshots'][0]['record_id']
  with sqlite3.connect(path/'stack.sqlite3') as db:raw=db.execute('SELECT payload FROM records WHERE id=?',(rid,)).fetchone()[0]
  original=json.loads(raw)
  for scenario in ('changed_patient_source','withdrawn_reference'):
   start=time.perf_counter()
   if scenario=='changed_patient_source':
    changed={**original,'balance_cents':original['balance_cents']+1}
    with sqlite3.connect(path/'stack.sqlite3') as db:db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(changed),rid))
    change={'record_id':rid,'before':original,'after':changed}
   else:
    change={'reference_id':s['reference_id'],'expected_revision':L.state(path,s['reference_id'])['revision'],'event_kind':'local_withdrawal','event_date':'2026-10-12','actor':'Isolated synthetic lifecycle verifier','actor_kind':'synthetic_test','reason':'Private-copy test of saved graph reference invalidation; not a publisher or clinical claim','notice':None,'question_changes':{}}
    preview=L.preview(path,change);change=L.commit(path,{'change':change,'preview_id':preview['id'],'request_id':str(uuid.uuid4())})
   stale=G.reopen(retriever,old);assert not stale['dependencies_current']
   refresh=G.refresh(stack,{'decision_id':old['id']});fresh=Q.load(path,refresh['current_decision_id'])
   assert fresh['question']==old['question'] and fresh['graph']==old['graph'] and fresh['population']==old['population']
   assert refresh['changed_member_count']==(1 if scenario=='changed_patient_source' else 1614)
   fresh_states={(m['patient_id'],r['node_id']):r for m in fresh['members'] for r in m['states']}
   unchanged=sum(v==fresh_states.get(k) for k,v in old_states.items())
   bound=G.evidence(path,fresh,s['patient_id']);ref=next(f for f in bound['findings'] if f['operation']=='reference_review')
   assert len(ref['report']['questions'])==6 and not ref['report']['clinical_applicability_established']
   if scenario=='changed_patient_source':
    assert {c['patient_id'] for c in refresh['changes']}=={s['patient_id']}
    assert {c['after']['operation'] for c in refresh['changes']}=={'financial_accounts','reference_review'}
    assert len({pid for pid,node in old_states if pid!=s['patient_id'] and old_states[pid,node]==fresh_states[pid,node]})==1613
   else:
    assert {c['after']['operation'] for c in refresh['changes']}=={'reference_review'}
    assert ref['status']=='reference_unavailable_requires_reassessment'
    assert all(v==fresh_states[k] for k,v in old_states.items() if v['operation']!='reference_review')
   check=D.check(retriever,{'request_id':s['acquisition_id']})
   assert check['status']=='graph_evidence_changed_requires_reanswer' and check['work_binding_unchanged']
   assert not check['task_state_changed'] and not check['request_sent'] and not check['clinical_resolution_established']
   assert check['current_dependency']['observation']['original_gap']['reference_id']==s['reference_id']
   assert care_readiness.task(path,s['task_id'])==original_task and Q.load(path,old['id'])==old
   assert G.reopen(retriever,fresh)['dependencies_current']
   row={'status':'passed','scenario':scenario,'changed_members':refresh['changed_member_count'],'unchanged_node_receipts':unchanged,'total_node_receipts':len(old_states),'same_original_question_and_population':True,'change':change,'refresh':refresh,'acquisition_check':check,'old_task_preserved':True,'seconds':time.perf_counter()-start,'new_paid_api_calls':refresh['new_paid_api_calls']}
   (OUT/(scenario+'.json')).write_text(json.dumps(row,indent=2)+'\n');results.append({k:row[k] for k in ('status','scenario','changed_members','unchanged_node_receipts','total_node_receipts','seconds','new_paid_api_calls')});print(json.dumps(results[-1]),flush=True)
   if scenario=='changed_patient_source':
    with sqlite3.connect(path/'stack.sqlite3') as db:db.execute('UPDATE records SET payload=? WHERE id=?',(raw,rid))
    assert G.reopen(retriever,old)['dependencies_current']
 (OUT/'change-verification.json').write_text(json.dumps({'status':'passed','scenarios':results,'source':'Consistent disposable copy; original warehouse, runtime source and reference lifecycle unchanged','old_question_work_acquisition_preserved':True,'clinical_applicability_claimed':False},indent=2)+'\n')
if __name__=='__main__':main()
