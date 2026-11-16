"""Attach exact saved machine output without modifying the internal review."""
import json
from pathlib import Path
from verify_stable_references_http import api

OUT=Path('artifacts/agere-followup-answers-v1')
TASK='a102c3a9d7c9b460a2e6665c048b78ba6e6c76c9d3ff3705fbfeeecacd3b6443'
FOLLOWUP='1dd91c30d1b453db2674f423eac4f29ec71e5d77df7253d12492b50faf8e2e6c'
ANSWER='fc6d15d4fd41a67aafd6615ba5c5c34c53688afc6f7373f3e69357abb3c59815'


if __name__=='__main__':
 OUT.mkdir(exist_ok=True)
 before=api('/api/stack/internal-execution?id='+TASK)
 request={'task_id':TASK,'followup_id':FOLLOWUP,'answer_id':ANSWER}
 answer=api('/api/stack/case-synthesis?id='+ANSWER)
 rejected=[]
 for invalid in [{**request,'answer_id':'d7d96be6f304b5649e24c329e38648d1ef6475c579c1645498dcd7fd177c8799'},
                 {**request,'answer_id':answer['parent_answer_id']},
                 {**request,'requirement_resolved':True}]:
  try:api('/api/stack/packet-followup/answer',invalid)
  except RuntimeError as exc:
   assert str(exc).startswith('400 ');rejected.append(str(exc))
  else:raise AssertionError('Invalid attachment accepted')
 result=api('/api/stack/packet-followup/answer',request)
 assert api('/api/stack/packet-followup/answer',request)==result
 state=api('/api/stack/packet-followup/answers?id='+TASK)
 rows=[r for r in state['answers'] if r['id']==result['id']]
 assert len(rows)==1 and rows[0]['current']
 after=api('/api/stack/internal-execution?id='+TASK);assert before==after
 for flag in ['requirement_resolved','human_review_recorded','care_delivered','clinical_execution_authorized']:assert result[flag] is False
 assert result['new_paid_api_calls']==0
 summary={'actual_http_verified':True,'attachment_id':result['id'],'task_id':TASK,'followup_id':FOLLOWUP,'answer_id':ANSWER,
  'exact_binding_current':True,'idempotent_replay':True,'rejected_context_or_incomplete_answer_or_forged_resolution':len(rejected),
  'original_internal_execution_and_review_unchanged':True,'task_revision':before['task_revision'],
  'summary':result['summary'],'new_paid_api_calls':0,'human_reviews_created':0,'care_delivered':False,'clinical_accuracy_validated':False}
 for name,value in [('live-attachment',result),('live-state',state),('internal-state-before',before),('internal-state-after',after),('rejected-requests',rejected),('http-verification',summary)]:
  (OUT/(name+'.json')).write_text(json.dumps(value,indent=2))
 print(json.dumps(summary,indent=2))
