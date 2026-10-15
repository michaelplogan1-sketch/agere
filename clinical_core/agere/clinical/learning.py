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
CREATE TABLE IF NOT EXISTS quarantined(question TEXT, plan_kind TEXT, plan TEXT, signature TEXT, route TEXT, agreement TEXT,
                                       controls TEXT, created REAL, context TEXT);
"""


def _db(path):
    db = sqlite3.connect(Path(path) / 'learning.sqlite3')
    db.executescript(SCHEMA)
    if 'context' not in [r[1] for r in db.execute('PRAGMA table_info(verified)')]:
        db.execute('ALTER TABLE verified ADD COLUMN context TEXT')
    # provenance: JSON for pairs carried forward from an earlier release (verified_carry_forward.py); NULL when captured here
    for table in ('verified', 'quarantined'):
        if 'provenance' not in [r[1] for r in db.execute(f'PRAGMA table_info({table})')]:
            db.execute(f'ALTER TABLE {table} ADD COLUMN provenance TEXT')
    return db


def context(path):
    """What a verified answer depends on: the data release and the semantic layer that interpreted it."""
    from . import semantic_layer
    meta = json.loads((Path(path) / 'run.json').read_text())
    return f"{meta['source_digest']}|{semantic_layer.VERSION}"


def record_verified(path, question, plan_kind, plan, signature, route, models, agreement):
    db = _db(path)
    try:
        db.execute('INSERT OR REPLACE INTO verified(question, plan_kind, plan, signature, route, models, agreement, created, context) '
                   'VALUES (?,?,?,?,?,?,?,?,?)',
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


# Answer-level controls that, replayed over past blind runs, fired only on wrong answers (docs/agere/FMEA.md), plus the
# deterministic oracles (oracles.py) whose warnings carry the same weight for capture.
QUARANTINE_CONTROLS = {'QC-PERIOD', 'QC-UNITS', 'QC-NUMBERS', 'QC-PARTS', 'QC-CLOCK', 'QC-PERTURB', 'QC-PARTITION',
                       'QC-METRIC'}  # QC-METRIC: query departs from the governed metric (metrics.py); its 'computed as' note is info


def capture_verified(path, question, planner_receipt, answer):
    """FMEA FM-23 at the source: record a hosted verified pair only AFTER the answer was built and its checks ran.

    Called once per answered question (conversation.handle_compose). The planner leaves the candidate pair in
    planner_receipt['hosted_sql']['capture']; it becomes a verified training pair only when the answer is answered, carries
    no high-precision warning (QUARANTINE_CONTROLS), the judge's query raised no oracle finding, and the candidates did
    not split into two defensible readings (FM-19: an ambiguous question has no single verified plan). Anything else is
    written to the 'quarantined' table with the reasons, for audit, and never reused or trained on.
    Returns {'recorded': bool, 'reasons': [...]}."""
    pr = planner_receipt or {}
    hosted = pr.get('hosted_sql') or {}
    cap = hosted.get('capture') or pr.get('capture')
    if pr.get('route') != 'hosted_verified_sql' or not cap: return {'recorded': False, 'reasons': ['not a hosted verified answer']}
    answer = answer or {}
    reasons = sorted({c['control'] for c in answer.get('quality_checks') or []
                      if c.get('severity') == 'warning' and c.get('control') in QUARANTINE_CONTROLS})
    if answer.get('status') not in ('answered', 'partial'): reasons.append(f"answer status {answer.get('status')}")
    if (hosted.get('verification') or {}).get('judge_findings'): reasons.append('judge query failed a deterministic check')
    if (hosted.get('verification') or {}).get('dissent'): reasons.append('an independent candidate disagrees')
    if hosted.get('readings') or answer.get('readings'): reasons.append('two defensible readings')
    if (pr.get('acceptance') or {}).get('status') == 'low_confidence': reasons.append('low-confidence free-form SQL (metric_router.acceptance)')
    db = _db(path)
    try:
        if reasons:
            db.execute('INSERT INTO quarantined(question, plan_kind, plan, signature, route, agreement, controls, created, context) '
                       'VALUES (?,?,?,?,?,?,?,?,?)',
                       (question.strip(), 'sql', json.dumps(cap['plan'], sort_keys=True), str(cap.get('signature'))[:4000], pr.get('route'),
                        cap.get('agreement'), json.dumps(reasons), time.time(), context(path)))
            db.commit()
            return {'recorded': False, 'reasons': reasons}
    finally:
        db.close()
    try: signature = json.loads(cap.get('signature')) if isinstance(cap.get('signature'), str) else cap.get('signature')
    except ValueError: signature = cap.get('signature')
    record_verified(path, question, 'sql', cap['plan'], signature, 'hosted_verified_sql', cap.get('models') or [], cap.get('agreement'))
    return {'recorded': True, 'reasons': []}


def quarantine(path, question, answer):
    """A verified pair whose answer trips a high-precision quality control must not be reused or trained on (FMEA FM-13/FM-23):
    two models can agree on the same wrong answer. Logged as a correction with a 'quarantine:' note, so every consumer of
    corrections (exact reuse, meaning cache, training-data promotion) already excludes it; the answer itself is unchanged."""
    hits = sorted({c['control'] for c in (answer or {}).get('quality_checks') or []
                   if c.get('severity') == 'warning' and c.get('control') in QUARANTINE_CONTROLS})
    if hits: record_correction(path, question, 'quarantine: ' + ', '.join(hits))
    return hits


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
