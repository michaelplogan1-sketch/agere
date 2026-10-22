"""Learned answer verifier: P(this delivered answer is wrong), trained on verified vs quarantined/corrected answers.

Labels (free, already logged by the learning loop in every stack's learning.sqlite3):
  right  rows in `verified` (an answered, check-clean answer) and `outcomes` answered rows never corrected or quarantined
  wrong  rows in `quarantined` (an answer that tripped a high-precision control) and `corrections` (an explicit user correction)
Features known BEFORE the controls run: hashed question words/bigrams and cues, the route that produced the answer and its agreement
signal. (The controls themselves are not features: predicting a quarantine from the control that caused it would be circular.)
One L2 logistic model, held-out accuracy by question hash against the base rate is stored with the model; `risky()` routes an answer
to extra checks (answer_checks adds a System One cross-check pass and a visible note) when p_wrong crosses the threshold stored with
the model. Heal-not-block: the verifier annotates and escalates checking; it never withholds an answer.
AGERE_ANSWER_VERIFIER=off disables. Train: tools/learned_routing/train_answer_verifier.py. Model: answer_verifier_model.json.
"""
import hashlib
import json
import math
import os
import sqlite3
import time
from pathlib import Path

from . import cascade_router as CR

VERSION = 'answer-verifier/1'
MODEL_FILE = Path(__file__).with_name('answer_verifier_model.json')
ROOT = Path(__file__).resolve().parents[3]
MIN_WRONG = 8


def _h(token): return CR._h(token)


def features(question, route=None, agreement=None):
    f = dict(CR.features(question))
    for t in (f'route:{route}' if route else None, f'agree:{agreement}' if agreement else None):
        if t: f[_h(t)] = f.get(_h(t), 0.0) + 1.0
    return f


def labels(stacks=None):
    """-> [{'question', 'route', 'agreement', 'wrong': bool, 'source'}] one per (question, route); wrong wins."""
    rows = {}
    for path in (stacks if stacks is not None else CR.stack_paths()):
        target = Path(path) / 'learning.sqlite3'
        if not target.exists(): continue
        with sqlite3.connect(f'file:{target}?mode=ro', uri=True) as db:
            names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}

            def put(q, route, agreement, wrong, source):
                k = (CR._norm(q), route)
                if k not in rows or (wrong and not rows[k]['wrong']):
                    rows[k] = {'question': q, 'route': route, 'agreement': agreement, 'wrong': wrong, 'source': source}
            if 'verified' in names:
                for q, route, agreement in db.execute('SELECT question, route, agreement FROM verified'): put(q, route, agreement, False, 'verified')
            if 'outcomes' in names:
                for q, route in db.execute("SELECT question, route FROM outcomes WHERE status='answered'"): put(q, route, None, False, 'outcomes')
            if 'quarantined' in names:
                for q, route, agreement in db.execute('SELECT question, route, agreement FROM quarantined'): put(q, route, agreement, True, 'quarantined')
            if 'corrections' in names:
                for (q,) in db.execute('SELECT question FROM corrections'): put(q, None, None, True, 'corrections')
    return list(rows.values())


def _sigmoid(z): return 1 / (1 + math.exp(-max(-30, min(30, z))))


def _fit(rows, l2=0.05, steps=250, lr=0.5):
    X = [features(r['question'], r['route'], r['agreement']) for r in rows]
    y = [1.0 if r['wrong'] else 0.0 for r in rows]
    n = len(rows); base = sum(y) / n
    b = math.log(max(base, 1e-3) / max(1 - base, 1e-3)); w = {}
    for _ in range(steps):
        gw, gb = {}, 0.0
        for f, t in zip(X, y):
            d = (_sigmoid(b + sum(w.get(k, 0.0) * v for k, v in f.items())) - t) / n
            gb += d
            for k, v in f.items(): gw[k] = gw.get(k, 0.0) + d * v
        for k in set(gw) | set(w): w[k] = w.get(k, 0.0) - lr * (gw.get(k, 0.0) + l2 * w.get(k, 0.0))
        b -= lr * gb
    return {k: round(v, 5) for k, v in w.items() if abs(v) > 1e-4}, round(b, 5)


def _predict(w, b, f): return _sigmoid(b + sum(w.get(k, 0.0) * v for k, v in f.items()))


def _fold(q): return int(hashlib.md5(CR._norm(q).encode()).hexdigest(), 16) % 5


def _auc(pairs):
    pos = [p for p, y in pairs if y]; neg = [p for p, y in pairs if not y]
    if not pos or not neg: return None
    return sum((a > b) + 0.5 * (a == b) for a in pos for b in neg) / (len(pos) * len(neg))


def train(rows):
    wrong = sum(r['wrong'] for r in rows)
    model = {'version': VERSION, 'trained_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'n': len(rows), 'wrong': wrong,
             'base_rate': round(wrong / max(len(rows), 1), 4), 'threshold': 0.5}
    if len(rows) < 40 or wrong < MIN_WRONG or len(rows) - wrong < MIN_WRONG:
        model['status'] = 'base_rate_only'; return model
    held = []
    for fold in range(5):
        test = [r for r in rows if _fold(r['question']) == fold]; trn = [r for r in rows if _fold(r['question']) != fold]
        if not test or len({r['wrong'] for r in trn}) < 2: continue
        w, b = _fit(trn, steps=120)
        held += [(_predict(w, b, features(r['question'], r['route'], r['agreement'])), r['wrong']) for r in test]
    auc = _auc(held)
    # lowest threshold whose held-out precision is >= min(0.6, 3 x base rate): only answers with several times the usual risk
    # escalate checking (the rest keep the standard checks)
    thr, goal = 0.5, min(0.6, 3 * wrong / len(rows))
    for t in [x / 50 for x in range(2, 46)]:
        flagged = [y for p, y in held if p >= t]
        if len(flagged) >= 10 and sum(flagged) / len(flagged) >= goal: thr = t; break
    acc = sum((p >= 0.5) == y for p, y in held) / max(len(held), 1)
    base_acc = max(wrong, len(rows) - wrong) / len(rows)
    w, b = _fit(rows)
    model.update(status='fitted', weights=w, bias=b, threshold=thr, heldout_auc=None if auc is None else round(auc, 4),
                 heldout_accuracy=round(acc, 4), base_accuracy=round(base_acc, 4), heldout_n=len(held),
                 flagged_at_threshold=sum(p >= thr for p, _ in held), precision_at_threshold=round(sum(y for p, y in held if p >= thr) / max(sum(p >= thr for p, _ in held), 1), 4))
    return model


def save(model, path=None):
    Path(path or MODEL_FILE).write_text(json.dumps(model, separators=(',', ':'), sort_keys=True) + '\n'); _CACHE.clear()


_CACHE = {}


def load(path=None):
    path = Path(path or os.environ.get('AGERE_ANSWER_VERIFIER_MODEL') or MODEL_FILE)
    try: key = (str(path), path.stat().st_mtime_ns)
    except OSError: return None
    if key not in _CACHE:
        try: _CACHE.clear(); _CACHE[key] = json.loads(path.read_text())
        except (OSError, ValueError): return None
    return _CACHE[key]


def p_wrong(question, route=None, agreement=None, model=None):
    """-> {'p_wrong', 'basis': 'model'|'base_rate', 'threshold', 'risky'} or None without a model."""
    if os.environ.get('AGERE_ANSWER_VERIFIER', 'on') == 'off': return None
    model = model or load()
    if not model: return None
    if model.get('status') == 'fitted':
        p = _predict(model['weights'], model['bias'], features(question, route, agreement)); basis = 'model'
    else:
        p = model['base_rate']; basis = 'base_rate'
    return {'p_wrong': round(p, 4), 'basis': basis, 'threshold': model['threshold'], 'risky': basis == 'model' and p >= model['threshold'],
            'heldout_auc': model.get('heldout_auc'), 'version': VERSION}


def annotate(question, receipt, answer, verdict):
    """answer_checks hook: attach p_wrong to the verdict; a risky answer is marked for an extra System One cross-check and noted."""
    route = (receipt or {}).get('route') if isinstance(receipt, dict) else None
    agreement = ((receipt or {}).get('agreement') if isinstance(receipt, dict) else None)
    out = p_wrong(question, route, agreement if isinstance(agreement, str) else None)
    if out is None: return None
    verdict['answer_verifier'] = out
    if out['risky']:
        verdict['annotations'].append(f"Learned verifier: p(wrong) = {out['p_wrong']:.2f} (threshold {out['threshold']:.2f}); "
                                      'extra checking applied, answer marked lower confidence.')
        if (verdict.get('certificate') or {}).get('status') != 'certified': verdict.setdefault('confidence', 'low')
    return out
