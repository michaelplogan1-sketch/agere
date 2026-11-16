import json
from pathlib import Path
from urllib.request import urlopen
OUT=Path('artifacts/agere-availability-synthesis-v1')
KEY=json.loads((OUT/'http-verification.json').read_text())['internal_task_id']
results={}
for kind,key in [('current',KEY),('historical','a102c3a9d7c9b460a2e6665c048b78ba6e6c76c9d3ff3705fbfeeecacd3b6443')]:
    with urlopen('http://localhost:8885/api/stack/capabilities/availability?task_id='+key,timeout=180) as response:r=json.load(response)
    assert not r['clinical_execution_authorized'] and r['new_paid_api_calls']==0
    if kind=='current':assert r['answers'][0]['availability']==json.loads((OUT/'answer.json').read_text())['source_availability']
    else:assert all(a['availability_status']=='not_recorded_in_historical_answer' for a in r['answers'])
    results[kind]=r
(OUT/'work-availability-http.json').write_text(json.dumps(results,indent=2)+'\n')
print(json.dumps({k:{'task_id':v['task_id'],'answers':len(v['answers']),'status':[a['availability_status'] for a in v['answers']]} for k,v in results.items()},indent=2))
