"""Literal received screen bindings shared by interpretation and question planning.

A response concerns the patient identified by the source row; the respondent is
retained separately. Only explicit booleans are mapped. Transport mode, income,
billing balances and requests for contact do not establish a reported barrier.
"""
from collections import Counter
from pathlib import Path
import hashlib
from ...contracts import digest
from . import warehouse_interpretation as warehouse

VERSION = 'native-screen-assertions/1'
# Distinct questions stay distinct concepts; agreement is not corroboration.
BINDINGS = (
    ('financial_screens', 'difficulty_paying_medical_bills', 'financial hardship', 'Difficulty paying medical bills reported'),
    ('social_screens', 'difficulty_paying_for_care_reported', 'affordability of care', 'Difficulty paying for care reported'),
    ('social_screens', 'food_difficulty_reported', 'food difficulty', 'Food difficulty reported'),
    ('social_screens', 'transport_difficulty_reported', 'transport difficulty', 'Transport difficulty reported'),
)
CONCEPTS = {r[2] for r in BINDINGS}
REPORTERS = {'patient', 'parent_or_guardian', 'parent', 'mother', 'father'}


def identity():
    return digest([hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), warehouse.identity()])


def contracts():
    return [{'stream': s, 'field': f, 'concept': c, 'relation': 'reported_barrier', 'meaning': title,
             'value': 'strict boolean; false negates only this reported difficulty',
             'subject': 'source patient; respondent retained, including proxy',
             'clocks': 'source_statement_date; explicit clinical date only if declared by source contract'}
            for s, f, c, title in BINDINGS]


def assess(record, concepts, as_of):
    output = []
    for stream, field, concept, title in BINDINGS:
        if record['source_table'] != stream or concept not in concepts:
            continue
        state = 'mapped'
        if not warehouse.native(record): state = 'unqualified_source_adapter'
        elif field not in record or record[field] is None: state = 'field_not_observed'
        elif type(record[field]) is not bool: state = 'invalid_literal_type'
        elif record.get('subject', 'patient') != 'patient': state = 'subject_not_patient'
        elif record.get('respondent') not in REPORTERS: state = 'respondent_unresolved'
        output.append({'record_id': record['record_id'], 'record_hash': digest(record),
            'concept': concept, 'relation': 'reported_barrier', 'field': field,
            'value': record.get(field), 'respondent': record.get('respondent'),
            'status': state, 'meaning': title, 'clock': warehouse.clock(record, as_of)})
    return output


def coverage(records, parameters, as_of):
    rows = [x for r in records if r['source_table'] in parameters['streams']
            for x in assess(r, parameters['concepts'], as_of)]
    return {'binding_version': VERSION, 'counts': dict(Counter(x['status'] for x in rows)),
        'unmapped_fields': [{k:v for k,v in x.items() if k != 'clock'} for x in rows if x['status'] != 'mapped'],
        'meaning': 'Counts describe declared fields, not complete interpretation of each record. Field not observed is not a negative response.'}
