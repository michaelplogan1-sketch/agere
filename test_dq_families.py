"""Data-quality / feed families (dq_families.py) and VBC metric families (vbc_families.py): deterministic answers from
the delivery, identity, record-version, claims, enrollment and appointment tables.

Small synthetic sqlite fixture with hand-computed answers per family, declines (shapes the families do not represent,
scope boilerplate that mentions duplicate charts), the economical-planner rung and SPECIALIST_ROUTES, the cascade score /
certificate for both tiers, and the calibration reference (tools/calibration/family_variants.py) agreeing with the
families on the fixture. No model calls."""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import os as _os


def setUpModule():  # the restated-records family is off in the product (native feeds lack version links); tested here
    _os.environ['AGERE_DQ_RESTATED_FROM_REBUILT_LINEAGE'] = 'on'


def tearDownModule():
    _os.environ.pop('AGERE_DQ_RESTATED_FROM_REBUILT_LINEAGE', None)

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clinical_core.agere.clinical import cascade, dq_families as dq, family_common as fc, sql_query, together, vbc_families as vb
from clinical_core.agere.ingest.delivery import COLUMNS as DF_COLUMNS, DDL as DELIVERY_DDL

sys.path.insert(0, str(Path(__file__).resolve().parent / 'tools' / 'calibration'))
import family_variants as FV  # noqa: E402

AS_OF = '2030-05-27'
SCOPE = ' Scope: every patient in the (sampled) delivery, duplicate charts counted as their person.'

SCHEMA = '''
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE raw_stg_person_map(source_key TEXT, person_id TEXT, tier INTEGER, evidence TEXT);
CREATE TABLE record(record_id TEXT PRIMARY KEY, patient_id TEXT, stream TEXT, event_date TEXT, recorded_date TEXT,
  available_date TEXT, organization_id TEXT, payload TEXT);
CREATE TABLE record_correction(record_id TEXT PRIMARY KEY, stream TEXT, patient_id TEXT, superseded_by TEXT);
CREATE TABLE measurement(record_id TEXT, patient_id TEXT, source_table TEXT, result_status TEXT, event_date TEXT);
CREATE TABLE raw_rosters_aco_provider_roster(_file TEXT, _row INTEGER, practice_id TEXT, provider_npi TEXT, term_date TEXT,
  file_variant TEXT);
CREATE TABLE payer_claim(payer_claim_id TEXT PRIMARY KEY, patient_id TEXT, product TEXT, line_of_business TEXT, claim_type TEXT,
  type_of_bill TEXT, service_from TEXT, service_to TEXT, admission_date TEXT, discharge_date TEXT, drg TEXT, final_status TEXT,
  allowed_cents INTEGER, paid_cents INTEGER, patient_responsibility_cents INTEGER, counts_in_metrics INTEGER, record_id TEXT);
CREATE TABLE payer_adjudication(payer_claim_id TEXT, record_id TEXT PRIMARY KEY, sequence INTEGER, claim_status TEXT, carc TEXT,
  adjudication_date TEXT, paid_date TEXT, paid_cents INTEGER);
CREATE TABLE payer_claim_line(record_id TEXT, payer_claim_id TEXT, patient_id TEXT, service_date TEXT, cpt TEXT, hcpcs TEXT,
  counts_in_metrics INTEGER);
CREATE TABLE payer_pharmacy_claim(record_id TEXT PRIMARY KEY, patient_id TEXT, fill_date TEXT, claim_status TEXT,
  plan_paid_cents INTEGER, reversed_by_record_id TEXT);
CREATE TABLE enrollment_span(record_id TEXT PRIMARY KEY, patient_id TEXT, product TEXT, line_of_business TEXT,
  coverage_start TEXT, coverage_end TEXT, attribution_practice_id TEXT);
CREATE TABLE death(patient_id TEXT PRIMARY KEY, death_date TEXT);
CREATE TABLE appointment(appointment_id TEXT PRIMARY KEY, patient_id TEXT, scheduled_date TEXT, final_status TEXT);
'''


def _df(relpath, feed, parser=None, **kw):
    row = {c: None for c in DF_COLUMNS}
    row.update(relpath=relpath, feed=feed, parser=parser, is_original=1, resend_by_name=0, truncated=0, empty_where_expected=0,
               empty_or_header_only=0, empty_file=0, header_only=0, is_v2_replacement=0, delivered_date='2028-02-03',
               days_after_period_end=3, series_usual_lag_days=3)
    row.update(kw)
    return row


FILES = [
    _df('ehr/a/2028-01/T.csv', 'ehr', 'flat'),                                                       # on time
    _df('ehr/a/2028-02/T.csv', 'ehr', 'flat', days_after_period_end=33, delivered_date='2028-03-31'),  # late (next cycle)
    _df('hl7/x/2028-02/H_20280210.hl7', 'hl7', 'hl7', name_date='2028-02-10', days_after_name_date=2, delivered_date='2028-02-12'),  # late
    _df('hl7/x/2028-02/H_20280210_resend.hl7', 'hl7', 'hl7', name_date='2028-02-10', days_after_name_date=3, is_original=0,
        resend_by_name=1, content_identical_to='hl7/x/2028-02/H_20280210.hl7'),                  # HL7 re-send (not a flat one)
    _df('ehr/a/2028-02/T_RESEND.csv', 'ehr', 'flat', is_original=0, resend_by_name=1, content_identical_to='ehr/a/2028-02/T.csv'),
    _df('payers/p/2028-01/M.csv', 'payers', 'flat', truncated=1),
    _df('x12/p/2028-01/I.x12', 'x12', 'x12', truncated=1, delivered_date='2029-01-05'),
    _df('fhir_bulk/f/2028-01/O.ndjson', 'fhir_bulk', 'fhir', truncated=1),
    _df('fhir_bulk/f/2028-01/O_RESEND.ndjson', 'fhir_bulk', 'fhir', is_original=0, resend_by_name=1,
        content_identical_to='fhir_bulk/f/2028-01/O.ndjson'),                                    # a whole-file re-send (counts)
    _df('ehr/a/2028-03/E.csv', 'ehr', 'flat', header_only=1, empty_or_header_only=1, empty_where_expected=1),
    _df('ehr/a/2028-03/R.csv', 'ehr', 'flat', header_only=1, empty_or_header_only=1),
    _df('cms_cclf/2028-01/ZC1', 'cms_cclf', None, empty_file=1, empty_or_header_only=1, empty_where_expected=1),
    _df('rosters/2028-01/R_20280110.csv', 'rosters', 'flat', name_date='2028-01-10', days_after_name_date=0),
    _df('rosters/2028-02/R_20280210.csv', 'rosters', 'flat', name_date='2028-02-10', days_after_name_date=0),
    _df('rosters/2028-03/R_20280310.csv', 'rosters', 'flat', name_date='2028-03-10', days_after_name_date=0),
]


def _rec(rid, pid, stream, ev, av, ss, org):
    return (rid, pid, stream, ev, ev, av, org, json.dumps({'source_system': ss, 'organization_id': org}))


def build(path):
    db = sqlite3.connect(path)
    db.executescript(SCHEMA + DELIVERY_DDL)
    db.executemany(f"INSERT INTO delivery_file({', '.join(DF_COLUMNS)}) VALUES ({', '.join('?' * len(DF_COLUMNS))})",
                   [[f[c] for c in DF_COLUMNS] for f in FILES])
    db.executemany('INSERT INTO delivery_gap(series, feed, missing_period) VALUES (?,?,?)',
                   [('ehr|a|T', 'ehr', '2028-04'), ('ehr|a|E', 'ehr', '2029-01'), ('payers|p|M', 'payers', '2028-07')])
    db.executemany('INSERT INTO raw_stg_person_map VALUES (?,?,0,"")', [
        ('epic:E1:a', 'P1'), ('epic:E1:b', 'P1'), ('fhir:F1:x', 'P1'), ('fhir:F1:y', 'P1'), ('mrn:PR-1:m1', 'P1'),  # 1 duplicate (vendor + its FHIR copy)
        ('athena:7:a', 'P2'), ('fhir:F2:z', 'P2'), ('member:X:1', 'P2'), ('member:X:2', 'P2'),           # member ids are not charts
        ('fhir:F3:a', 'P3'), ('fhir:F3:b', 'P3'), ('fhir:F3:c', 'P3'),                                     # 2 duplicates
        ('ecw:PR-A:4', 'P4'), ('epic:EPIC-CA:Z4', 'P4'), ('mrn:PR-A:old4', 'P4'), ('mrn:PR-A:new4', 'P4'),  # renumbered at go-live
        ('ecw:PR-A:5', 'P5'), ('epic:EPIC-CA:Z5', 'P5'), ('mrn:PR-A:only5', 'P5'),
        ('epic:EPIC-CA:Z6', 'P6'), ('mrn:PR-A:a6', 'P6'), ('mrn:PR-A:b6', 'P6'),                            # new chart only
    ])
    db.executemany('INSERT INTO record VALUES (?,?,?,?,?,?,?,?)', [
        _rec('r1', 'P4', 'conditions', '2027-05-01', '2027-05-31', 'ecw:PR-A', 'PR-A'),
        _rec('r2', 'P5', 'encounters', '2028-01-02', '2028-01-31', 'ecw:PR-A', 'PR-A'),
        _rec('r3', 'P4', 'conditions', '2028-10-01', '2028-10-31', 'epic:EPIC-CA', 'PR-A'),   # converted problem
        _rec('r4', 'P4', 'labs', '2028-05-01', '2028-10-31', 'epic:EPIC-CA', 'PR-A'),         # converted lab history
        _rec('r5', 'P4', 'notes', '2028-11-02', '2028-11-30', 'epic:EPIC-CA', 'PR-A'),        # new note, not converted
        _rec('r6', 'P4', 'conditions', '2028-11-15', '2028-11-30', 'epic:EPIC-CA', 'PR-A'),   # new problem
        _rec('r7', 'P6', 'conditions', '2028-10-01', '2028-10-31', 'epic:EPIC-CA', 'PR-A'),   # no old chart: not a copy
        _rec('r8', 'P5', 'encounters', '2028-12-01', '2028-12-31', 'epic:EPIC-CA', 'PR-A'),
        _rec('r9', 'P1', 'labs', '2028-01-01', '2028-01-31', 'hl7:REFLAB', 'PR-1'),
    ])
    db.executemany('INSERT INTO record_correction VALUES (?,?,?,?)',
                   [('c1', 'labs', 'P1', 'x'), ('c2', 'labs', 'P1', 'y'), ('c3', 'encounters', 'P2', 'z')])
    db.executemany('INSERT INTO measurement VALUES (?,?,?,?,?)', [
        ('m1', 'P1', 'labs', 'corrected', '2028-02-01'), ('m2', 'P2', 'labs', 'corrected', '2029-03-01'),
        ('m3', 'P2', 'labs', 'final', '2029-03-01'), ('m4', 'P2', 'vitals', 'corrected', '2029-03-01')])
    db.executemany('INSERT INTO raw_rosters_aco_provider_roster VALUES (?,?,?,?,?,NULL)', [
        ('rosters/2028-01/R_20280110.csv', 1, 'PA', 'N1', ''), ('rosters/2028-02/R_20280210.csv', 1, 'PA', 'N1', ''),
        ('rosters/2028-03/R_20280310.csv', 1, 'PA', 'N1', '2028-02-01'),                 # still listed active on 02-10: stale
        ('rosters/2028-01/R_20280110.csv', 2, 'PB', 'N2', ''), ('rosters/2028-02/R_20280210.csv', 2, 'PB', 'N2', '2028-02-01'),
        ('rosters/2028-02/R_20280210.csv', 3, 'PC', 'N3', ''),                           # left 02-05: still on staff on 02-01,
        ('rosters/2028-03/R_20280310.csv', 3, 'PC', 'N3', '2028-02-05')])                # so the February roster is not stale
    claims = [  # id, pid, product, lob, type, tob, from, to, adm, dis, drg, final, allowed, paid, pr, cim, record
        ('C1', 'P1', 'commercial', 'commercial', 'professional', None, '2028-03-01', '2028-03-01', None, None, None, 'paid', 10000, 8000, 2000, 1, 'k1'),
        ('C2', 'P2', 'medicaid', 'medicaid', 'professional', None, '2028-06-01', '2028-06-01', None, None, None, 'paid', 5000, 5000, 0, 1, 'k2'),
        ('C3', 'P1', 'commercial', 'commercial', 'institutional', '0111', '2028-04-01', '2028-04-05', '2028-04-01', '2028-04-05', '470', 'paid', 0, 20000, 1000, 1, 'k3'),
        ('C4', 'P1', 'commercial', 'commercial', 'institutional', '0111', '2028-04-20', '2028-04-22', '2028-04-20', '2028-04-22', None, 'paid', 3000, 3000, 0, 1, 'k4'),
        ('C5', 'P2', 'medicaid', 'medicaid', 'institutional', '0111', '2028-12-30', '2029-01-02', '2028-12-30', '2029-01-02', None, 'voided', 4000, 0, 0, 0, 'k5'),
        ('C6', 'P2', 'medicaid', 'medicaid', 'professional', None, '2028-07-01', '2028-07-01', None, None, None, 'denied', 100, 0, 0, 1, 'k6'),
        ('C7', 'P3', 'marketplace', 'commercial', 'professional', None, '2029-01-05', '2029-01-05', None, None, None, 'paid', 7000, 6500, 500, 1, 'k7'),
    ]
    db.executemany('INSERT INTO payer_claim VALUES (' + ','.join('?' * 17) + ')', claims)
    db.executemany('INSERT INTO record(record_id, patient_id, stream, payload) VALUES (?,?,?,?)',
                   [('k3', 'P1', 'payer_medical_claims', json.dumps({'admission_type': '3'})),
                    ('k4', 'P1', 'payer_medical_claims', json.dumps({'admission_type': '1'})),
                    ('k5', 'P2', 'payer_medical_claims', json.dumps({'admission_type': '1'}))])
    db.executemany('INSERT INTO payer_adjudication VALUES (?,?,?,?,?,?,?,?)', [
        ('C1', 'a1', 1, 'paid', None, '2028-03-20', '2028-03-20', 8000), ('C2', 'a2', 1, 'paid', None, '2028-06-20', '2028-06-20', 5000),
        ('C3', 'a3', 1, 'paid', None, '2028-05-10', '2028-05-10', 20000), ('C4', 'a4', 1, 'paid', None, '2028-05-15', '2028-05-15', 3000),
        ('C6', 'a6', 1, 'denied', 'CO-16', '2028-07-15', '2028-07-15', 0), ('C5', 'a5', 1, 'paid', None, '2028-12-31', '2028-12-31', 4000),
        ('C5', 'a5b', 2, 'voided', None, '2029-01-10', '2029-01-10', 0), ('C1', 'a1b', 2, 'recouped', 'CO-27', '2028-09-01', '2028-09-01', -1000),
        ('C7', 'a7', 1, 'paid', None, '2029-02-01', '2029-02-01', 6500)])
    db.executemany('INSERT INTO payer_claim_line VALUES (?,?,?,?,?,?,?)', [
        ('l1', 'C1', 'P1', '2028-03-01', '99385', None, 1), ('l2', 'C1', 'P1', '2028-03-01', '99213', None, 1),
        ('l3', 'C2', 'P2', '2028-06-01', '99395', None, 1), ('l4', 'C6', 'P2', '2028-07-01', '99386', None, 1),
        ('l5', 'C1', 'P1', '2028-03-01', '99385', None, 1)])
    db.executemany('INSERT INTO payer_pharmacy_claim VALUES (?,?,?,?,?,?)', [
        ('x1', 'P1', '2028-02-01', 'paid', 1000, None), ('x2', 'P1', '2028-03-01', 'paid', 2000, 'x3'),
        ('x3', 'P1', '2028-03-01', 'reversed', -2000, None), ('x4', 'P2', '2028-04-01', 'recouped', -300, None),
        ('x5', 'P2', '2028-04-02', 'rejected', 0, None), ('x6', 'P2', '2029-04-02', 'paid', 500, None)])
    # Mike 2026-10-25: transaction deltas, including reversals; event dates are authored here as the fill dates.
    db.execute('CREATE TABLE pharmacy_claim_event AS SELECT patient_id, fill_date AS event_date, plan_paid_cents AS payer_paid_delta_cents FROM payer_pharmacy_claim')
    db.executemany('INSERT INTO enrollment_span VALUES (?,?,?,?,?,?,?)', [
        ('e1', 'P1', 'commercial', 'commercial', '2027-06-01', None, 'PRAC-1'),
        ('e2', 'P2', 'medicaid', 'medicaid', '2028-03-15', '2028-08-31', 'PRAC-2'),
        ('e3', 'P2', 'medigap', 'medigap', '2028-01-01', None, 'PRAC-9'),
        ('e4', 'P3', 'marketplace', 'commercial', '2028-11-01', None, None)])
    db.execute("INSERT INTO death VALUES ('P1', '2028-06-10')")
    db.executemany('INSERT INTO appointment VALUES (?,?,?,?)', [
        ('A1', 'P1', '2028-02-01', 'fulfilled'), ('A2', 'P1', '2028-02-15', 'no_show'), ('A3', 'P1', '2028-03-01', 'no_show'),
        ('A4', 'P1', '2029-01-01', 'no_show'), ('A5', 'P2', '2028-05-05', 'rescheduled'), ('A6', 'P2', '2028-05-06', 'cancelled')])
    db.commit()
    db.close()


# The delivery's own definitions (public question text of the native question set) plus a few variants.
SPANS = FV.SPANS
APPTS = FV.APPTS
STAY = FV.STAY
QUESTIONS = {
    'late': ('How many data files came in late? Definition: Release-wide raw/ deliveries. Files delivered after their normal cycle '
             '(late or caught up in the next cycle).', 2),
    'resend': ('How many files were sent to us twice? Definition: Release-wide raw/ deliveries. Flat files re-sent with identical '
               'content under another name.', 2),  # the flat and the FHIR ndjson re-send; never the HL7 batch
    'truncated': ('How many files arrived cut off or incomplete? Definition: Release-wide raw/ deliveries. Flat files truncated '
                  "mid-record, HL7 batches whose trailer count doesn't match, X12 interchanges missing trailers.", 3),  # FHIR counts
    'missing': ('Are we missing any expected files? How many? Definition: Release-wide raw/ deliveries. Expected periodic files that '
                'never arrived by as_of.', 3),
    'empty': ('How many files came through empty? Definition: Release-wide raw/ deliveries. Files with a header only, or 0 bytes, '
              'where rows were expected.', 2),
    'dup_charts': ('How many duplicate patient charts are in the data? Definition: Duplicate charts present in the (sampled) '
                   'warehouse: patient_ids that belong to the same person as another chart.', 3),
    'dup_people': ('How many patients have more than one chart in our data? Definition: People with at least one duplicate chart '
                   'present in the (sampled) warehouse: a separate patient_id that belongs to the same person.', 2),
    'corrected': ('How many lab results were corrected after they were first reported? Definition: All patients. Lab results with '
                  'result_status corrected.', 2),
    'restated': ('How many of our records were later restated by a newer version? Definition: All patients. Source records that a '
                 'later record supersedes (supersedes_record_id points to them).', 3),
    'roster': ('How many providers were still on the roster after they left? Definition: Release-wide. Provider-practice '
               "affiliations listed on the monthly roster after the provider's departure.", 1),
    'conversion': ('After the EHR migrations, how many converted records duplicate data we already had? Definition: Release-wide. '
                   'Records created by an EHR data conversion that copy an existing record (problems, meds, allergies, '
                   'immunizations, labs, notes).', 2),
    'renumbered': ('How many patients got a new chart number when their practice changed EHRs? Definition: Release-wide. Patients '
                   'whose MRN changed at an EHR go-live.', 1),
    # Mike 2026-10-25: counted medical claims by service date; all pharmacy transaction deltas by event date.
    'paid_by_product': ("Break down 2028 medical claim payments by line of business. Definition: All patients. Sum of paid_cents "
                        "on counted payer medical claims with service_from in 2028, by the claim's product.", {'commercial': 310.0, 'medicaid': 50.0}),
    'pmpm_commercial': ('What was our commercial PMPM in 2028? Definition: Allowed amount on final-adjudication paid commercial medical '
                        'claims with service in 2028 / 2028 commercial member-months (delivered spans, through death).' + SCOPE, 56.67),
    'pmpm_total': ('What was our total medical PMPM in 2028? Definition: Allowed amount on final-adjudication paid medical claims with '
                   'service in 2028 (all products) / all 2028 primary member-months.' + SCOPE, 30.0),
    'liability': ('How much did members owe out of pocket on 2028 medical claims? Definition: All patients. Sum of patient_responsibility '
                  "(the payer's adjudicated member liability: 835 CAS PR, flat member liability) on payer medical claims whose latest "
                  'adjudication is paid, service_from in 2028; CCLF payment amounts are not used.' + SCOPE +
                  " Native: patient responsibility is the payer's adjudicated member liability (835 CAS PR, flat member liability); "
                  'CCLF payment amounts are not used.', 30.0),
    'rx': ('What did the plans pay for prescriptions in 2028? Definition: All patients. Sum of payer_paid_delta_cents on all pharmacy claim transactions with '
           'event date in 2028 (reversals and recoupments netted).', 7.0),
    'recoup27': ('How many payments were taken back because coverage was terminated retroactively? Definition: All patients. Recouped '
                 'adjudications with CARC CO-27 through as_of.', 1),
    'member_months': ('How many member-months did we have in 2028 by line of business? Definition: Member-months in 2028 (covered on '
                      'the 1st, through death) by product, all delivered spans including secondary products; a span version superseded '
                      'by a later delivered version of the same coverage (retro-termination, correction) is replaced by it (a span '
                      'superseded only by a member_id_reissued continuation is kept), and each person counts once per month per '
                      'product. Table keys are the delivered product values.' + SCOPE,
                      {'commercial': 6, 'medicaid': 5, 'medigap': 12, 'marketplace': 2}),
    'attribution': ('How many members were attributed to each practice in May 2028? Definition: Persons alive and enrolled on 2028-05-01 '
                    'by the attributed practice on their active span (spans with no attribution are not counted).' + SPANS + SCOPE,
                    {'PRAC-1': 1, 'PRAC-2': 1}),
    'top1': ('Who was our most expensive member in 2028? Definition: The 1 persons with the highest allowed amount on '
             'final-adjudication paid medical claims with service in 2028.' + SCOPE, ['P1']),
    'readmit': ('How many unplanned readmissions within 30 days did we have in 2028? Definition: Inpatient stays admitted in 2028 as '
                'emergency or urgent (UB-04 admission type 1 or 2) within 30 days after a prior inpatient discharge of the same '
                'person.' + STAY + SCOPE, 1),
    'readmit_rate': ('What was the 30-day readmission rate in 2028? Definition: Inpatient stays admitted in 2028 as emergency or urgent '
                     '(UB-04 admission type 1 or 2) within 0-30 days after a prior inpatient discharge of the same person, divided by '
                     'inpatient stays discharged in 2028 (discharge date, else service-to date), percent.' + STAY + SCOPE, 50.0),
    'planned': ('How many planned (elective) admissions did we have in 2028? Definition: Inpatient stays admitted in 2028 with UB-04 '
                'admission type 3 (elective).' + STAY + SCOPE, 1),
    'appts': ('How many appointments were scheduled for 2028? Definition: Appointments with a slot in 2028, any final status.' + APPTS + SCOPE, 5),
    'appts_cancelled': ('How many appointments were cancelled in 2028? Definition: Appointments with a 2028 slot whose final status is '
                        'cancelled (including rescheduled).' + APPTS + SCOPE, 2),
    'appts_month': ('How many appointments do we have booked each month in 2028? Definition: Appointments (any final status) by slot '
                    'month (YYYY-MM), 2028.' + APPTS + SCOPE, {'2028-02': 2, '2028-03': 1, '2028-05': 2}),
    'no_shows': ('Which patients no-showed three or more times? Definition: Persons with >= 3 appointments whose final status is '
                 'no_show, through as_of.' + APPTS + SCOPE, ['P1']),
    'preventive': ('How many preventive physicals did we bill in 2028? Definition: All patients. Distinct member-dates with a final paid '
                   'claim line 99381-99397 (preventive E/M), service in 2028.', 2),
}
DQ_KEYS = ('late', 'resend', 'truncated', 'missing', 'empty', 'dup_charts', 'dup_people', 'corrected', 'restated', 'roster',
           'conversion', 'renumbered')


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.stack_dir = Path(self.temp.name) / 'stack'
        self.stack_dir.mkdir()
        self.db = self.stack_dir / 'semantic.sqlite3'
        build(self.db)
        (self.stack_dir / 'run.json').write_text(json.dumps({'source_digest': 'dq-family-test'}))

    def tearDown(self):
        self.temp.cleanup()

    def answer(self, question):
        res = dq.plan(None, question, AS_OF) or vb.plan(None, question, AS_OF)
        self.assertIsNotNone(res, question)
        node = res['graph']['nodes'][0]['parameters']
        self.assertTrue(node['query'].startswith('-- DEFINITION: '))
        out = sql_query.execute(None, node, target=self.db)
        if node['returns'] in ('count', 'value'): return res, out['value']
        if node['returns'] == 'patient_set': return res, out['patient_ids']
        return res, {r['key']: r['value'] for r in out['rows']}


class FamilyAnswerTests(Fixture):
    def test_every_family_answers_its_definition(self):
        for key, (question, expected) in QUESTIONS.items():
            with self.subTest(key):
                res, got = self.answer(question)
                self.assertEqual(res['receipt']['version'], dq.VERSION if key in DQ_KEYS else vb.VERSION)
                if isinstance(expected, float): self.assertAlmostEqual(got, expected, places=2)
                else: self.assertEqual(got, expected)

    def test_routes_and_parameters_are_recorded(self):
        res = dq.plan(None, QUESTIONS['truncated'][0], AS_OF)
        self.assertEqual(res['spec'], {'family': 'file_count', 'parameters': {'condition': 'truncated'}, 'returns': 'count'})
        self.assertEqual(res['receipt']['defaults'], [])
        self.assertIn('dq family file_count@1', res['graph']['interpretation'])
        res = vb.plan(None, QUESTIONS['pmpm_commercial'][0], AS_OF)
        self.assertEqual(res['spec']['parameters'], {'year': 2028, 'products': ['commercial'], 'mm_scope': 'all', 'mm_products': ['commercial']})

    def test_same_sql_on_every_run(self):
        for key in ('conversion', 'member_months'):
            a = (dq.plan(None, QUESTIONS[key][0], AS_OF) or vb.plan(None, QUESTIONS[key][0], AS_OF))['graph']
            b = (dq.plan(None, QUESTIONS[key][0], AS_OF) or vb.plan(None, QUESTIONS[key][0], AS_OF))['graph']
            self.assertEqual(a, b)

    def test_population_word_never_reaches_the_query(self):
        for question, _ in QUESTIONS.values():
            res = dq.plan(None, question, AS_OF) or vb.plan(None, question, AS_OF)
            self.assertNotRegex(res['graph']['nodes'][0]['parameters']['query'], r'(?i)\bpopulation\b')

    def test_plain_question_uses_recorded_defaults(self):
        res = dq.plan(None, 'How many data files came in late?', AS_OF)
        self.assertEqual(res['spec']['parameters'], {'condition': 'late'})
        self.assertTrue(res['receipt']['defaults'])
        self.assertFalse(res['receipt']['has_definition'])
        self.assertIsNone(vb.plan(None, 'What was our commercial PMPM in 2028?', AS_OF))  # VBC families need the definition


class DeclineTests(Fixture):
    DECLINED = [
        'How many files had broken or malformed rows? Definition: Release-wide raw/ deliveries. Flat files with malformed rows (bad '
        'delimiters, short rows, bad encoding, embedded line breaks) or swapped columns.',
        'How many duplicate HL7 or X12 transmissions did we get? Definition: Release-wide raw/ deliveries. HL7 batches re-sending '
        'earlier messages plus X12 interchanges re-sent with the same control number.',
        'How many patients opted out of data sharing? Definition: Not answerable from this delivery: data-sharing consent and opt-out records.',
        'How many of our patients have diabetes? Definition: Persons with a documented type 1 or type 2 diabetes code (E10/E11). '
        'Documented = ICD-10 code on a paid payer claim, an EHR problem-list entry or an EHR encounter diagnosis, any date through as_of.' + SCOPE,
        'What was our initial claim denial rate in 2028? Definition: All patients. Payer medical claims whose first adjudication (lowest '
        'sequence) is dated 2028: share whose first status is denied, percent. Replacement and void transactions excluded.',
        'What was our no-show rate in 2028? Definition: Appointments with a 2028 slot: no_show / (fulfilled + no_show), percent.' + APPTS + SCOPE,
        'How many Medicare Advantage members do we have today? Definition: Persons alive with an active medicare_advantage or dual_ma span '
        'on as_of.' + SPANS + SCOPE,
        'What was our commercial PMPM in 2028 for women? Definition: Allowed amount on final-adjudication paid commercial medical claims '
        'with service in 2028 for women / 2028 commercial member-months (delivered spans, through death).' + SCOPE,
        'How many late files did each sender send? Definition: Release-wide raw/ deliveries. Files delivered after their normal cycle, by sender.',
    ]

    def test_shapes_the_families_do_not_represent_decline(self):
        for q in self.DECLINED:
            with self.subTest(q[:60]):
                self.assertIsNone(dq.plan(None, q, AS_OF))
                self.assertIsNone(vb.plan(None, q, AS_OF))

    def test_scope_boilerplate_and_span_rules_are_not_questions(self):
        # "duplicate charts counted as their person" and "a span superseded by a later version" are boilerplate
        q = ('How many members switched health plans during the period? Definition: Persons with primary coverage from more than one '
             'payer across their delivered spans.' + SPANS + SCOPE)
        self.assertIsNone(dq.plan(None, q, AS_OF))

    def test_validation_refuses_bad_parameters(self):
        with self.assertRaises(ValueError):
            dq.validate({'family': 'file_count', 'parameters': {'condition': 'nope'}, 'returns': 'count'})
        with self.assertRaises(ValueError):
            vb.validate({'family': 'claims_finance', 'parameters': {'measure': 'paid', 'year': 2028, 'products': ['bogus']}, 'returns': 'value'})
        with self.assertRaises(ValueError):
            vb.validate({'family': 'top_n_cost_members', 'parameters': {'year': 2028, 'n': 5}, 'returns': 'count'})


def fake_planner_stack(path):
    return SimpleNamespace(path=Path(path), allow_live=False, receipts=Path(path) / 'provider-receipts',
                           retriever=SimpleNamespace(meta={'snapshot': {'as_of': AS_OF}, 'source_digest': 'dq-family-test'}, encoder=None))


class PlannerRungTests(Fixture):
    def setUp(self):
        super().setUp()
        self.env = patch.dict(os.environ, {'AGERE_LOCAL_MODEL': 'off', 'AGERE_DQ_FAMILIES': 'on', 'AGERE_VBC_FAMILIES': 'on'})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        super().tearDown()

    def test_defined_questions_route_to_their_family_rung(self):
        from clinical_core.agere.clinical.economical_planner import plan
        for key, route, family in (('late', 'dq_family', 'file_count'), ('pmpm_commercial', 'vbc_family', 'pmpm'),
                                   ('readmit', 'vbc_family', 'readmissions')):
            graph, receipt = plan(fake_planner_stack(self.stack_dir), QUESTIONS[key][0], None, None)
            self.assertEqual(receipt['route'], route)
            self.assertEqual(receipt[route]['family'], family)
            self.assertEqual(receipt['new_paid_api_calls'], 0)
            self.assertEqual(graph['nodes'][0]['operation'], 'sql')
        self.assertTrue({'dq_family', 'vbc_family'} <= together.SPECIALIST_ROUTES)

    def test_plain_question_routes_through_the_late_rung(self):
        from clinical_core.agere.clinical.economical_planner import plan
        graph, receipt = plan(fake_planner_stack(self.stack_dir), 'How many files came through empty?', None, None)
        self.assertEqual(receipt['route'], 'dq_family')
        self.assertEqual(receipt['dq_family']['parameters']['condition'], 'empty_expected')

    def test_flag_off_keeps_the_ladder_unchanged(self):
        from clinical_core.agere.clinical.economical_planner import plan
        with patch.dict(os.environ, {'AGERE_DQ_FAMILIES': 'off'}):
            try:
                _, receipt = plan(fake_planner_stack(self.stack_dir), QUESTIONS['late'][0], None, None)
            except Exception:  # noqa: BLE001 - no other rung can answer in this fixture
                receipt = {}
            self.assertNotEqual(receipt.get('route'), 'dq_family')

    def test_verified_answer_is_recorded_and_reused(self):
        from clinical_core.agere.clinical.economical_planner import plan
        q = QUESTIONS['restated'][0]
        graph, receipt = plan(fake_planner_stack(self.stack_dir), q, None, None)
        node = graph['nodes'][0]['parameters']
        answer = {'status': 'answered', 'count': 3, 'query': node['query']}
        self.assertTrue(fc.record_route(self.stack_dir, q, receipt, answer))
        graph2, receipt2 = plan(fake_planner_stack(self.stack_dir), q, None, None)
        self.assertEqual(receipt2['route'], 'learned_verified_sql')
        self.assertEqual(receipt2['dq_family']['family'], 'restated_records')
        self.assertEqual(graph2['nodes'][0]['parameters']['query'], node['query'])


class CertificateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'cascade_thresholds.json'
        self.env = patch.dict(os.environ, {'AGERE_CASCADE_THRESHOLDS': str(self.path), 'AGERE_CASCADE': 'on'})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        cascade._CACHE.clear()
        self.temp.cleanup()

    def test_both_tiers_are_calibrated_route_tiers_bound_to_their_code(self):
        for tier, module_file in (('dq_family', 'dq_families.py'), ('vbc_family', 'vbc_families.py')):
            self.assertIn(tier, cascade.ROUTE_TIERS)
            self.assertIn(module_file, cascade.TIER_CODE[tier])
            self.assertIn('family_common.py', cascade.TIER_CODE[tier])
            self.assertIsNotNone(cascade.code_digest(tier))

    def test_score_and_certificate(self):
        receipt = {'route': 'dq_family', 'dq_family': {'family': 'file_count', 'defaults': [], 'has_definition': True}}
        self.assertEqual(cascade.route_score('dq_family', receipt), 1.0)
        plain = {'route': 'dq_family', 'dq_family': {'family': 'file_count', 'defaults': ['d'], 'has_definition': False}}
        self.assertAlmostEqual(cascade.route_score('dq_family', plain), 0.87)
        rec = {'tier': 'dq_family', 'alpha': 0.05, 'delta': 0.10, 'n_calibration': 98, 'threshold': 1.0, 'calibrated': True,
               'bound': 0.0232, 'n_accepted': 98, 'errors_accepted': 0, 'bind': cascade.code_digest('dq_family')}
        cascade.write([rec], self.path)
        high, low = cascade.certify_route(receipt), cascade.certify_route(plain)
        self.assertEqual((high['status'], high['accept']), ('certified', True))
        self.assertEqual((low['status'], low['accept']), ('below_threshold', False))

    def test_shipped_certificates_do_not_transfer_across_definition_changes(self):
        """Mike 2026-10-25 changed VBC definitions; retain its old certificate as historical, never relabel it current."""
        with patch.dict(os.environ, {'AGERE_CASCADE_THRESHOLDS': str(cascade.FILE)}):
            cascade._CACHE.clear()
            for tier in ('dq_family', 'vbc_family'):
                rec = cascade.record(tier)
                self.assertIsNotNone(rec, tier)
                self.assertTrue(rec['calibrated'])
                if tier == 'vbc_family':
                    self.assertNotEqual(rec['bind'], cascade.code_digest(tier))
                    current = cascade.certify(tier, 1.0)
                    self.assertEqual(current['status'], 'uncalibrated')
                    self.assertFalse(current['certified'])
                    self.assertIn('stale', current['reason'])
                    continue
                self.assertEqual(rec['bind'], cascade.code_digest(tier), f'{tier} certificate is stale: rerun tools/calibrate_cascades.py fit --write')
                self.assertLessEqual(rec['bound'], rec['alpha'])


class CalibrationReferenceTests(Fixture):
    """The calibration reference (independent Python over row reads) agrees with the families on the fixture."""
    def test_reference_matches_family_answers(self):
        db = sqlite3.connect(self.db)
        ref = FV.Reference(db, AS_OF)
        truths = [{'family': 'file_count', 'condition': 'late', 'formats': [], 'year': None, 'by_feed': False},
                  {'family': 'file_count', 'condition': 'truncated', 'formats': ['flat', 'hl7', 'x12'], 'year': None, 'by_feed': False},
                  {'family': 'duplicate_charts', 'unit': 'charts'}, {'family': 'chart_renumbering'},
                  {'family': 'conversion_duplicates', 'streams': ['conditions', 'medication_lists', 'allergies', 'immunizations', 'labs', 'notes']},
                  {'family': 'roster_after_departure', 'unit': 'affiliations'},
                  {'family': 'paid', 'year': 2028, 'group': 'product'}, {'family': 'pmpm', 'year': 2028, 'prod': 'commercial'},
                  {'family': 'readmit', 'year': 2028, 'days': 30, 'rate': True}, {'family': 'repeat', 'n': 3, 'status': 'no_show'}]
        expected = [2, 3, 3, 1, 2, 1, {'commercial': 310.0, 'medicaid': 50.0}, 56.67, 50.0, ['P1']]
        for t, e in zip(truths, expected):
            with self.subTest(t['family']):
                got = ref.answer(t)
                self.assertTrue(FV.same(got, e, 'table' if isinstance(e, dict) else 'patient_set' if isinstance(e, list)
                                        else 'value' if isinstance(e, float) else 'count'), (t, got))
        db.close()

    def test_generated_variants_are_claimed_by_their_own_tier_only(self):
        for it in FV.generate():
            own = (dq if it['tier'] == 'dq_family' else vb).plan(None, it['question'], AS_OF)
            other = (vb if it['tier'] == 'dq_family' else dq).plan(None, it['question'], AS_OF)
            self.assertIsNotNone(own, it['question'])
            self.assertIsNone(other, it['question'])
            self.assertEqual(own['spec']['returns'], it['answer_type'], it['question'])


if __name__ == '__main__':
    unittest.main()
