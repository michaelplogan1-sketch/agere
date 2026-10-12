"""Availability metadata for complete-unit passes and excerpt reconciliation.

Record presence is verified locally. Only identities, clocks and coverage counts
travel outside the selected units; no other record content is added to a request.
"""
from collections import Counter
from pathlib import Path
import hashlib
import json
import re
from ...contracts import digest
from . import evidence_threads as threads, case_passes as passes

VERSION = 'synthesis-source-availability/1'
INSTRUCTIONS = ''' Received-source availability is verified metadata, not clinical
evidence. Distinguish received but not submitted, selected units in other passes,
and references absent from this received same-patient snapshot. Never equate
unsubmitted content with missing/unreceived records or absent care. Unit counts
describe selected context only, not entire records. Reconciliation supplies only
cited excerpts and machine proposals, not full original units. Literal ID mentions
are not verified typed relationships. Unmapped/invalid references stay unresolved.
Source presence does not establish clinical status, payer eligibility, consent or
execution authority. Recommend source review before requesting already received
records; clinical and service recommendations remain proposals needing review.'''


def identity():
    return digest([VERSION, hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   passes.code_identity(), hashlib.sha256(Path(threads.__file__).read_bytes()).hexdigest()])


def snapshot(path, row):
    db, meta = threads.connect(path)
    try: records = threads.checked_records(db, row['patient_id'], meta['snapshot']['as_of'])
    finally: db.close()
    packet = row['model_context']; ix = {r['record_id']: r for r in records}
    if packet['snapshot']['as_of'] != meta['snapshot']['as_of'] or set(packet['sources']) - set(ix):
        raise ValueError('Availability snapshot differs from selected source context')
    return {'as_of': meta['snapshot']['as_of'], 'records': ix, 'packet': packet,
            'question': row['parameters']['question'],
            'dependency': digest([identity(), meta['snapshot'], records])}


def envelope(body, units, links, received, *, excerpts=None):
    """Include this envelope in packing measurements; overflow never drops metadata."""
    packet = received['packet']; ix = received['records']
    selected = Counter(u['source_ref'] for u in packet['evidence'])
    submitted = Counter(v['unit']['source_ref'] for v in units.values()) if excerpts is None else Counter()
    excerpt_counts = Counter(units[name]['unit']['source_ref'] for name in excerpts or {})
    current = set(submitted) | set(excerpt_counts)
    group_records = {v['unit']['source_ref'] for v in units.values()}
    edges = [e for e in links['edges'] if e['from'] in group_records or e['to'] in group_records]
    issues = [i for i in links['unresolved_references'] if i['source_record'] in group_records]
    # Match literal known IDs only against actual submitted text and the question.
    # Unknown-looking prose is never converted into an asserted missing reference.
    text = received['question'] + '\n' + ('\n'.join(v['text'] for v in units.values()) if excerpts is None
        else '\n'.join(q for v in excerpts.values() for q in v['excerpts']))
    literal = [key for key in sorted(ix) if re.search(r'(?<![\w-])'+re.escape(key)+r'(?![\w-])', text)]
    relevant = group_records | set(literal) | {e[k] for e in edges for k in ('from', 'to')} | {i['record_id'] for i in issues}
    rows = []
    for key in sorted(relevant):
        r = ix.get(key)
        rows.append([key, 'received' if r else 'not_in_received_same_patient_snapshot',
                     r['source_table'] if r else None, r.get('event_date') if r else None,
                     r.get('available_date') if r else None, selected[key], submitted[key], excerpt_counts[key]])
    outside = Counter(r['source_table'] for key, r in ix.items() if key not in selected)
    wire = json.loads(body['messages'][0]['content'])
    scope = {'schema': VERSION, 'as_of': received['as_of'], 'snapshot_digest': received['dependency'],
        'received_records': len(ix), 'selected_records': len(selected), 'selected_units': sum(selected.values()),
        'request_complete_units': sum(submitted.values()), 'request_excerpt_units': sum(excerpt_counts.values()),
        'selected_units_not_submitted_complete_in_this_request': sum(selected.values())-sum(submitted.values()),
        'received_records_outside_selection': len(ix)-len(selected),
        'outside_selection_in_selected_categories': {s:outside[s] for s in sorted({ix[k]['source_table'] for k in selected})},
        'scope': 'complete_selected_units' if excerpts is None else 'cited_excerpts_only',
        'records': {'columns': ['record_id','availability','source_table','event_date','available_date',
            'selected_units','request_complete_units','request_excerpt_units'], 'rows': rows},
        'literal_known_record_mentions': literal, 'typed_references': edges, 'unresolved_typed_references': issues,
        'content_of_other_records_submitted': False, 'clinical_meaning_validated': False}
    wire['received_source_availability'] = scope
    body = {**body, 'messages': [{'role':'user','content':passes.context.encode(wire).decode()}],
            'system': body['system']+INSTRUCTIONS}
    return body


def pass_request(packet, links, row, received):
    from . import record_synthesis
    body, units = (record_synthesis.pass_request(packet, links, row['missing_evidence']['record_scope'])
                   if 'record_scope' in row else passes.pass_request(packet, links))
    return envelope(body, units, links, received), units


def plan(row, links, received, maximum_passes=8):
    if type(maximum_passes) is not int or not 1 <= maximum_passes <= 16:
        raise ValueError('Explicit source-pass cap must be between 1 and 16')
    packet = row['model_context']
    candidates = [{**u, **packet['sources'][u['source_ref']]} for u in packet['evidence']]
    bundle = passes.context_passes.partition(row['patient_id'], {**row['parameters'],'max_bytes':20000},
        candidates, {'snapshot':packet['snapshot']}, packet.get('documentary_requirements'),
        packet.get('versioned_program_context'), wire_size=lambda p: passes.request_bytes(pass_request(p, links, row, received)[0]))
    if not bundle['review_ready'] or len(bundle['passes']) > maximum_passes:
        raise ValueError('Complete source and availability scope exceeds indivisible-unit or source-pass cap; no calls made')
    locations = {}
    for p in bundle['passes']:
        for rid in p['sources']: locations.setdefault(rid, []).append(p['pass_number'])
    return bundle, {**links, 'availability_dependency': received['dependency'], 'record_passes': locations,
        'cross_pass_edges': [e for e in links['edges'] if e['scope']=='in_scope'
            and not set(locations[e['from']]) & set(locations[e['to']])]}
