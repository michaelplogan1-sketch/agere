"""Explicit record scopes alongside the unchanged category-complete context contract."""
from collections import Counter
from pathlib import Path
import hashlib
import json
import re
from ...contracts import digest
from . import context_builder as context, evidence_threads, longitudinal

VERSION = 'selected-record-context/1'


def identity():
    return digest([VERSION, hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])


def location(path, key):
    if not isinstance(key, str) or not re.fullmatch('[a-f0-9]{64}', key):
        raise ValueError('Exact context receipt ID required')
    return Path(path)/'record-contexts'/(key+'.json')


def exists(path, key):
    return location(path, key).exists()


def received(path, patient):
    db, meta = evidence_threads.connect(path)
    try: return evidence_threads.checked_records(db, patient, meta['snapshot']['as_of']), meta
    finally: db.close()


def create(path, patient, question, record_ids):
    if not isinstance(record_ids, list) or not 1 <= len(record_ids) <= 200 or any(not isinstance(x, str) for x in record_ids) or len(set(record_ids)) != len(record_ids):
        raise ValueError('One to 200 distinct explicit received record IDs required')
    records, meta = received(path, patient); wanted = set(record_ids)
    if wanted - {r['record_id'] for r in records}:
        raise ValueError('Selected record unavailable for this patient')
    selected = [r for r in records if r['record_id'] in wanted]
    p = context.validate({'question': question, 'streams': sorted({r['source_table'] for r in selected}),
        'concepts': [], 'contract': '', 'program_ids': [], 'max_bytes': 60000})
    parent, _, _, _ = context.dependency_identity(path, p)
    evdb, _ = longitudinal.connect(path)
    try:
        events = [json.loads(r[0]) for r in evdb.execute('SELECT payload FROM events WHERE patient=? ORDER BY id', (patient,))]
        events = [e for e in events if e['source']['record_id'] in wanted]
    finally: evdb.close()
    row = context.pack(patient, p, context.units(patient, selected, events, p, meta['snapshot']['as_of']), meta)
    counts = Counter(r['source_table'] for r in records if r['record_id'] not in wanted)
    scope = {'kind': 'explicit_received_records', 'record_ids': sorted(wanted),
        'complete_categories': False, 'complete_chart': False,
        'selection_sufficiency': 'unassessed',
        'excluded_records': sum(counts.values()),
        'excluded_by_category': dict(sorted(counts.items())),
        'excluded_from_selected_categories': sum(n for s, n in counts.items() if s in p['streams'])}
    # Small semantic declaration travels in every model request via missing_evidence.
    # Record IDs and complete provenance already occur in the sources table.
    declaration = {'kind': scope['kind'], 'selected_records': len(selected), 'complete_categories': False,
        'excluded_received_records': scope['excluded_records'], 'selection_sufficiency': 'unassessed'}
    row['missing_evidence']['record_scope'] = declaration
    if row['model_context']:
        row['model_context']['missing_evidence'] = row['missing_evidence']
        row['budget']['used'] = len(context.encode(row['model_context']))
        if row['budget']['used'] > p['max_bytes']:
            row['status'] = 'scope_header_overflow'
            row['dispatch'].update(source_review_allowed=False, reason='scope_header_exceeds_context_budget')
    row.update(schema=VERSION, parameters=p, record_scope=scope, identity=identity(),
        parent_identity=parent, patient_source_fingerprint=digest(records),
        source_inventory={'records_in_explicit_scope': len(selected), 'received_records_outside_scope': sum(counts.values())})
    row['id'] = digest(row)
    if digest(received(path, patient)[0]) != row['patient_source_fingerprint']:
        raise ValueError('Sources changed during record context assembly')
    target = location(path, row['id']); target.parent.mkdir(exist_ok=True)
    temporary = target.with_suffix('.tmp'); temporary.write_text(json.dumps(row, indent=2)); temporary.replace(target)
    return row


def inspect(path, key):
    row = json.loads(location(path, key).read_text())
    if row['id'] != key or digest({k:v for k,v in row.items() if k != 'id'}) != key or row['identity'] != identity():
        raise ValueError('Record context identity changed; rebuild required')
    parent, _, _, _ = context.dependency_identity(path, row['parameters'])
    records, _ = received(path, row['patient_id'])
    if parent != row['parent_identity'] or digest(records) != row['patient_source_fingerprint']:
        raise ValueError('Record context sources or dependencies changed; rebuild required')
    return row


def verified(path, key):
    row = inspect(path, key)
    if not row['dispatch']['source_review_allowed'] or row['omissions']:
        raise ValueError('Complete declared record scope required before inference')
    if set(row['model_context']['sources']) != set(row['record_scope']['record_ids']):
        raise ValueError('Record scope is incomplete')
    return row
