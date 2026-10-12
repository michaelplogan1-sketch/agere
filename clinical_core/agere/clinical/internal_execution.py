"""Credential-scoped, local packet work. No clinical/external adapters exist here.

Credential possession is authenticated; a person's identity, license and clinical
authority are not. Provision/revoke are local administration functions, never HTTP
routes. Existing self-reported review engines retain their narrower authority.
"""
from datetime import datetime, timezone, date
from pathlib import Path
import hashlib
import json
import os
import secrets
import sqlite3

from ...contracts import digest
from . import action_work_state, care_capabilities as care, care_readiness

VERSION = 'internal-packet-execution/1'
ADAPTER = 'local_packet_dependency_check'
ROLES = {'packet_worker', 'packet_reviewer'}
LIMITS = {'external_actions_executed': 0, 'clinical_execution_authorized': False,
          'human_identity_verified': False, 'clinical_outcomes_verified': False,
          'service_slots_reserved': 0}


def now(): return datetime.now(timezone.utc).isoformat()


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None: raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError): raise ValueError('Timezone-aware operational timestamp required') from None


def database(path):
    file = Path(path)/'internal-execution.sqlite3'
    fd = os.open(file, os.O_CREAT | os.O_RDWR, 0o600); os.close(fd)
    db = sqlite3.connect(file, timeout=30)
    db.executescript('''
      CREATE TABLE IF NOT EXISTS credentials(id TEXT PRIMARY KEY,token_hash TEXT UNIQUE,payload TEXT);
      CREATE TABLE IF NOT EXISTS revocations(id TEXT PRIMARY KEY,payload TEXT);
      CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,task TEXT,payload TEXT);
      CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY,run TEXT,revision INTEGER,payload TEXT,UNIQUE(run,revision));
      CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,request_hash TEXT,payload TEXT);
    ''')
    for table in ('credentials', 'revocations', 'runs', 'reviews', 'requests'):
        for operation in ('UPDATE', 'DELETE'):
            db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{operation} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'Execution history is immutable'); END")
    return db


def provision(path, request):
    """Trusted local administration only; caller must protect the returned secret."""
    care.exact(request, 'principal_id identity_kind buyer_id service_id roles expires_at')
    if request['identity_kind'] not in ('local_operator', 'synthetic_test'):
        raise ValueError('Explicit local credential identity kind required')
    roles = request['roles']
    if not isinstance(roles, list) or not roles or any(r not in ROLES for r in roles) or len(set(roles)) != len(roles):
        raise ValueError('Explicit packet worker/reviewer roles required')
    for key in ('principal_id', 'buyer_id', 'service_id'): care.text(request[key], key, 150)
    if timestamp(request['expires_at']) <= timestamp(now()): raise ValueError('Credential must expire in the future')
    portfolio = care.portfolio(path, request['buyer_id'])
    if not any(s['id'] == request['service_id'] for s in portfolio['capabilities']):
        raise ValueError('Credential must name an existing buyer service')
    token = secrets.token_urlsafe(32)
    public = {**request, 'id': secrets.token_hex(16), 'issued_at': now(), 'human_identity_verified': False}
    db = database(path)
    try:
        with db: db.execute('INSERT INTO credentials VALUES (?,?,?)', (public['id'], hashlib.sha256(token.encode()).hexdigest(), json.dumps(public)))
    finally: db.close()
    return {'credential': public, 'token': token}


def revoke(path, credential_id, reason):
    care.text(reason, 'revocation reason', 1000); db = database(path)
    try:
        with db:
            if not db.execute('SELECT 1 FROM credentials WHERE id=?', (credential_id,)).fetchone(): raise ValueError('Unknown credential')
            db.execute('INSERT OR IGNORE INTO revocations VALUES (?,?)', (credential_id, json.dumps({'revoked_at': now(), 'reason': reason})))
    finally: db.close()


def authenticate(db, bearer, role):
    if not isinstance(bearer, str) or not bearer.startswith('Bearer ') or not 30 <= len(bearer) <= 200:
        raise PermissionError('A locally provisioned scoped credential is required')
    hashed = hashlib.sha256(bearer[7:].encode()).hexdigest()
    row = db.execute('SELECT payload FROM credentials WHERE token_hash=?', (hashed,)).fetchone()
    if not row: raise PermissionError('Credential is invalid, expired or revoked')
    principal = json.loads(row[0])
    if (timestamp(principal['expires_at']) <= timestamp(now()) or
            db.execute('SELECT 1 FROM revocations WHERE id=?', (principal['id'],)).fetchone()):
        raise PermissionError('Credential is invalid, expired or revoked')
    if role not in principal['roles']: raise PermissionError('Credential does not permit this packet operation')
    return principal


def authorize(principal, task):
    if task['buyer_id'] != principal['buyer_id'] or task['capability_id'] != principal['service_id']:
        raise PermissionError('Credential does not cover this buyer and service')


def identity():
    return digest([VERSION, ADAPTER, hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   hashlib.sha256(Path(action_work_state.__file__).read_bytes()).hexdigest()])


def binding(path, task_id):
    task = care_readiness.task(path, task_id); state = action_work_state.inspect(path, task_id)
    if not state['packet_matches_recorded_dependencies']:
        raise care.work.WorkConflict('Prepare a current evidence packet before running or reviewing internal checks')
    readiness = care_readiness.snapshot(path, task_id)
    if not readiness['handoff_readiness_current'] or care_readiness.task(path, task_id) != task:
        raise care.work.WorkConflict('Evidence changed during the packet check; reload')
    data = {'task_id': task_id, 'task_revision': task['revision'], 'plan_id': task['plan_id'],
            'task_hash': digest(task), 'readiness_id': readiness['id'],
            'work_state_hash': digest(state), 'adapter_identity': identity()}
    return task, state, {**data, 'id': digest(data)}


def replay(db, request_id, key):
    prior = db.execute('SELECT request_hash,payload FROM requests WHERE id=?', (request_id,)).fetchone()
    if not prior: return None
    if prior[0] != key: raise care.work.WorkConflict('Request ID already belongs to different authenticated work')
    return {**json.loads(prior[1]), 'idempotent_replay': True}


def remember(db, request_id, key, result):
    db.execute('INSERT INTO requests VALUES (?,?,?)', (request_id, key, json.dumps(result)))
    return {**result, 'idempotent_replay': False}


def run(path, request, bearer):
    care.exact(request, 'task_id expected_revision request_id')
    care.text(request['request_id'], 'request ID', 120)
    if type(request['expected_revision']) is not int: raise ValueError('Exact task revision required')
    db = database(path)
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            principal = authenticate(db, bearer, 'packet_worker')
            task = care_readiness.task(path, request['task_id']); authorize(principal, task)
            key = digest([request, principal['id'], 'run'])
            prior = replay(db, request['request_id'], key)
            if prior: return prior  # Historical completion only; inspect reports currentness separately.
            if task['revision'] != request['expected_revision']: raise care.work.WorkConflict('Task changed; reload before dispatch')
            task, state, evidence = binding(path, task['id'])
            rid = digest([ADAPTER, evidence['id']])
            existing = db.execute('SELECT payload FROM runs WHERE id=?', (rid,)).fetchone()
            if existing:
                return remember(db, request['request_id'], key, {'run': json.loads(existing[0]), 'deduplicated': True, **LIMITS})
            ack = {'event': 'acknowledged_by_local_adapter', 'at': now(), 'principal': principal,
                   'task_id': task['id'], 'task_revision': task['revision'], 'packet_binding_id': evidence['id']}
            # This adapter executes only local dependency checks. It cannot call a provider or send a message.
            checks = [{'layer': name, 'packet_current': layer['packet_current'], 'state': layer['state'],
                       'reason': layer['reason']} for name, layer in state['layers'].items()]
            if binding(path, task['id'])[2] != evidence:
                raise care.work.WorkConflict('Packet changed during execution; no completion recorded')
            completion = {'event': 'local_packet_checks_completed', 'at': now(), 'checks': checks,
                          'counts_by_route': state['layers']['documentary_review'].get('counts_by_route', {}),
                          'output_hash': digest(checks), 'clinical_requirements_resolved': False}
            receipt = {'schema': VERSION, 'id': rid, 'adapter': ADAPTER, 'task_id': task['id'],
                       'owner': task['owner'], 'binding': evidence, 'acknowledgement': ack, 'completion': completion,
                       'scope': 'Completed local evidence dependency checks, not clinical review or care delivery.', **LIMITS}
            db.execute('INSERT INTO runs VALUES (?,?,?)', (rid, task['id'], json.dumps(receipt)))
            return remember(db, request['request_id'], key, {'run': receipt, 'deduplicated': False, **LIMITS})
    finally: db.close()


def review(path, request, bearer):
    care.exact(request, 'run_id expected_revision request_id disposition note follow_up_on')
    care.text(request['request_id'], 'request ID', 120); care.text(request['note'], 'packet review note', 3000)
    if type(request['expected_revision']) is not int or request['expected_revision'] < 0: raise ValueError('Exact review revision required')
    if request['disposition'] not in ('accepted_for_evidence_review', 'needs_more_evidence'): raise ValueError('Internal packet disposition required')
    try:
        day = date.fromisoformat(request['follow_up_on'])
        if day.isoformat() != request['follow_up_on']: raise ValueError()
    except (TypeError, ValueError): raise ValueError('Canonical operational follow-up date required') from None
    db = database(path)
    try:
        with db:
            db.execute('BEGIN IMMEDIATE'); principal = authenticate(db, bearer, 'packet_reviewer')
            row = db.execute('SELECT payload FROM runs WHERE id=?', (request['run_id'],)).fetchone()
            if not row: raise ValueError('Unknown internal completion receipt')
            receipt = json.loads(row[0]); task = care_readiness.task(path, receipt['task_id']); authorize(principal, task)
            key = digest([request, principal['id'], 'review']); prior = replay(db, request['request_id'], key)
            if prior: return prior
            if day < timestamp(now()).date(): raise ValueError('Follow-up cannot be backdated before this operational review')
            evidence = binding(path, task['id'])[2]
            if receipt['binding'] != evidence: raise care.work.WorkConflict('Completion receipt is stale; run current packet checks first')
            rev = db.execute('SELECT COALESCE(MAX(revision),0) FROM reviews WHERE run=?', (receipt['id'],)).fetchone()[0]
            if rev != request['expected_revision']: raise care.work.WorkConflict('Packet review changed; reload before recording')
            value = {'schema': VERSION, 'run_id': receipt['id'], 'revision': rev+1, 'principal': principal,
                     'recorded_at': now(), 'disposition': request['disposition'], 'note': request['note'],
                     'follow_up_on': request['follow_up_on'], 'clock': 'Operational UTC date, separate from synthetic clinical dates',
                     'binding_id': evidence['id'], 'clinical_requirements_resolved': False, **LIMITS}
            value['id'] = digest(value)
            db.execute('INSERT INTO reviews VALUES (?,?,?,?)', (value['id'], receipt['id'], value['revision'], json.dumps(value)))
            return remember(db, request['request_id'], key, {'review': value, **LIMITS})
    finally: db.close()


def inspect(path, task_id):
    task = care_readiness.task(path, task_id); db = database(path)
    try:
        rows = [json.loads(row[0]) for row in db.execute('SELECT payload FROM runs WHERE task=? ORDER BY rowid', (task_id,))]
        reviews = {r['id']: [json.loads(row[0]) for row in db.execute('SELECT payload FROM reviews WHERE run=? ORDER BY revision', (r['id'],))] for r in rows}
    finally: db.close()
    try: current, reason = binding(path, task_id)[2], None
    except (ValueError, KeyError, FileNotFoundError) as exc: current, reason = None, str(exc)
    for r in rows:
        r['packet_current'] = r['binding'] == current; r['reviews'] = reviews[r['id']]
        latest = r['reviews'][-1] if r['reviews'] else None
        r['follow_up_state'] = ('refresh_required' if not r['packet_current'] else 'packet_review_needed' if not latest else
                                'due' if latest['follow_up_on'] <= timestamp(now()).date().isoformat() else 'scheduled')
    return {'schema': VERSION, 'task_id': task_id, 'task_revision': task['revision'], 'runs': rows,
            'current_run_id': next((r['id'] for r in reversed(rows) if r['packet_current']), None),
            'dispatch_available': current is not None, 'dispatch_unavailable_reason': reason,
            'scope': 'Local credential authentication only; no identity provider, clinician verification or external execution.', **LIMITS}
