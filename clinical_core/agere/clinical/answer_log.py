"""Answer telemetry: route, API calls, cost, latency and status for every question asked.

Makes the token-economics claim measurable in production: what share of questions
Agere answers without any model call, what each rung costs, and how fast it is.
"""
import hashlib, json, sqlite3, time
from pathlib import Path
from .evaluation import active as _evaluating

DDL = """CREATE TABLE IF NOT EXISTS answers(at REAL, question_sha TEXT, question TEXT, route TEXT, status TEXT, answer_type TEXT,
  api_calls INTEGER, api_usd REAL, seconds REAL, error TEXT)"""


def record(path, question, result=None, error=None, seconds=0.0, question_id=None):
    if _evaluating(): return
    db = sqlite3.connect(Path(path) / 'answers.sqlite3')
    try:
        db.execute(DDL)
        if 'question_id' not in {r[1] for r in db.execute('PRAGMA table_info(answers)')}:  # joins answer_costs (cost_telemetry)
            db.execute('ALTER TABLE answers ADD COLUMN question_id TEXT')
        a = (result or {}).get('answer') or {}
        econ = (result or {}).get('planning_economics') or {}
        route = ((result or {}).get('planner_receipt') or {}).get('route') or ('error' if error else None)
        calls = (result or {}).get('new_paid_api_calls')
        cost = econ.get('new_api_estimate_usd', econ.get('known_api_estimate_usd'))
        if calls == 0 and not error and cost is None:
            cost = 0.0
        # Unknown failed-attempt usage is NULL, never an invented zero-cost run.
        if error and result is None:
            calls, cost = None, None
        db.execute('INSERT INTO answers(at,question_sha,question,route,status,answer_type,api_calls,api_usd,seconds,error,question_id) '
                   'VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                   (time.time(), hashlib.sha256(question.encode()).hexdigest(), question[:500], route,
                    a.get('status') or ('error' if error else None), a.get('answer_type'),
                    calls, cost, round(seconds, 3),
                    (error or '')[:300], question_id))
        db.commit()
    finally:
        db.close()


def summary(path):
    target = Path(path) / 'answers.sqlite3'
    if not target.exists(): return {'questions': 0}
    db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    try:
        # The request-wide ledger telemetry includes readers, artifacts and
        # worker threads that the planner-only historical columns omit.
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns={r[1] for r in db.execute('PRAGMA table_info(answers)')}
        if 'answer_costs' in tables and 'question_id' in columns:
            rows=db.execute("SELECT a.route,a.status,CASE WHEN c.question_id IS NOT NULL THEN c.api_calls ELSE a.api_calls END, "
                "CASE WHEN c.question_id IS NOT NULL THEN CASE WHEN c.usd_known=1 THEN c.api_usd ELSE NULL END ELSE a.api_usd END,a.seconds "
                "FROM answers a LEFT JOIN answer_costs c ON c.question_id=a.question_id").fetchall()
        else:rows=db.execute('SELECT route,status,api_calls,api_usd,seconds FROM answers').fetchall()
        total=len(rows);calls=sum(r[2] or 0 for r in rows);usd=sum(r[3] or 0 for r in rows)
        unknown=sum(r[2] is None or r[3] is None for r in rows)
        import statistics
        by = {}
        for route, status, calls_, usd_, secs in rows:
            route=route or 'unknown'
            r = by.setdefault(route, {'route': route, 'questions': 0, 'api_calls': 0, 'api_usd': 0.0, 'seconds': []})
            r['questions'] += 1; r['api_calls'] += calls_ or 0; r['api_usd'] += usd_ or 0.0; r['seconds'].append(secs or 0.0)
        routes = sorted(({**{k: v for k, v in r.items() if k != 'seconds'}, 'api_usd': round(r['api_usd'], 4),
                          'median_seconds': round(statistics.median(r['seconds']), 2)} for r in by.values()), key=lambda r: -r['questions'])
        zero = sum(calls_ == 0 and status == 'answered' for _,status,calls_,_,_ in rows)
    finally:
        db.close()
    return {'questions': total, 'answered_without_api_calls': zero, 'share_without_api_calls': round(zero / total, 4) if total else None,
            'api_calls': calls, 'api_usd': round(usd, 4), 'unknown_usage_questions': unknown,
            'cost_scope': 'Request-wide telemetry where available, otherwise historical planner estimates. Prices are those recorded at execution; subscription allocation and local costs are excluded.',
            'usd_per_question': round(usd / total, 6) if total and not unknown else None, 'routes': routes}
