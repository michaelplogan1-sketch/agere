"""Source-bound coverage in the real question/match/queue/handoff APIs."""
import copy
import json
from pathlib import Path
from uuid import uuid4
from verify_care_capabilities_http import api, ANSWER

OUT = Path('artifacts/agere-coverage-context-v1')
REPORT = 'WHF-R-06120b49f9402026499cfb'


def write(name, row): (OUT/name).write_text(json.dumps(row, indent=2))


if __name__ == '__main__':
    ctx = api('/api/stack/coverage/context', {'patient_id': 'P-1001', 'as_of': '2029-08-15'})
    selected = api('/api/stack/coverage/select', {'context_id': ctx['id'], 'record_id': REPORT})
    assert selected['report']['reported_payer_type'] == 'medicare'
    assert selected['routing_fields']['payer_type'] == 'unknown'
    assert selected['report']['temporal_scope'] == 'historical_service_date'
    assert selected['routing_fields']['plan_id'].startswith('WH-FICTIONAL-PLAN-')
    buyer = 'synthetic-coverage-buyer'
    source = api('/api/stack/capabilities?buyer=synthetic-demo-buyer')
    cap = copy.deepcopy(source['capabilities'][0])
    cap.update(id='coverage-linked-pharmacy', name='Synthetic pharmacy coverage review team',
               owner={'kind': 'team', 'id': 'Synthetic benefits and pharmacy review'},
               plan_ids=[selected['routing_fields']['plan_id']], pathway_ids=['access_ckm', 'commercial_virtual'],
               provenance='Synthetic buyer fixture; exact fictional plan declaration, not real payer or CMS participation')
    existing = api('/api/stack/capabilities?buyer='+buyer)
    port = api('/api/stack/capabilities/save', {'buyer_id': buyer, 'expected_revision': existing['revision'],
               'actor': 'Automated synthetic coverage verification', 'capabilities': [cap]})
    req = {'buyer_id': buyer, 'origin': {'kind': 'answer', 'id': ANSWER, 'finding_ids': ['r1_f1']},
           'need_tags': ['medication_review'], 'rationale': 'Synthetic source-linked benefit review only; no treatment or entitlement decision',
           'coverage': {**selected['routing_fields'], 'region': 'IL', 'preferences': 'Not supplied; review required'},
           'coverage_binding': {'context_id': ctx['id'], 'record_id': REPORT}}
    plan = api('/api/stack/capabilities/prepare', req)
    assert plan['received_coverage']['coverage_selection'] == selected
    assert plan['coverage']['payer_type'] == 'unknown'
    assert all(not r['coverage_established'] for o in plan['options'] for r in o['routes'])
    assert api('/api/stack/capabilities/plan?id='+plan['id'])['id'] == plan['id']
    forged = copy.deepcopy(req); forged['coverage']['payer_type'] = 'original_medicare'
    assert api('/api/stack/capabilities/prepare', forged, 400)['status'] == 400
    queue = {'plan_id': plan['id'], 'option_id': plan['options'][0]['id'], 'actor': 'Automated synthetic coverage verification'}
    task = api('/api/stack/capabilities/queue', queue)['task']
    assert api('/api/stack/capabilities/queue', queue)['idempotent_replay']
    event = {'task_id': task['id'], 'expected_revision': task['revision'], 'request_id': 'coverage-http-'+str(uuid4()),
             'action': 'prepare_handoff', 'actor': 'Automated synthetic coverage verification',
             'note': 'Synthetic internal handoff with exact historical coverage report; no verified benefits or human clinical review'}
    result = api('/api/stack/capabilities/event', event)
    assert api('/api/stack/capabilities/event', event)['idempotent_replay']
    state = api('/api/stack/coverage/task?id='+task['id'])
    assert state['handoff_coverage_current'] and state['coverage_dependencies_current']
    assert not state['verified_benefits'] and not result['task']['handoff']['external_dispatch_performed']
    assert api('/api/stack/readiness/task?id='+task['id'])['handoff_readiness_current']
    old = api('/api/stack/capabilities/tasks?buyer=synthetic-demo-buyer')['tasks'][0]
    assert api('/api/stack/readiness/task?id='+old['id'])['handoff_readiness_current']
    assert api('/api/stack/coverage/task?id='+old['id'])['coverage_receipt'] is None
    for name, row in [('live-context.json', ctx), ('live-selection.json', selected), ('live-portfolio.json', port),
                      ('live-plan.json', plan), ('live-handoff.json', result), ('live-task-coverage.json', state)]: write(name, row)
    summary = {'actual_http_verified': True, 'source_context_id': ctx['id'], 'coverage_record_id': REPORT,
               'service_reference_date': selected['report']['service_reference_date'], 'decision_date': ctx['as_of'],
               'reported_medicare_subtype_left_unknown': True, 'temporal_scope': selected['report']['temporal_scope'],
               'task_id': task['id'], 'matching_plan_id': plan['id'], 'legacy_readiness_preserved': True,
               'handoff_coverage_current': True, 'new_paid_inference_calls': 0, 'human_reviews_created': 0,
               'verified_benefits': False, 'external_actions_executed': 0}
    write('http-verification.json', summary); print(json.dumps(summary, indent=2))
