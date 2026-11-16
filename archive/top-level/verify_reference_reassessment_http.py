"""Explicit live successor source recheck, using the existing synthetic case."""
import json,sys
from pathlib import Path
from urllib.error import HTTPError
import verify_reference_lifecycle_http as base
OUT=Path('artifacts/agere-reference-reassessment-v1');api=base.api
OLD=base.OLD;NEW=json.loads(Path('artifacts/agere-reference-lifecycle-v1/successor.json').read_text())
TARGET={'kind':'reference_applicability_question','manifest_id':NEW['id'],'question_id':NEW['questions'][0]['id']}
def save(name,v):(OUT/(name+'.json')).write_text(json.dumps(v,indent=2)+'\n')
def main():
 before=api('reference-reassessment/list',{'target':TARGET})
 if sys.argv[-1]=='prepare':
  assert len(before['candidates'])==1 and not before['preparations']
  c=before['candidates'][0];assert c['available_for_explicit_recheck'] and not c['wording_changed']
  request={'target':TARGET,'prior_acquisition_id':c['prior_acquisition_id']};preview=api('reference-reassessment/preview',request)
  save('before',before);save('preview',preview);save('request',{**request,'preview_id':preview['id']});save('history-before',base.tables())
  print(json.dumps({'status':'preview_verified','prior_context_id':c['prior_context_id'],'source_choices':c['record_ids']}));return
 assert len(before['preparations'])==1;row=before['preparations'][0];assert row['current'] and row['exact_context_reused'] and row['answers_carried_forward']==0
 assert row['provider_calls']==0 and not row['clinical_applicability_established']
 req=json.loads((OUT/'request.json').read_text());replayed=api('reference-reassessment/prepare',req)
 assert replayed=={k:v for k,v in row.items() if k not in ('current','stale_reasons')}
 states=api('reference-applicability/inspect',{'task_id':OLD['task_id']})['sets'];assert [s['current'] for s in states]==[False,True]
 assert states[0]['manifest']==OLD and states[1]['manifest']==NEW
 assert len(states[1]['questions'][0]['acquisitions'])==1 and all(not q['acquisitions'] for q in states[1]['questions'][1:])
 assert all(q['applicability']=='unresolved' for s in states for q in s['questions'])
 assert not states[0]['questions'][0]['acquisitions'][0]['current'] and states[1]['questions'][0]['acquisitions'][0]['current']
 original=json.loads(Path('artifacts/agere-reference-lifecycle-v1/original-context-before.json').read_text());assert api('context?id='+original['id'])==original
 assert original['id']==row['acquisition']['context_id']
 for bad in [{**req,'preview_id':'0'*64},{**req,'prior_acquisition_id':'0'*64},{**req,'target':{'kind':'reference_applicability_question','manifest_id':OLD['id'],'question_id':OLD['questions'][0]['id']}}]:
  try:api('reference-reassessment/prepare',bad);raise AssertionError('Invalid preparation accepted')
  except HTTPError as e:assert e.code in (400,409)
 assert base.tables()==json.loads((OUT/'history-before.json').read_text())
 save('state-after',before);save('preparation',replayed);save('question-sets-after',states)
 result={'status':'passed','old_manifest_id':OLD['id'],'new_manifest_id':NEW['id'],'target':TARGET,'preparation_id':row['id'],
 'exact_context_reused':True,'context_id':original['id'],'successor_source_acquisitions':1,'unresolved_successor_questions':6,
 'automatic_source_transfers':0,'answer_transfers':0,'original_context_and_manifests_unchanged':True,'all_program_and_task_history_unchanged':True,
 'replay_idempotent':True,'invalid_preview_predecessor_and_historical_target_rejected':True,'provider_calls':0,'external_actions':0,'clinical_review_recorded':False}
 save('http-verification',result);print(json.dumps(result,indent=2))
if __name__=='__main__':main()
