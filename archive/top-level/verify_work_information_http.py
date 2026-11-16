"""Local HTTP scenario verification; no provider or patient action."""
import json,sys
from pathlib import Path
from urllib.error import HTTPError
from verify_reference_lifecycle_http import api,tables
OUT=Path('artifacts/agere-work-information-v1')
def write(name,obj):(OUT/(name+'.json')).write_text(json.dumps(obj,indent=2)+'\n')
def read(name):return json.loads((OUT/(name+'.json')).read_text())
def reject(body):
 try:api('work-information/preview',body)
 except HTTPError as e:assert e.code in (400,409);return
 raise AssertionError('Invalid information input accepted')
def request():
 m=json.loads(Path('artifacts/agere-functional-options-v1/manifest.json').read_text())
 states=['Existing assessment can answer','New assessment is needed'];obs=['Reported available','Reported unavailable']
 return {'target':{'kind':'reference_applicability_question','manifest_id':m['id'],'question_id':m['questions'][1]['id']},
  'rationale':'Explore hypothetical information value for the existing assessment-review question. No current assessment quality or need is inferred.',
  'assumptions':{'prior':dict.fromkeys(states,.5),'utilities':{'Retrieve assessment':dict(zip(states,[1,0])),'Plan assessment review':dict(zip(states,[0,1]))},
   'utility_scale':'hypothetical review usefulness units',
   'assumption_source':'Illustrative review-planning assumptions entered by Agere build; not estimated from patient records. Equal state probabilities and channel reliability are hypothetical.',
   'channels':{'Check record availability':{'available':True,'cost':.1,'basis':'Assume 80 percent discriminating reliability and availability for this illustration only.',
    'likelihoods':{states[0]:dict(zip(obs,[.8,.2])),states[1]:dict(zip(obs,[.2,.8]))}},
    'Repeat current view':{'available':True,'cost':.05,'basis':'Assume this repeat adds no information; not an empirical measurement.',
    'likelihoods':{s:dict.fromkeys(obs,.5) for s in states}}}}}
if sys.argv[-1]=='prepare':
 req=request();write('request',req);write('history-before',tables());write('question-before',api('readiness/inspect',{'target':req['target']}))
 p=api('work-information/preview',req);write('preview',p);assert not p['automatic_acquisition'] and not p['question_resolved']
 print(json.dumps({'status':'previewed','id':p['id'],'question':p['question'],'baseline':p['calculation']['current_expected_utility']}))
elif sys.argv[-1]=='verify':
 req=read('request');rows=api('work-information/list',{'target':req['target']})['scenarios'];assert len(rows)==1 and rows[0]['current']
 row={k:v for k,v in rows[0].items() if k not in ('current','stale_reasons')};assert row==read('preview')
 assert row==api('work-information/prepare',{'scenario':req,'preview_id':row['id']})
 assert tables()==read('history-before');assert api('readiness/inspect',{'target':req['target']})==read('question-before')
 reject({**req,'clinical_probability_calibrated':True})
 bad=json.loads(json.dumps(req));bad['assumptions']['prior']['Existing assessment can answer']=.9;reject(bad)
 try:api('work-information/prepare',{'scenario':req,'preview_id':'changed'})
 except HTTPError as e:assert e.code in (400,409)
 else:raise AssertionError('Changed preview accepted')
 c=row['calculation'];channels={x['channel']:x for x in c['channels']};assert abs(channels['Check record availability']['net_value']-.2)<1e-12
 assert channels['Repeat current view']['gross_value']==0 and c['current_expected_utility']==.5
 write('scenario',row);report={'status':'passed','scenario_id':row['id'],'patient_id':row['patient_id'],'baseline_expected_utility':.5,'hypothetical_net_information_value':.2,'uninformative_gross_value':0,'utility_scale':c['utility_scale'],'question_and_work_unchanged':True,'provider_calls':0,'automatic_acquisition':False,'clinical_validation':False}
 write('http-verification',report);print(json.dumps(report,indent=2))
