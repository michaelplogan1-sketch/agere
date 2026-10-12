"""Reproduce planner delivery checks from saved development traces, without inference."""
import argparse
import hashlib
import json
from pathlib import Path

from clinical_core.agere.clinical.analysis_graph import validate
from clinical_core.agere.workflow_scorecard import build, patient_bound

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'artifacts/agere-planner-contracts-v1'


def read(path):
    return json.loads(Path(path).read_text())


def check():
    plan_path = Path('research/agere/workflow-evaluation-v1/plan.json')
    plan = read(plan_path)
    mapping = read(OUT / 'planning-trace-map.json')
    report = read(OUT / 'scorecard.json')
    rebuilt = build(plan_path, 'artifacts/agere-workflow-evaluation-v1/baseline', mapping)
    assert all(report[key] == value for key, value in rebuilt.items()), 'Scorecard no longer reproduces'
    rows = []
    for case in plan['cases']:
        path = Path(mapping[case['id']]) / 'planning.json'
        trace = read(path)
        assert trace['status'] == 'executed', case['id']
        response = trace['response']
        graph = response['graph']
        normalized = validate(graph, plan['snapshot']['as_of'])
        assert normalized == graph, case['id']
        assert patient_bound(graph['nodes'], case['patient']), case['id']
        assert response['question'] == case['planning_question'], case['id']
        assert graph['coverage'] == 'partial' and graph['unmet_requirements'], case['id']
        rows.append({'case': case['id'], 'trace': str(path),
                     'trace_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                     'runtime_valid': True, 'exact_patient_scope': True,
                     'original_planning_question_retained': True, 'coverage': 'partial'})
    assert report['stage_counts']['planning'] == {'executed_unvalidated': 6}
    assert report['complete_workflows'] == 0 and report['clinical_validated'] is False
    return {'status': 'passed', 'cases': rows, 'scorecard_reproduced': True,
            'provider_calls': 0, 'complete_workflows': 0, 'clinical_validated': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = check()
    if args.output:
        with args.output.open('x') as stream:
            json.dump(result, stream, indent=2)
            stream.write('\n')
    print(json.dumps(result, indent=2))
