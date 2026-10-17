"""Tests for clinical_core.agere.ingest.identity (person resolution over landing tables). Synthetic rows only.

    .venv/bin/python -m unittest test_ingest_identity
"""
import hashlib
import json
import sqlite3
import unittest

from clinical_core.agere.ingest import identity

LINEAGE = ('_feed', '_instance', '_drop', '_file', '_row')


def _add(db, table, rows, *, feed=None, instance=None, drop='2027-01'):
    cols = list(LINEAGE)
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    existing = [c[1] for c in db.execute(f'PRAGMA table_info("{table}")')]
    if not existing:
        db.execute(f'CREATE TABLE "{table}" (' + ', '.join(f'"{c}"' for c in cols) + ')')
        existing = cols
    for c in cols:
        if c not in existing:
            db.execute(f'ALTER TABLE "{table}" ADD COLUMN "{c}"')
    for i, r in enumerate(rows):
        row = {'_feed': feed, '_instance': instance, '_drop': r.pop('_drop', drop), '_file': f'{table}.txt', '_row': i}
        row.update(r)
        keys = list(row)
        db.execute(f'INSERT INTO "{table}" (' + ', '.join(f'"{k}"' for k in keys) + ') VALUES ('
                   + ', '.join('?' * len(keys)) + ')', [row[k] for k in keys])


def landing():
    db = sqlite3.connect(':memory:')
    # Example A: FFS beneficiary across CCLF / ALR / 834 / Epic deep chart / FHIR / SDOH
    _add(db, 'cclf_beneficiary_demographics', [
        dict(bene_mbi_id='2DX0U77CP79', bene_1st_name='DOROTHY', bene_last_name='EVANS', bene_dob='1948-08-15',
             bene_sex_cd='2', bene_race_cd='1', bene_line_1_adr='745 CENTER ST', geo_zip_plc_name='AMHERST',
             geo_usps_state_cd='MA', geo_zip5_cd='01059', bene_fips_state_cd='25', bene_fips_cnty_cd='15',
             bene_rng_bgn_dt='2026-08-28', bene_rng_end_dt=''),
        # Example C: MBI re-issue, contiguous spans + same demographics
        dict(bene_mbi_id='2H51Q97FQ99', bene_1st_name='ANN', bene_last_name='RIVERA', bene_dob='1952-09-24',
             bene_sex_cd='2', geo_zip5_cd='33101', bene_rng_bgn_dt='2026-09-01', bene_rng_end_dt='2028-06-16'),
        dict(bene_mbi_id='2HBCAB84FC1', bene_1st_name='ANN', bene_last_name='RIVERA', bene_dob='1952-09-24',
             bene_sex_cd='2', geo_zip5_cd='33101', bene_rng_bgn_dt='2028-06-17', bene_rng_end_dt=''),
        # cross-source different sex AND different ZIP: must not merge with the ADT person below
        dict(bene_mbi_id='3QQ1AA2BB33', bene_1st_name='PAT', bene_last_name='QUINN', bene_dob='1950-05-05',
             bene_sex_cd='1', geo_zip5_cd='10001', bene_rng_bgn_dt='2026-08-28'),
    ], feed='cms_cclf_csv', instance='beneficiary_demographics')
    _add(db, 'cclf_beneficiary_xref', [dict(crnt_num='2HBCAB84FC1', prvs_num='', prvs_id_obslt_dt='2028-06-17')],
         feed='cms_cclf_csv', instance='beneficiary_xref')
    _add(db, 'alr_aalr1_assigned_beneficiaries', [
        dict(bene_mbi_id='2DX0U77CP79', bene_1st_name='DOROTHY', bene_last_name='EVANS', bene_sex_cd='2',
             bene_brth_dt='08/15/1948', geo_ssa_state_name='MA', state_county_cd='25015')], feed='cms_alr')
    _add(db, 'x12_834_member', [
        dict(sponsor_name='WH-PAYER-1', member_id='2DX0U77CP79', subscriber_id='2DX0U77CP00',
             subscriber_indicator='Y', relationship_code='18', first_name='DOROTHY', last_name='EVANS',
             dob='1948-08-15', gender='F', address_1='745 CENTER ST', city='AMHERST', state='MA', postal_code='01059',
             plan_coverage_description='MEDICARE_FFS')], feed='x12_834', instance='WH-PAYER-1')
    _add(db, 'ehr_epic_patient', [
        dict(pat_id='Z2010159', first_name='Dorothy', last_name='Evans', birth_date='1948-08-15', sex_c='1',
             add_line_1='745 Center St', city='Amherst', state_c='25', zip='01059', county_c='25015')],
         feed='ehr', instance='epic/EPIC-DEEPCHART')
    _add(db, 'ehr_epic_identity_id', [
        dict(pat_id='Z2010159', line='12', identity_id='eef1cf8279db851e', identity_type_id='6525'),
        dict(pat_id='Z2010159', line='8', identity_id='fc7e1b4826e79079', identity_type_id='7081')],
         feed='ehr', instance='epic/EPIC-DEEPCHART')
    _add(db, 'fhir_patient', [
        dict(id='79836880', mrn='fc7e1b4826e79079', name_family='Evans', name_given='Dorothy',
             birth_date='1948-08-15', gender='female', address_zip='01059', _drop='2029-06'),
        dict(id='79836880', mrn='eef1cf8279db851e', name_family='Evans', name_given='Dorothy',
             birth_date='1948-08-15', gender='female', address_zip='01059', _drop='2029-07')],
         feed='fhir_bulk', instance='DEEPCHART')
    _add(db, 'sdoh_vendor', [dict(member_id='2DX0U77CP79', first_name='Dorothy', last_name='Evans',
                                  dob='1948-08-15', zip='01059', county_fips='25015')], feed='sdoh_vendor')
    # Example B: EHR migration athena -> EPIC-CA; FHIR Patient.id carries both MRNs; Epic spells the name differently
    _add(db, 'ehr_athena_patient', [
        dict(contextid='8711', patientid='2437453', enterpriseid='ICE-CA-8-ADB41A8', firstname='Harold',
             lastname='Wright', sex='M', dob='2018-01-28', zip='93001')], feed='ehr', instance='athena/8711')
    _add(db, 'ehr_epic_patient', [
        dict(pat_id='Z7000001', first_name='Hal', last_name='Wright-Jones', birth_date='2018-01-28', sex_c='2',
             zip='93001')], feed='ehr', instance='epic/EPIC-CA')
    _add(db, 'ehr_epic_identity_id', [dict(pat_id='Z7000001', line='1', identity_id='E7576157',
                                           identity_type_id='7200')], feed='ehr', instance='epic/EPIC-CA')
    _add(db, 'ehr_epic_identity_id_type', [dict(id_type='7200', id_type_name='WHN CA PRIMARY CARE 8 MRN')],
         feed='ehr', instance='epic/EPIC-CA')
    _add(db, 'fhir_patient', [
        dict(id='52381260', mrn='ICE-CA-8-ADB41A8', name_family='Wright', name_given='Harold',
             birth_date='2018-01-28', gender='male', _drop='2028-06'),
        dict(id='52381260', mrn='E7576157', name_family='Wright', name_given='Harold', birth_date='2018-01-28',
             gender='male', _drop='2029-06')], feed='fhir_bulk', instance='WHN-PRACTICE-CA-8')
    _add(db, 'hl7_message', [
        dict(message_code='ORU', patient_id='E7576157', patient_id_assigning_authority='PRACCA8',
             patient_name_family='WRIGHT', patient_name_given='HAROLD', patient_dob='2018-01-28', patient_sex='M'),
        dict(message_code='ADT', patient_id='L-NY-0-ABCDEF12', patient_id_assigning_authority='HOSPNY0',
             patient_name_family='QUINN', patient_name_given='PAT', patient_dob='1950-05-05', patient_sex='F',
             patient_address_zip='99999')], feed='hl7')
    # Example D: duplicate member ids at one payer, same demographics, each with its own subscriber id
    _add(db, 'payers_eligibility', [
        dict(payer='MCD2', member_id=m, subscriber_id=m[:-2] + '00', first_name='Julie', last_name='Johnson',
             dob='20000816', gender='F', address='6448 Main St', city='Portland', state='OR', zip='97233')
        for m in ('MCD286C2678E6', 'MCD23A0B6A661', 'MCD2DA237233F')], feed='payers', instance='WH-PAYER-MCD2')
    # Example E: same name + DOB, different sex, inside one source -> two people; payer member (M) joins the man
    _add(db, 'ehr_epic_patient', [
        dict(pat_id='Z1000001', first_name='Jordan', last_name='Lee', birth_date='1990-01-01', sex_c='1', zip='43001'),
        dict(pat_id='Z1000002', first_name='Jordan', last_name='Lee', birth_date='1990-01-01', sex_c='2', zip='43001')],
         feed='ehr', instance='epic/EPIC-OH')
    _add(db, 'payers_eligibility', [
        dict(payer='C1', member_id='C1AAAA0001F', subscriber_id='C1AAAA00000', first_name='JORDAN', last_name='LEE',
             dob='01/01/1990', gender='M', zip='43001')], feed='payers', instance='WH-PAYER-C1')
    # an 835 member id never seen with demographics -> unresolved; NM1 element drop shifts id into the qualifier
    _add(db, 'x12_835_claim', [dict(payer_name='WH-PAYER-MCD1', patient_last='PATIENT',
                                    patient_id_qualifier='MCD1ZZZZ9999')], feed='x12', instance='835')
    db.commit()
    return db


class Helpers(unittest.TestCase):
    def test_normalizers(self):
        self.assertEqual(identity.normalize_mbi(' 2dx0-u77-cp79 '), '2DX0U77CP79')
        self.assertIsNone(identity.normalize_mbi('C1227F77B3A'))
        self.assertEqual(identity.normalize_member_id(' mcd286c2678e6 '), 'MCD286C2678E6')
        self.assertEqual(identity.demographic_key('Évans', 'Dorothy Ann', '08/15/1948'), 'evans|dorothy|1948-08-15')
        self.assertEqual(identity.demographic_key("O'Neil-Smith", 'JO', '19480815'), 'oneilsmith|jo|1948-08-15')
        self.assertIsNone(identity.demographic_key('Carter', 'Gabriel', '2031-07-23'))
        self.assertIsNone(identity.demographic_key('Old', 'Person', '1899-12-31'))
        self.assertEqual(identity.parse_date('09121964'), '1964-09-12')
        self.assertEqual(identity.parse_date('20051022'), '2005-10-22')
        self.assertEqual(identity.normalize_sex('1', 'epic'), 'F')
        self.assertEqual(identity.normalize_sex('1', 'cms'), 'M')
        self.assertEqual(identity.normalize_sex('female'), 'F')
        self.assertEqual(identity.mrn_key('ICE-CA-5-770C93E'), 'mrn:WHN-PRACTICE-CA-5:ICE-CA-5-770C93E')
        self.assertEqual(identity.mrn_key('CE-CA-11-A1A4C4D'), 'mrn:WHN-PRACTICE-CA-11:CE-CA-11-A1A4C4D')
        self.assertEqual(identity.mrn_key('E7576157', 'PRACCA8'), 'mrn:WHN-PRACTICE-CA-8:E7576157')
        self.assertEqual(identity.mrn_key('E7576157', 'WHN CA PRIMARY CARE 8 MRN'), 'mrn:WHN-PRACTICE-CA-8:E7576157')
        self.assertEqual(identity.mrn_key('fc7e1b4826e79079'), 'mrn:DEEPCHART:fc7e1b4826e79079')
        self.assertEqual(identity.member_key('MCD2', 'mcd286c2678e6'), 'member:WH-PAYER-MCD2:MCD286C2678E6')
        self.assertEqual(identity.fhir_key('fhir/WHN-PRACTICE-CA-5', 'Patient/91739737'), 'fhir:WHN-PRACTICE-CA-5:91739737')


class Build(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = landing()
        cls.report = identity.build(cls.db)
        cls.map = {k: (p, t, e) for k, p, t, e in cls.db.execute('SELECT * FROM stg_person_map')}
        cls.persons = {p: json.loads(j) for p, j in cls.db.execute('SELECT * FROM stg_person')}

    def pid(self, key):
        self.assertIn(key, self.map, key)
        return self.map[key][0]

    def same(self, *keys):
        pids = {self.pid(k) for k in keys}
        self.assertEqual(len(pids), 1, {k: self.pid(k) for k in keys})
        self.assertIsNotNone(next(iter(pids)))
        return next(iter(pids))

    def test_ffs_beneficiary_everywhere(self):
        pid = self.same('mbi:2DX0U77CP79', 'member:WH-PAYER-1:2DX0U77CP79', 'member:WH-PAYER-1:2DX0U77CP00',
                        'epic:EPIC-DEEPCHART:Z2010159', 'mrn:DEEPCHART:eef1cf8279db851e',
                        'mrn:DEEPCHART:fc7e1b4826e79079', 'fhir:DEEPCHART:79836880',
                        'member:sdoh_vendor:2DX0U77CP79')
        p = self.persons[pid]
        self.assertEqual(pid, 'AGP-' + hashlib.sha256(b'epic:EPIC-DEEPCHART:Z2010159').hexdigest()[:12])
        self.assertEqual(p['data_segment'], 'deep_chart')
        self.assertEqual(p['sex_reported'], 'F')
        self.assertEqual(p['birth_date'], '1948-08-15')
        self.assertEqual(p['age_reported'], 81)
        self.assertEqual(p['display_name'], 'Dorothy Evans')
        self.assertEqual(p['postal_code'], '01059')
        self.assertEqual(p['state'], 'MA')
        self.assertEqual(p['county_fips'], '25015')
        self.assertEqual(p['mbi'], ['2DX0U77CP79'])
        self.assertTrue(p['synthetic'])
        self.assertEqual(self.map['mrn:DEEPCHART:fc7e1b4826e79079'][1], 1)
        self.assertEqual(self.map['member:WH-PAYER-1:2DX0U77CP79'][1], 3)  # EHR anchor reaches claims by demographics

    def test_ehr_migration_via_fhir_id(self):
        pid = self.same('athena:8711:2437453', 'mrn:WHN-PRACTICE-CA-8:ICE-CA-8-ADB41A8', 'fhir:WHN-PRACTICE-CA-8:52381260',
                        'mrn:WHN-PRACTICE-CA-8:E7576157', 'epic:EPIC-CA:Z7000001', 'hl7:PRACCA8:E7576157')
        self.assertEqual(self.map['athena:8711:2437453'][1], 1)  # Epic name differs: bridged by tier 1 (FHIR id) only
        self.assertEqual(self.persons[pid]['identity_max_tier'], 1)
        self.assertEqual(self.persons[pid]['data_segment'], 'ehr_only')

    def test_mbi_reissue(self):
        self.same('mbi:2H51Q97FQ99', 'mbi:2HBCAB84FC1')
        t, ev = self.map['mbi:2HBCAB84FC1'][1:]
        self.assertEqual(t, 2)
        self.assertIn('re-issue', ev)

    def test_duplicate_member_ids_one_payer(self):
        ids = ('MCD286C2678E6', 'MCD23A0B6A661', 'MCD2DA237233F')
        keys = [f'member:WH-PAYER-MCD2:{m}' for m in ids] + [f'member:WH-PAYER-MCD2:{m[:-2]}00' for m in ids]
        pid = self.same(*keys)
        self.assertEqual(self.persons[pid]['data_segment'], 'claims_only')
        self.assertEqual(len(self.persons[pid]['member_ids']), 6)

    def test_same_name_dob_different_sex_not_merged(self):
        f, m = self.pid('epic:EPIC-OH:Z1000001'), self.pid('epic:EPIC-OH:Z1000002')
        self.assertNotEqual(f, m)
        self.assertEqual(self.pid('member:WH-PAYER-C1:C1AAAA0001F'), m)
        self.assertEqual(self.persons[f]['sex_reported'], 'F')
        self.assertEqual(self.persons[m]['data_segment'], 'claims_plus_ehr')
        self.assertNotEqual(self.pid('mbi:3QQ1AA2BB33'), self.pid('hl7:HOSPNY0:L-NY-0-ABCDEF12'))

    def test_unresolved_and_report(self):
        p, t, ev = self.map['member:WH-PAYER-MCD1:MCD1ZZZZ9999']
        self.assertIsNone(p)
        self.assertIn('unresolved', ev)
        r = self.report
        self.assertEqual(r['persons'], len(self.persons))
        self.assertEqual(r['persons'], 8)  # Dorothy, Harold, Ann, Julie, Jordan F, Jordan M, Pat (CCLF), Pat (ADT)
        self.assertGreaterEqual(r['clusters_merged_by_demographics'], 2)
        self.assertEqual(r['unresolved_total'], 1)
        self.assertGreaterEqual(r['tier3_blocked'].get('same_source_sex_conflict', 0), 1)

    def test_rebuild_is_deterministic(self):
        before = sorted(self.db.execute('SELECT * FROM stg_person_map'))
        identity.build(self.db)
        self.assertEqual(before, sorted(self.db.execute('SELECT * FROM stg_person_map')))


if __name__ == '__main__':
    unittest.main()
