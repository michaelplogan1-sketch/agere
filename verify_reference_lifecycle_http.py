"""Live local mapping revision; no reported publisher event is fabricated."""
import json,sys
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import program_work
OUT=Path('artifacts/agere-reference-lifecycle-v1');OLD=json.loads(Path('artifacts/agere-reference-applicability-v1/manifest.json').read_text())
ROOT='http://localhost:8885/api/stack/'
def api(route,body=None):
 with urlopen(Request(ROOT+route,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'}),timeout=180) as r:return json.load(r)
def save(name,v):(OUT/(name+'.json')).write_text(json.dumps(v,indent=2)+'\n')
def tables():
 db=program_work.database(stack_path())
 try:return {t:[list(r) for r in db.execute('SELECT * FROM '+t+' ORDER BY 1,2')] for t in ('work_items','work_events','capability_tasks','capability_events')}
 finally:db.close()
def main():
 key=OLD['reference']['id'];task=OLD['task_id'];target={'kind':'reference_applicability_question','manifest_id':OLD['id'],'question_id':OLD['questions'][0]['id']}
 if sys.argv[-1]=='prepare':
  change={'reference_id':key,'expected_revision':0,'event_kind':'local_mapping_revision','event_date':'2026-10-11','actor':'Agere build','actor_kind':'machine',
    'reason':"Refine Agere's assessment question using the ATS Question 5 implementation discussion, which specifies assessment before remote program commencement. This is local machine curation, not a publisher correction or clinical review.",
    'notice':None,'question_changes':{'assessment':'Does the proposed remote rehabilitation program include a center-based patient assessment before participation, the essential rehabilitation components, and suitable supervision?'}}
  before=api('reference-lifecycle/inspect',{'reference_id':key});p=api('reference-lifecycle/preview',change)
  assert before['state']['revision']==0 and p['impact']['counts']['questions']==6 and p['impact']['counts']['source_contexts']==1
  save('change',change);save('preview',p);save('state-before',before);save('history-before',tables())
  context=json.loads(Path('artifacts/agere-reference-applicability-v1/acquisition.json').read_text())['context_id'];save('original-context-before',api('context?id='+context))
  print(json.dumps({'status':'preview_verified','counts':p['impact']['counts'],'preview_id':p['id']}));return
 state=api('reference-lifecycle/inspect',{'reference_id':key});assert state['state']['status']=='superseded_local_mapping'
 event=state['state']['events'][0];assert event['request']['change']==json.loads((OUT/'change.json').read_text())
 assert api('reference-lifecycle/commit',event['request'])==event
 assert state['reference']==OLD['reference'] and not event['publisher_event_independently_verified']
 rows=api('reference-applicability/inspect',{'task_id':task})['sets'];assert len(rows)==2 and [r['current'] for r in rows]==[False,True]
 assert rows[0]['manifest']==OLD and not rows[0]['questions'][0]['acquisitions'][0]['current']
 assert rows[1]['manifest']['original_target']==OLD['original_target'] and all(not q['acquisitions'] for q in rows[1]['questions'])
 assert len(state['successor_links'])==1 and sum(q['wording_changed'] for q in state['successor_links'][0]['question_map'])==1
 link=state['successor_links'][0];assert api('reference-lifecycle/successor',link['request'])==rows[1]['manifest']
 for route,body in [('readiness/context',{'target':target,'record_ids':['WHL-R-bc8396ef93c0c7e429f737']}),('reference-applicability/prepare',OLD['request']),('reference-lifecycle/commit',{**event['request'],'preview_id':'0'*64})]:
  try:api(route,body);raise AssertionError('Stale request accepted')
  except HTTPError as e:assert e.code in (400,409)
 before=json.loads((OUT/'original-context-before.json').read_text());assert before==api('context?id='+before['id'])
 assert tables()==json.loads((OUT/'history-before.json').read_text())
 for name,v in [('state-after',state),('event',event),('question-sets-after',rows),('successor',rows[1]['manifest'])]:save(name,v)
 report={'status':'passed','event_id':event['id'],'old_reference_id':key,'replacement_reference_id':event['replacement_id'],'old_manifest_id':OLD['id'],'new_manifest_id':rows[1]['manifest']['id'],
  'event_kind':'local_mapping_revision','publisher_correction_or_retraction_asserted':False,'affected_questions':6,'affected_source_contexts':1,'affected_paid_answers':0,'changed_question_wordings':1,
  'explicit_successor_questions':6,'contexts_automatically_transferred':0,'answers_automatically_transferred':0,'original_context_unchanged':True,'all_program_and_task_history_unchanged':True,
  'stale_acquisition_and_old_reference_materialization_rejected':True,'commit_and_successor_idempotency_verified':True,'clinical_review_recorded':False,'external_actions':0,'provider_calls':0}
 save('http-verification',report);print(json.dumps(report,indent=2))
if __name__=='__main__':main()
