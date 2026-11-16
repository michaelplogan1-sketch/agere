"""Prepare a synthetic stale dependency, then verify browser refresh and packet."""
import json
import sys
from pathlib import Path
from verify_care_capabilities_http import api

OUT = Path('artifacts/agere-action-work-state-v1')
TASK = 'a102c3a9d7c9b460a2e6665c048b78ba6e6c76c9d3ff3705fbfeeecacd3b6443'
BUYER = 'synthetic-coverage-buyer'


if __name__ == '__main__':
    if '--finish' not in sys.argv:
        before = api('/api/stack/work-state?id='+TASK); assert before['packet_matches_recorded_dependencies']
        reg = api('/api/stack/participation?buyer='+BUYER)
        reg['declarations'][0]['provenance'] += ' Synthetic combined work-state refresh verification.'
        saved = api('/api/stack/participation/save', {'buyer_id': BUYER, 'expected_revision': reg['revision'],
                    'actor': 'Automated synthetic work-state verification', 'actor_kind': 'synthetic_test', 'declarations': reg['declarations']})
        stale = api('/api/stack/work-state?id='+TASK)
        assert not stale['packet_matches_recorded_dependencies'] and stale['local_matching_refresh_available']
        assert stale['layers']['participation']['state'] == 'queue_refresh_required'
        (OUT/'live-stale-state.json').write_text(json.dumps(stale, indent=2))
        (OUT/'live-registry.json').write_text(json.dumps(saved, indent=2))
        print('Synthetic stale participation dependency prepared for browser refresh verification.')
    else:
        state = api('/api/stack/work-state?id='+TASK)
        assert state['packet_matches_recorded_dependencies']
        assert state['layers']['documentary_review']['state'] == 'documentary_review_incomplete'
        assert not state['clinical_execution_authorized'] and not state['verified_benefits']
        assert api('/api/stack/coverage/task?id='+TASK)['handoff_coverage_current']
        assert api('/api/stack/participation/task?id='+TASK)['handoff_participation_current']
        assert api('/api/stack/readiness/task?id='+TASK)['handoff_readiness_current']
        (OUT/'live-current-state.json').write_text(json.dumps(state, indent=2))
        summary = {'actual_http_verified': True, 'browser_refresh_endpoint_verified': True, 'task_id': TASK,
                   'task_revision': state['task_revision'], 'packet_matches_recorded_dependencies': True,
                   'documentary_review_remains_incomplete': True, 'new_paid_inference_calls': 0,
                   'human_reviews_created': 0, 'external_actions_executed': 0, 'clinical_execution_authorized': False}
        (OUT/'http-verification.json').write_text(json.dumps(summary, indent=2)); print(json.dumps(summary, indent=2))
