"""Actual joint scenario; no providers or documentary/task changes."""
import json,sys,time,urllib.request,urllib.error
from pathlib import Path
ROOT=Path(__file__).resolve().parent;OUT=ROOT/'artifacts/agere-shared-information-v1';OUT.mkdir(exist_ok=True)
PORTFOLIO='72a14f250440632549946c522f97e1f87ee17a911518d2571104a6e483e3ae44'

def call(endpoint,data):
 req=urllib.request.Request('http://localhost:8885/api/stack/shared-information/'+endpoint,data=json.dumps(data).encode(),headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=240) as r:return json.load(r)

def write(name,value):(OUT/name).write_text(json.dumps(value,indent=2)+'\n')

def prepare():
 start=time.monotonic();c=call('choices',{'portfolio_id':PORTFOLIO});write('choices.json',c)
 # Exact separate account-location requests for two existing medication questions.
 ids=['6358b3bec3f6fc3d77ccfa75c5b622d9b7db31b5ceb698629056ddd8d9df7d68','b7563e968b225cf438f7a48a1965b3e5e92b3114819b3739558d0e6e355b7711']
 linked={r['receipt_id'] for i in c['requests'] if i['id'] in ids for r in i['requirements']};qs=[q['receipt_id'] for q in c['questions'] if q['receipt_id'] in linked]
 a={'prior':{'Current records could support review':.5,'Additional documentation needed':.5},
 'utilities':{'Review current records':{'Current records could support review':1,'Additional documentation needed':0},'Plan documentation follow-up':{'Current records could support review':0,'Additional documentation needed':1}},
 'requests':{rid:{'available':True,'cost':.1,'basis':'Hypothetical engineering availability and cost, not observed staff capacity or patient expense'} for rid in ids},
 'outcomes':{'Both report available':{rid:'Reported available' for rid in ids},'Both report unavailable':{rid:'Reported unavailable' for rid in ids}},
 'likelihoods':{'Current records could support review':{'Both report available':.8,'Both report unavailable':.2},'Additional documentation needed':{'Both report available':.2,'Both report unavailable':.8}},
 'utility_scale':'hypothetical documentary review usefulness units','assumption_source':'Engineering illustration only; no probabilities or utilities learned from this patient',
 'objective':'Compare information for one documentation-review choice across two medication questions',
 'joint_basis':'Perfectly correlated report availability is explicitly assumed for illustration; discordant vectors have probability zero. This is not established by the received sources.'}
 req={'portfolio_id':PORTFOLIO,'question_ids':qs,'rationale':'Demonstrate why correlated requests must not have their information values added', 'assumptions':a}
 write('request.json',req);write('preview.json',call('preview',req));write('timing.json',{'choices_and_preview_seconds':time.monotonic()-start});print('Prepared current preview; scenario not saved')

def verify():
 req=json.loads((OUT/'request.json').read_text());preview=call('preview',req);saved=call('prepare',{'scenario':req,'preview_id':preview['id']});assert saved==preview
 assert call('prepare',{'scenario':req,'preview_id':preview['id']})==saved
 history=call('list',{'portfolio_id':PORTFOLIO});assert len(history['scenarios'])==1 and history['scenarios'][0]['current']
 rejected=[]
 for body in [{'scenario':req,'preview_id':'0'*64},{**req,'clinical_execution_authorized':True}]:
  try:call('prepare' if 'scenario' in body else 'preview',body);raise AssertionError('Unexpected acceptance')
  except urllib.error.HTTPError as e:assert e.code in (400,409);rejected.append(e.code)
 calc=saved['calculation'];assert calc['baseline_utility']==.5 and len(calc['bundles'])==4
 assert abs(calc['best_single_or_none']['net_value']-.2)<1e-12
 both=next(b for b in calc['bundles'] if len(b['request_ids'])==2);assert abs(both['net_value']-.1)<1e-12 and abs(both['gross_value']-.3)<1e-12
 write('scenario.json',saved);write('history.json',history);write('http-verification.json',{'passed':True,'id':saved['id'],'question_count':len(saved['questions']),'request_count':len(saved['selected_requests']),'rejections':rejected,'provider_calls':0,'requests_sent':0});print(json.dumps({'passed':True,'id':saved['id']}))

if __name__=='__main__':{'prepare':prepare,'verify':verify}[sys.argv[1]]()
