import json,urllib.request,urllib.error
from pathlib import Path
out=Path('/Users/mikel/clinical-insights-local/artifacts/agere-program-accountability-v1')
def post(path,body):
 req=urllib.request.Request('http://127.0.0.1:8885'+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=120) as r:return json.load(r)
cohort=post('/api/stack/cohort',{});assert cohort['counts']['matched']==1614
result=post('/api/stack/work/prepare',{'program':'hf_review','accountability':'program_payment_evidence'})
assert result['accountability_assessments']==result['counts']['selected']==1614
baseline=post('/api/stack/work/prepare',{'program':'hf_review'})
assert result['counts']==baseline['counts'] and result['new_paid_api_calls']==0
assert result['accountability_review']['requests']==14620
for bad in [{'program':'unknown','accountability':'program_payment_evidence'},{'program':'hf_review','accountability':'unregistered'}]:
 try:post('/api/stack/work/prepare',bad);raise AssertionError('Invalid input accepted')
 except urllib.error.HTTPError as e:assert e.code==400
(out/'workspace-proposal.json').write_text(json.dumps(result,indent=2)+'\n')
report={'passed':True,'synthetic':True,'patients_selected':1614,'clinical_candidates':result['counts']['candidates'],'candidate_counts_unchanged_from_existing_workflow':True,'accountability_assessments':1614,'source_review_requests':14620,'invalid_program_and_contract_rejected':True,'cohort_endpoint_passed':True,'new_paid_api_calls':0,'work_items_created':0,'requests_sent':0}
(out/'workspace-http-verification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
