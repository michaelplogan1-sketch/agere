"""Bind literal care accounts through received, typed, same-patient references.

Reuses native statement and clock contracts. No text matching, specialty-specific
handler, inferred attendance, or expansion of the caller's source scope.
"""
from collections import Counter, defaultdict
from pathlib import Path
import hashlib
import json
import sqlite3

from ...contracts import digest
from . import warehouse_interpretation as warehouse

VERSION = 'native-care-reference-assertions/1'
ALL = 'care requested services'
SERVICE_FIELDS = ('specialty', 'requested_specialty', 'referral_type', 'requested_service')
STREAMS = ('referrals', 'referral_status_updates', 'outside_report_status', 'imported_documents')
RELATIONS = ('care_order_plan', 'care_status_report', 'care_booking_report', 'care_completion')


def identity():
    return digest([hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), warehouse.identity()])


def service(record):
    # requested_service qualifies a specialty request rather than renaming it.
    values = [(f, ' '.join(record[f].replace('_', ' ').casefold().split()))
              for f in SERVICE_FIELDS if isinstance(record.get(f), str) and record[f].strip()]
    primary = [(f, v) for f, v in values if f != 'requested_service'] or values
    if not primary or len({v for _, v in primary}) != 1: return None
    field, value = primary[0]
    if len(value) > 100: return None
    return field, 'care '+value


def concepts(path):
    """Actual received request vocabulary, not a hand-picked specialty list."""
    db = sqlite3.connect((Path(path)/'stack.sqlite3').resolve().as_uri()+'?mode=ro', uri=True)
    try:
        found = {s[1] for (raw,) in db.execute("SELECT payload FROM records WHERE stream='referrals'")
                 if warehouse.native(r := json.loads(raw)) and (s := service(r))}
        return found | {ALL}
    finally: db.close()


def contracts():
    return {'version': VERSION, 'streams': list(STREAMS), 'all_services_concept': ALL,
        'scope': 'Exact observed specialty/requested_specialty/referral_type, normalized only for case and underscores. requested_service remains a qualifier.',
        'relations': list(RELATIONS),
        'meaning': 'Requests, literal status reports and reported bookings are distinct. care_completion means a source documents a performed service, with a same-patient referral and matching received imported report. It does not mean the requested episode, treatment or all follow-up is complete. false service_performed_confirmed is unknown completion, never negated attendance.',
        'use': 'Use source_state care_completion filter all to retain every patient and inspect separate care_accounts, requests, bookings, statuses and evidence gaps. care requested services covers all observed request types. For before-event questions use the same concept/streams in pre_event_state. Preserve report versus clinical service_date and each dependency receipt; no current-care, care-gap or risk inference.'}


def assess(records, requested, as_of):
    requested = set(requested)
    if not any(c.startswith('care ') for c in requested): return []
    ix = warehouse.index(records); rows = []
    for record in records:
        stream = record['source_table']
        if stream not in STREAMS[:3] or not warehouse.native(record): continue
        statement = warehouse.statement(record, as_of, ix)
        issues = list(statement['link_issues'])
        root = record if stream == 'referrals' else ix['records'].get(record.get('referral_record_id'))
        root_ok = (root is not None and root['source_table'] == 'referrals' and
                   root['patient_id'] == record['patient_id'] and warehouse.native(root) and warehouse.visible(root, as_of))
        if not root_ok:
            root = None
            issues.append({'field': 'referral_record_id', 'record_id': record.get('referral_record_id'), 'reason': 'request_reference_unresolved'})
        meaning = service(root) if root else None
        if not meaning: issues.append({'field': 'service', 'reason': 'request_service_unresolved'})
        selected = requested & ({ALL, meaning[1]} if meaning else {c for c in requested if c.startswith('care ')})
        if not selected: continue
        refs = list(statement['evidence'])
        if root:
            refs += [warehouse.reference(root, f) for f in SERVICE_FIELDS if f in root]
            refs.append(warehouse.reference(root, 'record_id'))
        clock = warehouse.clock(record, as_of)
        relation = 'care_order_plan' if stream == 'referrals' else 'care_status_report'
        field = meaning[0] if stream == 'referrals' and meaning else 'status'
        value = record.get(field)
        mapped = not issues and isinstance(value, str) and bool(value)

        def emit(rel, source_field, literal, problems=(), clinical=None):
            for concept in sorted(selected):
                rows.append({'record_id': record['record_id'], 'record_hash': digest(record),
                    'concept': concept, 'relation': rel, 'field': source_field, 'value': literal,
                    'status': 'mapped' if mapped and not problems else 'unresolved_reference_or_meaning',
                    'issues': issues + list(problems), 'request_id': root['record_id'] if root else None,
                    'service_concept': meaning[1] if meaning else None,
                    'request_values': {f: root[f] for f in warehouse.STATEMENTS['referrals'][2] if root and f in root},
                    'reference_evidence': list({digest(r): r for r in refs}.values()),
                    'clock': clinical or clock,
                    'meaning': 'Literal received '+rel.replace('_', ' ')+'; not verified clinical completion or closure of all requested care.'})

        emit(relation, field, value)
        if stream == 'referral_status_updates' and record.get('status') == 'appointment_reported_booked':
            emit('care_booking_report', 'status', record['status'])
        if stream == 'outside_report_status' and record.get('performed_service_documented') is True:
            report = ix['records'].get(record.get('imported_record_id'))
            problems = []
            valid = (report is not None and report['patient_id'] == record['patient_id'] and
                     report['source_table'] == 'imported_documents' and warehouse.native(report) and warehouse.visible(report, as_of))
            if not valid:
                problems.append({'field': 'imported_record_id', 'reason': 'received_report_reference_unresolved'})
            elif root is None or report.get('referral_record_id') != root['record_id']:
                problems.append({'field': 'referral_record_id', 'reason': 'report_request_reference_mismatch'})
            if record.get('status') != 'received': problems.append({'field': 'status', 'reason': 'report_receipt_not_confirmed'})
            clinical = warehouse.clock(report, as_of) if valid else clock
            if valid:
                refs += [warehouse.reference(report, f) for f in
                         ('record_id', 'referral_record_id', 'service_date', 'document_type', 'content_extent', 'reported_findings', 'original_record_id') if f in report]
            emit('care_completion', 'performed_service_documented', True, problems, clinical)
    return rows


def coverage(records, parameters, as_of):
    bindings = assess(records, parameters['concepts'], as_of)
    return {'binding_version': VERSION, 'counts': dict(Counter(b['status'] for b in bindings)),
        'unmapped_fields': [{k: v for k, v in b.items() if k != 'clock'}
                           for b in bindings if b['status'] != 'mapped'],
        'meaning': 'Unresolved request/report references are evidence gaps. Unconfirmed completion is not a negative attendance finding. Other care sources and narrative interpretation remain outside this binding.'}


def accounts(assertions, selected, pending, eligible, gaps):
    """Request-level inventory; never collapse different requests into one episode."""
    groups = defaultdict(list)
    selected_ids = {a['id'] for a in selected}; pending_ids = {a['assertion_id'] for a in pending}
    eligible_ids = {a['id'] for a in eligible}
    for a in assertions:
        if a.get('care_request_id'): groups[a['care_request_id']].append(a)
    output = []
    for key, rows in sorted(groups.items()):
        completion = [a for a in rows if a['relation'] == 'care_completion']
        qualified = [a for a in completion if a['authority'] == 'literal_structured_field' and
                     a['polarity'] == 'affirmed' and a['id'] in eligible_ids]
        output.append({'request_id': key, 'service_concept': rows[0]['care_service_concept'],
            'request_values': rows[0]['care_request_values'],
            'status': 'performed_service_documented' if qualified else 'completion_evidence_unresolved' if completion else 'completion_not_observed',
            'reports': [{'assertion_id': a['id'], 'record_id': a['source']['record_id'], 'relation': a['relation'],
                'value': a['value'], 'clocks': a['clocks'], 'selected_for_latest_state': a['id'] in selected_ids,
                'pending_for_selected_relation': a['id'] in pending_ids,
                'reference_evidence': a['reference_evidence']} for a in rows],
            'all_requested_care_complete': None, 'clinical_completion_verified': False})
    by_id = {a['request_id']: a for a in output}
    for gap in gaps:
        key = gap.get('request_id')
        if not key: continue  # Unknown request identity remains a patient-level gap.
        if key not in by_id:
            item = {'request_id': key, 'service_concept': gap['service_concept'],
                'request_values': gap['request_values'], 'reports': [],
                'status': 'request_meaning_unresolved' if not gap['service_concept'] else 'completion_evidence_unresolved',
                'all_requested_care_complete': None, 'clinical_completion_verified': False}
            by_id[key] = item; output.append(item)
        item = by_id[key]; item.setdefault('mapping_gaps', []).append(gap)
        if item['status'] == 'completion_not_observed': item['status'] = 'completion_evidence_unresolved'
    return sorted(output, key=lambda a: a['request_id'])
