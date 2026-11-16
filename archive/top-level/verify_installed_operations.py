"""Actual isolated installation workflows; no external calls or human reviews."""
import copy
import json
import os
import sqlite3
import sys
from pathlib import Path
from clinical_core.contracts import digest
import verify_runnable_release as V

BASE=Path(__file__).resolve().parent/os.environ.get('AGERE_INSTALLED_VERIFY','artifacts/agere-installed-operations-v1')
OUT=BASE/'http-v2';OUT.mkdir(exist_ok=True)
BUYER='g6-installed-local-operations-v2'
QUESTION='Prepare respiratory source-review work for patients P-1004, P-1002 excluding patient P-1003'

def save(name,data):
    with (OUT/(name+'.json')).open('x') as f:json.dump(data,f,indent=2);f.write('\n')
    return data

def read(name):return json.loads((OUT/(name+'.json')).read_text())
def call(route,body=None):return V.ok(8887,route,body)
def saved(name,route,body=None):return save(name,call(route,body))

def deny(name,route,body=None,code=403):
    status,response=V.call(8887,route,body)
    save(name,{'http':status,'response':response})
    assert status==code and isinstance(response,dict) and response.get('error'),(route,status,response)
    return response

def baseline():return json.loads((BASE/'before-http.json').read_text())
def original(name):V.equal(baseline(),save(name,V.snapshot(8885)))

def operations():
    V.equal(baseline(),save('installation-initial',V.snapshot(8887)))
    meta=saved('meta','meta');assert meta['recovery']['mode']=='local_operations' and not meta['allow_live']
    assert meta['retrieval']['patients']==1614
    contracts=saved('page-contracts','requirement-contracts');assert contracts['contracts']
    outcomes=saved('population-page-contracts','outcome-contracts');assert outcomes['contracts']
    qualifications=saved('qualification','learning-cohorts');assert qualifications['cohorts']
    for row in qualifications['cohorts']:
        assert row['status']=='unavailable_in_this_installation' and not row['qualification_transferred']
        assert call('learning-cohorts?id='+row['id'])==row
    deny('qualification-unknown','learning-cohorts?id=unknown',code=400)
    for route in ('context/interpret','learning-cohorts/read','semantic/review','context/output-recover'):
        deny('refused-'+route.replace('/','-'),route,{})
    deny('unavailable-get','learning-qualification')
    deny('hosted-plan-refused','compose',{'question':QUESTION})
    deny('candidate-plan-refused','decision/prepare',{'planner':'buyer_documentary_candidate'})

    template=call('capabilities?buyer=g3-buyer-documentary-development')
    port=saved('buyer','capabilities/save',{'buyer_id':BUYER,'expected_revision':0,
        'actor':'Synthetic installed-workflow verification','capabilities':template['capabilities']})
    assert port['revision']==1
    source=saved('individual-baseline','decision?id=c162f4159efe949e15f1383c0fa7c52ab9e4b0415ba10afafcab0be1abe2cb91')
    assert source['dependencies_current']
    request=copy.deepcopy(source['decision']['request'])
    request.update(buyer_id=BUYER,planner='cited_source_type_baseline')
    decision=saved('individual-decision','decision/prepare',request)
    assert decision['question']==source['decision']['question']
    assert decision['answer_id']==request['answer_id'] and decision['received_inventory']
    option=next(o for o in decision['matching']['options'] if o['capability']['id']=='navigation-review')
    task=saved('individual-work','decision/queue',{'decision_id':decision['id'],'option_id':option['id'],
        'actor':'Synthetic installed-workflow verification'})
    assert not task['task'].get('clinical_execution_authorized',False)
    event={'decision_id':decision['id'],'task_id':task['task']['id'],'expected_revision':task['task']['revision'],
           'request_id':'g6-installed-handoff','action':'prepare_handoff','actor':'Synthetic installed-workflow verification',
           'note':'Internal test only; no human review or external dispatch.'}
    prepared=saved('individual-handoff','decision/event',event)
    assert prepared['task']['revision']==task['task']['revision']+1 and prepared['external_actions_executed']==0
    replay=saved('individual-replay','decision/event',event);assert replay['idempotent_replay']
    deny('stale-work-refused','decision/event',{**event,'request_id':'g6-stale'},code=409)
    composition=saved('composition','compose',{'question':QUESTION,'planning':'scoped_local_candidate'})
    assert composition['results'][0]['result']['counts']=={'evaluated':1614,'matched':2,'not_matched':1612,'unknown':0}
    seed=composition['decision_seed']
    pop=saved('population-decision','decision/scoped-prepare',{'question':QUESTION,'buyer_id':BUYER,
        'as_of':seed['as_of'],'compiler_identity':composition['planner_receipt']['compiler_identity']})
    assert set(pop['selection']['patient_ids'])=={'P-1002','P-1004'}
    route=saved('population-payer','decision/population-route-member',{'decision_id':pop['id'],'patient_id':'P-1004'})
    match=saved('population-match','decision/population-match',{'decision_id':pop['id'],'patient_id':'P-1004'})
    option=next(o for o in match['matching']['options'] if o['capability']['id']=='respiratory-review')
    assert option['status']=='requires_verification' and not option['clinical_execution_authorized']
    poptask=saved('population-work','decision/population-queue',{'decision_id':pop['id'],
        'match_id':match['result_id'],'option_id':option['id'],'actor':'Synthetic installed-workflow verification'})
    assert poptask['decision_binding']['question']==QUESTION
    # Source request is a new locally selected context, not fabricated reading.
    context=saved('source-question','context/from-question',{'patient':'P-1002',
        'question':'Which received records describe respiratory symptoms and oxygen use?'})
    assert context['patient_id']=='P-1002'
    for name,key in [('individual',decision['id']),('population',pop['id'])]:
        view=saved(name+'-reopened','decision?id='+key);assert view['dependencies_current'] and view['tasks']
    original('original-after-operations')
    save('operations-verification',{'status':'passed','individual_decision':decision['id'],'population_decision':pop['id'],
        'new_buyer':BUYER,'original_roster':1614,'scoped_members':2,'payer_and_consent_unverified':True,
        'actual_owned_work_created':True,'new_paid_calls':0,'clinical_review_created':False})

def intake():
    req=call('portable-intake/example',{})
    req['package']['source_id']='g6-installed-source-variant'
    before=saved('intake-missing','portable-intake/save',req)
    req['mapping']['version']='installed-complete-v1'
    req['mapping']['kinds']['chemistry']['fields']['collection_date']='sample_day'
    after=saved('intake-complete','portable-intake/save',req)
    comparison=saved('intake-comparison','portable-intake/compare',{'before_id':before['id'],'after_id':after['id']})
    assert comparison['changed_requirements'] and comparison['roster_patients']==4
    changed=copy.deepcopy(req)
    record=next(r for r in changed['package']['tables']['observations'] if r['observation_key']=='lab-corrected')
    record['number']=62
    newer=saved('intake-new-source','portable-intake/save',changed)
    assert newer['id']!=after['id'] and newer['ingestion']['package_hash']!=after['ingestion']['package_hash']
    assert newer['patients'][1:]==after['patients'][1:]
    assert newer['patients'][0]!=after['patients'][0]
    reopened=saved('intake-old-reopened','portable-intake/inspect',{'id':after['id']})
    assert {k:v for k,v in reopened.items() if k!='current'}==after and reopened['current']
    deny('intake-cross-source-refused','portable-intake/compare',{'before_id':after['id'],'after_id':newer['id']},code=400)
    variant_request=json.loads((Path(__file__).resolve().parent/'artifacts/agere-local-recovery-v1/adapter/variant.json').read_text())['request']
    variant_request['package']['source_id']=req['package']['source_id']
    variant=saved('intake-renamed-schema','portable-intake/save',variant_request)
    def signature(v):
        return [(p['patient_id'],[(r['binding'],r['result'],r['state']['status'],r['state']['value'])
                for r in p['evaluation']['requirements']]) for p in v['patients']]
    assert signature(variant)==signature(after) and variant['summary']==after['summary']
    assert variant['snapshot']['excluded']=={'future':1,'late_arrival':1,'identity_quarantine':1,'superseded':1}
    original('original-after-intake')
    save('intake-verification',{'status':'passed','all_roster_members_retained':4,'unaffected_patients_equal':3,
         'received_change_creates_new_review':True,'old_review_retained':True,'cross_source_comparison_refused':True,
         'renamed_schema_results_equal':True,'source_clocks_and_supersession_retained':True})

def invalidate():
    row=read('individual-decision');key=row['id'];task=read('individual-handoff')['task']
    path=BASE/'installation/build/stack/stack.sqlite3'
    assert path.resolve().is_relative_to((BASE/'installation/build').resolve())
    with sqlite3.connect(path) as db:
        records=db.execute('SELECT id,payload FROM records WHERE patient=? ORDER BY id',(row['patient_id'],)).fetchall()
        rid,raw=next((k,p) for k,p in records if k not in row['source_versions']['records'])
        edited=json.loads(raw);edited['installation_verification_marker']='Synthetic mutation fixture only; not clinical evidence.'
        db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(edited),rid))
    try:
        view=saved('source-change-stale-v2','decision?id='+key)
        assert not view['dependencies_current'] and view['received_source_changes']['changes']
        deny('source-change-work-refused-v2','decision/event',{'decision_id':key,'task_id':task['id'],
            'expected_revision':task['revision'],'request_id':'g6-changed-source','action':'hold',
            'actor':'Synthetic installed-workflow verification','note':'Must not apply stale work.'},code=400)
    finally:
        with sqlite3.connect(path) as db:db.execute('UPDATE records SET payload=? WHERE id=?',(raw,rid))
    current=saved('source-restored-current','decision?id='+key)
    assert current['dependencies_current'] and current['tasks']==read('individual-reopened')['tasks']
    # A buyer version change is a separate dependency; the old decision remains inspectable.
    port=read('buyer');caps=copy.deepcopy(port['capabilities']);caps[0]['description']+=' Updated synthetic declaration.'
    saved('buyer-revised','capabilities/save',{'buyer_id':BUYER,'expected_revision':1,
        'actor':'Synthetic installed-workflow verification','capabilities':caps})
    stale=saved('buyer-change-stale','decision?id='+key);assert not stale['dependencies_current']
    deny('buyer-change-work-refused','decision/event',{'decision_id':key,'task_id':task['id'],
        'expected_revision':task['revision'],'request_id':'g6-changed-buyer','action':'hold',
        'actor':'Synthetic installed-workflow verification','note':'Must not apply stale buyer context.'},code=409)
    original('original-after-invalidation')
    save('invalidation-verification',{'status':'passed','source_fixture_record':rid,
        'source_restored':True,'source_change_blocks_work':True,'buyer_version_blocks_work':True,
        'historical_decision_and_task_retained':True,'clinical_reinterpretation_performed':False})

def restart():
    row=saved('restart-individual','decision?id='+read('individual-decision')['id'])
    assert row['decision']==read('individual-decision') or row['decision']=={k:v for k,v in read('individual-decision').items() if k!='request_execution_id'}
    assert row['tasks']==read('buyer-change-stale')['tasks'] and not row['dependencies_current']
    for name in ('intake-missing','intake-complete','intake-new-source','intake-renamed-schema'):
        value=saved('restart-'+name,'portable-intake/inspect',{'id':read(name)['id']})
        assert value['payload_digest']==read(name)['payload_digest'] and value['current']
    assert call('capabilities?buyer='+BUYER)['revision']==2
    original('original-after-restart')
    save('restart-verification',{'status':'passed','new_work_and_source_reviews_persist':True,'stale_context_stays_stale':True})

def rollback():
    V.equal(baseline(),save('rollback-original-decisions',V.snapshot(8887)))
    assert call('capabilities?buyer='+BUYER)['revision']==0
    deny('rollback-new-decision-absent','decision?id='+read('individual-decision')['id'],code=400)
    deny('rollback-new-intake-absent','portable-intake/inspect',{'id':read('intake-new-source')['id']},code=400)
    path=BASE/'installation/build/stack/program-work.sqlite3'
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
        assert db.execute('SELECT max(revision) FROM capability_portfolios WHERE buyer=?',(BUYER,)).fetchone()[0]==2
        assert db.execute('SELECT count(*) FROM capability_tasks WHERE buyer=?',(BUYER,)).fetchone()[0]>=2
    original('original-after-rollback')
    save('rollback-verification',{'status':'passed','fresh_install_restores_checkpoint':True,
        'changed_copy_and_new_work_retained':True,'original_and_current_shared_ledger_preserved':True})

if __name__=='__main__':globals()[sys.argv[1]]();print(sys.argv[1]+' verified')
