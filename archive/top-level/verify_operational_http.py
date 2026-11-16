import json
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
OUT=Path('artifacts/agere-operational-context-v1')
def api(route,body):
 with urlopen(Request('http://localhost:8885/api/stack/'+route,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'}),timeout=240) as r:return json.load(r)
def save(name,row):(OUT/(name+'.json')).write_text(json.dumps(row,indent=2)+'\n')
def reject(route,body):
 try:api(route,body)
 except HTTPError as e:assert e.code in (400,409);return
 raise AssertionError('Invalid request accepted')
def main():
 result={}
 for kind in ('resource','dependency'):
  req=json.loads((OUT/(kind+'-request.json')).read_text());ctx=api('operational-context/preview',req);save(kind+'-preview',ctx)
  row=api('operational-context/prepare',{**req,'preview_id':ctx['id']});assert row==ctx;assert api('operational-context/prepare',{**req,'preview_id':ctx['id']})==row;save(kind+'-context',row)
  assert api('operational-context/inspect',{'id':row['id']})['current'];history=api('operational-context/history',{'parent_type':kind,'parent_id':req['parent_id']});assert len(history['contexts'])==1 and history['contexts'][0]['current']
  original=api(kind+'-capacity/inspect' if kind=='resource' else 'precedence-capacity/inspect',{'id':req['parent_id']});assert original['current']
  sensitivity={'context_id':row['id'],'question':'How would five additional minutes per source-review task change this selected batch?','assumption_source':'Engineering sensitivity: original low, central and high task durations plus five minutes. This is supplied, not measured labor.','applicability_rationale':'Received office and other encounters are not these internal source-review tasks. Their intervals cannot calibrate staff effort. Five additional minutes is a hypothetical complexity allowance; comparability, real effort and interruptions remain unmeasured.','durations':{id:{key:v+5 for key,v in d.items()} for id,d in original['duration_assumptions'].items()}}
  sc=api('operational-sensitivity/compare',sensitivity);save(kind+'-sensitivity-request',sensitivity);save(kind+'-sensitivity',sc);assert api('operational-sensitivity/compare',sensitivity)==sc;assert api('operational-sensitivity/inspect',{'id':sc['id']})['current']
  assert len(api('operational-sensitivity/history',{'context_id':row['id']})['scenarios'])==1
  for case,comp in sc['comparisons'].items():assert comp['original']==original['comparisons'][case]['proposed' if kind=='resource' else 'with_dependencies']
  assert row['tasks_changed']==sc['tasks_changed']==0 and not sc['empirically_calibrated']
  reject('operational-context/prepare',{**req,'preview_id':'0'*64});reject('operational-sensitivity/compare',{**sensitivity,'durations':{}})
  result[kind]={'context_id':ctx['id'],'sensitivity_id':sc['id'],'patient_ids':ctx['patient_ids'],'denominators':{r['patient_id']:r['denominators'] for r in row['reports']},'central_original_finish':sc['comparisons']['central']['original']['whole_batch_clearance_minutes'],'central_supplied_finish':sc['comparisons']['central']['supplied_duration_scenario']['whole_batch_clearance_minutes'],'central_completed_difference':sc['comparisons']['central']['completion_count_difference']}
  print(kind,'passed',flush=True)
 result.update(status='passed',tasks_changed=0,provider_calls=0,external_actions=0,empirically_calibrated=False);save('http-verification',result)
if __name__=='__main__':main()
