"""Actual open-question development probes; retain every attempt without overwrites."""
import argparse,json,time
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
ROOT=Path(__file__).resolve().parent
OUT=ROOT/'artifacts/agere-appointment-graph-v1'
QUESTIONS={
'cross_domain':'For patient P-3002, connect recorded financial barriers, appointment follow-through and received payment accounts. Which relationships are documented, which are only associations, and what evidence is missing before linking these patterns to deterioration?',
'population':'Across the entire observed population, what appointment follow-through gaps are documented, and how does that evidence relate to available financial-hardship reports? Account for every patient, retain unknowns and dates, and distinguish recorded facts from explanations that still need evidence. Use records received by 2029-08-15.',
}
def api(route,body):
 with urlopen(Request('http://127.0.0.1:8885/api/stack/'+route,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'}),timeout=600) as r:return r.status,json.load(r)
def capture(name,stage):
 target=OUT/(stage+'-'+name+'.json')
 if target.exists():print('Existing capture retained',str(target),flush=True);return
 request={'question':QUESTIONS[name],'live':True};started=time.perf_counter()
 try:status,response=api('ask',request)
 except HTTPError as e:status,response=e.code,json.loads(e.read())
 saved={'request':request,'status':status,'elapsed_seconds':time.perf_counter()-started,'response':response}
 with target.open('x') as f:json.dump(saved,f,indent=2)
 print(json.dumps({'case':name,'status':status,'seconds':saved['elapsed_seconds'],'coverage':response.get('graph',{}).get('coverage'),'operations':[n['operation'] for n in response.get('graph',{}).get('nodes',[])],'new_paid_api_calls':response.get('new_paid_api_calls'),'unmet':response.get('graph',{}).get('unmet_requirements'),'error':response.get('error')}),flush=True)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--case',choices=[*QUESTIONS,'all'],default='all');p.add_argument('--stage',default='candidate');a=p.parse_args()
 for name in QUESTIONS if a.case=='all' else [a.case]:capture(name,a.stage)
