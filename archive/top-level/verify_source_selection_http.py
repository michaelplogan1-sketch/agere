"""Exercise the explicit candidate; prove coverage/replay and the default boundary."""
from collections import Counter
from pathlib import Path
import json
import urllib.request
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import record_context as C
from clinical_core.agere.clinical.native_snapshot_cache import install

OUT = Path('artifacts/agere-source-selection-v1')


def request(route, body=None):
    req = urllib.request.Request('http://localhost:8885/api/stack/'+route,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=90) as response: return json.load(response)


def main():
    path = stack_path(); install(path)
    cases = json.loads(Path('research/agere/workflow-evaluation-v1/plan.json').read_text())['cases']
    checks = []
    for case in cases:
        payload = {'patient': case['patient'], 'question': case['question'], 'selection':'schema_candidate'}
        result = request('context/from-question', payload)
        (OUT/(case['id']+'-http.json')).write_text(json.dumps(result, indent=2)+'\n')
        assert result['schema'] == 'schema-source-selection/1'
        default = request('context/from-question', {'patient':case['patient'],'question':case['question']})
        assert default['schema'] == 'question-source-scope/2'
        assert result['patient_id'] == case['patient'] and result['question'] == case['question']
        plan = result['source_plan']; assert plan
        records, _ = C.received(path, case['patient']); by_id = {r['record_id']: r for r in records}
        selected = set(result['preview']['selected_record_ids'])
        categories = {by_id[r]['source_table'] for r in selected}
        wanted = set(case['required_categories_if_received'])
        assert not wanted-categories
        old = json.loads(Path('artifacts/agere-workflow-evaluation-v1/baseline',case['id'],'source_scope.json').read_text())['response']
        assert not set(old['connected_record_ids'])-selected
        for category in result['preview']['request']['categories']:
            assert {r['record_id'] for r in records if r['source_table']==category} <= selected
        grouped = [rid for g in plan['plan']['groups'] for rid in g['record_ids']]
        assert Counter(grouped) == Counter(selected)
        for g, row in zip(plan['plan']['groups'], plan['contexts']):
            assert not row['omissions'] and row['dispatch']['source_review_allowed']
            assert row['budget']['selected_units'] == row['budget']['candidate_units']
            assert row['patient_id'] == case['patient'] and row['parameters']['question'].startswith(case['question'])
            preflight = request('context/plan', {'context_id': row['id']})
            assert preflight['can_dispatch'], preflight
        reopened = request('retrieval-source-plan/inspect', {'id': plan['plan']['id']})
        assert reopened['plan'] == plan['plan'] and reopened['contexts'] == plan['contexts']
        repeat = request('context/from-question', payload)
        assert repeat['id'] == result['id'] and repeat['cache_hit'] and repeat['new_paid_api_calls'] == 0
        checks.append({'case': case['id'], 'question_patient_retained': True,
            'all_required_received_categories_in_scope': True, 'baseline_reference_records_retained': len(old['connected_record_ids']),
            'selected_records': len(selected), 'review_groups': len(plan['contexts']),
            'omitted_units': 0, 'preflight_allowed_all_groups': True, 'source_plan_id': plan['plan']['id'],
            'selection_id': result['id'], 'replay_idempotent': True, 'new_paid_api_calls': 0,
            'default_remains_passage_reference':True,
            'clinical_validated': False, 'relevance_validated': False})
        print(json.dumps(checks[-1]), flush=True)
        (OUT/'http-verification.json').write_text(json.dumps({'status':'in_progress','cases':checks},indent=2)+'\n')
    old = json.loads(Path('artifacts/agere-reconciliation-contract-v2/delivery-verification.json').read_text())
    for case in old['cases']:
        answer = request('case-synthesis?id='+case['answer_id'])
        assert answer['id'] == case['answer_id']
    (OUT/'http-verification.json').write_text(json.dumps({'status':'passed','cases':checks,
        'prior_corrected_answers_reopen':3,'new_interpretations':0,'complete_workflows':0,'new_paid_api_calls':0},indent=2)+'\n')


if __name__ == '__main__': main()
