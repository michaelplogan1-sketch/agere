"""Pre-measurement smoke suite: run before any blind benchmark so self-inflicted breakage is caught in minutes.

1. Development + challenge sets must stay at their known score (49/50).
2. Known-answer probes across routes (local ops, medications, hosted) must return their verified values.
3. Hosted probes must not fall through to the frontier fallback because of validation/infrastructure errors.
Usage: .venv/bin/python bench/smoke.py   (needs the service on :8885)
"""
import json, subprocess, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBES = [  # question, expected route prefix, expected value (count/value) or None to only check the route
    ('How many claims were denied and then paid?', 'local_compiler', 959),
    ('What was our no-show rate for appointments scheduled in 2027?', 'local_compiler', 8.98),
    ('Which diabetics are not on a statin?', 'local_compiler', 119),
    ('What % of our heart failure patients are on an evidence-based beta blocker?', 'local_compiler', 45.79),
    ('Which CKD patients are likely to drop below an eGFR of 30 in the next year?', 'local_compiler', None),
    ('How many lab results came in without any collection date?', 'hosted_verified_sql', 16543),
    ('Average days from service date to first payer cash for 2028 medical claims?', 'hosted_verified_sql', None),
    ('How many patients filled a first-generation antihistamine in 2028?', 'hosted_verified_sql', None),
    ('What is the race breakdown of our diabetic patients?', 'local_unsupported', None),
]


def ask(q):
    req = urllib.request.Request('http://127.0.0.1:8885/api/stack/compose', data=json.dumps({'question': q}).encode(),
                                 headers={'content-type': 'application/json'})
    try:
        return json.load(urllib.request.urlopen(req, timeout=600))
    except urllib.error.HTTPError as e:
        return {'error': e.read().decode()[:300]}


def main():
    failures = []
    for q, route, expected in PROBES:
        d = ask(q)
        pr = d.get('planner_receipt') or {}
        a = d.get('answer') or {}
        got = a.get('count') if a.get('count') is not None else a.get('value')
        if a.get('patient_ids') and expected is not None and a.get('answer_type') == 'patient_set': got = len(a['patient_ids'])
        ok_route = (pr.get('route') or '').startswith(route)
        ok_value = expected is None or (isinstance(got, (int, float)) and abs(got - expected) <= max(0.01, abs(expected) * 0.001))
        print(f"{'PASS' if ok_route and ok_value else 'FAIL'}  {q[:70]:70s} route={pr.get('route')} got={got} {d.get('error', '')[:120]}")
        if not (ok_route and ok_value): failures.append(q)
    run = subprocess.run([str(ROOT / '.venv/bin/python'), str(ROOT / 'bench/run.py'), '--system', 'agere', '--jobs', '2', '--ids',
                          *[f'D{i:02d}' for i in range(1, 31)], *[f'C{i:02d}' for i in range(1, 21)]], capture_output=True, text=True, cwd=ROOT)
    latest = max((ROOT / 'bench/results/agere').glob('*.jsonl'), key=lambda p: p.stat().st_mtime)
    correct = sum(1 for l in latest.open() if json.loads(l)['score'].get('correct'))
    print(f"{'PASS' if correct >= 49 else 'FAIL'}  development+challenge {correct}/50")
    if correct < 49: failures.append('development+challenge')
    print('SMOKE', 'PASSED' if not failures else f'FAILED ({len(failures)})')
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
