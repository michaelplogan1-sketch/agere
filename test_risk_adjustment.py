"""risk_adjustment: CMS-HCC V24/V28 risk scores, diagnosis eligibility, recapture gaps and suspects."""
import csv, json, os, re, shutil, sqlite3, subprocess, tempfile, unittest
from pathlib import Path

from clinical_core.agere.clinical import (analysis_graph, capability_registry, graph_contracts, graph_planner, local_compiler,
                                          risk_adjustment as ra, semantic_layer, source_profile)
from clinical_core.agere.clinical.analysis_graph import build_answer, validate
from test_aggregate_explain import build

ROOT = Path(__file__).resolve().parent
TABLES = ROOT / 'config' / 'agere' / 'risk_adjustment'
AS_OF = '2030-05-27'
REAL_V20 = Path(os.environ.get('RISK_ADJUSTMENT_STACK') or next(
    (p / 'artifacts' / 'agere-healthcare-v20' / 'stack' for p in (ROOT, *ROOT.parents) if (p / 'artifacts' / 'agere-healthcare-v20' / 'stack').exists()),
    ROOT / 'artifacts' / 'agere-healthcare-v20' / 'stack'))
V24_FACTORS = {r['_NAME_']: float(r['COL1']) for r in csv.DictReader(open(TABLES / 'v24_relative_factors.csv', encoding='utf-8-sig'))}
SEGMENT_COLUMNS = {'community_non_dual': ('COMMUNITY_NA', 'COMMUNITY_ND'), 'community_full_dual': ('COMMUNITY_FBA', 'COMMUNITY_FBD'),
                   'community_partial_dual': ('COMMUNITY_PBA', 'COMMUNITY_PBD'), 'institutional': ('INSTITUTIONAL', 'INSTITUTIONAL')}


class Tables(unittest.TestCase):
    def test_every_table_is_cited_with_checksum(self):
        import hashlib
        man = json.loads((TABLES / 'manifest.json').read_text())
        self.assertTrue(man['tables'])
        for name, t in man['tables'].items():
            with self.subTest(name=name):
                self.assertTrue(t['url'].startswith('https://www.cms.gov/'))
                self.assertIn(t['payment_year'], (2025, 2026))
                self.assertEqual(hashlib.sha256((TABLES / name).read_bytes()).hexdigest(), t['sha256'])
        self.assertEqual(man['checks']['v28_py2025_sas_vs_py2026_python_coefficients']['mismatches'], 0)
        pay = json.loads((TABLES / 'payment_parameters.json').read_text())['payment_years']
        self.assertEqual(pay['2025']['blend'], {'V24': 0.33, 'V28': 0.67})
        self.assertEqual(pay['2026']['normalization'], {'V28': 1.067})
        for y in pay.values(): self.assertTrue(y['url'].startswith('https://www.cms.gov/files/document/'))


class CMSReference(unittest.TestCase):
    """Cases scored by the official CMS Python software (CMS_HCC_v28_2026_T_package_v3); reproduced exactly."""
    def test_v28_matches_cms_software_for_every_segment(self):
        ref = json.loads((TABLES / 'validation_cms_python_v28_py2026.json').read_text())
        self.assertGreaterEqual(len(ref['cases']), 3)
        for c in ref['cases']:
            age = c['age_at_2026_02_01']
            for segment, (aged, disabled) in SEGMENT_COLUMNS.items():
                with self.subTest(case=c['id'], segment=segment):
                    s = ra.score('V28', age, c['sex'], c['icd10'], segment, c['orec'])
                    self.assertAlmostEqual(s['raw'], c['expected_scores'][aged if age >= 65 else disabled], places=9)
                    self.assertEqual([h['hcc'] for h in s['hccs']], c['expected_hccs'])

    def test_worked_example_terms(self):
        # CMS reference case 1: female 71, E11.9 I50.22 N18.31 J44.9 I48.0 -> 2.159 (community, non-dual, aged)
        s = ra.score('V28', 71, 2, ['E11.9', 'I50.22', 'N18.31', 'J44.9', 'I48.0'])
        self.assertEqual(s['raw'], 2.159)
        self.assertEqual(sorted(s['interactions']), ['DIABETES_HF_V28', 'HF_CHR_LUNG_V28', 'HF_HCC238_V28', 'HF_KIDNEY_V28'])
        self.assertIn(('D5', 0.05), [(t['variable'], t['coefficient']) for t in s['terms']])


class Hierarchies(unittest.TestCase):
    def test_v28_diabetes_cancer_and_heart_failure(self):
        self.assertEqual([h['hcc'] for h in ra.score('V28', 70, 1, ['E11.22', 'E11.9'])['hccs']], [37])
        s = ra.score('V28', 70, 1, ['C34.90', 'C79.51'])
        self.assertEqual(([h['hcc'] for h in s['hccs']], s['dropped_by_hierarchy']), ([18], {20: 18}))
        self.assertEqual([h['hcc'] for h in ra.score('V28', 70, 1, ['I50.84', 'I50.22', 'I42.0'])['hccs']], [222])

    def test_v28_cc223_patch(self):
        self.assertEqual(ra.score('V28', 70, 1, ['Z95.811'])['hccs'], [])  # assist device alone: no HF CC -> dropped
        self.assertEqual([h['hcc'] for h in ra.score('V28', 70, 1, ['Z95.811', 'I50.22'])['hccs']], [223])

    def test_v24_hierarchies_follow_sas(self):
        self.assertEqual([h['hcc'] for h in ra.score('V24', 70, 1, ['E11.22', 'E11.9'])['hccs']], [18])
        self.assertEqual([h['hcc'] for h in ra.score('V24', 70, 1, ['N18.4', 'N18.31'])['hccs']], [137])
        s = ra.score('V24', 70, 1, ['J96.00', 'J44.9', 'J84.10'])  # COPD (111) dominates fibrosis (112); respiratory failure separate
        self.assertEqual(([h['hcc'] for h in s['hccs']], s['dropped_by_hierarchy']), ([84, 111], {112: 111}))
        self.assertIn('gCopdCF_CARD_RESP_FAIL', s['interactions'])

    def test_hierarchy_never_keeps_both_levels(self):
        for name in ra.MODELS:
            m = ra.load_model(name)
            for hcc, excluded in m['hierarchy']:
                kept, _ = ra.apply_hierarchy(m, {hcc: ['x'], **{e: ['x'] for e in excluded}})
                with self.subTest(model=name, hcc=hcc):
                    self.assertFalse(set(kept) & set(excluded))


class Interactions(unittest.TestCase):
    def test_v24_worked_example_from_cms_factor_table(self):
        """Female 71, community non-dual aged: every term looked up in C2419P1M (2020 CMS-HCC model, PY2025 software)."""
        s = ra.score('V24', 71, 2, ['E11.9', 'I50.22', 'N18.31', 'J44.9', 'I48.0'])
        names = ['F70_74', 'HCC19', 'HCC85', 'HCC96', 'HCC111', 'HCC138', 'DIABETES_CHF', 'CHF_gCopdCF', 'HCC85_gRenal_V24', 'HCC85_HCC96', 'D5']
        self.assertEqual(sorted(t['variable'] for t in s['terms']), sorted(names))
        self.assertAlmostEqual(s['raw'], round(sum(V24_FACTORS['CNA_' + n] for n in names), 3))
        self.assertEqual(s['raw'], 2.053)

    def test_disabled_segment_interaction_and_counts(self):
        s = ra.score('V28', 56, 2, ['F10.20', 'F31.9', 'G40.909'])  # under 65 -> community non-dual disabled
        self.assertEqual(s['segment'], 'CND')
        self.assertIn('gSubUseDisorder_gPsych_V28', s['interactions'])
        self.assertEqual(ra.score('V28', 70, 2, ['F10.20', 'F31.9'])['interactions'], [])  # aged segment has no such factor
        many = ra.score('V28', 85, 2, ['E11.22', 'I50.22', 'I48.0', 'J44.9', 'N18.4', 'L89.154', 'J96.00', 'F32.1', 'G40.909', 'E66.01', 'C34.90', 'I21.4'])
        self.assertEqual(many['payment_hcc_count'], 12)
        self.assertIn('D10P', [t['variable'] for t in many['terms']])

    def test_age_and_sex_edits(self):
        self.assertEqual([h['hcc'] for h in ra.score('V28', 44, 2, ['C50.911'])['hccs']], [22])
        self.assertEqual([h['hcc'] for h in ra.score('V28', 56, 2, ['C50.911'])['hccs']], [23])
        self.assertEqual([h['hcc'] for h in ra.score('V28', 70, 1, ['D66'])['hccs']], [111])
        self.assertEqual([h['hcc'] for h in ra.score('V28', 70, 2, ['D66'])['hccs']], [112])
        self.assertEqual(ra.ccs_for(ra.load_model('V24'), ['D66'], 70, 2), {48: ['D66']})  # V24I0ED3 mandatory edit
        self.assertEqual(ra.ccs_for(ra.load_model('V24'), ['F34.81'], 30, 2), {})


class PaymentMath(unittest.TestCase):
    def test_blend_normalization_and_coding_adjustment(self):
        p25, p26 = ra.payment_parameters(2025), ra.payment_parameters(2026)
        self.assertEqual(ra.payment_raf({'V24': 1.153, 'V28': 1.045}, p25), 0.941)  # normalised 1.0 each, minus 5.9%
        self.assertEqual(ra.payment_raf({'V24': 0.0, 'V28': 2.0}, p26), round(round(2.0 / 1.067, 3) * 0.941, 3))
        # PY2025: 0.33 x round(1.2/1.153) + 0.67 x round(1.5/1.045), times 0.941
        self.assertEqual(ra.payment_raf({'V24': 1.2, 'V28': 1.5}, p25), round((0.33 * 1.041 + 0.67 * 1.435) * 0.941, 3))

    def test_parameters(self):
        p = ra.validate({})
        self.assertEqual((p['payment_year'], p['coverage'], p['focus'], p['base_rate_pmpm']), (2026, 'medicare', 'all', None))
        for bad in ({'payment_year': 2019}, {'focus': 'astrology'}, {'base_rate_pmpm': -5}, {'colour': 1}, {'patient': 'x; drop'},
                    {'lookback_days': 10}, {'year': True}, {'group_by': 'zip'}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                ra.validate(bad)


# ---- fixture warehouse (semantic-layer/13 shape, only the tables the engine reads) ----------------------------------------
DDL = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE patient(patient_id TEXT PRIMARY KEY, age_reported INTEGER, sex_reported TEXT, coverage_type TEXT, coverage_status TEXT,
  pcp_clinician_id TEXT, pcp_practice_id TEXT, birth_date TEXT);
CREATE TABLE claim(claim_id TEXT PRIMARY KEY, patient_id TEXT, payer TEXT, service_date TEXT, final_status TEXT, principal_diagnosis TEXT,
  diagnosis_codes TEXT, place_of_service TEXT, type_of_bill TEXT, cpt TEXT, hcpcs TEXT);
CREATE TABLE claim_line(record_id TEXT, claim_id TEXT, patient_id TEXT, cpt TEXT, hcpcs TEXT);
CREATE TABLE visit(encounter_id TEXT, patient_id TEXT, type TEXT, status TEXT, event_date TEXT, attending_clinician_id TEXT, in_person INTEGER);
CREATE TABLE encounter_diagnosis(encounter_id TEXT, patient_id TEXT, rank INTEGER, icd10 TEXT, type TEXT, record_id TEXT);
CREATE TABLE clinician(clinician_id TEXT PRIMARY KEY, organization_id TEXT, specialty TEXT);
CREATE TABLE measurement(record_id TEXT, patient_id TEXT, concept TEXT, name TEXT, value_canonical REAL, event_date TEXT, loinc TEXT);
CREATE TABLE medication(record_id TEXT, patient_id TEXT, evidence TEXT, drug_name TEXT, drug_class TEXT, event_date TEXT);
CREATE TABLE note_fact(patient_id TEXT, record_id TEXT, fact_type TEXT, value, negated INTEGER, temporality TEXT, event_date TEXT, evidence_text TEXT);
CREATE TABLE record(record_id TEXT PRIMARY KEY, patient_id TEXT, stream TEXT, event_date TEXT, payload TEXT);
CREATE TABLE condition(record_id TEXT, patient_id TEXT, source_table TEXT, icd10 TEXT, label TEXT, onset TEXT, event_date TEXT,
  recorded_date TEXT, chronic INTEGER);
"""


def fixture(legacy=False):
    root = Path(tempfile.mkdtemp())
    db = sqlite3.connect(root / 'semantic.sqlite3')
    if legacy:  # v1.23 shape: no diagnosis codes anywhere
        db.executescript("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT); CREATE TABLE patient(patient_id TEXT, age_reported INTEGER, "
                         "sex_reported TEXT, coverage_type TEXT, pcp_practice_id TEXT, pcp_clinician_id TEXT); "
                         "CREATE TABLE claim(claim_id TEXT, patient_id TEXT, service_date TEXT); "
                         "CREATE TABLE condition(record_id TEXT, patient_id TEXT, source_table TEXT, icd10 TEXT);")
        db.execute("INSERT INTO meta VALUES ('version','semantic-layer/12'), ('as_of', ?)", (AS_OF,))
        db.execute("INSERT INTO condition VALUES ('c1','A','conditions',NULL)")
        db.commit(); db.close()
        return root
    db.executescript(DDL)
    db.execute("INSERT INTO meta VALUES ('version','semantic-layer/13'), ('as_of', ?)", (AS_OF,))
    pts = [('A', '1955-03-01', 'F', 'medicare', 'PR1', 'drA'), ('B', '1950-07-01', 'M', 'medicare', 'PR1', 'drA'),
           ('C', '1948-01-15', 'F', 'medicare', 'PR2', 'drB'), ('D', '1990-01-01', 'M', 'commercial', 'PR2', 'drB'),
           ('E', '1952-05-05', 'F', 'medicare', 'PR2', 'drB')]
    db.executemany('INSERT INTO patient(patient_id, birth_date, sex_reported, coverage_type, pcp_practice_id, pcp_clinician_id) VALUES (?,?,?,?,?,?)', pts)
    db.executemany('INSERT INTO clinician VALUES (?,?,?)', [('fm', 'PR1', 'Family Medicine'), ('pt', 'PR1', 'Physical Therapy')])
    claims = [  # (id, patient, date, status, dx list, pos, tob, line cpt)
        ('c1', 'A', '2028-03-01', 'paid', ['E11.22'], '11', None, '99213'),
        ('c2', 'A', '2029-02-01', 'paid', ['E11.22', 'I50.22'], '11', None, '99214'),
        ('c3', 'A', '2030-02-01', 'paid', ['E11.22'], '11', None, '99213'),
        ('c4', 'A', '2029-04-01', 'paid', ['C34.90'], '81', None, '99213'),       # independent lab: excluded
        ('c5', 'A', '2029-05-01', 'denied', ['G20'], '11', None, '99213'),       # denied: excluded
        ('c6', 'A', '2029-06-01', 'paid', ['F20.9'], '11', None, '80048'),       # lab code only: excluded
        ('c7', 'B', '2029-07-01', 'paid', ['J44.9'], None, '0131', '99284'),     # outpatient facility with eligible CPT
        ('c8', 'B', '2029-08-01', 'paid', ['I21.4'], None, '0111', None),        # inpatient facility
        ('c9', 'B', '2029-09-01', 'paid', ['N18.4'], None, '0811', None),        # hospice bill type: excluded
        ('c10', 'E', '2029-03-03', 'paid', ['E11.9'], '11', None, '99213'),
    ]
    for cid, pid, day, st, dx, pos, tob, cpt in claims:
        db.execute('INSERT INTO claim VALUES (?,?,?,?,?,?,?,?,?,?,?)', (cid, pid, 'PAY1', day, st, dx[0], json.dumps(dx), pos, tob, None, None))
        if cpt: db.execute('INSERT INTO claim_line VALUES (?,?,?,?,?)', (cid + 'l', cid, pid, cpt, None))
    visits = [('v1', 'B', 'telephone', '2029-02-02', 'fm', 0, 'F32.1'), ('v2', 'B', 'office', '2029-02-03', 'pt', 1, 'G35'),
              ('v3', 'B', 'office', '2029-02-04', 'fm', 1, 'I48.0'), ('v4', 'B', 'office', '2027-02-04', 'fm', 1, 'G30.9')]
    for enc, pid, t, day, clin, person, icd in visits:
        db.execute('INSERT INTO visit VALUES (?,?,?,?,?,?,?)', (enc, pid, t, 'completed', day, clin, person))
        db.execute('INSERT INTO encounter_diagnosis VALUES (?,?,?,?,?,?)', (enc, pid, 1, icd, 'primary', enc + 'r'))
    meas = [('m1', 'C', 'egfr', 'eGFR', 50, '2029-01-10', '98979-8'), ('m2', 'C', 'egfr', 'eGFR', 48, '2029-06-01', '98979-8'),
            ('m3', 'C', 'hba1c', 'HbA1c', 7.1, '2029-06-01', '4548-4'), ('m4', 'C', 'weight', 'weight_kg', 130, '2029-06-01', '29463-7'),
            ('m5', 'C', None, 'height_cm', 170, None, '8302-2'), ('m6', 'E', 'hba1c', 'HbA1c', 8.0, '2029-06-01', '4548-4'),
            ('m7', 'A', 'egfr', 'eGFR', 55, '2029-01-10', '98979-8')]  # one low value only: no CKD suspect
    db.executemany('INSERT INTO measurement VALUES (?,?,?,?,?,?,?)', [(r, p, c, n, v, d, l) for r, p, c, n, v, d, l in meas])
    db.execute("INSERT INTO medication VALUES ('rx1','C','dispensed','insulin glargine','insulin','2029-07-01')")
    db.execute("INSERT INTO note_fact VALUES ('C','n1','ejection_fraction',30,0,'current','2029-08-01','LVEF 30%')")
    db.execute("INSERT INTO record VALUES ('q1','C','mood_questionnaires','2029-09-01',?)", (json.dumps({'instrument': 'PHQ-9', 'total': 14}),))
    db.execute("INSERT INTO condition VALUES ('pl1','C','conditions','G20.A1','Parkinson disease','2020-01-01','2020-01-01','2020-01-01',1)")
    db.commit(); db.close()
    return root


class Engine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = fixture()
        cls.report = ra.execute(cls.path, None, {'base_rate_pmpm': 1000}, AS_OF)
        cls.by = {p['patient_id']: p for p in cls.report['patients']}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.path, ignore_errors=True)

    def test_scope_and_period(self):
        self.assertEqual(self.report['status'], 'answered')
        self.assertEqual(set(self.by), {'A', 'B', 'C', 'E'})  # commercial D out of the default Medicare scope
        self.assertEqual(self.report['period']['collection_year'], 2029)
        self.assertEqual(self.by['A']['age'], 74)  # as of 2030-02-01

    def test_eligibility_filtering(self):
        a, b = self.by['A'], self.by['B']
        self.assertEqual({h['hcc'] for h in a['hccs']['V28']}, {37, 226})  # lab-POS cancer, denied G20 and lab-only F20.9 excluded
        self.assertEqual({h['hcc'] for h in b['hccs']['V28']}, {280, 228, 238})  # facility claims + face-to-face visit
        reasons = {r['reason'] for r in self.report['diagnosis_filter'] if not r['eligible']}
        for want in ('place of service 81 (independent laboratory)', 'denied claim', 'audio-only telephone',
                     'provider type Physical Therapy not acceptable', 'bill type 0811 not risk-adjustment eligible'):
            self.assertIn(want, reasons)
        self.assertTrue(any(r.startswith('professional claim without CY') for r in reasons))

    def test_recapture(self):
        a = self.by['A']
        self.assertEqual(a['prior_year_hccs'], [37])
        self.assertEqual(a['not_recaptured_from_prior'], [])
        gaps = {g['hcc']: g for g in a['open_recapture_gaps']}
        self.assertEqual(set(gaps), {226})  # diabetes recaptured in 2030, heart failure not yet
        g = gaps[226]
        self.assertEqual((g['last_supporting']['date'], g['last_supporting']['ref'], g['last_supporting']['icd10']), ('2029-02-01', 'c2', 'I5022'))
        self.assertGreater(g['raf_delta'], 0)
        self.assertEqual(g['annual_value'], round(g['raf_delta'] * 1000 * 12, 2))
        rc = self.report['population']['recapture']
        self.assertEqual((rc['prior_year_hccs'], rc['recaptured_in_collection_year']), (1, 1))

    def test_suspects_cite_evidence(self):
        c = {s['category']: s for s in self.by['C']['suspects']}
        self.assertEqual(set(c), {'ckd_egfr', 'diabetes_a1c', 'diabetes_medication', 'hf_ejection_fraction', 'morbid_obesity',
                                  'depression_screen', 'documented_not_coded'})
        self.assertEqual([e['record_id'] for e in c['ckd_egfr']['evidence']], ['m1', 'm2'])
        self.assertEqual((c['ckd_egfr']['illustrative_icd10'], c['ckd_egfr']['hcc']), ('N1831', 329))
        self.assertEqual(c['morbid_obesity']['evidence'][0]['value'], 45.0)
        self.assertEqual(c['hf_ejection_fraction']['evidence'][0]['record_id'], 'n1')
        self.assertEqual(c['depression_screen']['evidence'][0]['record_id'], 'q1')
        self.assertEqual((c['documented_not_coded']['hcc'], c['documented_not_coded']['evidence'][0]['record_id']), (199, 'pl1'))
        for s in c.values():
            self.assertTrue(s['evidence']); self.assertIn('reading', s)
        self.assertNotIn('diabetes_a1c', {s['category'] for s in self.by['E']['suspects']})  # coded diabetic
        self.assertNotIn('ckd_egfr', {s['category'] for s in self.by['A']['suspects']})  # one low eGFR is not persistent

    def test_population_and_answer(self):
        pop = self.report['population']
        self.assertEqual(pop['patients_in_scope'], 4)
        self.assertEqual({r['group'] for r in pop['by']['practice']}, {'PR1', 'PR2'})
        self.assertTrue(0 < pop['coding_completeness']['rate'] <= 1)
        plan = validate({'interpretation': 'What is the average RAF by practice?', 'coverage': 'supported_observation', 'unmet_requirements': [],
                         'nodes': [{'id': 'answer', 'operation': 'risk_adjustment', 'inputs': [], 'purpose': 'raf',
                                    'parameters': {'focus': 'population', 'group_by': 'practice'}}]}, AS_OF)
        report = ra.execute(self.path, None, plan['nodes'][0]['parameters'], AS_OF)
        answer = build_answer(plan, [{'node': plan['nodes'][0], 'result': report}], {}, plan['interpretation'])
        self.assertEqual((answer['status'], answer['answer_type']), ('answered', 'table'))
        self.assertEqual({r['group'] for r in answer['table']}, {'PR1', 'PR2'})
        self.assertIn('ILLUSTRATIVE base rate', answer['value'])
        self.assertEqual(analysis_graph.benchmark_references(answer, operation='risk_adjustment'), [])
        json.dumps(answer)
        sub = ra.execute(self.path, ['A'], {'focus': 'recapture'}, AS_OF)
        self.assertEqual([p['patient_id'] for p in sub['patients']], ['A'])
        self.assertEqual(ra.summarize(sub)[1][0]['hcc'], 226)

    def test_graph_validation(self):
        plan = {'interpretation': 'q', 'coverage': 'supported_observation', 'unmet_requirements': [],
                'nodes': [{'id': 'answer', 'operation': 'risk_adjustment', 'inputs': [], 'purpose': 'raf', 'parameters': {'year': 2031}}]}
        with self.assertRaises(ValueError): validate(plan, AS_OF)
        plan['nodes'][0]['parameters'] = {'focus': 'nope'}
        with self.assertRaises(ValueError): validate(plan, AS_OF)

    def test_legacy_build_says_why(self):
        path = fixture(legacy=True)
        try:
            r = ra.execute(path, None, {}, AS_OF)
            self.assertEqual(r['status'], 'unsupported')
            self.assertIn('no ICD-10 diagnosis codes', r['data_gaps'][0])
            plan = {'interpretation': 'q', 'coverage': 'supported_observation', 'unmet_requirements': [],
                    'nodes': [{'id': 'answer', 'operation': 'risk_adjustment', 'inputs': [], 'purpose': 'raf', 'parameters': ra.validate({})}]}
            answer = build_answer(plan, [{'node': plan['nodes'][0], 'result': r}], {}, 'q')
            self.assertEqual(answer['status'], 'unsupported')
            self.assertTrue(answer['missing'])
        finally:
            shutil.rmtree(path, ignore_errors=True)


class Routing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        patients = {f'p{i}': {'age_reported': 70, 'sex_reported': 'F'} for i in range(4)}
        recs = [(f'p{i}', 'coverage', {'coverage_type': 'medicare', 'event_date': '2026-01-01'}) for i in range(4)]
        recs += [('p0', 'labs', {'test': 'HbA1c', 'value': 7, 'unit': '%', 'event_date': '2028-01-01'}),
                 ('p0', 'adt', {'event': 'emergency_registration', 'event_date': '2028-01-01'}),
                 ('p0', 'encounters', {'type': 'office', 'event_date': '2028-01-01', 'organization_id': 'PR1'}),
                 ('p0', 'medical_claims', {'claim_id': 'C1', 'service_date': '2028-01-01', 'event_date': '2028-01-01', 'status': 'paid',
                                           'principal_diagnosis': 'E11.9', 'diagnosis_codes': ['E11.9'], 'source_system': 'PAY1',
                                           'billed_cents': 10000, 'allowed_cents': 8000, 'payer_paid_cents': 8000})]
        cls.legacy_path = build(recs[:-1], patients)  # same roster without ICD-10 on claims (v1.23 shape)
        semantic_layer.build(cls.legacy_path)
        cls.legacy_profile = source_profile.load(cls.legacy_path)
        cls.path = build(recs, patients)
        semantic_layer.build(cls.path)
        cls.profile = source_profile.load(cls.path)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.path, ignore_errors=True)
        shutil.rmtree(cls.legacy_path, ignore_errors=True)

    def compile(self, q):
        return local_compiler.compile(q, self.profile, '2029-08-15')

    def test_build_without_diagnosis_codes_stays_unsupported(self):
        g, info = local_compiler.compile("What's the average RAF score for our Medicare members?", self.legacy_profile, '2029-08-15')
        self.assertEqual((g['coverage'], info['route']), ('unsupported', 'local_unsupported'))
        self.assertIn('CMS-HCC', ' '.join(g['unmet_requirements']))

    def test_phrasings(self):
        cases = {'What is the average RAF score by practice?': ('population', 'practice'),
                 'Which patients have open HCC recapture gaps this year?': ('recapture', 'none'),
                 'Show suspect conditions without a diagnosis code': ('suspects', 'none'),
                 'What is our coding completeness rate?': ('population', 'none'),
                 'Show the CMS-HCC risk score distribution for our Medicare Advantage members': ('population', 'none'),
                 'Where are our biggest coding gaps?': ('all', 'none'),
                 'Which HCCs were coded last year but not yet recaptured?': ('recapture', 'none'),
                 'List documentation gaps by PCP': ('suspects', 'pcp'),
                 'What is the RAF for patient P-1001?': ('raf', 'none'),
                 'Show HCC risk scores by payer for PY2025': ('population', 'payer')}
        for q, (focus, gb) in cases.items():
            with self.subTest(q=q):
                g, info = self.compile(q)
                self.assertIsNotNone(g, info)
                node = g['nodes'][-1]
                self.assertEqual(node['operation'], 'risk_adjustment', info)
                self.assertEqual((node['parameters']['focus'], node['parameters']['group_by']), (focus, gb))
        g, _ = self.compile('What is the RAF for patient P-1001?')
        self.assertEqual(g['nodes'][-1]['parameters']['patient'], 'P-1001')
        g, _ = self.compile('Show HCC risk scores by payer for PY2025')
        self.assertEqual(g['nodes'][-1]['parameters']['payment_year'], 2025)
        g, _ = self.compile('What are our recapture opportunities at $1,150 PMPM?')
        self.assertEqual(g['nodes'][-1]['parameters']['base_rate_pmpm'], 1150.0)

    def test_does_not_steal_vbc_risk_or_other_questions(self):
        for q in ('What is the average risk score by practice?', 'Show risk scores by PCP'):
            with self.subTest(q=q):
                g, info = self.compile(q)
                self.assertNotEqual(g['nodes'][-1]['operation'] if g and g['nodes'] else None, 'risk_adjustment', info)
        for q in ('How many patients have suspected pneumonia?', 'How many patients have diabetes?'):
            with self.subTest(q=q):
                g, info = self.compile(q)
                self.assertNotEqual(g['nodes'][-1]['operation'] if g and g.get('nodes') else None, 'risk_adjustment', info)

    def test_acceptance_file(self):
        rows = json.loads((ROOT / 'bench' / 'acceptance' / 'risk_adjustment.json').read_text())
        self.assertGreaterEqual(len(rows), 8)
        for row in rows:
            with self.subTest(q=row['question']):
                self.assertEqual(row['expect_operation'], 'risk_adjustment')
                g, info = self.compile(row['question'])
                self.assertIsNotNone(g, info)
                self.assertEqual(g['nodes'][-1]['operation'], 'risk_adjustment', info)

    def test_follow_ups_compile_back(self):
        for q in ra.follow_up():
            with self.subTest(q=q):
                g, info = self.compile(q)
                self.assertEqual(g['nodes'][-1]['operation'], 'risk_adjustment', info)


class Integration(unittest.TestCase):
    def test_registered_everywhere(self):
        self.assertIn('risk_adjustment', analysis_graph.OPERATIONS)
        self.assertIn('risk_adjustment', analysis_graph.NO_BENCHMARK_OPERATIONS)
        self.assertIn('risk_adjustment', graph_contracts.TYPED)
        self.assertEqual(graph_contracts.input_contract('risk_adjustment')['count'], [0, 1])
        self.assertIn('risk_adjustment', graph_planner.CONTRACTS)
        self.assertEqual(capability_registry.PRODUCT['risk_adjustment']['population'], 'optional')
        self.assertIn('vbc', capability_registry.PRODUCT['risk_adjustment']['use'])
        schema = graph_contracts.parameter_schema('risk_adjustment', {'cohort_fields': {'record_fields': {}}, 'evidence_catalog': {}})
        self.assertEqual(set(schema['properties']['focus']['enum']), set(ra.FOCUS))
        self.assertLess(len(json.dumps(schema)), 2500)
        self.assertTrue(set(schema['properties']) <= ra.FIELDS)
        ra.validate({k: v for k, v in graph_planner.CONTRACTS['risk_adjustment']['parameters'].items() if not isinstance(v, str) or '|' not in v})
        source = Path(analysis_graph.__file__).read_text()
        block = source[source.index("if last['operation'] == 'risk_adjustment':"):]
        block = block[:block.index("\n    if last['operation']", 10)]
        self.assertTrue(block.rstrip().endswith('return answer'))
        self.assertIn('risk_adjustment.py', source[source.index('def code_identity'):source.index('def code_identity') + 2000])

    def test_router_tool_comes_from_the_registry(self):
        from clinical_core.agere.clinical import operator_router
        self.assertIn('risk_adjustment', {t['name'] for t in operator_router.menu()})
        t = operator_router.tool('risk_adjustment')
        self.assertIn('RAF', t['description'])
        self.assertIn('population', t['input_schema']['properties'])

    def test_console_renders_risk_adjustment(self):
        from clinical_core.agere import ask_view
        html = ask_view.page()
        self.assertIn('function vizRisk', html)
        self.assertIn('if(o.risk_adjustment)return vizRisk(a)', html)
        node = shutil.which('node') or str(Path.home() / '.local' / 'bin' / 'node')
        if not Path(node).exists(): self.skipTest('node not available for a syntax check')
        script = re.findall(r'<script>(.*?)</script>', html, re.S)[-1]
        tmp = Path(os.environ.get('TMPDIR', '/tmp')) / 'agere_risk_adjustment_check.js'
        tmp.write_text(script)
        out = subprocess.run([node, '--check', str(tmp)], capture_output=True, text=True)
        tmp.unlink()
        self.assertEqual(out.returncode, 0, out.stderr[:500])


def _v20_ready():
    try:
        db = sqlite3.connect(f"file:{REAL_V20 / 'semantic.sqlite3'}?mode=ro", uri=True)
        meta = dict(db.execute('SELECT key, value FROM meta')); db.close()
        return meta.get('version') == semantic_layer.VERSION
    except Exception:
        return False


@unittest.skipUnless(_v20_ready(), 'warehouse v2.0 semantic layer not present (never rebuilt here)')
class RealV20(unittest.TestCase):
    def test_smoke(self):
        r = ra.execute(REAL_V20, None, {}, AS_OF)
        self.assertEqual(r['status'], 'answered')
        pop = r['population']
        self.assertGreater(pop['patients_in_scope'], 100)
        self.assertTrue(0.2 < pop['payment_raf']['mean'] < 3)
        self.assertTrue(0 <= pop['recapture']['recapture_rate'] <= 1)
        self.assertTrue(pop['suspects'])
        self.assertTrue(any(f['reason'] == 'audio-only telephone' and not f['eligible'] for f in r['diagnosis_filter']))
        for p in r['patients'][:20]:
            for s in p['suspects']:
                self.assertTrue(s['evidence'] and all(e.get('record_id') for e in s['evidence']))


if __name__ == '__main__':
    unittest.main()
