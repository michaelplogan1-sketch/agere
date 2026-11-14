"""Buyer-declared services, source-linked matching, and internal handoff work.

Public program discovery is not member benefits verification. No clinical action,
enrollment, patient contact, benefit entitlement or clinician review is inferred.
"""
from datetime import date
from pathlib import Path
import hashlib
import json

from ...contracts import digest
from . import program_work as work, source_synthesis

VERSION = 'buyer-capability-routing/1'
CATALOG = Path(__file__).resolve().parents[3] / 'config/agere/care_pathways.json'
PAYERS = ['original_medicare', 'medicare_advantage', 'commercial', 'medicaid', 'self_pay', 'other']
KINDS = ['clinician', 'agent', 'team']


def catalog():
    return json.loads(CATALOG.read_text())


def identity():
    return digest([VERSION, hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), catalog()])


def db_open(path):
    db = work.database(path)
    db.executescript('''
      CREATE TABLE IF NOT EXISTS capability_portfolios(buyer TEXT,revision INTEGER,payload TEXT,PRIMARY KEY(buyer,revision));
      CREATE TABLE IF NOT EXISTS capability_plans(id TEXT PRIMARY KEY,payload TEXT);
      CREATE TABLE IF NOT EXISTS capability_tasks(id TEXT PRIMARY KEY,buyer TEXT,patient TEXT,revision INTEGER,payload TEXT);
      CREATE TABLE IF NOT EXISTS capability_events(task TEXT,revision INTEGER,payload TEXT,PRIMARY KEY(task,revision));
      CREATE TABLE IF NOT EXISTS capability_requests(id TEXT PRIMARY KEY,hash TEXT,payload TEXT);
      CREATE TRIGGER IF NOT EXISTS capability_history_update BEFORE UPDATE ON capability_portfolios BEGIN SELECT RAISE(ABORT,'Portfolio history is immutable'); END;
      CREATE TRIGGER IF NOT EXISTS capability_history_delete BEFORE DELETE ON capability_portfolios BEGIN SELECT RAISE(ABORT,'Portfolio history is immutable'); END;
      CREATE TRIGGER IF NOT EXISTS capability_events_update BEFORE UPDATE ON capability_events BEGIN SELECT RAISE(ABORT,'Task history is immutable'); END;
      CREATE TRIGGER IF NOT EXISTS capability_events_delete BEFORE DELETE ON capability_events BEGIN SELECT RAISE(ABORT,'Task history is immutable'); END;
    ''')
    return db


def text(value, name, maximum=200, empty=False):
    return work.bounded(value, name, maximum, empty=empty)


def load(db, table, key):
    if table not in ('capability_plans', 'capability_tasks'):
        raise ValueError('Unknown capability table')
    row = db.execute('SELECT payload FROM ' + table + ' WHERE id=?', (key,)).fetchone()
    if row is None: raise ValueError('Unknown saved capability plan or task')
    return json.loads(row[0])


def strings(value, name, maximum=30, empty=True):
    if not isinstance(value, list) or len(value) > maximum or (not empty and not value):
        raise ValueError('Bounded ' + name + ' list required')
    result = [text(v, name) for v in value]
    if len(set(result)) != len(result):
        raise ValueError('Distinct ' + name + ' required')
    return sorted(result)


def day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError('Canonical date required')
    return value


def exact(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields.split()):
        raise ValueError('Expected exact fields: ' + fields)


def owner(value):
    exact(value, 'kind id')
    if value['kind'] not in KINDS:
        raise ValueError('Clinician, agent or team owner required')
    return {'kind': value['kind'], 'id': text(value['id'], 'accountable owner')}


def current(db, buyer):
    row = db.execute('SELECT payload FROM capability_portfolios WHERE buyer=? ORDER BY revision DESC LIMIT 1', (buyer,)).fetchone()
    return json.loads(row[0]) if row else {'buyer_id': buyer, 'revision': 0, 'capabilities': []}


def portfolio(path, buyer):
    buyer = text(buyer, 'buyer ID')
    db = db_open(path)
    try:
        return current(db, buyer)
    finally:
        db.close()


def declared_context(path, as_of):
    """Read the latest buyer declarations without choosing a buyer or a service."""
    import sqlite3
    day(as_of);file=Path(path)/'program-work.sqlite3';portfolios=[]
    if file.exists():
        db=sqlite3.connect(file.resolve().as_uri()+'?mode=ro',uri=True)
        try:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='capability_portfolios'").fetchone():
                rows=db.execute('SELECT p.payload FROM capability_portfolios p WHERE p.revision=(SELECT MAX(q.revision) FROM capability_portfolios q WHERE q.buyer=p.buyer) ORDER BY p.buyer')
                for raw, in rows:
                    p=json.loads(raw)
                    if digest({k:v for k,v in p.items() if k!='id'})!=p['id']:
                        raise work.WorkConflict('Buyer portfolio integrity changed')
                    services=[]
                    for c in p['capabilities']:
                        services.append({'capability_id':c['id'],
                            'effective_period_status':'within_declared_period' if c['effective_start']<=as_of<=c['effective_end'] else 'outside_declared_period',
                            'capacity_status':'stale_or_not_yet_observed' if not c['capacity_as_of']<=as_of<=c['capacity_until'] else
                                'unknown' if c['capacity_slots'] is None else 'no_reported_capacity' if c['capacity_slots']==0 else 'reported_capacity',
                            'clinical_eligibility_established':False})
                    portfolios.append({'declaration':p,'service_context':services,
                        'receipt_status':'received_by_cutoff' if p['recorded_at'][:10]<=as_of else 'not_received_by_cutoff'})
        finally:db.close()
    return {'as_of':as_of,'portfolios':portfolios,'buyer_selected':False,
        'status':'buyer_selection_required' if portfolios else 'no_buyer_portfolio_supplied',
        'scope':'Buyer-reported services only. Inspect effective/capacity dates and verify source/plan prerequisites after selecting the intended buyer. No portfolio does not establish that services are unavailable elsewhere.',
        'service_match_established':False,'coverage_established':False}


def normalize_capability(c):
    exact(c, 'id name description need_tags modality regions payer_types plan_ids pathway_ids owner available capacity_slots capacity_as_of capacity_until effective_start effective_end prerequisites provenance')
    out = {k: text(c[k], k, 2000 if k in ('description', 'provenance') else 200) for k in ('id', 'name', 'description', 'provenance')}
    for k in ('need_tags', 'regions', 'payer_types', 'plan_ids', 'pathway_ids', 'prerequisites'):
        out[k] = strings(c[k], k, empty=k != 'need_tags')
    if not set(out['payer_types']) <= set(PAYERS):
        raise ValueError('Unknown payer type')
    if not set(out['pathway_ids']) <= {p['id'] for p in catalog()['pathways']}:
        raise ValueError('Unknown knowledge pathway')
    if c['modality'] not in ('virtual', 'in_person', 'home', 'hybrid') or type(c['available']) is not bool:
        raise ValueError('Explicit modality and availability required')
    out.update(modality=c['modality'], available=c['available'], owner=owner(c['owner']))
    for k in ('capacity_as_of', 'capacity_until', 'effective_start', 'effective_end'):
        out[k] = day(c[k])
    if out['capacity_until'] < out['capacity_as_of'] or out['effective_end'] < out['effective_start']:
        raise ValueError('Date interval reversed')
    slots = c['capacity_slots']
    if slots is not None and (type(slots) is not int or not 0 <= slots <= 1000000):
        raise ValueError('Capacity must be unknown (null) or a nonnegative integer')
    out['capacity_slots'] = slots
    return out


def save_portfolio(path, request):
    exact(request, 'buyer_id expected_revision actor capabilities')
    buyer = text(request['buyer_id'], 'buyer ID'); actor = text(request['actor'], 'actor')
    if type(request['expected_revision']) is not int or request['expected_revision'] < 0:
        raise ValueError('Nonnegative expected revision required')
    if not isinstance(request['capabilities'], list) or len(request['capabilities']) > 100:
        raise ValueError('At most 100 services per buyer required')
    caps = sorted([normalize_capability(c) for c in request['capabilities']], key=lambda c: c['id'])
    if len({c['id'] for c in caps}) != len(caps):
        raise ValueError('Distinct capability IDs required')
    work.metadata(path)
    db = db_open(path)
    try:
        db.execute('BEGIN IMMEDIATE'); old = current(db, buyer)
        if old['revision'] != request['expected_revision']:
            raise work.WorkConflict('Buyer capabilities changed; reload before saving')
        if old['capabilities'] == caps:
            return old
        out = {'schema': VERSION, 'buyer_id': buyer, 'revision': old['revision'] + 1,
               'capabilities': caps, 'actor': actor, 'recorded_at': work.now(),
               'authority': 'buyer_reported_not_independently_verified', 'synthetic': True}
        out['id'] = digest(out)
        db.execute('INSERT INTO capability_portfolios VALUES (?,?,?)', (buyer, out['revision'], json.dumps(out)))
        db.commit(); return out
    finally:
        db.close()


def origin(path, request):
    exact(request, 'kind id finding_ids')
    key = text(request['id'], 'origin ID', 64)
    ids = strings(request['finding_ids'], 'finding IDs', 100)
    if request['kind'] == 'answer':
        row = source_synthesis.inspect(path, key)
        findings = [f for f in row['findings'] if f['id'] in ids]
        if not ids or len(findings) != len(ids):
            raise ValueError('Select exact findings from this saved answer')
        return {'kind': 'answer', 'id': key, 'hash': digest(row), 'patient_id': row['patient_id'],
                'question': row['question'], 'findings': findings, 'authority': 'machine_proposals_require_review'}
    if request['kind'] == 'assessment':
        if ids:
            raise ValueError('Assessment origins do not take finding IDs')
        db = work.database(path)
        try:
            row = work.load(db, 'work_assessments', key)
        finally:
            db.close()
        if row['source'] != work.metadata(path) or row['program'] != work.definition(row['program']['id']):
            raise work.WorkConflict('Assessment source or program changed')
        src = work.warehouse(path)
        try:
            raw = work.analysis_rows(path, src, row['program'], [row['patient_id']]).get(row['patient_id'])
            if (hashlib.sha256(raw.encode()).hexdigest() if raw else None) != row['analysis_hash']:
                raise work.WorkConflict('Assessment inputs changed')
        finally:
            src.close()
        if row.get('synthesis_context'):
            from .synthesis_work import verify
            verify(path, [row['synthesis_context']])
        if row.get('decision_context'):
            from .selection_context import verify
            verify(path, [row['decision_context']])
        if row.get('accountability_context'):
            from .program_accountability import verify_contexts
            verify_contexts(path, [row['accountability_context']])
        return {'kind': 'assessment', 'id': key, 'hash': digest(row), 'patient_id': row['patient_id'],
                'assessment': row, 'authority': 'review_assessment_not_clinical_eligibility'}
    raise ValueError('Saved answer or assessment origin required')


def coverage(value):
    exact(value, 'payer_type plan_id region as_of provenance preferences')
    if value['payer_type'] not in PAYERS + ['unknown']:
        raise ValueError('Known payer type or explicit unknown required')
    return {'payer_type': value['payer_type'], 'plan_id': text(value['plan_id'], 'plan ID', empty=True),
            'region': text(value['region'], 'region', empty=True), 'as_of': day(value['as_of']),
            'provenance': text(value['provenance'], 'coverage provenance', 2000),
            'preferences': text(value['preferences'], 'patient preferences', 2000, empty=True),
            'authority': 'reported_context_not_verified_benefits'}


def route_check(c, p, cov, knowledge):
    blocked = []; unknown = ['Member eligibility, network, benefit terms, cost sharing and authorization need verification',
                             'Clinical appropriateness, contraindications, patient goals and consent need review']
    if not c['available']: blocked.append('Buyer reports service unavailable')
    if not c['effective_start'] <= cov['as_of'] <= c['effective_end']: blocked.append('Service outside declared effective period')
    if c['regions']:
        if not cov['region']: unknown.append('Patient service region missing')
        elif cov['region'] not in c['regions']: blocked.append('Outside declared service regions')
    else: unknown.append('Service geography and licensure not specified')
    if c['payer_types'] and cov['payer_type'] != 'unknown' and cov['payer_type'] not in c['payer_types']:
        blocked.append('Payer type outside buyer service contract')
    if cov['payer_type'] == 'unknown': unknown.append('Patient payer type unknown')
    if c['plan_ids']:
        if not cov['plan_id']: unknown.append('Exact patient plan ID missing')
        elif cov['plan_id'] not in c['plan_ids']: blocked.append('Exact plan outside buyer declared contract')
    else: unknown.append('No exact contracted plan list supplied')
    capacity_current = c['capacity_as_of'] <= cov['as_of'] <= c['capacity_until']
    if not capacity_current: unknown.append('Capacity observation does not cover decision date')
    elif c['capacity_slots'] == 0: blocked.append('No reported service capacity')
    elif c['capacity_slots'] is None: unknown.append('Service capacity unknown')
    unknown.extend(c['prerequisites'])
    if p:
        if cov['payer_type'] != 'unknown' and cov['payer_type'] not in p['payer_types']:
            blocked.append('Payer type outside this public pathway')
        if p.get('effective_start') and cov['as_of'] < p['effective_start']:
            blocked.append('Pathway has not started on decision date')
        if p.get('effective_end') and cov['as_of'] > p['effective_end']:
            blocked.append('Pathway outside published model period')
        unknown.extend(p['prerequisites'])
        if cov['as_of'] != knowledge['checked_on']:
            unknown.append('Public policy was checked on ' + knowledge['checked_on'] + '; verify policy for decision date ' + cov['as_of'])
    else:
        unknown.append('No public payment pathway attached; buyer-specific funding must be checked')
    return {'pathway_id': p['id'] if p else None, 'pathway': p,
            'status': 'blocked' if blocked else 'requires_verification',
            'blocked_reasons': blocked, 'requirements': sorted(set(unknown)),
            'coverage_established': False, 'clinical_eligibility_established': False}


def prepare(path, request):
    exact(request, 'buyer_id origin need_tags rationale coverage')
    buyer = text(request['buyer_id'], 'buyer ID'); port = portfolio(path, buyer)
    src = origin(path, request['origin']); tags = strings(request['need_tags'], 'need tags', empty=False)
    rationale = text(request['rationale'], 'need mapping rationale', 2000); cov = coverage(request['coverage'])
    knowledge = catalog(); options = []; excluded = []
    pathways = {p['id']: p for p in knowledge['pathways']}
    for c in port['capabilities']:
        matched = sorted(set(tags) & set(c['need_tags']))
        if not matched:
            excluded.append({'capability_id': c['id'], 'reason': 'No declared need-tag overlap'}); continue
        routes = [route_check(c, pathways[p], cov, knowledge) for p in c['pathway_ids']] or [route_check(c, None, cov, knowledge)]
        option = {'capability': c, 'matched_tags': matched, 'routes': routes,
                  'status': 'blocked' if all(r['status'] == 'blocked' for r in routes) else 'requires_verification',
                  'service_slots_reserved': 0, 'clinical_execution_authorized': False}
        option['id'] = digest(option); options.append(option)
    out = {'schema': VERSION, 'buyer_id': buyer, 'patient_id': src['patient_id'],
           'request': request, 'origin': src, 'need_tags': tags, 'rationale': rationale,
           'need_mapping_authority': 'operator_selected_not_clinically_confirmed',
           'coverage': cov, 'portfolio_id': port.get('id'), 'portfolio_revision': port['revision'],
           'knowledge_hash': digest(knowledge), 'code_identity': identity(), 'warehouse': work.metadata(path),
           'options': options, 'excluded': excluded,
           'unmatched_tags': sorted(set(tags) - {t for o in options for t in o['matched_tags']}),
           'synthetic': True, 'new_paid_api_calls': 0, 'external_actions_executed': 0,
           'scope': 'Match declared needs to declared services. No inferred benefit, treatment recommendation or measured outcome benefit.'}
    out['id'] = digest(out)
    db = db_open(path)
    try:
        with db: db.execute('INSERT OR IGNORE INTO capability_plans VALUES (?,?)', (out['id'], json.dumps(out)))
    finally:
        db.close()
    return out


def inspect_plan(path, key):
    db = db_open(path)
    try: row = load(db, 'capability_plans', text(key, 'plan ID', 64))
    finally: db.close()
    if digest({k: v for k, v in row.items() if k != 'id'}) != row['id']:
        raise work.WorkConflict('Capability plan integrity changed')
    if prepare(path, row['request'])['id'] != row['id']:
        raise work.WorkConflict('Sources, buyer services, coverage policy or routing changed; prepare again')
    return row


def task_view(path, buyer):
    db = db_open(path)
    try:
        rows = [json.loads(r[0]) for r in db.execute('SELECT payload FROM capability_tasks WHERE buyer=? ORDER BY rowid DESC', (text(buyer, 'buyer ID'),))]
        for row in rows:
            try: inspect_plan(path, row['plan_id']); row['dependencies_current'] = True
            except (ValueError, KeyError): row['dependencies_current'] = False
        return {'buyer_id': buyer, 'tasks': rows, 'external_actions_executed': 0}
    finally: db.close()


def enqueue(path, request):
    exact(request, 'plan_id option_id actor')
    plan = inspect_plan(path, request['plan_id']); actor = text(request['actor'], 'actor')
    option = next((o for o in plan['options'] if o['id'] == request['option_id']), None)
    if not option or option['status'] == 'blocked':
        raise ValueError('A service with a potentially applicable route is required')
    # An exact source/need/service is one internal task even across repeated preparations.
    key = digest([VERSION, plan['buyer_id'], plan['patient_id'], plan['request']['origin'], plan['need_tags'], option['capability']['id']])
    db = db_open(path)
    try:
        db.execute('BEGIN IMMEDIATE')
        if current(db, plan['buyer_id']).get('id') != plan['portfolio_id']:
            raise work.WorkConflict('Buyer portfolio changed before queueing')
        old = db.execute('SELECT payload FROM capability_tasks WHERE id=?', (key,)).fetchone()
        if old:
            task = json.loads(old[0])
            if task['plan_id'] != plan['id']:
                if task['state'] == 'cancelled':
                    raise work.WorkConflict('Existing task was cancelled; do not silently reopen it')
                task.update(plan_id=plan['id'], option_id=option['id'], owner=option['capability']['owner'],
                            name=option['capability']['name'], state='queued_for_internal_review', revision=task['revision'] + 1)
                task.pop('handoff', None)
                event = {'action': 'context_refreshed', 'actor': actor, 'recorded_at': work.now(), 'task': task}
                db.execute('UPDATE capability_tasks SET revision=?,payload=? WHERE id=?', (task['revision'], json.dumps(task), key))
                db.execute('INSERT INTO capability_events VALUES (?,?,?)', (key, task['revision'], json.dumps(event)))
                db.commit()
                return {'task': task, 'idempotent_replay': False, 'context_refreshed': True, 'external_actions_executed': 0}
            return {'task': task, 'idempotent_replay': True, 'external_actions_executed': 0}
        task = {'id': key, 'buyer_id': plan['buyer_id'], 'patient_id': plan['patient_id'],
                'plan_id': plan['id'], 'option_id': option['id'], 'capability_id': option['capability']['id'],
                'name': option['capability']['name'], 'owner': option['capability']['owner'],
                'state': 'queued_for_internal_review', 'revision': 1, 'created_at': work.now(),
                'clinical_execution_authorized': False, 'service_slots_reserved': 0, 'synthetic': True}
        db.execute('INSERT INTO capability_tasks VALUES (?,?,?,?,?)', (key, plan['buyer_id'], plan['patient_id'], 1, json.dumps(task)))
        event = {'action': 'queued', 'actor': actor, 'recorded_at': work.now(), 'task': task}
        db.execute('INSERT INTO capability_events VALUES (?,?,?)', (key, 1, json.dumps(event)))
        db.commit(); return {'task': task, 'idempotent_replay': False, 'external_actions_executed': 0}
    finally: db.close()


def task_event(path, request):
    exact(request, 'task_id expected_revision request_id action actor note')
    actor = text(request['actor'], 'actor'); note = text(request['note'], 'operational note', 2000)
    rid = text(request['request_id'], 'request ID'); key = text(request['task_id'], 'task ID', 64)
    if request['action'] not in ('prepare_handoff', 'hold', 'cancel') or type(request['expected_revision']) is not int:
        raise ValueError('Internal prepare_handoff, hold or cancel action required')
    db = db_open(path)
    try:
        task = load(db, 'capability_tasks', key)
    finally: db.close()
    # Even an idempotent response verifies dependencies before displaying reusable work.
    plan = inspect_plan(path, task['plan_id'])
    option = next(o for o in plan['options'] if o['id'] == task['option_id'])
    db = db_open(path)
    try:
        db.execute('BEGIN IMMEDIATE')
        old = db.execute('SELECT hash,payload FROM capability_requests WHERE id=?', (rid,)).fetchone()
        if old:
            if old[0] != digest(request): raise work.WorkConflict('Request ID reused for a different action')
            return {**json.loads(old[1]), 'idempotent_replay': True}
        task = load(db, 'capability_tasks', key)
        if current(db, plan['buyer_id']).get('id') != plan['portfolio_id']:
            raise work.WorkConflict('Buyer portfolio changed before task action')
        if task['revision'] != request['expected_revision']: raise work.WorkConflict('Task changed; reload')
        if task['state'] == 'cancelled': raise ValueError('Cancelled task cannot progress')
        if request['action'] == 'prepare_handoff':
            task['handoff'] = {'schema': 'internal-capability-handoff/1', 'origin': plan['origin'],
                'service': option['capability'], 'coverage_context': plan['coverage'], 'routes': option['routes'],
                'need_tags': plan['need_tags'], 'mapping_rationale': plan['rationale'],
                'prepared_by': 'local deterministic evidence-bundle worker', 'human_review_completed': False,
                'external_dispatch_performed': False, 'service_slots_reserved': 0}
            task['state'] = 'handoff_prepared_for_review'
        else: task['state'] = 'on_hold' if request['action'] == 'hold' else 'cancelled'
        task['revision'] += 1
        out = {'task': task, 'external_actions_executed': 0, 'idempotent_replay': False}
        event = {'action': request['action'], 'actor': actor, 'note': note, 'recorded_at': work.now(), 'task': task}
        db.execute('UPDATE capability_tasks SET revision=?,payload=? WHERE id=?', (task['revision'], json.dumps(task), key))
        db.execute('INSERT INTO capability_events VALUES (?,?,?)', (key, task['revision'], json.dumps(event)))
        db.execute('INSERT INTO capability_requests VALUES (?,?,?)', (rid, digest(request), json.dumps(out)))
        db.commit(); return out
    finally: db.close()
