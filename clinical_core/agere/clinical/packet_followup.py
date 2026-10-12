"""Authenticated internal follow-through to existing, explicit evidence contexts.

An acquired context is work output, never resolution of a clinical requirement or
proof of care delivery. Source and review changes remain visible in history.
"""
from pathlib import Path
import hashlib
import json
from ...contracts import digest
from . import internal_execution as execution, care_readiness, evidence_dispositions as dispositions, record_context


def identity():
    return digest(['packet-evidence-followup/1', hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), dispositions.identity()])


def database(path):
    db = execution.database(path)
    db.executescript('''
      CREATE TABLE IF NOT EXISTS followups(id TEXT PRIMARY KEY,task TEXT,payload TEXT);
      CREATE TRIGGER IF NOT EXISTS immutable_followup_update BEFORE UPDATE ON followups BEGIN SELECT RAISE(ABORT,'Follow-up history is immutable'); END;
      CREATE TRIGGER IF NOT EXISTS immutable_followup_delete BEFORE DELETE ON followups BEGIN SELECT RAISE(ABORT,'Follow-up history is immutable'); END;
    ''')
    return db


def targets(path, task_id):
    report = care_readiness.snapshot(path, task_id)
    return [{'target': r['target']['target'], 'question': r['target']['question'], 'state': r['state'],
             'route': route['name']} for route in report['routes'] for r in route['reviews'] if not r['documentary_question_addressed']]


def prepare(path, request, bearer):
    execution.care.exact(request, 'run_id review_revision target record_ids note request_id')
    execution.care.text(request['request_id'], 'request ID', 120); execution.care.text(request['note'], 'follow-up note', 2000)
    if type(request['review_revision']) is not int or request['review_revision'] < 1: raise ValueError('Exact recorded packet review revision required')
    auth = database(path)
    try:
        with auth:
            auth.execute('BEGIN IMMEDIATE')
            principal = execution.authenticate(auth, bearer, 'packet_reviewer')
            row = auth.execute('SELECT payload FROM runs WHERE id=?', (request['run_id'],)).fetchone()
            if not row: raise ValueError('Unknown packet completion')
            run = json.loads(row[0]); task = care_readiness.task(path, run['task_id']); execution.authorize(principal, task)
            key = digest(['packet_followup', request, principal['id']]); prior = execution.replay(auth, request['request_id'], key)
            if prior: return prior
            evidence = execution.binding(path, task['id'])[2]
            if evidence != run['binding']: raise execution.care.work.WorkConflict('Packet completion is stale; refresh before follow-up')
            row = auth.execute('SELECT payload FROM reviews WHERE run=? ORDER BY revision DESC LIMIT 1', (run['id'],)).fetchone()
            review = json.loads(row[0]) if row else None
            if not review or review['revision'] != request['review_revision']:
                raise execution.care.work.WorkConflict('Packet review changed or is missing; reload before follow-up')
            allowed = targets(path, task['id'])
            target = next((t for t in allowed if t['target'] == request['target']), None)
            if target is None: raise ValueError('Select an unresolved requirement belonging to this exact task')
            acquisition = dispositions.acquisition_context(path, {'target': request['target'], 'record_ids': request['record_ids']})
            if execution.binding(path, task['id'])[2] != evidence:
                raise execution.care.work.WorkConflict('Evidence changed during follow-up; reload before recording')
            result = {'schema': 'packet-evidence-followup/1', 'task_id': task['id'], 'run_id': run['id'],
                'review_id': review['id'], 'review_revision': review['revision'], 'binding_id': evidence['id'],
                'principal': principal, 'recorded_at': execution.now(), 'note': request['note'],
                'request': request, 'target': target, 'acquisition': acquisition, 'implementation': identity(),
                'status': 'evidence_context_prepared', 'requirement_resolved': False, 'care_delivered': False,
                'new_paid_api_calls': 0, **execution.LIMITS}
            result['id'] = digest(result)
            auth.execute('INSERT INTO followups VALUES (?,?,?)', (result['id'], task['id'], json.dumps(result)))
            return execution.remember(auth, request['request_id'], key, result)
    finally: auth.close()


def inspect(path, task_id):
    state = execution.inspect(path, task_id); db = database(path)
    try: rows = [json.loads(r[0]) for r in db.execute('SELECT payload FROM followups WHERE task=? ORDER BY rowid', (task_id,))]
    finally: db.close()
    current = next((r for r in state['runs'] if r['id'] == state['current_run_id']), None)
    latest = current['reviews'][-1] if current and current['reviews'] else None
    for row in rows:
        reasons = []
        if not latest or row['review_id'] != latest['id']: reasons.append('Packet or internal review changed; follow-up belongs to an earlier review')
        if row['implementation'] != identity(): reasons.append('Follow-up interpretation contract changed')
        try:
            context = record_context.inspect(path, row['acquisition']['context_id'])
            if context['patient_id'] != row['acquisition']['target']['patient_id']: raise ValueError('Context patient mismatch')
            if dispositions.resolve_target(path, row['request']['target']) != row['acquisition']['target']:
                reasons.append('Original evidence requirement changed')
        except (ValueError, KeyError, FileNotFoundError) as exc: reasons.append(str(exc))
        row['current'] = not reasons; row['stale_reasons'] = reasons
    return {'schema': 'packet-evidence-followup/1', 'task_id': task_id, 'followups': rows,
            'current_run_id': state['current_run_id'], 'current_review_revision': latest['revision'] if latest else None,
            'available_targets': targets(path, task_id) if current else [],
            'prepare_available': bool(latest), 'requirement_resolved': False,
            'scope': 'Internal evidence acquisition linked to a packet review. Source presence does not resolve meaning, verify outcomes or close the review.', **execution.LIMITS}
