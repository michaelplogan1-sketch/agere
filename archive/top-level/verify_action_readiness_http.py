"""Real loopback integration using only labelled, unresolved synthetic observations."""
import copy
import json
from pathlib import Path
from uuid import uuid4
from verify_care_capabilities_http import api, ANSWER

OUT = Path('artifacts/agere-action-readiness-v1')
RECORD = 'WHM-R-f07f0c5291aacbe0494b18'


def write(name, row):
    (OUT/name).write_text(json.dumps(row, indent=2))


def observe(target, bindings, rationale):
    prior = api('/api/stack/readiness/inspect', {'target': target})
    req = {'target': target, 'expected_revision': prior['revision'], 'request_id': 'readiness-http-'+str(uuid4()),
           'actor': 'Automated synthetic HTTP verification', 'actor_kind': 'synthetic_test',
           'disposition': 'unresolved', 'rationale': rationale, 'bindings': bindings}
    row = api('/api/stack/readiness/review', req)
    assert api('/api/stack/readiness/review', req)['idempotent_replay']
    assert api('/api/stack/readiness/review', {**req, 'request_id': str(uuid4())}, 409)['status'] == 409
    assert api('/api/stack/readiness/review', {**req, 'request_id': str(uuid4()), 'expected_revision': row['revision'],
               'disposition': 'addressed'}, 400)['status'] == 400
    state = api('/api/stack/readiness/inspect', {'target': target})
    assert state['state'] == 'machine_observation' and not state['documentary_question_addressed']
    return state


def handoff(task):
    req = {'task_id': task['id'], 'expected_revision': task['revision'], 'request_id': 'readiness-handoff-'+str(uuid4()),
           'action': 'prepare_handoff', 'actor': 'Automated synthetic HTTP verification',
           'note': 'Attach unresolved documentary observations to this synthetic internal packet; no clinical review claimed'}
    row = api('/api/stack/capabilities/event', req)
    assert api('/api/stack/capabilities/event', req)['idempotent_replay']
    assert not row['readiness_attachment']['clinical_execution_authorized']
    return row


if __name__ == '__main__':
    OUT.mkdir(exist_ok=True)
    task = api('/api/stack/capabilities/tasks?buyer=synthetic-api-fixture')['tasks'][0]
    original_handoff = copy.deepcopy(task['handoff'])
    initial = api('/api/stack/readiness/task?id='+task['id'])
    target = initial['routes'][1]['reviews'][0]['target']['target']
    preview = api('/api/stack/readiness/source', {'target': target, 'record_id': RECORD})
    value = next(f for f in preview['fields'] if f['field'] == 'new_prescription_issued')
    field = {'kind': 'field', 'record_id': RECORD, 'record_hash': preview['record_hash'],
             'field': value['field'], 'value_hash': value['value_hash']}
    state = observe(target, [field], 'Synthetic test: the exact received prescription-response field is attached. Clinical appropriateness, consent and plan coverage remain unresolved.')
    write('live-service-observation.json', state)
    docs = api('/api/stack/readiness/documents')
    doc = next(p for p in docs['packages'] if p['program'] == 'synthetic_affordability_review' and p['version'] == '2')
    clause = next(v for v in doc['clauses'] if v['input_key'] == 'attribution')
    cb = {'kind': 'program_clause', 'package_id': doc['package_id'], 'input_key': clause['input_key'],
          'quote_hash': clause['quote_hash'], 'review_revision': doc['review']['revision']}
    dt = next(v['target']['target'] for v in initial['routes'][1]['reviews'] if v['target']['question'].startswith('Member eligibility'))
    document_state = observe(dt, [cb], 'Synthetic engineering check: attach the existing synthetic attribution clause. It is not an insurer contract, verified benefit, patient attribution or evidence that this service is covered.')
    assert document_state['latest']['dependencies'][0]['source_patient_id'] is None
    assert not document_state['latest']['dependencies'][0]['patient_applicability_established']
    write('live-document-clause-observation.json', document_state)
    first = handoff(task)
    assert api('/api/stack/readiness/task?id='+task['id'])['handoff_readiness_current']
    observe(target, [field], 'Synthetic correction test: the source field alone cannot establish this compound clinical prerequisite. Still unresolved; no clinician reviewed this test.')
    stale = api('/api/stack/readiness/task?id='+task['id'])
    assert not stale['handoff_readiness_current']
    write('live-stale-handoff.json', stale)
    final = handoff(first['task'])
    ready = api('/api/stack/readiness/task?id='+task['id'])
    assert ready['handoff_readiness_current']
    assert [r['status'] for r in ready['routes']] == ['route_blocked', 'documentary_review_incomplete']
    assert final['task']['handoff']['origin'] == original_handoff['origin']
    assert not final['task']['handoff']['human_review_completed']
    assert not final['task']['handoff']['external_dispatch_performed']
    write('live-current-handoff.json', final); write('live-readiness.json', ready)

    needs = api('/api/stack/evidence-needs', {'answer_id': ANSWER, 'selectors': []})
    need = needs['needs'][0]
    nt = {'kind': 'evidence_need', 'receipt_id': needs['id'], 'need_id': need['id']}
    question_state = observe(nt, [field], 'Synthetic test: attach a literal received response as a candidate dependency. This does not answer or clinically resolve the original machine-proposed question.')
    assert question_state['target']['question'] == need['question']
    write('live-question-observation.json', question_state)
    view = api('/api/stack/readiness/answer?id='+needs['id'])
    assert view['counts']['machine_observation'] >= 1
    ctx = api('/api/stack/readiness/context', {'target': nt, 'record_ids': [RECORD]})
    assert RECORD in ctx['record_scope']['record_ids'] and not ctx['meaning_resolved']
    assert ctx['requests_sent'] == 0 and ctx['new_paid_api_calls'] == 0
    write('live-acquisition-context.json', ctx)
    assert api('/api/stack/case-synthesis?id='+ANSWER)['id'] == ANSWER
    summary = {'actual_http_verified': True, 'task_id': task['id'], 'readiness_id': ready['id'],
               'question_target': nt, 'exact_received_false_value_preserved': value['value'] is False,
               'handoff_stale_after_review_change': True, 'handoff_current_after_reprepare': True,
               'original_question_and_origin_preserved': True, 'synthetic_observations_only': True,
               'clinical_execution_authorized': False, 'human_reviews_created': 0, 'external_actions_executed': 0,
               'new_paid_api_calls': 0, 'exact_imported_clause_attached': True, 'patient_applicability_inferred': False,
               'acquisition_context_id': ctx['context_id'], 'selected_records': len(ctx['record_scope']['record_ids'])}
    write('http-verification.json', summary); print(json.dumps(summary, indent=2))
