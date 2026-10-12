"""Shared lifecycle for explicitly registered adapters; selected identities only.

Historical and functional engines remain frozen. New adapters reuse the common
storage and event contract but never bind unrelated registry entries into work.
"""
from collections import Counter
import hashlib,json
from pathlib import Path
from ...contracts import digest
from . import program_work_v1 as storage
from .program_work_v1 import (database,warehouse,metadata,record_refs,referenced_ids,
    load,append_event,bounded,checked_date,now,WorkConflict,STATES,TERMINAL,LIMITS,ACTION_FIELDS)

VERSION='registered-program-work/1'

class AdapterEngine:
    def __init__(self,adapter):self.adapter=adapter
    def definition(self,program):return self.adapter.definition(program)
    def analysis_rows(self,path,src,spec,patients=None):return self.adapter.rows(path,spec,patients)
    def code_identity(self,program):
        return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            storage.code_identity(),self.definition(program)])
    def preparation_identity(self,path,programs):
        src=warehouse(path)
        try:
            return digest([[p,self.code_identity(p),sorted(self.analysis_rows(path,src,self.definition(p)).items())]
                for p in sorted(set(programs))])
        finally:src.close()

    def prepare(self,path, program, patient_ids, *, input_membership=None, decision_contexts=None, accountability_contexts=None, synthesis_contexts=None):
        """Read all requested patients, store an immutable proposal, return a preview."""
        spec = self.definition(program); meta = metadata(path); src = warehouse(path)
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
            raw_rows = self.analysis_rows(path,src,spec,ids)
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
                assessment = {'schema': VERSION, 'engine_identity':self.code_identity(program), 'patient_id': pid, 'program': spec, 'source': meta,
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
                        'code_identity': self.code_identity(program), 'input_hashes': input_hashes,
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

    def materialize(self,path, proposal_id, actor):
        bounded(proposal_id, 'proposal ID', 64); actor = bounded(actor, 'actor', 120)
        db = database(path); src = warehouse(path)
        try:
            p = load(db, 'work_proposals', proposal_id)
            if p['code_identity'] != self.code_identity(p['program']['id']) or p['source'] != metadata(path) or p['program'] != self.definition(p['program']['id']):
                raise WorkConflict('Proposal source or definition changed; prepare a current proposal')
            current = self.analysis_rows(path,src,p['program'],[pid for pid,_ in p['input_hashes']])
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

    def change(self,path, request):
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
            if a.get('engine_identity')!=self.code_identity(a['program']['id']):raise WorkConflict('Registered program engine changed; refresh work before editing')
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
            if a['program']!=self.definition(a['program']['id']):raise WorkConflict('Program definition changed; refresh work before editing')
            raw = self.analysis_rows(path,src,a['program'],[item['patient_id']]).get(item['patient_id'])
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
