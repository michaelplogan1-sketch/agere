import json
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
OUT=Path('artifacts/agere-reference-scope-v1');TASK='693081077ff08e07f21bdb3203a57511d9b0e45b754c5612a50b4674809de1a4'
def api(route,body=None):
 with urlopen(Request('http://localhost:8885'+route,data=json.dumps(body).encode() if body else None,headers={'Content-Type':'application/json'}),timeout=180) as r:return json.load(r)
before=api('/api/stack/work-state?id='+TASK)
view=api('/api/stack/reference-packet/list',{'task_id':TASK});assert not view['followups'] and not view['answers']
selected=json.loads((OUT/'selection.json').read_text())
try:
 api('/api/stack/reference-packet/prepare',{'run_id':'0'*64,'review_revision':1,'selection_id':selected['id'],'note':'No live review: verify missing credential rejection only','request_id':'reference-scope-live-auth-rejection'})
 raise AssertionError('No credential accepted')
except HTTPError as e:assert e.code==403
assert before==api('/api/stack/work-state?id='+TASK)
result={'status':'passed','new_live_review_or_followup_records':0,'missing_credential_rejected':True,'live_work_unchanged':True,'scope':'Positive authenticated lifecycle is verified only on disposable engineering fixtures.'}
(OUT/'packet-live-verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
