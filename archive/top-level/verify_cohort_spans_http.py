"""End-to-end saved proposal/task paths; never repeats a paid acquisition."""
import json
from pathlib import Path
from urllib.error import HTTPError
from verify_learning_cohorts_http import call, ANSWER

COHORT='8021f42a502aab70d962ecc3fce0fe3b4c1d706800e69e9135958bd071d5d02b'
OUT=Path('artifacts/agere-cohort-spans-v1')

def main():
    before=call('/api/stack/evidence-needs',{'answer_id':ANSWER,'selectors':[]})
    r=call('/api/stack/cohort-spans?cohort_id='+COHORT)
    assert r['total']==125 and sum(r['counts'].values())==125 and r['counts']['excluded']==7
    item=next(x for x in r['rows'] if x['stream']=='billing_authorizations' and x['status']=='proposed')
    detail=call('/api/stack/cohort-spans/item?cohort_id='+COHORT+'&item_id='+item['id'])
    assert detail['saved'] and detail['text'] and detail['source_availability']['other_record_contents_not_submitted']>0
    replay=call('/api/stack/cohort-spans',{'cohort_id':COHORT,'item_id':item['id']})
    assert replay['cache_hit'] and replay['new_paid_api_calls']==0
    request={'cohort_id':COHORT,'item_id':item['id'],'proposal_id':replay['proposals'][0]['id']}
    task=call('/api/stack/cohort-spans/select',request)
    assert task==call('/api/stack/cohort-spans/select',request)
    inspected=call('/api/stack/cohort-spans/task?cohort_id='+COHORT+'&id='+task['id'])
    assert not inspected['qualification']['human_reviewed'] and not inspected['qualification']['fit_eligible']
    assert inspected['partition']==detail['partition']
    rejected=[]
    for route,body in [('/api/stack/cohort-spans',{'cohort_id':COHORT,'item_id':item['id'],'labels':{}}),
                       ('/api/stack/cohort-spans/select',{**request,'proposal_id':'missing'})]:
        try:call(route,body)
        except HTTPError as exc:assert exc.code==400;rejected.append(route)
        else:raise AssertionError('Expected input rejection')
    after=call('/api/stack/evidence-needs',{'answer_id':ANSWER,'selectors':[]});assert before==after
    current=call('/api/stack/current-learning');assert current['source_reviews']['recorded_review_revisions']==0
    report={'inventory_counts':r['counts'],'field_id':item['id'],'task_id':task['id'],
        'inherited_partition':task['partition'],'exact_replay':True,'selection_replay':True,
        'invalid_requests_rejected':len(rejected),'original_question_unchanged':True,
        'human_reviews_created':0,'new_paid_api_calls':0,'original_queue_size':current['queue']['selected']}
    (OUT/'http-verification.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))

if __name__=='__main__':main()
