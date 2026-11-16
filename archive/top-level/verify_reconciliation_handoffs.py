"""Attach corrected synthetic answers to existing internal evidence-review work."""
import json
from pathlib import Path
from evaluate_stack_workflows import Run, connected_work, write

OUT = Path('artifacts/agere-reconciliation-contract-v2')


def main():
    plan = json.loads(Path('research/agere/workflow-evaluation-v1/plan.json').read_text())
    run = Run(OUT / 'workflows', 'http://localhost:8885')
    for case in plan['cases']:
        key = case['id']
        if key not in ('respiratory', 'functional', 'payer'): continue
        trace = json.loads((run.output / key / 'source_interpretation.json').read_text())
        if trace['status'] != 'executed': continue
        buyer = 'workflow-eval-' + key
        prior = Path('artifacts/agere-workflow-evaluation-v1/baseline') / key / 'buyer_fixture.json'
        if not prior.exists():
            cap = {'id': 'available', 'name': 'Synthetic evidence review',
                'description': 'Local synthetic evidence review fixture only. No real clinical service, contact or treatment.',
                'need_tags': [case['diagnostic_need_tag']], 'modality': 'virtual', 'regions': [], 'payer_types': [],
                'plan_ids': [], 'pathway_ids': [], 'owner': {'kind': 'agent', 'id': 'synthetic-evidence-reviewer'},
                'available': True, 'capacity_slots': None, 'capacity_as_of': plan['snapshot']['as_of'],
                'capacity_until': plan['snapshot']['as_of'], 'effective_start': '2029-01-01', 'effective_end': '2029-12-31',
                'prerequisites': ['Confirm current goals, consent, clinical appropriateness and benefits.'],
                'provenance': 'Assistant-authored synthetic software test fixture; not actual buyer inventory.'}
            caps = [cap, {**cap, 'id': 'unavailable', 'available': False},
                    {**cap, 'id': 'wrong-plan', 'plan_ids': ['OTHER-SYNTHETIC-PLAN']},
                    {**cap, 'id': 'unrelated', 'need_tags': ['unrelated_test_need']}]
            saved = run.call(key, 'buyer_fixture', 'capabilities/save', {'buyer_id': buyer, 'expected_revision': 0,
                'actor': 'Agere synthetic workflow evaluation', 'capabilities': caps})
            if saved['status'] != 'executed': continue
        connected_work(run, case, plan['snapshot']['as_of'])
        matched = json.loads((run.output / key / 'connected_matching.json').read_text())
        handoff = json.loads((run.output / key / 'connected_handoff.json').read_text())
        assert matched['status'] == handoff['status'] == 'executed'
        p = matched['response']; task = handoff['response']['task']; answer = trace['response']
        assert p['origin']['question'] == answer['question'] and p['origin']['findings'] == answer['findings']
        assert task['handoff']['origin'] == p['origin']
        assert task['handoff']['human_review_completed'] is False and task['handoff']['external_dispatch_performed'] is False
        assert task['state'] == 'handoff_prepared_for_review'
        for option in p['options']:
            cid = option['capability']['id']
            if cid in ('unavailable', 'wrong-plan'):
                rejected = run.call(key, 'reject_' + cid, 'capabilities/queue', {'plan_id': p['id'],
                    'option_id': option['id'], 'actor': 'Agere synthetic workflow evaluation'})
                assert rejected.get('http_status') in (400, 409)
        assert 'unrelated' in {o['capability_id'] for o in p['excluded']}
        reopened = run.call(key, 'reopened_tasks', 'capabilities/tasks?buyer=' + buyer)
        assert reopened['status'] == 'executed'
        assert task['id'] in json.dumps(reopened['response'])
        write(run.output / key / 'handoff-verification.json', {'status': 'passed', 'task_id': task['id'],
            'answer_id': answer['id'], 'original_question_and_findings_retained': True,
            'service_selection': 'explicit evaluator choice, not automatic clinical matching',
            'negative_controls_passed': True, 'human_review_created': False, 'external_dispatch_performed': False,
            'new_provider_calls': 0})


if __name__ == '__main__': main()
