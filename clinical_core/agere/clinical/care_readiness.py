"""Attach documentary prerequisite reviews to existing capability work receipts.

Version-one matching and task identities stay unchanged. Readiness adds a separate
versioned attachment; it grants no clinical/external execution authority.
"""
from collections import Counter
from pathlib import Path
import hashlib
import json
import sqlite3

from ...contracts import digest
from . import care_capabilities as care, evidence_dispositions as decisions

VERSION = 'care-documentary-readiness/1'


def identity():
    return digest([VERSION, hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), decisions.identity()])


def requirements(plan, option, route):
    result = []
    for question in route['requirements']:
        kind = ('buyer_prerequisite' if question in option['capability']['prerequisites'] else
                'public_pathway_prerequisite' if route['pathway'] and question in route['pathway']['prerequisites'] else
                'unverified_action_prerequisite')
        key = digest([VERSION, route['pathway_id'], kind, question])
        target = {'kind': 'service_requirement', 'plan_id': plan['id'], 'option_id': option['id'],
                  'pathway_id': route['pathway_id'], 'requirement_id': key}
        result.append({'target': target, 'id': digest(target), 'parent_hash': digest(plan),
                       'patient_id': plan['patient_id'], 'question': question, 'source_selectors': [],
                       'as_of': plan['coverage']['as_of'], 'authority': kind,
                       'pathway_source': route['pathway']['source_url'] if route['pathway'] else None})
    return result


def resolve_requirement(path, target):
    care.exact(target, 'kind plan_id option_id pathway_id requirement_id')
    if target['kind'] != 'service_requirement': raise ValueError('Service requirement target required')
    plan = care.inspect_plan(path, target['plan_id'])
    for option in plan['options']:
        if option['id'] != target['option_id']: continue
        for route in option['routes']:
            if route['pathway_id'] != target['pathway_id']: continue
            for item in requirements(plan, option, route):
                if item['target'] == target: return item
    raise ValueError('Requirement not present in this exact matching plan')


def task(path, key):
    db = care.db_open(path)
    try: return care.load(db, 'capability_tasks', care.text(key, 'task ID', 64))
    finally: db.close()


def storage(path):
    db = sqlite3.connect(Path(path)/'evidence-dispositions.sqlite3', timeout=30)
    db.executescript('''
      CREATE TABLE IF NOT EXISTS readiness_receipts(id TEXT PRIMARY KEY,payload TEXT);
      CREATE TABLE IF NOT EXISTS readiness_handoffs(task TEXT,revision INTEGER,receipt TEXT,payload TEXT,PRIMARY KEY(task,revision));
      CREATE TRIGGER IF NOT EXISTS immutable_readiness_update BEFORE UPDATE ON readiness_receipts BEGIN SELECT RAISE(ABORT,'Readiness receipts are immutable'); END;
      CREATE TRIGGER IF NOT EXISTS immutable_readiness_delete BEFORE DELETE ON readiness_receipts BEGIN SELECT RAISE(ABORT,'Readiness receipts are immutable'); END;
      CREATE TRIGGER IF NOT EXISTS immutable_handoff_update BEFORE UPDATE ON readiness_handoffs BEGIN SELECT RAISE(ABORT,'Readiness attachments are immutable'); END;
      CREATE TRIGGER IF NOT EXISTS immutable_handoff_delete BEFORE DELETE ON readiness_handoffs BEGIN SELECT RAISE(ABORT,'Readiness attachments are immutable'); END;
    ''')
    return db


def snapshot(path, key):
    item = task(path, key); plan = care.inspect_plan(path, item['plan_id'])
    option = next(o for o in plan['options'] if o['id'] == item['option_id'])
    routes = []
    for route in option['routes']:
        reviews = [decisions.inspect(path, t['target'], resolved=t) for t in requirements(plan, option, route)]
        counts = dict(Counter(r['state'] for r in reviews))
        status = ('route_blocked' if route['blocked_reasons'] else
                  'documentary_dispositions_recorded' if reviews and all(r['documentary_question_addressed'] for r in reviews) else
                  'documentary_review_incomplete')
        routes.append({'pathway_id': route['pathway_id'], 'name': route['pathway']['name'] if route['pathway'] else 'Buyer-specific service funding',
                       'status': status, 'blocked_reasons': route['blocked_reasons'], 'reviews': reviews, 'counts': counts})
    report = {'schema': VERSION, 'task_id': key, 'patient_id': item['patient_id'], 'plan_id': plan['id'],
              'option_id': option['id'], 'owner': item['owner'], 'routes': routes,
              'code_identity': identity(), 'source_plan_hash': digest(plan),
              'clinical_execution_authorized': False, 'verified_benefits': False, 'human_identity_authenticated': False,
              'scope': 'Documentary review state only. Self-reported dispositions do not establish clinical appropriateness, plan entitlement, actual consent or authorized clinical execution.'}
    report['id'] = digest(report)
    db = storage(path)
    try:
        with db: db.execute('INSERT OR IGNORE INTO readiness_receipts VALUES (?,?)', (report['id'], json.dumps(report)))
        row = db.execute('SELECT payload FROM readiness_handoffs WHERE task=? ORDER BY revision DESC LIMIT 1', (key,)).fetchone()
    finally: db.close()
    attachment = json.loads(row[0]) if row else None
    return {**report, 'latest_handoff_attachment': attachment,
            'handoff_readiness_current': bool(attachment and attachment['readiness_id'] == report['id'] and attachment['task_revision'] == item['revision'])}


def task_event(path, request):
    if request.get('action') != 'prepare_handoff': return care.task_event(path, request)
    before = snapshot(path, request.get('task_id'))
    result = care.task_event(path, request)
    after = snapshot(path, request['task_id'])
    if before['id'] != after['id']:
        raise care.work.WorkConflict('Review changed during handoff preparation; refresh the internal packet')
    attachment = {'task_id': request['task_id'], 'task_revision': result['task']['revision'],
                  'readiness_id': after['id'], 'documentary_status': [r['status'] for r in after['routes']],
                  'clinical_execution_authorized': False, 'external_actions_executed': 0}
    db = storage(path)
    try:
        with db:
            old = db.execute('SELECT payload FROM readiness_handoffs WHERE task=? AND revision=?', (attachment['task_id'], attachment['task_revision'])).fetchone()
            if old and json.loads(old[0]) != attachment:
                raise care.work.WorkConflict('Existing handoff revision has different readiness; issue a new internal preparation request')
            db.execute('INSERT OR IGNORE INTO readiness_handoffs VALUES (?,?,?,?)',
                       (attachment['task_id'], attachment['task_revision'], attachment['readiness_id'], json.dumps(attachment)))
    finally: db.close()
    return {**result, 'readiness_attachment': attachment}
