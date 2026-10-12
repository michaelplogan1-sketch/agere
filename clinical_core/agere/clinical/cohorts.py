"""Typed observable cohort selection with full membership and three-valued traces.

An absent matching record means no observed match, never absent disease or care.
Filters on the same record atom must be satisfied by that same source record.
"""
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
import hashlib, json, math, sqlite3
from ...contracts import digest
from .clinical_events import WINDOW_SCHEMA, validate_window, window_state, bind_event

VERSION = 'observed-cohort/1'
PATIENT_FIELDS = {'patient_id', 'age', 'sex', 'region', 'preferred_language', 'coverage',
                  'age_reported','sex_reported','profile_captured_date'}
COMMON = {'organization_id', 'source_system', 'status'}
RECORD_FIELDS = {
    'medical_claims': {'dx', 'provider', 'facility', 'place'},
    'problem_lists': {'problem_code', 'assertion_status'},
    'coverage': {'coverage_type', 'plan_id', 'medical_coverage', 'drug_coverage'},
    'encounters': {'encounter_type','type','reason'}, 'adt': {'event', 'reason', 'destination'},
    'labs': {'test', 'code', 'code_system', 'value', 'unit', 'assay', 'specimen','result_status'},
    'medication_orders': {'drug', 'drug_class', 'dose','medication_name','route','frequency','dose_unit'},
    'pharmacy_claims': {'drug', 'drug_class', 'dose', 'transaction_type', 'payer', 'days_supply'},
    'medication_history': {'drug', 'drug_class', 'dose', 'history_origin', 'payment_channel'},
    'medication_administration': {'drug', 'drug_class', 'dose', 'setting'},
    'referrals': {'service','destination','specialty','requested_specialty','referral_type','completion_status'}, 'financial': {'difficulty_paying_medical_bills'},
    'sdoh': {'transport', 'food_security', 'internet', 'respondent'},
    'households': {'caregiver_available', 'caregiver_role'},
    'vitals': {'systolic_bp', 'diastolic_bp', 'heart_rate', 'weight_kg', 'temperature_c','measurement','value','unit'},
    'notes': {'author_id', 'author_specialty', 'note_type'},
    'documents': {'author_id', 'author_specialty', 'document_type', 'extraction_method'},
    'financial_screens': {'difficulty_paying_medical_bills','respondent'},
    'conditions': {'description','icd10','onset'},
    'medication_lists': {'name','dose','frequency','indication'},
    'pharmacy_dispenses': {'medication_name','label_dose','label_frequency','days_supply_reported','payment_channel'},
    'pharmacy_history': {'medication_name','label_dose','label_frequency','days_supply_reported','payment_channel','original_dispense_record_id'},
    'medication_administrations': {'medication_name','administration_status','dose_given','dose_unit','route'},
    'medication_followup_reports': {'medication_name','reported_intent','days_used_reported','report_window_days','respondent'},
    'medication_intent_changes': {'medication_order_record_id','replacement_order_record_id'},
    'pharmacy_claim_transactions': {'transaction_status','claim_id','reject_code'},
    'referral_status_updates': {'referral_record_id','service_performed_confirmed','status_source'},
    'outside_report_status': {'performed_service_documented','referral_record_id'},
    'patient_billing_accounts': {'account_id','balance_cents','patient_responsibility_cents','currency'},
    'advance_care_documents': {'document_type','copy_quality','legal_validity'},
    'code_status_orders': {'cpr_in_arrest','intubation','portable_order_validity'},
}
OPS = ['eq', 'ne', 'lt', 'le', 'gt', 'ge', 'contains', 'prefix']
VALUE_SCHEMA = {'anyOf': [{'type': 'string'}, {'type': 'number'}, {'type': 'boolean'}]}
CONDITION_SCHEMA = {'type': 'object', 'properties': {
    'field': {'type': 'string'}, 'op': {'type': 'string', 'enum': OPS}, 'value': VALUE_SCHEMA},
    'required': ['field', 'op', 'value'], 'additionalProperties': False}
ATOM_SCHEMA = {'anyOf': [
    {'type': 'object', 'properties': {'kind': {'type': 'string', 'enum': ['patient']},
       **CONDITION_SCHEMA['properties']}, 'required': ['kind', 'field', 'op', 'value'], 'additionalProperties': False},
    {'type': 'object', 'properties': {'kind': {'type': 'string', 'enum': ['record']},
       'stream': {'type': 'string', 'enum': sorted(RECORD_FIELDS)},
       'conditions': {'type': 'array', 'items': CONDITION_SCHEMA}, 'window': WINDOW_SCHEMA},
       'required': ['kind', 'stream', 'conditions', 'window'], 'additionalProperties': False}]}
SCHEMA = {'type': 'object', 'properties': {k: {'type': 'array', 'items': ATOM_SCHEMA}
           for k in ('all', 'any', 'exclude')}, 'required': ['all', 'any', 'exclude'], 'additionalProperties': False}

from ..received_source_catalog import DEFINITIONS as SOURCE_DEFINITIONS
RECORD_FIELDS.update({table:set(s['fields']) for table,s in SOURCE_DEFINITIONS.items()})


def empty(): return {'all': [], 'any': [], 'exclude': []}

def _condition(condition, allowed):
    if not isinstance(condition, dict) or set(condition) != {'field', 'op', 'value'}:
        raise ValueError('Invalid cohort field condition')
    if condition['field'] not in allowed or condition['op'] not in OPS:
        raise ValueError('Unmapped cohort field/operator')
    v = condition['value']
    if type(v) not in (str, int, float, bool) or (type(v) is float and not math.isfinite(v)):
        raise ValueError('Finite scalar cohort value required')
    if isinstance(v, str) and not 1 <= len(v) <= 150: raise ValueError('Bounded cohort strings required')
    if condition['op'] in ('lt', 'le', 'gt', 'ge') and type(v) not in (int, float):
        raise ValueError('Numeric cohort comparison required')
    if condition['op'] in ('contains', 'prefix') and not isinstance(v, str):
        raise ValueError('String membership/prefix required')

def validate(spec, as_of=None):
    if spec is None: return empty()
    if not isinstance(spec, dict) or set(spec) != {'all', 'any', 'exclude'}:
        raise ValueError('Explicit all/any/exclude cohort required')
    if any(not isinstance(spec[k], list) for k in spec) or sum(map(len, spec.values())) > 20:
        raise ValueError('At most twenty typed cohort predicates')
    for atom in sum(spec.values(), []):
        if not isinstance(atom, dict): raise ValueError('Typed cohort predicate required')
        if atom.get('kind') == 'patient':
            if set(atom) != {'kind', 'field', 'op', 'value'}: raise ValueError('Invalid patient predicate')
            _condition({k: atom[k] for k in ('field', 'op', 'value')}, PATIENT_FIELDS)
            if atom['field'] == 'age' and (type(atom['value']) not in (int, float) or not 0 <= atom['value'] <= 130):
                raise ValueError('Age must be a numeric value from zero through 130')
        elif atom.get('kind') == 'record':
            if set(atom) != {'kind', 'stream', 'conditions', 'window'} or atom['stream'] not in RECORD_FIELDS:
                raise ValueError('Invalid source-record predicate')
            if not isinstance(atom['conditions'], list) or len(atom['conditions']) > 8:
                raise ValueError('At most eight conditions on one record')
            for c in atom['conditions']: _condition(c, RECORD_FIELDS[atom['stream']] | COMMON)
            validate_window(atom['window'], as_of)
            for c in atom['conditions']:
                if c['field'] in ('dx', 'problem_code') and c['op'] not in ('contains', 'prefix'):
                    raise ValueError('Diagnosis lists require exact membership or explicit code prefix')
        else: raise ValueError('Unknown cohort predicate kind')
    return spec

def truth_all(values): return False if False in values else (None if None in values else True)
def truth_any(values): return True if True in values else (None if None in values else False)

def compare(got, op, want):
    if got is None: return None
    if op == 'contains':
        return want in got if isinstance(got, list) and all(isinstance(x, str) for x in got) else None
    if op == 'prefix':
        if isinstance(got, str): return got.startswith(want)
        return any(x.startswith(want) for x in got) if isinstance(got, list) and all(isinstance(x, str) for x in got) else None
    if isinstance(got, (dict, list)): return None
    numeric = type(got) in (int, float) and type(want) in (int, float)
    if not numeric and type(got) is not type(want): return None
    if numeric and not math.isfinite(got): return None
    if op in ('lt', 'le', 'gt', 'ge') and not numeric: return None
    return {'eq': lambda: got == want, 'ne': lambda: got != want, 'lt': lambda: got < want,
            'le': lambda: got <= want, 'gt': lambda: got > want, 'ge': lambda: got >= want}[op]()

def record_value(record, field):
    if field == 'problem_code':
        problems = record.get('problems')
        if not isinstance(problems, list) or any(not isinstance(p, dict) or not isinstance(p.get('code'), str) for p in problems): return None
        return [p['code'] for p in problems]
    return record.get(field)

def patient_value(patient, field, as_of):
    if field != 'age': return patient.get(field)
    try:
        dob = date.fromisoformat(patient['birth_date']); cutoff = date.fromisoformat(as_of)
        if dob > cutoff: return None
        return cutoff.year-dob.year-((cutoff.month, cutoff.day) < (dob.month, dob.day))
    except (KeyError, TypeError, ValueError): return None

def evaluate_atom(atom, patient, records, as_of):
    if atom['kind'] == 'patient':
        got = patient_value(patient, atom['field'], as_of)
        return compare(got, atom['op'], atom['value']), {'predicate': atom, 'observed_value': got,
               'source': {'table': 'patients', 'field': 'birth_date' if atom['field'] == 'age' else atom['field'],
                          'patient_hash': digest(patient), 'as_of': as_of}}
    states, support, unknown = [], [], []
    eligible = [r for r in records if r['source_table'] == atom['stream']]
    from .warehouse_interpretation import index
    related=index(records)
    for r in eligible:
        values = {c['field']: record_value(r, c['field']) for c in atom['conditions']}
        state = truth_all([window_state(r, atom['window'], as_of,related),
                           *[compare(values[c['field']], c['op'], c['value']) for c in atom['conditions']]])
        states.append(state)
        if state is True or state is None:
            event = bind_event(r, as_of,related)
            trace = {'source': event['source'], 'field_values': values,
                     'date_basis': atom['window']['basis'], 'dates': {k: event[k] for k in
                       ('source_event_date', 'arrival_date', 'clinical_date', 'clinical_date_status', 'clinical_date_fields')}}
            trace['clock_dependencies']=event.get('clinical_date_evidence',[])
            trace['clock_link_issues']=event.get('clock_link_issues',[])
            (support if state is True else unknown).append(trace)
    return truth_any(states), {'predicate': atom, 'records_evaluated': len(eligible),
            'support_count': len(support), 'unknown_record_count': len(unknown),
            'support': support[:8], 'unknown_examples': unknown[:8],
            'nonmatch_meaning': 'No matching observed record, not absence of disease/care.'}

def evaluate_patient(spec, patient, records, as_of):
    groups, traces = {}, []
    for group in ('all', 'any', 'exclude'):
        values = []
        for atom in spec[group]:
            state, trace = evaluate_atom(atom, patient, records, as_of)
            values.append(state); traces.append({'group': group, 'state': state, **trace})
        groups[group] = values
    excluded = truth_any(groups['exclude'])
    state = truth_all([truth_all(groups['all']), truth_any(groups['any']) if groups['any'] else True,
                       None if excluded is None else not excluded])
    return state, traces

def code_identity():
    from .clinical_events import code_identity as event_identity
    return digest({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
                   [Path(__file__), Path(__file__).with_name('clinical_events.py')]}|{'event_bindings':event_identity()})

def select(path, spec=None, *, patient=None, limit=10, patient_ids=None):
    path = Path(path); meta = json.loads((path/'run.json').read_text()); as_of = meta['snapshot']['as_of']
    spec = validate(spec, as_of)
    if type(limit) is not int or not 1 <= limit <= 50: raise ValueError('Bounded cohort preview required')
    db = sqlite3.connect(path/'stack.sqlite3')
    try:
        if patient and not db.execute('SELECT 1 FROM patients WHERE id=?', (patient,)).fetchone(): raise ValueError('Unknown patient')
        if patient_ids is not None:
            if not isinstance(patient_ids, list) or len(patient_ids)>100000 or any(not isinstance(x,str) for x in patient_ids):
                raise ValueError('Explicit bounded patient membership required')
            patient_ids=sorted(set(patient_ids))
            known={r[0] for r in db.execute('SELECT id FROM patients')}
            if set(patient_ids)-known:raise ValueError('Unknown patient in supplied membership')
        key = digest([VERSION, code_identity(), meta['source_digest'], spec, patient, limit, patient_ids])
        old = db.execute('SELECT payload FROM result_cache WHERE id=?', (key,)).fetchone()
        if old: return {**json.loads(old[0]), 'cache_hit': True}
        roster = [(pid, json.loads(payload)) for pid, payload in db.execute('SELECT id,payload FROM patients'+
                  (' WHERE id=?' if patient else '')+' ORDER BY id', [patient] if patient else [])]
        if patient_ids is not None:
            allowed=set(patient_ids);roster=[(pid,p) for pid,p in roster if pid in allowed]
        streams = sorted({a['stream'] for a in sum(spec.values(), []) if a['kind'] == 'record'})
        records = defaultdict(list)
        if streams:
            sql = 'SELECT patient,payload FROM records WHERE stream IN ('+','.join('?' for _ in streams)+')'
            params = list(streams)
            if patient: sql += ' AND patient=?'; params.append(patient)
            for pid, payload in db.execute(sql+' ORDER BY id', params): records[pid].append(json.loads(payload))
        membership = {'matched': [], 'not_matched': [], 'unknown': []}; previews = {k: [] for k in membership}
        for pid, p in roster:
            state, traces = evaluate_patient(spec, p, records[pid], as_of)
            status = 'matched' if state is True else 'not_matched' if state is False else 'unknown'
            membership[status].append(pid)
            if len(previews[status]) < limit: previews[status].append({'patient_id': pid, 'status': status, 'predicates': traces})
        result = {'schema': VERSION, 'synthetic': True, 'id': key, 'specification': spec,
                  'snapshot': meta['snapshot'], 'source_digest': meta['source_digest'],
                  'counts': {'evaluated': len(roster), **{k: len(v) for k, v in membership.items()}},
                  'patient_ids': membership['matched'], 'unknown_patient_ids': membership['unknown'],
                  'input_membership_digest': digest(patient_ids) if patient_ids is not None else None,
                  'examples': previews, 'cache_hit': False, 'new_paid_api_calls': 0,
                  'scope': 'Complete selection over the observed snapshot. Unknowns are separate; no inferred clinical phenotype or provider attribution.'}
        with db: db.execute('INSERT OR REPLACE INTO result_cache VALUES (?,?)', (key, json.dumps(result, allow_nan=False)))
        return result
    finally: db.close()

def capabilities():
    return {'schema': SCHEMA, 'patient_fields': sorted(PATIENT_FIELDS),
            'record_fields': {k: sorted(v | COMMON) for k, v in RECORD_FIELDS.items()},
            'semantics': 'all predicates AND at least one any predicate (if present) AND no exclude predicate; unknown is preserved.',
            'age_basis': 'Birth date at snapshot cutoff', 'string_matching': 'Exact case-sensitive values; explicit code prefixes only.'}
