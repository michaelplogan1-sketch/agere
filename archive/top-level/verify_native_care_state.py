"""Free-form product development requests; no evaluator-supplied graph."""
import argparse,json,time
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
OUT=Path(__file__).resolve().parent/'artifacts/agere-care-reference-state-v1'
QUESTIONS={'patient': 'For P-1001, show the documented care requests, reported bookings and evidence of performed services, keeping each request separate and unresolved completion visible. Include the latest reported transport difficulty. A limited service report is not proof that all requested care is complete.', 'population': 'Across the entire observed population, summarize care requests, reported bookings and evidence of performed services by linked request, keeping every patient in the denominator and unobserved completion separate from confirmed nonattendance. Also show the latest reports of difficulty paying for care. Do not infer clinical eligibility, causality or missing care.', 'prior_population': "Across the entire observed population, what care requests and evidence of performed services were documented and received before each patient's first admission notification from 2026-01-01 through 2029-06-30, considering records received by 2029-06-30? Keep every patient, separate requests, preserve late and unresolved evidence, and distinguish a performed limited service from completion of all requested care."}

def capture(name,stage):
 target=OUT/(stage+'-'+name+'.json')
 if target.exists(): raise ValueError('Preserve existing capture')
 body={'question':QUESTIONS[name],'live':True};t=time.perf_counter()
 try:
  with urlopen(Request('http://127.0.0.1:8885/api/stack/ask',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'}),timeout=600) as r:status,response=r.status,json.load(r)
 except HTTPError as e:status,response=e.code,json.loads(e.read())
 value={'request':body,'status':status,'elapsed_seconds':time.perf_counter()-t,'response':response}
 with target.open('x') as f:json.dump(value,f,indent=2)
 print(json.dumps({'case':name,'status':status,'seconds':value['elapsed_seconds'],'calls':response.get('new_paid_api_calls'),'result_id':response.get('result_id'),'operations':[r['node']['operation'] for r in response.get('results',[])],'error':response.get('error')}),flush=True)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--case',choices=[*QUESTIONS,'all'],default='all');p.add_argument('--stage',default='candidate');a=p.parse_args()
 for name in QUESTIONS if a.case=='all' else [a.case]:capture(name,a.stage)
