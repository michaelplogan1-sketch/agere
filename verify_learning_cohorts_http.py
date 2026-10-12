"""Current cohort models return unreviewed proposals to an existing question task."""
import json
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

BASE='http://127.0.0.1:8885'
OUT=Path('artifacts/agere-learning-cohorts-v1')
TASK='28b2ab9b826c8bb2911e467604a7d99e5a61483dd291bde56b6297f550bba6b9'
ANSWER='fc6d15d4fd41a67aafd6615ba5c5c34c53688afc6f7373f3e69357abb3c59815'


def call(route, body=None):
    with urlopen(Request(BASE+route,data=json.dumps(body).encode() if body is not None else None,
                         headers={'Content-Type':'application/json'}),timeout=120) as r:
        return json.load(r)


def main():
    before=call('/api/stack/evidence-needs',{'answer_id':ANSWER,'selectors':[]})
    task=call('/api/stack/question-learning?id='+TASK)
    assert task['status']=='current'
    cohorts=call('/api/stack/learning-cohorts')['cohorts']
    current=next(c for c in cohorts if c['status']=='current')
    assert current['design']['structural_minimums_met']
    assert current['models']['status'] in ('complete_shadow_only','partial_shadow_only')
    assert current['acquisition']['completed']==current['target_count']
    results=[];diagnostics=[]
    for kind,model in current['models']['models'].items():
        assert not model['release']['automatic_acceptance']
        request={'cohort_id':current['id'],'kind':kind,'target':task['target']}
        first=call('/api/stack/learning-cohorts/read',request)
        repeat=call('/api/stack/learning-cohorts/read',request)
        assert first['id']==repeat['id'] and repeat['cache_hit']
        inspected=call('/api/stack/semantic?id='+first['id'])
        assert not inspected['reviews'] and not first['training_label_created']
        assert not first['reading']['automatic_acceptance'] and not first['current_clinical_state_established']
        results.append(first)
        detail=call('/api/stack/learning-cohorts/diagnostics?id='+current['id']+'&kind='+kind)
        assert detail['validation_rows']==current['counts']['validation']
        assert detail['score_reference']=='unreviewed_machine_teacher' and detail['new_model_calls']==0
        assert all(r['disagreement_axes'] and r['partition']=='validation' for r in detail['rows'])
        diagnostics.append({k:detail[k] for k in ('kind','validation_rows','agreement_rows','disagreement_rows','total')})
    rejected=[]
    request={'cohort_id':current['id'],'kind':'lexical','target':task['target']}
    for name,extra in [('unknown_cohort',{'cohort_id':'0'*64}),('unknown_reader',{'kind':'frontier'}),
                       ('injected_labels',{'labels':{'statement':'patient_affirmed'}}),
                       ('forged_resolution',{'resolved':True})]:
        try:call('/api/stack/learning-cohorts/read',{**request,**extra})
        except HTTPError as exc:assert exc.code==400;rejected.append(name)
        else:raise AssertionError(name)
    after=call('/api/stack/evidence-needs',{'answer_id':ANSWER,'selectors':[]})
    assert before==after
    saved=call('/api/stack/question-learning?id='+TASK)
    assert {r['id'] for r in results}<={r['id'] for r in saved['readings']}
    ready=call('/api/stack/current-learning')
    assert ready['queue']['selected']==192 and ready['source_reviews']['recorded_review_revisions']==0
    report={'cohort_id':current['id'],'question_task':TASK,'readings':results,'diagnostics':diagnostics,'exact_replay':True,
            'question_unchanged':True,'primary_queue_preserved':True,'rejected':rejected,
            'human_reviews_created':0,'reviewed_labels_created':0,'new_paid_api_calls':0}
    (OUT/'http-verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='readings'},indent=2))


if __name__=='__main__':main()
