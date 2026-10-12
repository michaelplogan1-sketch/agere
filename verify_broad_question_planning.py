"""Capture actual broad question responses; replay exact saved questions for UI checks."""
import argparse
import hashlib
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

OUT = Path(__file__).parent / 'artifacts/agere-broad-question-planning-v1'
QUESTIONS = {
    'admission': 'For patient P-1006, why was the patient admitted or readmitted? Show the observed admission timeline and supporting records, and distinguish documented reasons from possible contributors and missing evidence.',
    'care_context': 'For patient P-3002, what care appears incomplete and what transportation, housing, financial or caregiver circumstances might affect follow-through? Separate documented barriers from unassessed possibilities and identify evidence needed before recommending an intervention.',
    'aco': 'Across the entire observed population, which patients should an ACO focus on to improve quality and reduce downside risk while pursuing shared savings? Distinguish supported review opportunities from unknown attribution, contract definitions and intervention benefit.',
    'performance': 'How are physicians and hospitals performing across the observed population on utilization and readmissions? Show what the available data support and identify missing attribution, denominators and case-mix adjustment before making comparisons.',
    'cross_domain': 'For patient P-3002, connect recorded financial barriers, appointment follow-through and received payment accounts. Which relationships are documented, which are only associations, and what evidence is missing before linking these patterns to deterioration?',
}


def capture(name, directory, base):
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (name + '.json')
    if target.exists():
        print(name, 'already captured; no request repeated', flush=True)
        return
    question = QUESTIONS[name]
    request = {'question': question, 'live': True}
    started = time.perf_counter()
    try:
        req = urllib.request.Request(base + '/api/stack/ask', data=json.dumps(request).encode(),
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=600) as response:
            status, body = response.status, json.load(response)
    except urllib.error.HTTPError as error:
        status, body = error.code, json.loads(error.read())
    elapsed = time.perf_counter() - started
    saved = {'request': request, 'status': status, 'elapsed_seconds': elapsed, 'response': body}
    with target.open('x') as stream:
        json.dump(saved, stream, indent=2)
    print(json.dumps({'case': name, 'status': status, 'seconds': round(elapsed, 2),
        'schema': body.get('schema'), 'coverage': body.get('graph', {}).get('coverage'),
        'nodes': [n['operation'] for n in body.get('graph', {}).get('nodes', [])],
        'unmet': body.get('graph', {}).get('unmet_requirements'),
        'new_paid_api_calls': body.get('new_paid_api_calls'), 'error': body.get('error')}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', choices=[*QUESTIONS, 'all'], default='all')
    parser.add_argument('--stage', default='baseline')
    parser.add_argument('--base', default='http://127.0.0.1:8885')
    args = parser.parse_args()
    for name in QUESTIONS if args.case == 'all' else [args.case]:
        capture(name, OUT / args.stage, args.base)
