"""Shared helpers for the wave-2 revenue-cycle / payer / network engines registered in engine_suite.py.

Not an engine: read-only database opening, period handling, multiple-testing control, shrinkage, the standard answer
shape and a few route helpers, so each engine module stays a single readable method. Synthetic warehouse data only.
"""
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
import json
import math
import re
import sqlite3

import numpy as np

VERSION = 'revenue-common/1'


@contextmanager
def connect(path_or_stack):
    """Read-only connection to the semantic layer of a stack directory (run.json present) or a test directory holding
    semantic.sqlite3. Accepts a Stack (its .path) too."""
    from . import semantic_layer
    path = Path(getattr(path_or_stack, 'path', path_or_stack))
    target = semantic_layer.ensure(path) if (path / 'run.json').exists() else path / 'semantic.sqlite3'
    db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    try:
        yield db
    finally:
        db.close()


def tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}


def columns(db, table):
    return {r[1] for r in db.execute(f'PRAGMA table_info({table})')}


def iso(v, what='date'):
    s = str(v)[:10]
    date.fromisoformat(s)
    return s


def window(as_of, months=12, end=None):
    """(start, end) ISO dates: the `months` months ending at `end` (default the as-of date)."""
    e = date.fromisoformat(str(end or as_of)[:10])
    s = e - timedelta(days=round(months * 30.4375))
    return s.isoformat(), e.isoformat()


def payload(text):
    try:
        return json.loads(text) if text else {}
    except (TypeError, ValueError):
        return {}


def bh(pvalues):
    """Benjamini-Hochberg q-values (same order as input)."""
    p = np.asarray(list(pvalues), dtype=float)
    n = len(p)
    if not n: return []
    order = np.argsort(p)
    q = np.empty(n)
    prev = 1.0
    for rank, i in enumerate(order[::-1]):
        k = n - rank
        prev = min(prev, p[i] * n / k)
        q[i] = prev
    return [float(min(1.0, x)) for x in q]


def shrunk_rate(events, n, prior_events, prior_n):
    """Beta-binomial empirical-Bayes rate: (events + a) / (n + a + b) with the prior mean and strength prior_n."""
    return (events + prior_events) / (n + prior_n) if (n + prior_n) else 0.0


def logit(p, eps=1e-6):
    p = min(max(p, eps), 1 - eps)
    return math.log(p / (1 - p))


def auroc(y, s):
    """Mann-Whitney AUROC (ties half); None when one class is absent."""
    y = np.asarray(y, dtype=int); s = np.asarray(s, dtype=float)
    pos, neg = s[y == 1], s[y == 0]
    if not len(pos) or not len(neg): return None
    from scipy.stats import rankdata
    r = rankdata(np.concatenate([pos, neg]))
    return float((r[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def logistic_fit(X, y, l2=1.0, iters=50, weights=None):
    """L2-penalised logistic regression by Newton-Raphson (intercept column must be in X; it is not penalised)."""
    X = np.asarray(X, dtype=float); y = np.asarray(y, dtype=float)
    w = np.ones(len(y)) if weights is None else np.asarray(weights, dtype=float)
    beta = np.zeros(X.shape[1])
    pen = np.full(X.shape[1], float(l2)); pen[0] = 0.0
    for _ in range(iters):
        z = np.clip(X @ beta, -30, 30)
        mu = 1 / (1 + np.exp(-z))
        g = X.T @ (w * (y - mu)) - pen * beta
        H = (X * (w * mu * (1 - mu))[:, None]).T @ X + np.diag(pen)
        try:
            step = np.linalg.solve(H, g)
        except np.linalg.LinAlgError:
            step = np.linalg.lstsq(H, g, rcond=None)[0]
        beta += step
        if np.max(np.abs(step)) < 1e-8: break
    return beta


def sigmoid(z):
    return 1 / (1 + np.exp(-np.clip(z, -30, 30)))


def answer(report, plan, *, value, table, definitions=(), number=None, patient_ids=(), membership_role='evaluated',
           answer_type='table', overall_key=None, sources=None):
    """Standard engine answer fields for analysis_graph.build_answer_core: assumptions always in limitations."""
    partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements') or report.get('status') == 'partial'
    out = {'status': 'partial' if partial else 'answered', 'answer_type': answer_type, 'table': table,
           'patient_ids': list(patient_ids), 'value': value, 'method': report.get('method', ''),
           'limitations': list(report.get('assumptions') or []), 'definitions': list(definitions),
           'membership_role': membership_role}
    if overall_key: out['overall'] = {overall_key: {k: v for k, v in report.items() if k not in ('rows',)}}
    if number is not None: out['number'] = number
    if patient_ids: out['count'] = len(list(patient_ids))
    if sources: out['sources'] = sources
    return out


def low(question):
    return ' '.join(str(question).lower().split())


DEFINITIONAL = re.compile(r'^(?:what (?:is|are|does)|explain|define|how (?:is|are|does|do)\b.*\b(?:calculated|computed|work|defined))')
OURS = re.compile(r'\b(?:our|my|we|us|this year|last year|which|who|how many|list|show|find|\d{4})\b')


def knowledge_only(q):
    """'what is X' / 'explain X' without any reference to our data: stays a knowledge question."""
    return bool(DEFINITIONAL.match(q)) and not OURS.search(q)


def money(v):
    return ('-' if v < 0 else '') + f'${abs(v):,.0f}'
