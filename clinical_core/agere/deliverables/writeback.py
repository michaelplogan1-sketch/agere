"""Write a person's decision from a deliverable back to the workspace action store.

record_decision(ws_dir, item_id, decision, ...) appends to the work item's payload['human_decisions'] (new revision) and
writes an append-only work_events row. The backbone keeps unknown payload keys on later runs, so decisions survive replays.
Only work items of this workspace's store can be written; decisions are short codes from DECISIONS plus a free-text note.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re

DECISIONS = ('alternative_agent', 'rechallenge', 'accept_barrier', 'reask_patient', 'refer_prescriber', 'approve', 'override',
             'escalate', 'close', 'reopen', 'appeal', 'reroute')


def record_decision(ws_dir, item_id, decision, *, note='', actor='person', deliverable_id=None, patient_id=None):
    from ..plans import backbone
    if decision not in DECISIONS:
        raise ValueError(f'decision must be one of {DECISIONS}')
    if not item_id or not re.fullmatch(r'W[IF]-[0-9a-f]{16}', str(item_id)):
        raise ValueError('item_id must be a work item id (WI-... or WF-...)')
    note = str(note or '')[:1000]
    db = backbone.database(ws_dir)
    try:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT revision, payload, patient FROM work_items WHERE id=?', (item_id,)).fetchone()
        if row is None:
            db.execute('ROLLBACK')
            raise FileNotFoundError(f'No work item {item_id!r}')
        if patient_id and row[2] != patient_id:
            db.execute('ROLLBACK')
            raise ValueError('patient_id does not match the work item')
        rev, payload = row[0], json.loads(row[1])
        entry = {'decision': decision, 'note': note, 'actor': str(actor)[:120], 'deliverable_id': deliverable_id,
                 'at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
        payload.setdefault('human_decisions', []).append(entry)
        payload['revision'] = rev + 1
        db.execute('UPDATE work_items SET revision=?, payload=? WHERE id=?', (rev + 1, json.dumps(payload, default=str), item_id))
        db.execute('INSERT INTO work_events VALUES (?,?,?)', (item_id, rev + 1, json.dumps({'action': 'human_decision', **entry})))
        db.execute('COMMIT')
    except BaseException:
        if db.in_transaction:
            db.execute('ROLLBACK')
        raise
    finally:
        db.close()
    return {'item_id': item_id, 'revision': rev + 1, 'recorded': entry}
