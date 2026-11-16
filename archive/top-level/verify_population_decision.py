"""Two actual full-roster documentary programs -> buyer -> internal work.

Independent public received-source SQL; no hidden truth or model requests.
"""
import json,sqlite3,urllib.request,os,time
from pathlib import Path
from clinical_core.contracts import digest
OUT=Path(os.environ.get('AGERE_POPULATION_OUT','artifacts/agere-population-decision-v1/http'))
BASE='http://localhost:8885/api/stack/'
BUYER='g4-population-development'
TIMINGS=[]

def save(name,row):
    p=OUT/(name+'.json');assert not p.exists(),str(p);p.write_text(json.dumps(row,indent=2,allow_nan=False)+'\n');return row

def call(route,body=None):
    r=urllib.request.Request(BASE+route,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
    start=time.perf_counter()
    with urllib.request.urlopen(r,timeout=180) as response:result=json.load(response)
    TIMINGS.append({'route':route.split('?')[0],'seconds':round(time.perf_counter()-start,3)})
    return result

def ledger():
    with sqlite3.connect('artifacts/agere-full/provider-budget.sqlite3') as db:return dict(db.execute('SELECT id,status FROM calls'))

def main():
    OUT.mkdir(parents=True,exist_ok=True);before=ledger();cfg=call('decision/population-config');save('configuration',cfg)
    capabilities=[]
    for name,tag in [('respiratory','symptom_support'),('behavioral','behavioral_health')]:
        capabilities.append({'id':name+'-review','name':'Synthetic '+name+' source review','description':'Internal documentary review demonstration only',
          'need_tags':[tag],'modality':'virtual','regions':[],'payer_types':[],'plan_ids':[],'pathway_ids':[],
          'owner':{'kind':'team','id':'synthetic-'+name+'-review-team'},'available':True,'capacity_slots':4,
          'capacity_as_of':cfg['as_of'],'capacity_until':cfg['as_of'],'effective_start':'2026-01-01','effective_end':'2030-01-01',
          'prerequisites':['Patient-specific indication, goals, consent, participation and benefits require verification.'],
          'provenance':'Explicit fictional engineering declaration: four review slots, no actual care capacity asserted.'})
    existing=call('capabilities?buyer='+BUYER)
    if not existing['revision']:save('buyer',call('capabilities/save',{'buyer_id':BUYER,'expected_revision':0,'actor':'G4 local workflow verification','capabilities':capabilities}))
    else:assert sorted(existing['capabilities'],key=lambda c:c['id'])==sorted(capabilities,key=lambda c:c['id'])
    active=json.loads(Path('config/agere/active_warehouse.json').read_text())
    db=sqlite3.connect((Path(active['source_release'])/'warehouse.sqlite').resolve().as_uri()+'?mode=ro',uri=True)
    roster=sorted(r[0] for r in db.execute('SELECT patient_id FROM patients'));assert len(roster)==1614
    selectors=json.loads(Path('config/agere/domain_review_sources.json').read_text())['domains'];results=[]
    for domain in ('respiratory','behavioral'):
        expected=set();queries=[]
        for selector in selectors[domain]['selectors']:
            stream=selector['stream'];assert stream.replace('_','').isalnum()
            sql='SELECT DISTINCT patient_id FROM "'+stream+'" WHERE json_extract(facts,?) IN ('+','.join('?' for _ in selector['values'])+') AND available_date<=? AND synthetic=1'
            params=['$.'+selector['field'],*selector['values'],cfg['as_of']]
            expected.update(r[0] for r in db.execute(sql,params));queries.append({'sql':sql,'parameters':params})
        request={'question':'Across the original cohort, which '+domain+' records nominate documentary review, what remains unresolved, and which buyer services could review them?',
            'program':domain+'_source_review','cohort':cfg['cohort'],'buyer_id':BUYER,'as_of':cfg['as_of']}
        decision=save(domain+'-decision',call('decision/population-prepare',request))
        assert decision==call('decision/population-prepare',request)
        assert decision['selection']['patient_ids']==roster and decision['selection']['roster_digest']==digest(roster)
        nominated=sorted(m['patient_id'] for m in decision['members'] if m['review_candidate'] is True)
        assert nominated==sorted(expected),(domain,len(nominated),len(expected))
        assert decision['counts']['need_unresolved_without_nomination']==1614-len(expected)
        save(domain+'-independent-denominator',{'status':'passed','source':'public received warehouse views, read-only','queries':queries,'roster_count':len(roster),'roster_digest':digest(roster),'nominated_patient_ids':sorted(expected),'clinical_eligibility_established':False})
        patient=nominated[0]
        match=save(domain+'-member',call('decision/population-match',{'decision_id':decision['id'],'patient_id':patient}))
        assert match['question']==request['question'] and match['matching']['patient_id']==patient
        assert match['matching']['coverage']['payer_type']=='unknown'
        option=next(o for o in match['matching']['options'] if o['capability']['id']==domain+'-review')
        assert option['status']=='requires_verification' and not option['clinical_execution_authorized']
        queue={'decision_id':decision['id'],'match_id':match['result_id'],'option_id':option['id'],'actor':'G4 local workflow verification'}
        work=save(domain+'-work',call('decision/population-queue',queue));task=work['task']
        assert call('decision/population-queue',queue)['idempotent_replay']
        assert work['decision_binding']['question']==request['question'] and work['decision_binding']['selection_digest']==digest(decision['selection'])
        handoff=save(domain+'-handoff',call('decision/event',{'decision_id':decision['id'],'task_id':task['id'],'expected_revision':task['revision'],
            'request_id':'g4-population-'+domain+'-v1','action':'prepare_handoff','actor':'G4 local workflow verification','note':'Synthetic internal source review only; no clinician review, enrollment, outreach, care or outcome claimed.'}))
        assert handoff['task']['state']=='handoff_prepared_for_review'
        reopened=save(domain+'-reopened',call('decision?id='+decision['id']))
        assert reopened['dependencies_current'] and any(t['task']['id']==task['id'] and t['binding_current'] for t in reopened['tasks'])
        results.append({'domain':domain,'decision_id':decision['id'],'population':len(roster),'source_review_nominations':len(nominated),'selected_need_unresolved':1614-len(nominated),'independent_denominator_reconciled':True,'patient_id':patient,'task_id':task['id'],'task_state':handoff['task']['state'],'payer_eligibility':'unverified','full_population_entitlement_assessed':False})
        print(json.dumps(results[-1]),flush=True)
    assert ledger()==before
    save('request-timings',TIMINGS)
    save('verification',{'status':'passed','programs':results,'provider_calls_added':0,'clinical_validated':False,'external_actions':0,'scope':'Full-roster question decisions, two independent documentary program compositions, buyer capacity declarations and explicit payer prerequisites; one selected member handoff per program. Full-population patient-specific matching, operational completion, outcomes and financial reconciliation are not claimed.'})

if __name__=='__main__':main()
