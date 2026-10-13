"""User-flagged mistakes as permanent tests: replay every bench/regressions/<id>.json against a running service.

Cases come from corrections (/api/stack/correct) via `python -m tools.learning_loop --cases-dir bench/regressions`.
Each case is one unittest test: the question is posted to /api/stack/compose (like bench/acceptance.py) and the
answer must satisfy the case's checks (value within tolerance, route prefix, operation in the graph, status,
answer type, headline text, or - for weak cases with no structured expectation - differ from the flagged answer).
Usage: .venv/bin/python bench/regressions.py [--port 8885] [--dir bench/regressions] [-v]
"""
import argparse, json, sys, unittest, urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent / 'regressions'


def ask(port, question, timeout=900):
    req = urllib.request.Request(f'http://127.0.0.1:{port}/api/stack/compose', data=json.dumps({'question': question}).encode(),
                                 headers={'Content-Type': 'application/json'})
    try:
        return json.load(urllib.request.urlopen(req, timeout=timeout))
    except urllib.error.HTTPError as e:
        return {'error': e.read().decode()[:300]}
    except Exception as e:  # noqa: BLE001 - every failure is a failed case
        return {'error': f'{type(e).__name__}: {e}'[:300]}


def observed(response):
    a = response.get('answer') or {}
    got = a.get('count') if a.get('count') is not None else a.get('value')
    if got is None and isinstance(a.get('patient_ids'), list) and a.get('answer_type') == 'patient_set': got = len(a['patient_ids'])
    return {'route': (response.get('planner_receipt') or {}).get('route'), 'status': a.get('status'), 'answer_type': a.get('answer_type'),
            'headline': str(a.get('headline') or ''), 'value': got, 'error': response.get('error'),
            'operations': [n.get('operation') for n in (response.get('graph') or {}).get('nodes', [])]}


def check(case, response):
    """-> list of failure messages (empty = pass)."""
    got, c, fails = observed(response), case['checks'], []
    if got['error']: return [f"service error: {got['error']}"]
    if 'value' in c:
        tol = float(c.get('tolerance', max(0.01, abs(c['value']) * 0.001)))
        if not isinstance(got['value'], (int, float)) or abs(got['value'] - c['value']) > tol:
            fails.append(f"value {got['value']!r} != expected {c['value']} (±{tol})")
    if 'route_prefix' in c and not str(got['route'] or '').startswith(c['route_prefix']):
        fails.append(f"route {got['route']!r} does not start with {c['route_prefix']!r}")
    if 'not_route' in c and str(got['route'] or '').startswith(c['not_route']):
        fails.append(f"route {got['route']!r} is the corrected route {c['not_route']!r}")
    if 'operation' in c and c['operation'] not in got['operations']:
        fails.append(f"operation {c['operation']!r} not in {got['operations']}")
    for k in ('status', 'answer_type'):
        if k in c and got[k] != c[k]: fails.append(f"{k} {got[k]!r} != {c[k]!r}")
    if 'headline_contains' in c and str(c['headline_contains']).lower() not in got['headline'].lower():
        fails.append(f"headline does not contain {c['headline_contains']!r}: {got['headline'][:160]!r}")
    if c.get('differs_from_observed'):
        before = (case.get('observed') or {})
        same_headline = before.get('headline') and before.get('headline') == got['headline']
        same_shape = (not before.get('headline')) and before.get('route') == got['route'] and before.get('status') == got['status']
        if same_headline or same_shape: fails.append('answer unchanged from the one the user flagged')
    return fails


def load_cases(directory=ROOT):
    return [json.loads(f.read_text()) for f in sorted(Path(directory).glob('*.json'))]


def suite(cases, asker):
    """One unittest.TestCase per regression case; asker(question) -> service response."""
    class Regressions(unittest.TestCase):
        pass

    def make(case):
        def test(self):
            fails = check(case, asker(case['question']))
            if fails: self.fail(f"{case['id']} {case['question'][:80]!r}: " + '; '.join(fails) + (f" (note: {case.get('note')})" if case.get('note') else ''))
        test.__doc__ = f"{case['id']}{' (weak)' if case.get('weak') else ''}: {case['question'][:100]}"
        return test
    for case in cases: setattr(Regressions, f"test_{case['id']}", make(case))
    return unittest.defaultTestLoader.loadTestsFromTestCase(Regressions)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--port', type=int, default=8885); p.add_argument('--dir', default=str(ROOT)); p.add_argument('-v', action='store_true')
    a = p.parse_args(argv)
    cases = load_cases(a.dir)
    if not cases:
        print(f'no regression cases in {a.dir} (run python -m tools.learning_loop --cases-dir bench/regressions)'); return 0
    result = unittest.TextTestRunner(verbosity=2 if a.v else 1).run(suite(cases, lambda q: ask(a.port, q)))
    print(f'regressions {result.testsRun - len(result.failures) - len(result.errors)}/{result.testsRun} pass')
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
