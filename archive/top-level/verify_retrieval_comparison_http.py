"""Development-only six-workflow paired inspection, not clinical evaluation."""
import hashlib,json,urllib.request,urllib.error
from pathlib import Path
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import learning_queue as L,semantic_learning as S
OUT=Path('artifacts/agere-retrieval-comparison-v1');OUT.mkdir(exist_ok=True)
CASES=[
 ('medication','P-1001','What do the medication orders, pharmacy dispensing records and patient use reports say?'),
 ('respiratory','P-1002','What received records describe respiratory symptoms, oxygen use or pulmonary rehabilitation?'),
 ('cardiometabolic','P-1003','What received records describe blood pressure, glycemic measurements and diabetes follow-up?'),
 ('functional','P-1004','What received records describe mobility, daily activities, falls or caregiver help?'),
 ('appointment','P-3002','What do the received appointment bookings and status reports say about follow-through?'),
 ('payer','P-1006','What received enrollment and eligibility reports describe the plan and dates of coverage?')]
meta=json.loads((stack_path()/'run.json').read_text());policy,lm=L.lineage('artifacts/agere-current-source-learning-v1/acquisition/lineage',meta);protected=S.protections()
for _,pid,_ in CASES:assert L.eligibility(pid,policy,protected) is None
pre={'schema':'retrieval-inspection-development-plan/1','cases':[{'workflow':w,'patient':p,'question':q,'family':policy['patient_families'][p]} for w,p,q in CASES],
 'design':'Six authored workflow questions; original training patients only. Descriptive instrumentation checks, no relevance labels or clinical accuracy scoring. No training, tuning, blind-test claim or model promotion.',
 'checks':['identical 24-candidate input to paired selection','same final evidence as ordinary search','exact receipt replay','exact question and scope bound','stage roles explicitly unassessed'],
 'policy_sha256':hashlib.sha256(Path('artifacts/agere-current-source-learning-v1/acquisition/lineage/policy.json').read_bytes()).hexdigest(),
 'new_paid_api_calls':0,'external_disclosure':False}
(OUT/'predeclaration.json').write_text(json.dumps(pre,indent=2)+'\n')
def call(path,body):
 req=urllib.request.Request('http://localhost:8885/api/stack/'+path,json.dumps(body).encode(),{'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=600) as r:return json.load(r)
results=[]
for workflow,pid,question in CASES:
 body={'question':question,'patient':pid}
 paired=call('retrieval-comparison/prepare',body);report=paired['comparison']
 assert report['candidate_set_identical'] and report['clinical_validated'] is False and report['model_promoted'] is False
 ordinary=call('search',body)['retrieval']
 assert report['variants']['bge_reranked']==[r['id'] for r in ordinary['evidence']]
 assert ordinary['comparison_request']==body
 again=call('retrieval-comparison/prepare',body)
 assert again['cache_hit'] and again['comparison']==report
 assert call('retrieval-comparison/inspect',{'id':report['id']})==report
 coverage=call('retrieval-comparison/coverage',{'id':report['id']})
 assert coverage['patient_id']==pid and coverage['comparison_id']==report['id']
 assert sum(r['received_records'] for r in coverage['categories'])==coverage['received_records']
 assert all(r['reranked_selected']<=r['candidates']<=r['received_records'] for r in coverage['categories'])
 (OUT/(workflow+'-coverage.json')).write_text(json.dumps(coverage,indent=2)+'\n')
 assert {r['source']['patient_id'] for r in report['candidates']}<={pid}
 assert all(report['stage_assessment'][k]=='not_run' for k in ('semantic_reader','classifier','planner','service_matching'))
 (OUT/(workflow+'.json')).write_text(json.dumps(report,indent=2)+'\n')
 row={'workflow':workflow,'patient':pid,'id':report['id'],'candidates':len(report['candidates']),'before_selected':len(report['variants']['rrf_baseline']),'after_selected':len(report['variants']['bge_reranked']),
      'entered':len(report['changes']['entered_selected']),'left':len(report['changes']['left_selected']),'same_actual_search':True,'exact_cache_replay':True,'runtime':report['runtime']}
 results.append(row);print(json.dumps(row),flush=True)
for body in ({'question':' '},{'question':'test','labels':['fake']},{'question':'test','patient':'unknown-patient'}):
 try:call('retrieval-comparison/prepare',body)
 except urllib.error.HTTPError as e:assert e.code==400
 else:raise AssertionError('Invalid comparison accepted')
result={'status':'passed','workflow_comparisons':results,'clinical_accuracy_assessed':False,'candidate_recall_assessed':False,'reader_or_classifier_comparisons':0,'model_promotions':0,'new_paid_api_calls':0,'external_actions':0}
(OUT/'http-verification.json').write_text(json.dumps(result,indent=2)+'\n')
print('Six workflow comparisons and invalid-request checks passed.',flush=True)
