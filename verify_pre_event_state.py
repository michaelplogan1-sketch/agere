"""Free-form product development requests; no evaluator-supplied graph."""
import argparse,json,time
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
OUT=Path(__file__).resolve().parent/'artifacts/agere-pre-event-state-v1'
QUESTIONS={
 'indexed_patient':'For SYN-67F2F871-F14, what food and transport difficulties were documented and received before the first admission notification dated between 2029-01-01 and 2029-06-30, using only records received by 2029-06-30? Separate late reports and unresolved timing. Do not infer a cause or clinical risk.',
 'earlier_population':'Across the entire observed population, what food and transport difficulties were documented and received before each patient\'s first admission notification dated from 2026-01-01 through 2029-06-30, considering records received by 2029-06-30? Retain patients with no index and separate reports arriving after the event. This is a retrospective evidence question, not a causal or risk estimate.',
 'patient':'For P-3002, what food and transport difficulties were documented and received before the first admission notification dated between 2029-01-01 and 2029-06-30, using only records received by 2029-06-30? Separate late reports and unresolved timing. Do not infer a cause or clinical risk.',
 'population':'Across the entire observed population, what food and transport difficulties were documented and received before each patient\'s first admission notification dated between 2029-01-01 and 2029-06-30, using only records received by 2029-06-30? Keep every patient in the denominator, including those without an identifiable index, and expose late reports and unresolved dates. Do not infer causality or risk.',
 'care_gap':'Across the entire observed population, what evidence of nephrology care completion was documented and received before the first discharge on explicit clinical dates between 2029-01-01 and 2029-06-30, using only records received by 2029-06-30? Keep all patients and unknown timing visible. Distinguish an unobserved completion report from an established care gap; do not substitute notification dates for clinical dates.'}

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
