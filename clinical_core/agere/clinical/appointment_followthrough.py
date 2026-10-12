"""Explicit received appointment dispositions, never absence-derived attendance."""
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path
import hashlib, json, re
from ...contracts import digest
from . import operational_context as O, evidence_threads as T, case_passes

VERSION = 'appointment-followthrough/1'
PROGRAM = 'appointment_followthrough_source_review'
CONTRACT = {'id': 'first_received_booking_terminal_7d_receipt_14d', 'receipt_from': '2026-01-01',
    'event_days_after_scheduled': 7, 'receipt_days_after_scheduled': 14,
    'index': 'earliest_booking_receipt_day_one_identity', 'decision': 'next_calendar_day',
    'profile': 'received_prior_180d', 'positive': 'fulfilled', 'negative': 'no_show',
    'cancelled': 'competing_status_excluded', 'missing': 'unknown_never_negative'}
FIELDS = ('appointment_id', 'scheduled_date', 'status', 'completed_encounter_id')
LIMITS = [
    'Received appointment dispositions are documentary observations, not independently verified attendance, completed treatment or patient behavior.',
    'One earliest received booking per patient; ambiguous indices are not replaced by a later booking. Exact source identity is required.',
    'Fulfilled and no_show require explicit terminal reports within the declared event and receipt windows. Cancellation is separate; absent follow-up never becomes a negative.',
    'All binary labels require mature scheduled-date + 14-day receipt follow-up. Later reports remain excluded; historical versions discarded upstream cannot be reconstructed.',
    'Seven-day event and fourteen-day receipt windows are engineering assumptions. Source capture, clinical appropriateness and external validity remain unknown.',
    'No diagnosis, clinical risk score, patient outreach, plan entitlement or automatic action is authorized. Review work is local and unassigned.'
]

def identity():
    return digest([VERSION, CONTRACT, hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), O.identity()])

def definition(program):
    if program != PROGRAM: raise ValueError('Registered appointment review required')
    return {'id': PROGRAM, 'name': 'Received appointment follow-through review', 'operator': PROGRAM,
            'version': VERSION, 'definition_identity': identity(), 'purpose': 'Reconcile received booking and follow-through evidence.'}

def project(r):
    return {**{k: r.get(k) for k in O.COMMON}, 'fields': {k: r.get(k) for k in FIELDS}, 'record_hash': digest(r)}

def assess(pid, records, cutoff):
    if not O.day(cutoff): raise ValueError('Canonical receipt cutoff required')
    if len({r['record_id'] for r in records}) != len(records): raise ValueError('Distinct source records required')
    for r in records:
        if r.get('patient_id') != pid or r.get('source_table') != 'appointments' or r.get('synthetic') is not True or r.get('identity_status') != 'matched':
            raise ValueError('Matched same-patient synthetic appointments required')
    received = [r for r in records if O.day(r.get('available_date')) and r['available_date'] <= cutoff]
    unknown = [r for r in records if not O.day(r.get('available_date'))]
    out = {'patient_id': pid, 'status': 'no_received_booking', 'index': None, 'decision': None, 'window': None,
           'recorded_label': None, 'terminal_status': None, 'calendar_mature': False,
           'sources': [project(r) for r in sorted(received + unknown, key=lambda r: r['record_id'])],
           'history_record_ids': [], 'terminal_record_ids': [], 'excluded_reports': [], 'issues': [],
           'clinical_outcome_established': False, 'capture_complete': False}
    def stop(status, issues=None):
        out['status'] = status
        if issues: out['issues'] = sorted(set(issues))
        return out
    if any(r.get('status') == 'booked' for r in unknown): return stop('unknown_booking_receipt')
    booked = [r for r in received if r.get('status') == 'booked' and CONTRACT['receipt_from'] <= r['available_date']]
    if not booked: return out
    firstday = min(r['available_date'] for r in booked)
    first = [r for r in booked if r['available_date'] == firstday]
    keys = {O.source_key(r, r.get('appointment_id')) for r in first}
    if None in keys: return stop('incomplete_index_identity')
    if len(keys) != 1: return stop('ambiguous_first_booking')
    key = next(iter(keys)); dates = {r.get('scheduled_date') for r in first}
    if len(dates) != 1 or not O.day(next(iter(dates))): return stop('unknown_or_conflicting_index_schedule')
    scheduled = next(iter(dates))
    if any(not O.day(r.get('event_date')) or r['event_date'] > firstday for r in first): return stop('unknown_or_future_booking_event')
    if scheduled <= firstday: return stop('booking_not_received_before_scheduled_date')
    decision = (O.day(firstday) + timedelta(days=1)).isoformat()
    end = (O.day(scheduled) + timedelta(days=CONTRACT['event_days_after_scheduled'])).isoformat()
    deadline = (O.day(scheduled) + timedelta(days=CONTRACT['receipt_days_after_scheduled'])).isoformat()
    out.update(index={'source_identity': list(key), 'receipt_date': firstday, 'scheduled_date': scheduled,
                      'record_ids': sorted(r['record_id'] for r in first)},
               decision={'date': decision, 'feature_cutoff': firstday},
               window={'event_start': scheduled, 'event_end': end, 'receipt_end': deadline, 'available_by': cutoff},
               calendar_mature=deadline <= cutoff)
    history = [r for r in received + unknown if O.source_key(r, r.get('appointment_id')) == key]
    out['history_record_ids'] = sorted(r['record_id'] for r in history)
    terminal = []; issues = []
    for r in history:
        arrival, event, status = r.get('available_date'), r.get('event_date'), r.get('status')
        if not O.day(arrival): issues.append('unknown_same_identity_receipt'); continue
        if arrival > deadline:
            out['excluded_reports'].append({'record_id': r['record_id'], 'reason': 'received_after_frozen_followup'}); continue
        if r.get('scheduled_date') != scheduled: issues.append('changed_or_unknown_schedule')
        if status not in ('booked', 'arrived', 'fulfilled', 'no_show', 'cancelled'): issues.append('unmapped_status'); continue
        if status in ('booked', 'arrived'): continue
        if arrival <= firstday: issues.append('terminal_already_known_at_decision')
        if not O.day(event): issues.append('unknown_terminal_event'); continue
        if event > arrival: issues.append('terminal_event_after_receipt')
        start = decision if status == 'cancelled' else scheduled
        if not start <= event <= end:
            issues.append('terminal_outside_declared_event_window'); continue
        terminal.append(r)
    out['terminal_record_ids'] = sorted(r['record_id'] for r in terminal)
    statuses = {r['status'] for r in terminal}
    if len(statuses) > 1: issues.append('conflicting_terminal_statuses')
    signatures = {(r['status'], r['event_date'], r.get('completed_encounter_id')) for r in terminal}
    if len(signatures) > 1 and len(statuses) == 1: issues.append('ambiguous_terminal_history')
    if issues: return stop('unresolved_followthrough', issues)
    if terminal: out['terminal_status'] = terminal[0]['status']
    if not out['calendar_mature']: return stop('incomplete_followup')
    if not terminal: return stop('no_explicit_terminal_report')
    if out['terminal_status'] == 'cancelled': return stop('explicit_cancelled')
    out['recorded_label'] = int(out['terminal_status'] == 'fulfilled')
    return stop('explicit_' + out['terminal_status'])

def load_rows(path, patients=None):
    db, meta = T.connect(path)
    try:
        db.execute('BEGIN'); universe = {r[0] for r in db.execute('SELECT id FROM patients')}
        ids = sorted(universe if patients is None else set(patients))
        if not set(ids) <= universe: raise ValueError('Unknown appointment population patient')
        rows = []
        for pid in ids:
            records = [json.loads(r[0]) for r in db.execute("SELECT payload FROM records WHERE patient=? AND stream='appointments' ORDER BY id", (pid,))]
            rows.append(assess(pid, records, meta['snapshot']['as_of']))
        return meta, rows
    finally: db.close()

def rows(path, spec, patients=None):
    if spec != definition(PROGRAM): raise ValueError('Appointment definition changed')
    meta, result = load_rows(path, patients)
    return {r['patient_id']: json.dumps({'synthetic': True, 'patient_id': r['patient_id'], 'source_run': meta['source_run'],
        'operator': PROGRAM, 'appointment_followthrough': r, 'record_ids': [s['record_id'] for s in r['sources']],
        'workflow': {'as_of': meta['snapshot']['as_of'], 'status': r['status'], 'review_candidate': True,
        'missing_information': ['Verify the reported disposition and any conflicting or missing source history.',
            'Confirm patient needs, consent, responsible team and available services before any action.'],
        'clinical_eligibility_established': False, 'authority': 'received_source_review_only'}}, sort_keys=True, allow_nan=False)
        for r in result if r['sources']}

def build(path, patients=None):
    meta, rr = load_rows(path, patients)
    r = {'schema': VERSION, 'synthetic': True, 'question': 'What follow-through is explicitly reported after the first received booking?',
         'code_identity': identity(), 'source': meta, 'contract': CONTRACT, 'patient_ids': [x['patient_id'] for x in rr],
         'patients': rr, 'status_counts': dict(Counter(x['status'] for x in rr)),
         'label_counts': dict(Counter(str(x['recorded_label']) for x in rr if x['recorded_label'] is not None)),
         'limits': LIMITS, 'clinical_prediction_authorized': False, 'new_paid_api_calls': 0}
    r['id'] = digest(r); return r

def location(path, key):
    if not isinstance(key, str) or not re.fullmatch('[a-f0-9]{64}', key): raise ValueError('Exact appointment review ID required')
    return Path(path)/'appointment-followthrough'/(key + '.json')

def prepare(path, request):
    if not isinstance(request, dict) or set(request) != {'patient_ids'}: raise ValueError('Exact appointment population required')
    ids = request['patient_ids']
    if ids is not None and (not isinstance(ids, list) or not ids or any(not isinstance(p,str) or not p or len(p)>200 for p in ids) or len(ids)!=len(set(ids))):
        raise ValueError('Distinct patient IDs or null for the complete population required')
    r = build(path, ids); p = location(path, r['id'])
    if not p.exists(): case_passes.atomic_write(p, r)
    return r

def inspect(path, key):
    p = location(path, key)
    if not p.exists(): raise ValueError('Unknown appointment review')
    r = json.loads(p.read_text())
    if r.get('id') != key or digest({k:v for k,v in r.items() if k!='id'}) != key: raise ValueError('Appointment receipt integrity failed')
    if build(path, r['patient_ids'])['id'] != key: raise ValueError('Appointment sources or implementation changed')
    return r

def history(path):
    result = []
    for p in sorted((Path(path)/'appointment-followthrough').glob('*.json')):
        r = json.loads(p.read_text())
        result.append({k:r[k] for k in ('id','question','patient_ids','status_counts','label_counts')})
    return result

def summary(r):
    return {k:v for k,v in r.items() if k!='patients'} | {'patients': [
        {k:p[k] for k in ('patient_id','status','index','decision','window','recorded_label','terminal_status','calendar_mature','issues')} |
        {'source_count':len(p['sources'])} for p in r['patients']]}

def prepare_work(path, request):
    from . import program_work
    if not isinstance(request,dict) or set(request)!={'review_id','patient_id'}: raise ValueError('Exact review and selected patient required')
    r = inspect(path, request['review_id']); pid = request['patient_id']
    if pid not in r['patient_ids']: raise ValueError('Patient not in appointment review')
    result = program_work.prepare(path, PROGRAM, [pid], input_membership={'appointment_review_id': r['id'], 'authority': 'source_review_only'})
    inspect(path, r['id']); return result
