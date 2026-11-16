"""Windowed representation joins the exact source/question workflow in shadow mode."""
import json
from pathlib import Path
from urllib.error import HTTPError
from verify_learning_cohorts_http import call, TASK, ANSWER


def main():
    root=Path('artifacts/agere-windowed-student-v1')
    before=call('/api/stack/evidence-needs',{'answer_id':ANSWER,'selectors':[]})
    task=call('/api/stack/question-learning?id='+TASK)
    current=next(c for c in call('/api/stack/learning-cohorts')['cohorts'] if c.get('windowed_reader',{}).get('status')=='complete_shadow_only')
    assert current['sampling_coverage']['sampled_categories']==76
    assert current['sampling_coverage']['fitted_target_categories']==13
    assert current['sampling_coverage']['fitted_target_concepts']==11
    request={'cohort_id':current['id'],'kind':'windowed_minilm','target':task['target']}
    reading=call('/api/stack/learning-cohorts/read',request)
    replay=call('/api/stack/learning-cohorts/read',request)
    assert reading['id']==replay['id'] and replay['cache_hit']
    assert reading['student']['window_coverage']['complete_source_coverage'] and not reading['student']['window_coverage']['truncated']
    assert not reading['reading']['automatic_acceptance'] and not reading['training_label_created']
    detail=call('/api/stack/learning-cohorts/diagnostics?id='+current['id']+'&kind=windowed_minilm')
    assert detail['validation_rows']==48 and detail['new_model_calls']==0
    assert all(r['partition']=='validation' and r['disagreement_axes'] for r in detail['rows'])
    inspected=call('/api/stack/semantic?id='+reading['id']);assert not inspected['reviews']
    rejections=[]
    for field,value in [('labels',{}),('resolved',True),('cohort_id','0'*64)]:
        try:call('/api/stack/learning-cohorts/read',{**request,field:value})
        except HTTPError as exc:assert exc.code==400;rejections.append(field)
        else:raise AssertionError(field)
    after=call('/api/stack/evidence-needs',{'answer_id':ANSWER,'selectors':[]});assert before==after
    saved=call('/api/stack/question-learning?id='+TASK);assert reading['id'] in [r['id'] for r in saved['readings']]
    ready=call('/api/stack/current-learning');assert ready['source_reviews']['recorded_review_revisions']==0 and ready['queue']['selected']==192
    report={'cohort_id':current['id'],'question_task':TASK,'reading':reading,'sampling_coverage':current['sampling_coverage'],'exact_replay':True,'question_unchanged':True,
        'diagnostics':{k:detail[k] for k in ('validation_rows','agreement_rows','disagreement_rows','total')},
        'rejections':rejections,'human_reviews_created':0,'reviewed_labels_created':0,'new_paid_api_calls':0}
    (root/'http-verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='reading'},indent=2))


if __name__=='__main__':main()
