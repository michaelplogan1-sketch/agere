"""Exact imported document clauses as documentary dependencies, not patient facts."""
from pathlib import Path
import hashlib
from ...contracts import digest
from . import program_documents as documents, care_capabilities as care


def identity():
    return digest(['document-clause-binding/1', hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), documents.code_identity()])


def preview(path, key):
    row = documents.inspect_package(path, key); p = row['package']
    # Validate pinned typed inputs and exact quotes with the existing document engine.
    documents.validate_package({k: p[k] for k in ('document', 'spec', 'bindings', 'input_bindings', 'provenance', 'role')})
    return {'package_id': key, 'program': p['spec']['id'], 'version': p['spec']['version'], 'purpose': p['spec']['purpose'],
            'provenance': p['provenance'], 'effective_start': p['spec']['effective_start'], 'effective_end': p['spec']['effective_end'],
            'document_hash': p['compiled']['document_hash'], 'review': row['review'],
            'clauses': [{'input_key': k, 'label': p['input_bindings'][k]['label'], 'span': v if len(v['quote']) <= 3000 else None,
                         'quote_hash': digest(v), 'omitted': len(v['quote']) > 3000} for k, v in p['bindings'].items()],
            'patient_applicability_established': False, 'clinical_execution_authorized': False}


def catalog(path):
    items = documents.packages(path)
    if len(items) > 100: raise ValueError('Document catalog exceeds bounded listing; use exact imported package ID')
    rows = []; unavailable = []
    for item in items:
        try: rows.append(preview(path, item['id']))
        except ValueError as exc: unavailable.append({'package_id': item['id'], 'reason': str(exc)})
    return {'packages': rows, 'unavailable': unavailable, 'scope': 'Already imported document packages; no external retrieval or new policy interpretation'}


def bind(path, t, b):
    care.exact(b, 'kind package_id input_key quote_hash review_revision')
    p = preview(path, b['package_id']); clause = next((v for v in p['clauses'] if v['input_key'] == b['input_key']), None)
    if not clause or clause['omitted']: raise ValueError('Exact bounded imported clause required')
    if clause['quote_hash'] != b['quote_hash']: raise care.work.WorkConflict('Imported clause quote changed')
    if type(b['review_revision']) is not int or p['review']['revision'] != b['review_revision']:
        raise care.work.WorkConflict('Document semantic review revision changed')
    arrived = p['provenance']['received_on'] <= t['as_of']
    effective = p['effective_start'] <= t['as_of'] <= p['effective_end']
    return {'selector': b, 'record_id': None, 'record_hash': None, 'patient_id': t['patient_id'], 'source_patient_id': None,
            'source_table': 'imported_program_document', 'reference': {**p, 'clauses': [clause]},
            'clocks': {'available_date': p['provenance']['received_on'], 'event_date': None, 'decision_date': t['as_of'],
                       'availability': 'available_by_decision' if arrived else 'future_or_unknown_arrival',
                       'effective_start': p['effective_start'], 'effective_end': p['effective_end'],
                       'within_declared_effective_period': effective, 'clinical_currentness': 'not_established'},
            'semantic_review_attached': p['review']['revision'] > 0,
            'documentary_mapping_usable': arrived and effective and p['review']['status'] == 'mapping_confirmed',
            'patient_applicability_established': False, 'clinical_truth_established': False}
