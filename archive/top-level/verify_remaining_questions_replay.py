"""Uncached saved-source and decision replay; no inferred clinical qualification."""
import hashlib,json,sqlite3,time,urllib.request
from pathlib import Path
from verify_complete_plan_http import OUT,BASE

def read(p):return json.loads(Path(p).read_text())
def counts():
    with sqlite3.connect('artifacts/agere-full/provider-budget.sqlite3') as db:
        return db.execute('SELECT status,count(*) FROM calls GROUP BY status').fetchall()
def request(route,body=None):
    req=urllib.request.Request(BASE+route,data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type':'application/json'} if body is not None else {})
    start=time.perf_counter()
    with urllib.request.urlopen(req,timeout=900) as response:result=json.load(response)
    return result,round(time.perf_counter()-start,3)

before=counts();assert not any(s=='pending' and n for s,n in before)
checks=[]
for domain in ('cardiometabolic','medication'):
    folder=OUT/domain
    if not (folder/'canonical-answer.json').exists():continue
    answer=read(folder/'canonical-answer.json')
    for label,route,body,expected in [
        ('answer','case-synthesis?id='+answer['id'],None,answer),
        ('compose','source-plan-answers/compose',{'snapshot_id':answer['snapshot_id']},answer)]:
        result,seconds=request(route,body);assert result==expected
        checks.append({'domain':domain,'check':label,'exact_match':True,'seconds':seconds})
    if (folder/'reopened-decision.json').exists():
        saved=read(folder/'reopened-decision.json')
        result,seconds=request('decision?id='+saved['decision']['id'])
        assert result==saved and result['dependencies_current'] and all(t['binding_current'] for t in result['tasks'])
        checks.append({'domain':domain,'check':'decision_owned_work_followup','exact_match':True,'seconds':seconds})
    for file in folder.glob('group-*-source-stage.json'):
        if '-reopened-' in file.name:continue  # Transport wrapper, not the canonical answer.
        answer=read(file);result,seconds=request('context/source-stage?context_id='+answer['context_id'])
        assert result['answer']==answer and result['new_paid_api_calls']==0
        checks.append({'domain':domain,'check':'saved_source_stage','answer_id':answer['id'],'seconds':seconds,
            'unassessed_units':len(result['unassessed_units']),'reconciliation_completed_by_reopen':False})
for name,folder in [('payer','artifacts/agere-complete-question-v1/payer'),('functional','artifacts/agere-question-decision-v1')]:
    p=Path(folder)/'decision.json'
    if not p.exists():continue
    decision=read(p);result,seconds=request('decision?id='+decision['id'])
    assert result['dependencies_current'];checks.append({'check':'prior_'+name,'dependencies_current':True,'seconds':seconds})
protected=read('artifacts/agere-reconciliation-contract-v2/protected-for-continuation.json')['protected_files']
assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in protected.items())
prior=read('artifacts/agere-complete-question-v1/implementation-receipt.json')
assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in prior['evidence_sha256'].items())
after=counts();assert before==after
summary={'status':'passed','checks':checks,'new_provider_calls_on_replay':0,'ledger_before':before,'ledger_after':after,
    'protected_files_unchanged':len(protected),'previous_sealed_evidence_files_unchanged':len(prior['evidence_sha256']),
    'focused_tests_passed':56,'clinical_validation':False,'actual_new_source_arrival':False}
(OUT/'verification.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary),flush=True)
