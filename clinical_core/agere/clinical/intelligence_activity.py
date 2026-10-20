"""Read-only, source-scoped activity for the illustrative neural field.

No question text, patient data, training, promotion or model calls. A successful
answer is activity, not a newly verified training example. Missing stores remain
unavailable rather than being reported as a measured zero.
"""
import json
import sqlite3
from pathlib import Path
from . import semantic_layer


def _read(path, query, args=()):
    if not path.exists():
        return None
    try:
        db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=.5)
        try:
            return db.execute(query, args).fetchone()[0]
        finally:
            db.close()
    except sqlite3.Error:
        return None


def snapshot(path):
    path = Path(path)
    meta = json.loads((path / 'run.json').read_text())
    source = meta['source_digest']
    context = f'{source}|{semantic_layer.VERSION}'
    answers = _read(path / 'answers.sqlite3', "SELECT count(*) FROM answers WHERE status='answered'")
    examples = _read(path / 'learning.sqlite3', '''SELECT count(*) FROM verified v
        WHERE context=? AND NOT EXISTS (SELECT 1 FROM corrections c
          WHERE lower(trim(c.question))=lower(trim(v.question)))''', (context,))
    corrections = _read(path / 'learning.sqlite3', 'SELECT count(*) FROM corrections')
    return {
        'schema': 'intelligence-activity/1', 'source_id': source,
        'as_of': meta['snapshot']['as_of'], 'synthetic': bool(meta.get('synthetic')),
        'records': meta.get('visible_records', meta['snapshot'].get('visible_records')),
        'answered': answers, 'reusable_examples': examples, 'corrections': corrections,
        'scope': 'Recorded answers in this source installation; reusable examples match the current source and semantic version and exclude corrected questions.',
        'learning': 'Checked-plan reuse and correction capture. This view does not train or promote models.',
    }
