"""Read-only access to the semantic layer, and the one place SQL is executed."""
import os
import re
import sqlite3
import time
from pathlib import Path

DEFAULT_DB = 'artifacts/agere-full-stack-rerun-2026-10-30/focused/runtime/stack/semantic.sqlite3'
# Tables a recipe may read. `document` is a catalog view over notes, readable scans and exchange documents; document
# text itself is never selectable by a recipe: it is fetched by texts() for the reading tier only.
TABLES = ('document', 'note_fact', 'note_therapy_change', 'therapy_barrier', 'utilization_event', 'stay', 'appointment', 'visit', 'fill', 'claim', 'patient', 'concept_patient', 'member_month',
          'measurement_latest', 'immunization', 'death', 'organization', 'clinician', 'enrollment_span', 'condition',
          'encounter', 'prescription', 'allergy_entry', 'smoking_status', 'social_need', 'wellness_visit', 'cancer_screening')
SECONDS = 20.0


def path():
    return Path(os.environ.get('AGERE_TIERED_DB', DEFAULT_DB))


def connect():
    db = sqlite3.connect(path().resolve().as_uri() + '?mode=ro', uri=True)
    db.execute(DOCUMENT_VIEW)
    db.execute('PRAGMA query_only=ON')
    return db


DOCUMENT_VIEW = """CREATE TEMP VIEW document AS
SELECT record_id, patient_id, 'clinical_note' AS source, note_type AS document_type, event_date, organization_id, text_chars FROM clinical_note
UNION ALL SELECT record_id, patient_id, 'scanned_document', document_type, event_date, NULL, text_chars FROM scanned_document WHERE readable = 1
UNION ALL SELECT record_id, patient_id, 'hie_document', document_type, event_date, author_organization_id, text_chars FROM hie_document"""


def portable(sql):
    """The same SELECT runnable on the semantic layer by anyone: the document catalog is inlined as a CTE,
    because the view exists only on this module's connections."""
    text = sql.strip().rstrip(';')
    if not re.search(r'(?i)\bdocument\b(?!_)', text): return text
    body = DOCUMENT_VIEW.split(' AS\n', 1)[1]
    if re.match(r'(?is)^\s*with\b', text):
        return re.sub(r'(?is)^\s*with\b', 'WITH document AS (' + body + '),', text, count=1)
    return 'WITH document AS (' + body + ') ' + text


def texts(record_ids, db):
    """Document text for the reading tier. -> {record_id: {'text', 'event_date', 'source'}}. Only catalogued documents."""
    out, ids = {}, list(dict.fromkeys(record_ids))
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        marks = ','.join('?' * len(chunk))
        for rid, source, day, text in db.execute(
                f"SELECT d.record_id, d.source, d.event_date, COALESCE(t.text, json_extract(r.payload, '$.text')) FROM document d "
                f"LEFT JOIN scanned_document_text t ON t.record_id = d.record_id AND d.source = 'scanned_document' "
                f"LEFT JOIN record r ON r.record_id = d.record_id AND d.source <> 'scanned_document' WHERE d.record_id IN ({marks})", chunk):
            out[rid] = {'text': text or '', 'event_date': day, 'source': source}
    return out


def schema_version(db=None):
    own = db is None
    db = db or connect()
    try:
        rows = dict(db.execute("SELECT key, value FROM meta WHERE key IN ('version', 'source_digest')"))
        return f"{rows.get('version')}:{str(rows.get('source_digest'))[:16]}"
    finally:
        if own: db.close()


_as_of = {}


def as_of(db):
    """The semantic layer's own snapshot date: the clock for anything dated, never today's date."""
    key = str(path())
    if key not in _as_of:
        _as_of[key] = db.execute("SELECT value FROM meta WHERE key = 'as_of'").fetchone()[0][:10]
    return _as_of[key]


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
    if re.search(r'(?i)\b(scanned_document_text|payload)\b', text):
        raise RecipeError('A recipe may not select document text; use a reading plan')
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
