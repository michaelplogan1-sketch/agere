"""A new bounded availability-aware request; earlier paid result is preserved."""
import json
from pathlib import Path
from verify_learning_cohorts_http import call, ANSWER

OUT=Path('artifacts/agere-cohort-spans-v1')
CANDIDATE='0ea4cba28330fd10794281cce5087356b33f4fa09919f3d936e13b067b69622b'

def main():
    before=call('/api/stack/evidence-needs',{'answer_id':ANSWER,'selectors':[]})
    request={'candidate_id':CANDIDATE,'passage_rank':1}
    preview=call('/api/stack/question-span-availability/preview?candidate_id='+CANDIDATE+'&passage_rank=1')
    old_record='WHM-R-6559d0bf57036fa778e32a'
    availability=next(r for r in preview['source_availability']['candidate_record_availability'] if r['record_id']==old_record)
    assert availability['status']=='received' and not availability['content_submitted']
    result=call('/api/stack/question-span-availability',request)
    (OUT/'question-availability-proposal.json').write_text(json.dumps(result,indent=2))
    replay=call('/api/stack/question-span-availability',request)
    assert replay['id']==result['id'] and replay['cache_hit'] and replay['new_paid_api_calls']==0
    selected=[]
    if result['proposals']:
        task=call('/api/stack/question-span-availability/select',{'receipt_id':result['id'],'proposal_id':result['proposals'][0]['id']})
        inspected=call('/api/stack/question-learning?id='+task['task_id'])
        assert inspected['machine_acquisition'] and not inspected['qualification']['human_reviewed'];selected.append(task['task_id'])
    after=call('/api/stack/evidence-needs',{'answer_id':ANSWER,'selectors':[]});assert before==after
    report={'receipt_id':result['id'],'received_but_unsubmitted_reference':availability,'question_relation':result['question_relation'],
        'scope_reason':result['scope_reason'],'unresolved':result['unresolved'],'tasks':selected,
        'original_question_unchanged':True,'exact_replay':True,'new_paid_api_calls':result['new_paid_api_calls'],
        'usage':result['usage'],'human_reviews_created':0,'clinical_actions':0}
    (OUT/'question-availability-http.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))

if __name__=='__main__':main()
