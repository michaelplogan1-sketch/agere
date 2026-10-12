"""Persistent synthetic program work derived from full, source-bound review cohorts.

Preparing a proposal does not create work or contact anyone. Operational events
are local staff reports; neither enrollment nor completion establishes clinical
eligibility, optimized treatment, clinical benefit or payment entitlement.
"""
from collections import Counter
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from ...contracts import digest

VERSION = 'program-work/2'
PROGRAMS = {
    'hf_review': {'name': 'Heart failure medication review', 'operator': 'heart_failure_review',
                  'version': 'research-review/1', 'purpose': 'Reconcile EF, medication use, exceptions and tolerance before a PharmD program offer.'},
    'affordability': {'name': 'Affordability and access review', 'operator': 'affordability_review',
                      'version': 'research-review/1', 'purpose': 'Reconcile reported need and assistance options before a program offer.'},
}
STATES = ('review_needed', 'reviewing', 'ready_for_offer', 'offered', 'enrolled', 'active',
          'on_hold', 'declined', 'review_closed', 'completed')
TERMINAL = {'declined', 'review_closed', 'completed'}
LIMITS = [
    'Worklists contain documentation-based review candidates, not confirmed treatment gaps or program eligibility.',
    'Staff identity, contact, consent and program milestones are local self-reported operational records; there is no identity-provider authentication.',
    'Source links prove which records were referenced, not that those records support a clinical conclusion.',
    'A completed program is staff-reported completion, not proof of optimized treatment, clinical benefit or a payable outcome.',
    'This local workspace sends no messages, schedules no care and makes no treatment changes.',
]


from .program_work_v1 import WorkConflict


def now():
    return datetime.now(timezone.utc).isoformat()


def code_identity():
    from .selection_context import identity
    from .synthesis_work import identity as synthesis_identity
    from .protocol_programs import identity as protocol_identity
    from .functional_programs import identity as functional_identity
    return digest([hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),identity(),protocol_identity(),synthesis_identity(),functional_identity()])


def bounded(value, name, maximum=1000, *, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise ValueError('Bounded ' + name + ' required')
    return value.strip()


def checked_date(value):
    if value is None:
        return None
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError('Canonical due date required')
    return value


def definition(program):
    from . import functional_programs
    if functional_programs.handles(program):return functional_programs.definition(program)
    from . import protocol_programs
    if protocol_programs.handles(program):return protocol_programs.definition(program)
    if not isinstance(program, str) or program not in PROGRAMS:
        raise ValueError('Unknown review program')
    return {'id': program, **PROGRAMS[program]}


def database(path):
    db = sqlite3.connect(Path(path)/'program-work.sqlite3', timeout=30)
    db.execute('PRAGMA foreign_keys=ON')
    db.executescript('''
      CREATE TABLE IF NOT EXISTS work_proposals(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS work_assessments(id TEXT PRIMARY KEY,patient TEXT NOT NULL,program TEXT NOT NULL,payload TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS work_items(id TEXT PRIMARY KEY,patient TEXT NOT NULL,program TEXT NOT NULL,revision INTEGER NOT NULL,payload TEXT NOT NULL,UNIQUE(patient,program));
      CREATE TABLE IF NOT EXISTS work_events(item TEXT NOT NULL,revision INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(item,revision),FOREIGN KEY(item) REFERENCES work_items(id));
      CREATE TABLE IF NOT EXISTS work_requests(id TEXT PRIMARY KEY,request_hash TEXT NOT NULL,payload TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS work_program ON work_items(program,patient);
      CREATE TRIGGER IF NOT EXISTS immutable_event_update BEFORE UPDATE ON work_events BEGIN SELECT RAISE(ABORT,'Work events are append-only'); END;
      CREATE TRIGGER IF NOT EXISTS immutable_event_delete BEFORE DELETE ON work_events BEGIN SELECT RAISE(ABORT,'Work events are append-only'); END;
      CREATE TRIGGER IF NOT EXISTS immutable_assessment_update BEFORE UPDATE ON work_assessments BEGIN SELECT RAISE(ABORT,'Assessments are immutable'); END;
      CREATE TRIGGER IF NOT EXISTS immutable_assessment_delete BEFORE DELETE ON work_assessments BEGIN SELECT RAISE(ABORT,'Assessments are immutable'); END;
    ''')
    return db


def warehouse(path):
    return sqlite3.connect((Path(path)/'stack.sqlite3').resolve().as_uri()+'?mode=ro', uri=True)


def metadata(path):
    m = json.loads((Path(path)/'run.json').read_text())
    if m.get('synthetic') is not True:
        raise ValueError('This program workspace requires the synthetic warehouse')
    return {k: m[k] for k in ('source_run', 'source_digest', 'snapshot')}


def record_refs(db, patient, ids):
    if not isinstance(ids, list) or len(ids) > 500 or any(not isinstance(x, str) for x in ids) or len(set(ids)) != len(ids):
        raise ValueError('Distinct bounded source-record IDs required')
    found = {rid: json.loads(raw) for rid, raw in db.execute('SELECT id,payload FROM records WHERE patient=?', (patient,))}
    refs = []
    for rid in sorted(ids):
        r = found.get(rid)
        if r is None or r.get('patient_id') != patient:
            raise ValueError('Evidence must resolve to this patient in the warehouse')
        refs.append({'record_id': rid, 'record_hash': digest(r), 'source_table': r['source_table'],
                     'event_date': r.get('event_date'), 'available_date': r.get('available_date')})
    return refs


def referenced_ids(value):
    result = set()
    if isinstance(value, dict):
        if isinstance(value.get('record_id'), str):
            result.add(value['record_id'])
        for child in value.values():
            result.update(referenced_ids(child))
    elif isinstance(value, list):
        for child in value:
            result.update(referenced_ids(child))
    return result


def registered_programs():
    from .protocol_programs import catalog
    from .functional_programs import catalog as functional_catalog
    return [definition(k) for k in PROGRAMS]+catalog()+functional_catalog()


def analysis_rows(path,src,spec,patients=None):
    from . import functional_programs
    if functional_programs.handles(spec['id']):return functional_programs.rows(path,spec,patients)
    from . import protocol_programs
    if protocol_programs.handles(spec['id']):return protocol_programs.rows(path,spec,patients)
    rows=dict(src.execute('SELECT patient,payload FROM analyses WHERE operator=?',(spec['operator'],)))
    return rows if patients is None else {pid:rows[pid] for pid in patients if pid in rows}


def preparation_identity(path,programs):
    """Bind graph replay to definitions and current clinical input dependencies."""
    from . import protocol_programs
    src=warehouse(path);identities=[]
    try:
        for p in sorted(set(programs)):
            spec=definition(p)
            dependency=(protocol_programs.preparation(path,spec)['identity'] if protocol_programs.handles(p)
                else sorted(analysis_rows(path,src,spec).items()))
            identities.append([spec,dependency])
        return digest([code_identity(),identities])
    finally:src.close()


def prepare(path, program, patient_ids, *, input_membership=None, decision_contexts=None, accountability_contexts=None, synthesis_contexts=None):
    """Read all requested patients, store an immutable proposal, return a preview."""
    spec = definition(program); meta = metadata(path); src = warehouse(path)
    ids = sorted(set(patient_ids)); db = database(path)
    assessments = []; input_hashes = []; counts = Counter(assessed=0, candidates=0, missing_assessment=0)
    try:
        from .selection_context import verify
        decision_contexts=decision_contexts or {}
        if set(decision_contexts)-set(ids) or any(pid!=ctx.get('patient_id') for pid,ctx in decision_contexts.items()):
            raise ValueError('Decision context must match the selected patient')
        verify(path,list(decision_contexts.values()))
        from .program_accountability import verify_contexts
        accountability_contexts=accountability_contexts or {}
        if accountability_contexts and (set(accountability_contexts)!=set(ids) or any(pid!=ctx.get('patient_id') for pid,ctx in accountability_contexts.items())):
            raise ValueError('Accountability context must cover exactly the selected patients')
        verify_contexts(path,list(accountability_contexts.values()))
        from .synthesis_work import verify as verify_synthesis
        synthesis_contexts=synthesis_contexts or {}
        if set(synthesis_contexts)-set(ids) or any(pid!=ctx.get('patient_id') for pid,ctx in synthesis_contexts.items()):
            raise ValueError('Synthesis context must match selected patients')
        verify_synthesis(path,list(synthesis_contexts.values()))
        universe = {r[0] for r in src.execute('SELECT id FROM patients')}
        if not set(ids) <= universe:
            raise ValueError('Unknown patient in worklist population')
        raw_rows = analysis_rows(path,src,spec,ids)
        for pid in ids:
            raw = raw_rows.get(pid)
            input_hashes.append([pid, hashlib.sha256(raw.encode()).hexdigest() if raw else None])
            analysis = json.loads(raw) if raw else None
            if analysis is not None:
                if (analysis.get('synthetic') is not True or analysis.get('patient_id') != pid
                    or analysis.get('source_run') != meta['source_run']
                    or analysis['workflow'].get('as_of') != meta['snapshot']['as_of']):
                    raise ValueError('Clinical review and warehouse identities do not agree')
                candidate = analysis['workflow']['review_candidate']
                if type(candidate) is not bool:
                    raise ValueError('Explicit review-candidate state required')
                counts['assessed'] += 1; counts['candidates'] += candidate
            else:
                candidate = None; counts['missing_assessment'] += 1
            assessment = {'schema': VERSION, 'patient_id': pid, 'program': spec, 'source': meta,
                'review_candidate': candidate,
                'status': analysis['workflow']['status'] if analysis else 'assessment_unavailable',
                'analysis_hash': input_hashes[-1][1],
                'review': analysis if candidate else None,
                'evidence': record_refs(src, pid, sorted(referenced_ids(analysis))) if candidate else [],
                'decision_context':decision_contexts.get(pid),
                'clinical_eligibility_established': False}
            if analysis and analysis.get('protocol_receipt_id'):
                row=analysis['protocol_evidence']
                assessment['protocol_context']={'receipt_id':row['id'],'receipt_hash':digest(row),'protocol_id':row['protocol']['id'],'protocol_hash':row['protocol']['hash'],'status':row['status'],'trigger':row['trigger'],'documentary_readiness':row['documentary_readiness'],'missing_inputs':row['missing_inputs'],'unimplemented_clinical_requirements':row['unimplemented_clinical_requirements']}
            if accountability_contexts:assessment['accountability_context']=accountability_contexts[pid]
            if pid in synthesis_contexts:assessment['synthesis_context']=synthesis_contexts[pid]
            assessment['id'] = digest(assessment)
            assessments.append(assessment)
        proposal = {'schema': VERSION, 'synthetic': True, 'program': spec, 'source': meta,
                    'code_identity': code_identity(), 'input_hashes': input_hashes,
                    'assessment_ids': [a['id'] for a in assessments], 'patients_selected': len(ids),
                    'counts': dict(counts), 'input_membership': input_membership,
                    'accountability_assessments':len(accountability_contexts),
                    'synthesis_assessments':len(synthesis_contexts),
                    'assessment_status_counts':dict(Counter(a['status'] for a in assessments)),
                    'limits': LIMITS, 'external_actions_executed': 0}
        proposal['id'] = digest(proposal)
        with db:
            for a in assessments:
                db.execute('INSERT OR IGNORE INTO work_assessments VALUES (?,?,?,?)',
                           (a['id'], a['patient_id'], program, json.dumps(a, allow_nan=False)))
            db.execute('INSERT OR IGNORE INTO work_proposals VALUES (?,?)', (proposal['id'], json.dumps(proposal, allow_nan=False)))
        return {'schema': VERSION, 'synthetic': True, 'proposal_id': proposal['id'], 'program': spec,
                'counts': {'selected': len(ids), **counts}, 'input_membership': input_membership,
                'accountability_assessments':len(accountability_contexts),
                    'synthesis_assessments':len(synthesis_contexts),
                    'assessment_status_counts':dict(Counter(a['status'] for a in assessments)),
                'patients': [{'patient_id': a['patient_id'], 'status': a['status'], 'assessment_id': a['id'],
                              'missing_information': a['review']['workflow']['missing_information']} for a in assessments if a['review_candidate']][:10],
                'scope': 'Complete selected population assessed. Saving creates review work; it does not enroll or contact patients.',
                'new_paid_api_calls': 0, 'limits': LIMITS}
    finally:
        src.close(); db.close()


def load(db, table, identifier):
    if table not in {'work_items', 'work_proposals', 'work_assessments'}:
        raise ValueError('Unknown internal table')
    row = db.execute('SELECT payload FROM '+table+' WHERE id=?', (identifier,)).fetchone()
    if not row:
        raise ValueError('Unknown '+table.replace('work_', '').rstrip('s'))
    return json.loads(row[0])


def append_event(db, item, actor, action, data, before):
    previous = db.execute('SELECT payload FROM work_events WHERE item=? ORDER BY revision DESC LIMIT 1', (item['id'],)).fetchone()
    event = {'item_id': item['id'], 'revision': item['revision'], 'cycle': item['cycle'],
             'recorded_at': now(), 'actor': actor, 'action': action, 'data': data,
             'before_state': before, 'after_state': item['state'], 'assessment_id': item['assessment_id'],
             'previous_event_hash': json.loads(previous[0])['event_hash'] if previous else None,
             'authority': 'Local operational report; no clinical outcome verification'}
    event['event_hash'] = digest(event)
    item['updated_at'] = event['recorded_at']
    db.execute('INSERT INTO work_items VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload',
               (item['id'], item['patient_id'], item['program_id'], item['revision'], json.dumps(item)))
    db.execute('INSERT INTO work_events VALUES (?,?,?)', (item['id'], item['revision'], json.dumps(event)))


def materialize(path, proposal_id, actor):
    bounded(proposal_id, 'proposal ID', 64); actor = bounded(actor, 'actor', 120)
    db = database(path); src = warehouse(path)
    try:
        p = load(db, 'work_proposals', proposal_id)
        if p['code_identity'] != code_identity() or p['source'] != metadata(path) or p['program'] != definition(p['program']['id']):
            raise WorkConflict('Proposal source or definition changed; prepare a current proposal')
        current = analysis_rows(path,src,p['program'],[pid for pid,_ in p['input_hashes']])
        if any((hashlib.sha256(current[pid].encode()).hexdigest() if pid in current else None) != expected for pid, expected in p['input_hashes']):
            raise WorkConflict('Clinical review changed; prepare a current proposal')
        counts = Counter(created=0, unchanged=0, evidence_changed=0, reopened=0, noncandidate_without_work=0)
        from .selection_context import verify
        verify(path,[ctx for aid in p['assessment_ids'] if (ctx:=load(db,'work_assessments',aid).get('decision_context'))])
        from .program_accountability import verify_contexts
        verify_contexts(path,[ctx for aid in p['assessment_ids'] if (ctx:=load(db,'work_assessments',aid).get('accountability_context'))])
        from .synthesis_work import verify as verify_synthesis
        verify_synthesis(path,[ctx for aid in p['assessment_ids'] if (ctx:=load(db,'work_assessments',aid).get('synthesis_context'))])
        db.execute('BEGIN IMMEDIATE')
        for aid in p['assessment_ids']:
            a = load(db, 'work_assessments', aid); ident = digest([VERSION, a['program']['id'], a['patient_id']])
            old = db.execute('SELECT payload FROM work_items WHERE id=?', (ident,)).fetchone()
            if not old and a['review_candidate'] is not True:
                counts['noncandidate_without_work'] += 1; continue
            if old:
                item = json.loads(old[0])
                if item['assessment_id'] == aid:
                    counts['unchanged'] += 1; continue
                before = item['state']; item['revision'] += 1; item['assessment_id'] = aid
                item['reassessment_required'] = True; counts['evidence_changed'] += 1
                if before in TERMINAL:
                    item['state'] = 'review_needed'; item['cycle'] += 1; counts['reopened'] += 1
                action = 'evidence_changed'
            else:
                before = None; action = 'created'; counts['created'] += 1
                item = {'id': ident, 'patient_id': a['patient_id'], 'program_id': a['program']['id'],
                        'state': 'review_needed', 'revision': 1, 'cycle': 1, 'owner': None, 'due_date': None,
                        'assessment_id': aid, 'reassessment_required': False, 'created_at': now(),
                        'clinical_goal_verified': False, 'synthetic': True}
            item['review_candidate'] = a['review_candidate']; item['candidate_status'] = a['status']
            append_event(db, item, actor, action, {'proposal_id': proposal_id}, before)
        db.commit()
        return {'schema': VERSION, 'synthetic': True, 'program': p['program'], 'proposal_id': proposal_id,
                'changes': dict(counts), 'scope': 'Only selected patients refreshed. Other work and unknown cohort membership remain unchanged.',
                'new_paid_api_calls': 0, 'external_actions_executed': 0}
    except Exception:
        db.rollback(); raise
    finally:
        db.close(); src.close()


ACTION_FIELDS = {
    'assign': {'owner', 'due_date'}, 'start_review': {'note'},
    'finish_review': {'note', 'disposition', 'record_ids'},
    'record_contact': {'note', 'outcome', 'channel'}, 'offer': {'note'},
    'enroll': {'note', 'consent'}, 'start_program': {'note'},
    'record_monitoring': {'note', 'record_ids', 'due_date'},
    'complete_program': {'note', 'record_ids'}, 'hold': {'note', 'due_date'},
    'reopen': {'note'}, 'reconcile_change': {'note', 'record_ids'},
}


def change(path, request):
    if not isinstance(request, dict) or set(request) != {'item_id','expected_revision','action','actor','request_id','data'}:
        raise ValueError('Explicit work-event request required')
    item_id = bounded(request['item_id'], 'item ID', 64)
    actor = bounded(request['actor'], 'actor', 120); request_id = bounded(request['request_id'], 'request ID', 120)
    action = request['action']; data = request['data']
    if type(request['expected_revision']) is not int or request['expected_revision'] < 1:
        raise ValueError('Positive expected revision required')
    if not isinstance(action, str) or action not in ACTION_FIELDS or not isinstance(data, dict) or set(data) != ACTION_FIELDS[action]:
        raise ValueError('Registered action with exact data fields required')
    if 'note' in data: bounded(data['note'], 'operational note', 3000)
    if 'due_date' in data: checked_date(data['due_date'])
    db = database(path); src = warehouse(path); key = digest(request)
    try:
        db.execute('BEGIN IMMEDIATE')
        prior = db.execute('SELECT request_hash,payload FROM work_requests WHERE id=?', (request_id,)).fetchone()
        if prior:
            if prior[0] != key: raise WorkConflict('Request ID already used for another change')
            return {**json.loads(prior[1]), 'idempotent_replay': True}
        item = load(db, 'work_items', item_id); before = item['state']
        if item['revision'] != request['expected_revision']:
            raise WorkConflict('Work item changed; reload before editing')
        a = load(db, 'work_assessments', item['assessment_id'])
        if a.get('decision_context'):
            from .selection_context import verify
            try:verify(path,[a['decision_context']])
            except ValueError as error:raise WorkConflict(str(error)) from error
        if a.get('accountability_context'):
            from .program_accountability import verify_contexts
            try:verify_contexts(path,[a['accountability_context']])
            except ValueError as error:raise WorkConflict(str(error)) from error
        if a.get('synthesis_context'):
            from .synthesis_work import verify as verify_synthesis
            try:verify_synthesis(path,[a['synthesis_context']])
            except ValueError as error:raise WorkConflict(str(error)) from error
        if a['source'] != metadata(path):
            raise WorkConflict('Warehouse snapshot changed; refresh work before editing')
        if a['program']!=definition(a['program']['id']):raise WorkConflict('Program definition changed; refresh work before editing')
        raw = analysis_rows(path,src,a['program'],[item['patient_id']]).get(item['patient_id'])
        if (hashlib.sha256(raw.encode()).hexdigest() if raw else None) != a['analysis_hash']:
            raise WorkConflict('Clinical review changed; refresh work before editing')
        refs = record_refs(src, item['patient_id'], data['record_ids']) if 'record_ids' in data else []
        if 'record_ids' in data and not refs:
            raise ValueError('At least one patient source record is required')
        history = [json.loads(x[0]) for x in db.execute('SELECT payload FROM work_events WHERE item=? ORDER BY revision', (item_id,))]
        current_actions = {e['action'] for e in history if e['cycle'] == item['cycle']}
        if action not in ('assign', 'reopen') and not item['owner']:
            raise ValueError('Assign an accountable owner before recording program work')
        if item['reassessment_required'] and action not in ('assign','reconcile_change','hold','reopen'):
            raise WorkConflict('Changed evidence needs reconciliation before program progression')
        if action in ('offer','enroll','start_program','record_monitoring','complete_program') and item['review_candidate'] is not True:
            raise ValueError('Current review candidacy is unresolved or no longer supported')
        if action == 'assign':
            item['owner'] = bounded(data['owner'], 'owner', 120); item['due_date'] = data['due_date']
        elif action == 'start_review':
            if before != 'review_needed': raise ValueError('Review must start from review_needed')
            item['state'] = 'reviewing'
        elif action == 'finish_review':
            if before != 'reviewing' or data['disposition'] not in ('offer_program','information_needed','no_action_supported'):
                raise ValueError('Active review and a supported disposition required')
            if data['disposition'] == 'offer_program' and item['review_candidate'] is not True:
                raise ValueError('Cannot offer from an unsupported candidate')
            item['state'] = {'offer_program':'ready_for_offer','information_needed':'on_hold','no_action_supported':'review_closed'}[data['disposition']]
        elif action == 'record_contact':
            if before in TERMINAL or data['outcome'] not in ('attempted','reached','unreachable','declined') or data['channel'] not in ('phone','portal','mail','in_person'):
                raise ValueError('Open work item and bounded contact report required')
            if data['outcome'] == 'declined': item['state'] = 'declined'
        elif action in ('offer', 'enroll', 'start_program'):
            expected, target = {'offer':('ready_for_offer','offered'),'enroll':('offered','enrolled'),'start_program':('enrolled','active')}[action]
            if before != expected: raise ValueError('Expected state '+expected)
            if action == 'enroll' and data['consent'] != 'confirmed': raise ValueError('Explicit staff-reported consent required')
            item['state'] = target
        elif action == 'record_monitoring':
            if before != 'active': raise ValueError('Monitoring requires an active program')
            item['due_date'] = data['due_date']
        elif action == 'complete_program':
            current_evidence_actions = {e['action'] for e in history if e['cycle'] == item['cycle'] and e['assessment_id'] == item['assessment_id']}
            if (before != 'active' or 'enroll' not in current_actions
                or not current_evidence_actions & {'finish_review','reconcile_change'}
                or 'record_monitoring' not in current_evidence_actions):
                raise ValueError('Completion requires current-evidence review/monitoring and consent/enrollment in this cycle')
            item['state'] = 'completed'; item['due_date'] = None
        elif action == 'hold':
            if before in TERMINAL or data['due_date'] is None: raise ValueError('Open work and a reassessment date required')
            item['state'] = 'on_hold'; item['due_date'] = data['due_date']
        elif action == 'reopen':
            if before not in TERMINAL | {'on_hold'}: raise ValueError('Only held or closed work can reopen')
            item['state'] = 'review_needed'; item['cycle'] += 1
        elif action == 'reconcile_change':
            if not item['reassessment_required']: raise ValueError('No pending evidence change')
            item['reassessment_required'] = False
        item['revision'] += 1
        append_event(db, item, actor, action, {**data, 'resolved_source_evidence': refs}, before)
        result = {'schema': VERSION, 'synthetic': True, 'item': item, 'idempotent_replay': False,
                  'new_paid_api_calls': 0, 'external_actions_executed': 0, 'limits': LIMITS}
        db.execute('INSERT INTO work_requests VALUES (?,?,?)', (request_id, key, json.dumps(result)))
        db.commit(); return result
    except Exception:
        db.rollback(); raise
    finally:
        src.close(); db.close()


def inspect_item(path, item_id):
    db = database(path)
    try:
        item = load(db, 'work_items', bounded(item_id, 'item ID', 64))
        return {'schema': VERSION, 'synthetic': True, 'item': item,
                'assessment': load(db, 'work_assessments', item['assessment_id']),
                'history': [json.loads(r[0]) for r in db.execute('SELECT payload FROM work_events WHERE item=? ORDER BY revision', (item_id,))],
                'limits': LIMITS}
    finally:
        db.close()


def list_items(path, *, program=None, state=None, offset=0, limit=25):
    if program is not None: definition(program)
    if state is not None and state not in STATES: raise ValueError('Unknown work state')
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Bounded worklist page required')
    db = database(path)
    try:
        items = [json.loads(r[0]) for r in db.execute('SELECT payload FROM work_items'+(' WHERE program=?' if program else '')+' ORDER BY patient,program', (program,) if program else ())]
        counts = Counter(i['state'] for i in items)
        selected = [i for i in items if state is None or i['state'] == state]
        # Operational milestones are cumulative people, not clinical-effect measures.
        ever = {k:set() for k in ('offer','enroll','complete_program')}; relevant = {i['id'] for i in items}
        for ident, raw in db.execute('SELECT item,payload FROM work_events'):
            if ident in relevant:
                event = json.loads(raw)
                if event['action'] in ever: ever[event['action']].add(ident)
        return {'schema': VERSION, 'synthetic': True, 'programs': registered_programs(),
                'states': STATES, 'items': selected[offset:offset+limit], 'matched': len(selected), 'offset': offset, 'limit': limit,
                'summary': {'work_items': len(items), 'state_counts': dict(counts),
                            'unassigned': sum(not i['owner'] for i in items),
                            'evidence_changes_pending': sum(i['reassessment_required'] for i in items),
                            'overdue_open_work': sum(i['state'] not in TERMINAL and bool(i['due_date']) and i['due_date'] < date.today().isoformat() for i in items),
                            'ever_offered': len(ever['offer']), 'ever_enrolled': len(ever['enroll']),
                            'staff_reported_program_completions': len(ever['complete_program']),
                            'verified_clinical_goal_attainment': None,
                            'denominator': 'Persisted patient-program work items in the selected program, not all clinically eligible patients.'},
                'new_paid_api_calls': 0, 'external_actions_executed': 0, 'limits': LIMITS}
    finally:
        db.close()
