"""Actual local command -> decision -> buyer/payer -> internal work. No model calls."""
import json,sqlite3,time,urllib.request,urllib.error
from pathlib import Path
from clinical_core.contracts import digest

OUT=Path('artifacts/agere-scoped-review-compiler-v1')
BASE='http://localhost:8887/api/stack/'
QUESTION='Prepare respiratory source-review work for patients P-1004, P-1002 excluding patient P-1003'
TIMINGS=[]
def save(name,row):
    p=OUT/(name+'.json');assert not p.exists(),str(p)
    p.write_text(json.dumps(row,indent=2,allow_nan=False)+'\n');return row
def call(route,body=None):
    req=urllib.request.Request(BASE+route,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
    start=time.perf_counter()
    try:
        with urllib.request.urlopen(req,timeout=180) as f:return json.load(f)
    finally:TIMINGS.append({'route':route.split('?')[0],'seconds':round(time.perf_counter()-start,3)})
def ledger():
    with sqlite3.connect('artifacts/agere-full/provider-budget.sqlite3') as db:return digest([list(r) for r in db.execute('SELECT * FROM calls ORDER BY id')])
def reject(body):
    try:call('compose',body)
    except urllib.error.HTTPError as exc:
        row={'status':exc.code,'response':json.load(exc)};assert exc.code==400;return row
    raise AssertionError('Expected abstention')
def main():
    before=ledger()
    baseline=save('baseline-same-command',reject({'question':QUESTION}));assert 'disabled' in baseline['response']['error']
    for key,text in [('time','Review respiratory sources for patient P-1004 since 2029-01-01'),('clinical','Review respiratory sources for patient P-1004 and recommend oxygen')]:
        save('abstention-'+key,reject({'question':text,'planning':'scoped_local_candidate'}))
    row=save('candidate-composition',call('compose',{'question':QUESTION,'planning':'scoped_local_candidate'}))
    assert row['new_paid_api_calls']==0 and row['planner_receipt']['route']=='scoped_local_candidate'
    assert row['results'][0]['result']['counts']=={'evaluated':1614,'matched':2,'not_matched':1612,'unknown':0}
    assert row['planner_receipt']['excluded_patient_ids']==['P-1003']
    seed=row['decision_seed'];req={'question':QUESTION,'buyer_id':'g4-population-development','as_of':seed['as_of'],'compiler_identity':row['planner_receipt']['compiler_identity']}
    decision=save('candidate-decision',call('decision/scoped-prepare',req))
    assert decision['question']==QUESTION and decision['request']=={**seed,'buyer_id':req['buyer_id']}
    assert set(decision['selection']['patient_ids'])=={'P-1004','P-1002'}
    assert decision['counts']['review_candidates']==1 and decision['counts']['need_unresolved_without_nomination']==1
    route=save('candidate-payer-route',call('decision/population-route-member',{'decision_id':decision['id'],'patient_id':'P-1004'}))
    match=save('candidate-match',call('decision/population-match',{'decision_id':decision['id'],'patient_id':'P-1004'}))
    option=next(o for o in match['matching']['options'] if o['capability']['id']=='respiratory-review')
    assert option['status']=='requires_verification' and not option['clinical_execution_authorized']
    task=save('candidate-work',call('decision/population-queue',{'decision_id':decision['id'],'match_id':match['result_id'],'option_id':option['id'],'actor':'Scoped command engineering verification'}))
    assert task['decision_binding']['question']==QUESTION
    assert task['decision_binding']['selection_digest']==digest(decision['selection'])
    view=save('candidate-reopened',call('decision?id='+decision['id']))
    assert view['dependencies_current'] and any(t['binding_current'] for t in view['tasks'])
    compiled=[f for f in view['followups'] if f['schema']=='scoped-command-plan/1'];assert len(compiled)==1
    assert compiled[0]['graph']==row['graph'] and compiled[0]['compiler_receipt']==row['planner_receipt']
    assert compiled[0]['decision_request_digest']==digest(decision['request'])
    trace=save('candidate-lineage',call('decision/lineage',{'decision_id':decision['id']}))
    assert any(t['execution_id']==decision['request_execution_id'] and t['status']=='completed' for t in trace['traces'])
    exclusion=save('exclusion-composition',call('compose',{'question':'Review behavioral sources across all observed patients excluding patients P-1002, P-1004','planning':'scoped_local_candidate'}))
    assert exclusion['results'][0]['result']['counts']=={'evaluated':1614,'matched':1612,'not_matched':2,'unknown':0}
    assert ledger()==before
    save('http-verification',{'status':'passed','decision_id':decision['id'],'work_id':task['task']['id'],'work_idempotent_replay':task.get('idempotent_replay'),
        'same_command_standard_hosted_disabled':'abstains; no paid fallback attempted','candidate_selected_members':2,'review_candidates':1,'need_unresolved':1,
        'full_roster_exclusion':1612,'compiled_question_bound_to_decision_work_and_followup':True,
        'received_payer_reports_retained':True,'benefits_confirmed':False,'new_paid_calls':0,'full_provider_ledger_unchanged':True,
        'ledger_digest':before,'clinical_qualification':False,'default_activation':False,'timings':TIMINGS})
    print(json.dumps({'status':'passed','decision_id':decision['id'],'work_id':task['task']['id'],'ledger_unchanged':True}))
if __name__=='__main__':main()
