"""Exercise real loopback routes using clearly labelled synthetic fixtures."""
import copy
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

BASE = 'http://localhost:8885'
OUT = Path('artifacts/agere-buyer-capabilities-v1')
ANSWER = 'd7d96be6f304b5649e24c329e38648d1ef6475c579c1645498dcd7fd177c8799'


def api(route, body=None, expected=200):
    req = Request(BASE + route, data=json.dumps(body).encode() if body is not None else None,
                  headers={'Content-Type': 'application/json'} if body is not None else {})
    try:
        with urlopen(req, timeout=90) as r: status, raw = r.status, r.read()
    except HTTPError as e: status, raw = e.code, e.read()
    assert status == expected, (route, status, raw[:300])
    return json.loads(raw) if status == 200 else {'status': status, 'error': raw.decode()}


if __name__ == '__main__':
    OUT.mkdir(exist_ok=True)
    answer = api('/api/stack/case-synthesis?id=' + ANSWER)
    catalog = api('/api/stack/capabilities/catalog')
    assert len(catalog['service_templates']) == 22 and len(catalog['pathways']) == 21
    browser_port = api('/api/stack/capabilities?buyer=synthetic-demo-buyer')
    browser_tasks = api('/api/stack/capabilities/tasks?buyer=synthetic-demo-buyer')
    assert browser_tasks['tasks'][0]['state'] == 'handoff_prepared_for_review'
    assert browser_tasks['tasks'][0]['handoff']['origin']['findings'][0]['id'] == 'r1_f1'
    buyer = 'synthetic-api-fixture'
    port = api('/api/stack/capabilities?buyer=' + buyer)
    cap = copy.deepcopy(browser_port['capabilities'][0])
    cap.update(id='med-review-agent', name='Synthetic medication review packet agent',
               owner={'kind': 'agent', 'id': 'synthetic-local-evidence-worker'},
               pathway_ids=['access_ckm', 'commercial_virtual'],
               provenance='Automated API fixture; not a real provider, insurer contract or CMS participant')
    cap2 = copy.deepcopy(cap); cap2.update(id='unrelated-respite', name='Synthetic caregiver support team',
                                         need_tags=['caregiver_support'], pathway_ids=['guide'])
    port = api('/api/stack/capabilities/save', {'buyer_id': buyer, 'expected_revision': port['revision'],
                'actor': 'Automated synthetic HTTP verification', 'capabilities': [cap, cap2]})
    request = {'buyer_id': buyer, 'origin': {'kind': 'answer', 'id': ANSWER, 'finding_ids': ['r1_f1']},
               'need_tags': ['medication_review', 'transport'], 'rationale': 'Synthetic API mapping; not a treatment decision',
               'coverage': {'payer_type': 'commercial', 'plan_id': 'DEMO-PLAN-2029', 'region': 'IL',
                            'as_of': '2029-08-15', 'provenance': 'Synthetic coverage fixture, not real benefits evidence',
                            'preferences': 'Unknown; review required'}}
    plan = api('/api/stack/capabilities/prepare', request)
    assert plan['unmatched_tags'] == ['transport'] and len(plan['excluded']) == 1
    assert [r['status'] for r in plan['options'][0]['routes']] == ['blocked', 'requires_verification']
    assert api('/api/stack/capabilities/plan?id=' + plan['id']) == plan
    queue = {'plan_id': plan['id'], 'option_id': plan['options'][0]['id'], 'actor': 'Automated synthetic HTTP verification'}
    item = api('/api/stack/capabilities/queue', queue)['task']
    assert api('/api/stack/capabilities/queue', queue)['idempotent_replay']
    event = {'task_id': item['id'], 'expected_revision': item['revision'], 'request_id': 'http-'+str(uuid4()),
             'action': 'prepare_handoff', 'actor': 'Automated synthetic HTTP verification', 'note': 'Prepare a synthetic source packet only'}
    result = api('/api/stack/capabilities/event', event)
    assert result['task']['handoff']['origin']['findings'] == [next(f for f in answer['findings'] if f['id'] == 'r1_f1')]
    assert result['task']['owner']['kind'] == 'agent'
    assert api('/api/stack/capabilities/event', event)['idempotent_replay']
    wrong = copy.deepcopy(request); wrong['coverage']['plan_id'] = 'DIFFERENT-PLAN'
    blocked = api('/api/stack/capabilities/prepare', wrong)
    assert blocked['options'][0]['status'] == 'blocked'
    denial = api('/api/stack/capabilities/queue', {**queue, 'plan_id': blocked['id'], 'option_id': blocked['options'][0]['id']}, 400)
    ma = copy.deepcopy(request); ma['coverage']['payer_type'] = 'medicare_advantage'
    ma_plan = api('/api/stack/capabilities/prepare', ma)
    assert ma_plan['options'][0]['status'] == 'blocked'
    original = copy.deepcopy(request); original['coverage']['payer_type'] = 'original_medicare'
    original_plan = api('/api/stack/capabilities/prepare', original)
    assert original_plan['options'][0]['routes'][0]['status'] == 'requires_verification'
    assert any('verify policy for decision date' in s for s in original_plan['options'][0]['routes'][0]['requirements'])
    assessment = api('/api/stack/work/prepare', {'program': 'hf_review', 'patient': answer['patient_id'], 'synthesis_ids': [ANSWER]})
    assert assessment['synthesis_assessments'] == 1
    # All assessment IDs are retained in the immutable proposal even if not candidates.
    import sqlite3
    active = json.loads(Path('config/agere/active_warehouse.json').read_text())
    db = sqlite3.connect(Path(active['build'])/'stack/program-work.sqlite3')
    try:
        saved = json.loads(db.execute('SELECT payload FROM work_proposals WHERE id=?', (assessment['proposal_id'],)).fetchone()[0])
    finally: db.close()
    aid = saved['assessment_ids'][0]
    bound = api('/api/stack/capabilities/assessment?id=' + aid)
    assert bound['patient_id'] == answer['patient_id']
    assessed = copy.deepcopy(request); assessed['origin'] = {'kind': 'assessment', 'id': aid, 'finding_ids': []}
    assessment_plan = api('/api/stack/capabilities/prepare', assessed)
    assert assessment_plan['origin']['assessment']['synthesis_context']['answers'][0]['answer_id'] == ANSWER
    for name, value in [('live-portfolio', port), ('live-browser-tasks', browser_tasks), ('live-plan', plan),
                        ('live-handoff', result), ('live-blocked-plan', blocked), ('live-ma-plan', ma_plan),
                        ('live-original-plan', original_plan), ('live-assessment-plan', assessment_plan)]:
        (OUT/(name+'.json')).write_text(json.dumps(value, indent=2)+'\n')
    receipt = {'schema': 'buyer-capability-http-verification/1', 'saved_answer_id': ANSWER,
               'patient_id': answer['patient_id'], 'portfolio_id': port['id'], 'plan_id': plan['id'],
               'task_id': item['id'], 'assessment_id': aid, 'assessment_plan_id': assessment_plan['id'],
               'public_pathways': len(catalog['pathways']), 'service_templates': len(catalog['service_templates']),
               'commercial_original_and_ma_boundaries_checked': True, 'exact_plan_mismatch_blocks_queue': True,
               'browser_clinician_task_and_api_agent_task_verified': True, 'same_citations_in_handoff': True,
               'queue_and_worker_idempotence_verified': True, 'program_assessment_bridge_verified': True,
               'new_paid_api_calls': 0, 'external_actions_executed': 0, 'human_reviews': 0}
    (OUT/'http-verification.json').write_text(json.dumps(receipt, indent=2)+'\n')
    print(json.dumps(receipt, indent=2))
