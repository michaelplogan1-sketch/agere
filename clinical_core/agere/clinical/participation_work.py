"""Queue and handoff attachments for exact dated participation declarations."""
import json
from . import participation_evidence as evidence, coverage_work, care_capabilities as care, care_readiness


def prepare(path, request):
    plan = coverage_work.prepare(path, request)
    return {**plan, 'participation_contexts': {o['id']: evidence.prepare(path, plan['id'], o['id']) for o in plan['options']}}


def queued(path, task_id):
    db = evidence.database(path)
    try: row = db.execute('SELECT payload FROM queue_contexts WHERE task=? ORDER BY sequence DESC LIMIT 1', (task_id,)).fetchone()
    finally: db.close()
    return json.loads(row[0]) if row else None


def attach_queue(path, task, report, actor):
    actor = care.text(actor, 'queue actor')
    evidence.inspect(path, report['id'])
    row = {'task_id': task['id'], 'plan_id': task['plan_id'], 'option_id': task['option_id'], 'participation_id': report['id'], 'actor': actor}
    db = evidence.database(path)
    try:
        db.execute('BEGIN IMMEDIATE')
        old = db.execute('SELECT payload FROM queue_contexts WHERE task=? ORDER BY sequence DESC LIMIT 1', (task['id'],)).fetchone()
        # Check registry again inside this transaction; the full source/clause check precedes it.
        if evidence.current(db, report['buyer_id']).get('id') != report['declaration_registry_id']:
            raise care.work.WorkConflict('Participation declarations changed while queueing')
        current = care_readiness.task(path, task['id'])
        if current['plan_id'] != task['plan_id'] or current['option_id'] != task['option_id'] or current['state'] == 'cancelled':
            raise care.work.WorkConflict('Task changed while attaching participation context')
        if old and json.loads(old[0])['participation_id'] == report['id']: return json.loads(old[0])
        db.execute('INSERT INTO queue_contexts(task,payload) VALUES (?,?)', (task['id'], json.dumps(row))); db.commit()
        return row
    finally: db.close()


def enqueue(path, request):
    extra = request.get('participation_context_id')
    base = {k: v for k, v in request.items() if k != 'participation_context_id'}
    care.exact(base, 'plan_id option_id actor')
    report = evidence.inspect(path, extra) if extra is not None else evidence.prepare(path, base['plan_id'], base['option_id'])
    if report['plan_id'] != base['plan_id'] or report['option_id'] != base['option_id']:
        raise ValueError('Participation context must belong to this exact service match')
    result = coverage_work.enqueue(path, base)
    binding = attach_queue(path, result['task'], report, base['actor'])
    evidence.inspect(path, report['id'])
    return {**result, 'participation_queue_context': binding}


def task_view(path, key):
    task = care_readiness.task(path, key); binding = queued(path, key)
    report = evidence.prepare(path, task['plan_id'], task['option_id'])
    matches = bool(binding and binding['participation_id'] == report['id'])
    db = evidence.database(path)
    try: row = db.execute('SELECT payload FROM handoffs WHERE task=? ORDER BY revision DESC LIMIT 1', (key,)).fetchone()
    finally: db.close()
    attachment = json.loads(row[0]) if row else None
    return {'task_id': key, 'participation': report, 'queue_context': binding, 'queue_participation_current': matches,
            'latest_handoff_attachment': attachment,
            'handoff_participation_current': bool(matches and attachment and attachment['participation_id'] == report['id'] and attachment['task_revision'] == task['revision']),
            'clinical_execution_authorized': False, 'verified_participation': False}


def task_event(path, request):
    if request.get('action') != 'prepare_handoff': return coverage_work.task_event(path, request)
    care.exact(request, 'task_id expected_revision request_id action actor note')
    task = care_readiness.task(path, request['task_id']); binding = queued(path, task['id'])
    if binding:
        report = evidence.inspect(path, binding['participation_id'])
        if report['plan_id'] != task['plan_id'] or report['option_id'] != task['option_id']:
            raise care.work.WorkConflict('Task match changed; refresh participation in the internal queue')
    else:
        report = evidence.prepare(path, task['plan_id'], task['option_id'])
        binding = attach_queue(path, task, report, request.get('actor'))
    result = coverage_work.task_event(path, request)
    evidence.inspect(path, report['id'])
    if queued(path, task['id'])['participation_id'] != report['id']:
        raise care.work.WorkConflict('Participation queue context changed during preparation; prepare a new internal packet')
    attachment = {'task_id': task['id'], 'task_revision': result['task']['revision'], 'participation_id': report['id'],
                  'declaration_revision': report['declaration_revision'], 'clinical_execution_authorized': False, 'verified_participation': False}
    db = evidence.database(path)
    try:
        with db:
            old = db.execute('SELECT payload FROM handoffs WHERE task=? AND revision=?', (task['id'], attachment['task_revision'])).fetchone()
            if old and json.loads(old[0]) != attachment:
                raise care.work.WorkConflict('Existing handoff has a different participation context; issue a new preparation request')
            db.execute('INSERT OR IGNORE INTO handoffs VALUES (?,?,?)', (task['id'], attachment['task_revision'], json.dumps(attachment)))
    finally: db.close()
    return {**result, 'participation_attachment': attachment}
