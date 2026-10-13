"""Standards-coded warehouse releases (healthcare-warehouse/2): adapter dispatch, literal record projection and
semantic-layer standard-code columns that sit beside the existing local values."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from clinical_core.agere import healthcare_warehouse as v1
from clinical_core.agere import healthcare_warehouse_v2 as v2
from clinical_core.agere.warehouse import Warehouse
from clinical_core.agere.clinical import semantic_layer, sql_query
from test_aggregate_explain import build, visit
from test_healthcare_warehouse import source


def release(root, schema, rows, patient_cols=True):
    db = sqlite3.connect(root / 'warehouse.sqlite')
    extra = ',birth_date TEXT,race TEXT,ethnicity TEXT,preferred_language TEXT,interpreter_needed INTEGER,postal_code TEXT,county_fips TEXT' if patient_cols else ''
    db.executescript('CREATE TABLE patients(patient_id TEXT PRIMARY KEY,display_name TEXT,age_reported INTEGER,sex_reported TEXT,'
                     'profile_captured_date TEXT' + extra + ');'
                     'CREATE TABLE organizations(organization_id TEXT, npi TEXT, tin TEXT);'
                     'CREATE TABLE clinicians(clinician_id TEXT, npi TEXT, taxonomy_code TEXT);'
                     'CREATE TABLE data_dictionary(object_name TEXT, column_name TEXT, data_type TEXT, source_table TEXT, json_path TEXT,'
                     ' description TEXT, code_system TEXT, populated_share REAL);')
    if patient_cols:
        db.execute("INSERT INTO patients VALUES ('P-original','Synthetic',42,'F','2026-08-28','1984-02-01','Asian','Not Hispanic or Latino','Portuguese',1,'02139','25017')")
    else:
        db.execute("INSERT INTO patients VALUES ('P-original','Synthetic',42,'F','2026-08-28')")
    db.execute("INSERT INTO organizations VALUES ('ORG','1234567893','07-0000001')")
    db.execute("INSERT INTO clinicians VALUES ('CL','1245319599','207Q00000X')")
    db.execute("INSERT INTO data_dictionary VALUES ('v_lab_results','loinc','TEXT','labs','$.loinc','', 'LOINC', 0.9)")
    keys = list(rows[0])
    db.execute('CREATE TABLE source_records(' + ','.join(k + (' INTEGER' if k == 'synthetic' else ' TEXT') for k in keys) + ')')
    db.executemany('INSERT INTO source_records VALUES (' + ','.join('?' for _ in keys) + ')', [[r[k] for k in keys] for r in rows])
    db.commit(); db.close()
    manifest = {'schema': schema, 'as_of': '2030-05-27', 'files': {'warehouse.sqlite': v1.checksum(root / 'warehouse.sqlite')},
                'metrics': {'original_patient_id_set_preserved': True, 'new_patients_added': 0}}
    (root / 'manifest.json').write_text(json.dumps(manifest))
    return manifest


ROWS = [source(record_id='lab1', source_table='labs', event_date='2030-01-02',
               facts=json.dumps({'name': 'HDL', 'value': 41, 'unit': 'mg/dL', 'loinc': '2085-9', 'abnormal_flag': 'L',
                                 'reference_range': '>=40'})),
        source(record_id='line1', source_table='medical_claim_lines', event_date='2030-01-02',
               facts=json.dumps({'claim_id': 'C1', 'service_code_system': 'CPT', 'service_code': '99214',
                                 'local_charge_code': 'professional_evaluation'}))]


class AdapterV2(unittest.TestCase):
    def test_dispatch_keeps_v1_releases_on_adapter_1(self):
        self.assertEqual(v2.adapter_for({'schema': 'single-healthcare-warehouse/1'}), v1.VERSION)
        self.assertEqual(v2.adapter_for({'schema': 'healthcare-warehouse/2'}), v2.VERSION)
        with self.assertRaises(ValueError): v2.adapter_for({'schema': 'something-else'})

    def test_v2_release_projects_literal_records_and_demographics(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); release(root, 'healthcare-warehouse/2', ROWS)
            (root / 'private').mkdir(); (root / 'private' / 'truth.json').write_text('NOT JSON: never opened')
            w = Warehouse(root / 'derived.sqlite3')
            try:
                rel, receipt = v2.ingest(w, root)
                self.assertEqual(receipt['adapter'], v2.VERSION); self.assertEqual(receipt['record_projection'], v1.VERSION)
                self.assertFalse(receipt['private_files_read'])
                self.assertEqual(receipt['records_by_code_system'], {'LOINC': 1, 'CPT': 1})
                self.assertEqual(receipt['data_dictionary']['coded_columns'], ['v_lab_results.loinc=LOINC'])
                patients, snapshot = w.snapshot(rel, '2030-05-27')
                self.assertEqual(snapshot['source_dataset']['adapter'], v2.VERSION)
                p = patients['P-original']
                self.assertEqual((p['patient']['birth_date'], p['patient']['preferred_language'], p['patient']['interpreter_needed']),
                                 ('1984-02-01', 'Portuguese', 1))
                lab = next(r for r in p['records'] if r['record_id'] == 'lab1')
                # Byte-identical to adapter/1 projection: downstream record contracts are unchanged.
                db = sqlite3.connect(root / 'warehouse.sqlite'); db.row_factory = sqlite3.Row
                try: self.assertEqual(lab, v1.project(db.execute("SELECT * FROM source_records WHERE record_id='lab1'").fetchone()))
                finally: db.close()
                self.assertEqual(lab['loinc'], '2085-9'); self.assertEqual(lab['test'], 'HDL')
                self.assertTrue(v2.ingest(w, root)[1]['cache_hit'])
            finally: w.close()

    def test_v2_requires_preserved_roster(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); m = release(root, 'healthcare-warehouse/2', ROWS)
            m['metrics']['original_patient_id_set_preserved'] = False; (root / 'manifest.json').write_text(json.dumps(m))
            w = Warehouse(root / 'derived.sqlite3')
            try:
                with self.assertRaisesRegex(ValueError, 'roster'): v2.ingest(w, root)
            finally: w.close()


class SemanticStandards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        patients = {'p1': {'age_reported': 70, 'sex_reported': 'F', 'birth_date': '1956-01-01', 'race': 'Black or African American',
                           'ethnicity': 'Not Hispanic or Latino', 'preferred_language': 'Haitian Creole', 'interpreter_needed': 1,
                           'postal_code': '02121', 'county_fips': '25025', 'state': 'MA'},
                    'p2': {'age_reported': 40, 'sex_reported': 'M'}}  # v1-style patient: new columns stay NULL
        recs = [('p1', 'conditions', {'description': 'T2DM', 'icd10': 'E11.9'}),
                ('p2', 'conditions', {'description': 'Diabetes mellitus type 2', 'snomed': '44054006'}),
                ('p1', 'labs', {'test': 'HbA1c', 'name': 'HbA1c', 'value': 9.4, 'unit': '%', 'event_date': '2028-01-01', 'loinc': '4548-4',
                                'reference_range': '4.0-5.6', 'abnormal_flag': 'H'}),
                ('p2', 'labs', {'test': 'HbA1c', 'value': 6.1, 'unit': '%', 'event_date': '2028-01-01'}),  # local-only: dictionary fallback
                ('p1', 'medication_orders', {'medication_name': 'Empagliflozin', 'rxcui': '1545653', 'event_date': '2028-01-01'}),
                ('p2', 'pharmacy_dispenses', {'medication_name': 'Metformin', 'ndc': '00093-1048-01', 'rxcui': '861007', 'event_date': '2028-01-01'}),
                ('p1', 'encounters', {'type': 'inpatient', 'encounter_id': 'E1', 'event_date': '2028-02-01', 'place_of_service': '21',
                                      'diagnoses': [{'rank': 1, 'icd10': 'I50.22', 'type': 'principal'}, {'rank': 2, 'icd10': 'E11.9', 'type': 'secondary'}]}),
                visit('p2', 'ED', '2028-03-01'),
                ('p1', 'medical_claims', {'claim_id': 'C1', 'status': 'paid', 'service_date': '2028-02-01', 'payer_paid_cents': 100,
                                          'ms_drg': '292', 'principal_diagnosis': 'I50.22', 'diagnosis_codes': ['I50.22', 'E11.9'],
                                          'billing_provider_npi': '1234567893', 'type_of_bill': '0111', 'event_date': '2028-02-05'}),
                ('p2', 'medical_claims', {'claim_id': 'C2', 'status': 'paid', 'service_date': '2028-03-01', 'service_code_system': 'synthetic_local',
                                          'service_code': 'professional_evaluation', 'event_date': '2028-03-05'}),
                ('p1', 'medical_claim_lines', {'claim_id': 'C1', 'line_number': 1, 'service_code_system': 'CPT', 'service_code': '99223',
                                               'local_charge_code': 'professional_evaluation', 'units': 1, 'charge_cents': 30000}),
                ('p2', 'medical_claim_lines', {'claim_id': 'C2', 'line_number': 1, 'service_code_system': 'synthetic_local',
                                               'service_code': 'professional_evaluation'}),
                ('p1', 'immunizations', {'vaccine': 'influenza', 'cvx_code': '150', 'administration_status': 'completed', 'event_date': '2028-10-01'})]
        cls.path = build(recs, patients)
        semantic_layer.build(cls.path)

    def q(self, sql):
        return sql_query.execute(self.path, {'query': sql, 'returns': 'table'}, None)['rows']

    def test_patient_demographics_beside_reported_fields(self):
        rows = {r['patient_id']: r for r in self.q('SELECT * FROM patient')}
        self.assertEqual(rows['p1']['age_reported'], 70)
        self.assertEqual((rows['p1']['preferred_language'], rows['p1']['interpreter_needed'], rows['p1']['zip'], rows['p1']['county']),
                         ('Haitian Creole', 1, '02121', '25025'))
        self.assertIsNone(rows['p2']['birth_date'])

    def test_source_codes_win_and_provenance_is_recorded(self):
        labs = {r['patient_id']: r for r in self.q("SELECT patient_id, concept, loinc, loinc_source, abnormal_flag FROM measurement WHERE source_table='labs'")}
        self.assertEqual((labs['p1']['loinc'], labs['p1']['loinc_source'], labs['p1']['abnormal_flag']), ('4548-4', 'source', 'H'))
        self.assertEqual(labs['p1']['concept'], labs['p2']['concept'])
        conds = {r['patient_id']: r for r in self.q('SELECT patient_id, icd10, icd10_source FROM condition')}
        self.assertEqual((conds['p1']['icd10'], conds['p1']['icd10_source']), ('E11.9', 'source'))
        self.assertIsNone(conds['p2']['icd10_source'])
        meds = {r['patient_id']: r for r in self.q('SELECT patient_id, rxnorm, rxnorm_source, ndc, drug_class FROM medication')}
        self.assertEqual((meds['p1']['rxnorm'], meds['p1']['rxnorm_source']), ('1545653', 'source'))
        self.assertEqual(meds['p2']['ndc'], '00093-1048-01')

    def test_claims_lines_encounters_and_immunizations(self):
        claims = {r['claim_id']: r for r in self.q('SELECT * FROM claim')}
        self.assertEqual((claims['C1']['drg'], claims['C1']['principal_diagnosis'], claims['C1']['billing_npi']), ('292', 'I50.22', '1234567893'))
        self.assertEqual(json.loads(claims['C1']['diagnosis_codes']), ['I50.22', 'E11.9'])
        self.assertEqual(claims['C2']['local_charge_code'], 'professional_evaluation'); self.assertIsNone(claims['C2']['cpt'])
        lines = {r['claim_id']: r for r in self.q('SELECT * FROM claim_line')}
        self.assertEqual((lines['C1']['cpt'], lines['C1']['local_charge_code']), ('99223', 'professional_evaluation'))
        self.assertEqual((lines['C2']['cpt'], lines['C2']['local_charge_code']), (None, 'professional_evaluation'))
        dx = self.q("SELECT icd10, type FROM encounter_diagnosis WHERE encounter_id='E1' ORDER BY rank")
        self.assertEqual([d['icd10'] for d in dx], ['I50.22', 'E11.9'])
        v = self.q("SELECT principal_icd10, place_of_service FROM visit WHERE encounter_id='E1'")[0]
        self.assertEqual((v['principal_icd10'], v['place_of_service']), ('I50.22', '21'))
        self.assertEqual(self.q('SELECT cvx FROM immunization')[0]['cvx'], '150')

    def test_meta_records_code_coverage(self):
        db = sqlite3.connect(self.path / 'semantic.sqlite3')
        try: meta = dict(db.execute('SELECT key, value FROM meta'))
        finally: db.close()
        self.assertEqual(meta['version'], semantic_layer.VERSION)
        s = json.loads(meta['standards'])
        self.assertEqual(s['conditions_with_source_icd10'], [1, 2]); self.assertEqual(s['claim_lines_cpt_or_hcpcs'], [1, 2])


if __name__ == '__main__':
    unittest.main()
