"""Development integration probe: real saved answers, no injected planning labels."""
import json
from pathlib import Path
import urllib.request
from urllib.error import HTTPError

OUT=Path('artifacts/agere-buyer-decision-v1'); BASE='http://localhost:8885/api/stack/'
BUYER='g3-buyer-documentary-development'


def call(name,route,body=None):
    target=OUT/(name+'.json')
    if target.exists():return json.loads(target.read_text())
    req=urllib.request.Request(BASE+route,data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type':'application/json'} if body is not None else {})
    try:
        with urllib.request.urlopen(req,timeout=600) as response:result=json.load(response)
    except HTTPError as e:
        (OUT/(name+'-error.json')).write_bytes(e.read());raise
    target.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    return result


def capabilities():
    services=[
        ('respiratory-review','Respiratory evidence review',['symptom_support'],'Review documented respiratory symptoms, oxygen accounts and pulmonary rehabilitation evidence; preserve negated, historical and uncertain accounts.'),
        ('cardiometabolic-review','Cardiometabolic evidence review',['blood_pressure','diabetes'],'Review documented blood pressure, glycemic measurements and diabetes follow-up, retaining dates, units and uncertainty. No medication titration or clinical interpretation is authorized.'),
        ('benefits-review','Coverage and eligibility evidence review',['care_access'],'Review received enrollment, coverage and eligibility reports, conflicting plan or date accounts and unknown benefits; do not verify entitlement from a source account.'),
        ('navigation-review','Appointment navigation evidence review',['care_navigation'],'Review appointment bookings, status reports and follow-through, distinguishing scheduled, requested, cancelled and completed care.'),
        ('pharmacy-review','Medication evidence review',['medication_review'],'Review medication orders, reported use, reconciliation, fills and dose accounts; do not assume an order or dispensing establishes current use.'),
        ('function-review','Function and support evidence review',['functional_recovery','caregiver_support'],'Review mobility, daily activities, falls and caregiver help in received accounts; preserve negative and uncertain evidence.')]
    caps=[]
    for key,name,tags,description in services:
        caps.append({'id':key,'name':'Synthetic '+name,'description':description,'need_tags':tags,
            'modality':'virtual','regions':[],'payer_types':['original_medicare','medicare_advantage','commercial','medicaid','self_pay','other'],
            'plan_ids':[],'pathway_ids':[],'owner':{'kind':'team','id':'synthetic-'+key+'-team'},
            'available':True,'capacity_slots':8,'capacity_as_of':'2029-08-01','capacity_until':'2029-08-31',
            'effective_start':'2029-01-01','effective_end':'2029-12-31',
            'prerequisites':['Verify indication, goals, consent, benefits and participation before referral or care'],
            'provenance':'Synthetic development buyer declaration; not an actual service commitment'})
    return caps


def main():
    OUT.mkdir(exist_ok=True)
    old=call('old-functional','decision?id=4425a7424b037f08578c34c260a7dc2cde965890aba0a0b39f223346898dd25b')
    assert old['dependencies_current'] and old['tasks'][0]['binding_current']
    portfolio=call('portfolio-before','capabilities?buyer='+BUYER)
    caps=capabilities()
    call('portfolio','capabilities/save',{'buyer_id':BUYER,'expected_revision':portfolio['revision'],'actor':'G3 development integration','capabilities':caps})
    summary=[]
    for domain in ['respiratory','payer','cardiometabolic','appointment']:
        probe=json.loads((Path('artifacts/agere-question-decision-v1')/(domain+'-source-probe.json')).read_text())
        answer=probe['binding']['answer_id']
        req={'answer_id':answer,'buyer_id':BUYER,'coverage':{'payer_type':'unknown','plan_id':'','region':'','as_of':'2029-08-15',
            'provenance':'No coverage report selected; exact current benefits remain unknown','preferences':''}}
        baseline=call(domain+'-baseline','decision/prepare',req)
        decision=call(domain+'-decision','decision/prepare',{**req,'planner':'buyer_documentary_candidate'})
        assert decision['question']==probe['binding']['question']
        assert decision['baseline_comparison']['baseline']==baseline['planning']
        assert decision['planning']['unresolved']==baseline['planning']['unresolved']
        assert not decision['clinical_execution_authorized']
        allowed=decision['service_selection']['candidate_capability_ids']
        options=[o for o in decision['matching']['options'] if o['capability']['id'] in allowed] if decision['matching'] else []
        for n,option in enumerate(options):
            assert option['status']=='requires_verification'
            assert all(not r['coverage_established'] for r in option['routes'])
            task=call(domain+f'-queue-{n}','decision/queue',{'decision_id':decision['id'],'option_id':option['id'],'actor':'G3 development integration'})['task']
            handoff=call(domain+f'-handoff-{n}','decision/event',{'decision_id':decision['id'],'task_id':task['id'],
                'expected_revision':task['revision'],'request_id':'g3-buyer-'+domain+'-'+option['id'],
                'action':'prepare_handoff','actor':'G3 development integration','note':'Internal evidence review only; no external dispatch or clinical approval.'})
            assert not handoff['task']['handoff']['human_review_completed']
            assert not handoff['task']['handoff']['external_dispatch_performed']
            follow=call(domain+f'-followup-{n}','decision/followup',{'decision_id':decision['id'],'task_id':task['id'],'answer_id':answer})
            assert follow['status']=='no_new_evidence' and not follow['care_outcome_established']
        final=call(domain+'-final','decision?id='+decision['id'])
        assert final['dependencies_current'] and all(t['binding_current'] for t in final['tasks'])
        comparison=decision['baseline_comparison']
        summary.append({'domain':domain,'decision_id':decision['id'],'baseline_mapped':comparison['baseline_mapped_findings'],
            'candidate_mapped':comparison['candidate_mapped_findings'],'unmapped':decision['planning']['unmapped_finding_ids'],
            'work_items':len(final['tasks']),'followups':len(final['followups']),
            'planner_requests':len(decision['planning']['receipts']),'clinical_qualified':False})
        print(json.dumps(summary[-1]),flush=True)
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')


if __name__=='__main__':main()
