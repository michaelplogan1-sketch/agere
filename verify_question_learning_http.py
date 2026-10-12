"""Exercise local question acquisition without inference or source reviews."""
import json
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

BASE='http://127.0.0.1:8885'
OUT=Path('artifacts/agere-current-source-learning-v1');OUT.mkdir(exist_ok=True)
CANDIDATE='0ea4cba28330fd10794281cce5087356b33f4fa09919f3d936e13b067b69622b'
RECEIPT='fe21708fae4ecdd3ef00f70ef3fa8e5571f8a60dab925d1392b58327672eccd8'


def call(route, body=None):
    with urlopen(Request(BASE+route,data=json.dumps(body).encode() if body is not None else None,
                         headers={'Content-Type':'application/json'}),timeout=120) as r:
        return json.load(r)


def main():
    before=call('/api/stack/evidence-needs',{'answer_id':'fc6d15d4fd41a67aafd6615ba5c5c34c53688afc6f7373f3e69357abb3c59815','selectors':[]})
    ready=call('/api/stack/current-learning')
    assert ready['status']=='current' and ready['queue']['selected']==192
    assert ready['source_reviews']['recorded_review_revisions']==0
    preview=call('/api/stack/question-learning/preview?candidate_id='+CANDIDATE)
    assert preview['eligible_for_acquisition'] and len(preview['passages'])==8
    request={'candidate_id':CANDIDATE,'passage_rank':1,
        'quote':'reassess symptoms and measurements at the next visit','occurrence':0,'concept':'planned reassessment'}
    task=call('/api/stack/question-learning',request)
    assert task==call('/api/stack/question-learning',request)
    saved=call('/api/stack/question-learning?id='+task['id'])
    assert saved['status']=='current' and saved['context']['target_quote']==request['quote']
    assert not saved['qualification']['fit_eligible_at_creation'] and not saved['qualification']['automatic_acceptance']
    rejected=[]
    for name,extra in [('absent_quote',{'quote':'Not in the source'}),('wrong_passage',{'passage_rank':9}),
                       ('forged_label',{'labels':{'statement':'patient_affirmed'}}),('forged_resolution',{'resolved':True})]:
        try:call('/api/stack/question-learning',{**request,**extra})
        except HTTPError as exc:assert exc.code==400;rejected.append(name)
        else:raise AssertionError(name)
    assert before==call('/api/stack/evidence-needs',{'answer_id':'fc6d15d4fd41a67aafd6615ba5c5c34c53688afc6f7373f3e69357abb3c59815','selectors':[]})
    after=call('/api/stack/current-learning')
    assert after['source_reviews']['recorded_review_revisions']==0
    report={'task_id':task['id'],'exact_replay':True,'question_unchanged':True,'rejections':rejected,
            'new_paid_api_calls':0,'human_reviews_created':0,'training_labels_created':0}
    for name,value in [('task',task),('readiness',after),('http-verification',report)]:
        (OUT/(name+'.json')).write_text(json.dumps(value,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
