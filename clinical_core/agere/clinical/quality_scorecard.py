"""Six Sigma defect-rate tracking from the production answer log + the public error-rate scorecard (JSON for the product).

Production process (unit = one question asked; opportunity = that question): answers.sqlite3 (answer_log.record, written
for every question on the conversation path) and learning.sqlite3 corrections (a user said the answer was wrong). Each
question falls in exactly one class:
  hard_failure     status error / internal_error (the stack failed)
  not_answered     not_understood / declined / clarify (Agere must answer every question: a refusal is a defect)
  reported_wrong   answered, later corrected by a user (corrections table, same question text)
  partial          answered with a named missing piece (qualified, not counted as a defect; shown)
  data_absent      unsupported / not_available: an honest "the data is not delivered" (shown; not a defect)
  ok               answered
Defects = hard_failure + not_answered + reported_wrong. DPMO, sigma level (1.5 sigma shift), Wilson 95% interval and a
daily p-chart (monitor.control_chart, Laney-adjusted) come from tools/quality/defects.py, which also supplies the
rolling blind-test numbers (bench/results). The scorecard adds the clinical workflow task agreement
(bench/clinical_task_reference.py output) and the sealed truth gate's aggregates (tools/quality/sealed_gate.py), never
rows. Served at /api/scorecard (and /api/stack/quality) by service.py. Read-only; no model calls.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

VERSION = 'agere-quality-scorecard/1'
ROOT = Path(__file__).resolve().parents[3]
RESULTS = ROOT / 'bench' / 'results'
TASKS_FILE = RESULTS / 'clinical_tasks' / 'latest.json'
GATE_DIR = RESULTS / 'sealed_gate'
CLASSES = ('hard_failure', 'not_answered', 'reported_wrong', 'partial', 'data_absent', 'ok')
DEFECTS = ('hard_failure', 'not_answered', 'reported_wrong')
_HARD, _NOT, _ABSENT = {'error', 'internal_error'}, {'not_understood', 'declined', 'clarify', 'ambiguous'}, {'unsupported', 'not_available'}


def _defects():
    spec = importlib.util.spec_from_file_location('agere_quality_defects', ROOT / 'tools' / 'quality' / 'defects.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def classify(status, corrected=False):
    s = (status or '').lower()
    if s in _HARD or (not s): return 'hard_failure'
    if s in _NOT: return 'not_answered'
    if s in _ABSENT: return 'data_absent'
    if corrected: return 'reported_wrong'
    return 'partial' if s == 'partial' else 'ok'


def _ro(path):
    return sqlite3.connect(f'file:{path}?mode=ro', uri=True) if Path(path).exists() else None


def load(path):
    """[{at, day, question, route, status, cls, api_calls, api_usd}] from a stack directory's answer log + corrections."""
    path = Path(path)
    corrected = {}
    db = _ro(path / 'learning.sqlite3')
    if db is not None:
        try: corrected = {q: c for q, c in db.execute('SELECT question, max(created) FROM corrections GROUP BY question')}
        except sqlite3.Error: corrected = {}
        finally: db.close()
    db = _ro(path / 'answers.sqlite3')
    if db is None: return []
    try: raw = db.execute('SELECT at, question, route, status, api_calls, api_usd FROM answers ORDER BY at').fetchall()
    except sqlite3.Error: raw = []
    finally: db.close()
    out = []
    for at, q, route, status, calls, usd in raw:
        c = corrected.get(q)
        out.append({'at': at, 'day': dt.datetime.fromtimestamp(at or 0, dt.timezone.utc).date().isoformat(), 'question': q,
                    'route': route or 'unknown', 'status': status,
                    'cls': classify(status, corrected=c is not None and (at or 0) <= c), 'api_calls': calls, 'api_usd': usd})
    return out


def rates(rows, d=None):
    d = d or _defects()
    n = len(rows); k = sum(r['cls'] in DEFECTS for r in rows)
    hard = sum(r['cls'] == 'hard_failure' for r in rows)
    v = d.dpmo(k, n)
    return {'opportunities': n, 'defects': k, 'defect_rate': k / n if n else None, 'dpmo': v, 'sigma': d.sigma_level(v),
            'ci': d.wilson(k, n), 'hard_failure_rate': hard / n if n else None,
            'classes': {c: sum(r['cls'] == c for r in rows) for c in CLASSES}}


def production(path, *, now=None, windows=(7, 30)):
    """Defect rates for every logged question, rolling windows, a daily p-chart and the per-route breakdown."""
    d = _defects()
    rows = load(path)
    if not rows: return {'empty': 'No answers have been logged for this stack yet.', 'opportunities': 0}
    now = now or max(r['at'] for r in rows)
    out = {'overall': rates(rows, d), 'windows': {}, 'first_question_at': min(r['at'] for r in rows), 'last_question_at': now}
    for w in windows:
        out['windows'][f'last_{w}_days'] = rates([r for r in rows if r['at'] >= now - w * 86400], d)
    days = defaultdict(list)
    for r in rows: days[r['day']].append(r)
    points = [{'label': day, 'defects': sum(r['cls'] in DEFECTS for r in rs), 'opportunities': len(rs)} for day, rs in sorted(days.items())]
    out['p_chart'] = d.p_chart(points, laney=True)
    by = defaultdict(list)
    for r in rows: by[r['route']].append(r)
    out['routes'] = sorted(({'route': k, **{x: y for x, y in rates(v, d).items() if x != 'ci'}} for k, v in by.items()),
                           key=lambda r: -r['opportunities'])
    out['pareto'] = [{'class': c, 'count': n} for c, n in Counter(r['cls'] for r in rows if r['cls'] in DEFECTS).most_common()]
    return out


def blind_tests(results=RESULTS):
    """Rolling blind-test error rates (bench/results via tools/quality/defects.py), summarized."""
    results = Path(results)
    if not results.exists() or not any(results.glob('*/*.jsonl')): return {'empty': 'No scored benchmark runs were found.'}
    d = _defects()
    r = d.report(results, d.load_fmea())
    pick = lambda m: {k: m.get(k) for k in ('file', 'started', 'questions', 'answerable', 'strict_defects', 'strict_rate', 'strict_ci',
                                            'strict_dpmo', 'strict_sigma')} if m else None
    latest = [{'set': x.get('set'), 'runs': x.get('runs'), 'first_attempt': pick(x.get('first')), 'latest': pick(x.get('latest'))}
              for x in r.get('blind_sets') or []]
    return {'totals': r.get('totals'), 'blind_sets': latest}


def _latest_json(path):
    path = Path(path)
    files = sorted(path.glob('*.json')) if path.is_dir() else ([path] if path.exists() else [])
    for f in reversed(files):
        try: return {**json.loads(f.read_text()), 'file': f.name}
        except (OSError, ValueError): continue
    return None


def scorecard(path=None, *, results=RESULTS, tasks=TASKS_FILE, gate=GATE_DIR):
    """The public scorecard payload. Every section is computed from saved evidence or says why it is empty."""
    gate_doc = _latest_json(gate)
    if gate_doc:  # aggregates only, by construction of sealed_gate; strip anything row-shaped defensively
        gate_doc = {k: v for k, v in gate_doc.items() if k not in ('rows', 'items', 'answers', 'predictions')}
    return {'schema': VERSION, 'generated': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), 'synthetic': True,
            'production': production(path) if path else {'empty': 'No stack is connected.'},
            'blind_tests': blind_tests(results), 'clinical_tasks': _latest_json(tasks) or {'empty': 'No clinical task run saved.'},
            'sealed_truth_gate': gate_doc or {'empty': 'The sealed truth gate has not been run for a release yet.'},
            'definitions': {'defect': 'hard failure, not answered (not understood / declined / clarification) or reported wrong',
                            'opportunity': 'one question asked', 'sigma': 'z(1 - DPMO/1e6) + 1.5',
                            'not_defects': 'partial (missing piece named) and data_absent (input not delivered) are shown, not counted'}}
