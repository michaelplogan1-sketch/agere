"""Received enrollment and eligibility reports with exact service/arrival clocks.

Reported payer identity is separate from current membership and covered benefits.
Ambiguous Medicare is never promoted to Original Medicare or Medicare Advantage.
"""
from pathlib import Path
from collections import Counter, defaultdict
import hashlib
import json
import sqlite3
from ...contracts import digest
from . import care_capabilities as care, record_context, context_builder

VERSION = 'received-coverage-context/1'
STREAMS = {'coverage', 'coverage_verifications'}
FIELDS = {'coverage_type', 'coverage_type_reported', 'status', 'effective_start', 'effective_end', 'enrollment_source',
          'plan_id', 'payer_organization_id', 'service_reference_date', 'benefit_year', 'verification_reason',
          'benefit_disclaimer', 'deductible_remaining_reported_cents', 'oop_remaining_reported_cents',
          'copay_reported_cents', 'coinsurance_rate_reported'}
PAYER_TYPES = {'original_medicare': 'original_medicare', 'medicare_advantage': 'medicare_advantage',
               'commercial': 'commercial', 'medicaid': 'medicaid', 'self_pay': 'self_pay'}


def identity():
    return digest([VERSION, hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])


def database(path):
    db = sqlite3.connect(Path(path)/'coverage-evidence.sqlite3', timeout=30)
    db.executescript('''CREATE TABLE IF NOT EXISTS contexts(id TEXT PRIMARY KEY,payload TEXT);
      CREATE TABLE IF NOT EXISTS matches(plan TEXT PRIMARY KEY,payload TEXT);
      CREATE TABLE IF NOT EXISTS handoffs(task TEXT,revision INTEGER,payload TEXT,PRIMARY KEY(task,revision));''')
    for table in ('contexts', 'matches', 'handoffs'):
        for op in ('UPDATE', 'DELETE'):
            db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{op} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'Coverage receipts are immutable'); END;")
    return db


def load(path, table, key):
    if table not in ('contexts', 'matches'): raise ValueError('Unknown coverage receipt kind')
    db = database(path)
    try: row = db.execute('SELECT payload FROM '+table+' WHERE '+('id' if table == 'contexts' else 'plan')+'=?', (key,)).fetchone()
    finally: db.close()
    return json.loads(row[0]) if row else None


def date_value(value):
    try: return care.day(value)
    except (ValueError, TypeError): return None


def project(record, asof, superseded):
    clean = context_builder.clean(record)
    declared = FIELDS & set(record.get('_warehouse_fields', record))
    fields = {k: clean[k] for k in sorted(declared) if k in clean and (clean[k] is None or type(clean[k]) in (str, bool, int, float))}
    raw_type = fields.get('coverage_type_reported', fields.get('coverage_type'))
    payer = PAYER_TYPES.get(raw_type, 'unknown') if isinstance(raw_type, str) else 'unknown'
    start = date_value(fields.get('effective_start')); end = date_value(fields.get('effective_end'))
    reference = date_value(fields.get('service_reference_date')); notes = []
    if record['source_table'] == 'coverage_verifications':
        clock = ('reported_for_decision_date' if reference == asof else 'historical_service_date' if reference and reference < asof else
                 'future_service_date' if reference else 'service_date_unknown')
        notes.append('Eligibility response applies to its stated service reference date; it does not verify benefits for another date.')
    else:
        clock = ('outside_reported_period' if (start and asof < start) or (end and asof > end) else
                 'within_reported_period' if start and end else 'enrollment_end_unknown' if start else 'enrollment_period_unknown')
        notes.append('Registration enrollment is a source report, not independent member eligibility verification.')
    if raw_type == 'medicare': notes.append('Medicare subtype is not supplied; Original Medicare versus Medicare Advantage remains unknown.')
    elif raw_type == 'uninsured': notes.append('No active plan reported is not proof of no coverage or a self-pay election.')
    if not fields.get('plan_id'): notes.append('Exact plan ID is not supplied by this record.')
    if superseded: notes.append('A received superseding record exists; select or review the successor.')
    return {'record_id': record['record_id'], 'record_hash': digest(record), 'patient_id': record['patient_id'],
            'source_table': record['source_table'], 'source_system': record.get('source_system'),
            'source_date': record.get('event_date'), 'available_date': record.get('available_date'),
            'fields': fields, 'unrepresented_fields': sorted(declared - set(fields)),
            'reported_payer_type': raw_type, 'routing_payer_type': payer,
            'exact_plan_id': fields.get('plan_id') if isinstance(fields.get('plan_id'), str) else '',
            'service_reference_date': reference, 'temporal_scope': clock, 'superseded_by': superseded,
            'caveats': notes, 'verified_current_membership': False, 'verified_benefits': False,
            'clinical_execution_authorized': False}


def prepare(path, request):
    care.exact(request, 'patient_id as_of')
    patient = care.text(request['patient_id'], 'patient ID', 150); asof = care.day(request['as_of'])
    received, meta = record_context.received(path, patient)
    rows = [r for r in received if r['source_table'] in STREAMS and r.get('available_date') and r['available_date'] <= asof]
    if len(rows) > 200: raise ValueError('More than 200 received coverage reports; explicit pagination is required before selection')
    successors = defaultdict(list)
    ids = {r['record_id'] for r in rows}
    for r in rows:
        if r.get('supersedes_record_id') in ids: successors[r['supersedes_record_id']].append(r['record_id'])
    reports = [project(r, asof, sorted(successors[r['record_id']])) for r in sorted(rows, key=lambda r: (r.get('available_date', ''), r['record_id']), reverse=True)]
    groups = defaultdict(list)
    for r in reports:
        if r['source_table'] == 'coverage_verifications' and r['service_reference_date'] and r['exact_plan_id'] and not r['superseded_by']:
            groups[(r['exact_plan_id'], r['service_reference_date'])].append(r)
    conflicts = [{'plan_id': plan, 'service_reference_date': day, 'record_ids': [r['record_id'] for r in group],
                  'reason': 'Different payer/status reports for the same plan and service date'}
                 for (plan, day), group in sorted(groups.items())
                 if len({digest([r['reported_payer_type'], r['fields'].get('status')]) for r in group}) > 1]
    row = {'schema': VERSION, 'parameters': request, 'patient_id': patient, 'as_of': asof,
           'source_snapshot': meta['snapshot'], 'source_digest': meta['source_digest'],
           'coverage_source_fingerprint': digest(sorted((r['record_id'], digest(r)) for r in rows)),
           'reports': reports, 'source_counts': dict(Counter(r['source_table'] for r in rows)), 'conflicts': conflicts,
           'exact_plan_ids_observed': sorted({r['exact_plan_id'] for r in reports if r['exact_plan_id']}),
           'missingness': 'No received report is not evidence of no coverage; service benefits, network and authorization remain unverified.',
           'clinical_execution_authorized': False, 'verified_benefits': False, 'code_identity': identity()}
    row['id'] = digest(row)
    db = database(path)
    try:
        with db: db.execute('INSERT OR IGNORE INTO contexts VALUES (?,?)', (row['id'], json.dumps(row, allow_nan=False)))
    finally: db.close()
    return row


def inspect(path, key):
    row = load(path, 'contexts', care.text(key, 'coverage context ID', 64))
    if not row: raise ValueError('Unknown received coverage context')
    if digest({k: v for k, v in row.items() if k != 'id'}) != key or prepare(path, row['parameters'])['id'] != key:
        raise care.work.WorkConflict('Coverage records, cutoff or interpretation changed; load the reports again')
    return row


def select(context, record_id):
    record_id = care.text(record_id, 'coverage record ID', 180)
    report = next((r for r in context['reports'] if r['record_id'] == record_id), None)
    if report is None: raise ValueError('Select one exact received coverage report for this patient and cutoff')
    if report['superseded_by'] or report['temporal_scope'] in ('future_service_date', 'outside_reported_period'):
        raise ValueError('Superseded or future/out-of-period report cannot supply this routing context')
    provenance = ('received-coverage:' + context['id'] + ':' + record_id + '. '
                  + report['temporal_scope'] + '; reported status ' + str(report['fields'].get('status'))
                  + '; no current membership, benefit or network verification inferred.')
    return {'context_id': context['id'], 'record_id': record_id, 'report': report,
            'routing_fields': {'payer_type': report['routing_payer_type'], 'plan_id': report['exact_plan_id'],
                               'as_of': context['as_of'], 'provenance': provenance},
            'conflicts': [v for v in context['conflicts'] if record_id in v['record_ids']],
            'source_selection_authority': 'operator_selected_report_not_current_benefit_verification'}
