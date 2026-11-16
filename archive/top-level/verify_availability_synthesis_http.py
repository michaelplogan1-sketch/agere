"""Verify new synthesis through existing needs, assessment and buyer-work routes."""
import copy,json,sqlite3
from pathlib import Path
from urllib.request import Request,urlopen
from clinical_core.agere.active import stack_path
OUT=Path('artifacts/agere-availability-synthesis-v1')
BASE='http://localhost:8885'
def api(route,body=None):
    req=Request(BASE+route,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
    with urlopen(req,timeout=180) as r:return json.load(r)
def write(name,value):(OUT/(name+'.json')).write_text(json.dumps(value,indent=2)+'\n')
def main():
    original=json.loads((OUT/'answer.json').read_text());key=original['id'];patient=original['patient_id']
    answer=api('/api/stack/case-synthesis?id='+key);assert answer['id']==key and answer['source_availability']['received_records']==1876
    preview=api('/api/stack/context/plan',{'context_id':answer['context_id']});assert preview['saved_answer_id']==key and preview['spending_limit']=='uncapped'
    replay=api('/api/stack/context/synthesize',{'context_id':answer['context_id']});assert replay['cache_hit'] and replay['new_paid_api_calls']==0
    needs=api('/api/stack/evidence-needs',{'answer_id':key,'selectors':[]});write('evidence-needs',needs)
    # Existing program assessment integration is checked as context only. This
    # does not create a new functional rule or assert an affordability need.
    assessment=api('/api/stack/work/prepare',{'program':'affordability','patient':patient,'synthesis_ids':[key]});write('assessment-proposal',assessment)
    assert assessment['synthesis_assessments']==1
    db=sqlite3.connect(stack_path()/'program-work.sqlite3')
    saved=json.loads(db.execute('SELECT payload FROM work_proposals WHERE id=?',(assessment['proposal_id'],)).fetchone()[0]);db.close()
    aid=saved['assessment_ids'][0];bound=api('/api/stack/capabilities/assessment?id='+aid)
    assert bound['assessment']['synthesis_context']['answers'][0]['answer_id']==key
    assert not bound['assessment']['clinical_eligibility_established'];write('assessment',bound)
    buyer='synthetic-functional-review';port=api('/api/stack/capabilities?buyer='+buyer)
    cap={'id':'functional-evidence-review','name':'Synthetic function and support evidence review',
         'description':'Local evidence packet preparation; no assessment, referral, enrollment or contact performed.',
         'need_tags':['functional_recovery','home_care'],'modality':'virtual','regions':[],'payer_types':[],
         'plan_ids':[],'pathway_ids':[],'owner':{'kind':'agent','id':'synthetic-local-evidence-worker'},'available':True,
         'capacity_slots':None,'capacity_as_of':'2029-08-15','capacity_until':'2029-08-15',
         'effective_start':'2029-01-01','effective_end':'2029-12-31',
         'prerequisites':['Review dated findings and unresolved questions','Determine present support needs and patient goals before any clinical referral'],
         'provenance':'Automated synthetic integration fixture, not a buyer submission or verified care service'}
    home=copy.deepcopy(cap);home.update(id='home-nursing-review',name='Synthetic home nursing and therapy review',
         modality='home',pathway_ids=['home_health'],payer_types=['original_medicare','medicare_advantage'],
         owner={'kind':'clinician','id':'synthetic-home-health-review-team'},
         prerequisites=['Clinical indication and homebound criteria require review; examination did not establish homebound status','Exact member benefits, plan, agency, consent and authorization remain unverified'])
    port=api('/api/stack/capabilities/save',{'buyer_id':buyer,'expected_revision':port['revision'],'actor':'Automated synthetic build verification','capabilities':[cap,home]})
    request={'buyer_id':buyer,'origin':{'kind':'answer','id':key,'finding_ids':['p1_f1','p1_f3','p1_f4']},
         'need_tags':['functional_recovery','home_care'],'rationale':'Test source-linked review routing only. Dated exam does not establish homebound status or a new skilled need; this is not a recommendation to enroll or provide home services.',
         'coverage':{'payer_type':'unknown','plan_id':'','region':'','as_of':'2029-08-15','provenance':'No member-specific coverage was selected for this synthetic review','preferences':'Unknown; no consent inferred from agrees status'}}
    plan=api('/api/stack/capabilities/prepare',request);write('buyer-plan',plan)
    assert plan['need_mapping_authority']=='operator_selected_not_clinically_confirmed'
    assert len(plan['options'])==2 and all(not o['clinical_execution_authorized'] for o in plan['options'])
    option=next(o for o in plan['options'] if o['capability']['id']==cap['id'])
    queue={'plan_id':plan['id'],'option_id':option['id'],'actor':'Automated synthetic build verification'}
    task=api('/api/stack/capabilities/queue',queue)['task'];assert api('/api/stack/capabilities/queue',queue)['idempotent_replay']
    if task['state']=='queued_for_internal_review':
        task=api('/api/stack/capabilities/event',{'task_id':task['id'],'expected_revision':task['revision'],
            'request_id':'availability-functional-packet-v1','action':'prepare_handoff','actor':'Automated synthetic build verification',
            'note':'Prepare only an internal source-evidence review packet. No clinician review or external action occurred.'})['task']
    assert task['state']=='handoff_prepared_for_review';assert task['handoff']['origin']['findings']==plan['origin']['findings']
    write('internal-task',task)
    result={'answer_id':key,'patient_id':patient,'received_records':1876,'selected_records':4,'source_units':7,
        'saved_answer_http_replay':True,'evidence_needs_id':needs['id'],'assessment_id':aid,'program':'affordability',
        'program_check_scope':'Existing assessment bridge compatibility only; no functional or affordability rule validated',
        'buyer_id':buyer,'buyer_plan_id':plan['id'],'internal_task_id':task['id'],'task_state':task['state'],
        'exact_findings_in_handoff':True,'clinical_eligibility_established':False,'human_reviews_created':0,'external_actions':0,'new_paid_api_calls':0}
    write('http-verification',result);print(json.dumps(result,indent=2))
if __name__=='__main__':main()
