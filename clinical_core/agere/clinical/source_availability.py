"""Verified metadata for a bounded submission, never a substitute for its content."""
from ...contracts import digest
from . import evidence_threads as threads, warehouse_interpretation as native
from .learning_queue import sha

VERSION = 'bounded-source-availability/1'


def identity():
    return digest([VERSION, sha(__file__), native.identity(), sha(threads.__file__)])


def prepare(path, record, *, candidate_ids=()):
    db, meta = threads.connect(path)
    try:
        records = threads.checked_records(db, record['patient_id'], meta['snapshot']['as_of'])
    finally:
        db.close()
    ix = native.index(records)
    if ix['records'].get(record['record_id']) != record:
        raise ValueError('Submitted source changed in received inventory')
    if len(candidate_ids) > 32 or len(set(candidate_ids)) != len(candidate_ids):
        raise ValueError('Bounded distinct candidate record identities required')
    statement = native.statement(record, meta['snapshot']['as_of'], ix) or {}
    links = statement.get('linked_sources', [])
    issues = statement.get('link_issues', [])
    if len(links) + len(issues) > 32:
        raise ValueError('Typed reference availability exceeds bound; no silent omission')
    def entry(key):
        r = ix['records'].get(key)
        if r is None:
            return {'record_id': key, 'status': 'not_in_received_same_patient_snapshot', 'content_submitted': False}
        return {'record_id': key, 'status': 'received', 'content_submitted': key == record['record_id'],
                'source_table': r['source_table'], 'source_date': r.get('event_date'),
                'available_date': r['available_date'], 'record_hash': digest(r)}
    wire = {'as_of': meta['snapshot']['as_of'], 'received_same_patient_records': len(records),
            'other_record_contents_not_submitted': len(records)-1,
            'submitted_record': entry(record['record_id']),
            'typed_references': [{'field': x['field'], **entry(x['target']['record_id'])} for x in links],
            'unresolved_typed_references': issues,
            'candidate_record_availability': [entry(key) for key in candidate_ids],
            'boundary': 'Availability is verified only within this received same-patient snapshot. Received but not submitted is not missing or unreceived. Other record contents have not been interpreted. An absent reference never proves absent care; unknown/unmapped links remain unresolved.'}
    return {'wire': wire, 'dependency': digest([identity(), meta['snapshot'], records])}
