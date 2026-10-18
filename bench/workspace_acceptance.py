"""Offline acceptance run of the full workspace pipeline (onboarding/workspace_stack.py) on a native workspace: every
bench/acceptance/*.json phrasing through together.compose_together with live models off (local compiler only, no hosted
calls). Usage: .venv/bin/python bench/workspace_acceptance.py <workspace dir> [ops,comma,separated] [out.json]
"""
import json, os, signal, sys, time, traceback
def _alarm(*a): raise TimeoutError('question exceeded 240 s')
signal.signal(signal.SIGALRM, _alarm)
from collections import Counter, defaultdict
from pathlib import Path

for k, v in {'AGERE_LOCAL_MODEL': 'off', 'AGERE_BOTTLED': 'off', 'AGERE_LOCAL_SQL': 'off', 'AGERE_SEMANTIC_CACHE': 'off',
             'AGERE_VBC_SYNTHESIS': 'off'}.items():
    os.environ[k] = v
ROOT = Path('/Users/mikel/clinical-insights-local')
sys.path.insert(0, str(ROOT))
from clinical_core.agere.onboarding import workspace_stack
from clinical_core.agere.clinical import together

ws = Path(sys.argv[1])
ops = sys.argv[2].split(',') if len(sys.argv) > 2 and sys.argv[2] else None
out_path = Path(sys.argv[3]) if len(sys.argv) > 3 else None
stack = workspace_stack.stack_for(ws, allow_live=False)
cases = [c for f in sorted((ROOT / 'bench/acceptance').glob('*.json')) for c in json.loads(f.read_text())
         if not ops or c['expect_operation'] in ops]
rows = []
for c in cases:
    t = time.perf_counter()
    row = {'expect': c['expect_operation'], 'question': c['question']}
    signal.alarm(240)
    try:
        r = together.compose_together(stack, c['question'])
        nodes = [n.get('operation') for n in (r.get('graph') or {}).get('nodes', [])]
        dec = r.get('workspace_decline')
        a = r.get('answer') or {}
        row.update(route=(r.get('planner_receipt') or {}).get('route'), ops=nodes or (dec or {}).get('operations'),
                   status=a.get('status'), coverage=(r.get('graph') or {}).get('coverage'),
                   headline=str(a.get('headline') or '')[:200], decline=(dec or {}).get('missing'),
                   unmet=(r.get('graph') or {}).get('unmet_requirements'), paid=r.get('new_paid_api_calls'),
                   together=(r.get('together') or {}).get('decision'))
    except Exception as exc:  # noqa: BLE001
        row.update(error=f'{type(exc).__name__}: {exc}'[:400], tb=traceback.format_exc()[-1500:])
    signal.alarm(0)
    row['seconds'] = round(time.perf_counter() - t, 2)
    rows.append(row)
    if out_path:
        with open(str(out_path) + 'l', 'a') as f: f.write(json.dumps(row, default=str) + '\n')
    print(json.dumps({k: row.get(k) for k in ('expect', 'route', 'ops', 'status', 'coverage', 'decline', 'error', 'seconds', 'headline')})[:700], flush=True)
if out_path:
    out_path.write_text(json.dumps(rows, indent=1, default=str))
