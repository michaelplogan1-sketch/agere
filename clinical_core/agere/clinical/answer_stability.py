"""pass^k answer stability (FRONTIER #11): an answer counts as stable only when all k independent executions agree.

stability(answers) groups k executed answers by equivalence (verification_v2.same_answer through answer_guard._same: same
count / value / patient set / table) and reports pass^k (every sample in one group), the majority share and the number
of distinct answers. On the question path the samples are the accepted answer plus every independent proposer the
claim verifier (claim_verification.py) executed for the same question, plus internal samples a paid proposer already
produced (hosted SQL candidates, analyst votes) when they are on its receipt. A paid answer with k >= 2 that is not
pass^k gets a stability warning (heal, never block) and is never saved as a verified pair (learning_hooks.verified).
record() keeps one row per answered question in <stack>/answer-stability.sqlite3; report() gives the pass^k rate over
paid answers (the bench metric is bench/contest_report.py's majority-of-k / pass^k).
"""
import json
import sqlite3
import time
from pathlib import Path

VERSION = 'answer-stability/1'


def _same(a, b):
    from .answer_guard import _same as same
    try:
        return bool(same(a, b))
    except Exception:  # noqa: BLE001 - incomparable answers count as different
        return False


def stability(answers):
    """answers: [answer dict] (k samples). -> {'k', 'distinct', 'pass_k', 'majority_share', 'groups': [sizes], 'version'}"""
    groups = []
    for a in answers:
        if not isinstance(a, dict): continue
        for g in groups:
            if _same(g[0], a): g.append(a); break
        else:
            groups.append([a])
    k = sum(len(g) for g in groups)
    sizes = sorted((len(g) for g in groups), reverse=True)
    return {'k': k, 'distinct': len(groups), 'pass_k': k >= 2 and len(groups) == 1,
            'majority_share': round(sizes[0] / k, 4) if k else None, 'groups': sizes, 'version': VERSION}


def internal_samples(receipt):
    """Answers a proposer already produced internally (hosted SQL candidates' results; analyst votes) as answer dicts."""
    out = []
    for c in ((receipt or {}).get('hosted_sql') or {}).get('candidates') or []:
        if isinstance(c, dict) and isinstance(c.get('result'), dict):
            r = c['result']
            out.append({'status': 'answered', 'answer_type': r.get('answer_type') or ('count' if 'count' in r else 'value'), **r})
    for v in ((receipt or {}).get('hybrid') or {}).get('answers') or []:
        if isinstance(v, dict): out.append(v)
    return out


def record(path, question, proposer, paid, stab):
    target = Path(path) / 'answer-stability.sqlite3'
    db = sqlite3.connect(target)
    try:
        db.execute('CREATE TABLE IF NOT EXISTS stability(question TEXT, proposer TEXT, paid INTEGER, k INTEGER, distinct_answers INTEGER, '
                   'pass_k INTEGER, majority_share REAL, created REAL)')
        db.execute('INSERT INTO stability VALUES (?,?,?,?,?,?,?,?)', (question, proposer, int(bool(paid)), stab['k'], stab['distinct'],
                                                                      int(stab['pass_k']), stab['majority_share'], time.time()))
        db.commit()
    finally:
        db.close()


def report(path, *, paid_only=True):
    """pass^k over recorded answers with k >= 2: {'n', 'pass_k_rate', 'mean_majority_share', 'by_proposer'}."""
    target = Path(path) / 'answer-stability.sqlite3'
    if not target.exists(): return {'n': 0, 'pass_k_rate': None, 'version': VERSION}
    with sqlite3.connect(f'file:{target}?mode=ro', uri=True) as db:
        rows = db.execute('SELECT proposer, pass_k, majority_share FROM stability WHERE k >= 2' + (' AND paid = 1' if paid_only else '')).fetchall()
    by = {}
    for p, ok, share in rows:
        b = by.setdefault(p, [0, 0]); b[0] += 1; b[1] += ok
    return {'n': len(rows), 'pass_k_rate': round(sum(r[1] for r in rows) / len(rows), 4) if rows else None,
            'mean_majority_share': round(sum(r[2] or 0 for r in rows) / len(rows), 4) if rows else None,
            'by_proposer': {p: {'n': n, 'pass_k_rate': round(ok / n, 4)} for p, (n, ok) in by.items()}, 'version': VERSION}


def warning(stab):
    return (f"Answer stability: {stab['k']} independent executions gave {stab['distinct']} different answers "
            f"(majority {stab['majority_share']:.0%}); not pass^{stab['k']}.")


def dumps(stab): return json.dumps(stab, sort_keys=True)
