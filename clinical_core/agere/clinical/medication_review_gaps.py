"""Map unassessed attached medication evidence back to explicit source selection.

No omitted/quarantined result is recovered or promoted. A new, narrower selection
must be prepared by the caller and keeps the original question and answer intact.
"""
from pathlib import Path
import hashlib
from ...contracts import digest
from . import medication_interpretation as I, medication_partial_answers as P

VERSION = 'medication-review-gaps/1'
def identity(): return digest([VERSION, I.identity(), P.identity(), hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])

def preview(path, request):
    I.C.exact(request, 'review_id answer_id')
    state = I.listing(path, {'review_id': request['review_id']})
    partials = P.listing(path, {'review_id': request['review_id']})['partials']
    bound = [a for a in state['answers'] if a['current'] and a['request']['answer_id'] == request['answer_id']]
    bound += [a for a in partials if a['current'] and a['answer_id'] == request['answer_id']]
    if len(bound) != 1: raise ValueError('One current exact-review answer attachment required')
    attachment = bound[0]; selection = I.inspect_selection(path, attachment['request']['selection_id'])
    answer = I.S.inspect(path, request['answer_id']); context = I.S.context(path, selection['context_id'])
    if answer['patient_id'] != selection['patient_id'] or answer['context_id'] != context['id']:
        raise ValueError('Medication gap patient or context mismatch')
    units = {u['id']: u for u in context['model_context']['evidence']}
    records = {}; seen = set()
    for review in answer['coverage']['unassessed_units']:
        key = review['source_unit_id']
        if key not in units or key in seen: raise ValueError('Unknown or duplicate unresolved source unit')
        seen.add(key); unit = units[key]; rid = unit['source_ref']
        records.setdefault(rid, {'record_id': rid, 'source': context['model_context']['sources'][rid]['source'], 'unassessed_units': []})
        records[rid]['unassessed_units'].append({'source_unit_id': key, 'kind': unit['kind'], 'field': unit['field'],
            'reason': review['reason'], 'authority': review.get('disposition_authority', 'unvalidated_model_judgment')})
    selected = sorted(records)
    out = {'schema': VERSION, 'request': request, 'patient_id': selection['patient_id'],
        'original_selection_id': selection['id'], 'original_context_id': context['id'],
        'question_receipt_ids': selection['request']['question_receipt_ids'], 'records': [records[r] for r in selected],
        'counts': {'unassessed_units': len(seen), 'records_to_review': len(selected)},
        'selection_request': {'review_id': request['review_id'], 'question_receipt_ids': selection['request']['question_receipt_ids'], 'record_ids': selected},
        'binding': {'attachment_hash': digest(attachment), 'answer_hash': digest(answer), 'context_hash': digest(context)},
        'implementation': identity(), 'original_answer_changed': False, 'failed_request_resent': False,
        'clinical_meaning_validated': False, 'new_paid_api_calls': 0,
        'scope': 'Select complete received records for a new bounded review. Old omissions and quarantines remain unchanged; this preview does not dispatch or infer assessed coverage.'}
    out['id'] = digest(out)
    return out
