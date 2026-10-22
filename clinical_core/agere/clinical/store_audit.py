"""Verified-store error audit with prediction-powered inference (FRONTIER #5; Angelopoulos, Bates, Fannjiang, Jordan &
Zrnic 2023, "Prediction-powered inference").

The verified store (<stack>/learning.sqlite3 verified) is what learned_verified_sql, the semantic cache and bottled SQL
replay for free, so its error rate is the error rate of every free replay. audit() estimates it:
  1. every saved pair is re-executed read-only on the current semantic layer and System One judges, in BOTH families
     (system_one_cross_check), whether the result answers the saved question: prediction f = 1 (wrong) when both families
     say it answers a different question, 0 when both say it answers it, 0.5 when they split / cannot tell
  2. gold labels y for the subset that has them: saved non-blind bench rows of the same question (adjudicated, else
     scored) and user corrections of the question (wrong)
  3. PPI mean: theta = mean_N(f) + mean_n(y - f), with a normal (1 - alpha) interval from var(f)/N + var(y - f)/n; with no
     gold labels the prediction-only mean is reported and labelled as such
The audit is written to <stack>/store-audit.json (learning.summary shows it) and its per-pair labels (gold where present,
else a two-family-agreed System One label) to <stack>/store-audit-cases.json, which tools/certify_gates.py reads as
labelled cases for the 'learned_reuse.answer' gate. Synthetic data only; paid calls only with allow_live (~$0.0003/pair).
"""
import json
import math
import re
import sqlite3
import time
from pathlib import Path

VERSION = 'store-audit-ppi/1'


def ppi_mean(predictions, gold_pairs, alpha=0.1):
    """predictions: f over the whole store (N); gold_pairs: [(y, f)] on the labelled subset (n).
    -> {'estimate', 'low', 'high', 'N', 'n', 'prediction_only', 'rectifier'}"""
    N, n = len(predictions), len(gold_pairs)
    if not N: return {'estimate': None, 'N': 0, 'n': n}
    mf = sum(predictions) / N
    z = {0.1: 1.6449, 0.05: 1.96, 0.2: 1.2816}.get(alpha, 1.6449)
    var_f = sum((x - mf) ** 2 for x in predictions) / max(1, N - 1)
    if not n:
        se = math.sqrt(var_f / N)
        return {'estimate': round(mf, 4), 'low': round(max(0.0, mf - z * se), 4), 'high': round(min(1.0, mf + z * se), 4),
                'N': N, 'n': 0, 'prediction_only': True, 'rectifier': None}
    d = [y - f for y, f in gold_pairs]
    md = sum(d) / n
    var_d = sum((x - md) ** 2 for x in d) / max(1, n - 1)
    est = mf + md
    se = math.sqrt(var_f / N + var_d / n)
    return {'estimate': round(min(1.0, max(0.0, est)), 4), 'low': round(max(0.0, est - z * se), 4), 'high': round(min(1.0, est + z * se), 4),
            'N': N, 'n': n, 'prediction_only': False, 'rectifier': round(md, 4), 'alpha': alpha}


def _norm(q): return ' '.join(re.sub(r'[^a-z0-9%<>=.]+', ' ', str(q).lower()).split())


def pairs(path):
    target = Path(path) / 'learning.sqlite3'
    if not target.exists(): return []
    with sqlite3.connect(f'file:{target}?mode=ro', uri=True) as db:
        return [{'question': q, 'plan': json.loads(p), 'route': r, 'created': c}
                for q, p, r, c in db.execute("SELECT question, plan, route, created FROM verified WHERE plan_kind = 'sql'")]


def gold(path, questions, results=None):
    """{normalized question: 1 wrong / 0 right} from saved non-blind bench rows and user corrections."""
    out = {}
    try:
        import sys
        root = Path(__file__).resolve().parents[3]
        sys.path.insert(0, str(root))
        from tools import certify_gates as CG
        pool, _, blind = CG.bench_rows(CG.results_dir(results))
        want = {_norm(q) for q in questions}
        for r in pool:
            k = _norm(r['question'])
            if k in want and k not in blind and CG.asserted(r.get('answer')): out[k] = 1 if r['wrong'] else 0
    except Exception:  # noqa: BLE001 - no saved runs
        pass
    target = Path(path) / 'learning.sqlite3'
    if target.exists():
        with sqlite3.connect(f'file:{target}?mode=ro', uri=True) as db:
            for (q, note) in db.execute('SELECT question, note FROM corrections'):
                if not str(note).startswith('quarantine:'): out[_norm(q)] = 1
    return out


def _execute(path, plan):
    target = Path(path) / 'semantic.sqlite3'
    db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    try:
        deadline = time.perf_counter() + 10
        db.set_progress_handler(lambda: 1 if time.perf_counter() > deadline else 0, 20000)
        cur = db.execute(plan['query'])
        cols = [c[0] for c in cur.description or []]
        rows = [dict(zip(cols, r)) for r in cur.fetchmany(50)]
    finally:
        db.close()
    if len(rows) == 1 and len(cols) == 1:
        v = rows[0][cols[0]]
        return {'answer': {'status': 'answered', 'answer_type': 'count' if isinstance(v, int) else 'value', 'count' if isinstance(v, int) else 'value': v}}
    return {'answer': {'status': 'answered', 'answer_type': 'table', 'table': rows}}


def audit(stack, *, ledger=None, limit=None, results=None, write=True, s1=None):
    """-> {'version', 'pairs', 'audited', 'ppi': ppi_mean(...), 'flagged': [...], 'cost_usd', 'calls'}"""
    from .system_one_cross_check import cross_check
    path = Path(stack.path)
    ps = pairs(path)[:limit] if limit else pairs(path)
    labels = gold(path, [p['question'] for p in ps], results)
    preds, gold_pairs, cases, flagged, usd, calls = [], [], [], [], 0.0, 0
    for p in ps:
        try:
            result = _execute(path, p['plan'])
        except Exception as exc:  # noqa: BLE001 - a pair that no longer runs is wrong for reuse
            f, verdict = 1.0, {'verdict': 'execution_failed', 'error': f'{type(exc).__name__}: {exc}'[:160]}
        else:
            graph = {'nodes': [{'operation': 'sql', 'parameters': {'query': p['plan'].get('query'), 'returns': p['plan'].get('returns')}}]}
            verdict = cross_check(stack, p['question'], graph, result, ledger=ledger, s1=s1) if getattr(stack, 'allow_live', False) or s1 \
                else {'verdict': 'unavailable', 'calls': 0, 'cost_usd': 0.0}
            usd += verdict.get('cost_usd') or 0.0; calls += verdict.get('calls') or 0
            f = {'confirmed': 0.0, 'refuted': 1.0}.get(verdict['verdict'], 0.5)
        preds.append(f)
        y = labels.get(_norm(p['question']))
        if y is not None: gold_pairs.append((y, f))
        label = y if y is not None else (1 if f == 1.0 else 0 if f == 0.0 else None)
        if label is not None:
            cases.append({'key': _norm(p['question']), 'score': 1.0, 'error': bool(label), 'label_source': 'gold' if y is not None else 'system_one_two_family',
                          'route': p['route']})
        if f >= 1.0 or y == 1: flagged.append({'question': p['question'], 'route': p['route'], 'verdict': verdict.get('verdict'), 'gold': y})
    out = {'version': VERSION, 'pairs': len(pairs(path)), 'audited': len(ps), 'ppi': ppi_mean(preds, gold_pairs), 'flagged': flagged[:50],
           'cost_usd': round(usd, 6), 'calls': calls, 'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    if write:
        (path / 'store-audit.json').write_text(json.dumps(out, indent=1, default=str) + '\n')
        (path / 'store-audit-cases.json').write_text(json.dumps({'version': VERSION, 'gate': 'learned_reuse.answer', 'cases': cases}, indent=1) + '\n')
    return out


def last(path):
    try:
        return json.loads((Path(path) / 'store-audit.json').read_text())
    except (OSError, ValueError):
        return None
