"""Local synthetic G3 workflow; no inference or external care dispatch."""
import json
from pathlib import Path
import urllib.request
from urllib.error import HTTPError

OUT=Path('artifacts/agere-question-decision-v1');BASE='http://localhost:8885/api/stack/'
ANSWER='ad7aac92bb86800b8d56f793afd6fbac6e3a074216a63857b65057cbfb44476c'

def call(name,route,body=None):
    req=urllib.request.Request(BASE+route,data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type':'application/json'} if body is not None else {})
    with urllib.request.urlopen(req,timeout=180) as response: result=json.load(response)
    (OUT/(name+'.json')).write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    return result


def main():
    if (OUT/'final.json').exists(): raise ValueError('Completed workflow receipts already exist; do not overwrite sealed history')
    OUT.mkdir(exist_ok=True)
    source=call('source','decision/source?answer_id='+ANSWER)
    assert source['binding']['question']=='What received records describe mobility, daily activities, falls or caregiver help?'
    assert {r['need_tag'] for r in source['planning']['review_topics']}=={'functional_recovery','caregiver_support'}
    buyer='g3-connected-functional-development'
    portfolio=call('portfolio-before','capabilities?buyer='+buyer)
    capability={'id':'functional-review-team','name':'Synthetic home function and support review',
        'description':'Development buyer declaration for internal evidence review; no real service deployment.',
        'need_tags':['functional_recovery','caregiver_support'],'modality':'home','regions':[],
        'payer_types':['original_medicare','medicare_advantage','commercial','medicaid','self_pay','other'],
        'plan_ids':[],'pathway_ids':[],'owner':{'kind':'team','id':'synthetic-function-review-team'},
        'available':True,'capacity_slots':4,'capacity_as_of':'2029-08-01','capacity_until':'2029-08-31',
        'effective_start':'2029-01-01','effective_end':'2029-12-31',
        'prerequisites':['Confirm service indication, patient goals, consent and funding before any referral'],
        'provenance':'Synthetic G3 development buyer configuration, not verified service availability'}
    call('portfolio','capabilities/save',{'buyer_id':buyer,'expected_revision':portfolio['revision'],
         'actor':'G3 synthetic workflow verification','capabilities':[capability]})
    req={'answer_id':ANSWER,'buyer_id':buyer,'coverage':{'payer_type':'unknown','plan_id':'','region':'',
         'as_of':'2029-08-15','provenance':'No coverage report selected for this development decision','preferences':''}}
    decision=call('decision','decision/prepare',req)
    assert decision==call('decision-replay','decision/prepare',req)
    assert decision['planning']['unmapped_finding_ids']==['r1_f2']
    assert decision['matching']['patient_context']
    assert decision['matching']['participation_contexts']
    option=decision['matching']['options'][0]
    assert option['status']=='requires_verification' and not option['clinical_execution_authorized']
    assert all(not r['coverage_established'] for r in option['routes'])
    queue={'decision_id':decision['id'],'option_id':option['id'],'actor':'G3 synthetic workflow verification'}
    first=call('queued','decision/queue',queue);again=call('queue-replay','decision/queue',queue)
    assert first['task']==again['task'] and again['idempotent_replay']
    task=first['task']
    event={'decision_id':decision['id'],'task_id':task['id'],'expected_revision':task['revision'],
        'request_id':'g3-functional-'+decision['id'],'action':'prepare_handoff','actor':'G3 synthetic workflow verification',
        'note':'Prepare the internal review packet, with exact question, answer and source versions; no external dispatch.'}
    result=call('handoff','decision/event',event)
    assert not result['task']['handoff']['external_dispatch_performed']
    assert not result['task']['handoff']['human_review_completed']
    reopened=call('reopened','decision?id='+decision['id'])
    assert reopened['dependencies_current'] and len(reopened['tasks'])==1
    assert reopened['tasks'][0]['binding_current'] and reopened['tasks'][0]['task']==result['task']
    follow=call('followup','decision/followup',{'decision_id':decision['id'],'task_id':task['id'],'answer_id':ANSWER})
    assert follow['status']=='no_new_evidence' and not follow['changes'] and follow['unresolved']
    assert not follow['care_outcome_established']
    final=call('final','decision?id='+decision['id'])
    assert final['followups'][-1]==follow
    print(json.dumps({'status':'passed','decision_id':decision['id'],'task_id':task['id'],
        'finding_derived_topics':2,'unmapped_findings':1,'work_reopened':True,'followup':follow['status'],
        'provider_calls':0,'clinical_validated':False,'url':'http://localhost:8885/stack?decision='+decision['id']}))

if __name__=='__main__':main()
