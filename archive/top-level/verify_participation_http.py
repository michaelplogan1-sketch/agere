"""Real source-linked work, declaration correction and re-preparation without dispatch."""
import copy
import json
from pathlib import Path
from uuid import uuid4
from verify_care_capabilities_http import api

OUT = Path('artifacts/agere-participation-v1')
BUYER = 'synthetic-coverage-buyer'


def write(name, row): (OUT/name).write_text(json.dumps(row, indent=2))


if __name__ == '__main__':
    previous = json.loads(Path('artifacts/agere-coverage-context-v1/live-plan.json').read_text())
    req = previous['received_coverage']['request']
    packages = api('/api/stack/readiness/documents')['packages']
    package = next(p for p in packages if p['package_id'] == 'c8348279d698c1e568b6f9fe5e5b684ca71e839aae118957a95e982f58e4988d')
    clause = next(c for c in package['clauses'] if c['input_key'] == 'attribution')
    binding = {'kind': 'program_clause', 'package_id': package['package_id'], 'input_key': clause['input_key'],
               'quote_hash': clause['quote_hash'], 'review_revision': package['review']['revision']}
    declaration = {'id': 'synthetic-network-participation', 'service_id': 'coverage-linked-pharmacy',
                   'provider_organization_id': 'WH-PRACTICE-7', 'payer_organization_id': 'WH-PAYER-1',
                   'plan_id': req['coverage']['plan_id'], 'region': 'IL', 'pathway_id': '', 'relationship': 'network_contract',
                   'reported_status': 'participating', 'effective_start': '2029-01-01', 'effective_end': '2029-12-31',
                   'received_on': '2029-07-26', 'provenance': 'Synthetic integration fixture only. This is not a real payer contract or provider participation verification. The attached synthetic clause only illustrates unbound attribution.',
                   'clauses': [binding]}
    def save(d):
        reg = api('/api/stack/participation?buyer='+BUYER)
        return api('/api/stack/participation/save', {'buyer_id': BUYER, 'expected_revision': reg['revision'],
                    'actor': 'Automated synthetic participation verification', 'actor_kind': 'synthetic_test', 'declarations': [d]})
    first_registry = save(declaration)
    plan = api('/api/stack/capabilities/prepare', req); option = plan['options'][0]
    context = plan['participation_contexts'][option['id']]
    row = context['routes'][0]['declarations'][0]
    assert row['scope_status'] == 'declared_scope_matches'
    assert row['document_state'] == 'mapping_or_period_unresolved'
    assert not row['verified_participation'] and not context['verified_benefits']
    queue = {'plan_id': plan['id'], 'option_id': option['id'], 'actor': 'Automated synthetic participation verification', 'participation_context_id': context['id']}
    task = api('/api/stack/capabilities/queue', queue)['task']
    def prepare(task):
        event = {'task_id': task['id'], 'expected_revision': task['revision'], 'request_id': 'participation-'+str(uuid4()),
                 'action': 'prepare_handoff', 'actor': 'Automated synthetic participation verification',
                 'note': 'Synthetic participation scope packet; no external dispatch or network determination'}
        result = api('/api/stack/capabilities/event', event)
        assert api('/api/stack/capabilities/event', event)['idempotent_replay']
        return result, event
    first, old_event = prepare(task)
    assert api('/api/stack/participation/task?id='+task['id'])['handoff_participation_current']
    changed = copy.deepcopy(declaration); changed['reported_status'] = 'pending'
    changed_registry = save(changed)
    stale = api('/api/stack/participation/task?id='+task['id'])
    assert not stale['queue_participation_current'] and not stale['handoff_participation_current']
    assert api('/api/stack/capabilities/queue', queue, 409)['status'] == 409
    assert api('/api/stack/capabilities/event', old_event, 409)['status'] == 409
    refreshed = api('/api/stack/capabilities/prepare', req); report = refreshed['participation_contexts'][option['id']]
    queue['participation_context_id'] = report['id']; task = api('/api/stack/capabilities/queue', queue)['task']
    final, _ = prepare(task); view = api('/api/stack/participation/task?id='+task['id'])
    assert view['queue_participation_current'] and view['handoff_participation_current']
    assert api('/api/stack/coverage/task?id='+task['id'])['handoff_coverage_current']
    assert api('/api/stack/readiness/task?id='+task['id'])['handoff_readiness_current']
    assert not final['task']['handoff']['external_dispatch_performed']
    for name, value in [('live-initial-registry.json', first_registry), ('live-registry.json', changed_registry),
                        ('live-plan.json', refreshed), ('live-stale-task.json', stale), ('live-handoff.json', final), ('live-task.json', view)]: write(name, value)
    summary = {'actual_http_verified': True, 'task_id': task['id'], 'matching_plan_id': refreshed['id'],
               'participation_context_id': report['id'], 'declaration_revision': changed_registry['revision'],
               'exact_scope_and_imported_clause_attached': True, 'changed_declaration_blocks_stale_queue_and_event': True,
               'handoff_participation_current_after_refresh': True, 'prior_readiness_and_coverage_current': True,
               'clinical_execution_authorized': False, 'verified_participation': False, 'verified_benefits': False,
               'new_paid_inference_calls': 0, 'human_reviews_created': 0, 'external_actions_executed': 0}
    write('http-verification.json', summary); print(json.dumps(summary, indent=2))
