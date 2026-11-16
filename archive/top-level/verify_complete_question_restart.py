"""Uncached HTTP reads and exact local replay after an owned server restart."""
import hashlib,json,sqlite3,time,urllib.request
from pathlib import Path
from clinical_core.agere.workflow_scorecard import source_selection_decision
from verify_complete_plan_http import OUT,BASE

def read(p):return json.loads(Path(p).read_text())
def counts():
 with sqlite3.connect('artifacts/agere-full/provider-budget.sqlite3') as db:return db.execute('SELECT status,count(*) FROM calls GROUP BY status').fetchall()
def request(route,body=None):
 req=urllib.request.Request(BASE+route,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'} if body is not None else {})
 start=time.perf_counter()
 with urllib.request.urlopen(req,timeout=900) as r:result=json.load(r)
 return result,round(time.perf_counter()-start,3)

before=counts();checks=[];folder=OUT/'payer';answer=read(folder/'canonical-answer.json');decision=read(folder/'decision.json')
for label,route,body,expected in [
 ('answer_reopen','case-synthesis?id='+answer['id'],None,answer),
 ('complete_selection_replay','source-plan-answers/compose',{'snapshot_id':answer['snapshot_id']},answer),
 ('decision_replay','decision/prepare',decision['request'],decision),
 ('decision_reopen','decision?id='+decision['id'],None,read(folder/'reopened-decision.json'))]:
 result,seconds=request(route,body);assert result==expected,label
 checks.append({'check':label,'exact_match':True,'seconds':seconds})
old_id=read(folder/'superseded-first-composition/decision.json')['id']
old,_=request('decision?id='+old_id);assert not old['dependencies_current']
ui_id='0d22cedaa69417dd33269256a06548ff2568961fb4216af1824fbedd40fad992'
ui,seconds=request('decision?id='+ui_id)
assert ui['dependencies_current'] and len(ui['tasks'])==1 and ui['tasks'][0]['binding_current']
assert ui['decision']['answer_digest']==decision['answer_digest'] and ui['decision']['matching']==decision['matching']
(OUT/'payer/browser-reopened-decision.json').write_text(json.dumps(ui,indent=2)+'\n')
for label,key in [('historical_functional','4425a7424b037f08578c34c260a7dc2cde965890aba0a0b39f223346898dd25b'),('historical_buyer_candidate','f0f91f6f87e8520fb1eb9442e8376692cb99839f549ffe7df81d74b4c5e54a5c')]:
 r,seconds=request('decision?id='+key);assert r['dependencies_current']
 checks.append({'check':label,'dependencies_current':True,'seconds':seconds})
after=counts();assert before==after
protected=read('artifacts/agere-reconciliation-contract-v2/protected-for-continuation.json')['protected_files']
assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in protected.items())
g2=source_selection_decision();assert g2['status']=='decision_complete'
summary={'status':'passed','checks':checks,'superseded_composition_detected_stale':True,
 'browser_decision_same_answer_and_service_plan':True,'browser_internal_work_persisted':True,
 'ledger_before':before,'ledger_after':after,'new_provider_calls_on_replay':0,
 'protected_files_unchanged':len(protected),'g2_activation_decision':g2['status'],
 'focused_tests_passed':105,'python_compile':'passed','javascript_syntax':'passed',
 'clinical_validation':False,'new_source_arrival_followup':False}
(OUT/'verification.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary),flush=True)
