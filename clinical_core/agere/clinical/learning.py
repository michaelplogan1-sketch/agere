"""Capture for the learning loop: every question's outcome, and every verified (question -> plan) pair.

Only verified or user-confirmed answers become training data; Agere never learns from its own
unverified output. Declines and escalations are kept with their reasons so gaps can be clustered
into new grammar rules or bottled-model training examples.
"""
import json, os, sqlite3, time
from pathlib import Path

VERSION = 'learning-store/1'
SCHEMA = """
CREATE TABLE IF NOT EXISTS verified(question TEXT, plan_kind TEXT, plan TEXT, signature TEXT, route TEXT,
                                    models TEXT, agreement TEXT, created REAL, PRIMARY KEY(question, plan));
CREATE TABLE IF NOT EXISTS outcomes(question TEXT, route TEXT, status TEXT, reason TEXT, paid_calls INTEGER,
                                    usd REAL, created REAL);
CREATE TABLE IF NOT EXISTS corrections(question TEXT, note TEXT, expected TEXT, created REAL);
CREATE TABLE IF NOT EXISTS routing(question TEXT, decision TEXT, operator TEXT, parameters TEXT, chooser TEXT, executed INTEGER, created REAL);
"""


def _db(path):
    db = sqlite3.connect(Path(path) / 'learning.sqlite3')
    db.executescript(SCHEMA)
    if 'context' not in [r[1] for r in db.execute('PRAGMA table_info(verified)')]:
        db.execute('ALTER TABLE verified ADD COLUMN context TEXT')
    return db


def context(path):
    """What a verified answer depends on: the data release and the semantic layer that interpreted it."""
    from . import semantic_layer
    meta = json.loads((Path(path) / 'run.json').read_text())
    return f"{meta['source_digest']}|{semantic_layer.VERSION}"


def record_verified(path, question, plan_kind, plan, signature, route, models, agreement):
    db = _db(path)
    try:
        db.execute('INSERT OR REPLACE INTO verified VALUES (?,?,?,?,?,?,?,?,?)',
                   (question.strip(), plan_kind, json.dumps(plan, sort_keys=True), json.dumps(signature, default=str)[:4000],
                    route, json.dumps(models), agreement, time.time(), context(path)))
        db.commit()
    finally:
        db.close()


def lookup(path, question):
    """A previously verified plan for this exact question (normalised), or None."""
    target = Path(path) / 'learning.sqlite3'
    if not target.exists() or os.environ.get('AGERE_LEARNED', 'on') == 'off': return None
    _db(path).close()  # migrate older stores
    db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    try:
        corrected = db.execute('SELECT 1 FROM corrections WHERE lower(trim(question)) = lower(trim(?))', (question,)).fetchone()
        if corrected: return None  # a user flagged this question: re-derive, never reuse
        row = db.execute('SELECT plan_kind, plan, agreement FROM verified WHERE lower(trim(question)) = lower(trim(?)) AND context = ? '
                         'ORDER BY created DESC LIMIT 1', (question, context(path))).fetchone()
    finally:
        db.close()
    return {'plan_kind': row[0], 'plan': json.loads(row[1]), 'agreement': row[2]} if row else None


def record_outcome(path, question, route, status, reason=None, paid_calls=0, usd=0.0):
    db = _db(path)
    try:
        db.execute('INSERT INTO outcomes VALUES (?,?,?,?,?,?,?)',
                   (question.strip(), route, status, (reason or '')[:500], int(paid_calls or 0), float(usd or 0), time.time()))
        db.commit()
    finally:
        db.close()


def record_routing(path, question, info, executed):
    """The operator router's decision for a question (training data for the learned router; never benchmark truth)."""
    if not info or not info.get('decision'): return
    choice = info.get('choice') or {}
    chooser = info.get('chosen_by') or 'paid_router'
    db = _db(path)
    try:
        db.execute('INSERT INTO routing VALUES (?,?,?,?,?,?,?)',
                   (question.strip(), info['decision'], choice.get('name'), json.dumps(choice.get('input'), default=str)[:4000],
                    chooser, int(bool(executed)), time.time()))
        db.commit()
    finally:
        db.close()


def record_correction(path, question, note, expected=None):
    db = _db(path)
    try:
        db.execute('INSERT INTO corrections VALUES (?,?,?,?)', (question.strip(), note[:2000], json.dumps(expected), time.time()))
        db.commit()
    finally:
        db.close()


def summary(path):
    db = _db(path)
    try:
        routes = dict(db.execute('SELECT route, count(*) FROM outcomes GROUP BY route').fetchall())
        statuses = dict(db.execute('SELECT status, count(*) FROM outcomes GROUP BY status').fetchall())
        verified = db.execute('SELECT count(*) FROM verified').fetchone()[0]
        corrections = db.execute('SELECT count(*) FROM corrections').fetchone()[0]
        gaps = db.execute("SELECT reason, count(*) n FROM outcomes WHERE status IN ('declined','escalated') AND reason <> '' "
                          "GROUP BY reason ORDER BY n DESC LIMIT 15").fetchall()
    finally:
        db.close()
    return {'schema': VERSION, 'routes': routes, 'statuses': statuses, 'verified_training_pairs': verified,
            'corrections': corrections, 'top_gaps': [{'reason': r, 'count': n} for r, n in gaps]}


def loop_summary(path):
    """Read-only summary of the last learning-loop run (tools/learning_loop) for this stack, or status not_run."""
    root = Path(__file__).resolve().parents[3]
    base = (Path(os.environ['AGERE_LEARNING_LOOP_DIR']) if os.environ.get('AGERE_LEARNING_LOOP_DIR')
            else root / 'artifacts' / 'learning-loop' / Path(path).parent.name)
    target = base / 'loop.json'
    if not target.exists():
        return {'schema': 'learning-loop/1', 'status': 'not_run',
                'detail': 'Run python -m tools.learning_loop to cluster gaps, write regression cases and grammar proposals.'}
    try:
        result = json.loads(target.read_text())
    except ValueError:
        return {'schema': 'learning-loop/1', 'status': 'unreadable', 'detail': target.name}
    result['age_seconds'] = round(time.time() - target.stat().st_mtime)
    return result
