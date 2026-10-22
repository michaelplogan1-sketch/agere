"""Learned outcome reward model / pairwise candidate selector.

When several executed candidates compete (the cascade's executed-but-rejected proposals, the readings of an ambiguous metric) a
Bradley-Terry reward model orders them. It is trained on candidates Agere already produced and labelled by what happened to them:
  winner  the candidate's plan was verified (learning.sqlite3 `verified`: executed, checked, accepted and never corrected)
  loser   the candidate's plan was quarantined (`quarantined`: it tripped a high-precision control) or corrected
Candidate features are what is known at selection time: producing route, agreement/verification basis tokens, plan shape (joins,
predicates, length, date filters) and the number of open check reasons. Training pairs a winner with a loser; the model scores
w.x and P(a beats b) = sigmoid(score(a) - score(b)). Held-out pairwise accuracy (split by question) against a route-only baseline is
stored with the model. order() is used by proposers.Cascade.system_one_fallback: candidates are tried best-reward first and each
candidate receipt carries its reward and P(beats next). The reward model orders; it never removes a candidate, and System One still
decides (heal-not-block, cheapest first). AGERE_CANDIDATE_SELECTOR=off keeps input order.
Train: tools/learned_routing/train_candidate_selector.py. Model: candidate_selector_model.json.
"""
import hashlib
import json
import math
import os
import random
import re
import sqlite3
import time
from pathlib import Path

from . import cascade_router as CR

VERSION = 'candidate-selector/1'
MODEL_FILE = Path(__file__).with_name('candidate_selector_model.json')


def features(c):
    f = {}
    route = c.get('route') or c.get('name')
    if route: f[f'route:{route}'] = 1.0
    for tok in re.split(r'[=,;:\s]+', str(c.get('agreement') or '')):
        if tok: f[f'agree:{tok}'] = 1.0
    plan = str(c.get('plan') or '')
    low = plan.lower()
    if plan:
        f[f"joins:{min(low.count(' join '), 5)}"] = 1.0
        f[f"preds:{min(low.count(' and ') + low.count(' where '), 8)}"] = 1.0
        f[f"len:{min(len(plan) // 400, 6)}"] = 1.0
        for k, rx in (('date_filter', r'\b(?:date|time)\b.*(?:>=|<=|between)'), ('group_by', r'group by'), ('distinct', r'distinct'),
                      ('subquery', r'\(\s*select'), ('cte', r'\bwith\b')):
            if re.search(rx, low): f[f'sql:{k}'] = 1.0
    n = len(c.get('reasons') or [])
    f[f'reasons:{min(n, 3)}'] = 1.0
    return f


def labels(stacks=None):
    rows = {}
    for path in (stacks if stacks is not None else CR.stack_paths()):
        target = Path(path) / 'learning.sqlite3'
        if not target.exists(): continue
        with sqlite3.connect(f'file:{target}?mode=ro', uri=True) as db:
            names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table, wins in (('verified', True), ('quarantined', False)):
                if table not in names: continue
                for q, route, plan, agreement in db.execute(f'SELECT question, route, plan, agreement FROM {table}'):
                    rows[(CR._norm(q), route, hashlib.md5(str(plan).encode()).hexdigest())] = {
                        'question': q, 'route': route, 'agreement': agreement, 'plan': plan, 'win': wins}
    return list(rows.values())


def _dot(w, f): return sum(w.get(k, 0.0) * v for k, v in f.items())


def _fit(winners, losers, pairs, l2=0.02, steps=150, lr=0.3):
    w = {}
    for _ in range(steps):
        g = {}
        for i, j in pairs:
            d = {k: winners[i].get(k, 0.0) - losers[j].get(k, 0.0) for k in set(winners[i]) | set(losers[j])}
            p = 1 / (1 + math.exp(-max(-30, min(30, _dot(w, d)))))
            for k, v in d.items(): g[k] = g.get(k, 0.0) + (p - 1.0) * v / len(pairs)
        for k in set(g) | set(w): w[k] = w.get(k, 0.0) - lr * (g.get(k, 0.0) + l2 * w.get(k, 0.0))
    return {k: round(v, 5) for k, v in w.items() if abs(v) > 1e-4}


def _fold(q): return int(hashlib.md5(CR._norm(q).encode()).hexdigest(), 16) % 5


def _pairs(win_idx, lose_idx, cap=6000, seed=0):
    allp = [(i, j) for i in win_idx for j in lose_idx]
    if len(allp) > cap: random.Random(seed).shuffle(allp); allp = allp[:cap]
    return allp


def train(rows):
    win = [r for r in rows if r['win']]; lose = [r for r in rows if not r['win']]
    model = {'version': VERSION, 'trained_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'winners': len(win), 'losers': len(lose)}
    if len(win) < 10 or len(lose) < 10: model['status'] = 'untrained'; return model
    W = [features(r) for r in win]; L = [features(r) for r in lose]
    # held-out pairwise accuracy: fit on 4 folds of questions, score every held-out winner-vs-loser pair; baseline = route prior
    hits = base_hits = total = 0
    for fold in range(5):
        tw = [i for i, r in enumerate(win) if _fold(r['question']) != fold]; tl = [i for i, r in enumerate(lose) if _fold(r['question']) != fold]
        hw = [i for i, r in enumerate(win) if _fold(r['question']) == fold]; hl = [i for i, r in enumerate(lose) if _fold(r['question']) == fold]
        if not (tw and tl and hw and hl): continue
        w = _fit(W, L, _pairs(tw, tl, 3000), steps=60)
        rate = {}
        for r in [win[i] for i in tw]: rate.setdefault(r['route'], [0, 0])[0] += 1
        for r in [lose[i] for i in tl]: rate.setdefault(r['route'], [0, 0])[1] += 1
        prior = lambda r: (rate.get(r['route'], [1, 1])[0] + 1) / (sum(rate.get(r['route'], [1, 1])) + 2)
        for i in hw:
            for j in hl:
                total += 1
                s = _dot(w, W[i]) - _dot(w, L[j]); hits += (s > 0) + 0.5 * (s == 0)
                b = prior(win[i]) - prior(lose[j]); base_hits += (b > 0) + 0.5 * (b == 0)
    w = _fit(W, L, _pairs(range(len(win)), range(len(lose))))
    model.update(status='fitted', weights=w, heldout_pairs=total,
                 heldout_pairwise_accuracy=round(hits / total, 4) if total else None,
                 route_prior_pairwise_accuracy=round(base_hits / total, 4) if total else None)
    return model


def save(model, path=None):
    Path(path or MODEL_FILE).write_text(json.dumps(model, separators=(',', ':'), sort_keys=True) + '\n'); _CACHE.clear()


_CACHE = {}


def load(path=None):
    path = Path(path or os.environ.get('AGERE_CANDIDATE_SELECTOR_MODEL') or MODEL_FILE)
    try: key = (str(path), path.stat().st_mtime_ns)
    except OSError: return None
    if key not in _CACHE:
        try: _CACHE.clear(); _CACHE[key] = json.loads(path.read_text())
        except (OSError, ValueError): return None
    return _CACHE[key]


def score(candidate, model=None):
    model = model or load()
    if not model or model.get('status') != 'fitted': return None
    return _dot(model['weights'], features(candidate))


def order(candidates, model=None):
    """candidates: list of dicts (route/name, agreement, plan, reasons). -> (ordered list, receipts); input order kept without a model.
    Each receipt: {'index', 'reward', 'p_beats_next'}; ties and unscored candidates keep their relative order (stable sort)."""
    if os.environ.get('AGERE_CANDIDATE_SELECTOR', 'on') == 'off': return list(candidates), []
    model = model or load()
    scored = [(score(c, model), i, c) for i, c in enumerate(candidates)]
    if not model or model.get('status') != 'fitted' or len(candidates) < 2: return list(candidates), []
    scored.sort(key=lambda t: (-t[0], t[1]))
    receipts = []
    for k, (s, i, _) in enumerate(scored):
        nxt = scored[k + 1][0] if k + 1 < len(scored) else None
        receipts.append({'index': i, 'reward': round(s, 4),
                         'p_beats_next': None if nxt is None else round(1 / (1 + math.exp(-max(-30, min(30, s - nxt)))), 4)})
    return [c for _, _, c in scored], receipts
