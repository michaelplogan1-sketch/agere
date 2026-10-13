"""Typed observable cohort selection with full membership and three-valued traces.

An absent matching record means no observed match, never absent disease or care.
Filters on the same record atom must be satisfied by that same source record.
"""
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
import hashlib, json, math, re, sqlite3
from ...contracts import digest
from .clinical_events import WINDOW_SCHEMA, validate_window, window_state, bind_event
from . import concepts, grounding, source_profile

VERSION = 'observed-cohort/2'
PATIENT_FIELDS = {'patient_id', 'age', 'sex', 'region', 'preferred_language', 'coverage',
                  'age_reported','age_latest','sex_reported','profile_captured_date'}
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
       'required': ['kind', 'stream', 'conditions', 'window'], 'additionalProperties': False},
    {'type': 'object', 'properties': {'kind': {'type': 'string', 'enum': ['concept']},
       'concept': {'type': 'string'}, 'window': WINDOW_SCHEMA},
       'required': ['kind', 'concept', 'window'], 'additionalProperties': False},
    {'type': 'object', 'properties': {'kind': {'type': 'string', 'enum': ['measurement']},
       'concept': {'type': 'string'}, 'op': {'type': 'string', 'enum': ['eq', 'ne', 'lt', 'le', 'gt', 'ge']},
       'value': {'type': 'number'}, 'aggregate': {'type': 'string', 'enum': ['any', 'latest']}, 'window': WINDOW_SCHEMA},
       'required': ['kind', 'concept', 'op', 'value', 'aggregate', 'window'], 'additionalProperties': False}]}
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
            # Any received stream/field is allowed by name; grounding checks it is populated.
            if set(atom) != {'kind', 'stream', 'conditions', 'window'} or not isinstance(atom['stream'], str) \
                    or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', atom['stream']):
                raise ValueError('Invalid source-record predicate')
            if not isinstance(atom['conditions'], list) or len(atom['conditions']) > 8:
                raise ValueError('At most eight conditions on one record')
            allowed = RECORD_FIELDS.get(atom['stream'])
            for c in atom['conditions']:
                _condition(c, (allowed | COMMON) if allowed else {c.get('field')} if isinstance(c, dict)
                           and isinstance(c.get('field'), str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', c['field']) else set())
            validate_window(atom['window'], as_of)
            for c in atom['conditions']:
                if c['field'] in ('dx', 'problem_code') and c['op'] not in ('contains', 'prefix'):
                    raise ValueError('Diagnosis lists require exact membership or explicit code prefix')
        elif atom.get('kind') == 'concept':
            if set(atom) - {'system', 'status'} != {'kind', 'concept', 'window'} or not concepts.is_concept(atom['concept']) \
                    or atom.get('system', 'any') not in ('any', 'icd10', 'snomed', 'label') \
                    or atom.get('status', 'completed') != 'completed' or ('status' in atom and atom['concept'] not in concepts.ENCOUNTERS):
                raise ValueError('Concept predicate needs a library concept id (optional system any/icd10/snomed/label; '
                                 'optional status "completed" for encounter concepts)')
            validate_window(atom['window'], as_of)
        elif atom.get('kind') == 'measurement':
            if set(atom) - {'names'} != {'kind', 'concept', 'op', 'value', 'aggregate', 'window'} or atom['concept'] not in concepts.MEASUREMENTS \
                    or not (isinstance(atom.get('names', []), list) and all(isinstance(n, str) for n in atom.get('names', []))):
                raise ValueError('Measurement predicate needs a library measurement concept (optional literal names list)')
            if atom['op'] not in ('eq', 'ne', 'lt', 'le', 'gt', 'ge') or type(atom['value']) not in (int, float) \
                    or not math.isfinite(atom['value']) or atom['aggregate'] not in ('any', 'latest'):
                raise ValueError('Measurement predicate needs a numeric comparison and any/latest aggregate')
            validate_window(atom['window'], as_of)
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
        got = patient_value(patient, 'age_reported' if atom['field'] == 'age_latest' else atom['field'], as_of)
        if atom['field'] == 'age_latest':  # a later reported age (demographic update) supersedes the roster value; age_reported = as captured
            updates = [r for r in records if r.get('source_table') == 'demographic_updates' and isinstance(r.get('age_reported'), (int, float))
                       and (r.get('event_date') or '')[:10] <= as_of]
            if updates: got = max(updates, key=lambda r: (r.get('event_date') or '', r.get('available_date') or ''))['age_reported']
        return compare(got, atom['op'], atom['value']), {'predicate': atom, 'observed_value': got,
               'source': {'table': 'patients', 'field': 'birth_date' if atom['field'] == 'age' else atom['field'],
                          'patient_hash': digest(patient), 'as_of': as_of}}
    if atom['kind'] in ('concept', 'measurement'):
        return evaluate_concept(atom, records, as_of)
    states, support, unknown = [], [], []
    eligible = [r for r in records if r['source_table'] == atom['stream']]
    from .warehouse_interpretation import index
    related=index(records)
    for r in eligible:
        values = {c['field']: record_value(r, c['field']) for c in atom['conditions']}
        # A record that does not carry the field did not report it: no match, not unknown.
        # (Grounding has already confirmed the field is populated in this stream.)
        state = truth_all([window_state(r, atom['window'], as_of,related),
                           *[False if values[c['field']] is None else compare(values[c['field']], c['op'], c['value'])
                             for c in atom['conditions']]])
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

def _record_date(r):
    return r.get('event_date') or r.get('recorded_date') or r.get('available_date') or ''


def _concept_matches(binding, r):
    if 'values' in binding:
        got = r.get(binding['field'])
        values = set(binding['values'])
        if isinstance(got, list): return any(isinstance(x, str) and x in values for x in got)
        return isinstance(got, str) and got in values
    if 'names' in binding:
        return any(r.get(f) in binding['names'] for f in binding['name_fields'])
    return r.get(binding['value_field']) is not None


def _measurement_value(binding, r, units):
    """Value converted to the concept's canonical unit, or None if not interpretable."""
    raw = r.get(binding['value_field'])
    if type(raw) not in (int, float) or not math.isfinite(raw): return None
    unit = binding.get('fixed_unit') or r.get(binding['unit_field'])
    factor = units.get(unit)
    return None if factor is None else raw * factor


def evaluate_concept(atom, records, as_of):
    """Concept/measurement atoms match records via resolved bindings (all coding systems).

    A patient with no record of the concept is not_matched (no observed match);
    unknown is reserved for matching records whose window or value cannot be read.
    """
    resolved = atom['_resolved']
    from .warehouse_interpretation import index
    related = index(records)
    hits, unreported = [], 0
    for r in records:
        binding = next((b for b in resolved['bindings'] if b['stream'] == r['source_table'] and _concept_matches(b, r)), None)
        if binding is None: continue
        if atom.get('names') and not any(str(r.get(f, '')).lower() in {n.lower() for n in atom['names']}
                                         for f in binding.get('name_fields', [])): continue
        if atom.get('status') and r.get('status') != atom['status']: continue
        system = atom.get('system', 'any')
        if system == 'icd10' and not (r.get('icd10') or r.get('problem_code')): continue
        if system == 'snomed' and not r.get('snomed'): continue
        if system == 'label' and (r.get('icd10') or r.get('snomed')): continue
        if atom['kind'] == 'measurement' and r.get(binding['value_field']) is None:
            # Cancelled/unreported results (e.g. hemolysed specimen) carry no measurement.
            unreported += 1
            continue
        in_window = window_state(r, atom['window'], as_of, related)
        if in_window is False: continue
        value = _measurement_value(binding, r, resolved['units']) if atom['kind'] == 'measurement' else None
        hits.append((r, binding, in_window, value))
    if atom['kind'] == 'measurement':
        if atom['aggregate'] == 'latest' and hits:
            dated = [h for h in hits if h[2] is True and h[3] is not None]
            hits = [max(dated, key=lambda h: _record_date(h[0]))] if dated else hits
        states = [truth_all([h[2], compare(h[3], atom['op'], atom['value'])]) for h in hits]
    else:
        states = [h[2] for h in hits]
    support = [{'record_id': h[0].get('record_id'), 'stream': h[0]['source_table'], 'date': _record_date(h[0]),
                'label': next((h[0].get(f) for f in ('description', 'test', 'measurement', 'icd10') if h[0].get(f)), None),
                **({'value': h[3], 'unit': resolved['unit']} if atom['kind'] == 'measurement' else {})}
               for h, s in zip(hits, states) if s is True]
    return truth_any(states), {'predicate': {k: v for k, v in atom.items() if k != '_resolved'},
            'records_evaluated': len(hits), 'records_without_reported_value': unreported, 'support_count': len(support),
            'unknown_record_count': sum(s is None for s in states), 'support': support[:8],
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
    here = Path(__file__)
    return digest({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
                   [here, here.with_name('clinical_events.py'), here.with_name('concepts.py'),
                    here.with_name('grounding.py'), here.with_name('source_profile.py')]}|{'event_bindings':event_identity()})


def _streams(spec):
    out = set()
    for a in sum(spec.values(), []):
        if a['kind'] == 'record': out.add(a['stream'])
        elif '_resolved' in a: out.update(b['stream'] for b in a['_resolved']['bindings'])
    return sorted(out)

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
        # Grounding raises GroundingError for predicates the data cannot support.
        grounded, grounding_report = grounding.prepare(spec, source_profile.load(path))
        key = digest([VERSION, code_identity(), meta['source_digest'], spec, patient, limit, patient_ids])
        old = db.execute('SELECT payload FROM result_cache WHERE id=?', (key,)).fetchone()
        if old: return {**json.loads(old[0]), 'cache_hit': True}
        roster = [(pid, json.loads(payload)) for pid, payload in db.execute('SELECT id,payload FROM patients'+
                  (' WHERE id=?' if patient else '')+' ORDER BY id', [patient] if patient else [])]
        if patient_ids is not None:
            allowed=set(patient_ids);roster=[(pid,p) for pid,p in roster if pid in allowed]
        streams = _streams(grounded)
        if any(a.get('kind') == 'patient' and a.get('field') == 'age_latest' for part in grounded.values() if isinstance(part, list) for a in part):
            streams = set(streams) | {'demographic_updates'}  # latest reported age supersedes the roster age
        records = defaultdict(list)
        if streams:
            sql = 'SELECT patient,payload FROM records WHERE stream IN ('+','.join('?' for _ in streams)+')'
            params = list(streams)
            if patient: sql += ' AND patient=?'; params.append(patient)
            for pid, payload in db.execute(sql+' ORDER BY id', params): records[pid].append(json.loads(payload))
        membership = {'matched': [], 'not_matched': [], 'unknown': []}; previews = {k: [] for k in membership}
        for pid, p in roster:
            state, traces = evaluate_patient(grounded, p, records[pid], as_of)
            status = 'matched' if state is True else 'not_matched' if state is False else 'unknown'
            membership[status].append(pid)
            if len(previews[status]) < limit: previews[status].append({'patient_id': pid, 'status': status, 'predicates': traces})
        result = {'schema': VERSION, 'synthetic': True, 'id': key, 'specification': spec,
                  'snapshot': meta['snapshot'], 'source_digest': meta['source_digest'],
                  'counts': {'evaluated': len(roster), **{k: len(v) for k, v in membership.items()}},
                  'patient_ids': membership['matched'], 'unknown_patient_ids': membership['unknown'],
                  'input_membership_digest': digest(patient_ids) if patient_ids is not None else None,
                  'examples': previews, 'cache_hit': False, 'new_paid_api_calls': 0,
                  'grounding': grounding_report, 'interpretation': grounding.interpretation(grounded),
                  'scope': 'Complete selection over the observed snapshot. Unknowns are separate; no inferred clinical phenotype or provider attribution.'}
        with db: db.execute('INSERT OR REPLACE INTO result_cache VALUES (?,?)', (key, json.dumps(result, allow_nan=False)))
        return result
    finally: db.close()

def capabilities():
    return {'schema': SCHEMA, 'patient_fields': sorted(PATIENT_FIELDS),
            'record_fields': {k: sorted(v | COMMON) for k, v in RECORD_FIELDS.items()},
            'semantics': 'all predicates AND at least one any predicate (if present) AND no exclude predicate; unknown is preserved.',
            'age_basis': 'Birth date at snapshot cutoff', 'string_matching': 'Exact case-sensitive values; explicit code prefixes only.'}
