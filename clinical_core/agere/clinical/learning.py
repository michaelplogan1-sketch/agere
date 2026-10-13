"""Capture for the learning loop: every question's outcome, and every verified (question -> plan) pair.

Only verified or user-confirmed answers become training data; Agere never learns from its own
unverified output. Declines and escalations are kept with their reasons so gaps can be clustered
into new grammar rules or bottled-model training examples.
"""
import json, sqlite3, time
from pathlib import Path

VERSION = 'learning-store/1'
SCHEMA = """
CREATE TABLE IF NOT EXISTS verified(question TEXT, plan_kind TEXT, plan TEXT, signature TEXT, route TEXT,
                                    models TEXT, agreement TEXT, created REAL, PRIMARY KEY(question, plan));
CREATE TABLE IF NOT EXISTS outcomes(question TEXT, route TEXT, status TEXT, reason TEXT, paid_calls INTEGER,
                                    usd REAL, created REAL);
CREATE TABLE IF NOT EXISTS corrections(question TEXT, note TEXT, expected TEXT, created REAL);
"""


def _db(path):
    db = sqlite3.connect(Path(path) / 'learning.sqlite3')
    db.executescript(SCHEMA)
    return db


def record_verified(path, question, plan_kind, plan, signature, route, models, agreement):
    db = _db(path)
    try:
        db.execute('INSERT OR REPLACE INTO verified VALUES (?,?,?,?,?,?,?,?)',
                   (question.strip(), plan_kind, json.dumps(plan, sort_keys=True), json.dumps(signature, default=str)[:4000],
                    route, json.dumps(models), agreement, time.time()))
        db.commit()
    finally:
        db.close()


def record_outcome(path, question, route, status, reason=None, paid_calls=0, usd=0.0):
    db = _db(path)
    try:
        db.execute('INSERT INTO outcomes VALUES (?,?,?,?,?,?,?)',
                   (question.strip(), route, status, (reason or '')[:500], int(paid_calls or 0), float(usd or 0), time.time()))
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
