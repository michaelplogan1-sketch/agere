"""One bounded live reconciliation plus exact replay and assessment integration."""
import json
from pathlib import Path
import sqlite3
from urllib.error import HTTPError
from urllib.request import Request,urlopen

OUT=Path('artifacts/agere-stable-references-v1')
PARENT='d7d96be6f304b5649e24c329e38648d1ef6475c579c1645498dcd7fd177c8799'


def api(route,body=None):
    req=Request('http://localhost:8885'+route,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
    try:
        with urlopen(req,timeout=240) as response:return json.load(response)
    except HTTPError as exc:raise RuntimeError(str(exc.code)+' '+exc.read().decode()[:1000]) from None


if __name__=='__main__':
    OUT.mkdir(exist_ok=True)
    before=api('/api/stack/case-synthesis?id='+PARENT)
    plan=api('/api/stack/references/plan',{'answer_id':PARENT})
    (OUT/'live-plan.json').write_text(json.dumps(plan,indent=2));assert plan['reused_source_passes']==2
    result=api('/api/stack/references/reconcile',{'answer_id':PARENT})
    (OUT/'live-answer.json').write_text(json.dumps(result,indent=2))
    assert result['passes']==before['passes'] and len(result['reference_manifest'])==11
    assert result['new_paid_api_calls']<=1 and result['coverage']['interpretation_accuracy_assessed'] is False
    saved=api('/api/stack/case-synthesis?id='+result['id'])
    replay=api('/api/stack/references/reconcile',{'answer_id':PARENT})
    assert replay['cache_hit'] and replay['new_paid_api_calls']==0
    assert api('/api/stack/case-synthesis?id='+PARENT)==before
    needs=api('/api/stack/evidence-needs',{'answer_id':result['id'],'selectors':[]})
    bound=[step for step in saved['review_steps'] if step.get('evidence_references')]
    for step in bound:
        need=next(n for n in needs['needs'] if n['id']==step['id'])
        assert {r['record_id'] for r in step['evidence_references']}<={r['record_id'] for r in need['selectors']}
        assert need['semantic_resolution']=='pending'
    assessment=api('/api/stack/work/prepare',{'program':'hf_review','patient':result['patient_id'],'synthesis_ids':[result['id']]})
    assert assessment['synthesis_assessments']==1
    new_groups=result['reconciliation']['groups']
    summary={'actual_http_verified':True,'answer_id':result['id'],'parent_answer_id':PARENT,'source_passes_reused_unchanged':2,
        'stable_source_units':len(result['reference_manifest']),'new_reconciliation_groups':len(new_groups),
        'new_paid_api_calls':result['new_paid_api_calls'],'new_reconciliation_cost_estimate_usd':sum(g['usage'].get('cost_estimate',{}).get('estimated_usd',0) for g in new_groups),
        'exact_replay_new_paid_calls':0,'new_reconciliation_findings':len(result['reconciliation']['result']['findings']),
        'new_reconciliation_citations':sum(len(f['citations']) for f in result['reconciliation']['result']['findings']),
        'explicitly_bound_new_review_questions':len(bound),'evidence_needs_summary':needs['summary'],
        'existing_program_assessment_integrated':True,'human_reviews_created':0,'external_actions_executed':0,
        'clinical_accuracy_validated':False}
    for name,value in [('live-needs',needs),('live-assessment',assessment),('live-replay',replay),('http-verification',summary)]:
        (OUT/(name+'.json')).write_text(json.dumps(value,indent=2))
    print(json.dumps(summary,indent=2))
