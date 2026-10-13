"""Pre-measurement smoke suite: run before any blind benchmark so self-inflicted breakage is caught in minutes.

1. Development + challenge sets must stay at their known score (49/50).
2. Known-answer probes across routes (local ops, medications, hosted) must return their verified values.
3. Hosted probes must not fall through to the frontier fallback because of validation/infrastructure errors.
Usage: .venv/bin/python bench/smoke.py   (needs the service on :8885)
"""
import json, os, subprocess, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URL = os.environ.get('AGERE_URL', 'http://127.0.0.1:8885')
# question -> (expected route prefix, {snapshot as_of: expected count/value}); a value of None checks the route only.
# A new warehouse release starts with no baseline: probes then check the route and print BASELINE NEEDED until
# values verified against independent SQL are added here.
PROBES = [
    ('How many claims were denied and then paid?', 'local_compiler', {'2029-08-15': 959}),
    ('What was our no-show rate for appointments scheduled in 2027?', 'local_compiler', {'2029-08-15': 8.98}),
    ('Which diabetics are not on a statin?', 'local_compiler', {'2029-08-15': 119}),
    ('What % of our heart failure patients are on an evidence-based beta blocker?', 'local_compiler', {'2029-08-15': 45.79}),
    ('Which CKD patients are likely to drop below an eGFR of 30 in the next year?', 'local_compiler', {'2029-08-15': None}),
    ('How many lab results came in without any collection date?', 'hosted_verified_sql', {'2029-08-15': 16543}),
    ('Average days from service date to first payer cash for 2028 medical claims?', 'hosted_verified_sql', {'2029-08-15': None}),
    ('How many patients filled a first-generation antihistamine in 2028?', 'hosted_verified_sql', {'2029-08-15': None}),
    ('What is the race breakdown of our diabetic patients?', 'local_unsupported', {'2029-08-15': None}),  # v2.0 adds race
]


def active_as_of():
    try:
        return json.loads((ROOT / 'config/agere/active_warehouse.json').read_text())['snapshot']['as_of']
    except (OSError, ValueError, KeyError):
        return '2029-08-15'


def ask(q):
    req = urllib.request.Request(URL + '/api/stack/compose', data=json.dumps({'question': q}).encode(),
                                 headers={'content-type': 'application/json'})
    try:
        return json.load(urllib.request.urlopen(req, timeout=600))
    except urllib.error.HTTPError as e:
        return {'error': e.read().decode()[:300]}


def main():
    failures = []
    as_of = active_as_of()
    for q, route, baselines in PROBES:
        if as_of not in baselines:
            print(f"BASELINE NEEDED  {q[:70]:70s} (snapshot {as_of})"); continue
        expected = baselines[as_of]
        d = ask(q)
        pr = d.get('planner_receipt') or {}
        a = d.get('answer') or {}
        got = a.get('count') if a.get('count') is not None else a.get('value')
        if a.get('patient_ids') and expected is not None and a.get('answer_type') == 'patient_set': got = len(a['patient_ids'])
        ok_route = (pr.get('route') or '').startswith(route)
        ok_value = expected is None or (isinstance(got, (int, float)) and abs(got - expected) <= max(0.01, abs(expected) * 0.001))
        print(f"{'PASS' if ok_route and ok_value else 'FAIL'}  {q[:70]:70s} route={pr.get('route')} got={got} {d.get('error', '')[:120]}")
        if not (ok_route and ok_value): failures.append(q)
    run = subprocess.run([str(ROOT / '.venv/bin/python'), str(ROOT / 'bench/run.py'), '--system', 'agere', '--jobs', '2', '--url', URL, *(['--as-of', as_of] if as_of != '2029-08-15' else []), '--ids',
                          *[f'D{i:02d}' for i in range(1, 31)], *[f'C{i:02d}' for i in range(1, 21)]], capture_output=True, text=True, cwd=ROOT)
    latest = max((ROOT / 'bench/results/agere').glob('*.jsonl'), key=lambda p: p.stat().st_mtime)
    correct = sum(1 for l in latest.open() if json.loads(l)['score'].get('correct'))
    print(f"{'PASS' if correct >= 49 else 'FAIL'}  development+challenge {correct}/50")
    if correct < 49: failures.append('development+challenge')
    print('SMOKE', 'PASSED' if not failures else f'FAILED ({len(failures)})')
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
