"""Real API attachment, all-group selection, bounded cross-group review and replay."""
import json
import sqlite3
from pathlib import Path
from verify_source_plan_answers_live import OUT, call


def rejected(route, body):
    try:call(route,body)
    except RuntimeError as exc:
        assert str(exc).startswith('400 '),str(exc)
        return str(exc)
    raise AssertionError('Invalid scope was accepted')


def main():
    plan=json.loads(Path('artifacts/agere-retrieval-source-plan-v1/payer-source-plan.json').read_text())['plan']
    answers=[json.loads((OUT/('payer-group-'+str(i)+'.json')).read_text()) for i in (1,2)]
    attachments=[];failures=[]
    for i,a in enumerate(answers):
        r=call('source-plan-answers/attach',{'plan_id':plan['id'],'answer_id':a['id']})
        assert r['attachment']['context_id']==plan['groups'][i]['context_id']
        attachments.append(r['attachment'])
        assert call('source-plan-answers/attach',{'plan_id':plan['id'],'answer_id':a['id']})['cache_hit']
        if i==0:
            status=call('source-plan-answers/listing',{'plan_id':plan['id']})
            (OUT/'partial-attachment-state.json').write_text(json.dumps(status,indent=2)+'\n')
            failures.append(rejected('source-plan-answers/prepare',{'plan_id':plan['id'],'attachment_ids':[r['attachment']['id']]}))
    med=json.loads(Path('artifacts/agere-retrieval-source-plan-v1/medication-source-plan.json').read_text())['plan']
    failures.append(rejected('source-plan-answers/attach',{'plan_id':med['id'],'answer_id':answers[0]['id']}))
    failures.append(rejected('source-plan-answers/attach',{'plan_id':plan['id'],'answer_id':'../other'}))
    body={'plan_id':plan['id'],'attachment_ids':[a['id'] for a in attachments]}
    r=call('source-plan-answers/prepare',body);s=r['snapshot']
    assert s['source_units']==53 and s['selected_records']==24 and s['excluded_records']==474
    assert s['cross_group_interpretation']=='not_assessed' and not s['clinical_execution_authorized']
    assert call('source-plan-answers/prepare',body)['cache_hit']
    assert call('source-plan-answers/inspect',{'id':s['id']})['snapshot']==s
    (OUT/'answer-snapshot.json').write_text(json.dumps(r,indent=2)+'\n')
    preview=call('source-plan-reconciliation/preview',{'snapshot_id':s['id']})
    assert preview['maximum_inference_calls']==1 and preview['request_bytes']<=32768
    (OUT/'reconciliation-preview.json').write_text(json.dumps(preview,indent=2)+'\n')
    print(json.dumps({'snapshot_id':s['id'],'preview':{k:preview[k] for k in ('input_claims','cited_excerpts','request_bytes')}}),flush=True)
    # Exact saved snapshot includes all groups; no scope narrowing after failure.
    recovery=bool(preview.get('saved_failure'))
    if recovery:
        assert preview['saved_failure']['error']=='Ambiguous claim-review unit reference'
        rp=call('source-plan-recovery/preview',{'snapshot_id':s['id']})
        assert rp['quarantined_claim_reviews']==1 and rp['new_paid_api_calls']==0
        (OUT/'recovery-preview.json').write_text(json.dumps(rp,indent=2)+'\n')
    route='source-plan-recovery/recover' if recovery else 'source-plan-reconciliation/reconcile'
    answer=call(route,{'snapshot_id':s['id']})
    assert answer['coverage']['input_claims']==answer['coverage']['accounted_claims']==preview['input_claims']
    assert not answer['coverage']['source_coverage_upgraded'] and not answer['clinical_execution_authorized']
    (OUT/'cross-group-answer.json').write_text(json.dumps(answer,indent=2)+'\n')
    repeat=call(route,{'snapshot_id':s['id']})
    assert repeat['cache_hit'] and repeat['new_paid_api_calls']==repeat['new_token_count_calls']==0
    restored=call('source-plan-recovery/inspect' if recovery else 'source-plan-reconciliation/inspect',{'id':answer['id']})
    assert restored['result']==answer['result']
    report={'status':'passed','snapshot_id':s['id'],'plan_id':plan['id'],'group_answers':len(attachments),
        'source_units':53,'selected_records':24,'excluded_records':474,'reconciliation_id':answer['id'],
        'input_claims':preview['input_claims'],'cited_excerpts':preview['cited_excerpts'],'request_bytes':preview['request_bytes'],
        'invalid_scopes_rejected':failures,'exact_replay_without_new_calls':True,'clinical_validated':False,
        'local_recovery':recovery,'system_unassessed_claim_reviews':answer['coverage'].get('system_unassessed_claim_reviews',0)}
    (OUT/'http-verification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report),flush=True)


if __name__=='__main__':main()
