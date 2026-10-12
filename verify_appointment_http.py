import json
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
out=Path('artifacts/agere-appointment-followthrough-v1')
def api(action,body):
 with urlopen(Request('http://localhost:8885/api/stack/'+action,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'}),timeout=300) as r:return json.load(r)
def save(name,r):(out/(name+'.json')).write_text(json.dumps(r,indent=2)+'\n')
def reject(action,body):
 try:api('appointment-followthrough/'+action,body)
 except HTTPError as e:assert e.code in (400,409);return
 raise AssertionError('Invalid request accepted')
summary=json.loads((out/'population-summary.json').read_text());r=api('appointment-followthrough/prepare',{'patient_ids':None});assert r==summary
assert api('appointment-followthrough/open',{'review_id':r['id']})==r
for state in r['status_counts']:
 p=next(p for p in r['patients'] if p['status']==state);d=api('appointment-followthrough/inspect',{'review_id':r['id'],'patient_id':p['patient_id']});assert d['patient']['status']==state;save('example-'+state,d)
reject('inspect',{'review_id':r['id'],'patient_id':'absent'});reject('prepare',{'patient_ids':['P-1001','P-1001']});reject('prepare',{'patient_ids':[]});reject('work',{'review_id':r['id'],'patient_id':'absent'})
p=api('appointment-followthrough/work',{'review_id':r['id'],'patient_id':'P-3002'});save('work-proposal',p);assert p['counts']['candidates']==1
saved=api('work/save',{'proposal_id':p['proposal_id'],'actor':'Agere autonomous engineering verification'});save('work-saved',saved);assert saved['changes']['created']==1
replay=api('work/save',{'proposal_id':p['proposal_id'],'actor':'Agere autonomous engineering verification'});assert replay['changes']['created']==0 and replay['changes']['unchanged']==1
save('http-verification',{'status':'passed','population_review':r['id'],'population':len(r['patients']),'states':r['status_counts'],'exact_replay':True,'invalid_requests_rejected':4,'new_internal_review_items':1,'replay_duplicate_items':0,'patient':'P-3002','proposal_id':p['proposal_id'],'external_actions':0})
print('Appointment HTTP and persistent work passed',flush=True)
