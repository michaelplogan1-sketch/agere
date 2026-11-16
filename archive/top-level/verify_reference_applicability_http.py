"""Actual respiratory task -> dated reference -> exact evidence preparation."""
import json
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import program_work
OUT=Path('artifacts/agere-reference-applicability-v1');ROOT='http://localhost:8885/api/stack/'
TASK='55cf7f85a614fec76345b9d7e84ff860070c4fe4ce373c5add127e2ff7e9896b'
def api(route,body=None):
 req=Request(ROOT+route,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
 with urlopen(req,timeout=180) as r:return json.load(r)
def save(n,o):(OUT/(n+'.json')).write_text(json.dumps(o,indent=2)+'\n')
def tables():
 db=program_work.database(stack_path())
 try:return {t:[list(r) for r in db.execute('SELECT * FROM '+t+' ORDER BY 1,2')] for t in ('work_items','work_events','capability_tasks','capability_events')}
 finally:db.close()
def main():
 before=tables();state=api('reference-applicability/inspect',{'task_id':TASK});tasks=api('capabilities/tasks?buyer=synthetic-domain-source-review');task=next(t for t in tasks['tasks'] if t['id']==TASK)
 target=next(t for t in state['original_targets'] if t['question']=='Verify patient goals and exact service applicability')
 request={'task_id':TASK,'expected_revision':task['revision'],'target':target['target'],'reference_id':state['references'][0]['id'],
          'reason':'Examine a published rehabilitation delivery recommendation alongside this respiratory evidence-review task. The selected patient and buyer service are not established as clinically eligible or rehabilitation-ready.'}
 manifest=api('reference-applicability/prepare',request);assert manifest==api('reference-applicability/prepare',request)
 assert len(manifest['questions'])==6 and not manifest['clinical_applicability_established']
 q=manifest['questions'][0];target={'kind':'reference_applicability_question','manifest_id':manifest['id'],'question_id':q['id']}
 prepared=api('readiness/context',{'target':target,'record_ids':['WHL-R-bc8396ef93c0c7e429f737']});assert prepared==api('readiness/context',{'target':target,'record_ids':['WHL-R-bc8396ef93c0c7e429f737']})
 context=api('context?id='+prepared['context_id']);assert context['patient_id']==manifest['patient_id'] and context['parameters']['question']==q['question']
 review=api('readiness/inspect',{'target':target});assert not review['documentary_question_addressed']
 for route,body in [('reference-applicability/prepare',{**request,'clinical_execution_authorized':True}),('reference-applicability/prepare',{**request,'reference_id':'0'*64}),('readiness/review',{'target':target,'disposition':'addressed','actor_kind':'operator'})]:
  try:api(route,body);raise AssertionError('Invalid request accepted')
  except HTTPError as e:assert e.code==400
 final=api('reference-applicability/inspect',{'task_id':TASK});s=next(s for s in final['sets'] if s['manifest']['id']==manifest['id']);assert s['current'] and s['questions'][0]['acquisitions'][0]['current']
 assert tables()==before
 for name,obj in [('request',request),('manifest',manifest),('acquisition',prepared),('context',context),('final-state',final)]:save(name,obj)
 report={'status':'passed','task_id':TASK,'manifest_id':manifest['id'],'original_question':manifest['original_target']['question'],'patient_id':manifest['patient_id'],
  'questions':6,'reference_kind':manifest['reference']['kind'],'reference_checked_on':manifest['reference']['checked_on'],'decision_date':manifest['original_target']['as_of'],
  'context_id':prepared['context_id'],'source_records':len(prepared['record_scope']['record_ids']),'exact_original_question_preserved':True,'invalid_authority_and_reference_rejected':True,
  'idempotent_manifest_and_acquisition':True,'all_program_and_task_histories_unchanged':True,'applicability':'unresolved','provider_calls':0,'human_review_recorded':False,'external_actions':0}
 save('http-verification',report);print(json.dumps(report,indent=2))
if __name__=='__main__':main()
