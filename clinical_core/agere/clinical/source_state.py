"""Typed documentary state from existing interpreted assertions.

The selected clock and relation are part of the query, never inferred from a
drug name or a missing row. This is a projection of the interpretation ledger,
not a replacement clinical truth store or a new reader.
"""
from collections import Counter
from pathlib import Path
import hashlib
import json
import re
import time
import zlib

from ...contracts import digest
from . import interpretation_state as interpretation
from . import assertion_reconciliation as assertions
from .clinical_events import iso

VERSION = 'documented-source-state/1'
CLOCKS = ('source_statement_date', 'explicit_clinical_date', 'literal_reference_date')
FILTERS = ('all', 'affirmed', 'negated', 'conflicting', 'unresolved')
RELATIONS = {
    'medication': ('reported_use', 'treatment_hold_stop', 'dose_change', 'prescription_order',
                   'dispense_record', 'administration_record', 'use_observed'),
    'care': assertions.native_care_assertions.RELATIONS,
    **{c: ('reported_barrier',) for c in assertions.native_assertions.CONCEPTS},
}
LIMITS = [
    'State describes the latest usable mapped documentary account on the explicitly selected clock, not current clinical truth.',
    'Orders, dispensing, reported use, administration, holds and dose changes are distinct relations.',
    'A negated report is not absence of all care. Missing, unreviewed, conflicting and unplaced evidence produce unknown membership.',
    'Care completion is a dated documentary account, not clinical verification or closure of every requested service. Native care accounts retain explicit same-patient request/report links; unlinked legacy accounts do not establish episode identity.',
    'A source review is a local self-reported interpretation, not authenticated clinical adjudication.',
    'Unmapped received content and unavailable streams remain outside the assessed assertions; no completeness claim is made.',
]


def validate(p):
    if not isinstance(p, dict) or set(p) != {'streams', 'concept', 'relation', 'clock', 'filter'}:
        raise ValueError('Explicit source-state streams, concept, relation, clock and filter required')
    if not isinstance(p['concept'], str): raise ValueError('Exact source-state concept required')
    family = p['concept'].split(' ', 1)[0] if p['concept'].startswith(('medication ', 'care ')) else p['concept']
    if family not in RELATIONS or p['relation'] not in RELATIONS[family]:
        raise ValueError('Unmapped concept/relation contract; no inferred clinical-state binding')
    if p['clock'] not in CLOCKS or p['filter'] not in FILTERS:
        raise ValueError('Explicit supported documentary clock and filter required')
    scope = assertions.validate({'streams': p['streams'], 'concepts': [p['concept']]})
    return {**p, 'streams': scope['streams']}


def scope(p):
    p = validate(p)
    return {'streams': p['streams'], 'concepts': [p['concept']], 'filter': 'all'}


def code_identity():
    return digest([VERSION, hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), interpretation.code_identity()])


def receipt_id(snap, patient, *, interpretation_code=None):
    return digest([interpretation_code or interpretation.code_identity(), digest([snap['base_identity'], patient]),
                   snap['revisions'].get(patient, digest([])), snap['stale'].get(patient, [])])


def project(receipt, parameters):
    p = validate(parameters)
    rows = [a for a in receipt['effective_assertions'] if a['concept'] == p['concept']]
    if any(a['patient_id'] != receipt['patient_id'] or a['source']['patient_id'] != receipt['patient_id'] for a in rows):
        raise ValueError('Cross-patient source-state assertion')
    related = [a for a in rows if a['relation'] != p['relation']]
    relevant = [a for a in rows if a['relation'] == p['relation']]
    timeline = assertions.reconcile(receipt['patient_id'], relevant, receipt['as_of'])
    excluded_by_id = {x['assertion_id']: x['reason'] for g in timeline for x in g['excluded_from_dated_accounts']}
    eligible, excluded, pending = [], [], []
    for a in relevant:
        reason = excluded_by_id.get(a['id'])
        basis, day = assertions.documentary_clock(a)
        if a['authority'] == 'literal_structured_field':
            # Both timestamps can exist. Selecting document time uses that actual
            # field, not the clinical date chosen by the general timeline reducer.
            field = {'source_statement_date':'source_date', 'explicit_clinical_date':'clinical_date',
                     'literal_reference_date':'reference_date'}[p['clock']]
            basis, day = p['clock'], a['clocks'].get(field)
            if reason in ('documentary_date_unresolved','future_documentary_date'): reason = None
            if not iso(day): reason = 'requested_date_not_observed'
            elif day > receipt['as_of']: reason = 'future_documentary_date'
        # Subject/time proposals are not established exclusions until reviewed.
        if reason == 'nonclinical_source_role':
            excluded.append({'assertion_id':a['id'],'reason':reason})
        elif a['authority'] == 'machine_annotation':
            pending.append({'assertion_id': a['id'], 'reason': 'unreviewed_interpretation', 'date_basis': basis, 'date': day})
        elif reason in ('not_patient_assertion', 'not_available_at_cutoff', 'future_documentary_date', 'repeated_text_cannot_refresh_state') or a['polarity'] in ('hypothetical', 'retracted'):
            excluded.append({'assertion_id': a['id'], 'reason': reason or a['polarity']})
        elif reason or basis != p['clock'] or not iso(day):
            pending.append({'assertion_id': a['id'], 'reason': reason or 'different_documentary_clock', 'date_basis': basis, 'date': day})
        else:
            eligible.append((day, a))
    source_review_problems = [g for g in receipt['source_groups'] if g['target']['concept'] == p['concept'] and
                             g['status'] in ('review_conflict', 'unresolved_review', 'incomplete_review_context')]
    # A reviewed relation may move an occurrence out of this binding entirely.
    # Unknown relation/subject/time cannot silently behave as a clinical negative.
    unknown_relations = [a['id'] for a in related if a['relation'] == 'unknown' and not assertions.source_structure.exclusion(a)]
    native_gaps = [x for x in receipt['coverage'].get('native_fields', {}).get('unmapped_fields', [])
                   if x['concept'] == p['concept'] and x['status'] != 'field_not_observed']
    native_gaps += [x for x in receipt['coverage'].get('native_care_fields', {}).get('unmapped_fields', [])
                   if x['concept'] == p['concept'] and x['relation'] in (p['relation'], 'care_order_plan')]
    newest = max((day for day, _ in eligible), default=None)
    selected = [a for day, a in eligible if day == newest]
    polarities = {a['polarity'] for a in selected}
    conflicting = len(polarities) > 1 or any(g['status'] == 'review_conflict' for g in source_review_problems)
    if conflicting:
        status, value = 'conflicting', None
    elif pending or source_review_problems or unknown_relations or native_gaps or receipt['stale_readings']:
        status, value = 'unresolved_interpretation', None
    elif selected:
        status, value = 'documented', next(iter(polarities))
    else:
        status, value = 'unobserved', None
    steps = []
    if conflicting: steps.append('Reconcile the opposing source accounts or explicit reviews; no vote establishes the answer.')
    if pending or source_review_problems or unknown_relations:
        steps.append('Resolve the listed interpretation, relationship and date uncertainties against their exact source passages.')
    if receipt['stale_readings']: steps.append('Refresh readings whose source or interpretation version changed.')
    if not selected:
        steps.append('Obtain or interpret evidence for '+p['relation'].replace('_', ' ')+' on the '+p['clock'].replace('_', ' ')+'.')
    if receipt['coverage']['units_without_in_scope_assertions'] or receipt['coverage']['streams_without_records']:
        steps.append('Establish source coverage and inspect unmapped content; an unavailable record does not establish missing care.')
    care_accounts = assertions.native_care_assertions.accounts(rows, selected, pending, [a for _, a in eligible], native_gaps) if p['relation'] == 'care_completion' else []
    if care_accounts and any(a['status'] != 'performed_service_documented' for a in care_accounts):
        steps.append('Inspect each request separately and acquire missing completion evidence; a booking, completed conversation or false confirmation flag does not establish service attendance or its absence.')
    return {'schema': VERSION, 'synthetic': True, 'patient_id': receipt['patient_id'], 'as_of': receipt['as_of'],
            'parameters': {k:v for k,v in p.items() if k != 'filter'}, 'interpretation_receipt_id': receipt['id'],
            'status': status, 'value': value, 'selected_date': newest, 'date_basis': p['clock'],
            'selected_assertions': selected, 'prior_assertion_ids': [a['id'] for day,a in eligible if day != newest],
            'pending_assertions': pending, 'excluded_assertions': excluded,
            'source_review_problems': source_review_problems, 'unknown_relation_assertions': unknown_relations,
            'native_mapping_gaps': native_gaps,
            'care_accounts': care_accounts,
            'care_account_counts': dict(Counter(a['status'] for a in care_accounts)),
            'care_report_counts': dict(Counter(r['relation'] for a in care_accounts for r in a['reports'])),
            'care_account_meaning': 'Each explicitly linked request is separate. Latest documentary state is not the completion status of all requests. Unobserved evidence is not missing care.',
            'related_evidence': [{'assertion_id': a['id'], 'relation': a['relation'], 'authority': a['authority']} for a in related],
            'source_coverage': receipt['coverage'], 'next_evidence_steps': steps,
            'counts': {'selected_assertions': len(selected), 'pending_assertions': len(pending),
                       'related_assertions': len(related), 'excluded_assertions': len(excluded)},
            'clinical_eligibility_established': False, 'current_clinical_state_established': False,
            'episode_identity_established': False, 'limits': LIMITS}


def membership(row, requested):
    if requested == 'all': return True
    if requested == 'conflicting': return row['status'] == 'conflicting'
    if requested == 'unresolved': return row['status'] != 'documented'
    if row['status'] != 'documented': return None
    return row['value'] == requested


def storage(path):
    db = interpretation.storage(path)
    db.execute('CREATE TABLE IF NOT EXISTS source_states(id TEXT PRIMARY KEY, patient TEXT NOT NULL, payload BLOB NOT NULL)')
    return db


def execute(path, patient_ids, parameters, *, prepared=None, preview_limit=6):
    started = time.perf_counter(); p = validate(parameters); ids = sorted(set(patient_ids))
    if type(preview_limit) is not int or not 0 <= preview_limit <= 20: raise ValueError('Bounded source-state preview required')
    snap = prepared or interpretation.snapshot(path, scope(p))
    _, base = interpretation.execute(path, ids, scope(p), prepared=snap, preview_limit=0)
    store = storage(path); yes, unknown = set(), set(); statuses, counts = Counter(), Counter(); coverage_counts = Counter(dict.fromkeys(('patients_without_scoped_records', 'patients_with_unmapped_received_units', 'patients_with_absent_selected_streams', 'received_source_units', 'units_without_in_scope_assertions'), 0)); field_counts = Counter(); care_counts = Counter(); care_reports = Counter(); preview = []
    projection = {k:v for k,v in p.items() if k != 'filter'}; code = code_identity();interpretation_code=interpretation.code_identity()
    try:
        with store:
            for patient in ids:
                interpreted_id = receipt_id(snap, patient,interpretation_code=interpretation_code)
                key = digest([code, interpreted_id, projection])
                found = store.execute('SELECT payload FROM source_states WHERE id=?', (key,)).fetchone()
                if found:
                    row = json.loads(zlib.decompress(found[0])); counts['patient_cache_hits'] += 1
                else:
                    raw = store.execute('SELECT payload FROM receipts WHERE id=?', (interpreted_id,)).fetchone()
                    if raw is None: raise ValueError('Source interpretation receipt missing')
                    row = project(json.loads(zlib.decompress(raw[0])), p)
                    row.update(id=key, code_identity=code)
                    store.execute('INSERT INTO source_states VALUES (?,?,?)', (key, patient, zlib.compress(json.dumps(row).encode())))
                    counts['patient_states_computed'] += 1
                cov = row['source_coverage']
                coverage_counts.update({
                    'patients_without_scoped_records': int(not cov['received_source_units']),
                    'patients_with_unmapped_received_units': int(bool(cov['units_without_in_scope_assertions'])),
                    'patients_with_absent_selected_streams': int(bool(cov['streams_without_records'])),
                    'received_source_units': cov['received_source_units'],
                    'units_without_in_scope_assertions': cov['units_without_in_scope_assertions']})
                field_counts.update(cov.get('native_fields', {}).get('counts', {}))
                care_counts.update(row['care_account_counts'])
                care_reports.update(row['care_report_counts'])
                status = row['status'] if row['status'] != 'documented' else 'documented_'+row['value']
                statuses[status] += 1; counts.update(row['counts'])
                match = membership(row, p['filter'])
                if match is True: yes.add(patient)
                elif match is None: unknown.add(patient)
                candidate = {k:row[k] for k in ('id','patient_id','status','value','selected_date','date_basis','counts','next_evidence_steps')}
                candidate['membership'] = 'matched' if match else 'unknown' if match is None else 'not_matched'
                candidate['care_account_counts'] = row['care_account_counts']
                # Keep matched and unresolved examples labelled; never imply all
                # visible rows belong to a downstream matched-only population.
                preview = sorted(preview+[candidate], key=lambda r:(r['membership']!='matched', r['status']!='conflicting', r['patient_id']))[:preview_limit]
        if interpretation.snapshot(path, scope(p))['identity'] != snap['identity']:
            raise ValueError('Source reviews changed during state projection; rerun')
    finally: store.close()
    return yes, unknown, {'schema': VERSION, 'synthetic': True, 'parameters': p,
        'identity': digest([code, snap['identity'], p]), 'patients_evaluated': len(ids),
        'care_account_counts': dict(care_counts),
        'care_report_counts': dict(care_reports),
        'status_counts': dict(statuses), 'processing_counts': dict(counts),
        'interpretation_processing_counts': base['counts'],
        'evidence_coverage': {**dict(coverage_counts), 'native_field_counts': dict(field_counts),
            'complete_semantic_interpretation': False,
            'meaning': 'Absent records, unmapped received units and unobserved fields are distinct. Documentary negatives negate only the mapped report.'}, 'patients': preview,
        'population_digest': digest(ids), 'all_patient_receipts_persisted': True,
        'preview_scope': 'Membership is labelled on each example; only matched patients feed downstream operators.',
        'new_paid_api_calls': 0, 'elapsed_seconds': time.perf_counter()-started,
        'clinical_eligibility_established': False, 'limits': LIMITS}


def inspect(path, key):
    if not isinstance(key, str) or not re.fullmatch('[a-f0-9]{64}', key): raise ValueError('Exact source-state receipt required')
    db = storage(path)
    try: found = db.execute('SELECT payload FROM source_states WHERE id=?', (key,)).fetchone()
    finally: db.close()
    if found is None: raise ValueError('Unknown source-state receipt')
    row = json.loads(zlib.decompress(found[0]))
    if row['code_identity'] != code_identity(): raise ValueError('Source-state contract changed; rebuild')
    row['interpretation'] = interpretation.inspect(path, row['interpretation_receipt_id'])
    return row
