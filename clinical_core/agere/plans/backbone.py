"""Agere's single action backbone for customer workspaces: routed plans -> durable decisions and work items.

    res = run(ws.dir, plan, workspace=ws.slug, semantic_path=ws.semantic_path)
    items(ws.dir, lane='plan:pharmacy_gdmt'); item(ws.dir, item_id); decision(ws.dir, decision_id)

plans/router.py decides each patient's program; this module makes that decision durable and drives the work:

Store. One per workspace: <workspace>/action/program-work.sqlite3, created with the /stack decision and work-store code
(clinical/question_decision.database -> care_capabilities.db_open -> program_work.database): the same immutable
question_decisions / question_decision_tasks tables, the work_assessments (immutable), work_items (UNIQUE(patient,
program), revisioned), append-only work_events and request-idempotent work_requests tables, the same triggers. The demo
stack database is never read.

Decision. A routed plan is one question_decisions row whose id is the digest of (workspace, program kind, as_of, catalog
version, cohort, routing digest, each member's routing essentials). Re-running the same plan finds it (replay) and
changes nothing; a changed patient state makes a new decision whose unchanged members replay.

Work items. One item per patient per lane (lane = 'plan:<program kind>'); follow-up work is its own item
(lane + ':followup:<type>', e.g. appeal, alternate_program, pcp_notice). Each item binds an immutable assessment: the
routing essentials (route, program, slot, coverage, gap, barriers; never the date-relative fields). On a re-run:
- same assessment -> idempotent replay (receipts not yet due under an earlier `through` are revealed, never retracted);
- changed assessment -> reassessed: a new cycle with its own seed and steps, the old cycle kept in the event history,
  its open follow-ups superseded;
- now excluded (e.g. deceased) -> the item is cancelled with the reason.
Steps advance only on receipts from the simulated counterparty (plans/act.py, plans/counterparty.py); every receipt is
stored in work_requests under its request_id, so a duplicate submission returns the original receipt.

Completion. After every run the completion evaluator (plans/completion.py) checks enrolled items against the workspace
data (fills, PT claims) or the simulated attendance record; a closure that no longer holds is recorded as reopened.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
import json
from pathlib import Path
import sqlite3
import threading

from ...contracts import digest
from . import act as actmod, catalog as catmod, completion, router

SCHEMA = 'agere-workspace-action/1'
STORE_DIR = 'action'
_LOCKS, _GUARD = {}, threading.Lock()
LIMITS = ['Counterparties are simulated in-process adapters: no message, referral, enrollment or prior authorization is sent anywhere.',
          'Outcome rates are synthetic planning assumptions (programs.json simulation blocks), not measured vendor performance.',
          'Closures from workspace data show that a fill or claim exists, not that the program caused it.']


def _lock(path):
    with _GUARD:
        return _LOCKS.setdefault(str(Path(path).resolve()), threading.Lock())


def store_path(ws_dir):
    return Path(ws_dir) / STORE_DIR


def database(ws_dir):
    """The workspace action store (the /stack decision + work-store schema, bound to this workspace)."""
    from ..clinical import question_decision
    d = store_path(ws_dir)
    d.mkdir(parents=True, exist_ok=True)
    db = question_decision.database(d)
    db.execute('CREATE INDEX IF NOT EXISTS qd_tasks_task ON question_decision_tasks(task)')
    return db


def _v(f):
    return f.get('value') if isinstance(f, dict) else f


def essentials(row):
    """The routing facts an item depends on (no as_of-relative fields such as days_since_ed or priority)."""
    gap = row.get('gap') or {}
    cov = row.get('coverage') or {}
    cur, _ = actmod.barriers_of(gap)
    return {'route': row['route'], 'program_id': row.get('program_id'), 'vendor_slot': (row.get('vendor_slot') or {}).get('slot_id'),
            'coverage': {k: _v(cov.get(k)) for k in ('active', 'payer_id', 'product', 'line_of_business')},
            'gap': {'type': gap.get('type'), 'missing_pillars': sorted(_v(gap.get('missing_pillars')) or []),
                    'barrier_documented_pillars': sorted(_v(gap.get('barrier_documented_pillars')) or []),
                    'barrier_pillars': sorted(_v(gap.get('barrier_pillars')) or []), 'ed_visit_date': _v(gap.get('ed_visit_date'))},
            'barriers': sorted({(b['pillar'], b['barrier_type'], b.get('drug_key') or b.get('drug_class') or '', b.get('record_id') or '')
                                for b in cur})}


def _acute_facts(semantic_path, ids, as_of):
    """{patient_id: {'recent_acute_30d': True}} from utilization_event (ED, observation, inpatient) in the 30 days to as_of."""
    out = {}
    if not ids or semantic_path is None:
        return out
    try:
        db = sqlite3.connect(f'file:{Path(semantic_path)}?mode=ro', uri=True)
    except sqlite3.Error:
        return out
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='utilization_event'").fetchone():
            return out
        lo = (date.fromisoformat(as_of) - timedelta(days=30)).isoformat()
        ids = list(ids)
        for i in range(0, len(ids), 500):
            ch = ids[i:i + 500]
            for (pid,) in db.execute(f"SELECT DISTINCT patient_id FROM utilization_event WHERE kind IN ('ed_visit','observation','inpatient') "
                                     f"AND event_date BETWEEN ? AND ? AND patient_id IN ({','.join('?' * len(ch))})", [lo, as_of] + ch):
                out[pid] = {'recent_acute_30d': True}
    finally:
        db.close()
    return out


def _later(a, b):
    """The later of two `through` values, where None means 'no cut' (everything due)."""
    if a is None or b is None:
        return None
    return max(a, b)


def _load_ledger(db, action_id):
    return {rid: json.loads(p) for rid, p in db.execute('SELECT id, payload FROM work_requests WHERE id LIKE ?', (action_id + ':%',))}


def _receipt_ids(action):
    return [s['receipt']['receipt_id'] for s in action['steps'] if s.get('receipt')]


def _event(db, item_id, revision, payload):
    db.execute('INSERT INTO work_events VALUES (?,?,?)', (item_id, revision, json.dumps(payload, default=str)))


def _save_requests(db, action):
    for s in action['steps']:
        r = s.get('receipt')
        if r and not r.get('duplicate_submission'):
            req = {'request_id': s['request_id'], 'step': s['step'], 'attempt': s['attempt'], 'counterparty_id': s['counterparty_id']}
            db.execute('INSERT OR IGNORE INTO work_requests VALUES (?,?,?)', (s['request_id'], digest(req), json.dumps(r)))


def _strip(action):
    """Receipts in a stored action never carry the duplicate flag (that is a property of one submission, not the receipt)."""
    for s in action['steps']:
        if s.get('receipt'):
            s['receipt'] = {k: v for k, v in s['receipt'].items() if k != 'duplicate_submission'}
    return action


def run(ws_dir, plan, *, workspace, semantic_path=None, catalog=None, through=None, actor='agere-plan', question=None):
    """Make a routed plan durable and drive its work. -> {'decision_id', 'replay', 'items', 'summary', 'completion', ...}."""
    cat = catalog or catmod.load()
    kind, as_of = plan['program_kind'], plan['as_of']
    lane = f'plan:{kind}'
    with _lock(ws_dir):
        db = database(ws_dir)
        try:
            db.execute('BEGIN IMMEDIATE')
            dec = {'schema': SCHEMA, 'workspace': workspace, 'program_kind': kind, 'as_of': as_of,
                   'catalog_version': plan.get('catalog_version'), 'cohort_source': (plan.get('cohort') or {}).get('source'),
                   'question': question, 'patients': sorted(r['patient_id'] for r in plan['patients']),
                   'routing_digest': router.digest(plan), 'routes': plan['summary']['by_route'], 'lane': lane,
                   'members_digest': digest([[r['patient_id'], essentials(r) if actmod.actionable(r) else
                                              [x.get('reason') for x in r.get('exclusions') or []]] for r in plan['patients']])}
            dec['id'] = digest(dec)
            replay = db.execute('SELECT 1 FROM question_decisions WHERE id=?', (dec['id'],)).fetchone() is not None
            if not replay:
                db.execute('INSERT INTO question_decisions VALUES (?,?)', (dec['id'], json.dumps({**dec, 'recorded_by': actor,
                                                                                                    'limits': LIMITS})))
            acting = [r for r in plan['patients'] if actmod.actionable(r)]
            extra = _acute_facts(semantic_path, [r['patient_id'] for r in acting], as_of)
            counts = Counter()
            item_ids = []
            for r in acting:
                pid = r['patient_id']
                ess = essentials(r)
                assessment = {'schema': SCHEMA, 'patient_id': pid, 'lane': lane, 'essentials': ess,
                              'catalog_version': plan.get('catalog_version'), 'clinical_eligibility_established': False}
                assessment['id'] = digest(assessment)
                db.execute('INSERT OR IGNORE INTO work_assessments VALUES (?,?,?,?)',
                           (assessment['id'], pid, lane, json.dumps({**assessment, 'row': {k: r.get(k) for k in ('gap', 'coverage', 'attribution', 'task', 'decision')}}, default=str)))
                prog, slot = actmod.program_for(cat, plan, r)
                facts = actmod.patient_facts(r, extra.get(pid))
                old = db.execute('SELECT id, revision, payload FROM work_items WHERE patient=? AND program=?', (pid, lane)).fetchone()
                if old is None:
                    action = _strip(actmod.run_patient(r, prog, slot, workspace=workspace, as_of=as_of, cat=cat, facts=facts, through=through))
                    item_id = 'WI-' + digest([SCHEMA, workspace, pid, lane])[:16]
                    payload = {'id': item_id, 'patient_id': pid, 'lane': lane, 'program_kind': kind, 'revision': 1,
                               'decision_id': dec['id'], 'assessment_id': assessment['id'], 'cycle_as_of': as_of, 'cycle': 1,
                               'through': through, 'state': action['state'], 'action': action, 'closure': None,
                               'task': r.get('task'), 'external_dispatch_performed': False, 'synthetic': True}
                    db.execute('INSERT INTO work_items VALUES (?,?,?,?,?)', (item_id, pid, lane, 1, json.dumps(payload, default=str)))
                    _event(db, item_id, 1, {'action': 'created', 'actor': actor, 'decision_id': dec['id'], 'assessment_id': assessment['id'],
                                            'state': action['state'], 'receipts': _receipt_ids(action)})
                    _save_requests(db, action)
                    counts['created'] += 1
                else:
                    item_id, rev, payload = old[0], old[1], json.loads(old[2])
                    if payload['assessment_id'] == assessment['id'] and payload['state'] != 'cancelled':
                        eff = _later(payload.get('through'), through)
                        ledger = _load_ledger(db, payload['action']['action_id'])
                        action = _strip(actmod.run_patient(r, prog, slot, workspace=workspace, as_of=payload['cycle_as_of'], cat=cat,
                                                           facts=facts, through=eff, ledger=ledger))
                        if _receipt_ids(action) == _receipt_ids(payload['action']):
                            counts['unchanged'] += 1
                        else:
                            new = [x for x in _receipt_ids(action) if x not in set(_receipt_ids(payload['action']))]
                            payload.update(revision=rev + 1, through=eff, state=action['state'], action=action, decision_id=dec['id'])
                            db.execute('UPDATE work_items SET revision=?, payload=? WHERE id=?', (rev + 1, json.dumps(payload, default=str), item_id))
                            _event(db, item_id, rev + 1, {'action': 'receipts_received', 'actor': actor, 'receipts': new, 'state': action['state']})
                            _save_requests(db, action)
                            counts['updated'] += 1
                    else:
                        before = json.loads(db.execute('SELECT payload FROM work_assessments WHERE id=?', (payload['assessment_id'],)).fetchone()[0])
                        changes = {k: {'from': before['essentials'].get(k), 'to': ess.get(k)} for k in ess
                                   if before['essentials'].get(k) != ess.get(k)}
                        action = _strip(actmod.run_patient(r, prog, slot, workspace=workspace, as_of=as_of, cat=cat, facts=facts, through=through))
                        prior = {k: payload.get(k) for k in ('cycle', 'cycle_as_of', 'assessment_id', 'state', 'closure')}
                        payload.update(revision=rev + 1, decision_id=dec['id'], assessment_id=assessment['id'], cycle_as_of=as_of,
                                       cycle=payload.get('cycle', 1) + 1, through=through, state=action['state'], action=action,
                                       closure=None, task=r.get('task'))
                        db.execute('UPDATE work_items SET revision=?, payload=? WHERE id=?', (rev + 1, json.dumps(payload, default=str), item_id))
                        _event(db, item_id, rev + 1, {'action': 'reassessed', 'actor': actor, 'changes': changes, 'prior_cycle': prior,
                                                      'state': action['state'], 'receipts': _receipt_ids(action)})
                        _save_requests(db, action)
                        _supersede_followups(db, pid, lane, actor, keep={f['followup_id'] for f in action['followups']})
                        counts['reassessed'] += 1
                _upsert_followups(db, pid, lane, item_id, action, dec['id'], actor, counts)
                db.execute('INSERT OR IGNORE INTO question_decision_tasks VALUES (?,?,?)',
                           (dec['id'], item_id, json.dumps({'assessment_id': assessment['id'], 'patient_id': pid})))
                item_ids.append(item_id)
            for r in plan['patients']:
                if r['route'] != 'exclude':
                    continue
                old = db.execute('SELECT id, revision, payload FROM work_items WHERE patient=? AND program=?', (r['patient_id'], lane)).fetchone()
                if old and json.loads(old[2])['state'] != 'cancelled':
                    payload = json.loads(old[2])
                    reason = (r.get('exclusions') or [{}])[0].get('reason')
                    payload.update(revision=old[1] + 1, state='cancelled', cancelled_reason=f'excluded on reassessment: {reason}')
                    db.execute('UPDATE work_items SET revision=?, payload=? WHERE id=?', (old[1] + 1, json.dumps(payload, default=str), old[0]))
                    _event(db, old[0], old[1] + 1, {'action': 'cancelled', 'actor': actor, 'reason': payload['cancelled_reason']})
                    _supersede_followups(db, r['patient_id'], lane, actor, keep=set())
                    counts['cancelled'] += 1
            closures = _evaluate(db, item_ids, semantic_path, cat, actor, counts)
            db.execute('COMMIT')
        except BaseException:
            db.execute('ROLLBACK')
            raise
        finally:
            db.close()
    actions = [p['action'] for p in _payloads(ws_dir, item_ids)]
    return {'schema': SCHEMA, 'decision_id': dec['id'], 'replay': replay, 'store': str(store_path(ws_dir) / 'program-work.sqlite3'),
            'lane': lane, 'items': dict(counts), 'work_items': len(item_ids), 'summary': actmod.summarize(actions),
            'completion': completion.rates(actions, closures), 'limits': LIMITS, 'external_dispatch_performed': False}


def _upsert_followups(db, pid, lane, parent_id, action, decision_id, actor, counts):
    for f in action['followups']:
        prog = f"{lane}:followup:{f['type']}"
        row = db.execute('SELECT id, revision, payload FROM work_items WHERE patient=? AND program=?', (pid, prog)).fetchone()
        payload = {**f, 'id': None, 'patient_id': pid, 'lane': prog, 'parent_item': parent_id, 'parent_action': action['action_id'],
                   'decision_id': decision_id, 'program_id': action['program_id'], 'external_dispatch_performed': False}
        if row is None:
            fid = 'WF-' + digest([SCHEMA, pid, prog])[:16]
            payload.update(id=fid, revision=1)
            db.execute('INSERT INTO work_items VALUES (?,?,?,?,?)', (fid, pid, prog, 1, json.dumps(payload, default=str)))
            _event(db, fid, 1, {'action': 'created', 'actor': actor, 'trigger': f['trigger'], 'from_receipt': f.get('from_receipt')})
            counts['followups_created'] += 1
        else:
            old = json.loads(row[2])
            if old.get('followup_id') == f['followup_id'] and old.get('state') == f['state'] and old.get('superseded') is None:
                continue
            payload.update(id=row[0], revision=row[1] + 1)
            db.execute('UPDATE work_items SET revision=?, payload=? WHERE id=?', (row[1] + 1, json.dumps(payload, default=str), row[0]))
            _event(db, row[0], row[1] + 1, {'action': 'followup_updated', 'actor': actor, 'trigger': f['trigger'], 'state': f['state']})
            counts['followups_updated'] += 1


def _supersede_followups(db, pid, lane, actor, keep):
    for fid, rev, raw in db.execute('SELECT id, revision, payload FROM work_items WHERE patient=? AND program LIKE ?',
                                    (pid, lane + ':followup:%')).fetchall():
        p = json.loads(raw)
        if p.get('followup_id') in keep or p.get('state') in ('superseded', 'resolved'):
            continue
        p.update(revision=rev + 1, state='superseded', superseded=True)
        db.execute('UPDATE work_items SET revision=?, payload=? WHERE id=?', (rev + 1, json.dumps(p, default=str), fid))
        _event(db, fid, rev + 1, {'action': 'superseded', 'actor': actor, 'reason': 'parent item reassessed or cancelled'})


def _evaluate(db, item_ids, semantic_path, cat, actor, counts):
    rows = {}
    for iid in item_ids:
        rid, rev, raw = db.execute('SELECT id, revision, payload FROM work_items WHERE id=?', (iid,)).fetchone()
        rows[iid] = (rev, json.loads(raw))
    if semantic_path is None or not Path(semantic_path).is_file():
        return {}
    closures = completion.evaluate(semantic_path, [p['action'] for _, p in rows.values() if p['state'] != 'cancelled'], catalog=cat)
    for iid, (rev, p) in rows.items():
        c = closures.get(p['action']['action_id'])
        if c is None:
            continue
        prev = p.get('closure') or {}
        if prev.get('status') == 'closed' and c['status'] != 'closed':
            c = {**c, 'status': 'reopened', 'detail': f"was closed ({prev.get('detail')}); now: {c.get('detail')}"}
            closures[p['action']['action_id']] = c
        if {k: prev.get(k) for k in ('status', 'source', 'evidence')} == {k: c.get(k) for k in ('status', 'source', 'evidence')}:
            continue
        p.update(revision=rev + 1, closure=c)
        p['action']['closed_loop'] = {**(p['action'].get('closed_loop') or {}), 'status': c['status']}
        db.execute('UPDATE work_items SET revision=?, payload=? WHERE id=?', (rev + 1, json.dumps(p, default=str), iid))
        _event(db, iid, rev + 1, {'action': 'closure_evaluated', 'actor': actor, 'from': prev.get('status'), 'to': c['status'],
                                  'source': c['source'], 'evidence': c['evidence'], 'detail': c.get('detail')})
        counts['closure_' + c['status']] += 1
    return closures


# --- reads -------------------------------------------------------------------------------------------------------------------

def _ro(ws_dir):
    p = store_path(ws_dir) / 'program-work.sqlite3'
    if not p.is_file():
        return None
    return sqlite3.connect(f'file:{p}?mode=ro', uri=True)


def _payloads(ws_dir, ids):
    db = _ro(ws_dir)
    if db is None:
        return []
    try:
        out = []
        for i in range(0, len(ids), 500):
            ch = ids[i:i + 500]
            out += [json.loads(r[0]) for r in db.execute(f"SELECT payload FROM work_items WHERE id IN ({','.join('?' * len(ch))})", ch)]
        order = {k: n for n, k in enumerate(ids)}
        return sorted(out, key=lambda p: order.get(p['id'], 0))
    finally:
        db.close()


def _compact(p):
    if ':followup:' in p['lane']:
        return {'id': p['id'], 'patient_id': p['patient_id'], 'lane': p['lane'], 'kind': 'followup', 'type': p.get('type'),
                'state': p.get('state'), 'owner': p.get('owner'), 'due': p.get('due'), 'trigger': p.get('trigger'),
                'reason': p.get('reason'), 'parent_item': p.get('parent_item'), 'revision': p['revision']}
    a = p['action']
    last = next((s for s in reversed(a['steps']) if s.get('receipt')), None)
    return {'id': p['id'], 'patient_id': p['patient_id'], 'lane': p['lane'], 'kind': 'plan_item', 'program_id': a['program_id'],
            'vendor_slot': a.get('vendor_slot'), 'state': p['state'], 'closure': (p.get('closure') or {}).get('status'),
            'last_receipt': {k: last['receipt'][k] for k in ('outcome', 'at', 'counterparty_id', 'reason')} if last else None,
            'followups': [f['type'] for f in a['followups']], 'barriers': len(a.get('barriers') or []),
            'cycle': p.get('cycle'), 'revision': p['revision'], 'decision_id': p['decision_id']}


def items(ws_dir, *, lane=None, state=None, kind=None, patient_id=None, limit=500):
    """Compact current work items (plan items and follow-ups)."""
    db = _ro(ws_dir)
    if db is None:
        return {'items': [], 'counts': {}}
    try:
        q, args = 'SELECT payload FROM work_items WHERE program LIKE ?', ['plan:%']
        if lane:
            q += ' AND (program = ? OR program LIKE ?)'
            args += [lane, lane + ':followup:%']
        if patient_id:
            q += ' AND patient = ?'
            args.append(patient_id)
        rows = [_compact(json.loads(r[0])) for r in db.execute(q + ' ORDER BY program, patient', args)]
    finally:
        db.close()
    if state:
        rows = [r for r in rows if r['state'] == state]
    if kind:
        rows = [r for r in rows if r['kind'] == kind]
    counts = {'plan_items': dict(Counter(r['state'] for r in rows if r['kind'] == 'plan_item')),
              'followups': dict(Counter(f"{r['type']}:{r['state']}" for r in rows if r['kind'] == 'followup'))}
    return {'items': rows[:max(1, min(int(limit), 5000))], 'total': len(rows), 'counts': counts}


def item(ws_dir, item_id):
    """One work item with its step history (receipts), follow-ups and event history."""
    db = _ro(ws_dir)
    if db is None:
        raise FileNotFoundError('No action store in this workspace yet; run a plan first')
    try:
        row = db.execute('SELECT payload FROM work_items WHERE id=?', (str(item_id),)).fetchone()
        if row is None:
            raise FileNotFoundError(f'No work item {item_id!r}')
        p = json.loads(row[0])
        events = [json.loads(r[0]) | {'revision': r[1]} for r in db.execute('SELECT payload, revision FROM work_events WHERE item=? ORDER BY revision', (p['id'],))]
        fus = [json.loads(r[0]) for r in db.execute('SELECT payload FROM work_items WHERE patient=? AND program LIKE ?',
                                                     (p['patient_id'], p['lane'].split(':followup:')[0] + ':followup:%'))]
    finally:
        db.close()
    return {'item': p, 'events': events, 'followup_items': [_compact(f) for f in fus]}


def decisions(ws_dir):
    db = _ro(ws_dir)
    if db is None:
        return []
    try:
        out = []
        for raw, n in db.execute('SELECT d.payload, (SELECT COUNT(*) FROM question_decision_tasks t WHERE t.decision = d.id) '
                                 'FROM question_decisions d ORDER BY d.rowid DESC'):
            d = json.loads(raw)
            out.append({k: d.get(k) for k in ('id', 'program_kind', 'as_of', 'lane', 'question', 'routes', 'cohort_source', 'catalog_version')}
                       | {'patients': len(d.get('patients') or []), 'work_items': n})
        return out
    finally:
        db.close()


def decision(ws_dir, decision_id):
    """A saved plan decision with the current state of its work: funnel, outcomes, closure rates."""
    db = _ro(ws_dir)
    if db is None:
        raise FileNotFoundError('No action store in this workspace yet; run a plan first')
    try:
        row = db.execute('SELECT payload FROM question_decisions WHERE id=?', (str(decision_id),)).fetchone()
        if row is None:
            raise FileNotFoundError(f'No plan decision {decision_id!r}')
        ids = [r[0] for r in db.execute('SELECT task FROM question_decision_tasks WHERE decision=? ORDER BY rowid', (decision_id,))]
    finally:
        db.close()
    d = json.loads(row[0])
    payloads = [p for p in _payloads(ws_dir, ids) if p['state'] != 'cancelled']
    actions = [p['action'] for p in payloads]
    closures = {p['action']['action_id']: p['closure'] for p in payloads if p.get('closure')}
    return {'decision': {k: v for k, v in d.items() if k != 'patients'} | {'patients': len(d.get('patients') or [])},
            'summary': actmod.summarize(actions), 'completion': completion.rates(actions, closures)}
