"""Read-only access to the semantic layer, and the one place SQL is executed."""
import os
import re
import sqlite3
import time
from pathlib import Path

DEFAULT_DB = 'artifacts/agere-full-stack-rerun-2026-10-30/focused/runtime/stack/semantic.sqlite3'
# Tables a recipe may read. Raw record payloads and document text stay out of this core for now.
TABLES = ('utilization_event', 'stay', 'appointment', 'visit', 'fill', 'claim', 'patient', 'concept_patient', 'member_month',
          'measurement_latest', 'immunization', 'death', 'organization', 'clinician', 'enrollment_span', 'condition',
          'encounter', 'prescription', 'allergy_entry', 'smoking_status', 'social_need', 'wellness_visit', 'cancer_screening')
SECONDS = 20.0


def path():
    return Path(os.environ.get('AGERE_TIERED_DB', DEFAULT_DB))


def connect():
    db = sqlite3.connect(path().resolve().as_uri() + '?mode=ro', uri=True)
    db.execute('PRAGMA query_only=ON')
    return db


def schema_version(db=None):
    own = db is None
    db = db or connect()
    try:
        rows = dict(db.execute("SELECT key, value FROM meta WHERE key IN ('version', 'source_digest')"))
        return f"{rows.get('version')}:{str(rows.get('source_digest'))[:16]}"
    finally:
        if own: db.close()


def schema_text(db=None):
    """Table and column names only: what a model may see. Never rows."""
    own = db is None
    db = db or connect()
    try:
        return '\n'.join(f"{t}({', '.join(c[1] for c in db.execute(f'PRAGMA table_info({t})'))})" for t in TABLES)
    finally:
        if own: db.close()


class RecipeError(ValueError):
    pass


def run(sql, params=None, db=None):
    """Execute one read-only SELECT. -> (columns, rows). Raises RecipeError on anything else."""
    text = sql.strip().rstrip(';')
    if ';' in text or not re.match(r'(?is)^\s*(select|with)\b', text):
        raise RecipeError('A recipe is exactly one SELECT statement')
    if re.search(r'(?i)\b(attach|pragma|insert|update|delete|drop|alter|create|replace|vacuum)\b', text):
        raise RecipeError('A recipe may only read')
    own = db is None
    db = db or connect()
    deadline = time.monotonic() + SECONDS
    db.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 20000)
    try:
        cur = db.execute(text, params or {})
        return [d[0] for d in cur.description], [list(r) for r in cur.fetchall()]
    except sqlite3.Error as exc:
        raise RecipeError(f'{type(exc).__name__}: {exc}') from exc
    finally:
        db.set_progress_handler(None, 0)
        if own: db.close()
