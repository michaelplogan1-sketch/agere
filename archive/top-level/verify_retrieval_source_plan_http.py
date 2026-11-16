"""Real source-plan transport on two original-training development examples."""
import json
import sqlite3
import time
import urllib.request
import urllib.error
from pathlib import Path
from clinical_core.contracts import digest
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import learning_queue as L, semantic_learning as S
from clinical_core.agere.clinical.context_student import REQUEST_MAX_BYTES  # shared provider request bound

OUT = Path('artifacts/agere-retrieval-source-plan-v1')
meta = json.loads((stack_path()/'run.json').read_text())
policy, _ = L.lineage('artifacts/agere-current-source-learning-v1/acquisition/lineage', meta)
protections = S.protections()


def call(path, body=None):
    req = urllib.request.Request('http://localhost:8885/api/stack/'+path,
        json.dumps(body).encode() if body is not None else None, {'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=600) as response: return json.load(response)
    except urllib.error.HTTPError as e:
        if path == 'context/plan': print(e.read().decode(), flush=True)
        raise


def main():
    results = []
    db = sqlite3.connect((stack_path()/'stack.sqlite3').resolve().as_uri()+'?mode=ro', uri=True)
    for workflow, categories in [('medication', ['medication_orders']), ('payer', ['coverage', 'coverage_verifications'])]:
        comparison = json.loads(Path('artifacts/agere-retrieval-comparison-v1', workflow+'.json').read_text())
        patient = comparison['request']['patient']; assert L.eligibility(patient, policy, protections) is None
        request = {'comparison_id': comparison['id'], 'baseline': 'bge_reranked', 'categories': categories}
        t = time.perf_counter(); p = call('retrieval-source-plan/preview', request)
        assert p['can_prepare'] and p['analysis_window'] == comparison['request']['analysis_window']
        expected = {row[0] for row in db.execute('SELECT id FROM records WHERE patient=? AND stream IN ('+','.join('?' for _ in categories)+')', [patient, *categories])}
        assert expected == set(p['added_record_ids'])  # Both categories were absent from these development selections.
        selected = call('retrieval-source-plan/prepare', {'request': request, 'preview_id': p['id']})
        assert selected['plan']['status'] == 'declared_record_scope_prepared'
        contexts = selected['contexts']; all_ids = [rid for c in contexts for rid in c['record_scope']['record_ids']]
        assert len(all_ids) == len(set(all_ids)) and set(all_ids) == set(p['selected_record_ids'])
        plans = []
        for context in contexts:
            assert context['dispatch']['source_review_allowed'] and not context['omissions']
            assert set(context['model_context']['sources']) == set(context['record_scope']['record_ids'])
            assert call('context?id='+context['id']) == context
            # Existing bounded-interpretation planning; no inference or native token call.
            plan = call('context/plan', {'context_id': context['id']})
            assert plan['can_dispatch'] and plan['source_units'] == context['budget']['candidate_units']
            assert all(n <=REQUEST_MAX_BYTES for n in plan['request_bytes'])
            plans.append(plan)
        for source in p['records']:
            pid, raw = db.execute('SELECT patient,payload FROM records WHERE id=?', (source['record_id'],)).fetchone()
            assert pid == patient and digest(json.loads(raw)) == source['record_hash']
        again = call('retrieval-source-plan/prepare', {'request': request, 'preview_id': p['id']})
        assert again['cache_hit'] and again['plan'] == selected['plan']
        restored = call('retrieval-source-plan/inspect', {'id': selected['plan']['id']})
        assert restored['plan'] == selected['plan'] and restored['contexts'] == contexts
        (OUT/(workflow+'-source-plan.json')).write_text(json.dumps(selected, indent=2)+'\n')
        (OUT/(workflow+'-interpretation-plan.json')).write_text(json.dumps(plans, indent=2)+'\n')
        results.append({'workflow': workflow, 'patient': patient, 'plan_id': selected['plan']['id'],
                        'baseline_records': len(p['baseline_record_ids']), 'added_records': len(p['added_record_ids']),
                        'selected_records': len(p['selected_record_ids']), 'excluded_records': p['excluded_records'],
                        'source_units': sum(c['budget']['selected_units'] for c in contexts), 'omitted_units': sum(c['budget']['omitted_units'] for c in contexts),
                        'context_ids': [c['id'] for c in contexts], 'review_groups': len(contexts),
                        'source_passes': sum(v['source_passes'] for v in plans),
                        'bounded_interpretation_dispatchable': all(v['can_dispatch'] for v in plans),
                        'elapsed_seconds_including_replay_and_local_request_plan': time.perf_counter()-t})
        print(json.dumps(results[-1]), flush=True)
        for bad in ({**request, 'patient': 'P-7193'}, {**request, 'categories': ['not_received']}, {**request, 'categories': categories+categories}):
            try: call('retrieval-source-plan/preview', bad)
            except urllib.error.HTTPError as e: assert e.code == 400
            else: raise AssertionError('Invalid scope accepted')
        try: call('retrieval-source-plan/prepare', {'request': request, 'preview_id': 'stale'})
        except urllib.error.HTTPError as e: assert e.code == 400
        else: raise AssertionError('Stale scope accepted')
    db.close()
    (OUT/'http-verification.json').write_text(json.dumps({'status': 'passed', 'cases': results,
        'semantic_inference_calls': 0, 'clinical_accuracy_assessed': False, 'external_actions': 0}, indent=2)+'\n')


if __name__ == '__main__': main()
