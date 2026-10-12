"""Retrospective documentary projection before an explicit observed event.

Reuses outcome selection and source-state reduction. Neither the original index
definition nor an experimental target is changed. Calendar-day ordering is strict.
"""
from collections import Counter
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import re
import time
import zlib

from ...contracts import digest
from . import source_state as state, interpretation_state as interpretation, outcomes
from .clinical_events import iso
from .decision_landmarks import clinical_clock_available

VERSION = 'pre-event-documentary-state/1'
LIMITS = state.LIMITS + [
    'Retrospective reconstruction from the current received snapshot; superseded or quarantined history and historical system knowledge are not reconstructed.',
    'Both the selected documentary date and receipt must precede the index day. Same-day ordering and missing clocks remain unresolved; no timestamp fallback.',
    'Later records are retained as excluded evidence, never pre-event features. Current source reviews lack time-qualified review-context lineage and remain unresolved here.',
    'The index follows the exact upstream outcome definition. Source notifications are not verified clinical stays, and these states do not establish causality, risk or missing care.',
]
FIELDS = {'source_statement_date': 'source_date', 'explicit_clinical_date': 'clinical_date',
          'literal_reference_date': 'reference_date'}


def code_identity():
    return digest([VERSION, hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   state.code_identity(), outcomes.code_identity(),
                   hashlib.sha256(Path(__file__).with_name('decision_landmarks.py').read_bytes()).hexdigest()])


def timing(day, arrived, index_day, available_by):
    """Known exclusions precede uncertainty; no late row can poison prior state."""
    if iso(arrived) and arrived > available_by: return 'after_receipt_cutoff'
    if iso(day) and day > index_day: return 'documented_after_index'
    if iso(arrived) and arrived > index_day: return 'received_after_index'
    if not iso(arrived): return 'unknown_receipt_date'
    if not iso(day): return 'unknown_documentary_date'
    if day == index_day or arrived == index_day: return 'same_day_order_unknown'
    return 'eligible'


EXCLUDED = {'after_receipt_cutoff', 'documented_after_index', 'received_after_index'}


def reference_timing(assertion, index_day, cutoff):
    """A linked account cannot borrow a future request, report or receipt."""
    reasons = []
    for ref in assertion.get('reference_evidence', []):
        if ref['patient_id'] != assertion['patient_id']: raise ValueError('Cross-patient assertion reference')
        reasons.append(timing(ref.get('source_date'), ref.get('arrival_date'), index_day, cutoff))
    return next((r for r in reasons if r in EXCLUDED), next((r for r in reasons if r != 'eligible'), 'eligible'))


def project(receipt, anchor, spec, anchor_parameters, parameters):
    p = state.validate(parameters); pid = receipt['patient_id']
    if anchor['patient_id'] != pid: raise ValueError('Cross-patient temporal anchor')
    if any(a['patient_id'] != pid or a['source']['patient_id'] != pid for a in receipt['effective_assertions']):
        raise ValueError('Cross-patient temporal assertion')
    if any(s['patient_id'] != pid for s in receipt['sources'].values()):
        raise ValueError('Cross-patient temporal source')
    cutoff = anchor_parameters['available_by']; index = anchor.get('index'); anchor_problem = None
    if not index or anchor.get('indexed') is not True:
        anchor_problem = anchor['status']
    elif not iso(index.get('date')) or index['date'] > cutoff:
        anchor_problem = 'index_date_unavailable_at_cutoff'
    else:
        evidence = [anchor['evidence'][k] for k in index['evidence_ids']]
        if any(e['patient_id'] != pid or e['source']['patient_id'] != pid for e in evidence):
            raise ValueError('Cross-patient index evidence')
        if any(not iso(e.get('arrival_date')) or e['arrival_date'] > cutoff for e in evidence):
            anchor_problem = 'index_receipt_unavailable_at_cutoff'
        elif spec['basis'] == 'clinical' and any(not clinical_clock_available(e, cutoff) for e in evidence):
            anchor_problem = 'clinical_index_clock_unavailable_at_cutoff'
    day = index['date'] if index and not anchor_problem else None
    reduced = deepcopy(receipt); kept, excluded, pending = [], [], []
    source_timing = {rid: timing(s.get('event_date'), s.get('available_date'), day, cutoff)
                     if day else 'anchor_unresolved' for rid, s in receipt['sources'].items()}
    for a in receipt['effective_assertions']:
        if a['concept'] != p['concept']: continue
        reason = timing(a['clocks'].get(FIELDS[p['clock']]), a['clocks'].get('arrival_date'), day, cutoff) if day else 'anchor_unresolved'
        dependency_reason = reference_timing(a, day, cutoff) if day else 'anchor_unresolved'
        if reason not in EXCLUDED and (reason == 'eligible' or dependency_reason in EXCLUDED): reason = dependency_reason
        detail = {'assertion_id': a['id'], 'record_id': a['source']['record_id'], 'reason': reason,
                  'documentary_date': a['clocks'].get(FIELDS[p['clock']]), 'arrival_date': a['clocks'].get('arrival_date')}
        if reason in EXCLUDED: excluded.append(detail)
        elif reason != 'eligible': pending.append(detail)
        elif a['authority'] == 'source_review':
            pending.append({**detail, 'reason': 'review_context_not_time_qualified'})
        elif p['clock'] == 'explicit_clinical_date' and a.get('clinical_date_status') != 'explicit':
            pending.append({**detail, 'reason': 'clinical_clock_dependency_not_time_qualified'})
        else: kept.append(a)
    reduced['effective_assertions'] = kept
    reduced['source_groups'] = [g for g in receipt['source_groups']
        if source_timing.get(g['target']['record_id']) not in EXCLUDED]
    native = reduced['coverage'].get('native_fields', {})
    native['unmapped_fields'] = [g for g in native.get('unmapped_fields', [])
        if not anchor_problem and source_timing.get(g['record_id']) not in EXCLUDED]
    care = reduced['coverage'].get('native_care_fields', {})
    care['unmapped_fields'] = [g for g in care.get('unmapped_fields', [])
        if not anchor_problem and source_timing.get(g['record_id']) not in EXCLUDED]
    row = state.project(reduced, p)
    relevant_pending = [a for a in pending if next(x for x in receipt['effective_assertions'] if x['id'] == a['assertion_id'])['relation'] in (p['relation'], 'unknown')]
    if anchor_problem or relevant_pending:
        row.update(status='temporal_anchor_unresolved' if anchor_problem else 'unresolved_temporal_evidence', value=None)
    row['pending_assertions'].extend(relevant_pending); row['excluded_assertions'].extend(excluded)
    row['counts'].update(pending_assertions=len(row['pending_assertions']), excluded_assertions=len(row['excluded_assertions']))
    row.update(schema=VERSION, outcome_receipt_id=anchor['id'], anchor={
        'status': anchor['status'], 'problem': anchor_problem, 'index': index,
        'basis': spec['basis'], 'contract': spec['id'], 'parameters': anchor_parameters},
        temporal_scope={'before_date_exclusive': day, 'available_by': cutoff,
            'ordering': 'strictly_before_index_day', 'interpretation': 'retrospective_current_snapshot',
            'historical_system_knowledge_established': False},
        temporal_evidence={'assertion_reasons': dict(Counter(x['reason'] for x in excluded+pending)),
            'eligible_assertions': len(kept), 'source_record_timing': source_timing,
            'source_record_counts': dict(Counter(source_timing.values()))},
        # This is deliberately the snapshot inventory, not a manufactured count
        # of historical units (which can contain several assertions per record).
        source_coverage={**receipt['coverage'], 'inventory_scope': 'current_snapshot_including_excluded_records'},
        limits=LIMITS)
    if anchor_problem: row['next_evidence_steps'].insert(0, 'Resolve the event index and its selected clock before reconstructing prior evidence.')
    if relevant_pending: row['next_evidence_steps'].insert(0, 'Resolve the listed receipt/date ordering or time-qualified interpretation dependencies.')
    return row


def storage(path):
    db = state.storage(path)
    db.execute('CREATE TABLE IF NOT EXISTS pre_event_states(id TEXT PRIMARY KEY, patient TEXT NOT NULL, payload BLOB NOT NULL)')
    return db


def execute(path, patient_ids, parameters, *, anchor_prepared, prepared=None, preview_limit=6):
    started = time.perf_counter(); p = state.validate(parameters); ids = sorted(set(patient_ids))
    if type(preview_limit) is not int or not 0 <= preview_limit <= 20: raise ValueError('Bounded temporal preview required')
    if anchor_prepared['parameters']['filter'] != 'all': raise ValueError('Pre-event state requires all-state outcome retention')
    snap = prepared or interpretation.snapshot(path, state.scope(p))
    _, base = interpretation.execute(path, ids, state.scope(p), prepared=snap, preview_limit=0)
    outcomes.execute(path, ids, anchor_prepared['parameters'], prepared=anchor_prepared, preview_limit=0)
    code = code_identity(); interp_code = interpretation.code_identity()
    projection = {k:v for k,v in p.items() if k != 'filter'}
    db = storage(path); odb = outcomes.storage(path); yes, unknown = set(), set(); preview = []
    statuses, processing, reasons, coverage, care_counts, care_reports = Counter(), Counter(), Counter(), Counter(), Counter(), Counter()
    try:
        with db:
            for pid in ids:
                iid = state.receipt_id(snap, pid, interpretation_code=interp_code); oid = outcomes.receipt_id(anchor_prepared, pid)
                key = digest([code, iid, oid, projection]); found = db.execute('SELECT payload FROM pre_event_states WHERE id=?', (key,)).fetchone()
                if found:
                    row = json.loads(zlib.decompress(found[0])); processing['patient_cache_hits'] += 1
                else:
                    receipt = json.loads(zlib.decompress(db.execute('SELECT payload FROM receipts WHERE id=?', (iid,)).fetchone()[0]))
                    anchor = json.loads(zlib.decompress(odb.execute('SELECT payload FROM receipts WHERE id=?', (oid,)).fetchone()[0]))
                    row = project(receipt, anchor, anchor_prepared['contract'], anchor_prepared['parameters'], p)
                    row.update(id=key, code_identity=code)
                    db.execute('INSERT INTO pre_event_states VALUES (?,?,?)', (key, pid, zlib.compress(json.dumps(row).encode())))
                    processing['patient_states_computed'] += 1
                statuses[row['status'] if row['status'] != 'documented' else 'documented_'+row['value']] += 1
                reasons.update(row['temporal_evidence']['assertion_reasons'])
                care_counts.update(row['care_account_counts'])
                care_reports.update(row['care_report_counts'])
                cov = row['source_coverage']; coverage.update({
                    'patients_without_scoped_records': int(not cov['received_source_units']),
                    'patients_with_unmapped_received_units': int(bool(cov['units_without_in_scope_assertions'])),
                    'patients_without_eligible_prior_assertions': int(not row['temporal_evidence']['eligible_assertions']),
                    'patients_with_unresolved_anchor': int(bool(row['anchor']['problem'])),
                    'eligible_prior_assertions': row['temporal_evidence']['eligible_assertions']})
                match = state.membership(row, p['filter'])
                if match is True: yes.add(pid)
                elif match is None: unknown.add(pid)
                sample = {k:row[k] for k in ('id','patient_id','status','value','selected_date','date_basis','anchor','temporal_scope','counts','next_evidence_steps')}
                sample['membership'] = 'matched' if match is True else 'unknown' if match is None else 'not_matched'
                sample['care_account_counts'] = row['care_account_counts']
                preview = sorted(preview+[sample], key=lambda x:(x['status'] != 'documented', x['patient_id']))[:preview_limit]
        if interpretation.snapshot(path, state.scope(p))['identity'] != snap['identity']:
            raise ValueError('Source reviews changed during pre-event projection; rerun')
    finally: db.close(); odb.close()
    return yes, unknown, {'schema':VERSION, 'synthetic':True, 'parameters':p,
        'identity':digest([code, snap['identity'], anchor_prepared['identity'], p]),
        'patients_evaluated':len(ids), 'population_digest':digest(ids), 'status_counts':dict(statuses),
        'care_account_counts':dict(care_counts),
        'care_report_counts':dict(care_reports),
        'processing_counts':dict(processing), 'interpretation_processing_counts':base['counts'],
        'evidence_coverage':{**dict(coverage), 'inventory_scope':'current_snapshot_including_excluded_records',
            'complete_semantic_interpretation':False}, 'temporal_exclusion_counts':dict(reasons),
        'anchor_contract':anchor_prepared['contract'], 'anchor_parameters':anchor_prepared['parameters'],
        'patients':preview, 'all_patient_receipts_persisted':True, 'new_paid_api_calls':0,
        'elapsed_seconds':time.perf_counter()-started, 'limits':LIMITS}


def inspect(path, key):
    if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{64}',key): raise ValueError('Exact pre-event receipt required')
    db=storage(path)
    try: found=db.execute('SELECT payload FROM pre_event_states WHERE id=?',(key,)).fetchone()
    finally: db.close()
    if found is None: raise ValueError('Unknown pre-event receipt')
    row=json.loads(zlib.decompress(found[0]))
    if row['code_identity'] != code_identity(): raise ValueError('Pre-event contract changed; rebuild')
    row['interpretation']=interpretation.inspect(path,row['interpretation_receipt_id'])
    row['outcome']=outcomes.inspect(path,row['outcome_receipt_id'])
    return row
