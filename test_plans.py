"""Routed enrollment plans (clinical_core/agere/plans): catalog, deterministic router, request parsing, narrative gate,
envelope wiring (together / workspace_stack / Workspace.ask) and the workspace API route. Small synthetic sqlite fixtures."""
import csv
import io
import json
import sqlite3
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from clinical_core.agere.plans import answer as plan_answer, catalog, narrative, request, router

AS_OF = '2030-05-27'

SCHEMA = '''
CREATE TABLE meta(key TEXT, value TEXT);
CREATE TABLE patient(patient_id TEXT PRIMARY KEY, birth_date TEXT, age_latest INTEGER, pcp_clinician_id TEXT, pcp_practice_id TEXT,
  preferred_language TEXT, interpreter_needed INTEGER, payer_id TEXT, line_of_business TEXT);
CREATE TABLE enrollment_span(record_id TEXT, patient_id TEXT, member_id TEXT, payer_id TEXT, product TEXT, line_of_business TEXT,
  plan_id TEXT, coverage_start TEXT, coverage_end TEXT, enrollment_reason TEXT, termination_reason TEXT, dual_status TEXT,
  attribution_practice_id TEXT, attribution_npi TEXT, available_date TEXT);
CREATE TABLE payer_claim(payer_claim_id TEXT, record_id TEXT, patient_id TEXT, place_of_service TEXT, type_of_bill TEXT,
  service_from TEXT, service_to TEXT, admission_date TEXT, discharge_status TEXT, drg TEXT, counts_in_metrics INTEGER);
CREATE TABLE payer_claim_diagnosis(payer_claim_id TEXT, patient_id TEXT, service_from TEXT, rank INTEGER, icd10 TEXT);
CREATE TABLE payer_claim_line(record_id TEXT, payer_claim_id TEXT, patient_id TEXT, line_number INTEGER, service_date TEXT,
  cpt TEXT, hcpcs TEXT, counts_in_metrics INTEGER);
CREATE TABLE medication_list_entry(patient_id TEXT, drug_key TEXT, name TEXT, list_status TEXT, last_listed TEXT,
  last_order_date TEXT, last_fill_date TEXT, record_id TEXT);
CREATE TABLE fill(fill_id INTEGER, patient_id TEXT, fill_date TEXT, days_supply INTEGER, supply_end TEXT, class_key TEXT,
  drug_class TEXT, drug_name TEXT, record_ids TEXT);
CREATE TABLE condition(record_id TEXT, patient_id TEXT, icd10 TEXT, event_date TEXT);
CREATE TABLE encounter(record_id TEXT, patient_id TEXT, encounter_id TEXT, type TEXT, type_concept TEXT, event_date TEXT,
  organization_id TEXT, attending_clinician_id TEXT, disposition TEXT);
CREATE TABLE encounter_diagnosis(encounter_id TEXT, patient_id TEXT, rank INTEGER, icd10 TEXT, type TEXT, record_id TEXT);
CREATE TABLE clinician(clinician_id TEXT PRIMARY KEY, npi TEXT);
'''


def build_fixture(path):
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.execute("INSERT INTO meta VALUES ('as_of', ?)", (AS_OF,))
    pats = [  # id, birth_date, pcp clinician, pcp practice, language
        ('HF1', '1955-03-01', None, None, 'en'), ('HF2', '1950-01-01', None, None, 'es'), ('HF3', '1940-01-01', None, None, 'en'),
        ('HF4', '1960-06-15', None, None, 'en'), ('HF5', '1962-01-01', None, None, 'en'), ('HF6', '1938-01-01', None, None, 'en'),
        ('HF7', '1950-05-05', 'C9', 'PRAC-PT', 'en'),
        ('PT1', '1950-02-02', None, None, 'en'), ('PT2', '1980-02-02', None, None, 'en'), ('PT3', '1985-02-02', None, None, 'en'),
        ('PT4', '2018-02-02', None, None, 'en'), ('PT5', '1970-02-02', None, None, 'en')]
    db.executemany('INSERT INTO patient(patient_id, birth_date, pcp_clinician_id, pcp_practice_id, preferred_language) VALUES (?,?,?,?,?)', pats)
    spans = [
        ('ES-HF1', 'HF1', 'M1', 'PAY-MCD', 'medicaid', 'medicaid', '2028-01-01', None, None, 'PRAC-A', '1111111111', '2028-01-05'),
        ('ES-HF2', 'HF2', 'M2', 'PAY-COM', 'commercial', 'commercial', '2027-01-01', '2029-06-30', 'disenrolled', 'PRAC-B', '2222222222', '2027-01-05'),
        ('ES-HF3', 'HF3', 'M3', 'PAY-MCR', 'medicare_ffs', 'medicare', '2026-01-01', '2030-01-01', 'death', 'PRAC-A', '1111111111', '2026-01-05'),
        ('ES-HF4', 'HF4', 'M4', 'PAY-COM', 'commercial', 'commercial', '2027-01-01', None, None, None, None, '2027-01-05'),
        ('ES-HF5', 'HF5', 'M5', 'PAY-COM', 'commercial', 'commercial', '2027-01-01', None, None, 'PRAC-B', '2222222222', '2027-01-05'),
        ('ES-HF6', 'HF6', 'M6', 'PAY-MCR', 'medicare_ffs', 'medicare', '2026-01-01', None, None, 'PRAC-A', '1111111111', '2026-01-05'),
        ('ES-HF7', 'HF7', 'M7', 'PAY-SUP', 'medigap', 'medigap', '2026-01-01', None, None, None, None, '2026-01-05'),
        ('ES-PT1', 'PT1', 'N1', 'PAY-MCR', 'medicare_ffs', 'medicare', '2026-01-01', None, None, 'PRAC-A', '1111111111', '2026-01-05'),
        ('ES-PT2', 'PT2', 'N2', 'PAY-COM', 'commercial', 'commercial', '2026-01-01', None, None, 'PRAC-B', '2222222222', '2026-01-05'),
        ('ES-PT3', 'PT3', 'N3', 'PAY-COM', 'commercial', 'commercial', '2026-01-01', None, None, 'PRAC-B', '2222222222', '2026-01-05'),
        ('ES-PT4', 'PT4', 'N4', 'PAY-COM', 'commercial', 'commercial', '2026-01-01', None, None, 'PRAC-B', '2222222222', '2026-01-05'),
        ('ES-PT5', 'PT5', 'N5', 'PAY-COM', 'commercial', 'commercial', '2026-01-01', None, None, 'PRAC-B', '2222222222', '2026-01-05'),
        # a future-dated span (not yet available at as_of) never counts
        ('ES-HF2-FUT', 'HF2', 'M2b', 'PAY-COM', 'commercial', 'commercial', '2030-05-01', None, None, None, None, '2030-06-15'),
    ]
    db.executemany('INSERT INTO enrollment_span(record_id, patient_id, member_id, payer_id, product, line_of_business, coverage_start, '
                   'coverage_end, termination_reason, attribution_practice_id, attribution_npi, available_date) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', spans)
    db.executemany('INSERT INTO condition VALUES (?,?,?,?)',
                   [(f'CN-{p}', p, 'I50.22', '2027-03-01') for p in ('HF1', 'HF2', 'HF3', 'HF4', 'HF5', 'HF6', 'HF7')])
    fills = [  # HF1: ACEi + evidence-based BB in the 120-day window; an old MRA fill outside it
        (1, 'HF1', '2030-04-01', 30, '2030-04-30', 'ace_inhibitor', 'lisinopril 20 MG', 'FR-1'),
        (2, 'HF1', '2030-03-15', 90, '2030-06-12', 'hf_evidence_based_beta_blocker', 'carvedilol 25 MG', 'FR-2'),
        (3, 'HF1', '2029-06-01', 30, '2029-06-30', 'mra', 'spironolactone 25 MG', 'FR-3'),
        # HF5: all four pillars filled
        (4, 'HF5', '2030-05-01', 30, '2030-05-30', 'arni', 'sacubitril valsartan', 'FR-4'),
        (5, 'HF5', '2030-05-01', 30, '2030-05-30', 'hf_evidence_based_beta_blocker', '24 HR metoprolol succinate', 'FR-5'),
        (6, 'HF5', '2030-05-01', 30, '2030-05-30', 'mra', 'eplerenone', 'FR-6'),
        (7, 'HF5', '2030-05-01', 30, '2030-05-30', 'sglt2_inhibitor', 'empagliflozin', 'FR-7')]
    db.executemany('INSERT INTO fill VALUES (?,?,?,?,?,?,?,?,?)', [f[:6] + (f[5],) + f[6:] for f in fills])
    db.executemany('INSERT INTO medication_list_entry VALUES (?,?,?,?,?,?,?,?)', [
        ('HF2', 'spironolactone', 'SPIRONOLACTONE 25 MG ORAL TABLET', 'active', '2030-04-20', None, None, 'ML-1'),
        ('HF4', 'metoprolol', 'METOPROLOL TARTRATE 25 MG ORAL TABLET', 'active', '2030-05-01', None, None, 'ML-2'),
        ('HF4', 'empagliflozin', 'EMPAGLIFLOZIN 10 MG', 'discontinued', '2030-05-01', None, None, 'ML-3'),
        ('HF4', 'losartan', 'LOSARTAN 50 MG', 'active', '2027-01-01', None, None, 'ML-4')])  # stale: outside the lookback
    db.executemany('INSERT INTO encounter VALUES (?,?,?,?,?,?,?,?,?)', [
        ('EN-1', 'HF4', 'E1', 'office', 'primary_care_visit', '2029-01-10', 'ORG-OLD', 'C1', None),
        ('EN-2', 'HF4', 'E2', 'office', 'primary_care_visit', '2030-02-10', 'ORG-NEW', 'C2', None),
        ('EN-3', 'HF4', 'E3', 'office', 'primary_care_visit', '2030-07-10', 'ORG-FUTURE', 'C3', None),  # after as_of
        ('EN-4', 'PT5', 'E4', 'emergency_registration', 'emergency_visit', '2030-04-01', 'HOSP', None, None),
        ('EN-5', 'PT5', 'E5', 'admission', 'inpatient_admission', '2030-04-01', 'HOSP', None, None)])
    db.execute("INSERT INTO encounter_diagnosis VALUES ('E4', 'PT5', 1, 'M54.50', 'primary', 'ED-DX-1')")
    db.executemany('INSERT INTO clinician VALUES (?,?)', [('C1', '3333333333'), ('C2', '4444444444')])
    claims = [  # id, record, patient, pos, tob, from, to, admission, discharge status
        ('CL-HOSP', 'RC-HOSP', 'HF6', None, '0811', '2030-02-01', '2030-02-28', None, None),
        ('CL-ED1', 'RC-ED1', 'PT1', '23', None, '2030-05-07', '2030-05-07', None, None),
        ('CL-ED2', 'RC-ED2', 'PT2', '23', None, '2029-12-01', '2029-12-01', None, None),
        ('CL-ED3', 'RC-ED3', 'PT3', '23', None, '2029-10-01', '2029-10-01', None, None),
        ('CL-ED4', 'RC-ED4', 'PT4', '23', None, '2030-03-01', '2030-03-01', None, None),
        ('CL-ED-OLD', 'RC-ED-OLD', 'PT3', '23', None, '2028-01-01', '2028-01-01', None, None)]  # outside 12 months
    db.executemany('INSERT INTO payer_claim(payer_claim_id, record_id, patient_id, place_of_service, type_of_bill, service_from, '
                   'service_to, admission_date, discharge_status, counts_in_metrics) VALUES (?,?,?,?,?,?,?,?,?,1)', claims)
    db.executemany('INSERT INTO payer_claim_diagnosis VALUES (?,?,?,?,?)', [
        ('CL-ED1', 'PT1', '2030-05-07', 1, 'M54.50'), ('CL-ED2', 'PT2', '2029-12-01', 1, 'M54.59'),
        ('CL-ED3', 'PT3', '2029-10-01', 1, 'M54.50'), ('CL-ED4', 'PT4', '2030-03-01', 1, 'M54.50'),
        ('CL-ED-OLD', 'PT3', '2028-01-01', 1, 'M54.50')])
    db.executemany('INSERT INTO payer_claim_line VALUES (?,?,?,?,?,?,?,1)', [
        ('LN-PT2', 'CL-PT2', 'PT2', 1, '2029-12-20', '97161', None),
        ('LN-PT3', 'CL-PT3', 'PT3', 1, '2029-12-15', '97110', None)])  # 75 days after the ED visit
    db.commit()
    db.close()


class FixtureCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / 'semantic.sqlite3'
        build_fixture(cls.db)
        cls.gdmt = router.route(cls.db, ['HF1', 'HF2', 'HF3', 'HF4', 'HF5', 'HF6', 'HF7', 'NOPE'], 'pharmacy_gdmt', AS_OF)
        cls.pt = router.route(cls.db, ['PT1', 'PT2', 'PT3', 'PT4', 'PT5'], 'virtual_pt', AS_OF)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def row(self, plan, pid):
        return next(r for r in plan['patients'] if r['patient_id'] == pid)


class CatalogTests(unittest.TestCase):
    def test_packaged_catalog_is_valid_and_has_access_context(self):
        cat = catalog.load()
        self.assertEqual(catalog.validate(cat), [])
        self.assertIn('cms_access_model', cat['contexts'])
        self.assertTrue({'pharmacy_gdmt', 'virtual_pt', 'coverage_check'} <= set(catalog.kinds(cat)))
        for p in cat['programs']:  # vendor slots are customer-configured placeholders, never named companies
            self.assertTrue(all(s['vendor_name'] is None for s in p['vendor_slots']))

    def test_invalid_catalog_is_refused(self):
        cat = catalog.load()
        cat['programs'][0]['eligibility']['gap_type'] = 'nope'
        cat['programs'] = [p for p in cat['programs'] if p['kind'] != 'coverage_check']
        problems = catalog.validate(cat)
        self.assertTrue(any('unknown gap_type' in p for p in problems))
        self.assertTrue(any('coverage_check' in p for p in problems))


class CoverageAttributionTests(FixtureCase):
    def test_active_coverage_present(self):
        r = self.row(self.gdmt, 'HF1')
        self.assertTrue(r['coverage']['active']['value'])
        self.assertEqual((r['coverage']['payer_id']['value'], r['coverage']['product']['value'],
                          r['coverage']['line_of_business']['value']), ('PAY-MCD', 'medicaid', 'medicaid'))
        self.assertEqual(r['coverage']['member_id']['sources'], ['enrollment_span:ES-HF1'])

    def test_coverage_absent_routes_to_coverage_queue(self):
        r = self.row(self.gdmt, 'HF2')  # span ended; the future span is not yet available at as_of
        self.assertFalse(r['coverage']['active']['value'])
        self.assertEqual(r['route'], 'coverage_check')
        self.assertEqual(r['program_id'], 'coverage_verification')
        self.assertEqual(r['task']['owner'], 'coverage_queue')
        self.assertIn('no enrollment span active', r['decision']['value'])

    def test_secondary_only_coverage_is_not_primary(self):
        r = self.row(self.gdmt, 'HF7')
        self.assertEqual(r['route'], 'coverage_check')
        self.assertIn('secondary', r['coverage']['reason'])

    def test_attribution_from_span(self):
        a = self.row(self.gdmt, 'HF1')['attribution']
        self.assertEqual((a['method'], a['npi']['value'], a['practice_id']['value']), ('enrollment_span', '1111111111', 'PRAC-A'))

    def test_attribution_falls_back_to_most_recent_pcp_visit(self):
        a = self.row(self.gdmt, 'HF4')['attribution']
        self.assertEqual(a['method'], 'most_recent_pcp_visit')
        self.assertEqual((a['npi']['value'], a['practice_id']['value'], a['last_pcp_visit']['value']),
                         ('4444444444', 'ORG-NEW', '2030-02-10'))  # the post-as_of visit is ignored
        self.assertIn('encounter:EN-2', a['npi']['sources'])
        self.assertIn('clinician:C2', a['npi']['sources'])

    def test_attribution_falls_back_to_patient_pcp(self):
        a = self.row(self.gdmt, 'HF7')['attribution']
        self.assertEqual((a['method'], a['practice_id']['value']), ('patient_pcp', 'PRAC-PT'))


class GapTests(FixtureCase):
    def test_gdmt_pillar_gaps_from_fills(self):
        r = self.row(self.gdmt, 'HF1')
        g = r['gap']
        self.assertEqual(sorted(g['missing_pillars']['value']), ['mra', 'sglt2i'])  # old MRA fill is outside 120 days
        self.assertEqual(sorted(g['present_pillars']['value']), ['beta_blocker', 'rasi'])
        self.assertEqual(g['pillars']['rasi']['on_therapy']['sources'], ['fill:FR-1'])
        self.assertEqual(r['route'], 'enroll')
        self.assertEqual((r['program_id'], r['vendor_slot']['slot_id']), ('pharmacy_gdmt_titration', 'gdmt_pharmacy_slot_1'))
        self.assertEqual(r['task']['priority'], 'medium')
        self.assertEqual(r['task']['due_date'], '2030-06-03')
        self.assertEqual(r['task']['script_template_id'], 'gdmt_pharmacist_intro_v1')

    def test_gdmt_med_list_counts_and_tartrate_does_not(self):
        g2 = self.row(self.gdmt, 'HF2')['gap']
        self.assertIn('mra', g2['present_pillars']['value'])
        self.assertEqual(g2['pillars']['mra']['on_therapy']['sources'], ['medication_list_entry:ML-1'])
        g4 = self.row(self.gdmt, 'HF4')['gap']  # tartrate, a discontinued SGLT2i and a stale ARB do not count
        self.assertEqual(sorted(g4['missing_pillars']['value']), ['beta_blocker', 'mra', 'rasi', 'sglt2i'])
        self.assertEqual(self.row(self.gdmt, 'HF4')['task']['priority'], 'high')

    def test_all_pillars_present_is_excluded_as_no_gap(self):
        r = self.row(self.gdmt, 'HF5')
        self.assertEqual(r['route'], 'exclude')
        self.assertEqual(r['exclusions'][0]['reason'], 'no_gap')
        self.assertIsNone(r['task'])

    def test_pt_gap_recent_ed_visit_no_pt(self):
        r = self.row(self.pt, 'PT1')
        g = r['gap']
        self.assertTrue(g['present']['value'])
        self.assertEqual((g['ed_visit_date']['value'], g['days_since_ed']['value'], g['last_pt_date']['value']), ('2030-05-07', 20, None))
        self.assertEqual(g['ed_visit_date']['sources'], ['payer_claim:RC-ED1'])
        self.assertEqual((r['route'], r['program_id']), ('enroll', 'access_virtual_pt_msk'))  # Original Medicare -> ACCESS
        self.assertIn(r['vendor_slot']['slot_id'], ('access_msk_vendor_a', 'access_msk_vendor_b'))
        self.assertEqual(r['task']['priority'], 'high')
        self.assertIn('cms_access_model', self.pt['contexts'])

    def test_pt_within_window_excludes_and_late_pt_is_still_a_gap(self):
        r2 = self.row(self.pt, 'PT2')
        self.assertEqual((r2['route'], r2['exclusions'][0]['reason']), ('exclude', 'pt_completed'))
        self.assertIn('payer_claim_line:LN-PT2', r2['gap']['pt_within_window']['sources'])
        r3 = self.row(self.pt, 'PT3')
        self.assertEqual((r3['route'], r3['program_id']), ('enroll', 'plan_benefit_virtual_pt'))  # commercial -> plan benefit
        self.assertEqual(r3['gap']['last_pt_date']['value'], '2029-12-15')
        self.assertEqual(r3['gap']['qualifying_ed_visits']['value'], 1)  # the 2028 visit is outside the lookback
        self.assertEqual(r3['task']['priority'], 'low')

    def test_admitted_ed_visit_and_age(self):
        r5 = self.row(self.pt, 'PT5')
        self.assertEqual((r5['route'], r5['exclusions'][0]['reason']), ('exclude', 'ed_visit_admitted'))
        r4 = self.row(self.pt, 'PT4')
        self.assertEqual((r4['route'], r4['exclusions'][0]['reason']), ('exclude', 'age_ineligible'))

    def test_gap_details_are_recorded_not_obeyed(self):
        plan = router.route(self.db, ['HF1'], 'pharmacy_gdmt', AS_OF, gap_details={'HF1': {'missing_pillars': ['rasi']}})
        g = plan['patients'][0]['gap']
        self.assertEqual(sorted(g['missing_pillars']['value']), ['mra', 'sglt2i'])
        self.assertIn('missing_pillars', g['caller_disagrees'])
        self.assertEqual(g['caller_details']['sources'], ['caller:gap_details'])


class ExclusionLineageSummaryTests(FixtureCase):
    def test_deceased_and_hospice_and_unknown(self):
        r3 = self.row(self.gdmt, 'HF3')
        self.assertEqual((r3['route'], r3['exclusions'][0]['reason']), ('exclude', 'deceased'))
        self.assertEqual(r3['exclusions'][0]['sources'], ['enrollment_span:ES-HF3'])
        r6 = self.row(self.gdmt, 'HF6')
        self.assertEqual((r6['route'], r6['exclusions'][0]['reason']), ('exclude', 'hospice'))
        self.assertEqual(r6['exclusions'][0]['sources'], ['payer_claim:RC-HOSP'])
        self.assertEqual(self.row(self.gdmt, 'NOPE')['exclusions'][0]['reason'], 'unknown_patient')

    def test_every_field_carries_lineage(self):
        def walk(o, path=''):
            if isinstance(o, dict):
                if set(o) == {'value', 'sources'}:
                    self.assertIsInstance(o['sources'], list, path)
                    for s in o['sources']:
                        self.assertRegex(s, r'^[a-z_]+:.+', path)
                    return 1
                return sum(walk(v, f'{path}.{k}') for k, v in o.items())
            if isinstance(o, list):
                return sum(walk(v, path) for v in o)
            return 0
        self.assertGreater(walk(self.gdmt['patients']), 100)
        req = self.row(self.gdmt, 'HF1')['task']['required_fields']
        self.assertEqual(req['member_id'], {'value': 'M1', 'sources': ['enrollment_span:ES-HF1']})
        self.assertIn('fill:FR-1', self.row(self.gdmt, 'HF1')['decision']['sources'])

    def test_summary_counts(self):
        s = self.gdmt['summary']
        self.assertEqual(s['patients'], 8)
        self.assertEqual(s['by_route'], {'coverage_check': 2, 'enroll': 2, 'exclude': 4})
        self.assertEqual(s['by_exclusion_reason'], {'deceased': 1, 'hospice': 1, 'no_gap': 1, 'unknown_patient': 1})
        self.assertEqual(s['by_program'], {'pharmacy_gdmt_titration': 2, 'coverage_verification': 2})
        self.assertEqual(s['by_payer']['PAY-COM'], 2)  # HF4, HF5 (HF2's commercial span has ended)
        self.assertEqual(s['by_practice']['PRAC-A'], 2)  # HF1, HF6 (HF3's span ended at death)
        self.assertEqual(s['tasks'], 4)
        self.assertEqual(self.pt['summary']['by_route'], {'enroll': 2, 'exclude': 3})

    def test_deterministic_and_csv(self):
        again = router.route(self.db, ['HF1', 'HF2', 'HF3', 'HF4', 'HF5', 'HF6', 'HF7', 'NOPE'], 'pharmacy_gdmt', AS_OF)
        self.assertEqual(router.digest(again), router.digest(self.gdmt))
        rows = list(csv.DictReader(io.StringIO(router.tasks_csv(self.gdmt))))
        self.assertEqual(len(rows), 4)
        hf1 = next(r for r in rows if r['patient_id'] == 'HF1')
        self.assertEqual((hf1['owner'], hf1['priority'], hf1['gap']), ('gdmt_pharmacy_slot_1', 'medium', 'missing mra,sglt2i'))

    def test_candidates(self):
        self.assertEqual(router.candidates(self.db, 'pharmacy_gdmt', AS_OF), ['HF1', 'HF2', 'HF3', 'HF4', 'HF5', 'HF6', 'HF7'])
        self.assertEqual(router.candidates(self.db, 'virtual_pt', AS_OF), ['PT1', 'PT2', 'PT3', 'PT4', 'PT5'])

    def test_as_of_defaults_to_meta_and_unknown_kind_refused(self):
        self.assertEqual(router.route(self.db, ['HF1'], 'pharmacy_gdmt')['as_of'], AS_OF)
        with self.assertRaises(ValueError):
            router.route(self.db, ['HF1'], 'teleportation')


class RequestTests(unittest.TestCase):
    def test_plan_requests_and_cohort_split(self):
        q = ('Find all heart failure patients not on full GDMT, then create a plan to support those patients with agents who '
             'can sign them up for a pharmacy GDMT program')
        p = request.parse(q)
        self.assertEqual((p['program_kind'], p['cohort_question']), ('pharmacy_gdmt', 'Find all heart failure patients not on full GDMT'))
        p = request.parse('ED visits discharged with low back pain and no PT code within 60 days. Create a plan to support those '
                          'patients with agents who can sign them up for virtual PT with CMS ACCESS model vendors')
        self.assertEqual(p['program_kind'], 'virtual_pt')
        self.assertTrue(p['cohort_question'].endswith('ED visits discharged with low back pain and no PT code within 60 days'))
        self.assertEqual(request.parse('Create a plan for heart failure patients not on GDMT')['cohort_question'],
                         'List the patients: heart failure patients not on GDMT')

    def test_non_plan_questions_are_untouched(self):
        for q in ('How many patients are enrolled in Medicare?', 'Which beneficiaries cannot enroll because the payer is ineligible?',
                  'What is the most common route of administration?', 'Generate an outreach list of diabetics'):
            self.assertFalse(request.is_plan_request(q), q)
        self.assertFalse(request.is_plan_request('create a plan for HF patients', 'patient_set'))
        self.assertTrue(request.is_plan_request('anything', 'plan'))


@contextmanager
def _fake_factory(reply, seen):
    def send(body):
        seen.append(body)
        return {'content': [{'type': 'text', 'text': json.dumps(reply)}]}, 0.0012, False
    yield send


class NarrativeTests(FixtureCase):
    def test_off_by_default_and_without_sender(self):
        self.assertEqual(narrative.draft(self.gdmt)['status'], 'disabled')
        self.assertEqual(narrative.draft(self.gdmt, enabled=True)['status'], 'disabled')

    def test_draft_is_prose_only(self):
        before = router.digest(self.gdmt)
        seen = []
        reply = {'narrative': 'Two patients go to pharmacy outreach.', 'scripts': {'gdmt_pharmacist_intro_v1': 'Hi [FIRST_NAME]',
                                                                                    'invented_template': 'x'}}
        out = narrative.draft(self.gdmt, enabled=True, send_factory=lambda: _fake_factory(reply, seen))
        self.assertEqual(out['status'], 'drafted')
        self.assertEqual(set(out['scripts']), {'gdmt_pharmacist_intro_v1'})
        self.assertEqual(router.digest(self.gdmt), before)
        body = json.dumps(seen[0])
        self.assertTrue(seen[0]['model'].startswith('claude-haiku'))
        for pid in ('HF1', 'HF4', 'M1'):  # identifiers never leave in the prompt
            self.assertNotIn(pid, body)

    def test_bad_reply_fails_soft(self):
        out = narrative.draft(self.gdmt, enabled=True, send_factory=lambda: _fake_factory('not json', []))
        self.assertEqual(out['status'], 'failed')


class WiringTests(FixtureCase):
    def test_build_uses_cohort_path_then_falls_back(self):
        q = 'Find all heart failure patients not on full GDMT, then create a plan to sign them up for a pharmacy GDMT program'
        asked = []

        def cohort(cq):
            asked.append(cq)
            return {'answer': {'patient_ids': ['HF1', 'HF4']}, 'pipeline': 'full', 'cost_usd': 0.01}
        out = plan_answer.build(self.db, q, as_of=AS_OF, cohort=cohort)
        self.assertEqual(asked, ['Find all heart failure patients not on full GDMT'])
        self.assertEqual((out['cohort']['source'], out['plan']['summary']['patients']), ('answer_path', 2))
        env = plan_answer.envelope(out)
        self.assertEqual((env['answer_type'], env['answer']['patient_ids']), ('plan', ['HF1', 'HF4']))
        self.assertEqual(env['plan']['narrative']['status'], 'disabled')
        out = plan_answer.build(self.db, q, as_of=AS_OF, cohort=lambda cq: {'answer': None})
        self.assertEqual((out['cohort']['source'], out['plan']['summary']['patients']), ('deterministic_candidates', 7))

    def test_together_compose_plan_and_workspace_stack_envelope(self):
        from clinical_core.agere.clinical import together
        from clinical_core.agere.onboarding import workspace_stack
        stack = types.SimpleNamespace(path=self.db.parent, retriever=types.SimpleNamespace(meta={'snapshot': {'as_of': AS_OF}}))
        calls = []

        def fake_compose(stack_, question, *, planning='auto', answer_type=None):
            calls.append((question, answer_type))
            return {'answer': {'status': 'answered', 'answer_type': 'patient_set', 'patient_ids': ['PT1', 'PT2', 'PT3']},
                    'graph': {'nodes': []}, 'route': 'semantic', 'together': {'decision': 'analyst'},
                    'planning_economics': {'new_api_estimate_usd': 0.02}, 'planner_receipt': {'route': 'semantic'}}
        q = ('ED visits discharged with low back pain and no PT code within 60 days, then create a plan to sign them up for '
             'virtual PT with CMS ACCESS model vendors')
        with mock.patch.object(together, 'compose_together', fake_compose):
            result = together._compose_plan(stack, q)
        self.assertEqual(calls, [('List the patients: ED visits discharged with low back pain and no PT code within 60 days', 'patient_set')])
        env = workspace_stack.envelope(result)
        self.assertEqual((env['answer_type'], env['pipeline'], env['decision']), ('plan', 'full', 'plan'))
        self.assertEqual(env['answer']['patient_ids'], ['PT1', 'PT3'])  # PT2 already had PT
        self.assertEqual(env['plan']['summary']['by_route'], {'enroll': 2, 'exclude': 1})
        self.assertEqual(env['cost_usd'], 0.02)
        self.assertIn('result', env)  # the cohort step's receipts stay attached
        # non-plan results keep the existing envelope
        plain = workspace_stack.envelope(fake_compose(None, 'x'), answer_type='patient_set')
        self.assertNotIn('plan', plain)
        self.assertEqual(plain['answer'], {'patient_ids': ['PT1', 'PT2', 'PT3']})

    def test_compose_together_dispatches_plan_requests_only(self):
        from clinical_core.agere.clinical import together
        with mock.patch.object(together, '_compose_plan', return_value={'plan': 1}) as cp:
            self.assertEqual(together.compose_together(object(), 'q', answer_type='plan'), {'plan': 1})
            self.assertEqual(together.compose_together(object(), 'Find HF patients, then create a plan to enroll them'), {'plan': 1})
            self.assertEqual(cp.call_count, 2)


def _workspace(root, slug='plan-clinic', semantic=None):
    d = Path(root) / slug
    d.mkdir(parents=True)
    (d / 'workspace.json').write_text(json.dumps({'schema': 'agere-workspace/1', 'slug': slug, 'name': slug, 'status': 'ready',
                                                   'source': 'sqlite:///nowhere.sqlite', 'as_of': AS_OF, 'steps': {}, 'decisions': {}}))
    build_fixture(d / 'semantic.sqlite3')
    return d


class WorkspaceAndApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        _workspace(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_workspace_ask_routes_plan_requests_through_the_cohort_path(self):
        from clinical_core.agere.onboarding.workspace import Workspace
        ws = Workspace('plan-clinic', self.root)
        seen = []

        def fake_hybrid(self_, question, *, answer_type=None, send_factory=None):
            seen.append((question, answer_type))
            return {'answer': {'patient_ids': ['HF1', 'HF2', 'HF3']}, 'answer_type': answer_type, 'decision': 'unanimous',
                    'interpretation': 'hybrid', 'cost_usd': 0.05}
        q = 'Find all heart failure patients not on full GDMT, then create a plan to sign them up for a pharmacy GDMT program'
        with mock.patch.object(Workspace, '_ask_hybrid', fake_hybrid):
            out = ws.ask(q)
            plain = ws.ask('How many heart failure patients are there?')
        self.assertEqual(seen[0], ('Find all heart failure patients not on full GDMT', 'patient_set'))
        self.assertEqual(out['answer_type'], 'plan')
        self.assertEqual(out['plan']['summary']['by_route'], {'coverage_check': 1, 'enroll': 1, 'exclude': 1})
        self.assertEqual(out['cost_usd'], 0.05)
        self.assertNotIn('plan', plain)
        self.assertEqual(seen[1], ('How many heart failure patients are there?', None))
        hist = [json.loads(l) for l in (Path(self.root) / 'plan-clinic' / 'questions.jsonl').read_text().splitlines()]
        self.assertEqual(hist[-2]['decision'], 'plan')

    def test_api_plan_route_json_csv_and_errors(self):
        from clinical_core.agere import workspace_api
        code, out = workspace_api.handle('POST', '/api/workspaces/plan-clinic/plan',
                                         {'patient_ids': ['PT1', 'PT2', 'PT3'], 'program_kind': 'virtual_pt', 'as_of': AS_OF},
                                         root=self.root)
        self.assertEqual(code, 200)
        self.assertEqual((out['answer_type'], out['plan']['cohort']['source']), ('plan', 'caller'))
        self.assertEqual(out['plan']['summary']['by_route'], {'enroll': 2, 'exclude': 1})
        # a plan writes work items: viewers are refused, writers get the CSV
        self.assertEqual(workspace_api.handle('POST', '/api/workspaces/plan-clinic/plan', {'program_kind': 'pharmacy_gdmt'},
                                              root=self.root, can_write=False)[0], 403)
        code, out = workspace_api.handle('POST', '/api/workspaces/plan-clinic/plan', {'program_kind': 'pharmacy_gdmt', 'format': 'csv'},
                                         root=self.root)
        self.assertEqual(code, 200)
        rows = list(csv.DictReader(io.StringIO(out['csv'])))
        self.assertEqual(len(rows), out['summary']['tasks'])
        self.assertEqual(out['summary']['patients'], 7)  # deterministic HF candidates
        self.assertEqual(workspace_api.handle('POST', '/api/workspaces/plan-clinic/plan', {}, root=self.root)[0], 400)
        self.assertEqual(workspace_api.handle('POST', '/api/workspaces/plan-clinic/plan',
                                              {'patient_ids': ['PT1'], 'program_kind': 'nope'}, root=self.root)[0], 400)
        self.assertEqual(workspace_api.handle('POST', '/api/workspaces/missing/plan', {'program_kind': 'virtual_pt'}, root=self.root)[0], 404)


if __name__ == '__main__':
    unittest.main()


class ActTests(unittest.TestCase):
    """act.py no longer marks every step done: each step advances only on a simulated-counterparty receipt (see
    test_action_backbone.py for outcomes, follow-ups, persistence and completion)."""

    def test_steps_advance_only_on_receipts(self):
        from clinical_core.agere.plans.act import act
        plan = {'as_of': '2030-05-27', 'program_kind': 'virtual_pt',
                'programs': [{'id': 'access_virtual_pt_msk', 'name': 'ACCESS MSK'}],
                'patients': [
                    {'patient_id': 'P1', 'route': 'enroll', 'program_id': 'access_virtual_pt_msk',
                     'vendor_slot': {'slot_id': 'access_msk_vendor_a', 'display': 'ACCESS MSK vendor A'},
                     'coverage': {'active': {'value': True}, 'product': {'value': 'medicare_ffs'}},
                     'attribution': {'npi': {'value': '1234567893'}}},
                    {'patient_id': 'P2', 'route': 'coverage_check', 'program_id': 'coverage_verification',
                     'coverage': {'active': {'value': False}}, 'attribution': {}},
                    {'patient_id': 'P3', 'route': 'exclude'}]}
        out = act(plan, workspace='test')
        self.assertEqual(out['summary']['actions'], 2)
        self.assertEqual(out['summary']['steps_done_without_receipt'], 0)
        a = next(x for x in out['actions'] if x['patient_id'] == 'P1')
        self.assertEqual((a['vendor'], a['counterparty_id']), ('ACCESS MSK vendor A', 'sim:access_msk_vendor_a'))
        self.assertEqual(a['steps'][0]['step'], 'patient_outreach')
        for s in a['steps']:
            if s['status'] in ('done', 'failed', 'moved'):
                self.assertTrue(s['receipt']['receipt_id'].startswith('RCPT-'))
                self.assertTrue(s['receipt']['simulated'])
        c = next(x for x in out['actions'] if x['patient_id'] == 'P2')
        self.assertIn(c['state'], ('coverage_found', 'coverage_not_found'))
        self.assertTrue(c['followups'])  # found -> reroute, not found -> enrollment assistance
        self.assertEqual(act(plan, workspace='test'), out)  # deterministic
