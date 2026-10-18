"""Hook called by Workspace._record: register what an answer read so later ticks can mark it stale.

Kind is decided from the route: cohort-family routes (cohort_family, or learned_verified_sql carrying a family) are
re-run deterministically; other answers whose graph is SQL nodes written by code (local_compiler, governed metrics,
operators) are re-run as stored SQL; anything planned or written by a model is 'model' (flagged for re-ask).
Never raises: the registry is an annotation. Only workspaces that streaming already maintains are registered.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import enabled

CODE_ROUTES = ('local_compiler', 'governed_metric', 'metric_router', 'operator', 'cohort_family')


def _queries(out):
    graph = ((out.get('result') or {}).get('graph')) or {}
    qs = []
    for n in graph.get('nodes') or []:
        if isinstance(n, dict) and n.get('operation') == 'sql':
            q = (n.get('parameters') or {}).get('query')
            if q:
                qs.append(q)
    return qs


def on_answer(ws, question, out):
    if not enabled() or not question:
        return None
    # only workspaces already maintained by streaming (bootstrapped or ticked) get a registry; others stay untouched
    if not (Path(ws.dir) / 'streaming' / 'streaming.sqlite3').is_file() and not (Path(ws.dir) / 'stack' / 'streaming-state.sqlite3').is_file():
        return None
    try:
        from . import replay
        route = str(out.get('route') or out.get('decision') or '')
        qs = _queries(out)
        a = out.get('answer') if isinstance(out.get('answer'), dict) else {}
        ids = a.get('patient_ids') if isinstance(a.get('patient_ids'), list) else None
        if 'cohort_family' in route or (route == 'learned_verified_sql' and 'Cohort family' in json.dumps(out.get('result') or {})[:20000]):
            kind = 'cohort_family'
        elif len(qs) == 1 and any(r in route for r in CODE_ROUTES):
            kind = 'sql'
        else:
            kind = 'model'
        aid = 'ask:' + hashlib.sha256(question.strip().lower().encode()).hexdigest()[:16]
        tables = sorted({t for q in qs for t in replay.tables_read(q, ws.semantic_path)}) if qs else None
        return replay.register(ws.dir, aid, question, kind=kind, semantic_path=ws.semantic_path, as_of=ws.state.get('as_of'),
                               patient_ids=ids, count=a.get('count') if ids is None else None, query=';\n'.join(qs) or None,
                               tables=tables if tables is not None else [], as_of_mode='rolling')
    except Exception:  # noqa: BLE001 - the dependency registry never blocks an answer
        return None
