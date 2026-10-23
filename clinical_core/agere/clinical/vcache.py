"""vCache-style per-entry learned similarity thresholds for the semantic cache (Schroeder et al., ICLR 2026).

The semantic cache reuses a verified query for a slot-identical paraphrase when the encoder similarity clears a cut.
One global cut (calibrated_thresholds 'semantic_cache.similarity') is either too strict for entries whose paraphrases are
always safe or too loose for entries with near-miss neighbours. vCache learns a cut PER CACHED ENTRY under a user-set
error bound delta:
  * every observation is (entry question, similarity of a later question, was reusing the entry's plan correct?);
  * per entry, P(correct | s) = sigmoid(gamma * (s - t)) is fitted by MAP with a Gaussian prior centred on the global fit
    (entries with little evidence shrink to the global curve);
  * the entry's cut is the smallest s whose one-sided lower confidence bound (Laplace posterior, z=1.645) on P(correct)
    is >= 1 - delta; it never goes below FLOOR, and an entry with no observations uses the global gate unchanged.
Observations come from (a) the labelled cache cases (tools/calibration/data/semantic_cache_cases.json: adversarial
pairs, minimal differences, paraphrases; key = the cached base question), (b) online: each reuse is logged
(stack/vcache.sqlite3); a user correction of a reused answer is an error observation for that entry, and a later
verified plan that agrees with the reused plan is a correct observation (learning.record_correction / record_verified).
The fitted cuts live in vcache_thresholds.json (bound to the encoder identity); AGERE_VCACHE=off falls back to the global
gate; AGERE_VCACHE_DELTA sets the error bound (default 0.02, the cache certificate's alpha).
"""
import json
import math
import os
import sqlite3
import time
from pathlib import Path
from .evaluation import active as _evaluating

import numpy as np

VERSION = 'vcache/1'
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
THRESHOLDS = HERE / 'vcache_thresholds.json'
CASES = ROOT / 'tools' / 'calibration' / 'data' / 'semantic_cache_cases.json'
FLOOR = 0.70          # never reuse below this similarity, whatever an entry learned
Z = 1.645             # one-sided 95% lower bound
PRIOR_SD = (0.05, 8.0)  # prior sd on (t, gamma) around the global fit
DDL = """CREATE TABLE IF NOT EXISTS reuse(question TEXT, entry TEXT, similarity REAL, plan_digest TEXT, created REAL);
CREATE TABLE IF NOT EXISTS observation(entry TEXT, similarity REAL, correct INTEGER, source TEXT, question TEXT, created REAL);"""


def enabled():
    return os.environ.get('AGERE_VCACHE', 'on') != 'off'


def delta():
    try:
        return min(max(float(os.environ.get('AGERE_VCACHE_DELTA', '0.02')), 1e-4), 0.5)
    except ValueError:
        return 0.02


def key(question):
    return ' '.join(str(question).strip().split()).casefold().rstrip('?.')


# ---- fitting --------------------------------------------------------------------------------------------------------
def _sig(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -40, 40)))


def fit_curve(s, y, prior=None, prior_sd=PRIOR_SD, iters=60):
    """MAP fit of P(correct|s) = sigmoid(g * (s - t)). -> (t, g, cov 2x2). prior = (t0, g0) or None (weak prior)."""
    s, y = np.asarray(s, float), np.asarray(y, float)
    t0, g0 = prior if prior else (0.85, 20.0)
    sd = np.asarray(prior_sd if prior else (0.5, 100.0), float)
    theta = np.array([t0, g0], float)
    for _ in range(iters):
        t, g = theta
        p = _sig(g * (s - t))
        w = p * (1 - p)
        dz_dt, dz_dg = -g * np.ones_like(s), (s - t)
        grad = np.array([np.sum((y - p) * dz_dt), np.sum((y - p) * dz_dg)]) - (theta - [t0, g0]) / sd ** 2
        H = -np.array([[np.sum(w * dz_dt * dz_dt), np.sum(w * dz_dt * dz_dg)],
                       [np.sum(w * dz_dg * dz_dt), np.sum(w * dz_dg * dz_dg)]]) - np.diag(1 / sd ** 2)
        H[0, 1] += np.sum(-(y - p))  # d/dg of dz/dt term
        H[1, 0] = H[0, 1]
        try:
            step = np.linalg.solve(H, grad)
        except np.linalg.LinAlgError:
            break
        step = np.clip(step, [-0.05, -10.0], [0.05, 10.0])  # damped Newton
        theta = theta - step
        theta[1] = max(theta[1], 0.5)  # similarity must not lower P(correct)
        if np.max(np.abs(step)) < 1e-8: break
    try:
        cov = np.linalg.inv(-H)
    except np.linalg.LinAlgError:
        cov = np.diag(sd ** 2)
    return float(theta[0]), float(theta[1]), cov


def cut_for(t, g, cov, bound):
    """Smallest similarity in [FLOOR, 1] whose lower-bound P(correct) >= 1 - bound, or None when no s qualifies."""
    target = math.log((1 - bound) / bound)
    for s in np.round(np.arange(FLOOR, 1.0001, 0.0025), 4):
        z = g * (s - t)
        grad = np.array([-g, s - t])
        sd = float(np.sqrt(max(grad @ cov @ grad, 0.0)))
        if z - Z * sd >= target: return float(s)
    return None


def fit(cases, *, bound=None, min_obs=3, encoder_identity=None, global_threshold=None):
    """cases: [{'key', 'score', 'correct'}] (score -1 = no slot-identical candidate: not an observation).
    -> {'version', 'delta', 'global': {...}, 'entries': {key: {...}}}."""
    bound = delta() if bound is None else bound
    obs = [c for c in cases if c['score'] is not None and c['score'] >= 0]
    out = {'version': VERSION, 'delta': bound, 'floor': FLOOR, 'encoder_identity': encoder_identity,
           'written_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'observations': len(obs), 'entries': {}}
    if len(obs) < 5:
        out['global'] = {'threshold': global_threshold, 'reason': 'too few labelled observations'}
        return out
    if len({c['correct'] for c in obs}) < 2:
        # Every labelled reuse was right (the slot gate already removed the adversarial pairs): no curve exists, so use
        # the rule of three -- n clean observations bound the error rate by 3/n at 95% -- and loosen only where that clears delta.
        n = len(obs)
        clean = bool(obs[0]['correct']) and 3.0 / n <= bound
        out['global'] = {'threshold': global_threshold, 'n': n, 'errors': 0 if clean else n, 'basis': 'rule_of_three',
                         'rule_of_three_error_bound': round(3.0 / n, 4)}
        if clean:
            by = {}
            for c in obs: by.setdefault(c['key'], []).append(c)
            for k, rows in by.items():
                if len(rows) < min_obs: continue
                lo = min(r['score'] for r in rows)
                out['entries'][k] = {'threshold': round(max(FLOOR, lo - 0.02), 4), 'n': len(rows), 'errors': 0,
                                     'basis': 'rule_of_three', 'min_right_similarity': lo,
                                     '_points': [[round(r['score'], 4), 1] for r in rows]}
        return out
    t, g, cov = fit_curve([c['score'] for c in obs], [c['correct'] for c in obs])
    gcut = cut_for(t, g, cov, bound)
    out['global'] = {'t': round(t, 4), 'gamma': round(g, 3), 'threshold': gcut if gcut is not None else global_threshold,
                     'n': len(obs), 'errors': sum(not c['correct'] for c in obs)}
    by = {}
    for c in obs: by.setdefault(c['key'], []).append(c)
    for k, rows in by.items():
        if len(rows) < min_obs: continue
        te, ge, ce = fit_curve([r['score'] for r in rows], [r['correct'] for r in rows], prior=(t, g))
        cut = cut_for(te, ge, ce, bound)
        out['entries'][k] = {'threshold': cut, 'n': len(rows), 'errors': sum(not r['correct'] for r in rows), 't': round(te, 4),
                             'gamma': round(ge, 3),
                             'max_wrong_similarity': max([r['score'] for r in rows if not r['correct']], default=None),
                             'min_right_similarity': min([r['score'] for r in rows if r['correct']], default=None),
                             '_points': [[round(r['score'], 4), int(bool(r['correct']))] for r in rows]}
    return out


def cases_from_file(path=CASES):
    doc = json.loads(Path(path).read_text())
    rows = [{'key': key(c['base']), 'score': c['score'], 'correct': bool(c['reuse']), 'variant': c['variant']} for c in doc['cases']]
    return rows, doc.get('encoder_identity')


# ---- the store and the runtime gate ---------------------------------------------------------------------------------
_LOADED = {'mtime': None, 'doc': None}


def table():
    if not THRESHOLDS.exists(): return None
    m = THRESHOLDS.stat().st_mtime
    if _LOADED['mtime'] != m:
        _LOADED.update(mtime=m, doc=json.loads(THRESHOLDS.read_text()))
    return _LOADED['doc']


def _db(path):
    db = sqlite3.connect(Path(path) / 'vcache.sqlite3', timeout=5)
    db.executescript(DDL)
    return db


def online_observations(path, entry):
    target = Path(path) / 'vcache.sqlite3'
    if not target.exists(): return []
    db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    try:
        return [{'key': entry, 'score': s, 'correct': bool(c)} for s, c in
                db.execute('SELECT similarity, correct FROM observation WHERE entry = ?', (entry,))]
    finally:
        db.close()


def entry_threshold(path, entry_question, default, encoder_identity=None):
    """The cut for one cached entry -> (threshold, receipt). Fitted entry cut (file) refined by online observations;
    entries without evidence keep `default` (the global gate)."""
    receipt = {'vcache': VERSION, 'delta': delta(), 'entry': key(entry_question)}
    if not enabled(): return default, {**receipt, 'source': 'disabled'}
    doc = table() or {}
    if encoder_identity and doc.get('encoder_identity') and doc['encoder_identity'] != encoder_identity:
        return default, {**receipt, 'source': 'global (fitted with another encoder)'}
    k = key(entry_question)
    row = (doc.get('entries') or {}).get(k)
    online = []
    try:
        online = online_observations(path, k) if path else []
    except sqlite3.Error:
        online = []
    if online:
        offline = [{'key': k, 'score': s, 'correct': c} for s, c in (row or {}).get('_points', [])]
        g = doc.get('global') or {}
        prior = (g['t'], g['gamma']) if 't' in g else None
        pts = offline + online
        if len(pts) >= 3 and prior:
            te, ge, ce = fit_curve([p['score'] for p in pts], [p['correct'] for p in pts], prior=prior)
            cut = cut_for(te, ge, ce, delta())
            if cut is not None:
                return cut, {**receipt, 'source': 'entry (offline + online)', 'n': len(pts), 'online': len(online)}
            return max(default, 0.99), {**receipt, 'source': 'entry evidence too unsafe', 'n': len(pts)}
    errs = [o['score'] for o in online if not o['correct']]
    if row and row.get('threshold') is not None and errs:  # no fitted curve: an online error pushes the cut above it
        return max(row['threshold'], round(max(errs) + 0.0025, 4)), {**receipt, 'source': 'entry (fitted, raised by online error)',
                                                                   'n': row['n'], 'online': len(online)}
    if not row and errs:
        return max(default, round(max(errs) + 0.0025, 4)), {**receipt, 'source': 'global raised by online error', 'online': len(online)}
    if row and row.get('threshold') is not None:
        return row['threshold'], {**receipt, 'source': 'entry (fitted)', 'n': row['n'], 'errors': row['errors']}
    if row:  # observed, but no similarity meets the bound for this entry
        return max(default, 0.99), {**receipt, 'source': 'entry evidence too unsafe', 'n': row['n'], 'errors': row['errors']}
    return default, {**receipt, 'source': 'global (no entry evidence)'}


def log_reuse(path, question, entry_question, similarity, plan):
    if _evaluating(): return
    try:
        db = _db(path)
        try:
            db.execute('INSERT INTO reuse VALUES (?,?,?,?,?)', (key(question), key(entry_question), float(similarity),
                                                              _digest(plan), time.time()))
            db.commit()
        finally:
            db.close()
    except (sqlite3.Error, OSError):
        pass  # the cache answer stands; only the learning signal is lost


def observe(path, entry_question, similarity, correct, source, question=None):
    if _evaluating(): return
    db = _db(path)
    try:
        db.execute('INSERT INTO observation VALUES (?,?,?,?,?,?)', (key(entry_question), float(similarity), int(bool(correct)),
                                                                   source, key(question or ''), time.time()))
        db.commit()
    finally:
        db.close()


def _digest(plan):
    import hashlib
    q = plan.get('query') if isinstance(plan, dict) else plan
    return hashlib.sha256(' '.join(str(q).split()).encode()).hexdigest()[:16]


def _reuses(path, question):
    target = Path(path) / 'vcache.sqlite3'
    if not target.exists(): return []
    db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    try:
        return db.execute('SELECT entry, similarity, plan_digest FROM reuse WHERE question = ? ORDER BY created DESC',
                          (key(question),)).fetchall()
    finally:
        db.close()


def on_correction(path, question):
    """A user corrected a question the cache answered by reuse -> one error observation per reused entry."""
    n = 0
    for entry, sim, _ in _reuses(path, question):
        observe(path, entry, sim, False, 'user_correction', question); n += 1
    return n


def on_verified(path, question, plan):
    """A question the cache answered was later verified independently: same query -> correct, different -> error."""
    n = 0
    for entry, sim, digest in _reuses(path, question):
        observe(path, entry, sim, digest == _digest(plan), 'later_verification', question); n += 1
    return n


def write(doc, path=THRESHOLDS):
    Path(path).write_text(json.dumps(doc, indent=1, sort_keys=True))
    _LOADED['mtime'] = None
    return path
