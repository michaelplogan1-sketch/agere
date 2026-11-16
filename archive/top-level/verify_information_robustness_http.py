import json,sys,urllib.request,urllib.error
from pathlib import Path
ROOT=Path(__file__).resolve().parent;OUT=ROOT/'artifacts/agere-information-robustness-v1'
PORTFOLIO='72a14f250440632549946c522f97e1f87ee17a911518d2571104a6e483e3ae44'

def call(area,action,body):
 req=urllib.request.Request('http://localhost:8885/api/stack/'+area+'/'+action,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=240) as r:return json.load(r)

def write(name,value):(OUT/name).write_text(json.dumps(value,indent=2)+'\n')

def verify():
 alt=json.loads((OUT/'alternative-request.json').read_text());p=call('shared-information','preview',alt)
 history=call('shared-information','list',{'portfolio_id':PORTFOLIO});assert len(history['scenarios'])==2 and all(r['current'] for r in history['scenarios'])
 row=next(r for r in history['scenarios'] if r['id']==p['id']);assert {k:v for k,v in row.items() if k not in ('current','stale_reasons')}==p
 write('alternative-scenario.json',p)
 primary=json.loads((ROOT/'artifacts/agere-shared-information-v1/scenario.json').read_text())
 request={'portfolio_id':PORTFOLIO,'scenario_ids':[primary['id'],p['id']],'rationale':'Compare informative versus uninformative engineering models without assuming which model is correct'}
 preview=call('information-robustness','preview',request);saved=call('information-robustness','prepare',{'comparison':request,'preview_id':preview['id']});assert saved==preview
 assert call('information-robustness','prepare',{'comparison':request,'preview_id':preview['id']})==saved
 history=call('information-robustness','list',{'portfolio_id':PORTFOLIO});assert len(history['comparisons'])==1 and history['comparisons'][0]['current']
 c=saved['calculation'];assert c['policies_enumerated']==14 and c['maximin_policy']['request_ids']==[]
 assert abs(c['maximin_policy']['worst_expected_net_utility']-.5)<1e-12
 assert len(c['minimax_regret_policy']['request_ids'])==1 and abs(c['minimax_regret_policy']['worst_regret']-.1)<1e-12
 assert abs(c['no_acquisition_minimax_regret']['worst_regret']-.2)<1e-12
 try:call('information-robustness','prepare',{'comparison':request,'preview_id':'0'*64});raise AssertionError('bad preview accepted')
 except urllib.error.HTTPError as e:assert e.code in (400,409)
 write('request.json',request);write('comparison.json',saved);write('history.json',history);write('http-verification.json',{'passed':True,'scenario_ids':request['scenario_ids'],'comparison_id':saved['id'],'common_policies':14,'requests_sent':0,'provider_calls':0});print(json.dumps({'passed':True,'id':saved['id']}))

if __name__=='__main__':verify()
