"""Measure current work-state API after exact regex compilation caching."""
from urllib.request import urlopen
from pathlib import Path
import time,json
out=Path('artifacts/agere-patient-context-v1');expected=json.loads((out/'work-state.json').read_text())
rows=[]
for label in ['first_request_after_restart','warm_repeat']:
 start=time.monotonic()
 with urlopen('http://localhost:8885/api/stack/work-state?id=693081077ff08e07f21bdb3203a57511d9b0e45b754c5612a50b4674809de1a4',timeout=120) as response:actual=json.load(response)
 elapsed=time.monotonic()-start;assert actual==expected;rows.append({'request':label,'seconds':elapsed,'result_exactly_unchanged':True})
profile=json.loads((out/'work-state-profile.json').read_text());cached=json.loads((out/'work-state-profile-cached.json').read_text());assert profile['layers']==cached['layers'] and profile['packet_current']==cached['packet_current']
result={'http':rows,'profiled_before_seconds':profile['elapsed_seconds'],'profiled_after_seconds':cached['elapsed_seconds'],'scope':'One actual synthetic work item, local host, unchanged output. Profiling overhead included in before/after profiles; not a general production benchmark.','provider_calls':0}
(out/'latency-verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
