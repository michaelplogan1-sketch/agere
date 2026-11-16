"""Tests for clinical_core.agere.ingest.adapt_entities (run: .venv/bin/python -m unittest test_ingest_adapt_entities).

Hand-built synthetic landing rows; the clinical adapter runs on the same rows to prove its ids resolve to entity rows.
"""
import sqlite3
import unittest

from clinical_core.agere.ingest import adapt_clinical as ac
from clinical_core.agere.ingest import adapt_entities as ae
from test_ingest_adapt_clinical import table


def build(db):
    ents = list(ae.entities(db))
    clin = {i: p for k, i, p in ents if k == 'clinicians'}
    orgs = {i: p for k, i, p in ents if k == 'organizations'}
    return clin, orgs, ae.last_report()


class EhrEntityTests(unittest.TestCase):
    def setUp(self):
        db = self.db = sqlite3.connect(':memory:')
        table(db, 'rosters_aco_provider_roster', [
            {'tin': '203313039', 'practice_id': 'WHN-PRACTICE-AR-0', 'practice_name': 'Synthetic AR primary care practice 0',
             'group_npi': '1712816499', 'provider_npi': '1727890836', 'provider_last_name': 'Hughes', 'provider_first_name': 'Maria',
             'credential': 'MD', 'specialty': 'FM', 'taxonomy_code': '207Q00000X', 'effective_date': '2024-01-01'},
            {'tin': 'APT 2', 'practice_id': '2024-01-01'}],  # column-shifted row
            feed='rosters', drop='2026-09', file='rosters/2026-09/ACO_PROVIDER_ROSTER_20260910.csv')
        I = 'epic/EPIC-AR'
        table(db, 'ehr_epic_patient', [{'pat_id': 'Z1000001'}], instance=I)
        table(db, 'ehr_epic_identity_id', [{'pat_id': 'Z1000001', 'line': '1', 'identity_id': 'ICE-AR-0-ABC1234', 'identity_type_id': '2257'}], instance=I)
        table(db, 'ehr_epic_identity_id_type', [{'id_type': '2257', 'id_type_name': 'WHN AR PRIMARY CARE 0 MRN'}], instance=I)
        table(db, 'ehr_epic_clarity_dep', [{'department_id': '53972191', 'department_name': 'WHN AR PRIMARY CARE 0 FAMILY MEDICINE',
                                            'specialty': 'Family Medicine'}], instance=I)
        table(db, 'ehr_epic_clarity_ser', [{'prov_id': '9905547', 'prov_name': 'Maria Hughes'}, {'prov_id': '1588055', 'prov_name': 'Sam Nguyen'}],
              instance=I)
        table(db, 'ehr_epic_clarity_ser_2', [{'prov_id': '9905547', 'npi': '1727890836', 'clinician_title': 'MD'},
                                             {'prov_id': '1588055', 'npi': '', 'clinician_title': 'NP'}], instance=I)
        table(db, 'ehr_epic_clarity_ser_spec', [{'prov_id': '1588055', 'line': '1', 'specialty_c': '736'}], instance=I)
        table(db, 'ehr_epic_pat_enc', [{'pat_enc_csn_id': '5550001', 'pat_id': 'Z1000001', 'enc_type_c': '101', 'contact_date': '2027-01-10',
                                        'visit_prov_id': '9905547'},
                                       {'pat_enc_csn_id': '5550002', 'pat_id': 'Z1000001', 'enc_type_c': '76', 'contact_date': '2027-02-10',
                                        'visit_prov_id': '1588055'},
                                       {'pat_enc_csn_id': '5550003', 'pat_id': 'Z1000001', 'enc_type_c': '101', 'contact_date': '2027-03-10',
                                        'visit_prov_id': '2030-03-05'}], instance=I)  # malformed provider id
        table(db, 'ehr_epic_pat_enc_2', [{'pat_enc_csn_id': c, 'department_id': '53972191', 'appt_status_c': '2', 'enc_closed_yn': 'Y'}
                                         for c in ('5550001', '5550002', '5550003')], instance=I)
        A = 'athena/2186'
        table(db, 'ehr_athena_patient', [{'contextid': '2186', 'patientid': '2437453', 'enterpriseid': 'ICE-AZ-0-B687E9D'}], instance=A)
        table(db, 'ehr_athena_department', [{'contextid': '2186', 'departmentid': '647', 'departmentname': 'WHN-PRACTICE-AZ-0',
                                             'departmentcity': 'Show Low', 'departmentstate': 'AZ', 'departmentzip': '85901'}], instance=A)
        table(db, 'ehr_athena_provider', [{'contextid': '2186', 'providerid': '93071', 'providernpinumber': '1882185311',
                                           'providerfirstname': 'Grace', 'providerlastname': 'Kowalski', 'providertype': 'MD'}], instance=A)
        table(db, 'ehr_athena_clinicalprovider', [{'contextid': '2186', 'clinicalproviderid': '5121826', 'npi': '',
                                                   'firstname': 'Matthew', 'lastname': 'Williams', 'title': 'NP'}], instance=A)
        table(db, 'ehr_athena_clinicalencounter', [{'contextid': '2186', 'clinicalencounterid': '31174446', 'patientid': '2437453',
                                                    'departmentid': '647', 'clinicalencountertype': 'VISIT', 'encounterdate': '2026-09-03',
                                                    'encounterstatus': 'CLOSED', 'providerid': '93071'}], instance=A)
        W = 'ecw/WHN-PRACTICE-AK-0'
        table(db, 'ehr_ecw_patients', [{'pid': '48319488', 'hl7id': 'ICE-AK-0-F9781E3'}], instance=W)
        table(db, 'ehr_ecw_edi_facilities', [{'id': '9732', 'name': 'WHN-PRACTICE-AK-0 Primary Care', 'code': 'WHN-PRACTICE-AK-0',
                                              'addressline1': '1 Main St', 'city': 'Wasilla', 'state': 'AK', 'zip': '99654'}], instance=W)
        table(db, 'ehr_ecw_doctors', [{'doctorid': '515683', 'npi': '1943462097', 'printname': 'Yusuf Patel, MD', 'speciality': 'Family Medicine',
                                       'specialitycode': '207Q00000X', 'facilityid': '9732'},
                                      {'doctorid': '131909', 'npi': '', 'printname': 'NURSE, RESOURCE', 'speciality': 'Nursing', 'facilityid': '9732'}],
              instance=W)
        table(db, 'ehr_ecw_enc', [{'encounterid': '283697019', 'patientid': '48319488', 'date': '2026-09-03', 'status': 'CHK', 'visittype': 'FU',
                                   'deleteflag': '0', 'doctorid': '515683'},
                                  {'encounterid': '283697020', 'patientid': '48319488', 'date': '2026-09-04', 'status': 'CHK', 'visittype': 'FU',
                                   'deleteflag': '0', 'doctorid': '777'}], instance=W)  # doctor missing from doctors.csv

    def test_clinical_references_resolve(self):
        clin, orgs, rep = build(self.db)
        encs = [r for r in ac.records(self.db, lambda k: 'P|' + k) if r['source_table'] == 'encounters']
        self.assertEqual(len(encs), 6)  # Epic 3, athena 1, eCW 2
        for e in encs:
            self.assertIn(e['organization_id'], orgs, e['encounter_id'])
            if e.get('attending_clinician_id'):
                self.assertIn(e['attending_clinician_id'], clin, e['encounter_id'])
        self.assertIsNone(next(e for e in encs if e['encounter_id'] == 'epic:EPIC-AR:5550003').get('attending_clinician_id'))
        self.assertEqual(clin['ecw:WHN-PRACTICE-AK-0:777']['organization_id'], 'WHN-PRACTICE-AK-0')

    def test_roster_and_ehr_merge_by_npi(self):
        clin, orgs, rep = build(self.db)
        self.assertEqual(clin['1727890836'], {'clinician_id': '1727890836', 'organization_id': 'WHN-PRACTICE-AR-0', 'display_name': 'Maria Hughes, MD',
                                              'specialty': 'Family Medicine', 'npi': '1727890836', 'taxonomy_code': '207Q00000X'})
        np_ = clin['epic:EPIC-AR:1588055']
        self.assertEqual((np_['npi'], np_['specialty'], np_['organization_id'], np_['display_name']),
                         (None, 'Nurse Practitioner', 'WHN-PRACTICE-AR-0', 'Sam Nguyen, NP'))
        self.assertEqual(clin['1882185311']['organization_id'], 'WHN-PRACTICE-AZ-0')
        self.assertIn('athena:2186:5121826', clin)
        self.assertEqual(clin['1943462097']['display_name'], 'Yusuf Patel, MD')
        self.assertIn('ecw:WHN-PRACTICE-AK-0:131909', clin)
        self.assertFalse(any('2030' in k for k in clin))

    def test_practice_organizations(self):
        clin, orgs, rep = build(self.db)
        self.assertEqual(orgs['WHN-PRACTICE-AR-0'], {'organization_id': 'WHN-PRACTICE-AR-0', 'organization_type': 'primary_care_practice',
                                                     'display_name': 'Synthetic AR primary care practice 0', 'npi': '1712816499',
                                                     'taxonomy_code': '261QP2300X', 'tin': '20-3313039', 'address_line': None, 'city': None,
                                                     'state': 'AR', 'postal_code': None})
        self.assertEqual((orgs['WHN-PRACTICE-AZ-0']['city'], orgs['WHN-PRACTICE-AZ-0']['postal_code']), ('Show Low', '85901'))
        self.assertEqual(orgs['WHN-PRACTICE-AK-0']['address_line'], '1 Main St')
        self.assertNotIn('2024-01-01', orgs)
        self.assertEqual(rep['organization_types']['primary_care_practice'], 3)


class OtherEntityTests(unittest.TestCase):
    def test_deepchart_departments(self):
        db = sqlite3.connect(':memory:')
        table(db, 'ehr_epic_clarity_dep', [{'department_id': '1', 'department_name': 'SYNTHETIC CARE PRACTICE 8 FAMILY MEDICINE'},
                                           {'department_id': '2', 'department_name': 'SYNTHETIC DISTRICT 1 HOSPITAL CARE FAMILY MEDICINE'}],
              instance='epic/EPIC-DEEPCHART')
        clin, orgs, rep = build(db)
        self.assertEqual((orgs['WH-PRACTICE-7']['organization_type'], orgs['WH-PRACTICE-7']['display_name']), ('primary_care', 'Synthetic care practice 8'))
        self.assertEqual((orgs['WH-HOSPITAL-0']['organization_type'], orgs['WH-HOSPITAL-0']['display_name']),
                         ('hospital', 'Synthetic district 1 hospital care'))

    def test_hospitals_payers_pharmacies_and_claim_npis(self):
        db = sqlite3.connect(':memory:')
        table(db, 'rosters_aco_provider_roster', [{'tin': '203313039', 'practice_id': 'WHN-PRACTICE-AK-0', 'group_npi': '1712816499',
                                                   'provider_npi': '1201884073', 'provider_first_name': 'Priya', 'provider_last_name': 'Tran',
                                                   'credential': 'MD', 'taxonomy_code': '207Q00000X'}], feed='rosters', drop='2026-09')
        table(db, 'hl7_message', [{'message_code': 'ADT', 'sending_facility': 'HOSPAK0', 'patient_id_assigning_authority': 'HOSPAK0'},
                                  {'message_code': 'ORU', 'sending_facility': 'REFLAB', 'patient_id_assigning_authority': 'PRACCA5',
                                   'receiving_facility': 'PRACCA5'}], feed='hl7', drop='2026-09')
        table(db, 'payers_eligibility', [{'member_id': 'C1', 'payer': 'C1'}], feed='payers', instance='WH-PAYER-C1')
        table(db, 'sdoh_vendor', [{'member_id': 'X'}], feed='sdoh_vendor')
        table(db, 'ncpdp_claim', [{'service_provider_id': '1167942964', 'service_provider_id_qualifier': '01', 'prescriber_id': '1241139314',
                                   'prescriber_id_qualifier': '01', 'file_sender': 'WH-PAYER-MA1'}], feed='ncpdp', instance='WH-PAYER-MA1')
        table(db, 'x12_837_claim', [
            {'transaction_type': '837I', 'facility_code': '11', 'billing_provider_npi': '1618494102', 'billing_provider_tin': '076629922',
             'billing_provider_name': 'BILLING PROVIDER', 'attending_npi': '1111111111', 'payer_id': 'WH-PAYER-MSSP'},
            {'transaction_type': '837P', 'facility_code': '22', 'billing_provider_npi': '1222222222', 'billing_provider_name': 'BILLING PROVIDER',
             'rendering_npi': '1333333333', 'payer_id': 'WH-PAYER-C1'},
            {'transaction_type': '837P', 'facility_code': '81', 'billing_provider_npi': '1444444444', 'payer_id': 'WH-PAYER-C1'},
            {'transaction_type': '837P', 'facility_code': '11', 'billing_provider_npi': '1712816499', 'billing_provider_tin': '203313039',
             'billing_provider_name': 'WHN-PRACTICE-AK-0', 'rendering_npi': '1201884073', 'payer_id': 'WH-PAYER-C1'}],
            feed='x12_payer', instance='WH-PAYER-C1')
        clin, orgs, rep = build(db)
        self.assertEqual(orgs['WHN-HOSPITAL-AK-0']['organization_type'], 'hospital')
        self.assertEqual(orgs['WHN-PRACTICE-CA-5']['organization_type'], 'primary_care_practice')
        self.assertEqual(orgs['REFLAB']['organization_type'], 'lab')
        self.assertEqual({o for o in orgs if o.startswith('WH-PAYER-')}, {'WH-PAYER-C1', 'WH-PAYER-MA1', 'WH-PAYER-MSSP'})
        self.assertEqual(orgs['WHN-SDOH-VENDOR']['organization_type'], 'data_vendor')
        self.assertEqual(orgs['NPI-1167942964']['organization_type'], 'pharmacy')
        self.assertEqual((orgs['NPI-1618494102']['organization_type'], orgs['NPI-1618494102']['tin']), ('hospital', '07-6629922'))
        self.assertEqual(orgs['NPI-1222222222']['organization_type'], 'specialty_group')  # professional group at a hospital POS
        self.assertEqual(orgs['NPI-1444444444']['organization_type'], 'lab')
        self.assertNotIn('NPI-1712816499', orgs)  # the roster practice's group NPI: no second row
        self.assertEqual(clin['1333333333']['organization_id'], 'NPI-1222222222')
        self.assertEqual(clin['1111111111']['organization_id'], 'NPI-1618494102')
        self.assertIn('1241139314', clin)  # prescriber
        self.assertEqual(clin['1201884073']['organization_id'], 'WHN-PRACTICE-AK-0')  # roster wins over billing org

    def test_npi_check_digit(self):
        self.assertTrue(ae.npi_valid('1234567893'))
        self.assertFalse(ae.npi_valid('1234567890'))
        self.assertFalse(ae.npi_valid('12345'))


if __name__ == '__main__':
    unittest.main()
