"""Exercise the actual loopback routes using the already saved synthetic answer."""
import json
from pathlib import Path
import urllib.request
import urllib.error
ROOT=Path(__file__).resolve().parent
OUT=ROOT/'artifacts/agere-case-synthesis-v1'

def request(route,body=None):
 data=json.dumps(body).encode() if body is not None else None
 with urllib.request.urlopen(urllib.request.Request('http://localhost:8885'+route,data=data,headers={'Content-Type':'application/json'}),timeout=60) as r:return json.loads(r.read())

def main():
 old=json.loads((OUT/'answer.json').read_text());context=old['context_id'];key=old['id']
 replay=request('/api/stack/context/synthesize',{'context_id':context})
 saved=request('/api/stack/case-synthesis?id='+key)
 assert replay['cache_hit'] and replay['new_paid_api_calls']==0 and saved['id']==key
 assert saved['findings']==replay['findings']
 rejected=[]
 for route,body in [('/api/stack/context/synthesize',{'context_id':'invalid'}),('/api/stack/context/synthesize',{'context_id':context,'allow_live':True}),('/api/stack/case-synthesis?id='+'f'*64,None)]:
  try:request(route,body)
  except urllib.error.HTTPError as e:assert e.code==400;rejected.append(e.code)
  else:raise AssertionError('Malformed request accepted')
 before=request('/api/stack/work?program=hf_review&offset=0')['summary']
 proposal=request('/api/stack/work/prepare',{'program':'hf_review','patient':old['patient_id'],'synthesis_ids':[key]})
 assert proposal['synthesis_assessments']==1 and proposal['counts']['selected']==1
 after=request('/api/stack/work?program=hf_review&offset=0')['summary'];assert before==after
 (OUT/'work-proposal.json').write_text(json.dumps(proposal,indent=2))
 meta=request('/api/stack/meta');assert meta['model_roles']['source_synthesis']['provider']=='claude'
 report={'synthetic':True,'answer_id':key,'post_replay_verified':True,'get_inspection_verified':True,'invalid_requests_rejected':rejected,'model_role_visible':True,'synthesis_work_assessments':proposal['synthesis_assessments'],'work_counts_unchanged':before==after,'work_candidates':proposal['counts']['candidates'],'new_paid_api_calls':0}
 (OUT/'http-verification.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
if __name__=='__main__':main()
