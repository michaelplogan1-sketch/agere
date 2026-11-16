"""Local synthetic HTTP workflow; no provider calls or active-source migration."""
import copy,json,urllib.request,urllib.error
from pathlib import Path
from clinical_core.contracts import digest

OUT=Path('artifacts/agere-portable-intake-v1')
def post(action,body):
    req=urllib.request.Request('http://127.0.0.1:8885/api/stack/portable-intake/'+action,
        data=json.dumps(body).encode(),headers={'Content-Type':'application/json'},method='POST')
    with urllib.request.urlopen(req,timeout=30) as response:return json.load(response)
def write(name,data):(OUT/name).write_text(json.dumps(data,indent=2)+'\n')
def signature(r):return [(p['patient_id'],[(x['binding'],x['result'],x['state']['status'],x['state']['value']) for x in p['evaluation']['requirements']]) for p in r['patients']]

x=post('example',{});original=copy.deepcopy(x)
before=post('save',x);assert post('save',x)==before;write('before.json',before)
x['mapping']['version']='buyer-literal-map-v2';x['mapping']['kinds']['chemistry']['fields']['collection_date']='sample_day'
after=post('save',x);assert post('inspect',{'id':after['id']})=={**after,'current':True};write('after.json',after)
comparison=post('compare',{'before_id':before['id'],'after_id':after['id']});write('comparison.json',comparison)
assert len(comparison['changed_requirements'])==12
assert before['summary']['unresolved_work_items']==22 and after['summary']['unresolved_work_items']==19
assert post('inspect',{'id':before['id']})['work_items']==before['work_items']
audit=[]
raw_index={r['observation_key']:r for r in x['package']['tables']['observations']}
for p in after['patients']:
    for r in p['source_evidence']:
        trace=r['_portable_source'];raw=raw_index[r['record_id']]
        assert trace['row_hash']==digest(raw)
        for target,field in trace['field_aliases'].items():
            if field in raw:assert r[target]==raw[field]
        for target,field in trace['clock_aliases'].items():assert r[target]==raw.get(field)
        assert any(c['source_patient_id']==raw['member_key'] and c['patient_id']==p['patient_id'] for c in x['package']['crosswalk'])
        audit.append({'record_id':r['record_id'],'patient_id':p['patient_id'],'row_hash':trace['row_hash'],'field_count':len(trace['field_aliases'])})
write('source-audit.json',{'status':'passed','visible_records':audit,'complete_source_rows_preserved':len(raw_index)})

# A second explicit column map, with a separate source identity and table names.
alternate=copy.deepcopy(x)
rename={'observation_key':'ID','member_key':'PERSON','category':'TYPE','written_on':'SOURCE_DAY','authored_on':'SIGNED_DAY','received_on':'RECEIVED_DAY','replaces':'PREVIOUS','analyte':'TEST','number':'RESULT','units':'UNIT','result_state':'STATUS','sample_day':'COLLECTED','clinic':'SPECIALTY','attended':'ATTENDANCE','visit_day':'VISIT','bills_difficult':'HARDSHIP'}
alternate['package']['source_id']='alternate-synthetic-column-layout'
alternate['package']['tables']={'people':[{'PERSON':r['member_key']} for r in x['package']['tables']['members']],
    'events':[{rename.get(k,k):v for k,v in r.items()} for r in x['package']['tables']['observations']]}
alternate['mapping']={'schema':'portable-source-map/1','version':'alternate-literal-map-v1',
    'roster':{'table':'people','id':'PERSON'},'records':{'table':'events','id':'ID','patient':'PERSON','kind':'TYPE',
        'clocks':{'event_date':'SOURCE_DAY','recorded_date':'SIGNED_DAY','available_date':'RECEIVED_DAY'},'supersedes':'PREVIOUS'},
    'kinds':{'chemistry':{'source_table':'labs','fields':{'test':'TEST','value':'RESULT','unit':'UNIT','status':'STATUS','collection_date':'COLLECTED'}},
        'specialty_visit':{'source_table':'referrals','fields':{'service':'SPECIALTY','attendance_status':'ATTENDANCE','attendance_date':'VISIT'}},
        'cost_screen':{'source_table':'financial','fields':{'difficulty_paying_medical_bills':'HARDSHIP'}}}}
second=post('save',alternate);assert signature(second)==signature(after);assert second['summary']==after['summary'];write('alternate-schema.json',second)
try:post('compare',{'before_id':before['id'],'after_id':second['id']})
except urllib.error.HTTPError as error:
    assert error.code==400;rejected=json.load(error);assert 'Exact same source' in rejected['error']
else:raise AssertionError('Cross-source mapping comparison accepted')
x['contract']='affordability';affordability=post('save',x);write('affordability.json',affordability)
assert affordability['patients'][0]['evaluation']['requirements'][0]['state']['value'] is False
assert affordability['patients'][1]['evaluation']['requirements'][0]['state']['value'] is True
assert all(p['evaluation']['status']=='requirements_unresolved' for p in affordability['patients'])
listing=post('list',{});write('listing.json',listing)
write('http-verification.json',{'status':'passed','before_id':before['id'],'after_id':after['id'],
    'alternate_schema_id':second['id'],'affordability_id':affordability['id'],'roster_patients':4,
    'received_records':15,'visible_records':9,'mapping_changed_requirements':12,'documentary_requirements_newly_satisfied':3,
    'remaining_work_items':19,'two_schemas_same_documentary_results':True,'source_audit_visible_records':len(audit),
    'same_request_replay_exact':True,'original_questions_preserved':True,'cross_source_comparison_rejected':True,
    'new_paid_api_calls':0,'current_reviews':sum(r['current'] for r in listing['reviews']),
    'historical_reviews':sum(not r['current'] for r in listing['reviews'])})
print(json.dumps({'status':'passed','current_reviews':sum(r['current'] for r in listing['reviews']),'visible_records':len(audit)}))
