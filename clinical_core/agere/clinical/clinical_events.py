"""Source-bound clinical dates without substituting document or arrival dates."""
from collections import Counter
from datetime import date
from pathlib import Path
import hashlib, json, re, sqlite3
from ...contracts import digest

VERSION = 'clinical-event-bindings/1'
# These are explicit fields, not inferred dates from narrative or source timestamps.
CLINICAL_FIELDS = {
    'labs': ('collection_date', 'specimen_date'),
    'pharmacy_claims': ('fill_date',),
    'medication_history': ('dispense_date',),
    'medical_claims': ('service_date',),
    'medication_administration': ('administration_date',),
    'encounters': ('encounter_date',),
    'adt': ('admission_date', 'discharge_date'),
    'vitals': ('measurement_date',),
}
# Streams whose records can describe a planned event that happens after the record arrives.
PLANNED_SOURCES = re.compile(r'appointment|order|referral|plan|schedul|recall|reminder|task|outreach', re.I)
WINDOW_SCHEMA = {'type': 'object', 'properties': {
    'basis': {'type': 'string', 'enum': ['all_observed', 'source_event', 'arrival', 'clinical']},
    'start': {'type': 'string'}, 'end': {'type': 'string'}},
    'required': ['basis', 'start', 'end'], 'additionalProperties': False}

def iso(value):
    if not isinstance(value, str): return None
    try:
        parsed = date.fromisoformat(value)
        return value if parsed.isoformat() == value else None
    except ValueError: return None

def validate_window(window, as_of=None):
    if window is None: return {'basis': 'all_observed', 'start': '', 'end': ''}
    if not isinstance(window, dict) or set(window) != {'basis', 'start', 'end'}:
        raise ValueError('Explicit date basis/start/end required')
    if window['basis'] not in WINDOW_SCHEMA['properties']['basis']['enum']:
        raise ValueError('Unsupported date basis')
    for field in ('start', 'end'):
        if not isinstance(window[field], str) or (window[field] and not iso(window[field])):
            raise ValueError('Canonical ISO dates required')
        if as_of and window[field] and window[field] > as_of:
            raise ValueError('Window extends beyond this observed snapshot')
    if window['start'] and window['end'] and window['start'] > window['end']:
        raise ValueError('Reversed date window')
    if window['basis'] == 'all_observed' and (window['start'] or window['end']):
        # An explicit snapshot cutoff adds no constraint to this already-cut snapshot.
        # Preserve rejection of every genuine date filter on an all-observed basis.
        if not window['start'] and as_of and window['end'] == as_of:
            return {'basis': 'all_observed', 'start': '', 'end': ''}
        raise ValueError('All-observed basis cannot have date bounds')
    return dict(window)

def bind_event(record, as_of, related=None):
    fields = {k: record[k] for k in CLINICAL_FIELDS.get(record['source_table'], ())
              if record.get(k) is not None}
    if not fields: status, clinical = 'not_observed', None
    elif any(not iso(v) for v in fields.values()): status, clinical = 'invalid_explicit_date', None
    elif len(set(fields.values())) != 1: status, clinical = 'conflicting_explicit_dates', None
    else:
        clinical = next(iter(fields.values()))
        status = 'explicit_future_date' if clinical > as_of else 'explicit_date'
    source = {'record_id': record['record_id'], 'patient_id': record['patient_id'],
              'source_table': record['source_table'], 'record_hash': digest(record),
              'source_system': record.get('source_system'), 'organization_id': record.get('organization_id')}
    result={'event_id': digest([VERSION, source, fields]), 'source': source,
            'source_event_date': record.get('event_date'), 'arrival_date': record.get('available_date'),
            'recorded_date': record.get('recorded_date'), 'clinical_date': clinical,
            'clinical_date_status': status, 'clinical_date_fields': fields,
            'clinical_date_inferred_from_note': False}
    from .warehouse_interpretation import native,clock
    if native(record):
        result.update(clock(record,as_of,related,CLINICAL_FIELDS.get(record['source_table'],())))
        result['event_id']=digest([VERSION,source,result['clinical_date_evidence'],result['clock_link_issues']])
    return result

def window_state(record, window, as_of, related=None):
    window = validate_window(window, as_of)
    if window['basis'] == 'all_observed': return True
    event = bind_event(record, as_of, related)
    value = {'source_event': event['source_event_date'], 'arrival': event['arrival_date'],
             'clinical': event['clinical_date']}[window['basis']]
    if not iso(value):
        # An undated observation cannot have happened after the record was received: when it arrived before the window
        # opened it is definitely outside the window (a legacy chart import received in 2026 is no 2029 test), not unknown.
        # Records that describe planned events (bookings, orders, referrals) may arrive before their event and stay unknown.
        received = iso(event['arrival_date'][:10]) if isinstance(event['arrival_date'], str) else None
        if window['start'] and received and received < window['start'] and not PLANNED_SOURCES.search(record.get('source_table') or ''):
            return False
        return None
    if window['basis'] == 'clinical' and event['clinical_date_status'] not in ('explicit_date', 'explicit_future_date'):
        return None
    return (not window['start'] or value >= window['start']) and value <= (window['end'] or as_of)

def scoped_records(records, window, as_of):
    from .warehouse_interpretation import index
    related=index(records)
    selected, unknown, excluded = [], [], 0
    for record in records:
        state = window_state(record, window, as_of,related)
        if state is True: selected.append(record)
        elif state is None: unknown.append(record['record_id'])
        else: excluded += 1
    return selected, {'window': validate_window(window, as_of), 'records_selected': len(selected),
                      'records_outside_window': excluded, 'records_with_unknown_date': len(unknown),
                      'unknown_date_record_ids': unknown,
                      'clinical_dates_substituted': False}

def code_identity():
    from .warehouse_interpretation import identity
    return digest([hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),identity()])

def build(path):
    path = Path(path); meta = json.loads((path/'run.json').read_text()); db = sqlite3.connect(path/'stack.sqlite3')
    counts = Counter(); by_source = Counter()
    try:
        db.executescript('''CREATE TABLE IF NOT EXISTS clinical_events(
            record TEXT PRIMARY KEY, patient TEXT, stream TEXT, clinical_date TEXT, status TEXT, payload TEXT);
            CREATE INDEX IF NOT EXISTS clinical_event_patient ON clinical_events(patient,stream,clinical_date);''')
        with db:
            from .warehouse_interpretation import index
            for pid, in db.execute('SELECT id FROM patients ORDER BY id'):
                records=[json.loads(raw) for raw, in db.execute('SELECT payload FROM records WHERE patient=? ORDER BY id',(pid,))]
                related=index(records)
                for r in records:
                    event = bind_event(r, meta['snapshot']['as_of'],related)
                    db.execute('INSERT OR REPLACE INTO clinical_events VALUES (?,?,?,?,?,?)',
                               (r['record_id'], r['patient_id'], r['source_table'], event['clinical_date'],
                                event['clinical_date_status'], json.dumps(event, allow_nan=False)))
                    counts[event['clinical_date_status']] += 1
                    by_source[r['source_table']+':'+event['clinical_date_status']] += 1
        receipt = {'schema': VERSION, 'synthetic': True, 'source_digest': meta['source_digest'],
                   'source_run': meta['source_run'], 'code_identity': code_identity(),
                   'records': sum(counts.values()), 'date_status_counts': dict(counts),
                   'by_source': dict(by_source), 'clinical_dates_substituted': False,
                   'new_paid_api_calls': 0, 'scope': 'Explicit source field bindings; no free-text date extraction.'}
        (path/'clinical-events.json').write_text(json.dumps(receipt, indent=2)); return receipt
    finally: db.close()

def timeline(patient, records, as_of, unknown_window_records=None, reference_records=None):
    from .warehouse_interpretation import index
    related=index(reference_records if reference_records is not None else records+list(unknown_window_records or []))
    events = [bind_event(r, as_of,related) for r in records]
    dated = sorted((e for e in events if e['clinical_date_status'] == 'explicit_date'),
                   key=lambda e: (e['clinical_date'], e['source']['record_id']))
    unresolved = [e for e in events if e['clinical_date_status'] != 'explicit_date']
    unknown = [bind_event(r,as_of,related) for r in (unknown_window_records or [])]
    return {'patient_id': patient['patient_id'], 'operator': 'clinical_timeline', 'synthetic': True,
            'dated_events': dated, 'undated_or_future_events': unresolved,'unknown_window_events':unknown,
            'summary': {'explicit_clinical_events': len(dated), 'unresolved_or_future_events': len(unresolved)+len(unknown)},
            'care_gap_established': False,
            'scope': 'Structured explicit dates only. Unknown dates remain visible; note dates are not clinical dates.'}
