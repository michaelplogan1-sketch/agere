"""Per-entry semantic-cache cutoff proposals, with explicit policy and evidence identity.

MAP/Laplace and rule-of-three proposals are heuristic. They do not certify the
post-selection reuse error, clinical correctness, or the randomized vCache paper's
all-request guarantee. Unqualified reuse retains the ordinary answer review path.
"""
import hashlib
import json
import math
import os
import sqlite3
import time
from pathlib import Path
from .evaluation import active as _evaluating

import numpy as np

VERSION = 'vcache/2'
POLICY_VERSION = 'semantic-cache-headroom/2'
METHOD = 'map-laplace-or-entry-rule-of-three-proposal/2'
LOSS = 'independently-verified-reference-plan-disagreement'
DENOMINATOR = 'admitted-cache-reuse-requests'
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
    value = float(os.environ.get('AGERE_VCACHE_DELTA', '0.02'))
    if not math.isfinite(value) or not 0 < value < 1:
        raise ValueError('AGERE_VCACHE_DELTA must be a finite error target between zero and one')
    return value


def policy(encoder_identity=None, bound=None):
    bound = delta() if bound is None else float(bound)
    if not math.isfinite(bound) or not 0 < bound < 1:
        raise ValueError('invalid cache error target')
    return {'version': POLICY_VERSION, 'error_target': bound, 'scorer': encoder_identity,
            'loss': LOSS, 'denominator': DENOMINATOR, 'method': METHOD}


def _binding(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')) if value else None


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
    binding = policy(encoder_identity, bound)
    bound = binding['error_target']
    cases = list(cases)
    data_identity = hashlib.sha256(json.dumps(cases, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    obs = [c for c in cases if c['score'] is not None and c['score'] >= 0]
    out = {'version': VERSION, 'policy': binding, 'data_identity': data_identity,
           'qualification': {'qualified': False, 'method': METHOD, 'reason': 'proposal; post-selection reuse risk is not certified'}, 'delta': bound, 'floor': FLOOR, 'encoder_identity': encoder_identity,
           'written_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'observations': len(obs), 'entries': {}}
    if len(obs) < 5:
        out['global'] = {'threshold': global_threshold, 'reason': 'too few labelled observations'}
        by = {}
        for c in obs: by.setdefault(c['key'], []).append(c)
        out['entries'] = {k: {'threshold': None, 'n': len(rows), 'errors': sum(not r['correct'] for r in rows),
                              'reason': 'entry evidence insufficient for a cutoff'}
                          for k, rows in by.items() if len(rows) >= min_obs}
        return out
    if len({c['correct'] for c in obs}) < 2:
        # Every labelled reuse was right (the slot gate already removed the adversarial pairs): no curve exists, so use
        # the rule of three -- n clean observations bound the error rate by 3/n at 95% -- and loosen only where that clears delta.
        n = len(obs)
        clean = bool(obs[0]['correct']) and 3.0 / n <= bound
        out['global'] = {'threshold': global_threshold, 'n': n, 'errors': sum(not c['correct'] for c in obs), 'basis': 'rule_of_three',
                         'rule_of_three_error_bound': round(3.0 / n, 4)}
        by = {}
        for c in obs: by.setdefault(c['key'], []).append(c)
        for k, rows in by.items():
            if len(rows) < min_obs: continue
            lo = min(r['score'] for r in rows)
            out['entries'][k] = {'threshold': round(max(FLOOR, lo - 0.02), 4) if clean and 3.0 / len(rows) <= bound else None,
                                 'n': len(rows), 'errors': sum(not r['correct'] for r in rows),
                                 'basis': 'rule_of_three', 'min_right_similarity': lo if clean else None,
                                 '_points': [[round(r['score'], 4), int(bool(r['correct']))] for r in rows]}
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
_LOADED = {}


def table():
    path = Path(os.environ.get('AGERE_VCACHE_THRESHOLDS') or THRESHOLDS)
    try:
        stamp = (str(path.resolve()), path.stat().st_mtime_ns)
        if stamp not in _LOADED:
            doc = json.loads(path.read_text())
            _LOADED.clear()
            _LOADED[stamp] = doc if isinstance(doc, dict) else {}
        return _LOADED[stamp]
    except (OSError, ValueError):
        return None


def _db(path):
    db = sqlite3.connect(Path(path) / 'vcache.sqlite3', timeout=5)
    db.executescript(DDL)
    for name in ('reuse', 'observation'):
        if 'binding' not in {r[1] for r in db.execute('PRAGMA table_info(' + name + ')')}:
            db.execute('ALTER TABLE ' + name + ' ADD COLUMN binding TEXT')
    return db


def online_observations(path, entry, binding=None):
    target = Path(path) / 'vcache.sqlite3'
    if not target.exists(): return []
    db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    try:
        columns = {r[1] for r in db.execute('PRAGMA table_info(observation)')}
        field = 'binding' if 'binding' in columns else 'NULL'
        rows = db.execute('SELECT similarity, correct, ' + field + ' FROM observation WHERE entry = ?', (entry,))
        # Legacy positives cannot loosen a new policy. Legacy negative feedback
        # may conservatively raise a cutoff, and never confers qualification.
        return [{'key': entry, 'score': s, 'correct': bool(c)} for s, c, b in rows
                if binding is None or b == _binding(binding) or b is None and not c]
    finally:
        db.close()


def entry_threshold(path, entry_question, default, encoder_identity=None):
    """Return the actual cutoff (None means unresolved), never a fabricated safe cut."""
    receipt = {'vcache': VERSION, 'entry': key(entry_question), 'qualified': False,
               'qualification_reason': 'adaptive proposal; post-selection reuse risk is not certified'}
    try:
        binding = policy(encoder_identity)
    except (ValueError, TypeError):
        return default, {**receipt, 'effective': 'global', 'source': 'global (invalid error policy)', 'policy': None}
    receipt.update(policy=binding, delta=binding['error_target'])
    if not enabled(): return default, {**receipt, 'effective': 'global', 'source': 'disabled'}
    doc = table() or {}
    matches = (bool(encoder_identity) and doc.get('version') == VERSION and doc.get('policy') == binding
               and doc.get('delta') == binding['error_target'] and doc.get('encoder_identity') == encoder_identity
               and isinstance(doc.get('data_identity'), str) and len(doc['data_identity']) == 64
               and (doc.get('qualification') or {}).get('method') == METHOD)
    k = key(entry_question)
    raw_row = (doc.get('entries') or {}).get(k)
    row = (doc.get('entries') or {}).get(k) if matches else None
    if raw_row and raw_row.get('threshold') is None and doc.get('encoder_identity') == encoder_identity and not matches:
        return None, {**receipt, 'effective': 'unresolved',
                      'source': 'unresolved entry requires new matching evidence'}
    receipt['data_identity'] = doc.get('data_identity') if matches else None
    try:
        online = online_observations(path, k, binding) if path else []
    except sqlite3.Error:
        online = []
    if online and matches:
        offline = [{'key': k, 'score': s, 'correct': c} for s, c in (row or {}).get('_points', [])]
        g = doc.get('global') or {}
        prior = (g['t'], g['gamma']) if 't' in g else None
        pts = offline + online
        if len(pts) >= 3 and prior:
            te, ge, ce = fit_curve([p['score'] for p in pts], [p['correct'] for p in pts], prior=prior)
            cut = cut_for(te, ge, ce, binding['error_target'])
            return cut, {**receipt, 'effective': 'adaptive' if cut is not None else 'unresolved',
                         'source': 'entry (offline + matching online)' if cut is not None else 'entry evidence unresolved',
                         'n': len(pts), 'online': len(online)}
    if row and row.get('threshold') is None:
        return None, {**receipt, 'effective': 'unresolved', 'source': 'entry evidence unresolved',
                      'n': row.get('n'), 'errors': row.get('errors')}
    cut = row['threshold'] if row else default
    if row and (not isinstance(cut, (int, float)) or not math.isfinite(cut) or not FLOOR <= cut <= 1):
        return None, {**receipt, 'effective': 'unresolved', 'source': 'invalid entry cutoff'}
    errs = [o['score'] for o in online if not o['correct']]
    if errs:
        cut = max(cut, round(max(errs) + 0.0025, 4))
        return cut if cut <= 1 else None, {**receipt, 'effective': 'adaptive' if cut <= 1 else 'unresolved',
                'source': 'cut raised by online error', 'online': len(online)}
    if row:
        return cut, {**receipt, 'effective': 'adaptive', 'source': 'entry (fitted proposal)',
                     'n': row.get('n'), 'errors': row.get('errors')}
    reason = 'no entry evidence' if matches or not doc else 'stale or incomplete policy/scorer/evidence binding'
    return default, {**receipt, 'effective': 'global', 'source': 'global (' + reason + ')'}


def log_reuse(path, question, entry_question, similarity, plan, binding=None):
    if _evaluating(): return
    try:
        db = _db(path)
        try:
            db.execute('INSERT INTO reuse(question,entry,similarity,plan_digest,created,binding) VALUES (?,?,?,?,?,?)', (key(question), key(entry_question), float(similarity),
                                                              _digest(plan), time.time(), _binding(binding)))
            db.commit()
        finally:
            db.close()
    except (sqlite3.Error, OSError):
        pass  # the cache answer stands; only the learning signal is lost


def observe(path, entry_question, similarity, correct, source, question=None, binding=None):
    if _evaluating(): return
    db = _db(path)
    try:
        db.execute('INSERT INTO observation(entry,similarity,correct,source,question,created,binding) VALUES (?,?,?,?,?,?,?)', (key(entry_question), float(similarity), int(bool(correct)),
                                                                   source, key(question or ''), time.time(), _binding(binding)))
        db.commit()
    finally:
        db.close()


def _digest(plan):
    import hashlib
    q = plan.get('query') if isinstance(plan, dict) else plan
    return hashlib.sha256(str(q).encode()).hexdigest()[:16]


def _reuses(path, question):
    target = Path(path) / 'vcache.sqlite3'
    if not target.exists(): return []
    db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    try:
        field = 'binding' if 'binding' in {r[1] for r in db.execute('PRAGMA table_info(reuse)')} else 'NULL'
        return [(e, s, d, json.loads(b) if b else None) for e, s, d, b in db.execute(
            'SELECT entry, similarity, plan_digest, ' + field + ' FROM reuse WHERE question = ? ORDER BY created DESC',
            (key(question),))]
    finally:
        db.close()


def on_correction(path, question):
    """A user corrected a question the cache answered by reuse -> one error observation per reused entry."""
    n = 0
    for entry, sim, _, binding in _reuses(path, question):
        observe(path, entry, sim, False, 'user_correction', question, binding); n += 1
    return n


def on_verified(path, question, plan):
    """Later independently verified query agrees exactly: reference-plan agreement, not clinical truth."""
    n = 0
    for entry, sim, digest, binding in _reuses(path, question):
        observe(path, entry, sim, digest == _digest(plan), 'later_verification', question, binding); n += 1
    return n


def write(doc, path=THRESHOLDS):
    Path(path).write_text(json.dumps(doc, indent=1, sort_keys=True))
    _LOADED['mtime'] = None
    return path
