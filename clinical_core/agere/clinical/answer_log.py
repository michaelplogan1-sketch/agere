"""Answer telemetry: route, API calls, cost, latency and status for every question asked.

Makes the token-economics claim measurable in production: what share of questions
Agere answers without any model call, what each rung costs, and how fast it is.
"""
import hashlib, json, sqlite3, time
from pathlib import Path

DDL = """CREATE TABLE IF NOT EXISTS answers(at REAL, question_sha TEXT, question TEXT, route TEXT, status TEXT, answer_type TEXT,
  api_calls INTEGER, api_usd REAL, seconds REAL, error TEXT)"""


def record(path, question, result=None, error=None, seconds=0.0):
    db = sqlite3.connect(Path(path) / 'answers.sqlite3')
    try:
        db.execute(DDL)
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
        db.execute('INSERT INTO answers VALUES (?,?,?,?,?,?,?,?,?,?)',
                   (time.time(), hashlib.sha256(question.encode()).hexdigest(), question[:500], route,
                    a.get('status') or ('error' if error else None), a.get('answer_type'),
                    calls, cost, round(seconds, 3),
                    (error or '')[:300]))
        db.commit()
    finally:
        db.close()


def summary(path):
    target = Path(path) / 'answers.sqlite3'
    if not target.exists(): return {'questions': 0}
    db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    try:
        total, calls, usd = db.execute('SELECT count(*), coalesce(sum(api_calls),0), coalesce(sum(api_usd),0) FROM answers').fetchone()
        unknown = db.execute('SELECT count(*) FROM answers WHERE api_usd IS NULL OR api_calls IS NULL').fetchone()[0]
        import statistics
        by = {}
        for route, calls_, usd_, secs in db.execute('SELECT coalesce(route, "unknown"), api_calls, api_usd, seconds FROM answers'):
            r = by.setdefault(route, {'route': route, 'questions': 0, 'api_calls': 0, 'api_usd': 0.0, 'seconds': []})
            r['questions'] += 1; r['api_calls'] += calls_ or 0; r['api_usd'] += usd_ or 0.0; r['seconds'].append(secs or 0.0)
        routes = sorted(({**{k: v for k, v in r.items() if k != 'seconds'}, 'api_usd': round(r['api_usd'], 4),
                          'median_seconds': round(statistics.median(r['seconds']), 2)} for r in by.values()), key=lambda r: -r['questions'])
        zero = db.execute("SELECT count(*) FROM answers WHERE api_calls = 0 AND status = 'answered'").fetchone()[0]
    finally:
        db.close()
    return {'questions': total, 'answered_without_api_calls': zero, 'share_without_api_calls': round(zero / total, 4) if total else None,
            'api_calls': calls, 'api_usd': round(usd, 4), 'unknown_usage_questions': unknown,
            'cost_scope': 'Recorded known API estimates; historical undercount and local costs remain unresolved.',
            'usd_per_question': round(usd / total, 6) if total and not unknown else None, 'routes': routes}
