"""Actual API regression traces; no retry of a saved failed stage."""
import argparse
import hashlib
import json
from pathlib import Path

from evaluate_stack_workflows import Run, write

OUT = Path('artifacts/agere-reconciliation-contract-v2')
BASELINE = Path('artifacts/agere-workflow-evaluation-v1/baseline')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='http://localhost:8885')
    parser.add_argument('--case', choices=['respiratory', 'functional', 'payer', 'appointment'], required=True)
    args = parser.parse_args()
    run = Run(OUT / 'workflows', args.base)
    case = args.case
    context = json.loads((BASELINE / case / 'source_scope.json').read_text())
    target = run.output / case / 'source_scope.json'
    if not target.exists(): write(target, context)
    cid = context['response']['context_id']
    pre = run.call(case, 'interpretation_preflight', 'context/plan', {'context_id': cid})
    if pre['status'] != 'executed': return
    result = run.call(case, 'source_interpretation', 'context/synthesize', {'context_id': cid})
    if result['status'] != 'executed': return
    answer = result['response']
    assert answer['question'] == context['response']['question']
    assert answer['patient_id'] == context['response']['patient_id']
    assert answer['context_id'] == cid
    assert answer['clinical_execution_authorized'] is False
    if case == 'appointment':
        old = json.loads((BASELINE / case / 'source_interpretation.json').read_text())['response']
        assert answer['id'] == old['id'] and answer['findings'] == old['findings']
        assert answer['new_paid_api_calls'] == 0
    else:
        assert answer['schema'] == 'typed-reference-reconciliation/2'
        assert answer['coverage']['source_coverage_upgraded'] is False
        assert len(answer['claim_reviews']) == answer['claim_scope']['input_claims']
        assert len({v['input_id'] for v in answer['claim_reviews']}) == len(answer['claim_reviews'])
    saved = run.call(case, 'saved_answer', 'case-synthesis?id=' + answer['id'])
    if saved['status'] != 'executed': return
    assert saved['response']['id'] == answer['id']
    replay = run.call(case, 'replay', 'context/synthesize', {'context_id': cid})
    if replay['status'] != 'executed': return
    assert replay['response']['id'] == answer['id'] and replay['response']['new_paid_api_calls'] == 0
    write(run.output / case / 'verification.json', {
        'status': 'passed', 'question_patient_context_retained': True,
        'saved_answer_reopened': True, 'replay_added_provider_calls': 0,
        'clinical_validated': False, 'answer_id': answer['id'],
        'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})


if __name__ == '__main__': main()
