"""Explicit source identity and per-unit meaning judgments for new source reads.

Transport identity is checked locally. Subject, time and assertion interpretations
are unvalidated model judgments, never substituted for source text or care state.
"""
from pathlib import Path
import copy, hashlib, json
from ...contracts import digest
from . import synthesis_availability as A, case_passes as P, record_synthesis as R

VERSION = 'source-meaning/1'
AXES = {
    'subject': ['patient', 'other_person', 'mixed', 'unclear', 'not_applicable'],
    'time': ['at_source_date', 'historical_context', 'mixed', 'unclear', 'not_applicable'],
    'assertion': ['report_or_observation', 'plan_or_request', 'negated', 'hypothetical', 'mixed', 'unclear', 'not_applicable'],
}
INSTRUCTIONS = ''' Canonical identity metadata is verified by local source binding.
Different raw source_patient_id values do not contradict a matched canonical
patient_id. A linked chart can still discuss a relative, copied history, a prior
plan, a denial or an uncertain subject: interpret the actual text independently.
For EVERY unit add meaning with subject, time, assertion and reason (max 200
characters). Use mixed/unclear when appropriate, not a forced single interpretation.
at_source_date means what that dated source describes, NEVER current at the
analysis cutoff. Plans, orders, fills, patient reports and observed administrations
remain distinct; one declined dose does not establish persistent nonuse. A later
arrival date does not update the clinical event. Copied or linked statements are
not independent confirmations. Discuss internal tensions within one unit as a
documented account or hypothesis; conflict findings require two distinct cited
units. Meaning fields are reviewable judgments, not evidence or clinical truth.
Do not call every raw-ID difference an unresolved identity problem. If a real
subject ambiguity exists, identify its exact source basis. Record absence from a
selected packet is not missing care. All original unit text remains authoritative.
'''

def identity():
    return digest([VERSION, A.identity(), hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])

def schema():
    value = copy.deepcopy(P.MAP_SCHEMA)
    review = value['properties']['unit_reviews']['items']
    review['properties']['meaning'] = P.single.obj({
        **{k: {'type': 'string', 'enum': v} for k, v in AXES.items()},
        'reason': P.single.STR})
    review['required'].append('meaning')
    return value

def envelope(body, units, received):
    result = copy.deepcopy(body)
    wire = json.loads(result['messages'][0]['content'])
    patient = received['packet']['patient_id']
    records = sorted({v['unit']['source_ref'] for v in units.values()})
    rows = []
    for rid in records:
        source = received['packet']['sources'][rid]['source']
        original = received['records'][rid]
        if source['patient_id'] != patient or original['patient_id'] != patient:
            raise ValueError('Canonical source patient differs from selected patient')
        if source.get('identity_status') != original.get('identity_status'):
            raise ValueError('Canonical source identity status changed')
        rows.append([rid, patient, source.get('identity_status')])
    wire['canonical_identity'] = {
        'patient_id': patient, 'records': {'columns': ['record_id', 'canonical_patient_id', 'identity_status'], 'rows': rows},
        'basis': 'Verified selected source metadata; only matched rows assert resolved source linkage. Raw source-system IDs are a separate namespace.',
        'narrative_subject_verified': False, 'clinical_meaning_validated': False}
    wire['schema'] = VERSION
    result['messages'][0]['content'] = P.context.encode(wire).decode()
    result['system'] += INSTRUCTIONS
    result['output_config']['format']['schema'] = schema()
    return result

def pass_request(packet, links, row, received):
    body, units = A.pass_request(packet, links, row, received)
    return envelope(body, units, received), units

def plan(row, links, received, maximum_passes=8):
    if type(maximum_passes) is not int or not 1 <= maximum_passes <= 16:
        raise ValueError('Explicit source-pass cap must be between 1 and 16')
    packet = row['model_context']
    candidates = [{**u, **packet['sources'][u['source_ref']]} for u in packet['evidence']]
    bundle = P.context_passes.partition(row['patient_id'], {**row['parameters'], 'max_bytes': 20000},
        candidates, {'snapshot': packet['snapshot']}, packet.get('documentary_requirements'),
        packet.get('versioned_program_context'), wire_size=lambda p: P.request_bytes(pass_request(p, links, row, received)[0]))
    if not bundle['review_ready'] or len(bundle['passes']) > maximum_passes:
        raise ValueError('Complete source meaning scope exceeds indivisible-unit or source-pass cap; no calls made')
    locations = {}
    for p in bundle['passes']:
        for rid in p['sources']: locations.setdefault(rid, []).append(p['pass_number'])
    return bundle, {**links, 'availability_dependency': received['dependency'], 'record_passes': locations,
        'cross_pass_edges': [e for e in links['edges'] if e['scope'] == 'in_scope'
            and not set(locations[e['from']]) & set(locations[e['to']])]}

def parse(raw, body, units, row):
    value = json.loads(''.join(v['text'] for v in raw.get('content', []) if v.get('type') == 'text'))
    reviews = value.get('unit_reviews') if isinstance(value, dict) else None
    if not isinstance(reviews, list): raise ValueError('Explicit per-unit meaning reviews required')
    meanings = {}
    for review in reviews:
        if not isinstance(review, dict) or set(review) != {'unit', 'status', 'reason', 'meaning'}:
            raise ValueError('Explicit unit coverage and meaning required')
        key = review['unit']; meaning = review.pop('meaning')
        if not isinstance(key, str) or key not in units or key in meanings:
            raise ValueError('Unknown or duplicate meaning unit')
        if not isinstance(meaning, dict) or set(meaning) != {*AXES, 'reason'}:
            raise ValueError('Subject, time, assertion and reason required')
        if any(not isinstance(meaning[k], str) or meaning[k] not in vals for k, vals in AXES.items()):
            raise ValueError('Unknown source meaning category')
        P.single.bounded_text(meaning['reason'], 200)
        meanings[key] = meaning
    core = {**raw, 'content': [{'type': 'text', 'text': json.dumps(value)}]}
    result = P.parse_pass(core, body, units, row)
    for review in result['unit_reviews']:
        review['meaning'] = meanings[review['unit']]
        review['meaning_authority'] = 'unvalidated_model_judgment'
    return result
