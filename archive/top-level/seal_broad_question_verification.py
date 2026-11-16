"""Verify captured development workflows without repeating provider requests."""
import hashlib
import json
import sqlite3
from pathlib import Path

from clinical_core.agere.clinical import analysis_graph
from clinical_core.agere.economics import usage_cost
from verify_broad_question_planning import QUESTIONS

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'artifacts/agere-broad-question-planning-v1'
PROVIDER = ROOT / 'artifacts/agere-healthcare-v123/stack/provider-receipts'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(name, value):
    with (OUT / name).open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')


def requests(value):
    if isinstance(value, dict):
        if value.get('request_hash'):
            yield value['request_hash']
        for child in value.values():
            yield from requests(child)
    elif isinstance(value, list):
        for child in value:
            yield from requests(child)


def main():
    traces = {}
    keys = set()
    for stage in ('baseline', 'candidate', 'final'):
        for name, question in QUESTIONS.items():
            path = OUT / stage / (name + '.json')
            if not path.exists():
                continue
            capture = json.loads(path.read_text())
            assert capture['request']['question'] == question
            keys.update(requests(capture))
            result = capture['response']
            graph = result.get('graph', {})
            traces[stage + '/' + name] = {
                'status': capture['status'], 'seconds': capture['elapsed_seconds'],
                'coverage': graph.get('coverage'), 'nodes': len(graph.get('nodes', [])),
                'new_paid_api_calls_reported': result.get('new_paid_api_calls'),
                'error': result.get('error'), 'sha256': sha(path),
            }
            if stage != 'final':
                continue
            assert capture['status'] == 200 and graph['coverage'] == 'partial'
            assert graph['nodes'] and graph['unmet_requirements']
            assert result['question'] == question
            assert result['clinical_execution_authorized'] is False
            analysis_graph.validate(graph, result['snapshot']['as_of'])
            cohort = result['results'][0]
            assert cohort['node']['operation'] == 'cohort'
            counts = cohort['result']['counts']
            assert counts['evaluated'] == 1614
            expected = 1614 if name in ('aco', 'performance') else 1
            assert counts['matched'] == expected
            if expected == 1:
                patient = 'P-1006' if name == 'admission' else 'P-3002'
                assert cohort['result']['examples']['matched'] == [patient]
            traces[stage + '/' + name]['cohort_counts'] = counts
            traces[stage + '/' + name]['source_digest'] = result['source_digest']
            if name != 'cross_domain':
                assert result['new_paid_api_calls'] == 0
                previous = json.loads((OUT / 'candidate' / (name + '.json')).read_text())['response']
                assert previous['graph'] == graph
            else:
                assert result['new_paid_api_calls'] == 1

    assert len({traces['final/' + n]['source_digest'] for n in QUESTIONS}) == 1
    before = json.loads((OUT / 'baseline/admission-plan-request.json').read_text())
    after = json.loads((OUT / 'candidate-admission-plan-request.json').read_text())
    byte_comparison = {'before': len(json.dumps(before).encode()),
                       'after': len(json.dumps(after).encode()), 'guard': 20000,
                       'basis': 'Same original admission selector scope; no provider call for this controlled comparison.'}
    assert byte_comparison['before'] == 20074 and byte_comparison['after'] == 19964

    # HTTP failures omit planner stage receipts. Preserve their returned usage and
    # exact-question selector keys, rather than dropping failed-call cost.
    failed = {
        '368878332aadd3b3aa6fa27d7863ece95d0109de1425afd20590b36caed6c717': 'baseline/care_context',
        '30bbb0b369ca5583b97117a40ef1c97a890605671dc3eaf242506ff5e8d0ea53': 'baseline/performance',
        '340eab5bb4e99bf2c7127ecdf159b667f858d4955c16f529de74dc438430e1ab': 'candidate/cross_domain',
    }
    missing_selectors = {
        '39c287373c47a131b8823ec096d0e9d9209ae424f160ae3a93420fdae5bf4427': 'care_context',
        '5478a57784048cda6c9902b12e7ced7d8ca8fdbfe255b834e5d5ba14c79394c5': 'performance',
    }
    keys.update(failed)
    keys.update(missing_selectors)
    ledger = sqlite3.connect('file:' + str(ROOT / 'artifacts/agere-full/provider-budget.sqlite3') + '?mode=ro', uri=True)
    costs = []
    for key in sorted(keys):
        path = PROVIDER / (key + '.json')
        value = json.loads(path.read_text())
        raw = value.get('response', value)
        if key in missing_selectors:
            texts = [json.loads(c['text']) for c in raw['content'] if c['type'] == 'text']
            assert ''.join(s['text'] for s in texts[0]['segments']) == QUESTIONS[missing_selectors[key]]
        row = ledger.execute('SELECT status FROM calls WHERE id=?', (key,)).fetchone()
        assert row is not None
        if key in failed:
            assert row[0] == 'failed'
            assert (PROVIDER / (key + '-failure.json')).is_file()
        costs.append({'request_hash': key, 'ledger_status': row[0],
                      'failed_case': failed.get(key), 'response_sha256': sha(path),
                      'usage': raw['usage'], 'model': raw['model'],
                      'estimate': usage_cost(raw['model'], raw['usage'])})
    ledger.close()
    save('cost-accounting.json', {
        'scope': '18 distinct returned product-provider requests for these development traces; includes failed validation and selector calls.',
        'requests': costs, 'unique_requests': len(costs),
        'estimated_usd': round(sum(c['estimate']['estimated_usd'] for c in costs), 8),
        'failed_request_estimated_usd': round(sum(c['estimate']['estimated_usd'] for c in costs if c['failed_case']), 8),
        'invoice_verified': False,
        'excluded': ['Codex/Astra engineering conversation', 'local hardware', 'human review'],
    })
    baseline = json.loads((OUT / 'preservation-baseline.json').read_text())
    changed = [name for name, value in baseline.items() if not (ROOT / name).is_file() or sha(ROOT / name) != value]
    assert not changed, changed
    save('preservation-final.json', {'checked': len(baseline), 'changed': changed,
                                   'scope': '170 protected files and 148 prior implementation receipts'})
    save('verification.json', {
        'status': 'passed', 'gate': 'G3', 'traces': traces,
        'original_four_executable_before': 1, 'original_four_executable_after': 4,
        'five_development_cases_executable_final': 5,
        'request_bytes': byte_comparison, 'focused_tests': 32,
        'browser_evidence': 'browser-final.txt',
        'browser_check': 'Actual 8885 UI submitted original care/context question; 20 included evidence units, no omissions, source-state/review/acquisition/portfolio results, zero new paid calls.',
        'restarted_runtime': {'port': 8885, 'pid': 48253, 'session': 93199},
        'mvp_http': {'port': 5178, 'path': '/app.html', 'status': 200},
        'independent_generalization_evaluation': False,
        'clinical_qualification': False, 'gate_promoted': False,
        'limits': 'All five answers remain partial. Cross-domain case was repaired after failure and is a development regression, not an independent holdout. Cold cost/latency and warm replay are not equivalent baselines.',
    })
    print(json.dumps({'status': 'passed', 'cases': 5, 'protected': len(baseline),
                      'provider_requests': len(costs),
                      'estimated_usd': round(sum(c['estimate']['estimated_usd'] for c in costs), 8)}))


if __name__ == '__main__':
    main()
