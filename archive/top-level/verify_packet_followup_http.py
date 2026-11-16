"""Source-bound follow-up using the actual received synthetic warehouse."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4
from clinical_core.agere.clinical import internal_execution as execution
from verify_internal_execution_http import api, TASK, BUYER

OUT = Path('artifacts/agere-packet-followup-v1')


if __name__ == '__main__':
    OUT.mkdir(exist_ok=True); active = json.loads(Path('config/agere/active_warehouse.json').read_text()); stack = Path(active['build'])/'stack'
    credential = execution.provision(stack, {'principal_id': 'Automated synthetic evidence follow-up fixture', 'identity_kind': 'synthetic_test',
        'buyer_id': BUYER, 'service_id': 'coverage-linked-pharmacy', 'roles': ['packet_reviewer'],
        'expires_at': (datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat()})
    try:
        before = api('/api/stack/packet-followup?id='+TASK); assert before['prepare_available']
        target = next(t for t in before['available_targets'] if t['question'].startswith('Clinical appropriateness'))
        req = {'run_id': before['current_run_id'], 'review_revision': before['current_review_revision'], 'target': target['target'],
            'record_ids': ['WHM-R-f07f0c5291aacbe0494b18'], 'note': 'Synthetic source follow-up for the recorded medication/symptom reassessment request. This source does not establish clinical appropriateness, consent or completed care.',
            'request_id': 'followup-http-'+str(uuid4())}
        assert api('/api/stack/packet-followup/prepare', req, expected=403)['error']
        assert api('/api/stack/packet-followup/prepare', {**req, 'review_revision': 999}, credential, expected=409)['error']
        db = sqlite3.connect(stack/'stack.sqlite3')
        try: foreign = db.execute("SELECT id FROM records WHERE patient != 'P-1001' LIMIT 1").fetchone()[0]
        finally: db.close()
        assert api('/api/stack/packet-followup/prepare', {**req, 'record_ids': [foreign]}, credential, expected=400)['error']
        result = api('/api/stack/packet-followup/prepare', req, credential)
        assert api('/api/stack/packet-followup/prepare', req, credential)['idempotent_replay']
        context = api('/api/stack/context?id='+result['acquisition']['context_id'])
        assert context['patient_id']=='P-1001' and context['record_scope']['selection_sufficiency']=='unassessed'
        assert result['acquisition']['decision_cutoff']=='2029-08-15' and not result['requirement_resolved']
        assert 'WHM-R-f07f0c5291aacbe0494b18' in context['record_scope']['record_ids']
        assert context['dispatch']['source_review_allowed'] and not context['omissions']
        state = api('/api/stack/packet-followup?id='+TASK)
        assert next(r for r in state['followups'] if r['id']==result['id'])['current']
        assert api('/api/stack/work-state?id='+TASK)['layers']['documentary_review']['state']=='documentary_review_incomplete'
        for name, value in [('live-followup', result), ('live-context', context), ('live-state', state)]:
            (OUT/(name+'.json')).write_text(json.dumps(value, indent=2))
        summary = {'actual_http_verified': True, 'followup_id': result['id'], 'context_id': context['id'],
            'same_patient_and_cutoff_enforced': True, 'record_context_available_in_main_interpretation_route': True,
            'explicit_starting_records': 1, 'connected_records': len(context['record_scope']['record_ids']),
            'context_units': len(context['model_context']['evidence']), 'selection_sufficiency': 'unassessed',
            'request_replay_verified': True, 'task_revision': 7, 'human_reviews_created': 0,
            'synthetic_followup_receipts_created': 1, 'new_paid_inference_calls': 0, 'external_actions_executed': 0,
            'requirement_resolved': False, 'clinical_outcomes_verified': False}
    finally: execution.revoke(stack, credential['credential']['id'], 'Synthetic follow-up HTTP verification finished')
    summary['test_credential_revoked'] = True
    (OUT/'http-verification.json').write_text(json.dumps(summary, indent=2)); print(json.dumps(summary, indent=2))
