"""Local account follow-through, explicit selection and replay; no model dispatch."""
import json
from pathlib import Path
from verify_stable_references_http import api

OUT=Path('artifacts/agere-need-accounts-v1')


if __name__=='__main__':
    needs=json.loads((OUT/'live-needs.json').read_text());request={'receipt_id':needs['id'],'need_id':needs['needs'][0]['id']}
    row=api('/api/stack/evidence-needs/accounts',request)
    assert row['cache_hit'] and row['new_paid_api_calls']==0
    assert api('/api/stack/evidence-needs/accounts?id='+row['id'])=={k:v for k,v in row.items() if k!='cache_hit'}
    account=row['accounts'][0];selection={'account_receipt_id':row['id'],'account_id':account['account_id']}
    selected=api('/api/stack/evidence-needs/account-context',selection)
    assert api('/api/stack/evidence-needs/account-context',selection)==selected
    assert api('/api/stack/evidence-needs/account-context?id='+selected['id'])==selected
    assert set(account['source_record_ids'])<=set(selected['record_scope']['record_ids'])
    assert selected['budget']['omitted_units']==0
    try:api('/api/stack/evidence-needs/account-context',{**selection,'account_id':'unrelated'})
    except RuntimeError as exc:assert '400' in str(exc)
    else:raise AssertionError('Unrelated account accepted')
    assert api('/api/stack/evidence-needs',{'answer_id':needs['answer_id'],'selectors':[]})==needs
    plan=api('/api/stack/context/plan',{'context_id':selected['context_id']})
    assert plan['can_dispatch'] and plan['spending_limit']=='uncapped'
    summary={'actual_http_verified':True,'account_receipt_id':row['id'],'selection_id':selected['id'],
        'context_id':selected['context_id'],'account_id':account['account_id'],'received_sources':account['source_count'],
        'outside_original_answer':account['outside_original_scope'],'account_state':account['status'],
        'reported_balance_selected':account['snapshot_amounts'] is not None,'context_records':len(selected['record_scope']['record_ids']),
        'context_bytes':selected['budget']['used'],'context_units':plan['source_units'],'planned_source_passes':plan['source_passes'],
        'exact_replay_verified':True,'invalid_account_rejected':True,'all_received_account_sources_included':True,
        'original_needs_unchanged':True,'new_paid_api_calls':0,'interpretation_status':'pending_specific_payload_approval_after_automatic_review_rejection',
        'clinical_accuracy_validated':False,'financial_hardship_established':False,'realized_savings_established':False}
    (OUT/'http-verification.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))
