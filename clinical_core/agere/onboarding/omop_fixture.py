"""Reshape the synthetic healthcare warehouse into an OMOP-CDM-like layout (a *different* schema).

Used to prove onboarding: the onboarding pipeline sees only this OMOP-shaped SQLite file
and must recover Agere's semantic layer; the native build of the same patients is the
reference. The reshape deliberately changes what a real CDM/ETL changes:

* integer surrogate keys everywhere (person_id, provider_id, care_site_id, ...), with the
  original identifiers kept only in *_source_value columns;
* money in decimal dollars (cost, procedure_occurrence) instead of integer cents;
* ICD-10-CM codes without the dot (E11319);
* glucose results converted to mmol/L;
* sex spelled female/male; versions of one encounter as separate visit_occurrence rows;
* claim lines in procedure_occurrence; coverage in payer_plan_period; plus an unmappable
  observation_period table.

Only the public warehouse tables (patients, source_records, organizations, clinicians) are read.
"""
from collections import defaultdict
from pathlib import Path
import hashlib
import json
import sqlite3

from ..healthcare_warehouse import project
from ..clinical import concepts, semantic_layer

DDL = """
CREATE TABLE person(person_id INTEGER PRIMARY KEY, person_source_value TEXT, gender_source_value TEXT, year_of_birth INTEGER,
  month_of_birth INTEGER, birth_datetime TEXT, race_source_value TEXT, ethnicity_source_value TEXT, language_source_value TEXT,
  location_zip TEXT, location_state TEXT, location_county_fips TEXT);
CREATE TABLE care_site(care_site_id INTEGER PRIMARY KEY, care_site_source_value TEXT, care_site_name TEXT, place_of_service_source_value TEXT,
  npi TEXT, tin TEXT, taxonomy_source_value TEXT, city TEXT, state TEXT, zip TEXT);
CREATE TABLE provider(provider_id INTEGER PRIMARY KEY, provider_source_value TEXT, provider_name TEXT, npi TEXT, specialty_source_value TEXT,
  taxonomy_source_value TEXT, care_site_id INTEGER);
CREATE TABLE visit_occurrence(visit_occurrence_id INTEGER PRIMARY KEY, person_id INTEGER, visit_concept_id INTEGER, visit_start_date TEXT,
  visit_start_datetime TEXT, visit_end_date TEXT, visit_source_value TEXT, visit_status_source_value TEXT, provider_id INTEGER, care_site_id INTEGER,
  discharged_to_source_value TEXT, place_of_service_source_value TEXT, encounter_number TEXT, source_row_id TEXT, etl_received_date TEXT);
CREATE TABLE condition_occurrence(condition_occurrence_id INTEGER PRIMARY KEY, person_id INTEGER, condition_start_date TEXT,
  condition_source_value TEXT, condition_source_description TEXT, condition_concept_code TEXT, condition_onset_text TEXT,
  condition_recorded_date TEXT, care_site_id INTEGER, source_row_id TEXT, etl_received_date TEXT);
CREATE TABLE measurement(measurement_id INTEGER PRIMARY KEY, person_id INTEGER, measurement_date TEXT, measurement_datetime TEXT,
  measurement_source_value TEXT, measurement_source_code TEXT, value_as_number REAL, value_source_value TEXT, unit_source_value TEXT,
  range_source_value TEXT, abnormal_flag_source_value TEXT, result_status_source_value TEXT, care_site_id INTEGER, source_row_id TEXT,
  etl_received_date TEXT);
CREATE TABLE drug_exposure(drug_exposure_id INTEGER PRIMARY KEY, person_id INTEGER, drug_exposure_start_date TEXT, drug_source_value TEXT,
  drug_source_code TEXT, drug_ndc_code TEXT, drug_type_source_value TEXT, drug_status_source_value TEXT, source_row_id TEXT,
  etl_received_date TEXT);
CREATE TABLE procedure_occurrence(procedure_occurrence_id INTEGER PRIMARY KEY, person_id INTEGER, procedure_date TEXT,
  procedure_source_value TEXT, procedure_source_vocabulary TEXT, quantity REAL, line_charge_amount REAL, claim_source_value TEXT,
  line_number INTEGER, revenue_source_value TEXT, care_site_id INTEGER, source_row_id TEXT);
CREATE TABLE payer_plan_period(payer_plan_period_id INTEGER PRIMARY KEY, person_id INTEGER, payer_plan_period_start_date TEXT,
  payer_plan_period_end_date TEXT, payer_source_value TEXT, plan_status_source_value TEXT, plan_source_value TEXT);
CREATE TABLE cost(cost_id INTEGER PRIMARY KEY, person_id INTEGER, claim_source_value TEXT, source_row_id TEXT, reverses_source_row_id TEXT,
  payer_source_value TEXT, service_date TEXT, adjudication_status_source_value TEXT, total_charge REAL, total_allowed REAL,
  paid_by_payer REAL, paid_by_patient REAL, adjudicated_date TEXT, received_date TEXT, received_datetime TEXT, care_site_id INTEGER);
CREATE TABLE observation_period(observation_period_id INTEGER PRIMARY KEY, person_id INTEGER, observation_period_start_date TEXT,
  observation_period_end_date TEXT, period_type_concept_id INTEGER);
"""

# Known truth: target entity -> (source table, {target field: source column}). FK fields that need a lookup
# through a surrogate key are recorded as (column, lookup_table, lookup_key, lookup_value).
TRUTH = {
    'patient': ('person', {'patient_id': 'person_source_value', 'sex_reported': 'gender_source_value', 'birth_date': 'birth_datetime',
                           'race': 'race_source_value', 'ethnicity': 'ethnicity_source_value', 'preferred_language': 'language_source_value',
                           'zip': 'location_zip', 'state': 'location_state', 'county': 'location_county_fips'}),
    'coverage': ('payer_plan_period', {'patient_id': 'person_id', 'coverage_type': 'payer_source_value', 'status': 'plan_status_source_value',
                                       'event_date': 'payer_plan_period_start_date'}),
    'visit': ('visit_occurrence', {'record_id': 'source_row_id', 'encounter_id': 'encounter_number', 'patient_id': 'person_id',
                                   'type': 'visit_source_value', 'status': 'visit_status_source_value', 'event_date': 'visit_start_date',
                                   'end_date': 'visit_end_date', 'available_date': 'etl_received_date', 'organization_id': 'care_site_id',
                                   'attending_clinician_id': 'provider_id', 'disposition': 'discharged_to_source_value',
                                   'place_of_service': 'place_of_service_source_value'}),
    'condition': ('condition_occurrence', {'record_id': 'source_row_id', 'patient_id': 'person_id', 'label': 'condition_source_description',
                                           'icd10': 'condition_source_value', 'snomed': 'condition_concept_code', 'onset': 'condition_onset_text',
                                           'event_date': 'condition_start_date', 'recorded_date': 'condition_recorded_date',
                                           'available_date': 'etl_received_date', 'organization_id': 'care_site_id'}),
    'measurement': ('measurement', {'record_id': 'source_row_id', 'patient_id': 'person_id', 'name': 'measurement_source_value',
                                    'loinc': 'measurement_source_code', 'value': 'value_as_number', 'unit': 'unit_source_value',
                                    'result_status': 'result_status_source_value', 'event_date': 'measurement_date',
                                    'available_date': 'etl_received_date', 'reference_range': 'range_source_value',
                                    'abnormal_flag': 'abnormal_flag_source_value', 'organization_id': 'care_site_id'}),
    'medication': ('drug_exposure', {'record_id': 'source_row_id', 'patient_id': 'person_id', 'drug_name': 'drug_source_value',
                                     'evidence': 'drug_type_source_value', 'status': 'drug_status_source_value', 'rxnorm': 'drug_source_code',
                                     'ndc': 'drug_ndc_code', 'event_date': 'drug_exposure_start_date', 'available_date': 'etl_received_date'}),
    'claim': ('cost', {'record_id': 'source_row_id', 'claim_id': 'claim_source_value', 'patient_id': 'person_id', 'payer': 'payer_source_value',
                       'service_date': 'service_date', 'status': 'adjudication_status_source_value', 'payer_paid_cents': 'paid_by_payer',
                       'billed_cents': 'total_charge', 'allowed_cents': 'total_allowed', 'patient_responsibility_cents': 'paid_by_patient',
                       'event_date': 'adjudicated_date', 'available_date': 'received_date', 'available_time': 'received_datetime',
                       'service_organization_id': 'care_site_id', 'reverses_record_id': 'reverses_source_row_id'}),
    'claim_line': ('procedure_occurrence', {'record_id': 'source_row_id', 'claim_id': 'claim_source_value', 'patient_id': 'person_id',
                                            'line_number': 'line_number', 'service_date': 'procedure_date', 'service_code': 'procedure_source_value',
                                            'code_system': 'procedure_source_vocabulary', 'revenue_code': 'revenue_source_value',
                                            'units': 'quantity', 'charge_cents': 'line_charge_amount', 'service_organization_id': 'care_site_id'}),
    'clinician': ('provider', {'clinician_id': 'provider_source_value', 'organization_id': 'care_site_id', 'specialty': 'specialty_source_value',
                               'npi': 'npi', 'taxonomy_code': 'taxonomy_source_value'}),
    'organization': ('care_site', {'organization_id': 'care_site_source_value', 'organization_type': 'place_of_service_source_value',
                                   'display_name': 'care_site_name', 'npi': 'npi', 'taxonomy_code': 'taxonomy_source_value', 'tin': 'tin',
                                   'city': 'city', 'state': 'state', 'postal_code': 'zip'}),
}
UNMAPPED_TABLES = ['observation_period']
VISIT_CONCEPT = {'inpatient': 9201, 'emergency': 9203, 'observation': 581385, 'telephone': 5083, 'home_health': 581476}


def sample_patients(source, n, seed='agere-onboarding-1'):
    """Deterministic patient sample: the n roster IDs with the smallest sha256(seed + id)."""
    db = sqlite3.connect(f'file:{source}?mode=ro', uri=True)
    try:
        ids = [r[0] for r in db.execute('SELECT patient_id FROM patients')]
    finally:
        db.close()
    return sorted(sorted(ids, key=lambda p: hashlib.sha256((seed + p).encode()).hexdigest())[:n])


def _d(v): return (v or '')[:10] or None


def _dollars(cents): return None if cents is None else round(cents / 100.0, 2)


def build(source, out, patient_ids):
    """Write the OMOP-shaped SQLite file for the given patients. Returns counts."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    src = sqlite3.connect(f'file:{source}?mode=ro', uri=True)
    src.row_factory = sqlite3.Row
    dst = sqlite3.connect(out)
    dst.executescript(DDL)
    counts = defaultdict(int)
    try:
        orgs = [dict(r) for r in src.execute('SELECT * FROM organizations ORDER BY organization_id')]
        site = {o['organization_id']: i + 1 for i, o in enumerate(orgs)}
        dst.executemany('INSERT INTO care_site VALUES (?,?,?,?,?,?,?,?,?,?)',
                        [(site[o['organization_id']], o['organization_id'], o.get('display_name'), o.get('organization_type'), o.get('npi'),
                          o.get('tin'), o.get('taxonomy_code'), o.get('city'), o.get('state'), o.get('postal_code')) for o in orgs])
        clins = [dict(r) for r in src.execute('SELECT * FROM clinicians ORDER BY clinician_id')]
        prov = {c['clinician_id']: 1000 + i for i, c in enumerate(clins)}
        dst.executemany('INSERT INTO provider VALUES (?,?,?,?,?,?,?)',
                        [(prov[c['clinician_id']], c['clinician_id'], c.get('display_name'), c.get('npi'), c.get('specialty'),
                          c.get('taxonomy_code'), site.get(c.get('organization_id'))) for c in clins])
        person = {}
        ids = {k: 0 for k in ('visit', 'cond', 'meas', 'drug', 'proc', 'ppp', 'cost')}

        def nxt(k):
            ids[k] += 1; return ids[k]
        med_streams = {m[0]: m for m in semantic_layer.MED_SOURCES}
        for i, pid in enumerate(patient_ids):
            p = dict(src.execute('SELECT * FROM patients WHERE patient_id = ?', (pid,)).fetchone())
            person[pid] = 50000 + i * 7
            bd = p.get('birth_date')
            dst.execute('INSERT INTO person VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                        (person[pid], pid, {'F': 'female', 'M': 'male'}.get(p.get('sex_reported'), p.get('sex_reported')),
                         int(bd[:4]) if bd else None, int(bd[5:7]) if bd else None, (bd + 'T00:00:00') if bd else None,
                         p.get('race'), p.get('ethnicity'), p.get('preferred_language'), p.get('postal_code'), p.get('state'), p.get('county_fips')))
            dates = []
            for raw in src.execute('SELECT * FROM source_records WHERE patient_id = ? ORDER BY record_id', (pid,)):
                r = project(raw)
                st, rid = r['source_table'], r['record_id']
                pe = person[pid]
                if r.get('event_date'): dates.append(r['event_date'])
                if st == 'encounters':
                    t = r.get('type')
                    dst.execute('INSERT INTO visit_occurrence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                (nxt('visit'), pe, VISIT_CONCEPT.get(t, 9202), _d(r.get('event_date')), r.get('event_time') or r.get('start_time'),
                                 _d(r.get('end_time')), t, r.get('status'), prov.get(r.get('attending_clinician_id')), site.get(r.get('organization_id')),
                                 r.get('disposition'), r.get('place_of_service'), r.get('encounter_id'), rid, _d(r.get('available_date'))))
                elif st == 'conditions':
                    code = r.get('icd10')
                    dst.execute('INSERT INTO condition_occurrence VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                                (nxt('cond'), pe, _d(r.get('event_date')), code.replace('.', '') if isinstance(code, str) else code,
                                 r.get('description'), r.get('snomed'), r.get('onset'), _d(r.get('recorded_date')), site.get(r.get('organization_id')),
                                 rid, _d(r.get('available_date'))))
                elif st in ('labs', 'vitals'):
                    spec = concepts.MEASUREMENT_STREAMS[st]
                    name = r.get(spec['name']) or next((r.get(f) for f in spec['alt_names'] if r.get(f)), None)
                    if name is None: continue
                    value, unit = r.get('value'), r.get('unit')
                    num = value if type(value) in (int, float) else None
                    if st == 'labs' and num is not None and unit == 'mg/dL' and concepts.concept_for_label(name) == 'glucose':
                        num, unit = num / 18.016, 'mmol/L'
                    dst.execute('INSERT INTO measurement VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                (nxt('meas'), pe, _d(r.get('event_date')), r.get('event_time'), name, r.get('loinc'), num,
                                 None if value is None else str(value), unit, r.get('reference_range'), r.get('abnormal_flag'),
                                 r.get('result_status'), site.get(r.get('organization_id')), rid, _d(r.get('available_date'))))
                elif st in med_streams:
                    _, name_f, status_f, evidence = med_streams[st]
                    if not r.get(name_f): continue
                    dst.execute('INSERT INTO drug_exposure VALUES (?,?,?,?,?,?,?,?,?,?)',
                                (nxt('drug'), pe, _d(r.get('event_date')), r.get(name_f), None if r.get('rxcui') is None else str(r.get('rxcui')),
                                 None if r.get('ndc') is None else str(r.get('ndc')), evidence, r.get(status_f) if status_f else None, rid,
                                 _d(r.get('available_date'))))
                elif st == 'medical_claim_lines':
                    dst.execute('INSERT INTO procedure_occurrence VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                                (nxt('proc'), pe, r.get('service_date'), r.get('service_code'), r.get('service_code_system'), r.get('units'),
                                 _dollars(r.get('charge_cents')), r.get('claim_id'), r.get('line_number'), r.get('revenue_code'),
                                 site.get(r.get('service_organization_id')), rid))
                elif st == 'medical_claims':
                    dst.execute('INSERT INTO cost VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                (nxt('cost'), pe, r.get('claim_id'), rid, r.get('original_adjudication_record_id'), r.get('source_system'),
                                 r.get('service_date'), r.get('status'), _dollars(r.get('billed_cents')), _dollars(r.get('allowed_cents')),
                                 _dollars(r.get('payer_paid_cents')), _dollars(r.get('patient_responsibility_cents')), _d(r.get('event_date')),
                                 _d(r.get('available_date')), r.get('available_time'), site.get(r.get('service_organization_id'))))
                elif st == 'coverage':
                    dst.execute('INSERT INTO payer_plan_period VALUES (?,?,?,?,?,?,?)',
                                (nxt('ppp'), pe, _d(r.get('event_date')), r.get('effective_end'), r.get('coverage_type'), r.get('status'),
                                 r.get('enrollment_source')))
                else:
                    continue
                counts[st] += 1
            if dates:
                dst.execute('INSERT INTO observation_period VALUES (?,?,?,?,?)', (i + 1, person[pid], min(dates), max(dates), 32817))
        dst.commit()
        counts['person'] = len(person)
        return dict(counts)
    finally:
        src.close(); dst.close()
