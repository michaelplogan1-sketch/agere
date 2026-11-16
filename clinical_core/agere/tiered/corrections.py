"""When an answer is wrong: a person reports it, the method that gave it stops answering, a reviewer settles it, and the
time to fix is measured.

A general-purpose assistant that is told it was wrong answers the next person the same way. Here a report has effect at
once and is kept until someone with authority settles it:
  - a saved recipe (bottle) that gave the answer is set to status 'reported': it answers nothing until reviewed (upkeep
    leaves this status alone, so model agreement cannot reinstate it);
  - a built-in recipe is reviewed code and stays in service, but no longer answers the reported wording: the question
    climbs past it to the next rung;
  - a frontier-written answer has no recipe to stop; the report records its SQL for the reviewer.
Resolutions: 'fixed' (the method was corrected or replaced), 'not_wrong' (the answer stands: a saved recipe is promoted
again), 'definition' (the answer followed a governed definition the person did not expect; the definition is the thing to
review). Every report keeps who, when, what was answered and why it was thought wrong; question text is kept here because
the reviewer needs it, and this store holds the same data the answer did.

    report(question, answer, note, reporter)  -> report id
    blocked(question)                         -> ids of recipes and saved recipes that may not answer this wording
    resolve(report_id, resolution, reviewer, note)
    summary()                                 -> open, resolved, median hours to resolve, by method
"""
import contextvars
import json
import os
import re
import sqlite3
import statistics
import time
from datetime import datetime
from pathlib import Path

DEFAULT = Path('artifacts/agere-tiered-core/corrections.sqlite3')
RESOLUTIONS = ('fixed', 'not_wrong', 'definition')
# The organization a request acts for on the hosted server (None: the local app, one organization). A report, the methods it
# blocks, and who may see or resolve it are all scoped to it; only a report made locally changes a shared saved recipe.
ORG = contextvars.ContextVar('agere_correction_org', default=None)


def _path():
    return Path(os.environ.get('AGERE_TIERED_CORRECTIONS', DEFAULT))


def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _norm(question):
    return ' '.join(re.sub(r'[^a-z0-9 ]', ' ', str(question).lower()).split())


def _db():
    p = _path(); p.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(p)
    db.execute('CREATE TABLE IF NOT EXISTS report(id INTEGER PRIMARY KEY, at TEXT, question TEXT, question_norm TEXT, tier TEXT, recipe_id TEXT, '
               'bottle_id INTEGER, computation TEXT, rows_json TEXT, note TEXT, reporter TEXT, status TEXT, resolution TEXT, reviewer TEXT, '
               'resolved_at TEXT, resolution_note TEXT)')
    if 'org' not in [c[1] for c in db.execute('PRAGMA table_info(report)')]: db.execute('ALTER TABLE report ADD COLUMN org TEXT')
    return db


def report(question, answer, note, reporter=None, bottles=None, org=None):
    """answer: {'tier', 'recipe' (the receipt's recipe: recipe_id or bottle_id, sql), 'rows'} as the app returned it."""
    org = org if org is not None else ORG.get()
    note = ' '.join(str(note or '').split())
    if not note: raise ValueError('Say what is wrong with the answer, so a reviewer can check it.')
    recipe = answer.get('recipe') or {}
    bottle_id = recipe.get('bottle_id')
    db = _db()
    cur = db.execute('INSERT INTO report(at, question, question_norm, tier, recipe_id, bottle_id, computation, rows_json, note, reporter, status, org) '
                     'VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                     (_now(), question, _norm(question), answer.get('tier'), recipe.get('recipe_id'), bottle_id, recipe.get('sql') or answer.get('computation'),
                      json.dumps((answer.get('rows') or [])[:20], default=str), note[:2000], str(reporter) if reporter is not None else None, 'open', org))
    db.commit(); rid = cur.lastrowid; db.close()
    if bottle_id is not None and org is None:   # a hosted organization's report blocks the recipe for that organization only (blocked())
        from . import bottles as bottle_store
        b = bottles or bottle_store.Bottles()
        b.set_status(bottle_id, 'reported', f'report {rid}: {note[:200]}')
    return rid


def blocked(question):
    """Recipe ids and bottle ids with an open report on this wording, for the organization this request acts for."""
    if not _path().exists(): return set()
    db = _db()
    rows = db.execute("SELECT recipe_id, bottle_id FROM report WHERE status = 'open' AND question_norm = ? AND org IS ?", (_norm(question), ORG.get())).fetchall()
    db.close()
    return {r for pair in rows for r in pair if r is not None}


def resolve(report_id, resolution, reviewer, note='', bottles=None, org=None):
    if resolution not in RESOLUTIONS: raise ValueError(f'resolution is one of {RESOLUTIONS}')
    org = org if org is not None else ORG.get()
    db = _db()
    row = db.execute('SELECT status, bottle_id FROM report WHERE id = ? AND org IS ?', (report_id, org)).fetchone()
    if row is None: raise KeyError(f'no report {report_id}')   # another organization's report is not found, not refused
    if row[0] != 'open': raise ValueError(f'report {report_id} is already resolved')
    db.execute('UPDATE report SET status = ?, resolution = ?, reviewer = ?, resolved_at = ?, resolution_note = ? WHERE id = ?',
               ('resolved', resolution, str(reviewer), _now(), ' '.join(str(note).split())[:2000], report_id))
    db.commit(); db.close()
    if row[1] is not None and org is None:
        from . import bottles as bottle_store
        b = bottles or bottle_store.Bottles()
        # a saved recipe found right answers again; one that was wrong stays out of service (a corrected one is saved anew)
        b.set_status(row[1], 'promoted' if resolution == 'not_wrong' else 'retired', f'report {report_id} resolved: {resolution}')


def reports(status=None, limit=100, org=None):
    org = org if org is not None else ORG.get()
    db = _db()
    cols = ['id', 'at', 'question', 'tier', 'recipe_id', 'bottle_id', 'computation', 'rows_json', 'note', 'reporter', 'status', 'resolution',
            'reviewer', 'resolved_at', 'resolution_note']
    q = 'SELECT ' + ', '.join(cols) + ' FROM report WHERE org IS ?' + (' AND status = ?' if status else '') + ' ORDER BY id DESC LIMIT ?'
    out = [dict(zip(cols, r)) for r in db.execute(q, (org,) + ((status,) if status else ()) + (int(limit),))]
    db.close()
    for r in out: r['rows'] = json.loads(r.pop('rows_json') or '[]')
    return out


def summary(org=None):
    """-> {'open', 'resolved', 'by_resolution', 'median_hours_to_resolve', 'by_tier'}: the time-to-fix measure."""
    rs = reports(limit=100000, org=org)
    hours = [(datetime.fromisoformat(r['resolved_at'].replace('Z', '+00:00')) - datetime.fromisoformat(r['at'].replace('Z', '+00:00'))).total_seconds() / 3600
             for r in rs if r['resolved_at']]
    by = lambda key: {k: sum(1 for r in rs if r[key] == k) for k in sorted({r[key] for r in rs if r[key]})}
    return {'open': sum(1 for r in rs if r['status'] == 'open'), 'resolved': len(hours), 'by_resolution': by('resolution'),
            'median_hours_to_resolve': round(statistics.median(hours), 2) if hours else None, 'by_tier': by('tier')}
