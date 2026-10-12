"""Versioned received-text adapters shared by extraction, indexing and retrieval.

Only observable source fields are eligible. In particular document.original_text
is a simulator convenience and must NEVER bypass received/OCR text. New fields
must be mapped explicitly; the inventory exposes unmapped string fields.
"""
from collections import Counter
import json

from .received_source_catalog import DEFINITIONS as SOURCE_DEFINITIONS, identity as source_catalog_identity
VERSION = 'received-narrative/3:'+source_catalog_identity()[:16]
COMMON = ('text', 'narrative', 'assessment', 'plan', 'impression', 'comments', 'comment',
          'summary', 'chief_complaint', 'hpi', 'family_history', 'social_history')
EXTRA = {
    'referrals': ('indication', 'reason'),
    'medication_orders': ('order_context', 'reason', 'instructions'),
    'medication_history': ('reason', 'instructions', 'coverage_caveat'),
    'medication_administration': ('reason',),
    'encounters': ('reason',), 'procedure_orders': ('indication', 'reason', 'payment_arrangement'),
    'allergies': ('reaction', 'assertion'), 'labs': ('interpretation',),
    'adt': ('reason', 'discharge_summary'), 'sdoh': ('description',),
    'life_events': ('description',),
    'coverage': ('change_context',), 'vitals': ('measurement_context',),
}
# These are structured/provenance strings, not unmapped narrative fields.
METADATA = set('record_id patient_id source_table source_system organization_id event_date available_date identity_status supersedes_record_id content_hash synthetic status code test unit drug_class drug dose days_supply transaction_type payment_channel dispense_id note_type author_id author_role signing_date document_type title channel extraction_method report_status binary_fixture encounter_type event service procedure income_band_usd food_security transport caregiver_available'.split())
METADATA.update('recorded_date source_patient_id identity_link_basis claim_id service_date paid_date place provider facility amount_basis author_name author_specialty author_credential coverage_type effective_start effective_end plan_id prior_plan_id plan_design drug_coverage_end coverage_scope geography_level vendor verification household_id caregiver_role source assessment_type respondent preferred_language education employment internet health_literacy specimen assay code_system assertion_status provenance attachment_mime_type prescription_id quantity_unit transaction_id payer fill_date history_origin medication_history_network dispense_date authorization_id destination substance reverses_transaction_id partial_of setting reject_code'.split())
FORBIDDEN = {'original_text', 'clean_text', 'truth', 'target', 'scenario', 'family_id'}
WAREHOUSE_TEXT = set(COMMON) | {'extracted_text','plan_text','disposition_text','report','reason','interpretation','description',
    'reported_observations','clinical_observation','directions_text','instructions','result_text',
    'ordering_reason','course_summary','history_text','goc',
    'history','findings','comparison','indication','patient_stated_goal'}


def _strings(value, path):
    """Preserve field paths and offsets within each source string, including lists."""
    if isinstance(value, str) and value.strip():
        yield path, value
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from _strings(item, f'{path}[{i}]')
    elif isinstance(value, dict):
        # Explicit child-text schema, never traverse arbitrary hidden metadata.
        for key in ('text', 'narrative', 'assessment', 'plan', 'impression', 'comment', 'description'):
            if key in value:
                yield from _strings(value[key], f'{path}.{key}')


def text_fields(record):
    if record.get('_warehouse_source',{}).get('adapter') == 'original-cohort-warehouse-adapter/1':
        allowed=WAREHOUSE_TEXT | set(SOURCE_DEFINITIONS.get(record['source_table'],{}).get('narrative_fields',[]))
        fields = tuple(k for k in record.get('_warehouse_fields',[]) if k in allowed)
        for field in fields:
            for path,text in _strings(record.get(field),field):
                yield {'field':path,'text':text}
        return
    fields = ('extracted_text',) if record['source_table'] == 'documents' else COMMON + EXTRA.get(record['source_table'], ())
    for field in dict.fromkeys(fields):
        for path, text in _strings(record.get(field), field):
            yield {'field': path, 'text': text}


def retrieval_notes(patient_id, records):
    result = []
    for r in records:
        if r['patient_id'] != patient_id:
            raise ValueError('Cross-patient narrative input')
        for part in text_fields(r):
            result.append({'patient_id': patient_id,
                'note_id': json.dumps([r['record_id'], part['field']], separators=(',', ':')),
                'record_id': r['record_id'], 'source_table': r['source_table'],
                'source_field': part['field'], 'date': r['event_date'],
                'available_date': r['available_date'], 'text': part['text'],
                'note_type': r.get('note_type', r['source_table'])})
    return result


def coverage(records):
    tables = Counter(); fields = Counter(); unmapped = Counter(); unread = []
    text_records = 0; chars = 0
    for r in records:
        table = r['source_table']; tables[table] += 1
        parts = list(text_fields(r)); text_records += bool(parts)
        for part in parts:
            fields[table + '.' + part['field'].split('[')[0]] += 1
            chars += len(part['text'])
        native=r.get('_warehouse_source',{}).get('adapter')=='original-cohort-warehouse-adapter/1'
        mapped = (WAREHOUSE_TEXT | set(SOURCE_DEFINITIONS.get(table,{}).get('narrative_fields',[]))) if native else set(COMMON + EXTRA.get(table, ())) if table != 'documents' else {'extracted_text'}
        source_fields=((k,r[k]) for k in r.get('_warehouse_fields',[]) if k in r) if native else r.items()
        for name, value in source_fields:
            if isinstance(value, str) and value.strip() and name not in mapped | METADATA | FORBIDDEN:
                unmapped[table + '.' + name] += 1
        if table == 'documents' and not parts:
            unread.append(r['record_id'])
    return {'adapter_version': VERSION, 'records_by_source': dict(tables),
            'records_with_received_text': text_records, 'text_fields': dict(fields),
            'text_characters': chars, 'documents_without_received_text': unread,
            'unmapped_string_fields': dict(unmapped),
            'meaning': 'Field transport coverage, not semantic accuracy or complete clinical understanding',
            'missing_data_means_missing_care': False}
