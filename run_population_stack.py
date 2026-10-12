"""Execute the composed population layer against the preserved synthetic index."""
import argparse
import json
from pathlib import Path
import sqlite3

from clinical_core.agere.clinical.stack_runtime import Stack
from clinical_core.agere.clinical.graph_workbench import examples
from run_integrated_stack import keys


def direct_counts(path):
    db = sqlite3.connect(path/'stack.sqlite3')
    try:
        universe = {r[0] for r in db.execute('SELECT id FROM patients')}
        # Independent scan of raw observed ADT payloads; not the cohort evaluator.
        admission = {pid for pid, raw in db.execute("SELECT patient,payload FROM records WHERE stream='adt'")
                     if json.loads(raw).get('event') == 'A01'}
    finally: db.close()
    link = json.loads((path/'annotation-link.json').read_text())
    db = sqlite3.connect(Path(link['path'])/'evidence.sqlite3')
    try:
        text, screen = set(), set()
        for pid, kind, concept, assertion, subject, value in db.execute('SELECT patient,kind,concept,assertion,subject,value_json FROM evidence'):
            if kind == 'text' and concept == 'financial hardship' and assertion == 'affirmed' and subject == 'patient': text.add(pid)
            if kind == 'structured' and concept == 'financial_hardship' and json.loads(value) is True: screen.add(pid)
    finally: db.close()
    counts = []
    for group in (text, screen):
        counts.append({'group_and_endpoint': len(group & admission), 'group_without_observed_endpoint': len(group-admission),
                       'complement_and_endpoint': len(admission-group), 'complement_without_observed_endpoint': len(universe-group-admission)})
    return {'patients': len(universe), 'admission_notification': len(admission), 'narrative_hardship': len(text),
            'positive_screen': len(screen), 'narrative_without_positive_screen': len(text-screen),
            'positive_screen_without_narrative_annotation': len(screen-text),
            'narrative_and_admission': len(text & admission), 'comparisons': counts}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stack', type=Path, default=Path('artifacts/agere-population-stack-v3'))
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    output = args.stack/'examples'; output.mkdir(exist_ok=True)
    if args.live: keys()
    stack = Stack(args.stack, allow_live=args.live)
    try:
        if args.live:
            question = examples()[0]['question']
            result = stack.compose(question,planning='frontier')
            (output/'frontier-composition.json').write_text(json.dumps(result, indent=2, allow_nan=False))
            print(json.dumps({'route': result['route'], 'coverage': result['graph']['coverage'],
                              'nodes': [(x['node']['id'], x['node']['operation']) for x in result['results']],
                              'planner_receipt': result['planner_receipt'], 'elapsed_seconds': result['elapsed_seconds']}, indent=2))
        else:
            outputs = []
            for i, example in enumerate(examples()):
                (output/f'graph-{i+1}.json').write_text(json.dumps(example['graph'], indent=2))
                result = stack.run_graph(example['graph']); outputs.append(result)
                (output/f'composition-{i+1}.json').write_text(json.dumps(result, indent=2, allow_nan=False))
            independent = direct_counts(args.stack)
            comparison = outputs[0]['results'][4]['result']['comparisons']
            assert [r['cells'] for r in comparison] == independent['comparisons']
            assert outputs[1]['results'][2]['result']['counts']['matched'] == independent['positive_screen_without_narrative_annotation']
            assert outputs[1]['results'][3]['result']['summary']['patients'] == independent['positive_screen_without_narrative_annotation']
            report = {'synthetic': True, 'independent_observed_counts': independent,
                      'count_check': 'Independent raw-record/evidence scan agrees with both composed graphs.',
                      'new_paid_api_calls': 0, 'examples': [r['result_id'] for r in outputs],
                      'clinical_accuracy_measured': False}
            (output/'direct-count-check.json').write_text(json.dumps(report, indent=2))
            print(json.dumps(report, indent=2))
    finally: stack.close()


if __name__ == '__main__': main()
