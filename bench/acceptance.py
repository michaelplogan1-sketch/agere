"""Integration acceptance: every registered capability must be reachable from varied phrasings, end to end.

Posts each bench/acceptance/<operation>.json question to a running service and checks that the executed graph
used the expected operation and returned an answer. Reports route mix (compiler / router / SQL / frontier)
and cost. Usage: .venv/bin/python bench/acceptance.py [--port 8886] [--ops survival,monitor] [--jobs 4]
"""
import argparse, json, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent / 'acceptance'


def ask(port, question):
    req = urllib.request.Request(f'http://127.0.0.1:{port}/api/stack/compose', data=json.dumps({'question': question}).encode(),
                                 headers={'Content-Type': 'application/json'})
    start = time.perf_counter()
    try:
        d = json.load(urllib.request.urlopen(req, timeout=900))
    except urllib.error.HTTPError as e:
        return {'error': e.read().decode()[:300], 'seconds': time.perf_counter() - start}
    except Exception as e:  # noqa: BLE001 - report every failure
        return {'error': f'{type(e).__name__}: {e}'[:300], 'seconds': time.perf_counter() - start}
    receipt = d.get('planner_receipt') or {}
    ops = [n.get('operation') for n in (d.get('graph') or {}).get('nodes', [])]
    usd = (d.get('planning_economics') or {}).get('new_api_estimate_usd')
    return {'route': receipt.get('route'), 'operations': ops, 'coverage': (d.get('graph') or {}).get('coverage'),
            'headline': str((d.get('answer') or {}).get('headline') or '')[:160], 'usd': usd,
            'router': (receipt.get('local_compiler_declined') or {}).get('operator_router', {}).get('decision'),
            'seconds': round(time.perf_counter() - start, 1)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--port', type=int, default=8886); p.add_argument('--ops', default=''); p.add_argument('--jobs', type=int, default=4)
    a = p.parse_args()
    cases = [c for f in sorted(ROOT.glob('*.json')) for c in json.loads(f.read_text())
             if not a.ops or c['expect_operation'] in a.ops.split(',')]
    with ThreadPoolExecutor(a.jobs) as pool:
        results = list(pool.map(lambda c: {**c, **ask(a.port, c['question'])}, cases))
    by_op, routes, cost = {}, {}, 0.0
    for r in results:
        r['pass'] = not r.get('error') and bool({r['expect_operation'], *r.get('also_accept', [])} & set(r.get('operations') or []))
        by_op.setdefault(r['expect_operation'], []).append(r['pass'])
        routes[r.get('route') or 'error'] = routes.get(r.get('route') or 'error', 0) + 1
        cost += r.get('usd') or 0
        if not r['pass']:
            print(f"FAIL [{r['expect_operation']}] {r['question']}\n     -> route={r.get('route')} ops={r.get('operations')} "
                  f"router={r.get('router')} {r.get('error') or r.get('headline')}")
    print('\nper capability:')
    for op, v in sorted(by_op.items()): print(f'  {op:15s} {sum(v)}/{len(v)}')
    print('routes:', routes)
    print(f'total {sum(r["pass"] for r in results)}/{len(results)}  cost ${cost:.3f}')
    out = Path(__file__).resolve().parent / 'results' / 'acceptance'
    out.mkdir(parents=True, exist_ok=True)
    (out / (time.strftime('%Y%m%dT%H%M%S') + '.json')).write_text(json.dumps(results, indent=1))


if __name__ == '__main__':
    sys.exit(main())
