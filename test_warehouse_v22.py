"""Expanded-population warehouse releases (v2.2): adapter/3 dispatch and receipts beside adapter/1 and adapter/2,
semantic population-feed tables, enrollment-span member months / payer attribution / payer-claim cost in vbc, and
the --expansion activation gate."""
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from build_healthcare_warehouse_stack import ingest as build_ingest
from clinical_core.contracts import digest
from clinical_core.agere import healthcare_warehouse as v1, healthcare_warehouse_v2 as v2, healthcare_warehouse_v3 as v3
from clinical_core.agere.warehouse import Warehouse
from clinical_core.agere.warehouse_activation import validate_migration
from clinical_core.agere.clinical import semantic_layer, source_profile, sql_query, vbc
from test_aggregate_explain import AS_OF, pcp
from test_healthcare_warehouse import source
from test_warehouse_v2 import ROWS, release

RELEASES = Path('/Users/mikel/synthetic-patient-ecosystem/releases')


def expanded_release(root, *, claim_added=0):
    """A v2.2-shaped release: data_segment roster, population feeds, an evaluation answer key that must never be read."""
    db = sqlite3.connect(root / 'warehouse.sqlite')
    db.executescript('CREATE TABLE patients(patient_id TEXT PRIMARY KEY,display_name TEXT,age_reported INTEGER,sex_reported TEXT,'
                     'profile_captured_date TEXT,birth_date TEXT,county_fips TEXT,data_segment TEXT);'
                     'CREATE TABLE organizations(organization_id TEXT, organization_type TEXT, npi TEXT);'
                     'CREATE TABLE clinicians(clinician_id TEXT, npi TEXT);'
                     'CREATE TABLE data_dictionary(object_name TEXT, column_name TEXT, data_type TEXT, source_table TEXT, json_path TEXT,'
                     ' description TEXT, code_system TEXT, populated_share REAL);'
                     # Evaluation truth shipped in the public file: a private-field name makes any read fail loudly.
                     'CREATE TABLE eval_mpi_answer_key(enterprise_patient_id TEXT, truth TEXT);'
                     "INSERT INTO eval_mpi_answer_key VALUES ('M-dup', 'P-original');")
    db.executemany('INSERT INTO patients VALUES (?,?,?,?,?,?,?,?)', [
        ('P-original', 'Synthetic', 42, 'F', '2026-08-28', '1984-02-01', '25017', 'deep_chart'),
        ('M-claims', 'Synthetic', 61, 'M', '2026-09-01', '1965-03-01', '18097', 'claims_only'),
        ('M-dup', 'Synthetic', 61, 'M', '2026-09-01', '1965-03-01', '18097', 'duplicate_chart')])
    db.execute("INSERT INTO organizations VALUES ('ASC-1','asc','1234567893')")
    db.execute("INSERT INTO clinicians VALUES ('CL','1245319599')")
    rows = ROWS + [source(record_id='span1', patient_id='M-claims', source_table='enrollment_spans', event_date='2028-01-01',
                          facts=json.dumps({'member_id': 'X1', 'payer_id': 'WH-PAYER-C1', 'product': 'commercial', 'plan_id': 'C1-COM',
                                            'coverage_start': '2028-01-01', 'coverage_end': None})),
                   source(record_id='pc1', patient_id='M-claims', source_table='payer_medical_claims', event_date='2028-02-10',
                          facts=json.dumps({'payer_claim_id': 'PC1', 'claim_status': 'paid', 'adjudication_sequence': 1,
                                            'procedure_code': None, 'principal_diagnosis': 'I10', 'allowed_amount': 27.5})),
                   source(record_id='hie1', patient_id='M-claims', source_table='hie_documents', event_date='2028-03-01',
                          facts=json.dumps({'document_type': 'ED summary', 'loinc_document_code': '34111-5', 'text': 'Seen in ED.'}))]
    keys = list(rows[0])
    db.execute('CREATE TABLE source_records(' + ','.join(k + (' INTEGER' if k == 'synthetic' else ' TEXT') for k in keys) + ')')
    db.executemany('INSERT INTO source_records VALUES (' + ','.join('?' for _ in keys) + ')', [[r[k] for k in keys] for r in rows])
    db.commit(); db.close()
    manifest = {'schema': 'healthcare-warehouse/2', 'engine': 'healthcare-warehouse-v2.2-population/1', 'as_of': '2030-05-27',
                'files': {'warehouse.sqlite': v1.checksum(root / 'warehouse.sqlite')},
                'metrics': {'original_patient_id_set_preserved': True, 'new_patients_added': claim_added,
                            'population_v21': {'segments': {'claims_only': 1}}}}
    (root / 'manifest.json').write_text(json.dumps(manifest))
    (root / 'private').mkdir(); (root / 'private' / 'truth.json').write_text('NOT JSON: never opened')
    return manifest


class AdapterDispatch(unittest.TestCase):
    def test_manifest_dispatch(self):
        self.assertEqual(v3.adapter_for({'schema': 'single-healthcare-warehouse/1'}), v1.VERSION)
        self.assertEqual(v3.adapter_for({'schema': 'healthcare-warehouse/2', 'engine': 'healthcare-warehouse-v2-standards/1',
                                         'metrics': {}}), v2.VERSION)
        self.assertEqual(v3.adapter_for({'schema': 'healthcare-warehouse/2', 'metrics': {'population_v21': {}}}), v3.VERSION)
        with self.assertRaises(ValueError): v3.adapter_for({'schema': 'other'})

    @unittest.skipUnless((RELEASES / 'healthcare-warehouse-v2.2' / 'manifest.json').exists(), 'releases not present')
    def test_real_release_manifests_pick_their_adapter(self):
        want = {'healthcare-warehouse-v1.23': v1.VERSION, 'healthcare-warehouse-v2.0': v2.VERSION, 'healthcare-warehouse-v2.2': v3.VERSION}
        for name, adapter in want.items():
            manifest = json.loads((RELEASES / name / 'manifest.json').read_text())
            self.assertEqual(v3.adapter_for(manifest), adapter, name)


class AdapterReadsAllThreeReleases(unittest.TestCase):
    def ingest(self, make):
        d = Path(tempfile.mkdtemp()); make(d)
        w = Warehouse(d / 'derived.sqlite3')
        try:
            rel, receipt = build_ingest(w, d)
            patients, snapshot = w.snapshot(rel, '2030-05-27')
        finally: w.close()
        db = sqlite3.connect(d / 'warehouse.sqlite'); db.row_factory = sqlite3.Row
        try: raw = {r['record_id']: v1.project(r) for r in db.execute('SELECT * FROM source_records')}
        finally: db.close()
        records = {r['record_id']: r for p in patients.values() for r in p['records']}
        return receipt, snapshot, records, raw, patients

    def test_record_contract_identical_across_adapters(self):
        cases = [(lambda d: release(d, 'single-healthcare-warehouse/1', ROWS, patient_cols=False), v1.VERSION),
                 (lambda d: release(d, 'healthcare-warehouse/2', ROWS), v2.VERSION),
                 (expanded_release, v3.VERSION)]
        for make, adapter in cases:
            receipt, snapshot, records, raw, _ = self.ingest(make)
            self.assertEqual(receipt['adapter'], adapter)
            self.assertEqual(snapshot['source_dataset']['adapter'], adapter)
            self.assertEqual(records, raw)  # every record = adapter/1 projection of its row
            self.assertTrue(all(r['_warehouse_source']['adapter'] == v1.VERSION for r in records.values()))

    def test_expanded_receipt_measures_roster_and_skips_evaluation_tables(self):
        receipt, _, records, _, patients = self.ingest(expanded_release)
        self.assertEqual(receipt['segments'], {'deep_chart': 1, 'claims_only': 1, 'duplicate_chart': 1})
        self.assertEqual(receipt['original_patient_id_digest'], digest(['M-claims', 'M-dup', 'P-original']))
        self.assertEqual(receipt['panel_patient_id_digest'], digest(['P-original']))
        self.assertEqual(receipt['patients_without_received_records'], 1)
        self.assertFalse(receipt['manifest_roster_claim_consistent'])  # manifest says 0 new patients; 2 were added
        self.assertEqual(receipt['evaluation_tables_present_not_read'], ['eval_mpi_answer_key'])
        self.assertNotIn('eval_mpi_answer_key', receipt['read_boundary'])
        self.assertFalse(receipt['private_files_read'])
        self.assertEqual(patients['M-claims']['patient']['data_segment'], 'claims_only')
        self.assertEqual(records['span1']['product'], 'commercial')
        self.assertEqual(receipt['records_by_code_system'].get('LOINC'), 2)  # lab loinc + HIE document LOINC

    def test_consistent_roster_claim_and_unknown_patient(self):
        receipt = self.ingest(lambda d: expanded_release(d, claim_added=2))[0]
        self.assertTrue(receipt['manifest_roster_claim_consistent'])
        d = Path(tempfile.mkdtemp()); expanded_release(d)
        db = sqlite3.connect(d / 'warehouse.sqlite'); db.execute("UPDATE source_records SET patient_id='nobody' WHERE record_id='hie1'"); db.commit(); db.close()
        m = json.loads((d / 'manifest.json').read_text()); m['files']['warehouse.sqlite'] = v1.checksum(d / 'warehouse.sqlite')
        (d / 'manifest.json').write_text(json.dumps(m))
        w = Warehouse(d / 'derived.sqlite3')
        try:
            with self.assertRaisesRegex(ValueError, 'Unknown source patient'): v3.ingest(w, d)
        finally: w.close()


# --- semantic population tables --------------------------------------------------------------------------------------
def stack_fixture(records, patients, entities=()):
    """Stack fixture with a sibling warehouse.sqlite3 so the semantic layer can read directory entities."""
    root = Path(tempfile.mkdtemp()); path = root / 'stack'; path.mkdir()
    (path / 'run.json').write_text(json.dumps({'source_digest': 'fixture', 'snapshot': {'as_of': AS_OF, 'release': 'rel'}}))
    db = sqlite3.connect(path / 'stack.sqlite3')
    db.execute('CREATE TABLE patients(id TEXT PRIMARY KEY, payload TEXT)')
    db.execute('CREATE TABLE records(id TEXT PRIMARY KEY, patient TEXT, stream TEXT, payload TEXT)')
    db.execute('CREATE TABLE result_cache(id TEXT PRIMARY KEY, payload TEXT)')
    for pid, p in patients.items(): db.execute('INSERT INTO patients VALUES (?,?)', (pid, json.dumps({'patient_id': pid, **p})))
    for rid, pid, stream, r in records:
        db.execute('INSERT INTO records VALUES (?,?,?,?)', (rid, pid, stream, json.dumps({'record_id': rid, 'patient_id': pid, 'source_table': stream, **r})))
    db.commit(); db.close()
    w = sqlite3.connect(root / 'warehouse.sqlite3')
    w.execute('CREATE TABLE source_entities(release TEXT, kind TEXT, id TEXT, payload TEXT)')
    w.executemany('INSERT INTO source_entities VALUES (?,?,?,?)', [('rel', k, o.get('organization_id') or o.get('clinician_id'), json.dumps(o)) for k, o in entities])
    w.commit(); w.close()
    source_profile._CACHE.clear()
    return path


def ahc(pid, rid, day, answers):
    return (rid, pid, 'social_screens', {'instrument': 'AHC_HRSN', 'panel_loinc': '96777-8', 'event_date': day,
                                         'items': [{'item': k, 'answer': v} for k, v in answers.items()]})


SPAN = lambda rid, pid, product, start, end=None, **kw: (rid, pid, 'enrollment_spans', {
    'payer_id': kw.get('payer', 'WH-PAYER-1'), 'product': product, 'plan_id': kw.get('plan'), 'coverage_start': start, 'coverage_end': end,
    'attribution_practice_id': kw.get('practice'), 'attribution_npi': kw.get('npi'), 'event_date': start, 'available_date': start})
PAYER_CLAIM = lambda rid, pid, cid, status, seq, day, allowed, paid, **kw: (rid, pid, 'payer_medical_claims', {
    'payer_claim_id': cid, 'claim_status': status, 'adjudication_sequence': seq, 'service_from': day, 'product': kw.get('product', 'commercial'),
    'allowed_amount': allowed, 'paid_amount': paid, 'billing_provider_npi': kw.get('npi'), 'place_of_service': kw.get('pos'),
    'event_date': kw.get('event', day), 'available_date': kw.get('event', day)})
PATIENTS = {'m1': {'age_reported': 61, 'sex_reported': 'M', 'data_segment': 'claims_only', 'county_fips': '18097'},
            'm2': {'age_reported': 44, 'sex_reported': 'F', 'data_segment': 'claims_plus_ehr'},
            'p1': {'age_reported': 70, 'sex_reported': 'F', 'data_segment': 'deep_chart', 'county_fips': '25025'},
            'p2': {'age_reported': 50, 'sex_reported': 'M', 'data_segment': 'deep_chart'},
            'd1': {'age_reported': 61, 'sex_reported': 'M', 'data_segment': 'duplicate_chart'}}
RECORDS = [
    SPAN('s1', 'm1', 'commercial', '2028-01-15', '2028-06-10', plan='C1-COM', practice='PR-A', npi='1111111111'),
    SPAN('s2', 'm1', 'medicare_advantage', '2028-09-01', practice='PR-B'),
    SPAN('s3', 'm2', 'medicaid', '2029-01-01', '2029-03-31'),
    SPAN('s4', 'p1', 'medicare_ffs', '2028-01-01'),
    ('cov1', 'p1', 'coverage', {'coverage_type': 'medicare', 'status': 'active', 'effective_start': '2027-01-01', 'event_date': '2027-01-01'}),
    ('cov2', 'p2', 'coverage', {'coverage_type': 'commercial', 'status': 'active', 'effective_start': '2028-07-01', 'event_date': '2028-07-01'}),
    ('pcp1', *pcp('p1', 'drA', 'PR1')),
    PAYER_CLAIM('pc1', 'm1', 'X1', 'paid', 1, '2028-02-03', 100.0, 80.0, npi='2222222222', pos='24'),
    PAYER_CLAIM('pc2', 'm1', 'X2', 'paid', 1, '2028-03-03', 50.0, 40.0),
    PAYER_CLAIM('pc3', 'm1', 'X2', 'reversed', 2, '2028-03-03', -50.0, -40.0, event='2028-04-01'),
    PAYER_CLAIM('pc4', 'm1', 'X3', 'denied', 1, '2028-04-03', 0, 0),
    ('pcl1', 'm1', 'payer_medical_claim_lines', {'payer_claim_id': 'X1', 'line_number': 1, 'procedure_code': '99213',
                                                 'procedure_code_system': 'CPT', 'units': 1, 'charge_amount': 150.0, 'allowed_amount': 100.0}),
    # provider-side claim for a payer-feed member: the payer feed is preferred for cost, so this one is not added
    ('mc1', 'm1', 'medical_claims', {'claim_id': 'MC1', 'service_date': '2028-03-10', 'allowed_cents': 5000, 'payer_paid_cents': 4000,
                                     'status': 'paid', 'event_date': '2028-03-10', 'available_date': '2028-03-10'}),
    ('rx1', 'm2', 'payer_pharmacy_claims', {'rx_claim_id': 'RX1', 'drug_name': 'lisinopril 10 MG Oral Tablet', 'claim_status': 'paid',
                                            'plan_paid': 8.0, 'patient_pay': 2.0, 'ndc': '00172', 'bin': '990008', 'pcn': 'MAPD',
                                            'event_date': '2029-01-05', 'available_date': '2029-01-06'}),
    ('rx2', 'm2', 'payer_pharmacy_claims', {'rx_claim_id': 'RX2', 'drug_name': 'lisinopril 10 MG Oral Tablet', 'claim_status': 'rejected',
                                            'event_date': '2029-02-05', 'available_date': '2029-02-06'}),
    ('e1', 'm2', 'medication_orders', {'medication_name': 'lisinopril 10 MG Oral Tablet', 'order_class': 'e-prescribe', 'event_date': '2029-01-02',
                                       'linked_fill_event_ids': ['WHN-RX-m2-ACE in-2029-01-05', 'WHN-RX-m2-ACE in-2029-02-05']}),
    ('e2', 'm2', 'medication_orders', {'medication_name': 'doxycycline 100 MG Oral Capsule', 'order_class': 'e-prescribe',
                                       'event_date': '2029-01-10', 'linked_fill_event_ids': []}),
    ('e3', 'm2', 'medication_orders', {'medication_name': 'metformin 500 MG Oral Tablet', 'order_class': 'e-prescribe', 'event_date': '2029-02-20',
                                       'linked_fill_event_ids': ['WHN-RX-m2-acute-2029-03-01T10:00:00']}),
    ('o1', 'p1', 'medication_orders', {'medication_name': 'amlodipine', 'rxcui': '308136', 'order_class': 'outpatient_prescription',
                                       'encounter_id': 'ENC1', 'event_date': '2028-05-01'}),
    ('d1x', 'p1', 'pharmacy_dispenses', {'medication_name': 'Amlodipine', 'rxcui': '308136', 'status': 'dispensed', 'event_date': '2028-05-03'}),
    ('l1', 'p1', 'medication_orders', {'medication_name': 'Atorvastatin 40 MG', 'event_date': '2027-01-01'}),
    ('l1d', 'p1', 'pharmacy_dispenses', {'medication_name': 'Atorvastatin 40 MG', 'status': 'dispensed', 'medication_order_record_id': 'l1',
                                         'event_date': '2027-01-04'}),
    ('h1', 'm2', 'hie_documents', {'document_type': 'ED summary', 'loinc_document_code': '34111-5', 'source_encounter': 'E9',
                                   'author_organization_id': 'HOSP', 'text': 'Seen in ED for wheeze.', 'event_date': '2029-02-01'}),
    ('v1', 'm1', 'sdoh_vendor_attributes', {'delivery_date': '2027-01-15', 'member_match_status': 'exact', 'county_fips': '18097',
                                            'svi_overall_percentile': 82.6, 'svi_socioeconomic_percentile': 81.3, 'food_access_low_flag': True,
                                            'event_date': '2027-01-15'}),
    ('v2', 'm1', 'sdoh_vendor_attributes', {'delivery_date': '2028-07-15', 'member_match_status': 'exact', 'county_fips': '18097',
                                            'svi_overall_percentile': 82.6, 'svi_socioeconomic_percentile': 81.3, 'event_date': '2028-07-15'}),
    ('v3', 'm2', 'sdoh_vendor_attributes', {'delivery_date': '2028-07-15', 'member_match_status': 'partial_address', 'county_fips': '06037',
                                            'svi_overall_percentile': 40.1, 'event_date': '2028-07-15'}),
    ahc('m2', 'a1', '2029-01-02', {'food_worry': 'yes', 'food_ran_out': 'no', 'transportation_barrier': 'no', 'housing_situation': 'no',
                                   'utilities_shutoff': 'yes', 'physical_safety': 'no'}),
    ('n1', 'm2', 'notes', {'note_type': 'office_followup', 'author_id': 'CL', 'author_credential': 'NP', 'template_style': 'terse',
                           'note_status': 'signed', 'signed_date': '2029-01-02', 'text': 'f/u HTN. No SOB.', 'event_date': '2029-01-02'}),
    ('enc1', 'p1', 'encounters', {'type': 'office', 'encounter_id': 'ENC1', 'status': 'completed', 'event_date': '2028-05-01',
                                  'diagnoses': [{'rank': 1, 'icd10': 'I10', 'type': 'primary'}]}),
    ('c1', 'p1', 'conditions', {'description': 'Essential hypertension', 'icd10': 'I10', 'onset': '2028-05-01', 'event_date': '2028-05-01',
                                'diagnosis_basis': 'clinical_encounter', 'diagnosing_encounter_id': 'ENC1'}),
    ('c0', 'p1', 'conditions', {'description': 'Type 2 diabetes mellitus', 'icd10': 'E11.9', 'event_date': '2026-08-28'}),
    ('plu', 'p1', 'notes', {'note_type': 'problem_list_update', 'encounter_id': 'ENC1', 'event_date': '2028-05-01',
                            'text': 'New problem added to the list: Essential hypertension (I10).\nBasis: two high office BPs.'}),
]
ENTITIES = [('organizations', {'organization_id': 'ASC-1', 'organization_type': 'asc', 'npi': '2222222222'}),
            ('organizations', {'organization_id': 'PH-1', 'organization_type': 'retail_pharmacy', 'npi': '3333333333'}),
            ('clinicians', {'clinician_id': 'CL-1', 'organization_id': 'PR-A', 'npi': '1111111111'})]


class SemanticPopulationTables(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = stack_fixture(RECORDS, PATIENTS, ENTITIES)
        semantic_layer.build(cls.path)

    def q(self, sql):
        return sql_query.execute(self.path, {'query': sql, 'returns': 'table'}, None)['rows']

    def test_enrollment_spans_become_member_months(self):
        months = [r['month'] for r in self.q("SELECT month FROM member_month WHERE patient_id='m1' ORDER BY month")]
        self.assertEqual(months[:6], ['2028-01', '2028-02', '2028-03', '2028-04', '2028-05', '2028-06'])
        self.assertNotIn('2028-07', months); self.assertNotIn('2028-08', months)  # terminated span, gap before re-enrollment
        self.assertEqual(months[6], '2028-09'); self.assertEqual(months[-1], AS_OF[:7])  # open span runs to the snapshot
        lob = {r['month']: r for r in self.q("SELECT month, line_of_business, product, plan_id FROM member_month WHERE patient_id='m1'")}
        self.assertEqual((lob['2028-02']['line_of_business'], lob['2028-02']['plan_id']), ('commercial', 'C1-COM'))
        self.assertEqual((lob['2028-10']['line_of_business'], lob['2028-10']['product']), ('medicare', 'medicare_advantage'))
        self.assertEqual(len(self.q("SELECT 1 FROM member_month WHERE patient_id='m2'")), 3)
        p = {r['patient_id']: r for r in self.q('SELECT patient_id, data_segment, line_of_business FROM patient')}
        self.assertEqual((p['m1']['data_segment'], p['m1']['line_of_business']), ('claims_only', 'medicare'))
        self.assertIsNone(p['d1']['line_of_business'])

    def test_payer_claims_follow_adjudication_sequence(self):
        c = {r['payer_claim_id']: r for r in self.q('SELECT * FROM payer_claim')}
        self.assertEqual((c['X1']['final_status'], c['X1']['allowed_cents'], c['X1']['paid_cents']), ('paid', 10000, 8000))
        self.assertEqual((c['X2']['first_status'], c['X2']['final_status'], c['X2']['adjudications']), ('paid', 'reversed', 2))
        self.assertEqual(self.q("SELECT cpt FROM payer_claim_line WHERE payer_claim_id='X1'")[0]['cpt'], '99213')
        rx = {r['rx_claim_id']: r for r in self.q('SELECT * FROM payer_pharmacy_claim')}
        self.assertEqual((rx['RX1']['plan_paid_cents'], rx['RX1']['bin'], rx['RX2']['claim_status']), (800, '990008', 'rejected'))
        meds = self.q("SELECT evidence, drug_class FROM medication WHERE record_id='rx1'")
        self.assertEqual(meds[0]['evidence'], 'payer_claim')

    def test_prescriptions_linked_to_fills(self):
        p = {r['record_id']: r for r in self.q('SELECT * FROM prescription')}
        self.assertEqual((p['e1']['fill_status'], p['e1']['fills_received'], p['e1']['declared_fill_ids'], p['e1']['prescribed_not_filled']),
                         ('filled', 1, 2, 0))  # the rejected claim is not a fill
        self.assertEqual((p['e2']['fill_status'], p['e2']['prescribed_not_filled']), ('not_filled', 1))
        self.assertEqual((p['e3']['fill_status'], p['e3']['prescribed_not_filled']), ('declared_unreceived', None))
        self.assertEqual((p['o1']['fill_status'], p['o1']['fill_basis'], p['o1']['first_fill_date']), ('filled', 'patient_drug_window_30d', '2028-05-03'))
        self.assertEqual((p['l1']['fill_status'], p['l1']['fill_basis'], p['l1']['fill_expected']), ('filled', 'medication_order_record_id', None))
        links = self.q("SELECT fill_record_id, basis FROM prescription_fill WHERE order_record_id='e1'")
        self.assertEqual(links, [{'fill_record_id': 'rx1', 'basis': 'linked_fill_event_id'}])

    def test_documents_notes_and_sdoh(self):
        h = self.q('SELECT * FROM hie_document')[0]
        self.assertEqual((h['loinc'], h['encounter_id'], h['text_chars']), ('34111-5', 'E9', 22))
        n = self.q("SELECT * FROM clinical_note WHERE record_id='n1'")[0]
        self.assertEqual((n['template_style'], n['author_credential'], n['note_status']), ('terse', 'NP', 'signed'))
        svi = {r['patient_id']: r for r in self.q('SELECT * FROM patient_svi')}
        self.assertEqual((svi['m1']['county_source'], svi['m1']['svi_overall']), ('patient_demographics', 82.6))
        self.assertEqual((svi['m2']['county_fips'], svi['m2']['county_source'], svi['m2']['svi_overall']), ('06037', 'vendor_match', 40.1))
        self.assertIsNone(svi['p1']['svi_overall'])  # county known, but the vendor file has no SVI for it
        self.assertEqual(self.q("SELECT food_access_low FROM sdoh_vendor WHERE record_id='v1'")[0]['food_access_low'], 1)

    def test_ahc_screens_map_into_social_need(self):
        rows = {r['need']: r['positive'] for r in self.q("SELECT need, positive FROM social_need WHERE instrument='AHC_HRSN'")}
        self.assertEqual(rows, {'food': 1, 'transport': 0, 'housing': 0, 'utilities': 1, 'safety': 0})

    def test_directory_facility_class_and_place_of_service(self):
        o = {r['organization_id']: r for r in self.q('SELECT * FROM organization')}
        self.assertEqual((o['ASC-1']['facility_class'], o['ASC-1']['place_of_service'], o['ASC-1']['place_of_service_claims']),
                         ('ambulatory_surgery_center', '24', 1))
        self.assertEqual((o['PH-1']['facility_class'], o['PH-1']['place_of_service']), ('pharmacy', None))

    def test_incident_diagnoses(self):
        d = self.q('SELECT * FROM incident_diagnosis')
        self.assertEqual(len(d), 1)
        self.assertEqual((d[0]['icd10'], d[0]['diagnosis_date'], d[0]['diagnosing_encounter_id'], d[0]['encounter_icd10_match'],
                          d[0]['problem_list_note_record_id'], d[0]['first_prescription_record_id']),
                         ('I10', '2028-05-01', 'ENC1', 1, 'plu', 'o1'))
        basis = {r['record_id']: r['diagnosis_basis'] for r in self.q("SELECT record_id, diagnosis_basis FROM condition WHERE source_table='conditions'")}
        self.assertEqual((basis['c1'], basis['c0']), ('clinical_encounter', None))

    def test_meta_and_planner_description(self):
        db = sqlite3.connect(self.path / 'semantic.sqlite3')
        try: meta = dict(db.execute('SELECT key, value FROM meta'))
        finally: db.close()
        self.assertEqual(meta['version'], semantic_layer.VERSION)
        feeds = json.loads(meta['population_feeds'])
        self.assertEqual(feeds['enrollment']['spans'], 4); self.assertEqual(feeds['incident']['incident_diagnoses'], 1)
        described = semantic_layer.describe(self.path)['tables']
        for t in ('member_month', 'prescription', 'patient_svi', 'incident_diagnosis', 'payer_claim'):
            self.assertTrue(described[t]['meaning'], t)


class MetricLayerFindings(unittest.TestCase):
    """Regressions for the metric-layer review: local discharge dates, unnormalised GFR, exact drug classes, primary-care types."""
    @classmethod
    def setUpClass(cls):
        recs = [('h1', 'p1', 'encounters', {'type': 'inpatient', 'encounter_id': 'H9', 'event_date': '2028-03-01',
                                            'end_time': '2028-03-05T02:00:00+00:00'}),
                ('h1a', 'p1', 'adt', {'event': 'discharge', 'encounter_id': 'H9', 'event_date': '2028-03-04'}),
                # local gap 2028-03-04 -> 2028-04-04 = 31 days (no readmission); the UTC date would give 30
                ('h2', 'p1', 'encounters', {'type': 'inpatient', 'encounter_id': 'H10', 'event_date': '2028-04-04',
                                            'end_time': '2028-04-06T15:00:00+00:00'}),
                ('g1', 'p1', 'labs', {'test': 'Glomerular filtration rate', 'value': 55, 'unit': 'mL/min'}),
                ('g2', 'p1', 'labs', {'test': 'eGFR', 'value': 60, 'unit': 'mL/min/1.73m2', 'event_date': '2028-02-01'}),
                ('m1', 'p1', 'medication_lists', {'name': 'diphenhydramine 25 mg capsule', 'event_date': '2028-01-01'}),
                ('m2', 'p1', 'medication_lists', {'name': 'loratadine 10 mg tablet', 'event_date': '2028-01-01'}),
                ('m3', 'p1', 'medication_lists', {'name': 'albuterol 90 mcg inhaler', 'event_date': '2028-01-01'}),
                ('m4', 'p1', 'medication_lists', {'name': 'metoprolol tartrate 25 mg', 'event_date': '2028-01-01'}),
                ('y1', 'p1', 'encounters', {'type': 'youth_health_supervision', 'encounter_id': 'Y1', 'event_date': '2028-05-01'}),
                ('y2', 'p1', 'encounters', {'type': 'Adolescent med', 'encounter_id': 'Y2', 'event_date': '2028-06-01'})]
        cls.path = stack_fixture(recs, {'p1': {'age_reported': 15, 'sex_reported': 'F'}})
        semantic_layer.build(cls.path)

    def q(self, sql):
        return sql_query.execute(self.path, {'query': sql, 'returns': 'table'}, None)['rows']

    def test_stay_discharge_uses_adt_local_date(self):
        stays = self.q('SELECT * FROM stay ORDER BY admit_date')
        self.assertEqual((stays[0]['discharge_date'], stays[0]['discharge_date_source'], stays[0]['discharge_time_utc']),
                         ('2028-03-04', 'adt', '2028-03-05T02:00:00+00:00'))
        self.assertEqual((stays[0]['readmitted_30d'], stays[0]['los_days']), (0, 3))
        self.assertEqual((stays[1]['discharge_date'], stays[1]['discharge_date_source']), ('2028-04-06', 'encounter_end_time_utc'))

    def test_unnormalised_gfr_is_not_egfr(self):
        rows = {r['record_id']: r for r in self.q("SELECT record_id, concept, loinc, value_canonical FROM measurement WHERE source_table='labs'")}
        self.assertEqual(rows['g1']['concept'], 'gfr_unnormalized'); self.assertIsNone(rows['g1']['value_canonical'])
        self.assertNotEqual(rows['g1']['loinc'], '33914-3')
        self.assertEqual((rows['g2']['concept'], rows['g2']['value_canonical']), ('egfr', 60))
        self.assertEqual([r['record_id'] for r in self.q("SELECT record_id FROM measurement_latest WHERE concept='egfr'")], ['g2'])

    def test_exact_drug_classes_and_vocabulary(self):
        cls = {r['record_id']: r['drug_class'] for r in self.q('SELECT record_id, drug_class FROM medication')}
        self.assertEqual(cls, {'m1': 'first_generation_antihistamine', 'm2': 'second_generation_antihistamine',
                               'm3': 'short_acting_beta_agonist', 'm4': 'beta_blocker'})
        beers = {r['drug_class'] for r in self.q("SELECT drug_class FROM drug_class_vocabulary WHERE class_group='beers_high_risk'")}
        self.assertIn('first_generation_antihistamine', beers); self.assertNotIn('second_generation_antihistamine', beers)
        self.assertIn('never LIKE', semantic_layer.describe(self.path)['tables']['medication']['meaning'])

    def test_youth_and_adolescent_visits_are_primary_care(self):
        rows = {r['encounter_id']: r['type_concept'] for r in self.q("SELECT encounter_id, type_concept FROM visit WHERE encounter_id IN ('Y1','Y2')")}
        self.assertEqual(rows, {'Y1': 'primary_care_visit', 'Y2': 'primary_care_visit'})


class SpanMemberMonthsInVBC(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = stack_fixture(RECORDS, PATIENTS, ENTITIES)
        semantic_layer.build(cls.path)

    def run_metric(self, period=('2028-01-01', '2028-12-31'), **p):
        return vbc.execute(self.path, None, {'period': {'start': period[0], 'end': period[1]}, **p}, AS_OF)

    def test_member_months_prefer_spans_and_fall_back_to_coverage(self):
        r = self.run_metric(metric='eligibility', group_by='coverage_type')
        rows = {x['group']: x for x in r['rows']}
        # m1: Jan-Jun + Sep-Dec (span gap and termination respected) -> 10, grouped by its span line of business;
        # p1: span from 2028-01 -> 12 (coverage record ignored); p2: no span, coverage record from 2028-07 -> 6; m2 enrols in 2029.
        self.assertEqual(rows['medicare'], {'group': 'medicare', 'members': 2, 'member_months': 22})
        self.assertEqual(rows['commercial'], {'group': 'commercial', 'members': 1, 'member_months': 6})
        self.assertIn('enrollment spans', r['method'])
        r2 = self.run_metric(period=('2029-01-01', '2029-06-30'), metric='eligibility')
        self.assertEqual(r2['rows'][0]['member_months'], 3 + 6 + 6 + 6)  # m2 Jan-Mar only

    def test_payer_attribution_from_spans(self):
        early = vbc.attribution(*self._db_people('2028-01-01', '2028-06-30'))
        self.assertEqual((early['m1']['practice'], early['m1']['pcp'], early['m1']['method']), ('PR-A', 'CL-1', 'payer_attribution'))
        late = vbc.attribution(*self._db_people('2028-01-01', '2028-12-31'))
        self.assertEqual((late['m1']['practice'], late['m1']['pcp']), ('PR-B', 'unattributed'))
        self.assertEqual((late['p1']['pcp'], late['p1']['method']), ('drA', 'assigned_pcp'))  # no payer attribution: unchanged
        r = self.run_metric(metric='attribution', group_by='practice')
        self.assertEqual({x['group']: x.get('payer_attribution', 0) for x in r['rows']}['PR-B'], 1)

    def _db_people(self, start, end):
        db = sqlite3.connect(f'file:{self.path / "semantic.sqlite3"}?mode=ro', uri=True)
        self.addCleanup(db.close)
        return db, vbc.eligibility(db, AS_OF, start, end), start, end

    def test_cost_uses_payer_feed_for_its_members(self):
        row = self.run_metric(metric='cost')['rows'][0]
        self.assertEqual(row['member_months'], 28)
        # X1 paid (100.00) counts; X2 ends reversed; X3 denied; m1's provider-side claim is superseded by the payer feed.
        self.assertAlmostEqual(row['allowed_pmpm'], round(100 / 28, 2))
        self.assertAlmostEqual(row['paid_pmpm'], round(40 / 28, 2))  # Mike 2026-10-25: sum the paid amount on every counted claim, including X2's final -$40.


def migration_fixture():
    connection = {'records': 120, 'patients': 15, 'snapshot': {'as_of': '2030-05-27', 'source_dataset': {
        'original_patient_id_digest': 'new-roster', 'source_sha256': 'sha'}}}
    audit = {'kind': 'expansion', 'new_received_rows': 120, 'patient_id_digest': 'new-roster', 'manifest_source_sha256': 'sha',
             'prior_patients': 10, 'new_patients': 5, 'patients': 15, 'prior_patients_missing': 0, 'prior_rows_missing': 0,
             'prior_rows_changed': 0, 'changed_by_table': {}, 'prior_patient_id_digest': 'old-roster'}
    prior = {'snapshot': {'as_of': '2030-05-27', 'source_dataset': {'original_patient_id_digest': 'old-roster'}}}
    return audit, connection, prior


class ExpansionGate(unittest.TestCase):
    def test_expansion_accepts_superset_roster_with_counted_new_patients(self):
        self.assertEqual(validate_migration(*migration_fixture(), expansion=True), 'expanded')

    def test_expansion_needs_explicit_flag_and_expansion_audit(self):
        audit, connection, prior = migration_fixture()
        with self.assertRaisesRegex(ValueError, 'original-cohort migration'): validate_migration(audit, connection, prior)
        with self.assertRaisesRegex(ValueError, 'expansion migration audit'):
            validate_migration({**audit, 'kind': 'restatement'}, connection, prior, expansion=True)

    def test_dropped_patients_or_rows_rejected(self):
        audit, connection, prior = migration_fixture()
        for key, value, message in [('prior_patients_missing', 1, 'drop prior patients'), ('prior_rows_missing', 3, 'drop prior rows'),
                                    ('new_patients', 4, 'reconcile'), ('patients', 16, 'reconcile'), ('new_patients', None, 'count')]:
            with self.assertRaisesRegex(ValueError, message, msg=key): validate_migration({**audit, key: value}, connection, prior, expansion=True)

    def test_changed_prior_rows_need_restatement_and_per_table_counts(self):
        audit, connection, prior = migration_fixture()
        changed = {**audit, 'prior_rows_changed': 7, 'changed_by_table': {'labs': 5, 'medical_claims': 2}}
        with self.assertRaisesRegex(ValueError, '--restatement'): validate_migration(changed, connection, prior, expansion=True)
        self.assertEqual(validate_migration(changed, connection, prior, expansion=True, restatement=True), 'expanded_restated')
        with self.assertRaisesRegex(ValueError, 'per table'):
            validate_migration({**changed, 'changed_by_table': {'labs': 5}}, connection, prior, expansion=True, restatement=True)

    def test_audit_must_be_against_the_active_roster_and_cutoff(self):
        audit, connection, prior = migration_fixture()
        with self.assertRaisesRegex(ValueError, 'active roster'):
            validate_migration({**audit, 'prior_patient_id_digest': 'other'}, connection, prior, expansion=True)
        later = copy.deepcopy(prior); later['snapshot']['as_of'] = '2031-01-01'
        with self.assertRaisesRegex(ValueError, 'roll back'): validate_migration(audit, connection, later, expansion=True)
        self.assertEqual(validate_migration(audit, connection, None, expansion=True), 'expanded')  # first activation

    def test_identical_and_restated_paths_unchanged(self):
        connection = {'records': 100, 'patients': 10, 'snapshot': {'as_of': '2030-05-27', 'source_dataset': {
            'original_patient_id_digest': 'r', 'source_sha256': 'sha'}}}
        prior = {'snapshot': {'as_of': '2029-01-01', 'source_dataset': {'original_patient_id_digest': 'r'}}}
        same = {'identical_roster': True, 'prior_rows_missing_or_changed': 0, 'new_received_rows': 100, 'patient_id_digest': 'r',
                'manifest_source_sha256': 'sha'}
        self.assertEqual(validate_migration(same, connection, prior), 'identical')
        restated = {**same, 'kind': 'restatement', 'prior_rows_missing': 0, 'prior_rows_changed': 4, 'changed_by_table': {'labs': 4},
                    'prior_rows_missing_or_changed': 4}
        with self.assertRaises(ValueError): validate_migration(restated, connection, prior)
        self.assertEqual(validate_migration(restated, connection, prior, restatement=True), 'restated')
        with self.assertRaisesRegex(ValueError, 'roster'):
            validate_migration(same, connection, {'snapshot': {'as_of': '2029-01-01', 'source_dataset': {'original_patient_id_digest': 'x'}}})


class ExpansionAuditTool(unittest.TestCase):
    def test_audit_counts_new_patients_and_changed_prior_rows(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent / 'tools'))
        from restatement_audit import audit
        d = Path(tempfile.mkdtemp()); old, new = d / 'old', d / 'new'; old.mkdir(); new.mkdir()
        release(old, 'healthcare-warehouse/2', ROWS)
        expanded_release(new)
        db = sqlite3.connect(new / 'warehouse.sqlite')  # restate one prior row
        db.execute("UPDATE source_records SET facts=json_set(facts,'$.value',42) WHERE record_id='lab1'"); db.commit(); db.close()
        build = d / 'build'; build.mkdir()
        (build / 'connection.json').write_text(json.dumps({'release': 'new', 'records': 5, 'snapshot': {'as_of': '2030-05-27', 'source_dataset': {
            'source': str(new / 'warehouse.sqlite'), 'original_patient_id_digest': 'n', 'source_sha256': 's'}}}))
        a = audit(build, old, expansion=True)
        self.assertEqual((a['kind'], a['identical_roster'], a['prior_patients'], a['new_patients'], a['prior_patients_missing']),
                         ('expansion', False, 1, 2, 0))
        self.assertEqual((a['prior_rows_missing'], a['prior_rows_changed'], a['changed_by_table']), (0, 1, {'labs': 1}))
        self.assertEqual(a['prior_patient_id_digest'], digest(['P-original']))
        self.assertEqual(a['rows_for_new_patients'], 3)


if __name__ == '__main__':
    unittest.main()
