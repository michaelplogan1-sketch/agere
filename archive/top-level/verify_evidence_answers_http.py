"""Attach an existing paid answer locally; never dispatch a model request."""
import json
from pathlib import Path
from verify_stable_references_http import api

OUT=Path('artifacts/agere-evidence-answers-v1')


if __name__=='__main__':
    OUT.mkdir(exist_ok=True)
    selected=json.loads(Path('artifacts/agere-need-candidates-v1/medication-selection.json').read_text())
    answer=json.loads(Path('artifacts/agere-need-candidates-v1/browser-verification.json').read_text())['answer_id']
    needs=json.loads(Path('artifacts/agere-need-candidates-v1/medication-needs.json').read_text())
    before=api('/api/stack/evidence-needs',{'answer_id':needs['answer_id'],'selectors':[]})
    request={'receipt_id':selected['receipt_id'],'need_id':selected['need_id'],'selection_kind':'candidates','selection_id':selected['id'],'answer_id':answer}
    row=api('/api/stack/evidence-needs/answer',request);assert row['new_paid_api_calls']==0 and not row['requirement_resolved']
    assert api('/api/stack/evidence-needs/answer',request)==row
    for bad in [{**request,'answer_id':needs['answer_id']},{**request,'requirement_resolved':True},{**request,'need_id':needs['needs'][1]['id']}]:
        try:api('/api/stack/evidence-needs/answer',bad)
        except RuntimeError as exc:assert '400' in str(exc)
        else:raise AssertionError('Invalid question attachment accepted')
    history=api('/api/stack/evidence-needs/answers?receipt_id='+row['receipt_id']+'&need_id='+row['need_id'])
    item=next(a for a in history['answers'] if a['id']==row['id']);assert item['current']
    assert api('/api/stack/evidence-needs',{'answer_id':needs['answer_id'],'selectors':[]})==before
    summary={'actual_http_verified':True,'attachment_id':row['id'],'original_answer_id':needs['answer_id'],
        'receipt_id':row['receipt_id'],'need_id':row['need_id'],'followup_answer_id':answer,'selection_id':selected['id'],
        'summary':row['summary'],'current_dependencies':True,'exact_replay_verified':True,
        'invalid_context_question_and_resolution_rejected':True,'original_needs_unchanged':True,
        'new_paid_api_calls':0,'human_reviews_created':0,'requirement_resolved':False,'clinical_accuracy_validated':False}
    for name,value in [('attachment',row),('history',history),('http-verification',summary)]:
        (OUT/(name+'.json')).write_text(json.dumps(value,indent=2))
    print(json.dumps(summary,indent=2))
