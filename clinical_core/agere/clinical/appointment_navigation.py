"""Source-bound navigation review and explicit, unverified buyer responsibilities.

Appointment reports do not establish why a visit was missed or whether another
visit is needed. This adapter adds a documentary origin without changing older
origin, service, assessment, or model identities.
"""
from pathlib import Path
import hashlib, json, sqlite3
from ...contracts import digest
from . import appointment_followthrough as A, care_capabilities as C, care_readiness as R
from . import patient_context as P, coverage_work as W, participation_work as E

VERSION = 'appointment-navigation-review/1'
KIND = 'appointment_navigation_review'
COMPONENTS = [
    {'key': 'source_reconciliation', 'label': 'Reconcile the exact appointment history',
     'question': 'What do the source identities, dates, explicit dispositions, missing reports and conflicts establish about this historical booking?'},
    {'key': 'patient_preferences', 'label': 'Confirm present needs, goals and permission',
     'question': 'Is any further service wanted or clinically appropriate now, and who can confirm patient goals, consent and permitted helper involvement?'},
    {'key': 'access_and_coverage', 'label': 'Check access, benefits and participation',
     'question': 'Which practical access needs, exact benefits, participating provider, modality, capacity and funding arrangements apply to this particular service?'},
    {'key': 'followthrough', 'label': 'Specify accountable follow-through',
     'question': 'Who would coordinate an authorized next step, confirm its receipt and review unresolved follow-through? No contact or scheduling is performed here.'},
]
LIMITS = {'clinical_need_established': False, 'cause_of_no_show_established': False,
          'current_patient_consent_established': False, 'verified_benefits': False,
          'clinical_priority_assigned': False, 'clinical_execution_authorized': False,
          'service_slots_reserved': 0, 'external_actions_executed': 0, 'new_paid_api_calls': 0}


def identity():
    return digest([VERSION, hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), A.identity(), P.identity(), W.identity()])


def db_open(path):
    db = sqlite3.connect(Path(path)/'appointment-navigation.sqlite3', timeout=30)
    db.executescript('CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY,payload TEXT); CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY,task TEXT,payload TEXT);')
    for table in ('reviews', 'proposals'):
        for op in ('UPDATE', 'DELETE'):
            db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{op} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'Navigation history is immutable'); END")
    return db


def store(path, table, row):
    db = db_open(path)
    try:
        with db:
            if table == 'reviews': db.execute('INSERT OR IGNORE INTO reviews VALUES (?,?)', (row['id'], json.dumps(row, allow_nan=False)))
            elif table == 'proposals': db.execute('INSERT OR IGNORE INTO proposals VALUES (?,?,?)', (row['id'], row['task_id'], json.dumps(row, allow_nan=False)))
            else: raise ValueError('Unknown navigation receipt')
    finally: db.close()
    return row


def load(path, table, key):
    C.text(key, 'navigation receipt ID', 64)
    if table not in ('reviews', 'proposals'): raise ValueError('Unknown navigation receipt')
    db = db_open(path)
    try: found = db.execute('SELECT payload FROM '+table+' WHERE id=?', (key,)).fetchone()
    finally: db.close()
    if not found: raise ValueError('Unknown navigation receipt')
    row = json.loads(found[0])
    if row['id'] != key or digest({k: v for k, v in row.items() if k != 'id'}) != key: raise C.work.WorkConflict('Navigation receipt integrity changed')
    return row


def parent_hash(path, key):
    return hashlib.sha256(A.location(path, key).read_bytes()).hexdigest()


def source_snapshot(path, request):
    C.exact(request, 'review_id patient_id')
    parent_digest = parent_hash(path, request['review_id'])
    review = A.inspect(path, request['review_id'])
    if parent_hash(path, review['id']) != parent_digest: raise C.work.WorkConflict('Parent appointment review changed during preparation')
    row = next((r for r in review['patients'] if r['patient_id'] == request['patient_id']), None)
    if row is None or not row['sources']: raise ValueError('Same-review patient with received appointment sources required')
    proposal = C.work.prepare(path, A.PROGRAM, [row['patient_id']], input_membership={'appointment_review_id': review['id'], 'authority': 'source_review_only'})
    assessment = proposal['patients'][0]
    # Reuse the original assessment rather than replacing its questions with navigation language.
    source = C.origin(path, {'kind': 'assessment', 'id': assessment['assessment_id'], 'finding_ids': []})
    result = {'schema': VERSION, 'parameters': request, 'code_identity': identity(),
              'patient_id': row['patient_id'], 'as_of': review['source']['snapshot']['as_of'],
              'appointment_review_id': review['id'], 'parent_file_hash': parent_digest, 'source_metadata': review['source'],
              'currentness_scope': 'Immutable parent receipt; current selected-patient appointment evidence and original work assessment.',
              'appointment': row, 'contract': review['contract'],
              'work_origin': source, 'work_proposal_id': proposal['proposal_id'],
              'original_work_questions': assessment['missing_information'], 'responsibilities': COMPONENTS,
              'question': 'What must be verified before this historical appointment evidence can support buyer navigation?',
              'authority': 'received_source_review_only',
              'limits': [*A.LIMITS, 'A historical no-show is not evidence of present unmet need, its cause, consent, a coverage entitlement or a clinical priority. Research prediction scores are not used in matching or work.'],
              **LIMITS}
    result['id'] = digest(result)
    return result


def prepare(path, request): return store(path, 'reviews', source_snapshot(path, request))


def inspect(path, key):
    row = load(path, 'reviews', key)
    if row['code_identity'] != identity() or row['parent_file_hash'] != parent_hash(path, row['appointment_review_id']):
        raise C.work.WorkConflict('Appointment parent receipt or implementation changed; prepare again')
    meta, received = A.load_rows(path, [row['patient_id']])
    if meta != row['source_metadata'] or received != [row['appointment']]:
        raise C.work.WorkConflict('Selected patient appointment evidence changed; prepare again')
    origin = C.origin(path, {'kind': 'assessment', 'id': row['work_origin']['id'], 'finding_ids': []})
    if origin != row['work_origin']: raise C.work.WorkConflict('Original appointment work assessment changed; prepare again')
    return row


def install():
    if getattr(C.origin, 'appointment_navigation_adapter', False): return
    original_origin, original_prepare = C.origin, C.prepare
    def origin(path, request):
        if request.get('kind') != KIND: return original_origin(path, request)
        C.exact(request, 'kind id finding_ids')
        if request['finding_ids'] != []: raise ValueError('Appointment navigation has no model finding IDs')
        r = inspect(path, request['id'])
        return {'kind': KIND, 'id': r['id'], 'hash': digest(r), 'patient_id': r['patient_id'], 'review': r,
                'authority': 'historical_appointment_documentary_review_not_current_care_need'}
    def matching(path, request):
        if request.get('origin', {}).get('kind') == KIND:
            r = inspect(path, request['origin']['id'])
            if request.get('need_tags') != ['care_navigation']: raise ValueError('This appointment origin supports care_navigation review only; no treatment indication is established')
            if request.get('coverage', {}).get('as_of') != r['as_of']: raise ValueError('Navigation and service matching must use the same receipt cutoff')
        return original_prepare(path, request)
    origin.appointment_navigation_adapter = True
    C.origin, C.prepare = origin, matching


def inputs(path, task_id):
    task = R.task(path, task_id); plan = C.inspect_plan(path, task['plan_id'])
    if plan['origin']['kind'] != KIND: raise ValueError('Exact appointment navigation task required')
    if task['state'] == 'cancelled': raise ValueError('Cancelled navigation task cannot receive a current proposal')
    context = P.task_view(path, task_id); coverage = W.task_view(path, task_id); participation = E.task_view(path, task_id)
    if not context['dependencies_current'] or not coverage['coverage_dependencies_current'] or not participation['queue_participation_current']:
        raise C.work.WorkConflict('Current patient, coverage and queued participation dependencies required')
    return task, plan, context, coverage, participation


def proposal_snapshot(path, request):
    C.exact(request, 'task_id expected_revision component_bindings coordination_note')
    C.text(request['coordination_note'], 'coordination note', 2000)
    task, plan, context, coverage, participation = inputs(path, request['task_id'])
    if type(request['expected_revision']) is not int or request['expected_revision'] != task['revision']:
        raise C.work.WorkConflict('Navigation task changed; reload before proposing responsibilities')
    bindings = request['component_bindings']
    if not isinstance(bindings, list) or len(bindings) > len(COMPONENTS): raise ValueError('At most one service proposal per responsibility')
    selected = {}; keys = {c['key'] for c in COMPONENTS}
    for b in bindings:
        C.exact(b, 'component_key option_id rationale'); C.text(b['rationale'], 'service mapping rationale', 2000)
        if b['component_key'] not in keys or b['component_key'] in selected: raise ValueError('Distinct known navigation responsibilities required')
        option = next((o for o in plan['options'] if o['id'] == b['option_id']), None)
        if option is None: raise ValueError('Exact service option from this patient and buyer match required')
        selected[b['component_key']] = (b, option)
    components = []
    for c in COMPONENTS:
        b, o = selected.get(c['key'], (None, None))
        components.append({**c, 'state': 'no_service_proposed' if o is None else 'blocked_service_proposed' if o['status'] == 'blocked' else 'service_proposed_requires_verification',
                           'proposed_service': None if o is None else {'option_id': o['id'], 'capability': o['capability'], 'routes': o['routes'], 'rationale': b['rationale']},
                           'question_resolved': False, 'responsibility_accepted': False})
    r = {'schema': VERSION, 'request': request, 'code_identity': identity(), 'task_id': task['id'], 'task_revision': task['revision'],
         'patient_id': task['patient_id'], 'plan_id': plan['id'], 'plan_hash': digest(plan), 'source_origin': plan['origin'],
         'task_state': task['state'], 'internal_handoff_hash': digest(task.get('handoff')),
         'patient_context': context, 'coverage_context': coverage, 'participation_context': participation,
         'components': components, 'unassigned_components': [c['key'] for c in components if c['state'] == 'no_service_proposed'],
         'blocked_components': [c['key'] for c in components if c['state'] == 'blocked_service_proposed'],
         'scope': 'Proposed internal review responsibilities only. Service tags and stated owners do not establish staff qualifications, accepted assignment, care delivery or resolved questions.', **LIMITS}
    r['id'] = digest(r)
    return r


def prepare_proposal(path, request):
    C.exact(request, 'proposal preview_id')
    row = proposal_snapshot(path, request['proposal'])
    if row['id'] != request['preview_id']: raise C.work.WorkConflict('Preview changed; inspect a current responsibility proposal')
    return store(path, 'proposals', row)


def inspect_proposal(path, key):
    row = load(path, 'proposals', key)
    if proposal_snapshot(path, row['request']) != row: raise C.work.WorkConflict('Navigation task, sources or proposed responsibilities changed')
    return row


def task_view(path, request):
    C.exact(request, 'task_id')
    task, plan, context, coverage, participation = inputs(path, request['task_id'])
    db = db_open(path)
    try: rows = [json.loads(r[0]) for r in db.execute('SELECT payload FROM proposals WHERE task=? ORDER BY rowid DESC', (task['id'],))]
    finally: db.close()
    for row in rows:
        try: inspect_proposal(path, row['id']); row.update(current=True, stale_reason=None)
        except (ValueError, KeyError, FileNotFoundError) as e: row.update(current=False, stale_reason=str(e))
    return {'task': task, 'source_review': plan['origin']['review'], 'responsibilities': COMPONENTS, 'options': plan['options'],
            'patient_context': context, 'coverage_context': coverage, 'participation_context': participation, 'proposals': rows, **LIMITS}
