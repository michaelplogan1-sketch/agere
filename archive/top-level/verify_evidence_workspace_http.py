"""Live local integration checks; never dispatch a model or clinical action."""
import json
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError
OUT=Path('artifacts/agere-evidence-workspace-v1')
BASE='http://127.0.0.1:8885'
def api(route,body=None):
 request=Request(BASE+route,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
 with urlopen(request,timeout=180) as r:return json.load(r)
a=api('/api/stack/source-atlas');assert a['patients']==1614 and a['source_categories']==158
r=api('/api/stack/source-records?'+urlencode({'stream':'pharmacy_renewal_requests','patient':'P-1001'}))
x=api('/api/stack/evidence-thread?'+urlencode({'patient':r['records'][0]['patient_id'],'record':r['records'][0]['record_id']}));assert x['component_records']==5
contexts=[]
for size in [16000,32000]:
 c=api('/api/stack/context/prepare',{'patient':x['patient_id'],'parameters':{'question':'What do these received sources say, where do they disagree, and what remains unknown?','streams':x['context_streams'],'concepts':[],'contract':'','program_ids':[],'max_bytes':size}})
 receipt=api('/api/stack/context?id='+c['patients'][0]['id']);assert receipt['budget']['used']<=size
 contexts.append({'id':receipt['id'],'status':receipt['status'],'budget':receipt['budget'],'dispatch':receipt['dispatch']})
assert contexts[0]['dispatch']['source_review_allowed'] is False
assert contexts[1]['dispatch']['source_review_allowed'] is True
q=api('/api/stack/search',{'question':'No prescription was issued; medication and symptom review','patient':'P-1001'})
assert q['new_paid_api_calls']==0 and q['retrieval']['evidence']
first=q['retrieval']['evidence'][0]['source'];thread=api('/api/stack/evidence-thread?'+urlencode({'patient':first['patient_id'],'record':first['record_id']}));assert thread['root_record']==first['record_id']
for route in ['/api/stack/source-records?stream=invented','/api/stack/evidence-thread?patient=P-1002&record='+r['records'][0]['record_id']]:
 try:api(route);raise AssertionError('Invalid request accepted')
 except HTTPError as e:assert e.code==400
result={'status':'passed','synthetic':True,'inventory':{k:a[k] for k in ('patients','records','source_categories')},'thread_id':x['id'],'context_results':contexts,'search_evidence_count':len(q['retrieval']['evidence']),'search_thread_id':thread['id'],'negative_requests_rejected':2,'new_paid_api_calls':0}
(OUT/'http-verification.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
