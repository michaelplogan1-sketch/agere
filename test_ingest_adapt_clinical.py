"""Tests for clinical_core.agere.ingest.adapt_clinical (run: .venv/bin/python -m unittest test_ingest_adapt_clinical).

Hand-built landing rows (synthetic only) for each source-of-truth rule; a stub resolver stands in for identity.py.
"""
import base64
import json
import sqlite3
import unittest

from clinical_core.agere.ingest import adapt_clinical as ac


def table(db, name, rows, *, feed='ehr', instance=None, drop='2027-01', file=None):
    """Create/append a landing table: rows are dicts of sender columns; lineage columns are filled in."""
    cols = ['_feed', '_instance', '_drop', '_file', '_row']
    full = []
    for i, r in enumerate(rows):
        r = dict(r)
        r.setdefault('_feed', feed)
        r.setdefault('_instance', instance)
        r.setdefault('_drop', drop)
        r.setdefault('_file', file or f'{feed}/{instance}/{drop}/{name}.txt')
        r.setdefault('_row', i)
        full.append(r)
        cols += [k for k in r if k not in cols]
    have = [c[1] for c in db.execute(f'PRAGMA table_info("{name}")')]
    if not have:
        db.execute(f'CREATE TABLE "{name}" (' + ', '.join(f'"{c}"' for c in cols) + ')')
    else:
        for c in cols:
            if c not in have:
                db.execute(f'ALTER TABLE "{name}" ADD COLUMN "{c}"')
    for r in full:
        db.execute(f'INSERT INTO "{name}" (' + ', '.join(f'"{k}"' for k in r) + ') VALUES (' + ','.join('?' * len(r)) + ')',
                   tuple(r.values()))


def stub_resolve(key):
    """Every native / MRN / HL7 key is its own person, except keys marked unknown."""
    return None if key is None or 'UNKNOWN' in key else 'P|' + key


def run(db, resolve=stub_resolve):
    recs = list(ac.records(db, resolve))
    by = {}
    for r in recs:
        by.setdefault(r['source_table'], []).append(r)
    return recs, by, ac.last_report()


def current(recs):
    """What stage() keeps: records not named by another record's supersedes_record_id."""
    sup = {r['supersedes_record_id'] for r in recs if r.get('supersedes_record_id')}
    return [r for r in recs if r['record_id'] not in sup]


def fhir_res(**kw):
    return json.dumps(kw)


class EpicTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        I = 'epic/EPIC-AR'
        table(self.db, 'ehr_epic_patient', [{'pat_id': 'Z1000001', 'first_name': 'Ann', 'last_name': 'Test'}], instance=I)
        table(self.db, 'ehr_epic_identity_id', [{'pat_id': 'Z1000001', 'line': '1', 'identity_id': 'ICE-AR-0-ABC1234', 'identity_type_id': '2257'}], instance=I)
        table(self.db, 'ehr_epic_identity_id_type', [{'id_type': '2257', 'id_type_name': 'WHN AR PRIMARY CARE 0 MRN'}], instance=I)
        table(self.db, 'ehr_epic_clarity_dep', [{'department_id': '53972191', 'department_name': 'WHN AR PRIMARY CARE 0 FAMILY MEDICINE',
                                                 'specialty': 'Family Medicine'}], instance=I)
        table(self.db, 'ehr_epic_clarity_edg', [{'dx_id': '11', 'dx_name': 'Type 2 diabetes mellitus without complications', 'icd10_code': 'E11.9'},
                                                {'dx_id': '12', 'dx_name': 'Essential hypertension', 'icd10_code': 'I10'}], instance=I)
        table(self.db, 'ehr_epic_clarity_ser_2', [{'prov_id': '77', 'npi': '1234567893'}], instance=I)
        table(self.db, 'ehr_epic_pat_enc', [{'pat_enc_csn_id': '5550001', 'pat_id': 'Z1000001', 'enc_type_c': '101', 'contact_date': '2027-01-10',
                                             'visit_prov_id': '77'}], instance=I, drop='2027-01')
        table(self.db, 'ehr_epic_pat_enc_2', [{'pat_enc_csn_id': '5550001', 'department_id': '53972191', 'appt_status_c': '6', 'enc_closed_yn': 'N',
                                               'update_date': '2027-01-10 09:00:00'}], instance=I, drop='2027-01')
        table(self.db, 'ehr_epic_pat_enc_2', [{'pat_enc_csn_id': '5550001', 'department_id': '53972191', 'appt_status_c': '2', 'enc_closed_yn': 'Y',
                                               'enc_close_date': '2027-01-11', 'update_date': '2027-01-11 08:00:00'}], instance=I, drop='2027-02')
        table(self.db, 'ehr_epic_pat_enc_dx', [{'pat_enc_csn_id': '5550001', 'line': '2', 'pat_id': 'Z1000001', 'dx_id': '12'},
                                               {'pat_enc_csn_id': '5550001', 'line': '1', 'pat_id': 'Z1000001', 'dx_id': '11'}], instance=I)
        # note: header + text lines delivered out of order, one line re-sent in a _RESEND file
        table(self.db, 'ehr_epic_hno_info', [{'note_id': '900001', 'pat_id': 'Z1000001', 'pat_enc_csn_id': '5550001', 'ip_note_type_c': '1',
                                              'current_author_id': '77', 'create_instant_dttm': '2027-01-10 16:59:00', 'note_status_c': '2'}], instance=I)
        table(self.db, 'ehr_epic_hno_note_text', [{'note_id': '900001', 'line': '3', 'note_text': 'Plan: metformin'},
                                                  {'note_id': '900001', 'line': '1', 'note_text': 'CC'},
                                                  {'note_id': '900001', 'line': '2', 'note_text': 'follow-up of diabetes'}], instance=I)
        table(self.db, 'ehr_epic_hno_note_text', [{'note_id': '900001', 'line': '2', 'note_text': 'follow-up of diabetes', 'file_variant': 'resend'}],
              instance=I, file='ehr/epic/EPIC-AR/2027-01/hno_note_text_RESEND.txt')

    def test_encounter_with_diagnoses_and_versions(self):
        recs, by, rep = run(self.db)
        encs = by['encounters']
        self.assertEqual(len(encs), 2)  # arrived, then completed
        cur = current(encs)
        self.assertEqual(len(cur), 1)
        e = cur[0]
        self.assertEqual(e['status'], 'completed')
        self.assertEqual(e['type'], 'office')
        self.assertEqual(e['encounter_id'], 'epic:EPIC-AR:5550001')
        self.assertEqual([(d['rank'], d['icd10'], d['type']) for d in e['diagnoses']], [(1, 'E11.9', 'primary'), (2, 'I10', 'secondary')])
        self.assertEqual(e['organization_id'], 'WHN-PRACTICE-AR-0')
        self.assertEqual(e['event_date'], '2027-01-10')
        self.assertEqual(e['available_date'], '2027-02-28')
        self.assertEqual(e['patient_id'], 'P|epic:EPIC-AR:Z1000001')
        self.assertEqual(e['source_patient_id'], 'ICE-AR-0-ABC1234')
        self.assertEqual(e['attending_clinician_id'], '1234567893')
        self.assertTrue(e['record_id'].startswith('AGR-'))
        self.assertTrue(e['_lineage'])
        # stable ids across runs
        self.assertEqual(sorted(r['record_id'] for r in recs), sorted(r['record_id'] for r in run(self.db)[0]))

    def test_mrn_fallback_when_native_key_unknown(self):
        recs, by, rep = run(self.db, resolve=lambda k: ('P|' + k) if k and k.startswith('mrn:') else None)
        self.assertEqual(by['encounters'][0]['patient_id'], 'P|mrn:WHN-PRACTICE-AR-0:ICE-AR-0-ABC1234')
        self.assertEqual(by['encounters'][0]['identity_link_basis'], 'mrn:WHN-PRACTICE-AR-0:ICE-AR-0-ABC1234')

    def test_note_text_reassembly(self):
        recs, by, rep = run(self.db)
        [n] = by['notes']
        self.assertEqual(n['text'], 'CC\nfollow-up of diabetes\nPlan: metformin')
        self.assertEqual(n['note_type'], 'progress_note')
        self.assertEqual(n['note_status'], 'signed')
        self.assertEqual(n['signed_date'], '2027-01-10')
        self.assertEqual(n['author_id'], '1234567893')
        self.assertEqual(n['encounter_id'], 'epic:EPIC-AR:5550001')


class EhrDeathTests(unittest.TestCase):
    def test_real_death_dates_kept_column_shifted_values_dropped(self):
        db = sqlite3.connect(':memory:')
        table(db, 'ehr_epic_patient', [{'pat_id': 'Z1000001', 'death_date': 'APT 2'}, {'pat_id': 'Z1000002', 'death_date': '06033'},
                                       {'pat_id': 'Z1000003', 'death_date': '2027-01-05'}, {'pat_id': 'Z1000004', 'death_date': '2031-01-01'}],
              instance='epic/EPIC-AR', drop='2027-01')
        table(db, 'ehr_ecw_patients', [{'pid': '41', 'hl7id': 'ICE-AK-0-1', 'deceased': '1', 'deceaseddate': '01/07/2027'}],
              instance='ecw/WHN-PRACTICE-AK-0', drop='2027-01')
        recs, by, rep = run(db)
        got = sorted((r['source'], r['date_of_death']) for r in by['death_records'])
        self.assertEqual(got, [('ecw patients.deceaseddate', '2027-01-07'), ('epic patient.death_date', '2027-01-05')])
        self.assertEqual(rep['dropped']['ehr_death_value_not_a_date'], 3)  # junk text, a ZIP code, a date after the delivery


class AthenaEcwTests(unittest.TestCase):
    def test_athena_problem(self):
        db = sqlite3.connect(':memory:')
        I = 'athena/2186'
        table(db, 'ehr_athena_patient', [{'contextid': '2186', 'patientid': '1825454', 'enterpriseid': 'ICE-AZ-0-0429896'}], instance=I)
        table(db, 'ehr_athena_department', [{'contextid': '2186', 'departmentid': '647', 'departmentname': 'WHN-PRACTICE-AZ-0'}], instance=I)
        table(db, 'ehr_athena_patientproblem', [{'contextid': '2186', 'patientproblemid': '73250999', 'type': 'PROBLEM', 'patientid': '1825454',
                                                 'chartid': '8272528', 'diagnosiscode': 'N1831', 'snomedcode': '709044004',
                                                 'problemlistname': 'Chronic kidney disease, stage 3a', 'status': 'ACTIVE',
                                                 'createddatetime': '2026-09-28 00:00:00.000', 'deleteddatetime': ''}], instance=I, drop='2026-10')
        table(db, 'ehr_athena_patientproblem', [{'contextid': '2186', 'patientproblemid': '73251000', 'type': 'PROBLEM', 'patientid': '1825454',
                                                 'diagnosiscode': 'F411', 'problemlistname': 'Generalized anxiety disorder', 'status': 'ACTIVE',
                                                 'createddatetime': '2026-09-28 00:00:00.000', 'deleteddatetime': '2026-11-02 00:00:00.000'}],
              instance=I, drop='2026-11')
        recs, by, rep = run(db)
        [c] = by['conditions']
        self.assertEqual(c['icd10'], 'N18.31')
        self.assertEqual(c['snomed'], '709044004')
        self.assertEqual(c['clinical_status'], 'active')
        self.assertEqual(c['problem_type'], 'problem_list')
        self.assertEqual(c['diagnosis_basis'], 'ehr_problem_list')
        self.assertEqual(c['organization_id'], 'WHN-PRACTICE-AZ-0')
        self.assertEqual(c['source_patient_id'], 'ICE-AZ-0-0429896')
        self.assertEqual(c['patient_id'], 'P|athena:2186:1825454')
        self.assertEqual(rep['dropped']['retired_by_source'], 1)  # the deleted problem

    def test_athena_utc_times_become_practice_local(self):
        db = sqlite3.connect(':memory:')
        I = 'athena/2186'
        table(db, 'ehr_athena_patient', [{'contextid': '2186', 'patientid': '2437453', 'enterpriseid': 'ICE-AZ-0-B687E9D'}], instance=I)
        table(db, 'ehr_athena_department', [{'contextid': '2186', 'departmentid': '647', 'departmentname': 'WHN-PRACTICE-AZ-0'}], instance=I)
        table(db, 'ehr_athena_clinicalencounter', [{'contextid': '2186', 'clinicalencounterid': '31174446', 'patientid': '2437453',
                                                    'departmentid': '647', 'clinicalencountertype': 'VISIT', 'encounterdate': '2026-09-03',
                                                    'encounterstatus': 'CLOSED', 'createddatetime': '2026-09-04 02:30:00.000',
                                                    'closeddatetime': '2026-09-04 02:59:00.000', 'lastmodified': '2026-09-03 00:00:00.000'}],
              instance=I, drop='2026-09')
        [e] = run(db)[1]['encounters']
        self.assertEqual(e['start_time'], '2026-09-03T19:30:00-07:00')  # UTC 02:30 next day = 19:30 MST
        self.assertEqual(e['event_date'], '2026-09-03')
        self.assertEqual(e['recorded_date'], '2026-09-03')  # midnight values are dates, not converted

    def test_ecw_lab_with_loinc_and_appointment_slot(self):
        db = sqlite3.connect(':memory:')
        I = 'ecw/WHN-PRACTICE-AK-0'
        table(db, 'ehr_ecw_patients', [{'pid': '48319488', 'hl7id': 'ICE-AK-0-F9781E3'}], instance=I)
        table(db, 'ehr_ecw_enc', [{'encounterid': '380653404', 'patientid': '48319488', 'date': '2026-09-23', 'status': 'CHK', 'visittype': 'LAB',
                                   'deleteflag': '0', 'starttime': '2026-09-23 07:00:00'},
                                  {'encounterid': '463628939', 'patientid': '48319488', 'date': '2026-09-03', 'status': 'PEN', 'visittype': 'TEL',
                                   'deleteflag': '0', 'starttime': '2026-09-03 08:20:00'}], instance=I, drop='2026-08')
        table(db, 'ehr_ecw_enc', [{'encounterid': '463628939', 'patientid': '48319488', 'date': '2026-09-03', 'status': 'CHK', 'visittype': 'TEL',
                                   'deleteflag': '1', 'starttime': '2026-09-03 08:20:00'},
                                  {'encounterid': '283697019', 'patientid': '48319488', 'date': '2026-09-03', 'status': 'CHK', 'visittype': 'TEL',
                                   'deleteflag': '0', 'starttime': '2026-09-03 16:00:00'}], instance=I, drop='2026-09')
        table(db, 'ehr_ecw_items', [{'itemid': '1889612', 'keyname': 'LABORDER', 'itemname': 'BASIC METABOLIC PANEL'},
                                    {'itemid': '9812283', 'keyname': 'LABATTR', 'itemname': 'Sodium'}], instance=I)
        table(db, 'ehr_ecw_labloinccodes', [{'itemid': '9812283', 'code': '2951-2'}], instance=I)
        table(db, 'ehr_ecw_labdata', [{'reportid': '113324030', 'encounterid': '380653404', 'itemid': '1889612', 'status': 'Final',
                                       'resultdate': '2026-09-24', 'colldate': '2026-09-23', 'colltime': '07:00:00', 'resultime': '12:00:00',
                                       'deleteflag': '0', 'cancelled': '0'}], instance=I, drop='2026-09')
        table(db, 'ehr_ecw_labdatadetail', [{'reportid': '113324030', 'itemid': '9812283', 'value': '146', 'propid': '9812283'}], instance=I, drop='2026-09')
        recs, by, rep = run(db)
        [lab] = by['labs']
        self.assertEqual((lab['name'], lab['loinc'], lab['value'], lab['unit']), ('Sodium', '2951-2', 146, 'mmol/L'))
        self.assertEqual(lab['unit_source'], 'loinc_default')
        self.assertEqual(lab['specimen_time'], '2026-09-23T07:00:00')
        self.assertEqual(lab['event_date'], '2026-09-23')
        self.assertEqual(lab['resulted_time'], '2026-09-24T12:00:00')
        self.assertEqual(lab['result_status'], 'final')
        self.assertEqual(lab['organization_id'], 'WHN-PRACTICE-AK-0')
        self.assertEqual(lab['panel'], 'BASIC METABOLIC PANEL')
        # the slot (PEN, then CHK with deleteFlag=1) is an appointment with two versions, never an encounter; LAB visit dropped
        appts = by['appointments']
        self.assertEqual([a['status'] for a in sorted(appts, key=lambda a: a['available_date'])], ['booked', 'fulfilled'])
        self.assertTrue(all(a.get('supersedes_record_id') is None for a in appts))
        self.assertEqual([e['encounter_id'] for e in by['encounters']], ['ecw:WHN-PRACTICE-AK-0:283697019'])
        self.assertEqual(rep['dropped']['ecw_lab_collection_not_a_visit'], 1)


class FhirTests(unittest.TestCase):
    def patient(self, db, folder, feed='fhir_bulk', pid='24026315', mrn='ICE-CA-5-4E1E097'):
        table(db, 'fhir_patient', [{'id': pid, '_resource': fhir_res(resourceType='Patient', id=pid, identifier=[
            {'type': {'coding': [{'code': 'MR'}]}, 'value': mrn}])}], feed=feed, instance=folder if feed == 'fhir_bulk' else f'fhir/{folder}',
            file=f'{feed}/{folder}/2027-01/Patient.ndjson')

    def obs(self, pid='24026315', oid='2274163638', value=0.96):
        return fhir_res(resourceType='Observation', id=oid, meta={'lastUpdated': '2027-01-16T00:00:00Z'}, status='final',
                        category=[{'coding': [{'code': 'laboratory'}]}],
                        code={'coding': [{'system': 'http://loinc.org', 'code': '2160-0', 'display': 'Creatinine'}], 'text': 'Creatinine'},
                        subject={'reference': f'Patient/{pid}'}, effectiveDateTime='2027-01-15T10:15:00-08:00',
                        valueQuantity={'value': value, 'unit': 'mg/dL'})

    def test_native_practice_observation_one_copy(self):
        db = sqlite3.connect(':memory:')
        self.patient(db, 'WHN-PRACTICE-CA-5')
        for feed, inst in (('fhir_bulk', 'WHN-PRACTICE-CA-5'), ('ehr', 'fhir/WHN-PRACTICE-CA-5')):
            table(db, 'fhir_observation', [{'id': '2274163638', '_resource': self.obs()}], feed=feed, instance=inst,
                  file=f'{feed}/{inst}/2027-01/Observation.ndjson')
        recs, by, rep = run(db)
        [lab] = by['labs']
        self.assertEqual((lab['name'], lab['loinc'], lab['value'], lab['unit']), ('Creatinine', '2160-0', 0.96, 'mg/dL'))
        self.assertEqual(lab['specimen_time'], '2027-01-15T10:15:00-08:00')
        self.assertEqual(lab['event_date'], '2027-01-15')
        self.assertEqual(lab['organization_id'], 'WHN-PRACTICE-CA-5')
        self.assertEqual(lab['patient_id'], 'P|fhir:WHN-PRACTICE-CA-5:24026315')
        self.assertEqual(len(lab['_lineage']), 2)
        self.assertEqual(rep['dropped']['fhir_native_bulk_copy'], 1)

    def athena_lab_practice(self, db, drop='2027-01'):
        """athena AZ-0 delivering labs (and nothing else) in the given month."""
        A = 'athena/2186'
        table(db, 'ehr_athena_patient', [{'contextid': '2186', 'patientid': '2437453', 'enterpriseid': 'ICE-AZ-0-B687E9D'}], instance=A)
        table(db, 'ehr_athena_department', [{'contextid': '2186', 'departmentid': '647', 'departmentname': 'WHN-PRACTICE-AZ-0'}], instance=A)
        table(db, 'ehr_athena_document', [{'contextid': '2186', 'documentid': '1', 'patientid': '2437453', 'departmentid': '647',
                                           'documentclass': 'LABRESULT'}], instance=A, drop=drop)
        table(db, 'ehr_athena_clinicalresult', [{'contextid': '2186', 'clinicalresultid': '7', 'documentid': '1',
                                                 'observationdatetime': '2027-01-10 18:00:00.000'}], instance=A, drop=drop)
        table(db, 'ehr_athena_clinicalresultobservation', [{'contextid': '2186', 'clinicalobservationid': '9', 'clinicalresultid': '7',
                                                            'observationidentifierloinc': '2345-7', 'observationidentifiertext': 'Glucose',
                                                            'result': '99', 'resultstatus': 'F'}], instance=A, drop=drop)

    def test_fhir_copy_dropped_only_where_vendor_delivers_the_type(self):
        db = sqlite3.connect(':memory:')
        self.athena_lab_practice(db)
        self.patient(db, 'WHN-PRACTICE-AZ-0', mrn='ICE-AZ-0-B687E9D')
        bulk = dict(feed='fhir_bulk', instance='WHN-PRACTICE-AZ-0')
        table(db, 'fhir_observation', [{'id': '1', '_resource': self.obs()}], file='fhir_bulk/WHN-PRACTICE-AZ-0/2027-01/Observation.ndjson', **bulk)
        # athena delivers no appointment table: the FHIR appointment is the only scheduling record and is loaded
        table(db, 'fhir_appointment', [{'id': '62', '_resource': fhir_res(
            resourceType='Appointment', id='62', meta={'lastUpdated': '2027-01-03T00:00:00Z'}, status='booked', start='2027-01-11T10:00:00-07:00',
            created='2027-01-02T23:30:00-07:00', participant=[{'actor': {'reference': 'Patient/24026315'}}])}],
            file='fhir_bulk/WHN-PRACTICE-AZ-0/2027-01/Appointment.ndjson', **bulk)
        # a FHIR lab delivered in a month the athena extract was not delivering labs: gap fill, unless the same record exists
        table(db, 'fhir_observation', [{'id': '2', '_resource': self.obs(oid='2', value=1.1)}], drop='2027-06',
              file='fhir_bulk/WHN-PRACTICE-AZ-0/2027-06/Observation.ndjson', **bulk)
        table(db, 'fhir_encounter', [{'id': '9', '_resource': fhir_res(resourceType='Encounter', id='9', status='finished',
                                                                       subject={'reference': 'Patient/24026315'}, period={'start': '2027-01-15'})}],
              feed='fhir_bulk', instance='DEEPCHART', file='fhir_bulk/DEEPCHART/2027-01/Encounter.ndjson')
        table(db, 'ehr_epic_pat_enc', [{'pat_enc_csn_id': '1', 'pat_id': 'Z1', 'enc_type_c': '101', 'contact_date': '2027-01-15'}],
              instance='epic/EPIC-DEEPCHART', drop='2027-01')
        recs, by, rep = run(db)
        fhir = [r for r in recs if r['source_system'].startswith('fhir:')]
        self.assertEqual(sorted((r['source_table'], r['_lineage'][0]['file'].split('/')[2]) for r in fhir),
                         [('appointments', '2027-01'), ('labs', '2027-06')])
        self.assertTrue(all(r['fhir_gap_fill'] for r in fhir))
        self.assertEqual(rep['dropped']['fhir_type_delivered_by_vendor_extract'], 1)
        self.assertEqual(rep['dropped']['fhir_copy_of_epic_deepchart'], 1)

    def test_fhir_cross_feed_duplicate_key(self):
        db = sqlite3.connect(':memory:')
        self.athena_lab_practice(db, drop='2026-09')  # athena labs delivered only in 2026-09 ...
        self.patient(db, 'WHN-PRACTICE-AZ-0', mrn='ICE-AZ-0-B687E9D')
        same = fhir_res(resourceType='Observation', id='3', status='final', category=[{'coding': [{'code': 'laboratory'}]}],
                        code={'coding': [{'system': 'http://loinc.org', 'code': '2345-7'}], 'text': 'Glucose'},
                        subject={'reference': 'Patient/24026315'}, effectiveDateTime='2027-01-10T11:00:00-07:00',
                        valueQuantity={'value': 99, 'unit': 'mg/dL'})
        table(db, 'fhir_observation', [{'id': '3', '_resource': same}], feed='fhir_bulk', instance='WHN-PRACTICE-AZ-0', drop='2027-01',
              file='fhir_bulk/WHN-PRACTICE-AZ-0/2027-01/Observation.ndjson')  # ... the bulk copy of the same result arrives later
        recs, by, rep = run(db)
        self.assertEqual([r['source_system'] for r in by['labs']], ['athena:2186'])
        self.assertEqual(rep['dropped']['fhir_duplicate_of_vendor_record'], 1)

    def test_fhir_survey_total_from_value_integer(self):
        db = sqlite3.connect(':memory:')
        self.patient(db, 'WHN-PRACTICE-AK-0', mrn='ICE-AK-0-F9781E3')
        table(db, 'fhir_observation', [{'id': '5', '_resource': fhir_res(
            resourceType='Observation', id='5', status='final', category=[{'coding': [{'code': 'survey'}]}],
            code={'coding': [{'system': 'http://loinc.org', 'code': '44261-6'}], 'text': 'PHQ-9'}, subject={'reference': 'Patient/24026315'},
            effectiveDateTime='2027-02-16T10:23:00-09:00', valueInteger=14)}], feed='fhir_bulk', instance='WHN-PRACTICE-AK-0',
            file='fhir_bulk/WHN-PRACTICE-AK-0/2027-02/Observation.ndjson')
        [q] = run(db)[1]['mood_questionnaires']  # eCW practice: no PHQ in the vendor extract, FHIR is the only source
        self.assertEqual((q['instrument'], q['total'], q['event_date']), ('PHQ-9', 14, '2027-02-16'))

    def test_encounter_in_progress_then_finished(self):
        db = sqlite3.connect(':memory:')
        self.patient(db, 'WHN-PRACTICE-CA-5')
        base = dict(resourceType='Encounter', id='6396217061', meta={'lastUpdated': '2027-01-11T00:00:00Z'},
                    **{'class': {'code': 'AMB'}}, subject={'reference': 'Patient/24026315'}, period={'start': '2027-01-11T08:30:00-08:00'})
        table(db, 'fhir_encounter', [{'id': '6396217061', '_resource': fhir_res(**base, status='in-progress', reasonCode=[])},
                                     {'id': '6396217061', '_resource': fhir_res(**base, status='finished', reasonCode=[
                                         {'coding': [{'system': 'http://hl7.org/fhir/sid/icd-10-cm', 'code': 'I25.10'}]},
                                         {'coding': [{'system': 'http://hl7.org/fhir/sid/icd-10-cm', 'code': 'I10'}]}])}],
              feed='fhir_bulk', instance='WHN-PRACTICE-CA-5', file='fhir_bulk/WHN-PRACTICE-CA-5/2027-01/Encounter.ndjson')
        recs, by, rep = run(db)
        encs = by['encounters']
        self.assertEqual(len(encs), 2)
        [cur] = current(encs)
        self.assertEqual(cur['status'], 'completed')
        self.assertEqual(cur['type'], 'office')
        self.assertEqual([d['icd10'] for d in cur['diagnoses']], ['I25.10', 'I10'])
        self.assertEqual(cur['start_time'], '2027-01-11T08:30:00-08:00')
        self.assertEqual(cur['encounter_id'], 'fhir:WHN-PRACTICE-CA-5:6396217061')

    def test_emergency_encounter_diagnoses_disposition_and_patient_death(self):
        db = sqlite3.connect(':memory:')
        F = 'WHN-PRACTICE-CA-5'
        table(db, 'fhir_patient', [{'id': '24026315', '_resource': fhir_res(resourceType='Patient', id='24026315', identifier=[
            {'type': {'coding': [{'code': 'MR'}]}, 'value': 'ICE-CA-5-4E1E097'}], deceasedDateTime='2027-01-20')}],
            feed='fhir_bulk', instance=F, file=f'fhir_bulk/{F}/2027-02/Patient.ndjson', drop='2027-02')
        icd = 'http://hl7.org/fhir/sid/icd-10-cm'
        table(db, 'fhir_condition', [
            {'id': 'c1', '_resource': fhir_res(resourceType='Condition', id='c1', subject={'reference': 'Patient/24026315'},
                                               code={'coding': [{'system': icd, 'code': 'S93401A'}]}, recordedDate='2027-01-12')},
            {'id': 'c2', '_resource': fhir_res(resourceType='Condition', id='c2', subject={'reference': 'Patient/24026315'},
                                               category=[{'coding': [{'code': 'encounter-diagnosis'}]}], encounter={'reference': 'Encounter/E1'},
                                               code={'coding': [{'system': icd, 'code': 'R07.9'}]}, recordedDate='2027-01-12')}],
              feed='fhir_bulk', instance=F, file=f'fhir_bulk/{F}/2027-01/Condition.ndjson')
        table(db, 'fhir_encounter', [{'id': 'E1', '_resource': fhir_res(
            resourceType='Encounter', id='E1', status='finished', **{'class': {'code': 'EMER'}}, subject={'reference': 'Patient/24026315'},
            period={'start': '2027-01-12T03:00:00-08:00', 'end': '2027-01-12T07:00:00-08:00'},
            diagnosis=[{'condition': {'reference': 'Condition/c1'}, 'rank': 1}],
            hospitalization={'dischargeDisposition': {'coding': [{'code': 'home'}]}})}],
              feed='fhir_bulk', instance=F, file=f'fhir_bulk/{F}/2027-01/Encounter.ndjson')
        recs, by, rep = run(db)
        [enc] = by['encounters']
        self.assertEqual(enc['type'], 'emergency')
        self.assertEqual([(d['icd10'], d['type']) for d in enc['diagnoses']], [('S93.401A', 'primary'), ('R07.9', 'secondary')])
        self.assertEqual(enc['disposition'], 'home')
        [death] = by['death_records']
        self.assertEqual((death['date_of_death'], death['source'], death['patient_id']),
                         ('2027-01-20', 'fhir Patient.deceasedDateTime', 'P|' + ac.fhir_key(F, '24026315')))

    def test_document_reference_text(self):
        db = sqlite3.connect(':memory:')
        self.patient(db, 'WHN-PRACTICE-CA-5')
        data = base64.b64encode('CC: f/u of chronic conditions.'.encode()).decode()
        table(db, 'fhir_documentreference', [{'id': '1875949181', '_resource': fhir_res(
            resourceType='DocumentReference', id='1875949181', status='current', subject={'reference': 'Patient/24026315'},
            type={'coding': [{'system': 'http://loinc.org', 'code': '11506-3', 'display': 'Progress note'}]}, date='2027-01-11T08:54:00-08:00',
            context={'encounter': [{'reference': 'Encounter/6396217061'}]}, content=[{'attachment': {'contentType': 'text/plain', 'data': data}}])}],
            feed='fhir_bulk', instance='WHN-PRACTICE-CA-5', file='fhir_bulk/WHN-PRACTICE-CA-5/2027-01/DocumentReference.ndjson')
        [n] = run(db)[1]['notes']
        self.assertEqual((n['text'], n['note_type'], n['signed_date']), ('CC: f/u of chronic conditions.', 'progress_note', '2027-01-11'))


def oru(db, file, cid, accession, loinc, name, value, status, coll, resulted, mrn='ICE-CA-9-3A910D1', auth='PRACCA9', row=0):
    key = f'{file.rsplit("/", 1)[-1]}:{row + 1}'
    lin = dict(feed='hl7', instance=None, drop=file.split('/')[2], file=file)
    table(db, 'hl7_message', [{'message_key': key, 'message_control_id': cid, 'message_code': 'ORU', 'trigger_event': 'R01',
                               'message_type': 'ORU^R01', 'patient_id': mrn, 'patient_id_assigning_authority': auth,
                               'sending_facility': 'REFLAB', 'receiving_facility': auth, 'message_datetime': resulted, '_row': row}], **lin)
    table(db, 'hl7_order', [{'message_key': key, 'order_ordinal': 1, 'placer_order_number': accession, 'observation_datetime': coll,
                             'results_report_datetime': resulted, 'universal_service_text': name, '_row': row}], **lin)
    table(db, 'hl7_observation', [{'message_key': key, 'order_ordinal': 1, 'patient_id': mrn, 'placer_order_number': accession,
                                   'loinc_code': loinc, 'local_code': name[:3].upper(), 'observation_text': name, 'value': value,
                                   'value_type': 'NM', 'units': 'mg/dL', 'reference_range': '70-99', 'result_status': status,
                                   'obr_observation_datetime': coll, '_row': row}], **lin)


class Hl7Tests(unittest.TestCase):
    def ehr_lab(self, db):
        I = 'ecw/WHN-PRACTICE-CA-9'
        table(db, 'ehr_ecw_patients', [{'pid': '1', 'hl7id': 'ICE-CA-9-3A910D1'}], instance=I)
        table(db, 'ehr_ecw_enc', [{'encounterid': '10', 'patientid': '1', 'date': '2026-09-08', 'status': 'CHK', 'visittype': 'FU', 'deleteflag': '0',
                                   'starttime': '2026-09-08 10:00:00'}], instance=I, drop='2026-09')
        table(db, 'ehr_ecw_items', [{'itemid': '20', 'keyname': 'LABATTR', 'itemname': 'Glucose'},
                                    {'itemid': '21', 'keyname': 'LABATTR', 'itemname': 'POTASSIUM'}], instance=I)  # 21: no LOINC row
        table(db, 'ehr_ecw_labloinccodes', [{'itemid': '20', 'code': '2345-7'}], instance=I)
        table(db, 'ehr_ecw_labdata', [{'reportid': '30', 'encounterid': '10', 'itemid': '99', 'status': 'Final', 'colldate': '2026-09-08',
                                       'colltime': '10:00:00', 'resultdate': '2026-09-08', 'resultime': '17:00:00', 'deleteflag': '0',
                                       'cancelled': '0'}], instance=I, drop='2026-09')
        table(db, 'ehr_ecw_labdatadetail', [{'reportid': '30', 'itemid': '20', 'value': '65.0'},
                                            {'reportid': '30', 'itemid': '21', 'value': '4.1'}], instance=I, drop='2026-09')

    def test_oru_duplicate_of_ehr_dropped_reference_lab_kept(self):
        db = sqlite3.connect(':memory:')
        self.ehr_lab(db)
        f = 'hl7/oru/2026-09/REFLAB_20260909.hl7'
        oru(db, f, 'AAA1', 'WHN-LO-de8e', '2345-7', 'Glucose', '65', 'F', '2026-09-08T10:00:00', '2026-09-08T17:00:00', row=0)
        oru(db, f, 'AAA2', 'WHN-LO-de8e', '4548-4', 'Hemoglobin A1c', '7.9', 'F', '2026-09-08T10:00:00', '2026-09-08T18:00:00', row=1)
        oru(db, f, 'AAA3', 'WHN-LO-de8e', '2823-3', 'Potassium', '4.10', 'F', '2026-09-08T10:00:00', '2026-09-08T18:00:00', row=2)
        recs, by, rep = run(db)
        labs = by['labs']
        self.assertEqual(len(labs), 3)  # EHR glucose + EHR potassium (no LOINC, matched by name) + reference-lab A1c
        ref = [l for l in labs if l.get('reference_lab_result')]
        self.assertEqual([(l['loinc'], l['value']) for l in ref], [('4548-4', 7.9)])
        self.assertEqual(ref[0]['organization_id'], 'WHN-PRACTICE-CA-9')
        self.assertEqual(ref[0]['patient_id'], 'P|mrn:WHN-PRACTICE-CA-9:ICE-CA-9-3A910D1')
        self.assertEqual(ref[0]['available_date'], '2026-09-09')
        self.assertEqual(rep['dropped']['oru_duplicate_of_ehr_lab'], 2)

    def test_corrected_oru_supersedes_final_and_resend_dropped(self):
        db = sqlite3.connect(':memory:')
        oru(db, 'hl7/oru/2027-11/REFLAB_20271109.hl7', 'F001', 'WHN-LO-ef89', '2345-7', 'Glucose', '75', 'F',
            '2027-11-09T08:00:00', '2027-11-09T12:00:00')
        oru(db, 'hl7/oru/2027-11/REFLAB_20271110.hl7', 'C001', 'WHN-LO-ef89', '2345-7', 'Glucose', '82.0', 'C',
            '2027-11-09T08:00:00', '2027-11-10T09:00:00')
        oru(db, 'hl7/oru/2027-11/REFLAB_20271111_resend.hl7', 'F001', 'WHN-LO-ef89', '2345-7', 'Glucose', '75', 'F',
            '2027-11-09T08:00:00', '2027-11-09T12:00:00')
        recs, by, rep = run(db)
        labs = by['labs']
        self.assertEqual(len(labs), 2)
        f = next(l for l in labs if l['result_status'] == 'final')
        c = next(l for l in labs if l['result_status'] == 'corrected')
        self.assertEqual(c['supersedes_record_id'], f['record_id'])
        self.assertEqual(c['replaces_result_record_id'], f['record_id'])
        self.assertEqual(c['value'], 82.0)
        self.assertEqual(current(labs), [c])
        self.assertEqual(rep['dropped']['hl7_resend_duplicate'], 1)

    def test_adt_admit_discharge(self):
        db = sqlite3.connect(':memory:')
        f = 'hl7/adt/2027-11/WHN-HOSPITAL-AK-0_20271108.hl7'
        common = {'message_code': 'ADT', 'patient_id': 'L-AK-0-D8DEB363', 'patient_id_assigning_authority': 'HOSPAK0',
                  'sending_facility': 'HOSPAK0', 'visit_number': 'WHN-E-849d504b933a74b218fb', 'assigned_location': 'MED'}
        table(db, 'hl7_message', [
            {**common, 'message_key': 'f:1', 'message_control_id': 'M1', 'trigger_event': 'A04', 'message_type': 'ADT^A04', 'patient_class': 'E',
             'message_datetime': '2027-11-08T06:00:00', 'event_recorded_datetime': '2027-11-08T06:00:00', 'assigned_location': 'ED'},
            {**common, 'message_key': 'f:2', 'message_control_id': 'M2', 'trigger_event': 'A01', 'message_type': 'ADT^A01', 'patient_class': 'I',
             'message_datetime': '2027-11-08T08:15:00', 'event_recorded_datetime': '2027-11-08T08:15:00', 'message_datetime_raw': '20271108081500'},
            {**common, 'message_key': 'f:3', 'message_control_id': 'M3', 'trigger_event': 'A03', 'message_type': 'ADT^A03', 'patient_class': 'I',
             'message_datetime': '2027-11-12T14:15:00', 'event_recorded_datetime': '2027-11-12T14:15:00', 'discharge_disposition': '01'}],
            feed='hl7', drop='2027-11', file=f)
        table(db, 'hl7_message', [{**common, 'message_key': 'r:1', 'message_control_id': 'M2', 'trigger_event': 'A01', 'message_type': 'ADT^A01',
                                   'patient_class': 'I', 'message_datetime': '2027-11-08T08:15:00'}],
              feed='hl7', drop='2027-11', file='hl7/adt/2027-11/WHN-HOSPITAL-AK-0_20271120_resend.hl7')
        table(db, 'hl7_diagnosis', [{'message_key': 'f:2', 'set_id': '1', 'diagnosis_code': 'J18.9', 'coding_system': 'I10', 'diagnosis_type': 'W'},
                                    {'message_key': 'f:3', 'set_id': '1', 'diagnosis_code': 'J18.9', 'coding_system': 'I10', 'diagnosis_type': 'F'}],
              feed='hl7', drop='2027-11', file=f)
        recs, by, rep = run(db)
        msgs = {m['trigger_event']: m for m in by['adt_messages']}
        self.assertEqual(sorted(msgs), ['A01', 'A03', 'A04'])
        a01, a03 = msgs['A01'], msgs['A03']
        self.assertEqual(a01['organization_id'], 'WHN-HOSPITAL-AK-0')
        self.assertEqual(a01['patient_id'], 'P|hl7:HOSPAK0:L-AK-0-D8DEB363')
        self.assertEqual(a01['encounter_id'], 'WHN-E-849d504b933a74b218fb')
        self.assertEqual(a01['prior_patient_class'], 'E')
        self.assertEqual(a01['admit_diagnosis'], {'code': 'J18.9', 'system': 'I10', 'type': 'W', 'description': None})
        self.assertEqual(a01['admit_datetime'], '2027-11-08T06:00:00')  # registration (A04) opened the visit
        self.assertEqual(a01['message_datetime'], '20271108081500')
        self.assertEqual(a03['discharge_datetime'], '2027-11-12T14:15:00')
        self.assertEqual(a03['discharge_disposition'], 'home')
        self.assertEqual(a03['discharge_diagnosis']['type'], 'F')
        self.assertEqual(a03['event_date'], '2027-11-12')
        self.assertTrue(all(m.get('supersedes_record_id') is None for m in by['adt_messages']))
        self.assertEqual(rep['dropped']['hl7_resend_duplicate'], 1)
        # canonical hospital events: ED registration, inpatient admission (from the ED), discharge home
        ev = {e['event']: e for e in by['adt']}
        self.assertEqual(sorted(ev), ['admission', 'discharge', 'emergency_registration'])
        reg, adm, dis = ev['emergency_registration'], ev['admission'], ev['discharge']
        self.assertEqual((reg['patient_class'], reg['unit'], reg['event_time']), ('emergency', 'emergency_department', '2027-11-08T06:00:00'))
        self.assertEqual((adm['patient_class'], adm['admit_source'], adm['prior_patient_class']), ('inpatient', 'emergency_department', 'emergency'))
        self.assertEqual(adm['diagnosis']['code'], 'J18.9')
        self.assertEqual((dis['destination'], dis['discharge_patient_class'], dis['event_date']), ('home', 'inpatient', '2027-11-12'))
        self.assertNotIn('patient_class', dis)  # a discharge must not match the ED / inpatient concepts a second time
        for e in ev.values():
            self.assertEqual(e['encounter_id'], 'WHN-E-849d504b933a74b218fb')
            self.assertEqual(e['organization_id'], 'WHN-HOSPITAL-AK-0')
            self.assertEqual(e['patient_id'], 'P|hl7:HOSPAK0:L-AK-0-D8DEB363')
            self.assertIsNone(e.get('supersedes_record_id'))
        # the values hit the concept library's ADT bindings
        from clinical_core.agere.clinical import concepts
        import re as _re
        f = concepts.ENCOUNTERS
        self.assertRegex(reg['event'], f['emergency_visit']['fields']['adt']['event'])
        self.assertRegex(adm['event'], f['inpatient_admission']['fields']['adt']['event'])
        self.assertRegex(adm['patient_class'], f['inpatient_admission']['fields']['adt']['patient_class'])

    def test_adt_events_updates_reposts_and_class_change(self):
        db = sqlite3.connect(':memory:')
        f = 'hl7/adt/2028-01/WHN-HOSPITAL-TN-0_20280105.hl7'
        common = {'message_code': 'ADT', 'patient_id': 'L-TN-0-00000001', 'patient_id_assigning_authority': 'HOSPTN0', 'sending_facility': 'HOSPTN0',
                  'visit_number': 'WHN-E-000000000000000000aa'}
        msgs = [('M1', 'A04', 'E', 'ED', '2028-01-05T10:00:00', None), ('M2', 'A08', 'E', 'ED', '2028-01-05T11:00:00', None),
                ('M3', 'A06', 'I', 'MED', '2028-01-05T15:00:00', None), ('M4', 'A03', 'I', 'MED', '2028-01-07T09:00:00', '03'),
                ('M5', 'A03', 'I', 'MED', '2028-01-07T12:00:00', '03')]  # a second discharge message for the same visit
        table(db, 'hl7_message', [{**common, 'message_key': f'f:{i}', 'message_control_id': cid, 'trigger_event': trig,
                                   'message_type': f'ADT^{trig}', 'patient_class': pc, 'assigned_location': loc, 'message_datetime': t,
                                   'event_recorded_datetime': t, 'discharge_disposition': disp}
                                  for i, (cid, trig, pc, loc, t, disp) in enumerate(msgs)], feed='hl7', drop='2028-01', file=f)
        # an observation stay at another hospital: class O in OBS, no visit-level update
        table(db, 'hl7_message', [{'message_code': 'ADT', 'patient_id': 'L-TN-0-00000002', 'patient_id_assigning_authority': 'HOSPTN0',
                                   'sending_facility': 'HOSPTN0', 'visit_number': 'WHN-E-000000000000000000bb', 'message_key': 'g:1',
                                   'message_control_id': 'N1', 'trigger_event': 'A01', 'message_type': 'ADT^A01', 'patient_class': 'O',
                                   'assigned_location': 'OBS', 'message_datetime': '2028-01-06T08:00:00'}], feed='hl7', drop='2028-01', file=f)
        recs, by, rep = run(db)
        self.assertEqual(len(by['adt_messages']), 6)  # every message kept as received
        ev = sorted((e['visit_number'][-2:], e['event'], e.get('patient_class') or e['discharge_patient_class'], e['event_time']) for e in by['adt'])
        self.assertEqual(ev, [('aa', 'admission', 'inpatient', '2028-01-05T15:00:00'), ('aa', 'discharge', 'inpatient', '2028-01-07T12:00:00'),
                              ('aa', 'emergency_registration', 'emergency', '2028-01-05T10:00:00'),
                              ('bb', 'observation_start', 'observation', '2028-01-06T08:00:00')])
        dis = next(e for e in by['adt'] if e['event'] == 'discharge')
        self.assertEqual(dis['destination'], 'snf')
        self.assertEqual(rep['dropped']['adt_repeated_event_for_visit'], 1)


class DateClockTests(unittest.TestCase):
    def test_problem_list_documented_date_is_the_record_date(self):
        db = sqlite3.connect(':memory:')
        I = 'ecw/WHN-PRACTICE-AK-0'
        table(db, 'ehr_ecw_patients', [{'pid': '1', 'hl7id': 'ICE-AK-0-0000001'}], instance=I)
        table(db, 'ehr_ecw_itemdetail', [{'itemid': '50', 'value': 'E11.9', 'propid': '13'}], instance=I)
        table(db, 'ehr_ecw_problemlist', [{'patientid': '1', 'asmtid': '50', 'addeddate': '2027-10-08', 'onsetdate': '2027-02-28',
                                           'inactiveflag': '0', 'resolved': '0', 'deleteflag': '0'}], instance=I, drop='2027-10')
        [c] = run(db)[1]['conditions']
        self.assertEqual((c['event_date'], c['recorded_date'], c['documented_date']), ('2027-10-08',) * 3)
        self.assertEqual((c['onset_date'], c['onset']), ('2027-02-28', '2027-02-28'))

    def test_appointment_versions_carry_booking_and_slot(self):
        db = sqlite3.connect(':memory:')
        I = 'epic/EPIC-DEEPCHART'
        table(db, 'ehr_epic_patient', [{'pat_id': 'Z1'}], instance=I)
        table(db, 'ehr_epic_f_sched_appt', [{'pat_enc_csn_id': '11', 'pat_id': 'Z1', 'contact_date': '2028-03-01', 'appt_dttm': '2028-03-01 09:00:00',
                                             'appt_status_c': '1', 'appt_made_dttm': '2028-02-01 08:00:00'}], instance=I, drop='2028-02')
        table(db, 'ehr_epic_f_sched_appt', [{'pat_enc_csn_id': '11', 'pat_id': 'Z1', 'contact_date': '2028-03-01', 'appt_dttm': '2028-03-01 09:00:00',
                                             'appt_status_c': '2', 'appt_made_dttm': '2028-02-01 08:00:00'}], instance=I, drop='2028-03')
        # a version delivered with no slot at all: the made date is the slot of last resort
        table(db, 'ehr_epic_f_sched_appt', [{'pat_enc_csn_id': '12', 'pat_id': 'Z1', 'contact_date': '', 'appt_dttm': '',
                                             'appt_status_c': '2', 'appt_made_dttm': '2028-04-02 10:00:00'}], instance=I, drop='2028-04')
        appts = run(db)[1]['appointments']
        self.assertTrue(all(a['booked_date'] for a in appts))
        self.assertEqual(sorted((a['appointment_id'][-2:], a['status'], a['booked_date'], a['scheduled_date'], a.get('scheduled_date_source'))
                                for a in appts),
                         [('11', 'booked', '2028-02-01', '2028-03-01', None), ('11', 'fulfilled', '2028-02-01', '2028-03-01', None),
                          ('12', 'fulfilled', '2028-04-02', '2028-04-02', 'made_date')])
        self.assertTrue(all(a['slot_start'] for a in appts))

    def test_egfr_label_with_equation_maps_to_concept(self):
        import re as _re
        from clinical_core.agere.clinical import concepts
        names = _re.compile(r'^(?:' + concepts.MEASUREMENTS['egfr']['names'] + r')$', _re.I)
        self.assertTrue(names.match('EGFR CKD-EPI 2021'))
        self.assertTrue(names.match('eGFR'))
        self.assertFalse(names.match('EGFR mutation'))


class VariantTests(unittest.TestCase):
    def test_v2_file_replaces_original(self):
        db = sqlite3.connect(':memory:')
        I = 'ecw/WHN-PRACTICE-AK-0'
        table(db, 'ehr_ecw_patients', [{'pid': '1', 'hl7id': 'ICE-AK-0-0000001'}], instance=I)
        table(db, 'ehr_ecw_rxhub_scriptlog', [{'id': '5', 'patientid': '1', 'scriptdate': '2026-09-07', 'drugname': 'metformin 500 MG Or'}],
              instance=I, drop='2026-09', file='ehr/ecw/WHN-PRACTICE-AK-0/2026-09/rxhub_scriptlog.csv')
        table(db, 'ehr_ecw_rxhub_scriptlog', [{'id': '5', 'patientid': '1', 'scriptdate': '2026-09-07', 'file_variant': 'v2',
                                               'drugname': 'metformin 500 MG Oral Tablet', 'dosage': 'Take 1 tablet by mouth twice daily'}],
              instance=I, drop='2026-09', file='ehr/ecw/WHN-PRACTICE-AK-0/2026-09/rxhub_scriptlog_v2.csv')
        recs, by, rep = run(db)
        [m] = by['medication_orders']
        self.assertEqual(m['medication_name'], 'metformin 500 MG Oral Tablet')
        self.assertEqual(m['frequency'], 'twice_daily')
        self.assertEqual(rep['dropped']['superseded_by_v2_file'], 1)


if __name__ == '__main__':
    unittest.main()
