"""Actual loopback recovery checks; original decisions plus portable adapter input."""
import copy
import json
import os
from pathlib import Path
import urllib.error
from clinical_core.contracts import digest
import verify_scoped_review_compiler as H

OUT = Path('artifacts/agere-local-recovery-v1')
IDS = {'individual':'f0f91f6f87e8520fb1eb9442e8376692cb99839f549ffe7df81d74b4c5e54a5c',
       'population':'47b22d8abb7edbcff4b7ac1a630495bf4d4957f2a90fd33c8fb53658d99cadb2'}

def save(name, value):
    name = os.environ.get('AGERE_RECOVERY_HTTP', 'http') + name[4:] if name.startswith('http/') else name
    p = OUT / (name + '.json'); p.parent.mkdir(parents=True, exist_ok=True)
    assert not p.exists(), str(p)
    p.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    return value


def adapter():
    H.BASE = 'http://localhost:8885/api/stack/'
    request = H.call('portable-intake/example', {})
    before = save('adapter/missing', H.call('portable-intake/save', request))
    assert before['patients'][3]['evaluation']['requirements'][0]['state']['reason'] == 'portable_source_mapping_incomplete'
    request['mapping']['version'] = 'literal-v2'
    request['mapping']['kinds']['chemistry']['fields']['collection_date'] = 'sample_day'
    after = save('adapter/complete', H.call('portable-intake/save', request))
    comparison = save('adapter/comparison', H.call('portable-intake/compare', {'before_id':before['id'],'after_id':after['id']}))
    assert comparison['roster_patients'] == 4 and comparison['changed_requirements']
    renamed = copy.deepcopy(request)
    rename = {'observation_key':'ID','member_key':'PERSON','category':'TYPE','written_on':'SOURCE_DAY','authored_on':'SIGNED_DAY','received_on':'RECEIVED_DAY','replaces':'PREVIOUS','analyte':'TEST','number':'RESULT','units':'UNIT','result_state':'STATUS','sample_day':'COLLECTED','clinic':'SPECIALTY','attended':'ATTENDANCE','visit_day':'VISIT','bills_difficult':'HARDSHIP'}
    renamed['package']['tables'] = {'people':[{'PERSON':r['member_key']} for r in request['package']['tables']['members']],
        'events':[{rename.get(k,k):v for k,v in r.items()} for r in request['package']['tables']['observations']]}
    m = renamed['mapping']; m['roster'] = {'table':'people','id':'PERSON'}; m['records']['table'] = 'events'
    for k in ('id','patient','kind','supersedes'): m['records'][k] = rename[m['records'][k]]
    m['records']['clocks'] = {k:rename[v] for k,v in m['records']['clocks'].items()}
    for v in m['kinds'].values(): v['fields'] = {k:rename[x] for k,x in v['fields'].items()}
    variant = save('adapter/variant', H.call('portable-intake/save', renamed))
    def signature(v): return [(p['patient_id'],[(r['binding'],r['result'],r['state']['status'],r['state']['value']) for r in p['evaluation']['requirements']]) for p in v['patients']]
    assert signature(after) == signature(variant)
    assert after['summary'] == variant['summary'] and after['ingestion']['package_hash'] != variant['ingestion']['package_hash']
    assert variant['summary']['roster_patients'] == 4 and variant['summary']['visible_records'] == 9
    assert variant['snapshot']['excluded'] == {'future':1,'late_arrival':1,'identity_quarantine':1,'superseded':1}
    assert not variant['clinical_eligibility_established']
    save('adapter/verification', {'status':'passed','independent_source_schema':True,'exact_results_after_mapping':True,
       'missing_mapping_vs_unobserved':True,'full_roster':4,'visible':9,'clinical_qualification':False,'saved_ids':[before['id'],after['id'],variant['id']]})


def restored():
    H.BASE = 'http://localhost:8887/api/stack/'
    meta = save('http/recovery-meta', H.call('meta'))
    assert meta['recovery']['mode'] == 'inspection_only' and not meta['allow_live']
    assert meta['retrieval']['patients'] == 1614
    for name, ident in IDS.items():
        after = save('http/restored-'+name, H.call('decision?id='+ident))
        before = json.loads((OUT / ('http/before-'+name+'.json')).read_text())
        assert after['decision'] == before['decision']
        assert after['tasks'] == before['tasks']
        assert after['followups'] == before['followups']
        assert after['dependencies_current'] == before['dependencies_current']
    lineage = save('http/restored-lineage', H.call('decision/lineage', {'decision_id':IDS['individual']}))
    before = json.loads((OUT / 'http/before-lineage.json').read_text())
    assert lineage == before
    try:
        H.call('decision/statement-review-status', {'result_id':'7c6f964a5c8b01d42b9703f5edce009c3abaf249fcc4343b319d30116163ac1f'})
    except urllib.error.HTTPError as exc:
        review = json.load(exc)
        assert exc.code == 400 and review['error'] == 'Cohort implementation or parent workspace changed'
        save('http/restored-review-qualification-rejection', review)
    else: raise AssertionError('Frozen learning cohort silently transferred to another workspace')
    route = 'semantic?id=d210958c126598d9b6023ac3692a1eada6169e3081e5dfb75714e38abe851ab0'
    reading = save('http/restored-source-reading', H.call(route))
    H.BASE = 'http://localhost:8885/api/stack/'
    original_reading = save('http/original-source-reading', H.call(route))
    H.BASE = 'http://localhost:8887/api/stack/'
    assert reading == original_reading and reading['review_revision'] == 0 and reading['reviews'] == []
    for name in ('missing','complete','variant'):
        prior = json.loads((OUT / ('adapter/'+name+'.json')).read_text())
        value = save('http/restored-adapter-'+name, H.call('portable-intake/inspect', {'id':prior['id']}))
        assert {k:v for k,v in value.items() if k != 'current'} == prior and value['current']
    for route, body in [('decision/event',{'decision_id':IDS['individual']}),('decision/statement-review',{}),('compose',{'question':'Review respiratory sources for patient P-1002','planning':'scoped_local_candidate'}),('portable-intake/save',{})]:
        try: H.call(route,body)
        except urllib.error.HTTPError as exc:
            assert exc.code == 403; error = json.load(exc); assert 'Recovery inspection only' in error['error']
            save('http/refused-'+route.replace('/','-'),error)
        else: raise AssertionError('Recovery allowed work/model mutation')
    assert H.ledger() == json.loads((OUT/'ledger-before.json').read_text())['digest']
    save('http/verification', {'status':'passed','original_individual_and_population_decisions':True,'exact_owned_work_and_followups':True,
        'exact_dispatch_lineage':True,'source_reading_and_review_preserved':True,'frozen_cohort_requalification_required':review,'portable_adapter_receipts_reopened':3,'mutation_and_model_routes_refused':4,
        'provider_ledger_unchanged':True,'new_paid_calls':0,'new_model_execution':False,'timings':H.TIMINGS})

if __name__ == '__main__':
    import sys
    {'adapter':adapter,'restored':restored}[sys.argv[1]]()
    print(sys.argv[1] + ' checks passed')
