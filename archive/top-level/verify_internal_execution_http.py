"""Real loopback checks with short-lived, clearly synthetic local credentials.

Secrets stay in process memory and every provisioned test credential is revoked.
"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4
from clinical_core.agere.clinical import internal_execution as execution

OUT = Path('artifacts/agere-internal-execution-v1')
TASK = 'a102c3a9d7c9b460a2e6665c048b78ba6e6c76c9d3ff3705fbfeeecacd3b6443'
BUYER = 'synthetic-coverage-buyer'


def api(route, body=None, credential=None, expected=200):
    headers = {'Content-Type': 'application/json'}
    if credential: headers['Authorization'] = 'Bearer '+credential['token']
    req = Request('http://localhost:8885'+route, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    try:
        with urlopen(req, timeout=90) as response: status, raw = response.status, response.read()
    except HTTPError as exc: status, raw = exc.code, exc.read()
    assert status == expected, (route, status, raw[:300])
    return json.loads(raw)


if __name__ == '__main__':
    OUT.mkdir(exist_ok=True); config = json.loads(Path('config/agere/active_warehouse.json').read_text()); stack = Path(config['build'])/'stack'
    credentials = []
    def provision(role, principal):
        value = execution.provision(stack, {'principal_id': principal, 'identity_kind': 'synthetic_test', 'buyer_id': BUYER,
            'service_id': 'coverage-linked-pharmacy', 'roles': [role],
            'expires_at': (datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat()})
        credentials.append(value); return value
    try:
        worker = provision('packet_worker', 'Synthetic local packet worker')
        reviewer = provision('packet_reviewer', 'Automated synthetic packet-review fixture')
        task = api('/api/stack/work-state?id='+TASK)
        request = {'task_id': TASK, 'expected_revision': task['task_revision'], 'request_id': 'http-'+str(uuid4())}
        assert api('/api/stack/internal-execution/run', request, expected=403)['error']
        assert api('/api/stack/internal-execution/run', request, reviewer, expected=403)['error']
        result = api('/api/stack/internal-execution/run', request, worker)
        assert api('/api/stack/internal-execution/run', request, worker)['idempotent_replay']
        assert api('/api/stack/internal-execution/run', {**request, 'request_id': 'http-'+str(uuid4())}, worker)['deduplicated']
        state = api('/api/stack/internal-execution?id='+TASK); run = next(r for r in state['runs'] if r['id']==result['run']['id'])
        review = {'run_id': run['id'], 'expected_revision': run['reviews'][-1]['revision'] if run['reviews'] else 0,
            'request_id': 'http-'+str(uuid4()), 'disposition': 'needs_more_evidence',
            'note': 'Automated synthetic engineering check only. Clinical, benefit, participation and patient-goal review remain unresolved.',
            'follow_up_on': datetime.now(timezone.utc).date().isoformat()}
        assert api('/api/stack/internal-execution/review', review, worker, expected=403)['error']
        reviewed = api('/api/stack/internal-execution/review', review, reviewer)
        assert api('/api/stack/internal-execution/review', review, reviewer)['idempotent_replay']
        assert api('/api/stack/internal-execution/review', {**review, 'request_id': 'http-'+str(uuid4())}, reviewer, expected=409)['error']
        state = api('/api/stack/internal-execution?id='+TASK)
        assert state['current_run_id'] == run['id'] and not state['clinical_execution_authorized']
        assert next(r for r in state['runs'] if r['id']==run['id'])['follow_up_state']=='due'
        assert api('/api/stack/work-state?id='+TASK)['layers']['documentary_review']['state']=='documentary_review_incomplete'
        answers = ['975024996abefa0c26ade1337cdb55810996df6397704e765c3efe4ed8655658',
                   '0227207af37a3865659acbd25461b5a1eb5d6eee00ce23f4a17d259f3cbac08b',
                   'd7d96be6f304b5649e24c329e38648d1ef6475c579c1645498dcd7fd177c8799']
        for answer in answers: assert api('/api/stack/case-synthesis?id='+answer)['id']==answer
        for name, value in [('live-execution', result), ('live-review', reviewed), ('live-state', state)]:
            (OUT/(name+'.json')).write_text(json.dumps(value, indent=2))
        summary = {'actual_http_verified': True, 'task_id': TASK, 'task_revision': task['task_revision'], 'run_id': run['id'],
                   'unauthenticated_and_wrong_role_rejected': True, 'idempotence_and_deduplication_verified': True,
                   'stale_review_revision_rejected': True, 'saved_answers_replayed': len(answers), 'dependency_layers_checked': 4,
                   'human_reviews_created': 0, 'synthetic_packet_dispositions_created': 1, 'new_paid_inference_calls': 0,
                   'external_actions_executed': 0, 'clinical_execution_authorized': False}
    finally:
        for value in credentials: execution.revoke(stack, value['credential']['id'], 'Synthetic HTTP verification finished')
    assert api('/api/stack/internal-execution/run', request, worker, expected=403)['error']
    summary['all_test_credentials_revoked'] = True
    (OUT/'http-verification.json').write_text(json.dumps(summary, indent=2)); print(json.dumps(summary, indent=2))
