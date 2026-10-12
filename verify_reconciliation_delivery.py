"""Independent saved-source, quote, question, accounting and scorecard checks."""
from collections import Counter
from pathlib import Path
import hashlib
import json

from clinical_core.agere.active import stack_path
from clinical_core.contracts import digest
from clinical_core.agere.clinical import reconciliation as R, structured_transport, reference_table_context
from clinical_core.agere.clinical import reference_reconciliation_transport, meaning_source_dispatch, literal_reference_runtime
from clinical_core.agere.clinical.native_snapshot_cache import install
from clinical_core.agere.workflow_scorecard import build

OUT = Path('artifacts/agere-reconciliation-contract-v2')


def read(path): return json.loads(Path(path).read_text())
def write(path, value): Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def main():
    path = stack_path(); install(path); literal_reference_runtime.install(); structured_transport.install()
    reference_table_context.install(); reference_reconciliation_transport.install(); meaning_source_dispatch.install(path)
    plan = read('research/agere/workflow-evaluation-v1/plan.json')
    cases = {c['id']: c for c in plan['cases']}; checks = []
    parents = read(OUT / 'parents.json')
    for item in parents[:3]:
        case = item['case']; folder = OUT / 'workflows' / case
        trace = read(folder / 'source_interpretation.json'); assert trace['status'] == 'executed'
        answer = trace['response']; stored = R.inspect(path, answer['id'])
        parent = R.S.inspect(path, item['parent_id']); row = R.S.context(path, item['context_id'])
        assert answer['question'] == cases[case]['question'] and answer['patient_id'] == cases[case]['patient']
        assert answer['context_id'] == row['id'] == item['context_id']
        assert digest(parent) == stored['parent_answer_hash'] and answer['passes'] == parent['passes']
        assert answer['retained_source_findings'] == [f for p in parent['passes'] for f in p['result']['findings']]
        assert Counter(c['input_id'] for c in answer['claim_reviews']) == Counter(f['id'] for f in answer['retained_source_findings'])
        original_quotes = {(c['source_unit_id'], c['quote']) for f in answer['retained_source_findings'] for c in f['citations']}
        mapped = {u['id']: u for u in row['model_context']['evidence']}
        citation_count = 0
        for finding in answer['findings']:
            for citation in finding['citations']:
                assert (citation['source_unit_id'], citation['quote']) in original_quotes
                u = mapped[citation['source_unit_id']]
                text = u['text'] if u['kind'] == 'narrative' else R.P.context.encode(u['reported_fields']).decode()
                base = u['start'] if u['kind'] == 'narrative' else 0
                assert text[citation['start']-base:citation['end']-base] == citation['quote']
                assert citation['source'] == row['model_context']['sources'][u['source_ref']]['source']
                citation_count += 1
        for binding in answer['reference_bindings']:
            assert binding['evidence_references'] == [answer['reference_manifest'][n] for n in binding['unit_refs']]
        for k in ('received_units','submitted_units','model_accounted_units','unassessed_units'):
            assert answer['coverage'][k] == parent['coverage'][k]
        handoff = read(folder / 'connected_handoff.json')['response']['task']
        assert handoff['handoff']['origin']['question'] == answer['question']
        assert handoff['handoff']['origin']['findings'] == answer['findings']
        assert handoff['handoff']['human_review_completed'] is False
        assert handoff['handoff']['external_dispatch_performed'] is False
        assert all(read(folder / ('reject_' + k + '.json'))['http_status'] in (400, 409) for k in ('unavailable','wrong-plan'))
        assert read(folder / 'replay.json')['response']['new_paid_api_calls'] == 0
        checks.append({'case': case, 'answer_id': answer['id'], 'task_id': handoff['id'],
            'parent_passes_unchanged': len(parent['passes']), 'exact_citations': citation_count,
            'input_claims': len(answer['claim_reviews']), 'source_coverage_upgraded': False,
            'question_patient_sources_handoff_retained': True, 'clinical_validated': False})
    previous = read(OUT / 'before-scorecard.json')
    overrides = read(OUT / 'stage-trace-map.json')
    report = build('research/agere/workflow-evaluation-v1/plan.json', 'artifacts/agere-workflow-evaluation-v1/baseline',
        read('artifacts/agere-planner-contracts-v1/planning-trace-map.json'), overrides)
    metadata = read(OUT / 'scorecard.json')
    for key in ('previous_release_planning','previous_release_source_interpretation','known_gaps'):
        report[key] = metadata[key]
    retention = read(OUT / 'preservation.json')
    report['usage_accounting'] = {k: retention[k] for k in ('new_provider_calls','new_provider_statuses','cost')}
    assert report['stage_counts']['source_interpretation'] == {'blocked_by_scope': 1, 'executed_unvalidated': 5}
    assert report['connected_answer_handoffs'] == 5 and report['complete_workflows'] == 0
    assert previous['stage_counts']['source_interpretation']['runtime_error'] == 3
    write(OUT / 'scorecard.json', report)
    write('artifacts/agere-workflow-evaluation-v1/scorecard.json', report)
    verification = {'status': 'passed', 'cases': checks, 'preservation_receipt': str(OUT / 'preservation.json'),
        'source_interpretations_executed': 5, 'connected_answer_handoffs': 5, 'complete_workflows': 0,
        'verification_provider_calls': 0, 'clinical_validated': False,
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    write(OUT / 'delivery-verification.json', verification)
    print(json.dumps(verification, indent=2))


if __name__ == '__main__': main()
