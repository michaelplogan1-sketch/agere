"""Open-question development execution; preserve each attempt and its cost."""
import argparse,json,time
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from verify_appointment_graph import QUESTIONS as PREVIOUS
OUT=Path(__file__).resolve().parent/'artifacts/agere-native-assertions-v1'
QUESTIONS={**PREVIOUS,'social_context':'Across the entire observed population, what food and transport difficulties have patients or their caregivers reported, and how do those reports overlap with documented appointment follow-through gaps? Keep every patient and unknown in view, use the latest dated reports received by 2029-08-15, and distinguish recorded co-occurrence from explanations or causal effects.'}

def capture(name,stage):
 target=OUT/(stage+'-'+name+'.json')
 if target.exists():print('Existing capture retained: '+str(target),flush=True);return
 body={'question':QUESTIONS[name],'live':True};t=time.perf_counter()
 try:
  with urlopen(Request('http://127.0.0.1:8885/api/stack/ask',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'}),timeout=600) as r:status,response=r.status,json.load(r)
 except HTTPError as e:status,response=e.code,json.loads(e.read())
 value={'request':body,'status':status,'elapsed_seconds':time.perf_counter()-t,'response':response}
 with target.open('x') as f:json.dump(value,f,indent=2)
 print(json.dumps({'case':name,'status':status,'seconds':value['elapsed_seconds'],'calls':response.get('new_paid_api_calls'),'result_id':response.get('result_id'),'coverage':response.get('answer_coverage'),'operations':[r['node']['operation'] for r in response.get('results',[])],'error':response.get('error')}),flush=True)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--case',choices=[*QUESTIONS,'all'],default='all');p.add_argument('--stage',default='candidate');a=p.parse_args()
 for name in QUESTIONS if a.case=='all' else [a.case]:capture(name,a.stage)
