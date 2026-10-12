"""Immutable saved-answer attachments to a declared, grouped source plan.

Completion here describes accounted source units, never clinical correctness.
Explicit answer selections form immutable snapshots; no latest-wins promotion.
"""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import re
import sqlite3
from ...contracts import digest
from . import retrieval_source_plan as P, source_synthesis as S

VERSION = 'source-plan-answers/1'


def identity():
    return digest([VERSION, hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])


def validate(body, fields):
    if not isinstance(body, dict) or set(body) != set(fields):
        raise ValueError('Exact source-plan answer fields required')
    for key in fields:
        if key != 'attachment_ids' and (not isinstance(body[key], str) or not re.fullmatch('[a-f0-9]{64}', body[key])):
            raise ValueError('Exact receipt IDs required')
    return body


@contextmanager
def database(path):
    db = sqlite3.connect(Path(path)/'source-plan-answers.sqlite3')
    db.executescript('''
      CREATE TABLE IF NOT EXISTS attachments(id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, payload TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS snapshots(id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, payload TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS plan_attachments ON attachments(plan_id);
      CREATE INDEX IF NOT EXISTS plan_snapshots ON snapshots(plan_id);
      CREATE TRIGGER IF NOT EXISTS attachment_update BEFORE UPDATE ON attachments BEGIN SELECT RAISE(ABORT,'Immutable answer attachment'); END;
      CREATE TRIGGER IF NOT EXISTS attachment_delete BEFORE DELETE ON attachments BEGIN SELECT RAISE(ABORT,'Immutable answer attachment'); END;
      CREATE TRIGGER IF NOT EXISTS snapshot_update BEFORE UPDATE ON snapshots BEGIN SELECT RAISE(ABORT,'Immutable answer snapshot'); END;
      CREATE TRIGGER IF NOT EXISTS snapshot_delete BEFORE DELETE ON snapshots BEGIN SELECT RAISE(ABORT,'Immutable answer snapshot'); END;
    ''')
    try:
        with db: yield db
    finally: db.close()


def seal(row):
    return {**row, 'id': digest(row)}


def load(db, table, key):
    row = db.execute('SELECT payload FROM '+table+' WHERE id=?', (key,)).fetchone()
    if not row: raise ValueError('Unknown saved answer attachment or snapshot')
    value = json.loads(row[0])
    if value.get('id') != key or seal({k:v for k,v in value.items() if k != 'id'}) != value or value.get('code_identity') != identity():
        raise ValueError('Saved answer attachment identity changed')
    return value


def checked(plan_result, path, answer_id):
    """Read the server's immutable answer; never accept a caller's answer payload."""
    plan = plan_result['plan']; answer = S.inspect(path, answer_id)
    groups = {g['context_id']: g for g in plan['groups']}
    cid = answer.get('context_id'); group = groups.get(cid)
    if not group: raise ValueError('Answer belongs to another source context')
    context = next(c for c in plan_result['contexts'] if c['id'] == cid)
    if (answer.get('id') != answer_id or answer.get('status') != 'complete'
            or answer.get('patient_id') != plan['preview']['patient_id']
            or answer.get('question') != plan['preview']['review_question']
            or answer.get('context_digest') != group['context_hash']
            or answer.get('record_scope') != context['record_scope']):
        raise ValueError('Saved answer patient, question or source scope differs from the plan')
    expected = [u['id'] for u in context['model_context']['evidence']]
    reviews = [u for p in answer.get('passes', []) for u in p['result']['unit_reviews']]
    actual = [u['source_unit_id'] for u in reviews]
    coverage = answer.get('coverage', {})
    if (len(expected) != len(set(expected)) or sorted(actual) != sorted(expected)
            or coverage.get('complete_declared_scope') is not True
            or any(coverage.get(k) != len(expected) for k in ('received_units', 'submitted_units', 'model_accounted_units'))):
        raise ValueError('Saved answer does not account for every group source unit exactly once')
    return answer, group, context


def attachment(result, path, answer_id):
    answer, group, context = checked(result, path, answer_id)
    return seal({'schema': VERSION, 'kind': 'group_answer', 'code_identity': identity(),
        'plan_id': result['plan']['id'], 'plan_digest': digest(result['plan']),
        'context_id': context['id'], 'context_digest': digest(context), 'group_number': group['group_number'],
        'answer_id': answer_id, 'answer_digest': digest(answer), 'record_ids': group['record_ids'],
        'source_units': len(context['model_context']['evidence']),
        'model_unresolved_units': len(answer['coverage']['unassessed_units']),
        'within_group_relations_assessed': answer['coverage']['cross_group_relations_assessed'],
        'clinical_validated': False, 'clinical_execution_authorized': False})


def attach(retriever, body):
    validate(body, ('plan_id', 'answer_id'))
    result = P.inspect(retriever, {'id': body['plan_id']})
    row = attachment(result, retriever.path, body['answer_id'])
    with database(retriever.path) as db:
        old = db.execute('SELECT id FROM attachments WHERE id=?', (row['id'],)).fetchone()
        if old and load(db, 'attachments', row['id']) != row: raise ValueError('Stored attachment changed')
        if not old: db.execute('INSERT INTO attachments VALUES (?,?,?)', (row['id'], body['plan_id'], json.dumps(row)))
    return {'attachment': row, 'cache_hit': bool(old), 'new_paid_api_calls': 0}


def verified_attachments(retriever, result, rows):
    for row in rows:
        if row != attachment(result, retriever.path, row['answer_id']):
            raise ValueError('Attached answer or source dependencies changed')
    return rows


def listing(retriever, body):
    validate(body, ('plan_id',)); result = P.inspect(retriever, {'id': body['plan_id']})
    with database(retriever.path) as db:
        rows = [load(db, 'attachments', r[0]) for r in db.execute('SELECT id FROM attachments WHERE plan_id=? ORDER BY id', (body['plan_id'],))]
        snapshots = [load(db, 'snapshots', r[0]) for r in db.execute('SELECT id FROM snapshots WHERE plan_id=? ORDER BY id', (body['plan_id'],))]
    verified_attachments(retriever, result, rows)
    groups = []
    for g in result['plan']['groups']:
        options = [r for r in rows if r['context_id'] == g['context_id']]
        groups.append({'group_number': g['group_number'], 'context_id': g['context_id'],
            'record_ids': g['record_ids'], 'attachments': options,
            'status': 'answer_selection_required' if len(options)>1 else 'answer_attached' if options else 'missing_answer'})
    return {'schema': VERSION, 'plan_id': body['plan_id'], 'groups': groups,
        'answered_groups': sum(bool(g['attachments']) for g in groups), 'total_groups': len(groups),
        'snapshots': [{'id': s['id'], 'attachment_ids': s['attachment_ids']} for s in snapshots],
        'cross_group_interpretation': 'not_assessed', 'clinical_validated': False, 'new_paid_api_calls': 0}


def prepare(retriever, body):
    validate(body, ('plan_id', 'attachment_ids')); ids = body['attachment_ids']
    if (not isinstance(ids, list) or not 1 <= len(ids) <= 32 or any(not isinstance(i,str) or not re.fullmatch('[a-f0-9]{64}',i) for i in ids)
            or len(set(ids)) != len(ids)): raise ValueError('Distinct exact attachment IDs required')
    result = P.inspect(retriever, {'id': body['plan_id']}); plan = result['plan']
    with database(retriever.path) as db: rows = [load(db, 'attachments', k) for k in ids]
    if any(r['plan_id'] != body['plan_id'] for r in rows): raise ValueError('Attachment belongs to another plan')
    verified_attachments(retriever, result, rows)
    if sorted(r['context_id'] for r in rows) != sorted(g['context_id'] for g in plan['groups']):
        raise ValueError('Select exactly one answer for every source-plan group')
    rows.sort(key=lambda r: r['group_number'])
    saved = seal({'schema': VERSION, 'kind': 'answer_snapshot', 'code_identity': identity(),
        'plan_id': body['plan_id'], 'plan_digest': digest(plan), 'patient_id': plan['preview']['patient_id'],
        'original_question': plan['preview']['original_question'], 'analysis_window': plan['preview']['analysis_window'],
        'attachment_ids': [r['id'] for r in rows], 'groups': rows,
        'selected_records': len(plan['preview']['selected_record_ids']), 'excluded_records': plan['preview']['excluded_records'],
        'source_units': sum(r['source_units'] for r in rows), 'model_unresolved_units': sum(r['model_unresolved_units'] for r in rows),
        'cross_group_interpretation': 'not_assessed' if len(rows)>1 else 'not_applicable',
        'status': 'group_answers_attached', 'clinical_validated': False, 'clinical_execution_authorized': False})
    with database(retriever.path) as db:
        old = db.execute('SELECT id FROM snapshots WHERE id=?', (saved['id'],)).fetchone()
        if old and load(db, 'snapshots', saved['id']) != saved: raise ValueError('Stored snapshot changed')
        if not old: db.execute('INSERT INTO snapshots VALUES (?,?,?)', (saved['id'], body['plan_id'], json.dumps(saved)))
    return {**inspect(retriever, {'id': saved['id']}), 'cache_hit': bool(old), 'new_paid_api_calls': 0}


def inspect(retriever, body):
    validate(body, ('id',))
    with database(retriever.path) as db: saved = load(db, 'snapshots', body['id'])
    result = P.inspect(retriever, {'id': saved['plan_id']})
    if digest(result['plan']) != saved['plan_digest']: raise ValueError('Parent source plan changed')
    with database(retriever.path) as db: rows = [load(db, 'attachments', i) for i in saved['attachment_ids']]
    if rows != saved['groups']: raise ValueError('Snapshot attachments changed')
    verified_attachments(retriever, result, rows)
    return {'snapshot': saved, 'answers': [S.inspect(retriever.path, r['answer_id']) for r in rows],
            'plan': result['plan'], 'contexts': result['contexts']}
