"""Semantic-model contract: the explicit mapping target for warehouse onboarding.

The contract is derived from clinical/semantic_layer.py (table DDL, SCHEMA_DOC meanings,
VERSIONED_STREAMS, MED_SOURCES, CARC) plus onboarding annotations that the semantic layer
keeps implicit: for each entity the *source grain* a customer warehouse supplies, which
fields are mapped from the source versus derived by Agere, storage and semantic types,
units, code systems, keys and foreign keys, and the versioning/dedup rule that turns
received rows into entities.

`build_model()` regenerates config/agere/semantic_model.json; semantic_layer.py is read,
never modified. A domain pack is the same JSON shape with different entities.
"""
from pathlib import Path
import json
import re
import sqlite3

from ..clinical import semantic_layer

ROOT = Path(__file__).resolve().parents[3]
MODEL_PATH = ROOT / 'config' / 'agere' / 'semantic_model.json'


def artifacts_root():
    """The checkout's artifacts/ directory; a worktree nested inside the main checkout uses the main one."""
    for base in [ROOT, *ROOT.parents]:
        if (base / 'artifacts' / 'agere').is_dir(): return base / 'artifacts'
    return ROOT / 'artifacts'


def onboarding_dir():
    return artifacts_root() / 'onboarding'
VERSION = 'agere-semantic-model/1'


def F(type_, semantic, *, required=False, key=False, fk=None, code_system=None, unit=None, aliases=(), doc='',
      values=None, role='mapped', column=True, name_required=False):
    spec = {'type': type_, 'semantic': semantic, 'required': required, 'role': role, 'column': column, 'description': doc}
    if name_required: spec['name_required'] = True
    if key: spec['key'] = True
    if fk: spec['foreign_key'] = fk
    if code_system: spec['code_system'] = code_system
    if unit: spec['unit'] = unit
    if aliases: spec['aliases'] = list(aliases)
    if values: spec['allowed_values'] = values
    return spec


def D(type_, doc):
    """A field Agere derives from mapped fields (never mapped from a source column)."""
    return F(type_, 'derived', role='derived', doc=doc)


# Shared field families.
PID = F('text', 'identifier', required=True, fk='patient.patient_id',
        aliases=('patient id', 'person id', 'member id', 'subject', 'mrn', 'pat id', 'beneficiary id'),
        doc='Patient the row belongs to (the source patient identifier, not a warehouse surrogate).')
RID = F('text', 'identifier', key=True, name_required=True,
        aliases=('record id', 'source row id', 'row id', 'source record', 'line id', 'source key'),
        doc='Unique identifier of the received source row (lineage; every semantic row keeps it). When the source has none, '
            'apply synthesizes <table>#<row number>.')
ORG = F('text', 'identifier', fk='organization.organization_id',
        aliases=('organization id', 'care site', 'facility', 'location', 'department', 'site id'),
        doc='Organization / care site where the event happened.')
AVAIL = F('text', 'date', name_required=True, aliases=('available date', 'received date', 'load date', 'etl date', 'arrival date', 'ingest date'),
          doc='Date the row became available to the warehouse (receipt clock, not the clinical event clock).')
EVENT = F('text', 'date', aliases=('event date', 'start date', 'service date', 'date'), doc='Clinical event date; NULL when unknown.')

ENTITIES = {
    'patient': {
        'table': 'patient', 'grain': 'one row per roster patient', 'keys': ['patient_id'],
        'versioning': None,
        'fields': {
            'patient_id': F('text', 'identifier', required=True, key=True,
                            aliases=('patient id', 'person source value', 'person id', 'member id', 'mrn', 'subject id'),
                            doc='Source patient identifier; prefer the source/human identifier over integer surrogates.'),
            'sex_reported': F('text', 'categorical', aliases=('sex', 'gender', 'administrative gender'), values=['F', 'M', 'U'],
                              doc='Reported sex as F/M/U.'),
            'age_reported': F('integer', 'integer', aliases=('age', 'age reported', 'age at capture'),
                              doc='Reported age at profile capture (not advanced).'),
            'birth_date': F('text', 'date', aliases=('birth date', 'date of birth', 'dob', 'birth datetime'), doc='Date of birth.'),
            'race': F('text', 'categorical', aliases=('race',), doc='Reported race.'),
            'ethnicity': F('text', 'categorical', aliases=('ethnicity', 'ethnic group'), doc='Reported ethnicity.'),
            'preferred_language': F('text', 'categorical', aliases=('language', 'preferred language'), doc='Preferred language.'),
            'zip': F('text', 'categorical', aliases=('zip', 'postal code', 'zip code'), code_system='ZIP', doc='Residence ZIP.'),
            'state': F('text', 'categorical', aliases=('state', 'province'), doc='Residence state.'),
            'county': F('text', 'categorical', aliases=('county', 'county fips', 'fips'), doc='Residence county FIPS.'),
            'coverage_type': D('text', 'Latest coverage record coverage_type (from the coverage entity).'),
            'coverage_status': D('text', 'Latest coverage record status (from the coverage entity).'),
        }},
    'coverage': {
        'table': None, 'grain': 'one row per received coverage / enrollment period record', 'keys': [],
        'versioning': {'rule': 'latest_by', 'partition': ['patient_id'], 'order': [['event_date', 'desc']],
                       'feeds': {'patient.coverage_type': 'coverage_type', 'patient.coverage_status': 'status'}},
        'fields': {
            'patient_id': PID,
            'coverage_type': F('text', 'categorical', required=True, aliases=('coverage type', 'payer', 'plan type', 'line of business', 'insurance type', 'financial class'),
                               doc='Coverage category (commercial, medicare, medicaid, uninsured, ...).'),
            'status': F('text', 'categorical', aliases=('status', 'coverage status', 'enrollment status'), doc='Coverage status.'),
            'event_date': F('text', 'date', aliases=('start date', 'effective date', 'period start', 'enrollment date'), doc='Coverage effective date.'),
        }},
    'visit': {
        'table': 'visit', 'grain': 'one row per received encounter record (status versions repeat the encounter)',
        'keys': ['encounter_id'],
        'versioning': {'rule': 'latest_version', 'key': 'encounter_id', 'null_key': 'own_entity',
                       'order': [["status = 'completed'", 'desc'], ['available_date', 'desc'], ['record_id', 'desc']],
                       'count_field': 'versions',
                       'source': "semantic_layer.build(): visit = row_number() over coalesce(encounter_id, record_id) "
                                 "order by (status='completed') desc, available_date desc, record_id desc"},
        'fields': {
            'record_id': RID,
            'encounter_id': F('text', 'identifier', aliases=('encounter id', 'encounter number', 'visit number', 'csn', 'encounter key', 'visit id'),
                              doc='Business encounter identifier shared by all versions of one encounter.'),
            'patient_id': PID,
            'type': F('text', 'categorical', required=True, aliases=('type', 'encounter type', 'visit type', 'visit source', 'class'),
                      doc='Encounter type as received (office, telephone, emergency, inpatient, ...).'),
            'status': F('text', 'categorical', aliases=('status', 'encounter status', 'visit status'), doc='Encounter status (completed, in_progress, ...).'),
            'event_date': F('text', 'date', aliases=('visit start date', 'start date', 'admit date', 'event date', 'encounter date', 'contact date'),
                            doc='Encounter start date.'),
            'end_date': F('text', 'date', aliases=('end date', 'discharge date', 'visit end date', 'discharge time'), doc='Encounter end date.'),
            'available_date': AVAIL,
            'organization_id': ORG,
            'attending_clinician_id': F('text', 'identifier', fk='clinician.clinician_id',
                                        aliases=('attending clinician', 'provider id', 'attending provider', 'clinician id', 'practitioner'),
                                        doc='Attending clinician.'),
            'disposition': F('text', 'categorical', aliases=('disposition', 'discharged to', 'discharge disposition'), doc='Disposition.'),
            'place_of_service': F('text', 'categorical', code_system='CMS place of service', aliases=('place of service', 'pos'),
                                  doc='CMS place-of-service code.'),
            'versions': D('integer', 'Number of received versions of the encounter.'),
        }},
    'condition': {
        'table': 'condition', 'grain': 'one row per received condition record', 'keys': ['record_id'], 'versioning': None,
        'fields': {
            'record_id': RID, 'patient_id': PID,
            'label': F('text', 'text', aliases=('description', 'condition name', 'diagnosis name', 'diagnosis description', 'problem', 'label', 'display'),
                       doc='Received condition description.'),
            'icd10': F('text', 'code', code_system='ICD-10-CM', aliases=('icd10', 'diagnosis code', 'dx code', 'condition code', 'condition source value'),
                       doc='ICD-10-CM code, dotted canonical form.'),
            'snomed': F('text', 'code', code_system='SNOMED CT', aliases=('snomed', 'concept code'), doc='SNOMED CT code.'),
            'onset': F('text', 'text', aliases=('onset', 'onset date', 'onset text'), doc='Onset as received.'),
            'event_date': F('text', 'date', aliases=('condition start date', 'start date', 'diagnosis date', 'event date', 'contact date'), doc='Condition date.'),
            'recorded_date': F('text', 'date', aliases=('recorded date', 'entered date', 'noted date'), doc='Recorded date.'),
            'available_date': AVAIL, 'organization_id': ORG,
            'concept': D('text', 'Agere concept from ICD-10 prefix / label (concepts.concept_for_code).'),
        }},
    'measurement': {
        'table': 'measurement', 'grain': 'one row per received lab or vital result', 'keys': ['record_id'], 'versioning': None,
        'fields': {
            'record_id': RID, 'patient_id': PID,
            'name': F('text', 'categorical', required=True, aliases=('test name', 'measurement name', 'lab name', 'component', 'measurement source', 'observation name'),
                      doc='Local test / measurement name.'),
            'loinc': F('text', 'code', code_system='LOINC', aliases=('loinc', 'test code', 'measurement code', 'observation code', 'source code'),
                       doc='LOINC code when received.'),
            'value': F('real', 'decimal', required=True, aliases=('value', 'result', 'value as number', 'numeric value', 'result value'),
                       doc='Numeric result as received.'),
            'unit': F('text', 'categorical', aliases=('unit', 'units', 'unit source', 'uom'), doc='Unit as received (UCUM-like text).'),
            'result_status': F('text', 'categorical', aliases=('result status', 'status'), doc='Result status (final, cancelled, ...).'),
            'event_date': F('text', 'date', aliases=('measurement date', 'collection date', 'result date', 'event date'), doc='Collection/event date; NULL when unknown.'),
            'available_date': AVAIL,
            'reference_range': F('text', 'text', aliases=('reference range', 'range', 'normal range'), doc='Reference range text.'),
            'abnormal_flag': F('text', 'categorical', aliases=('abnormal flag', 'flag', 'interpretation'), doc='Abnormal flag.'),
            'organization_id': ORG,
            'concept': D('text', 'Agere measurement concept (name pattern, then LOINC; concepts.MEASUREMENTS).'),
            'value_canonical': D('real', 'value converted to the concept canonical unit (concepts.MEASUREMENTS[c].units).'),
            'unit_canonical': D('text', 'Canonical unit of the concept.'),
        }},
    'medication': {
        'table': 'medication', 'grain': 'one row per received medication mention (order, dispense, claim, list, administration, report)',
        'keys': ['record_id'], 'versioning': None,
        'fields': {
            'record_id': RID, 'patient_id': PID,
            'drug_name': F('text', 'text', required=True, aliases=('drug name', 'medication name', 'drug source value', 'medication', 'product name'),
                           doc='Medication name as received.'),
            'evidence': F('text', 'categorical', aliases=('evidence', 'drug type', 'source type', 'record type', 'exposure type'),
                          values=sorted({m[3] for m in semantic_layer.MED_SOURCES}),
                          doc='What kind of evidence the row is: ' + ', '.join(m[3] for m in semantic_layer.MED_SOURCES) + '.'),
            'status': F('text', 'categorical', aliases=('status', 'drug status', 'order status'), doc='Status as received.'),
            'rxnorm': F('text', 'code', code_system='RxNorm', aliases=('rxnorm', 'rxcui', 'drug code', 'drug concept code', 'drug source code'),
                        doc='RxNorm CUI as received.'),
            'ndc': F('text', 'code', code_system='NDC', aliases=('ndc', 'national drug code', 'drug ndc'), doc='NDC as received.'),
            'event_date': F('text', 'date', aliases=('start date', 'exposure start', 'order date', 'fill date', 'event date'), doc='Event date.'),
            'available_date': AVAIL,
            'drug_class': D('text', 'Drug class from the active ingredient (drug_classes.classify).'),
        }},
    'claim': {
        'table': 'claim', 'grain': 'one row per received claim adjudication record (denied, paid, reversed, re-paid)',
        'keys': ['claim_id'],
        'versioning': {'rule': 'claim_settlement', 'key': 'claim_id', 'null_key': 'exclude',
                       'order': [['available_date', 'asc'], ['available_time', 'asc'], ['record_id', 'asc']],
                       'source': 'semantic_layer._derived() claim aggregate + semantic_layer.settle_history() adjudication chain'},
        'fields': {
            'record_id': RID,
            'claim_id': F('text', 'identifier', required=True, aliases=('claim id', 'claim number', 'claim source value', 'icn', 'claim key'),
                          doc='Business claim identifier shared by every adjudication of the claim.'),
            'patient_id': PID,
            'payer': F('text', 'categorical', aliases=('payer', 'payer source', 'payer id', 'source system', 'plan'), doc='Payer.'),
            'service_date': F('text', 'date', aliases=('service date', 'date of service', 'from date'), doc='Service date.'),
            'status': F('text', 'categorical', required=True, aliases=('status', 'adjudication status', 'claim status'),
                        values=['paid', 'denied', 'reversed', 'pending'], doc='Adjudication status.'),
            'payer_paid_cents': F('integer', 'money_cents', unit='USD cents', aliases=('payer paid', 'paid by payer', 'plan paid', 'paid amount'),
                                  doc='Amount the payer paid on this adjudication.'),
            'billed_cents': F('integer', 'money_cents', unit='USD cents', aliases=('billed', 'charge', 'total charge', 'billed amount', 'submitted charge'),
                              doc='Billed charge.'),
            'allowed_cents': F('integer', 'money_cents', unit='USD cents', aliases=('allowed', 'allowed amount', 'total allowed'), doc='Allowed amount.'),
            'patient_responsibility_cents': F('integer', 'money_cents', unit='USD cents',
                                              aliases=('patient responsibility', 'paid by patient', 'member liability', 'patient paid'),
                                              doc='Patient responsibility.'),
            'event_date': F('text', 'date', aliases=('adjudication date', 'adjudicated date', 'processed date', 'paid date'), doc='Adjudication date.'),
            'available_date': AVAIL,
            'available_time': F('text', 'datetime', name_required=True, aliases=('received datetime', 'available time', 'load timestamp'), doc='Receipt timestamp.'),
            'service_organization_id': F('text', 'identifier', fk='organization.organization_id',
                                         aliases=('service organization', 'care site', 'billing facility', 'facility'), doc='Servicing organization.'),
            'reverses_record_id': F('text', 'identifier', name_required=True, aliases=('reverses', 'original adjudication', 'reversal of', 'reverses source row'),
                                    doc='For reversals, the record_id of the adjudication being reversed.'),
            'first_status': D('text', 'Status of the first-arriving adjudication.'),
            'final_status': D('text', 'Status after the adjudication chain (settle_history).'),
            'net_payer_paid_cents': D('integer', 'Sum paid minus sum reversed.'),
            'versions': D('integer', 'Adjudication records received for the claim.'),
        }},
    'claim_line': {
        'table': 'claim_line', 'grain': 'one row per received claim line', 'keys': ['record_id'], 'versioning': None,
        'fields': {
            'record_id': RID,
            'claim_id': F('text', 'identifier', required=True, fk='claim.claim_id', aliases=('claim id', 'claim number', 'claim source value', 'claim key'),
                          doc='Claim of the line.'),
            'patient_id': {**PID, 'required': False, 'description': PID['description'] + ' Optional for lines: derived through claim_id when absent.'},
            'line_number': F('integer', 'integer', aliases=('line number', 'line', 'line seq'), doc='Line number.'),
            'service_date': F('text', 'date', aliases=('service date', 'procedure date', 'line date'), doc='Line service date.'),
            'service_code': F('text', 'code', code_system='CPT/HCPCS', required=True, aliases=('procedure code', 'service code', 'cpt', 'hcpcs', 'procedure source value'),
                              doc='CPT or HCPCS code (split into cpt/hcpcs columns).'),
            'code_system': F('text', 'categorical', aliases=('code system', 'vocabulary', 'procedure source vocabulary', 'coding system'),
                             doc='Code system of service_code.'),
            'revenue_code': F('text', 'categorical', code_system='NUBC revenue code', aliases=('revenue code', 'rev code'), doc='UB-04 revenue code.'),
            'units': F('real', 'decimal', aliases=('units', 'quantity', 'service units'), doc='Units.'),
            'charge_cents': F('integer', 'money_cents', unit='USD cents', aliases=('line charge', 'charge', 'charge amount'), doc='Line charge.'),
            'service_organization_id': F('text', 'identifier', fk='organization.organization_id', aliases=('care site', 'facility', 'service organization'), doc='Servicing org.'),
        }},
    'clinician': {
        'table': 'clinician', 'grain': 'one row per clinician', 'keys': ['clinician_id'], 'versioning': None,
        'fields': {
            'clinician_id': F('text', 'identifier', required=True, key=True,
                              aliases=('clinician id', 'provider source value', 'provider id', 'practitioner id', 'prov id'), doc='Clinician identifier.'),
            'organization_id': ORG,
            'specialty': F('text', 'categorical', aliases=('specialty', 'specialty source value'), doc='Specialty.'),
            'npi': F('text', 'code', code_system='NPI', aliases=('npi', 'national provider identifier'), doc='NPI.'),
            'taxonomy_code': F('text', 'code', code_system='NUCC taxonomy', aliases=('taxonomy', 'taxonomy code'), doc='NUCC taxonomy.'),
        }},
    'organization': {
        'table': 'organization', 'grain': 'one row per organization / care site', 'keys': ['organization_id'], 'versioning': None,
        'fields': {
            'organization_id': F('text', 'identifier', required=True, key=True,
                                 aliases=('organization id', 'care site source value', 'facility id', 'site id', 'org id', 'department id', 'location id'),
                                 doc='Organization identifier (department, care site, facility).'),
            'organization_type': F('text', 'categorical', aliases=('organization type', 'facility type', 'site type', 'place of service'), doc='Organization type.'),
            'display_name': F('text', 'text', aliases=('name', 'display name', 'organization name', 'care site name'), doc='Name.'),
            'npi': F('text', 'code', code_system='NPI', aliases=('npi',), doc='Organization NPI.'),
            'taxonomy_code': F('text', 'code', code_system='NUCC taxonomy', aliases=('taxonomy', 'taxonomy code'), doc='NUCC taxonomy.'),
            'tin': F('text', 'code', code_system='TIN', aliases=('tin', 'tax id', 'ein', 'tax identification number'), doc='Tax identification number.'),
            'city': F('text', 'categorical', aliases=('city',), doc='City.'),
            'state': F('text', 'categorical', aliases=('state',), doc='State.'),
            'postal_code': F('text', 'categorical', code_system='ZIP', aliases=('zip', 'postal code'), doc='ZIP.'),
        }},
}

DERIVED_ONLY = {  # semantic tables built by Agere from other entities or free text; never mapped from a source column
    'stay': 'Collapsed inpatient/observation stays from visits (aggregate._stays).',
    'concept_patient': 'Patients per library concept from condition/measurement/encounter rows.',
    'measurement_latest': 'Latest dated measurement per patient and concept.',
    'current_record': 'Latest version per business id in versioned streams (VERSIONED_STREAMS).',
    'link': 'Record-to-record references (fields ending _record_id/_record_ids).',
    'note_fact': 'Facts extracted from free text by the note-fact rules.',
    'social_need': 'Normalised social-needs answers from social/financial screens.',
    'appointment': 'Appointment entity from booked + status records (not yet an onboarding target).',
    'claim_adjustment': 'X12 CARC adjustment lines (not yet an onboarding target; needs 835 adjustment segments).',
    'encounter': 'Encounter + ADT rows (visit is the onboarding target).',
    'encounter_diagnosis': 'Encounter-coded ICD-10 diagnoses (not yet an onboarding target).',
    'immunization': 'Immunization rows (not yet an onboarding target).',
    'record': 'Every received record (the onboarding lineage table plays this role).',
}


def _ddl_columns():
    """Columns of every semantic table as declared in semantic_layer.DDL (base columns; ALTERs add more)."""
    db = sqlite3.connect(':memory:')
    db.executescript(semantic_layer.DDL)
    out = {}
    for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        out[name] = {r[1]: (r[2] or 'TEXT').lower() for r in db.execute(f'PRAGMA table_info({name})')}
    db.close()
    return out


def _altered_columns():
    """Columns semantic_layer adds after the DDL (ALTER TABLE ... ADD COLUMN / _add(...)), read from its source text."""
    src = Path(semantic_layer.__file__).read_text()
    out = {}
    for table, col, typ in re.findall(r"ALTER TABLE (\w+) ADD COLUMN (\w+) (\w+)", src):
        out.setdefault(table, {})[col] = typ.lower()
    for table, body in re.findall(r"_add\(out, '(\w+)', ([^\n]*)\)", src):
        for col, typ in re.findall(r"'(\w+) (TEXT|INTEGER|REAL)'", body):
            out.setdefault(table, {})[col] = typ.lower()
    # Tables created later in executescript blocks (claim_line, clinician, organization, encounter_diagnosis, immunization).
    db = sqlite3.connect(':memory:')
    for stmt in re.findall(r"CREATE TABLE \w+\([^;]*?\);", src):
        name = re.match(r"CREATE TABLE (\w+)", stmt).group(1)
        if name in out and name in _ddl_columns(): continue
        try:
            db.execute(stmt)
        except sqlite3.Error:
            continue
        for r in db.execute(f'PRAGMA table_info({name})'):
            out.setdefault(name, {})[r[1]] = (r[2] or 'TEXT').lower()
    db.close()
    return out


def build_model():
    ddl = _ddl_columns()
    for table, cols in _altered_columns().items():
        for c, t in cols.items(): ddl.setdefault(table, {}).setdefault(c, t)
    entities = {}
    for name, e in ENTITIES.items():
        table = e['table']
        cols = ddl.get(table, {}) if table else {}
        fields = {k: dict(v) for k, v in e['fields'].items()}
        for k, v in fields.items():
            v['column'] = bool(table) and k in cols
        other = sorted(c for c in cols if c not in fields)
        entities[name] = {'table': table, 'meaning': semantic_layer.SCHEMA_DOC.get(table or '', ''), 'grain': e['grain'],
                          'keys': e['keys'], 'versioning': e['versioning'], 'fields': fields,
                          'semantic_layer_columns_derived_elsewhere': other, 'onboarding_target': True}
    for table, doc in DERIVED_ONLY.items():
        entities[table] = {'table': table, 'meaning': semantic_layer.SCHEMA_DOC.get(table, ''), 'derivation': doc,
                           'columns': ddl.get(table, {}), 'onboarding_target': False}
    return {'schema': VERSION, 'domain': 'healthcare',
            'derived_from': {'module': 'clinical_core/agere/clinical/semantic_layer.py', 'semantic_layer_version': semantic_layer.VERSION,
                             'versioned_streams': semantic_layer.VERSIONED_STREAMS,
                             'carc_categories': {f'{g}-{c}': v for (g, c), v in semantic_layer.CARC.items()}},
            'conventions': {'money': 'integer USD cents', 'dates': 'ISO YYYY-MM-DD text; NULL when unknown (never substituted by receipt date)',
                            'codes': 'ICD-10-CM dotted upper case; LOINC with check digit; NDC 11-digit; NPI 10-digit Luhn',
                            'lineage': 'every mapped row keeps the source row identifier in record_id',
                            'approval': 'a mapping is applied only when approval.approved is true and only fields with accept: true'},
            'entities': entities}


def write_model(path=MODEL_PATH):
    model = build_model()
    Path(path).write_text(json.dumps(model, indent=1, sort_keys=False) + '\n')
    return model


def load_model(path=MODEL_PATH):
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else build_model()


def targets(model):
    return {k: v for k, v in model['entities'].items() if v.get('onboarding_target')}
