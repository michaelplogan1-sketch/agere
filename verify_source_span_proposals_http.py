"""Live source proposals in the original unresolved-question path; no reviews."""
import json
from pathlib import Path
from urllib.error import HTTPError
from verify_learning_cohorts_http import call

CASES=[('outside_care','93346f7f18d3ddfe2bdebf8ac940d2154a88e550002756109d1aa817e0b5b7f1',3),
       ('reassessment','0ea4cba28330fd10794281cce5087356b33f4fa09919f3d936e13b067b69622b',1)]

def main():
    root=Path('artifacts/agere-source-span-proposals-v1');reports=[]
    for name,candidate,rank in CASES:
        c=call('/api/stack/evidence-needs/candidates?id='+candidate)
        before=call('/api/stack/evidence-needs',{'answer_id':c['binding']['answer_id'],'selectors':[]})
        request={'candidate_id':candidate,'passage_rank':rank}
        preview=call('/api/stack/source-span-proposals/preview?candidate_id='+candidate+'&passage_rank='+str(rank))
        assert preview['request_bytes']<=20000 and preview['complete_received_field']
        result=call('/api/stack/source-span-proposals',request)
        (root/(name+'-proposal.json')).write_text(json.dumps(result,indent=2)+'\n')
        replay=call('/api/stack/source-span-proposals',request)
        assert result['id']==replay['id'] and replay['cache_hit'] and replay['new_paid_api_calls']==0
        tasks=[]
        if result['proposals']:
            selected=call('/api/stack/source-span-proposals/select',{'receipt_id':result['id'],'proposal_id':result['proposals'][0]['id']})
            task=call('/api/stack/question-learning?id='+selected['task_id'])
            assert not task['qualification']['human_reviewed'] and task['machine_acquisition']
            assert task['context']['target_quote']==result['proposals'][0]['quote']
            tasks.append(task['id'])
        after=call('/api/stack/evidence-needs',{'answer_id':c['binding']['answer_id'],'selectors':[]});assert before==after
        reports.append({'case':name,'receipt':result['id'],'relation':result['question_relation'],
            'proposals':len(result['proposals']),'abstained':result['abstained'],'question_tasks':tasks,
            'original_question_unchanged':True,'exact_replay':True,'request_bytes':result['request_bytes'],
            'new_paid_api_calls':result['new_paid_api_calls'],'usage':result['usage']})
    rejected=[]
    for body in ({'candidate_id':CASES[0][1],'passage_rank':1}, {'candidate_id':CASES[0][1],'passage_rank':3,'labels':{}},
                 {'candidate_id':CASES[0][1],'passage_rank':True}):
        try:call('/api/stack/source-span-proposals',body)
        except HTTPError as exc:assert exc.code==400;rejected.append(body)
        else:raise AssertionError('Expected exact-source/schema rejection')
    current=call('/api/stack/current-learning');assert current['source_reviews']['recorded_review_revisions']==0
    report={'cases':reports,'rejections':rejected,'human_reviews_created':0,'clinical_actions':0,'original_queue_size':current['queue']['selected']}
    (root/'http-verification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))

if __name__=='__main__':main()
