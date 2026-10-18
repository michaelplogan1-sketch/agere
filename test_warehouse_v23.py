"""Client-realism warehouse releases (v2.3): adapter/4 (outside-PDF text layer, manifest count check) and the semantic-layer/15
client-data rules, one fixture per source-system quirk: lab strings/corrections/duplicates/local codes, claim versions and
CARC 18 duplicates, undotted ICD-10, missing rendering NPIs, 9999-12-31 spans and reissued member IDs, B2 pharmacy
reversals (fills and PDC), fuzzy order-to-fill linkage, ADT gaps and all-source utilization for claims-only members,
problem/medication/allergy list rot, smoking status, note-only therapy changes and dictation, and outside documents."""
import json
import sqlite3
import tempfile
import unittest
import zlib
from pathlib import Path

from build_healthcare_warehouse_stack import ingest as build_ingest
from clinical_core.agere import healthcare_warehouse as v1, healthcare_warehouse_v2 as v2, healthcare_warehouse_v3 as v3
from clinical_core.agere import healthcare_warehouse_v4 as v4, outside_documents
from clinical_core.agere.warehouse import Warehouse
from clinical_core.agere.clinical import client_data, measures, metrics, semantic_layer, sql_query, vbc
from test_aggregate_explain import AS_OF
from test_healthcare_warehouse import source
from test_warehouse_v22 import expanded_release, stack_fixture

RELEASES = Path('/Users/mikel/synthetic-patient-ecosystem/releases')


def pdf(lines=(), image=False, literal=None):
    """A minimal PDF: one page whose FlateDecode content stream shows `lines` (hex strings in TJ) or paints an image."""
    if image: content = b'q 612 0 0 792 0 0 cm /Im0 Do Q'
    elif literal: content = b'BT /F1 9 Tf (' + literal + b') Tj ET'
    else: content = b''.join(b'BT 1 0 0 1 48 %d Tm /F1 9 Tf [<%s>]TJ ET\n' % (700 - 12 * i, l.encode('cp1252').hex().encode())
                             for i, l in enumerate(lines))
    body = zlib.compress(content)
    parts = [b'1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj', b'2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj',
             b'3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Resources<</XObject<</Im0 5 0 R>>>>/Contents 4 0 R>>endobj',
             b'4 0 obj<</Length %d/Filter/FlateDecode>>stream\n' % len(body) + body + b'\nendstream\nendobj',
             b'5 0 obj<</Type/XObject/Subtype/Image/Width 1/Height 1/ColorSpace/DeviceGray/BitsPerComponent 8/Length 1>>stream\n\x00\nendstream\nendobj']
    return b'%PDF-1.7\n' + b'\n'.join(parts) + b'\n%%EOF\n'


class OutsideDocumentText(unittest.TestCase):
    def test_text_layer_lines_and_date_of_service(self):
        got = outside_documents.read(pdf(['ECHOCARDIOGRAM REPORT', 'Date of service: 08/27/2027    Provider: A. Doe, MD',
                                          'IMPRESSION: LVEF 35% by biplane Simpson.']))
        self.assertEqual(got['reason'], None)
        self.assertEqual(got['text'].splitlines()[0], 'ECHOCARDIOGRAM REPORT')
        self.assertEqual(outside_documents.date_of_service(got['text']), '2027-08-27')
        self.assertIsNone(outside_documents.date_of_service('Date of service: 10/21/226'))  # OCR-damaged year: not repaired

    def test_image_only_scan_is_present_but_unreadable(self):
        got = outside_documents.read(pdf(image=True))
        self.assertEqual((got['text'], got['image_only'], got['reason']), (None, True, 'image_only_no_text_layer'))
        self.assertEqual(outside_documents.read(b'not a pdf')['reason'], 'not_a_pdf')

    def test_literal_strings_with_escapes(self):
        self.assertEqual(outside_documents.read(pdf(literal=rb'BP 120\/80 \(seated\)'))['text'], 'BP 120/80 (seated)')


def v23_release(root, *, broken=False):
    """v2.2-shaped fixture release turned v2.3: outside PDFs (text layer, image-only, shared, missing) and a v2.3 manifest."""
    expanded_release(root)
    docs = root / 'documents' / 'outside'; docs.mkdir(parents=True)
    (docs / 'DOC-TEXT.pdf').write_bytes(pdf(['CONSULT LETTER', 'Date of service: 03/01/2028', 'Stop atorvastatin 20 mg due to muscle aches.']))
    (docs / 'DOC-IMAGE.pdf').write_bytes(pdf(image=True))
    rows = [('sd1', 'DOC-TEXT', True, None, '2028-03-02'), ('sd2', 'DOC-IMAGE', False, None, '2028-04-01'),
            ('sd3', 'DOC-IMAGE', False, 'IMPRESS1ON: n0 acute process.', '2028-05-01'), ('sd4', 'DOC-GONE', True, None, '2028-06-01')]
    db = sqlite3.connect(root / 'warehouse.sqlite')
    keys = [r[1] for r in db.execute('PRAGMA table_info(source_records)')]
    for rid, doc, layer, ocr, day in rows:
        r = source(record_id=rid, patient_id='M-claims', source_table='scanned_documents', event_date=day,
                   facts=json.dumps({'document_id': doc, 'document_type': 'Outside Records', 'relative_path': f'documents/outside/{doc}.pdf',
                                     'has_text_layer': layer, 'ocr_text': ocr, 'page_count': 1, 'review_status': 'unreviewed'}))
        db.execute('INSERT INTO source_records VALUES (' + ','.join('?' * len(keys)) + ')', [r[k] for k in keys])
    db.commit()
    counts = dict(db.execute('SELECT source_table, count(*) FROM source_records GROUP BY 1'))
    db.close()
    m = json.loads((root / 'manifest.json').read_text())
    m['engine'] = 'healthcare-warehouse-v2.3-population/1'
    m['files'] = {'warehouse.sqlite': v1.checksum(root / 'warehouse.sqlite'),
                  'documents/outside/DOC-TEXT.pdf': v1.checksum(docs / 'DOC-TEXT.pdf'),
                  'documents/outside/DOC-IMAGE.pdf': 'f' * 64 if broken else v1.checksum(docs / 'DOC-IMAGE.pdf'),
                  'private/eval_outside_documents.jsonl': 'never-read'}
    m['metrics'].update(patients=3, source_records=sum(counts.values()) + (1 if broken else 0), source_tables=counts)
    (root / 'manifest.json').write_text(json.dumps(m))
    (root / 'private' / 'eval_outside_documents.jsonl').write_text('NOT JSON: never opened')
    return m


class AdapterV4(unittest.TestCase):
    def ingest(self, **kw):
        d = Path(tempfile.mkdtemp()); v23_release(d, **kw)
        w = Warehouse(d / 'derived.sqlite3')
        try:
            rel, receipt = build_ingest(w, d)
            docs = {r[0]: r[1:] for r in w.db.execute('SELECT record_id, status, reason, text, pdf_date_of_service, records_sharing_file '
                                                      'FROM source_document_text WHERE release = ?', (rel,))}
            patients, snapshot = w.snapshot(rel, '2030-05-27')
        finally: w.close()
        return receipt, docs, patients, d

    def test_dispatch(self):
        pop = {'schema': 'healthcare-warehouse/2', 'metrics': {'population_v21': {}}}
        self.assertEqual(v4.adapter_for({**pop, 'engine': 'healthcare-warehouse-v2.2-population/1'}), v3.VERSION)
        self.assertEqual(v4.adapter_for({**pop, 'engine': 'healthcare-warehouse-v2.3-population/1'}), v4.VERSION)
        self.assertEqual(v4.adapter_for({**pop, 'files': {'documents/outside/X.pdf': 'x'}}), v4.VERSION)
        self.assertEqual(v4.adapter_for({'schema': 'healthcare-warehouse/2', 'engine': 'healthcare-warehouse-v2-standards/1', 'metrics': {}}), v2.VERSION)
        self.assertEqual(v4.adapter_for({'schema': 'single-healthcare-warehouse/1'}), v1.VERSION)

    @unittest.skipUnless((RELEASES / 'healthcare-warehouse-v2.3' / 'manifest.json').exists(), 'releases not present')
    def test_real_release_manifests_pick_their_adapter(self):
        want = {'healthcare-warehouse-v1.23': v1.VERSION, 'healthcare-warehouse-v2.0': v2.VERSION, 'healthcare-warehouse-v2.2': v3.VERSION,
                'healthcare-warehouse-v2.3': v4.VERSION}
        for name, adapter in want.items():
            self.assertEqual(v4.adapter_for(json.loads((RELEASES / name / 'manifest.json').read_text())), adapter, name)

    def test_outside_pdfs_read_by_text_layer_never_private(self):
        receipt, docs, patients, d = self.ingest()
        self.assertEqual(receipt['adapter'], v4.VERSION)
        self.assertEqual(docs['sd1'][0], 'text_layer'); self.assertIn('Stop atorvastatin', docs['sd1'][2])
        self.assertEqual(docs['sd1'][3], '2028-03-01')
        self.assertEqual((docs['sd2'][0], docs['sd2'][4]), ('image_only', 2))  # one file named by two records
        self.assertEqual((docs['sd4'][0], docs['sd4'][1]), ('missing', 'file_not_in_release'))
        self.assertEqual(receipt['outside_documents']['status'], {'text_layer': 1, 'image_only': 2, 'missing': 1})
        self.assertFalse(receipt['private_files_read']); self.assertEqual(receipt['evaluation_tables_present_not_read'], ['eval_mpi_answer_key'])
        self.assertTrue(receipt['manifest_counts_consistent'])
        records = {r['record_id']: r for p in patients.values() for r in p['records']}
        self.assertEqual(records['sd3']['ocr_text'], 'IMPRESS1ON: n0 acute process.')  # OCR text stays in the record, unchanged
        db = sqlite3.connect(d / 'warehouse.sqlite'); db.row_factory = sqlite3.Row
        try: raw = {r['record_id']: v1.project(r) for r in db.execute('SELECT * FROM source_records')}
        finally: db.close()
        self.assertEqual(records, raw)  # adapter/1 record contract

    def test_checksum_mismatch_and_count_disagreement_recorded(self):
        receipt, docs, _, _ = self.ingest(broken=True)
        self.assertEqual(docs['sd2'][0], 'checksum_mismatch')
        self.assertFalse(receipt['manifest_counts_consistent'])
        self.assertIn('source_records', receipt['manifest_count_mismatches'])


# --- semantic layer fixtures ---------------------------------------------------------------------------------------
def lab(rid, pid, name, value, unit, day, **kw):
    return (rid, pid, 'labs', {'name': name, 'test': name, 'value': value, 'unit': unit, 'event_date': day, 'available_date': kw.pop('av', day),
                               'result_status': kw.pop('status', 'final'), **kw})


def claim(rid, pid, cid, status, seq, day, allowed, **kw):
    return (rid, pid, 'payer_medical_claims', {'payer_claim_id': cid, 'claim_status': status, 'adjudication_sequence': seq, 'service_from': day,
            'service_to': kw.pop('to', day), 'product': kw.pop('product', 'commercial'), 'allowed_amount': allowed, 'paid_amount': allowed,
            'claim_frequency_code': kw.pop('freq', '1'), 'event_date': kw.get('event', day), 'available_date': kw.pop('event', day),
            'claim_type': kw.pop('ctype', 'professional'), **kw})


def rx(rid, pid, day, status='paid', **kw):
    return (rid, pid, 'payer_pharmacy_claims', {'rx_claim_id': rid.upper(), 'claim_status': status, 'drug_name': kw.pop('drug', 'atorvastatin 20 MG Oral Tablet'),
            'ndc': kw.pop('ndc', '00093505910'), 'days_supply': kw.pop('ds', 30), 'quantity_dispensed': 30, 'fill_number': kw.pop('n', 0),
            'prescription_number': kw.pop('rxn', '7001'), 'pharmacy_npi': kw.pop('ph', '1999999999'), 'pharmacy_name': 'Synthetic Chain Pharmacy B #1',
            'pharmacy_type': 'retail_pharmacy', 'prescriber_npi': kw.pop('prescriber', '1111111111'), 'plan_paid': 8.0, 'patient_pay': 2.0,
            'event_date': day, 'available_date': day, **kw})


def adt(rid, pid, visit, trigger, cls, when, arrived, **kw):
    return (rid, pid, 'adt_messages', {'trigger_event': trigger, 'patient_class': cls, 'visit_number': visit, 'event_time': when,
            'available_time': arrived, 'event_date': when[:10], 'available_date': arrived[:10], **kw})


PATIENTS = {'m1': {'age_reported': 70, 'sex_reported': 'F', 'data_segment': 'claims_only'},
            'm2': {'age_reported': 55, 'sex_reported': 'M', 'data_segment': 'claims_plus_ehr'},
            'p1': {'age_reported': 66, 'sex_reported': 'F', 'data_segment': 'deep_chart'}}
SPAN = lambda rid, pid, product, start, end=None, **kw: (rid, pid, 'enrollment_spans', {'payer_id': 'WH-PAYER-C1', 'product': product,
        'member_id': kw.get('member'), 'previous_member_id': kw.get('previous'), 'coverage_start': start, 'coverage_end': end,
        'termination_reason': kw.get('term'), 'enrollment_reason': kw.get('why', 'baseline'), 'event_date': start, 'available_date': start})
NOTE = lambda rid, pid, day, text, **kw: (rid, pid, 'notes', {'note_type': kw.get('type', 'office_followup'), 'text': text, 'event_date': day,
                                                              'encounter_id': kw.get('enc'), 'note_status': 'signed'})
RECORDS = [
    # eligibility: reissued member ID mid-span (one person), open span sent as 9999-12-31
    SPAN('s1', 'm1', 'medicaid', '2028-01-01', '2028-06-15', member='MCD-OLD', term='member_id_reissued'),
    SPAN('s2', 'm1', 'medicaid', '2028-06-15', '9999-12-31', member='MCD-NEW', previous='MCD-OLD', why='member_id_reissued'),
    SPAN('s3', 'm2', 'commercial', '2028-01-01', '9999-12-31', member='C-1'),
    # labs
    lab('eg1', 'm2', 'eGFR', '>60', 'mL/min/1.73m2', '2029-03-01', loinc='98979-8', value_type='ST'),
    lab('ts1', 'm2', 'TSH', '<0.01', 'mIU/L', '2029-03-01', loinc='3016-3', value_type='ST'),
    lab('k1', 'm2', 'Potassium', None, 'mmol/L', '2029-03-02', loinc='2823-3', comment='Specimen hemolyzed. Potassium not reported; recollection requested.'),
    lab('na1', 'm2', 'Sodium', 'TNP', 'mmol/L', '2029-03-02', loinc='2951-2', comment='Test not performed: insufficient specimen.'),
    lab('hd1', 'm2', 'Cholesterol in HDL', 63, 'mg/dL', '2029-03-08', loinc='2085-9', laboratory_order_record_id='LO1', specimen_time='2029-03-07T08:00'),
    lab('hd2', 'm2', 'Cholesterol in HDL', 65.0, 'mg/dL', '2029-03-12', loinc='2085-9', laboratory_order_record_id='LO1', specimen_time='2029-03-07T08:00',
        status='corrected', comment='CORRECTED RESULT. Previously reported as 63. Notified provider.'),
    lab('cr1', 'm2', 'Creatinine', 0.85, 'mg/dL', '2029-03-03', loinc='2160-0', laboratory_order_record_id='LO2', specimen_time='2029-03-02T08:00'),
    lab('cr2', 'm2', 'Creatinine', 0.85, 'mg/dL', '2029-03-03', loinc='2160-0', laboratory_order_record_id='LO2', specimen_time='2029-03-02T08:00',
        interface_message_id='ORU-1'),
    lab('ef1', 'p1', 'eGFR', 50, 'mL/min/1.73m2', '2029-01-05', av='2029-01-06'),
    lab('ef2', 'p1', 'eGFR', 55.6, 'mL/min/1.73m2', '2029-01-05', av='2029-06-01', status='corrected', replaces_result_record_id='ef1'),
    lab('a1', 'm2', 'HEMOGLOBIN A1C', 5.8, '%', '2029-03-04', local_code='HGBA1C'),
    # payer claims: original voided and replaced (frequency 8 / 7), CARC 18 duplicate, undotted ICD-10, missing rendering NPI
    claim('o1a', 'm1', 'O1', 'paid', 1, '2029-02-01', 100.0, principal_diagnosis='E119', diagnosis_codes=['E119', 'I10'], rendering_provider_npi=''),
    claim('o1v', 'm1', 'O1', 'voided', 2, '2029-02-01', 0.0, freq='8', carc='129', event='2029-03-20', principal_diagnosis='E119',
          diagnosis_codes=['E119', 'I10'], rendering_provider_npi=''),
    claim('o1r', 'm1', 'O1-R', 'paid', 1, '2029-02-01', 110.0, freq='7', original_payer_claim_id='O1', event='2029-03-01',
          principal_diagnosis='E11.9', diagnosis_codes=['E11.9', 'I10', 'Z79.899']),
    claim('dup', 'm1', 'O2-D', 'denied', 1, '2029-02-15', 0.0, carc='18'),
    claim('den', 'm1', 'O3', 'denied', 1, '2029-02-20', 0.0, carc='CO-16'),
    # utilization for the claims-only member: ED professional claim + ADT ED visit (same day), facility-only ED claim, admission
    claim('ed1', 'm1', 'E1', 'paid', 1, '2029-04-10', 300.0, place_of_service='23'),
    claim('ed2', 'm1', 'E2', 'paid', 1, '2029-05-05', 900.0, ctype='institutional', type_of_bill='0131'),
    ('ed2l', 'm1', 'payer_medical_claim_lines', {'payer_claim_id': 'E2', 'line_number': 1, 'revenue_code': '0450', 'service_date': '2029-05-05'}),
    claim('ip1', 'm1', 'I1', 'paid', 1, '2029-06-01', 9000.0, ctype='institutional', type_of_bill='0111', admission_date='2029-06-01',
          discharge_date='2029-06-04', to='2029-06-04'),
    claim('ed3', 'm1', 'E3-D', 'denied', 1, '2029-07-01', 0.0, place_of_service='23', carc='18'),   # duplicate: no visit
    adt('ad1', 'm1', 'V1', 'A03', 'E', '2029-04-10T22:00', '2029-04-10T22:05'),                   # discharge arrives first
    adt('ad2', 'm1', 'V1', 'A04', 'E', '2029-04-10T18:00', '2029-04-10T23:00'),
    adt('ad3', 'm1', 'V1', 'A08', 'E', '2029-04-10T19:00', '2029-04-10T23:01'),
    adt('ad4', 'm1', 'V2', 'A01', 'I', '2029-06-01T08:00', '2029-06-01T09:00'),                   # discharge never received
    adt('ad5', 'm1', 'V3', 'A01', 'I', '2029-06-30T08:00', '2029-06-30T09:00'),
    adt('ad6', 'm1', 'V3', 'A03', 'I', '2029-07-02T08:00', '2029-07-02T09:00'),
    # deep chart: EHR ED visit and the payer's ED claim for it are one visit
    ('enc1', 'p1', 'encounters', {'type': 'emergency', 'encounter_id': 'ENC-ED', 'status': 'completed', 'event_date': '2029-02-10'}),
    claim('ed4', 'p1', 'E4', 'paid', 1, '2029-02-10', 400.0, place_of_service='23', product='medicare_ffs'),
    # pharmacy: a paid statin fill returned to stock (B2) is not a fill
    rx('rx1', 'm2', '2029-01-01', n=0), rx('rx1b', 'm2', '2029-01-12', status='reversed', n=0, transaction_code='B2', reversal_reason='return_to_stock'),
    rx('rx2', 'm2', '2029-02-01', n=1), rx('rx3', 'm2', '2029-03-03', n=2), rx('rx3same', 'm2', '2029-03-03', n=2, ds=90, rxn='7009'),
    # e-prescription without a fill link -> fuzzy chain matching
    ('erx1', 'm2', 'medication_orders', {'medication_name': 'lisinopril 40 MG Oral Tablet', 'rxcui': '197884', 'order_class': 'e-prescribe',
                                         'ordering_clinician_id': 'CL-1', 'pharmacy': 'Synthetic Chain Pharmacy B #1', 'days_supply': 30,
                                         'refills_authorized': 2, 'script_message_id': 'NEWRX-1', 'event_date': '2029-04-02'}),
    rx('lf0', 'm2', '2029-04-04', drug='lisinopril 40 MG Oral Tablet', ndc='00172376170', rxn='5550', n=0),
    rx('lf1', 'm2', '2029-05-04', drug='lisinopril 40 MG Oral Tablet', ndc='00172376170', rxn='5550', n=1),
    rx('lf2', 'm2', '2029-06-03', drug='lisinopril 40 MG Oral Tablet', ndc='00172376170', rxn='5550', n=2),
    rx('lf3', 'm2', '2029-07-03', drug='lisinopril 40 MG Oral Tablet', ndc='00172376170', rxn='5550', n=3),  # beyond refills authorized
    rx('old', 'm2', '2029-04-20', drug='lisinopril 40 MG Oral Tablet', ndc='00172376170', rxn='4440', n=6, prescriber='1222222222'),
    ('erx2', 'm2', 'medication_orders', {'medication_name': 'metformin 500 MG Oral Tablet', 'order_class': 'e-prescribe', 'ordering_clinician_id': 'CL-1',
                                         'event_date': '2029-04-02', 'script_message_id': 'NEWRX-2'}),
    ('dsp', 'm2', 'pharmacy_dispenses', {'medication_name': 'Metformin 500 mg tablet', 'rxcui': '861007', 'ndc': '00093101910', 'status': 'prepared',
                                         'event_date': '2029-04-03'}),
    # lists
    ('pl1', 'm2', 'conditions', {'description': 'Essential hypertension', 'icd10': 'I10', 'diagnosis_basis': 'ehr_problem_list', 'clinical_status': 'active', 'event_date': '2027-03-02'}),
    ('pl2', 'm2', 'conditions', {'description': 'Benign essential hypertension', 'icd10': 'I10', 'diagnosis_basis': 'ehr_problem_list', 'clinical_status': 'active', 'event_date': '2028-07-12'}),
    ('pl3', 'm2', 'conditions', {'description': 'HTN', 'icd10': None, 'diagnosis_basis': 'ehr_problem_list', 'event_date': '2026-11-01'}),
    ('pl4', 'm2', 'conditions', {'description': 'Acute upper respiratory infection', 'icd10': 'J06.9', 'diagnosis_basis': 'ehr_problem_list', 'clinical_status': 'active', 'event_date': '2028-01-01'}),
    ('pl5', 'm2', 'conditions', {'description': 'Hyperlipidemia', 'icd10': 'E78.5', 'diagnosis_basis': 'ehr_problem_list', 'clinical_status': 'active', 'event_date': '2027-01-10'}),
    claim('hx1', 'm2', 'H1', 'paid', 1, '2029-05-01', 80.0, principal_diagnosis='I10', diagnosis_codes=['I10']),
    ('ml1', 'm2', 'medication_lists', {'name': 'lisinopril 40 mg tablet', 'list_status': 'active', 'source': 'prescribed', 'event_date': '2029-04-02'}),
    ('ml2', 'm2', 'medication_lists', {'name': 'simvastatin 20 mg tablet', 'list_status': 'active', 'source': 'prescribed', 'event_date': '2028-01-02'}),
    ('ml3', 'm2', 'medication_lists', {'name': 'aspirin 81 mg tablet', 'list_status': 'active', 'source': 'patient_reported', 'event_date': '2029-04-02'}),
    ('al1', 'm2', 'allergies', {'substance': 'penicillin (rash)', 'coded': False, 'reaction': None, 'status': 'active', 'no_known_allergies': False, 'event_date': '2028-06-12'}),
    ('al2', 'm2', 'allergies', {'display': 'Sulfa — rash', 'verification': 'not_supplied', 'event_date': '2027-01-01'}),
    ('al3', 'm2', 'allergies', {'substance': 'latex', 'coded': False, 'reaction': None, 'status': 'active', 'no_known_allergies': False, 'event_date': '2028-06-12'}),
    ('al4', 'm1', 'allergies', {'substance': 'NKDA', 'coded': True, 'no_known_allergies': True, 'status': 'active', 'event_date': '2028-06-12'}),
    ('sh1', 'm2', 'social_history', {'smoking_status': 'Former smoker', 'smoking_status_snomed': '8517006', 'event_date': '2027-03-02'}),
    ('sh2', 'm2', 'social_history', {'smoking_status': 'Current every day smoker', 'smoking_status_snomed': '449868002', 'event_date': '2029-01-01'}),
    # note-only therapy decisions and dictation
    NOTE('n1', 'm2', '2029-03-10', 'Assessment and Plan: 1. Stop atorvastatin 20 mg due to muscle aches; patient instructed to discontinue.\n'
                                    '2. hypertension: BP 152/76, above goal. Increase antihypertensive dose; recheck in 4 weeks.'),
    NOTE('n2', 'm2', '2029-03-01', 'Discussed starting insulin; patient declines at this time.\nContinue lysine april 40 mg daily.\n'
                                    'Reports increased dyspnea on exertion.\nDictated but not read.'),
    NOTE('n3', 'm2', '2029-04-02', 'Plan: Start lisinopril 40 mg daily; home BP log.', type='medication_start'),
    # outside documents (text read by the adapter into source_document_text)
    ('d1', 'm2', 'scanned_documents', {'document_id': 'DOC-A', 'document_title': 'Echo', 'has_text_layer': True, 'relative_path': 'documents/outside/A.pdf', 'event_date': '2028-03-02'}),
    ('d2', 'm2', 'scanned_documents', {'document_id': 'DOC-B', 'has_text_layer': False, 'ocr_text': None, 'relative_path': 'documents/outside/B.pdf', 'event_date': '2028-04-01'}),
    ('d3', 'm2', 'scanned_documents', {'document_id': 'DOC-C', 'has_text_layer': False, 'ocr_text': 'IMPRESS1ON: LVEF 4O%. n0 acute process.',
                                       'relative_path': 'documents/outside/C.pdf', 'event_date': '2028-05-01'}),
    ('d4', 'm2', 'scanned_documents', {'document_id': 'DOC-S', 'has_text_layer': True, 'relative_path': 'documents/outside/S.pdf', 'event_date': '2027-02-07'}),
    ('d5', 'm2', 'scanned_documents', {'document_id': 'DOC-S', 'has_text_layer': True, 'relative_path': 'documents/outside/S.pdf', 'event_date': '2027-08-30'}),
    ('d6', 'm2', 'scanned_documents', {'document_id': 'DOC-A2', 'document_title': 'Echo', 'has_text_layer': True, 'relative_path': 'documents/outside/A2.pdf', 'event_date': '2028-03-09'}),
]
ECHO = 'ECHOCARDIOGRAM REPORT\nPatient: Synthetic    Date of service: 03/01/2028\n' + 'FINDINGS: normal valves. ' * 8 + '\nIMPRESSION: LVEF 35% by biplane Simpson.'
DOC_TEXT = [('d1', 'text_layer', None, ECHO, '2028-03-01', 1, 'documents/outside/A.pdf'),
            ('d2', 'image_only', 'image_only_no_text_layer', None, None, 1, 'documents/outside/B.pdf'),
            ('d3', 'image_only', 'image_only_no_text_layer', None, None, 1, 'documents/outside/C.pdf'),
            ('d4', 'text_layer', None, 'RADIOLOGY\nDate of service: 08/27/2027\nIMPRESSION: No acute process.', '2027-08-27', 2, 'documents/outside/S.pdf'),
            ('d5', 'text_layer', None, 'RADIOLOGY\nDate of service: 08/27/2027\nIMPRESSION: No acute process.', '2027-08-27', 2, 'documents/outside/S.pdf'),
            ('d6', 'text_layer', None, ECHO, '2028-03-01', 1, 'documents/outside/A2.pdf')]
ENTITIES = [('clinicians', {'clinician_id': 'CL-1', 'organization_id': 'PR-A', 'npi': '1111111111'})]


def v23_stack():
    path = stack_fixture(RECORDS, PATIENTS, ENTITIES)
    w = sqlite3.connect(path.parent / 'warehouse.sqlite3')
    w.execute(v4.DDL)
    w.executemany('INSERT INTO source_document_text VALUES (' + ','.join('?' * 14) + ')',
                  [('rel', rid, 'm2', None, rel, 'sha', 'sha', st, why, text, len(text or ''), dos, n, outside_documents.VERSION)
                   for rid, st, why, text, dos, n, rel in DOC_TEXT])
    w.commit(); w.close()
    semantic_layer.build(path)
    return path


class ClientDataRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = v23_stack()

    def q(self, sql):
        return sql_query.execute(self.path, {'query': sql, 'returns': 'table'}, None)['rows']

    def one(self, sql):
        rows = self.q(sql); self.assertEqual(len(rows), 1, rows); return rows[0]

    # labs ------------------------------------------------------------------------------------------------------------
    def test_censored_strings_become_bounded_numbers(self):
        eg = self.one("SELECT value, value_canonical, value_censor, value_text, excluded_reason FROM measurement WHERE record_id = 'eg1'")
        self.assertEqual((eg['value'], eg['value_canonical'], eg['value_censor'], eg['value_text'], eg['excluded_reason']), (60, 60, '>', '>60', None))
        self.assertEqual(self.one("SELECT value, value_censor FROM measurement WHERE record_id = 'ts1'"), {'value': 0.01, 'value_censor': '<'})
        latest = self.one("SELECT value_canonical, value_censor FROM measurement_latest WHERE patient_id = 'm2' AND concept = 'egfr'")
        self.assertEqual((latest['value_canonical'], latest['value_censor']), (60, '>'))

    def test_tnp_and_hemolyzed_excluded_with_reason(self):
        rows = {r['record_id']: r for r in self.q("SELECT record_id, value, excluded_reason FROM measurement WHERE record_id IN ('k1','na1')")}
        self.assertEqual((rows['k1']['excluded_reason'], rows['k1']['value']), ('specimen_hemolyzed', None))
        self.assertEqual((rows['na1']['excluded_reason'], rows['na1']['value']), ('test_not_performed', None))
        self.assertEqual(self.q("SELECT 1 FROM measurement_latest WHERE patient_id = 'm2' AND concept IN ('potassium', 'sodium')"), [])

    def test_latest_correction_wins_and_duplicates_collapse(self):
        rows = {r['record_id']: r for r in self.q("SELECT record_id, result_current, superseded_by_record_id, supersede_reason FROM measurement "
                                                   "WHERE record_id IN ('hd1','hd2','cr1','cr2','ef1','ef2')")}
        self.assertEqual((rows['hd1']['result_current'], rows['hd1']['superseded_by_record_id'], rows['hd1']['supersede_reason']), (0, 'hd2', 'corrected'))
        self.assertEqual(rows['hd2']['result_current'], 1)
        self.assertEqual((rows['cr2']['result_current'], rows['cr2']['supersede_reason'], rows['cr1']['result_current']),
                         (0, 'duplicate_interface_message', 1))
        self.assertEqual((rows['ef1']['result_current'], rows['ef1']['superseded_by_record_id']), (0, 'ef2'))  # replaces_result_record_id
        hdl = self.one("SELECT value_canonical, same_day_values FROM measurement_latest WHERE patient_id = 'm2' AND concept = 'hdl'")
        self.assertEqual(hdl['value_canonical'], 65.0)
        self.assertEqual(self.one("SELECT value_canonical FROM measurement_latest WHERE patient_id = 'p1' AND concept = 'egfr'")['value_canonical'], 55.6)

    def test_local_only_code_decides_the_analyte(self):
        rows = self.q("SELECT concept, loinc, loinc_source, local_code FROM measurement WHERE record_id = 'a1'")
        self.assertEqual(rows, [{'concept': 'hba1c', 'loinc': '4548-4', 'loinc_source': 'local_code_map', 'local_code': 'HGBA1C'}])

    # eligibility -----------------------------------------------------------------------------------------------------
    def test_open_span_sentinel_and_reissued_member_id(self):
        s = {r['record_id']: r for r in self.q('SELECT * FROM enrollment_span')}
        self.assertEqual((s['s2']['coverage_end'], s['s2']['coverage_end_received'], s['s2']['open_ended']), (None, '9999-12-31', 1))
        self.assertEqual((s['s2']['previous_member_id'], s['s2']['member_id_reissued'], s['s1']['member_id_reissued']), ('MCD-OLD', 1, 1))
        months = [r['month'] for r in self.q("SELECT month FROM member_month WHERE patient_id = 'm1' ORDER BY month")]
        self.assertEqual(len(months), len(set(months)))  # the reissue month counts once
        self.assertEqual((months[0], months[-1], len(months)), ('2028-01', AS_OF[:7], 20))

    # claims ----------------------------------------------------------------------------------------------------------
    def test_replacement_supersedes_original_and_carc18_never_counts(self):
        c = {r['payer_claim_id']: r for r in self.q('SELECT * FROM payer_claim')}
        self.assertEqual((c['O1']['version_status'], c['O1']['superseded_by_payer_claim_id'], c['O1']['counts_in_metrics']),
                         ('superseded_by_replacement', 'O1-R', 0))
        self.assertEqual((c['O1-R']['version_status'], c['O1-R']['claim_frequency_code'], c['O1-R']['original_payer_claim_id'], c['O1-R']['counts_in_metrics']),
                         ('current', '7', 'O1', 1))
        self.assertEqual((c['O2-D']['version_status'], c['O2-D']['duplicate_submission'], c['O2-D']['counts_in_metrics']), ('duplicate_submission', 1, 0))
        self.assertEqual((c['O3']['version_status'], c['O3']['counts_in_metrics']), ('current', 1))  # an ordinary denial stays

    def test_undotted_icd10_normalised_and_rendering_npi_nullable(self):
        c = self.one("SELECT principal_diagnosis, principal_diagnosis_received, diagnosis_codes, icd10_normalized, rendering_npi, rendering_npi_missing "
                     "FROM payer_claim WHERE payer_claim_id = 'O1'")
        self.assertEqual((c['principal_diagnosis'], c['principal_diagnosis_received'], json.loads(c['diagnosis_codes']), c['icd10_normalized']),
                         ('E11.9', 'E119', ['E11.9', 'I10'], 1))
        self.assertEqual((c['rendering_npi'], c['rendering_npi_missing']), (None, 1))
        dx = self.q("SELECT icd10, icd10_received, counts_in_metrics FROM payer_claim_diagnosis WHERE payer_claim_id = 'O1' ORDER BY rank")
        self.assertEqual(dx[0], {'icd10': 'E11.9', 'icd10_received': 'E119', 'counts_in_metrics': 0})
        self.assertEqual(client_data.icd10('Z7189'), 'Z71.89'); self.assertEqual(client_data.icd10('I10'), 'I10')

    def test_claims_metrics_count_current_versions_only(self):
        db = sqlite3.connect(f'file:{self.path / "semantic.sqlite3"}?mode=ro', uri=True); self.addCleanup(db.close)
        r = metrics.resolve({'question': 'payer claim denial rate this year', 'as_of': AS_OF})
        self.assertEqual(r['id'], 'payer_claim_denial_rate')
        # 2029: counted = O1-R, O3 (denied), E1, E2, I1, E4, H1 -> 1 of 7; O1 (superseded), O2-D and E3-D (CARC 18) never count
        self.assertAlmostEqual(db.execute(r["sql"]).fetchone()[0], 100 / 7)  # governed SQL returns full precision
        self.assertEqual(metrics.lint('payer claim denial rate this year', "SELECT 100.0 * sum(final_status = 'denied') / count(*) FROM payer_claim",
                                      AS_OF)[0]['rule'], 'ML-VERSION')
        cost = vbc.execute(self.path, ['m1'], {'metric': 'cost', 'period': {'start': '2029-01-01', 'end': AS_OF}}, AS_OF)['rows'][0]
        self.assertEqual(cost['member_months'], 8)
        self.assertAlmostEqual(cost['allowed_pmpm'], round((110 + 300 + 900 + 9000) / 8, 2))  # O1 superseded, CARC 18 duplicates out

    # pharmacy --------------------------------------------------------------------------------------------------------
    def test_b2_reversal_pairs_with_the_fill_it_reverses(self):
        rx = {r['record_id']: r for r in self.q('SELECT record_id, counts_as_fill, reversed_by_record_id, reversal_of_record_id FROM payer_pharmacy_claim')}
        self.assertEqual((rx['rx1']['counts_as_fill'], rx['rx1']['reversed_by_record_id']), (0, 'rx1b'))
        self.assertEqual((rx['rx1b']['counts_as_fill'], rx['rx1b']['reversal_of_record_id']), (0, 'rx1'))
        self.assertEqual(rx['rx2']['counts_as_fill'], 1)
        self.assertEqual(self.one("SELECT DISTINCT fill_reversed FROM medication WHERE record_id = 'rx1'")['fill_reversed'], 1)

    def test_pdc_never_counts_a_returned_fill(self):
        db = sqlite3.connect(f'file:{self.path / "semantic.sqlite3"}?mode=ro', uri=True); self.addCleanup(db.close)
        rows, _ = measures._rows(measures.Context(db), {'type': 'pdc', 'label': 'statins', 'classes': ['statin']})
        days = sorted(d for d, _, _ in rows['m2'])
        self.assertNotIn('2029-01-01', days)  # B2-reversed
        self.assertEqual(days, ['2029-02-01', '2029-03-03', '2029-03-03'])  # payer claims count as fills (claims-only members)
        met, text = measures._pdc(rows['m2'], {'label': 'statins', 'min_fills': 2}, {'period_start': '2029-01-01', 'period_end': '2029-06-30'})
        self.assertIn('2 fills', text)  # same-day fills: one fill with the longest days supply (PQA)
        self.assertIn('from 2029-02-01', text)

    # fuzzy order -> fill linkage -------------------------------------------------------------------------------------
    def test_eprescription_linked_to_its_pharmacy_chain(self):
        p = self.one("SELECT * FROM prescription WHERE record_id = 'erx1'")
        self.assertEqual((p['fill_status'], p['fill_basis'], p['fills_received'], p['matched_chain'], p['script_message_id']),
                         ('filled', 'fuzzy_prescription_chain', 3, 'payer:m2:1999999999:5550', 'NEWRX-1'))  # refills 2 -> 3 fills, lf3 is a later Rx
        self.assertGreater(p['match_confidence'], 0.95)
        comp = json.loads(p['match_components'])
        self.assertEqual((comp['prescriber'], comp['timing'], comp['new_prescription']), (2.5, 2.0, 1.0))
        fills = [r['fill_record_id'] for r in self.q("SELECT fill_record_id FROM prescription_fill WHERE order_record_id = 'erx1' ORDER BY fill_date")]
        self.assertEqual(fills, ['lf0', 'lf1', 'lf2'])
        q = self.one("SELECT fill_status, prescribed_not_filled, match_confidence FROM prescription WHERE record_id = 'erx2'")
        self.assertEqual((q['fill_status'], q['prescribed_not_filled'], q['match_confidence']), ('not_filled', 1, None))  # prepared != dispensed

    def test_linker_renewals_continue_a_chain_and_fills_are_not_shared(self):
        o = lambda day, name, refills=1: {'patient': 'p', 'date': day, 'key': client_data.drug_key(name), 'rxcui': None, 'clinician': 'C',
                                          'pharmacy': None, 'days_supply': 30, 'refills': refills}
        k = client_data.drug_key
        fill = lambda day, n, rid, name: (day, n, rid, k(name), None, 30)
        chain = lambda fills: {'patient': 'p', 'fills': fills, 'first_date': fills[0][0], 'prescriber': '1', 'pharmacy_name': 'x', 'mail': False}
        los = 'losartan potassium 50 MG Oral Tablet'
        chains = {'c1': chain([fill('2029-01-02', 0, 'f0', los), fill('2029-02-01', 1, 'f1', los), fill('2029-03-03', 2, 'f2', los),
                               fill('2029-04-02', 3, 'f3', los)]),
                  'c3': chain([fill('2028-12-01', 0, 'm0', 'metformin 500 MG Oral Tablet')]),
                  'c4': chain([fill('2029-05-01', 0, 'a0', 'atorvastatin 20 MG Oral Tablet')])}
        orders = {'o1': o('2029-01-01', 'losartan 50 mg tablet'), 'o2': o('2029-03-01', 'losartan 50 mg tablet'),  # o2 renews the same Rx number
                  'o3': o('2029-01-01', 'metformin 500 mg tablet'),       # last filled before the order: nothing to link
                  'o4': o('2029-01-01', 'atorvastatin 20 mg tablet')}     # first fill 120 days later: outside the window
        links = client_data.link_prescriptions(orders, chains, {'C': '1'})
        self.assertEqual({oid: [f[2] for f in v[3]] for oid, v in links.items()}, {'o1': ['f0', 'f1'], 'o2': ['f2', 'f3']})
        self.assertEqual(links['o2'][2]['new_prescription'], 0.0)   # renewal: no new-prescription bonus, no penalty
        self.assertEqual(client_data.drug_key('losartan potassium 100 MG Oral Tablet'), ('losartan', '100mg'))
        self.assertEqual(client_data.drug_key('120 ACTUAT fluticasone propionate 0.044 MG/ACTUAT Metered Dose Inhaler [Flovent]'), ('fluticasone', '0.044mg'))

    # ADT and utilization ---------------------------------------------------------------------------------------------
    def test_adt_visits_record_gaps_and_order(self):
        v = {r['visit_number']: r for r in self.q('SELECT * FROM adt_visit')}
        self.assertEqual((v['V1']['admit_date'], v['V1']['discharge_known'], v['V1']['out_of_order'], v['V1']['a08_updates']), ('2029-04-10', 1, 1, 1))
        self.assertEqual((v['V2']['discharge_known'], v['V2']['discharge_date']), (0, None))

    def test_claims_only_member_utilization_from_claims_and_adt(self):
        ev = self.q("SELECT kind, event_date, end_date, sources, in_ehr FROM utilization_event WHERE patient_id = 'm1' ORDER BY event_date, kind")
        self.assertEqual([(e['kind'], e['event_date'], e['sources']) for e in ev],
                         [('ed_visit', '2029-04-10', 'adt,claim'), ('ed_visit', '2029-05-05', 'claim'),
                          ('inpatient', '2029-06-01', 'adt,claim'), ('inpatient', '2029-06-30', 'adt')])
        self.assertEqual(ev[2]['end_date'], '2029-06-04')  # ADT never sent the discharge; the facility claim did
        stays = self.q("SELECT admit_date, discharge_date, stay_source, readmitted_30d FROM stay WHERE patient_id = 'm1' ORDER BY admit_date")
        self.assertEqual([(s['admit_date'], s['stay_source'], s['readmitted_30d']) for s in stays],
                         [('2029-06-01', 'adt', 1), ('2029-06-30', 'adt', 0)])  # readmitted 26 days after discharge
        u = vbc.execute(self.path, ['m1'], {'metric': 'utilization', 'period': {'start': '2029-01-01', 'end': '2029-12-31'}}, AS_OF)['rows'][0]
        self.assertEqual((u['ed_visits'], u['admissions'], u['readmissions']), (2, 2, 1))

    def test_ehr_visit_and_its_claim_are_one_event(self):
        ev = self.q("SELECT sources, in_ehr FROM utilization_event WHERE patient_id = 'p1' AND kind = 'ed_visit'")
        self.assertEqual(ev, [{'sources': 'claim,ehr', 'in_ehr': 1}])
        self.assertEqual(len(self.q("SELECT 1 FROM encounter WHERE patient_id = 'p1' AND type_concept = 'emergency_visit'")), 1)

    # lists -----------------------------------------------------------------------------------------------------------
    def test_problem_list_rot(self):
        p = {r['problem_key']: r for r in self.q("SELECT * FROM problem_list_entry WHERE patient_id = 'm2'")}
        htn = p['I10']
        self.assertEqual((htn['entries'], htn['shorthand_entries'], htn['first_recorded'], htn['last_confirmed'], htn['last_supporting_evidence'], htn['stale']),
                         (3, 1, '2026-11-01', '2028-07-12', '2029-05-01', 0))
        self.assertEqual((p['J06']['acute'], p['J06']['stale']), (1, 1))      # acute problem never resolved
        self.assertEqual(p['E78']['stale'], 1); self.assertLess(p['E78']['recency_weight'], 0.5)

    def test_medication_list_against_fills(self):
        m = {r['drug_key']: r for r in self.q("SELECT * FROM medication_list_entry WHERE patient_id = 'm2'")}
        self.assertIsNone(m['lisinopril']['discrepancy']); self.assertEqual(m['lisinopril']['last_fill_date'], '2029-07-03')
        self.assertEqual(m['simvastatin']['discrepancy'], 'listed_active_no_fill_180d')   # commission
        self.assertEqual((m['aspirin']['otc'], m['aspirin']['discrepancy']), (1, None))     # OTC lives only on the list
        self.assertEqual(m['atorvastatin']['discrepancy'], 'filled_not_listed')            # omission

    def test_allergies_and_smoking_status(self):
        a = {r['substance']: r for r in self.q('SELECT * FROM allergy_entry')}
        self.assertEqual((a['penicillin']['reaction'], a['penicillin']['free_text']), ('rash', 1))
        self.assertEqual(a['sulfa']['reaction'], 'rash')
        self.assertEqual((a['latex']['reaction_missing'], a['no known allergies']['no_known_allergies']), (1, 1))
        s = self.one("SELECT status, status_date, structured_records FROM smoking_status WHERE patient_id = 'm2'")
        self.assertEqual((s['status'], s['status_date'], s['structured_records']), ('current', '2029-01-01', 2))

    # notes -----------------------------------------------------------------------------------------------------------
    def test_note_only_therapy_changes(self):
        t = {(r['record_id'], r['action']): r for r in self.q('SELECT * FROM note_therapy_change')}
        stop = t[('n1', 'stop')]
        self.assertEqual((stop['target'], stop['drug_class'], stop['reason'], stop['fills_after_180d']), ('atorvastatin', 'statin', 'muscle aches', 0))
        self.assertEqual((t[('n1', 'dose_increase')]['target_kind'], t[('n1', 'dose_increase')]['drug_class']), ('drug_class', 'antihypertensive'))
        self.assertEqual(t[('n2', 'declined')]['target'], 'insulin')
        self.assertEqual(t[('n3', 'start')]['order_record_id'], 'erx1')
        n = {r['record_id']: r for r in self.q("SELECT record_id, dictated, dictation_unreviewed FROM clinical_note")}
        self.assertEqual((n['n2']['dictated'], n['n2']['dictation_unreviewed'], n['n1']['dictated']), (1, 1, 0))
        self.assertEqual(client_data.therapy_mentions('Stop lysine april 20 mg due to cough.')[0][1:2] + (client_data.therapy_mentions(
            'Stop lysine april 20 mg due to cough.')[0][6],), ('lisinopril', 1))
        self.assertEqual(client_data.therapy_mentions('Reports increased dyspnea on exertion.'), [])

    # outside documents -----------------------------------------------------------------------------------------------
    def test_scanned_document_readability(self):
        d = {r['record_id']: r for r in self.q('SELECT * FROM scanned_document')}
        self.assertEqual((d['d1']['text_source'], d['d1']['readable']), ('pdf_text_layer', 1))
        self.assertEqual((d['d2']['readable'], d['d2']['unreadable_reason']), (0, 'image_only_no_ocr'))
        self.assertEqual(d['d3']['text_source'], 'front_office_ocr')
        self.assertEqual((d['d4']['unreadable_reason'], d['d5']['text_source']), ('file_belongs_to_another_record', 'pdf_text_layer'))
        self.assertEqual(d['d6']['duplicate_of_record_id'], 'd1')  # the same report faxed twice
        facts = {(r['record_id'], r['fact_type']) for r in self.q("SELECT record_id, fact_type FROM note_fact WHERE stream = 'scanned_documents'")}
        self.assertIn(('d1', 'ejection_fraction'), facts)
        self.assertNotIn('d2', {rid for rid, _ in facts})

    def test_meta_and_descriptions(self):
        db = sqlite3.connect(self.path / 'semantic.sqlite3')
        try: meta = dict(db.execute('SELECT key, value FROM meta'))
        finally: db.close()
        self.assertEqual(meta['version'], 'semantic-layer/19')
        feeds = json.loads(meta['population_feeds'])
        self.assertEqual(feeds['client_claims']['pharmacy']['b2_reversals_paired'], 1)
        described = semantic_layer.describe(self.path)['tables']
        for t in ('utilization_event', 'adt_visit', 'problem_list_entry', 'medication_list_entry', 'allergy_entry', 'smoking_status',
                  'scanned_document', 'note_therapy_change', 'payer_claim_diagnosis'):
            self.assertTrue(described[t]['meaning'], t)


class EarlierReleasesUnchanged(unittest.TestCase):
    """A release without v2.3 artifacts keeps every earlier answer: no superseded labs, no added events or stays."""
    def test_v22_shaped_stack_has_neutral_client_columns(self):
        from test_warehouse_v22 import RECORDS as V22, PATIENTS as P22, ENTITIES as E22
        path = stack_fixture(V22, P22, E22); semantic_layer.build(path)
        q = lambda sql: sql_query.execute(path, {'query': sql, 'returns': 'table'}, None)['rows']
        self.assertEqual(q('SELECT count(*) AS n FROM measurement WHERE result_current = 0 OR excluded_reason IS NOT NULL')[0]['n'], 0)
        self.assertEqual(q("SELECT count(*) AS n FROM stay WHERE stay_source <> 'encounters'")[0]['n'], 0)
        self.assertEqual({r['version_status'] for r in q('SELECT version_status FROM payer_claim')}, {'current'})
        self.assertEqual(q("SELECT fill_basis FROM prescription WHERE record_id = 'o1'")[0]['fill_basis'], 'patient_drug_window_30d')


if __name__ == '__main__':
    unittest.main()
