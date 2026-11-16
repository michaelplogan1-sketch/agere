"""Verify bounded synthetic domain review through the actual HTTP work path."""
import copy,json,sys
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from clinical_core.agere.gateway import BudgetLedger

OUT=Path('artifacts/agere-domain-review-v1')
CASES={'respiratory':'SYN-3C114EFC-C0C','behavioral':'P-7193'}
ACTOR='Automated synthetic domain review verification'
def api(route,body=None):
    req=Request('http://localhost:8885'+route,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
    with urlopen(req,timeout=240) as response:return json.load(response)
def write(name,value):(OUT/(name+'.json')).write_text(json.dumps(value,indent=2)+'\n')
def read(name):return json.loads((OUT/(name+'.json')).read_text())
def ledger():return BudgetLedger(Path('artifacts/agere-full/provider-budget.sqlite3')).receipt()
def prepare():
    write('ledger-before',ledger())
    for domain,patient in CASES.items():
        page=api('/api/stack/domain-review?domain='+domain+'&patient='+patient);write(domain+'-page',page)
        assert page['counts']['population']==1614 and len(page['patients'])==1
        row=page['patients'][0];assert row['patient_id']==patient and not row['clinical_execution_authorized']
        assert row['suggested_record_ids']
        ctx=api('/api/stack/domain-review/context',{'review_id':page['id'],'patient_id':patient,'record_ids':row['suggested_record_ids']})
        write(domain+'-context',ctx)
        assert ctx['selected_record_ids']==row['suggested_record_ids'] and not ctx['guidance_in_model_input']
        preview=api('/api/stack/context/plan',{'context_id':ctx['context']['id']});write(domain+'-preview',preview)
        first=api('/api/stack/domain-review?domain='+domain);second=api('/api/stack/domain-review?domain='+domain+'&id='+page['id']+'&offset=10')
        assert len(first['patients'])==len(second['patients'])==10
        assert not ({p['patient_id'] for p in first['patients']}&{p['patient_id'] for p in second['patients']})
        try:
            api('/api/stack/domain-review/context',{'review_id':page['id'],'patient_id':patient,'record_ids':[]})
            raise AssertionError('Empty domain context accepted')
        except HTTPError as e:assert e.code==400
        print(json.dumps({'domain':domain,'patient':patient,'counts':page['counts'],'selected_records':ctx['selected_record_ids'],'context_id':ctx['context']['id'],'budget':ctx['context']['budget'],'preview':preview}),flush=True)
def synthesize(domain):
    ctx=read(domain+'-context')['context']
    answer=api('/api/stack/context/synthesize',{'context_id':ctx['id']});write(domain+'-answer',answer)
    print(json.dumps({'domain':domain,'answer_id':answer['id'],'cache_hit':answer.get('cache_hit'),'new_paid_api_calls':answer.get('new_paid_api_calls'),'findings':answer['findings'],'recommendations':answer.get('recommendations')}),flush=True)
def finish():
    buyer='synthetic-domain-source-review';port=api('/api/stack/capabilities?buyer='+buyer)
    caps=[]
    for domain in CASES:
        tags=['respiratory'] if domain=='respiratory' else ['behavioral_health']
        cap={'id':domain+'-evidence-review','name':'Synthetic '+domain+' evidence review',
            'description':'Local source packet preparation; no clinical assessment, referral, enrollment or contact performed.',
            'need_tags':tags,'modality':'virtual','regions':[],'payer_types':[],'plan_ids':[],'pathway_ids':[],
            'owner':{'kind':'agent','id':'synthetic-local-evidence-worker'},'available':True,'capacity_slots':None,
            'capacity_as_of':'2029-08-15','capacity_until':'2029-08-15','effective_start':'2029-01-01','effective_end':'2029-12-31',
            'prerequisites':['Resolve dated source limits and current clinical assessment','Verify patient goals and exact service applicability'],
            'provenance':'Automated synthetic integration fixture; not an actual buyer declaration or care service'}
        caps.append(cap)
        clinical=copy.deepcopy(cap);clinical.update(id=domain+'-team-review',name='Synthetic '+domain+' clinician review',owner={'kind':'clinician','id':'synthetic-'+domain+'-review-team'},
            pathway_ids=['pulmonary_rehab'] if domain=='respiratory' else ['bhi'],
            payer_types=['original_medicare','medicare_advantage'],prerequisites=['Clinical indication, exact benefit criteria and team participation unverified','No patient agreement, order or authorization inferred'])
        caps.append(clinical)
    if port['revision']==0:
        port=api('/api/stack/capabilities/save',{'buyer_id':buyer,'expected_revision':0,'actor':ACTOR,'capabilities':caps})
    write('buyer-portfolio',port);results=[]
    for domain,patient in CASES.items():
        answer=read(domain+'-answer');key=answer['id'];ctx=read(domain+'-context')['context']
        replay=api('/api/stack/context/synthesize',{'context_id':ctx['id']})
        assert replay['cache_hit'] and replay['new_paid_api_calls']==0
        needs=api('/api/stack/evidence-needs',{'answer_id':key,'selectors':[]});write(domain+'-needs',needs)
        tags=['respiratory'] if domain=='respiratory' else ['behavioral_health']
        request={'buyer_id':buyer,'origin':{'kind':'answer','id':key,'finding_ids':[f['id'] for f in answer['findings'] if f['kind']=='documented_account']},'need_tags':tags,
            'rationale':'Operator-selected synthetic source-review routing. The dated source findings do not establish a current diagnosis, clinical service need, benefit entitlement or consent.',
            'coverage':{'payer_type':'unknown','plan_id':'','region':'','as_of':'2029-08-15','provenance':'No member-specific benefit evidence selected for this synthetic review','preferences':'Unverified; no consent inferred'}}
        plan=api('/api/stack/capabilities/prepare',request);write(domain+'-buyer-plan',plan)
        assert len(plan['options'])==2 and not any(o['clinical_execution_authorized'] for o in plan['options'])
        option=next(o for o in plan['options'] if o['capability']['id']==domain+'-evidence-review')
        q={'plan_id':plan['id'],'option_id':option['id'],'actor':ACTOR}
        task=api('/api/stack/capabilities/queue',q)['task'];assert api('/api/stack/capabilities/queue',q)['idempotent_replay']
        if task['state']=='queued_for_internal_review':
            task=api('/api/stack/capabilities/event',{'task_id':task['id'],'expected_revision':task['revision'],'request_id':domain+'-source-packet-v1','action':'prepare_handoff','actor':ACTOR,'note':'Local evidence packet only. No human clinical review, external referral, patient contact or clinical action.'})['task']
        assert task['state']=='handoff_prepared_for_review';write(domain+'-task',task)
        availability=api('/api/stack/capabilities/availability?task_id='+task['id']);write(domain+'-availability',availability)
        assert availability['answers'][0]['answer_id']==key
        results.append({'domain':domain,'patient_id':patient,'review_id':read(domain+'-page')['id'],'selected_record_ids':ctx['record_scope']['record_ids'],
            'context_id':ctx['id'],'answer_id':key,'evidence_needs_id':needs['id'],'buyer_plan_id':plan['id'],'buyer_task_id':task['id'],
            'task_state':task['state'],'saved_answer_replay':True,'queue_idempotence':True,'clinical_quality_validated':False,'human_reviews_created':0,'external_actions':0})
    write('http-verification',results);write('ledger-after',ledger());print(json.dumps(results,indent=2))
if __name__=='__main__':
    if sys.argv[1]=='prepare':prepare()
    elif sys.argv[1]=='synthesize':synthesize(sys.argv[2])
    elif sys.argv[1]=='finish':finish()
