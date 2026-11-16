"""Measure library engine on a small hand-checkable store (synthetic). Every expected number is computed by hand below.

Measurement period 2028-01-01..2028-12-31. Enrolled (active coverage from 2027-01-01): d1 d2 d3 w1 w2 c1 c2; d4 is uninsured.
Ages at 2028-12-31 from birth dates: d1 68 F, d2 58 M, d3 88 M, d4 66 F, w1 58 F, w2 63 F, c1 73 M, c2 48 F.
"""
import copy, unittest
from datetime import date, timedelta

from clinical_core.agere.clinical import measures, semantic_layer, vbc, local_compiler, source_profile, outreach
from test_aggregate_explain import build, pcp, visit

PERIOD = {'start': '2028-01-01', 'end': '2028-12-31'}
BIRTH = {'d1': ('1960-06-01', 'F'), 'd2': ('1970-01-01', 'M'), 'd3': ('1940-01-01', 'M'), 'd4': ('1962-01-01', 'F'),
         'w1': ('1970-03-01', 'F'), 'w2': ('1965-01-01', 'F'), 'c1': ('1955-01-01', 'M'), 'c2': ('1980-01-01', 'F')}


def cov(pid, kind='medicare'):
    return (pid, 'coverage', {'coverage_type': kind, 'status': 'active', 'effective_start': '2027-01-01', 'event_date': '2027-01-01'})


def cond(pid, desc, icd10, day):
    return (pid, 'conditions', {'description': desc, 'icd10': icd10, 'event_date': day})


def lab(pid, test, value, unit, day, loinc=None):
    return (pid, 'labs', {'test': test, 'value': value, 'unit': unit, 'event_date': day, **({'loinc': loinc} if loinc else {})})


def bp(pid, s, d, day):
    return (pid, 'vitals', {'blood_pressure_systolic': s, 'blood_pressure_diastolic': d, 'event_date': day})


def fill(pid, name, day, status='dispensed', days=30):
    return (pid, 'pharmacy_dispenses', {'medication_name': name, 'status': status, 'days_supply_reported': days, 'event_date': day})


def line(pid, claim, code, day, system='CPT'):
    return (pid, 'medical_claim_lines', {'claim_id': claim, 'line_number': 1, 'service_code_system': system, 'service_code': code,
                                         'service_date': day, 'event_date': day})


def stay(pid, enc, admit, discharge, principal):
    return (pid, 'encounters', {'type': 'inpatient', 'encounter_id': enc, 'event_date': admit, 'end_time': discharge + 'T10:00:00',
                                'organization_id': 'H1', 'diagnoses': [{'rank': 1, 'icd10': principal, 'type': 'principal'}]})


class MeasureLibrary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        patients = {pid: {'age_reported': 2029 - int(b[:4]), 'sex_reported': s, 'birth_date': b} for pid, (b, s) in BIRTH.items()}
        recs = [cov(p) for p in ('d1', 'd2', 'd3', 'w1', 'w2', 'c1', 'c2')] + [cov('d4', 'uninsured')]
        recs += [pcp('d1', 'drA', 'PR1'), pcp('d2', 'drB', 'PR2'), pcp('c1', 'drA', 'PR1')]
        # diabetes / hypertension
        recs += [cond('d1', 'Type 2 diabetes mellitus without complications', 'E11.9', '2027-05-01'),
                 cond('d2', 'Type 2 diabetes mellitus without complications', 'E11.9', '2028-02-01'),
                 cond('d3', 'Type 2 diabetes mellitus without complications', 'E11.9', '2027-05-01'),
                 cond('d4', 'Type 2 diabetes mellitus without complications', 'E11.9', '2027-05-01'),
                 cond('d1', 'Essential hypertension', 'I10', '2026-01-01'), cond('d2', 'Essential hypertension', 'I10', '2027-01-01')]
        recs += [lab('d1', 'HbA1c', 7.0, '%', '2028-03-01', '4548-4'), lab('d1', 'HbA1c', 9.6, '%', '2028-06-01', '4548-4'),
                 lab('d3', 'HbA1c', 6.5, '%', '2028-06-01'),
                 lab('d1', 'eGFR', 75, 'mL/min/1.73m2', '2028-06-01', '98979-8'), lab('d1', 'Microalbumin/Creatinine Ratio', 12, 'mg/g', '2028-06-01', '9318-7'),
                 lab('d2', 'eGFR', 80, 'mL/min/1.73m2', '2028-07-01', '98979-8')]
        recs += [bp('d1', 150, 85, '2028-09-01'), bp('d1', 135, 80, '2028-09-01'), bp('d1', 160, 95, '2028-02-01'), bp('d2', 145, 95, '2028-10-01')]
        # medications: d1 statin once + metformin twice; d2 metformin every 30 days all year; c1 statin returned to stock
        recs += [fill('d1', 'Atorvastatin 20 mg tablet', '2028-02-01'), fill('d1', 'Metformin 500 mg tablet', '2028-01-01'),
                 fill('d1', 'Metformin 500 mg tablet', '2028-02-01'), fill('c1', 'Atorvastatin 40 mg tablet', '2028-05-01', status='returned_to_stock')]
        recs += [fill('d2', 'Metformin 500 mg tablet', (date(2028, 1, 1) + timedelta(days=30 * i)).isoformat()) for i in range(12)]
        # screenings: w1 colonoscopy 2020 + mammogram 2027-03; c2 mammogram 2028-06 + colorectal cancer history; d2 FIT result 2028-04; w2 FIT 2027 (too old)
        recs += [line('w1', 'C1', '45378', '2020-05-01'), line('w1', 'C2', '77067', '2027-03-01'), line('c2', 'C3', '77067', '2028-06-01'),
                 line('w2', 'C4', '82274', '2027-06-01'),
                 ('d2', 'cancer_screening_results', {'test': 'FIT', 'result': 'negative', 'event_date': '2028-04-01'}),
                 cond('c2', 'Personal history of malignant neoplasm of large intestine', 'Z85.038', '2025-01-01')]
        # depression screening: d1 PHQ-9 at a primary-care visit; c1 visits but no screen
        recs += [visit('d1', 'primary_care', '2028-05-01'), ('d1', 'mood_questionnaires', {'instrument': 'PHQ-9', 'total': 3, 'event_date': '2028-05-01'})]
        # c1: ASCVD + HF + CKD; ED visits; two stays (HF then pneumonia); follow-up after first discharge only
        recs += [cond('c1', 'Chronic ischemic heart disease', 'I25.10', '2027-06-01'), cond('c1', 'Heart failure, unspecified', 'I50.9', '2027-03-01'),
                 cond('c1', 'Chronic kidney disease, stage 3', 'N18.30', '2027-04-01')]
        recs += [visit('c1', 'emergency', '2028-03-01'), visit('c1', 'primary_care', '2028-03-05'), visit('c1', 'emergency', '2028-03-04'),
                 visit('c1', 'emergency', '2028-06-01'), visit('c1', 'emergency', '2028-09-01')]
        recs += [stay('c1', 'E-S1', '2028-09-01', '2028-09-04', 'I50.9'), visit('c1', 'primary_care', '2028-09-08'),
                 stay('c1', 'E-S2', '2028-09-20', '2028-09-22', 'J18.9'),
                 ('c1', 'medication_reconciliation', {'event_date': '2028-09-10'}),
                 ('c1', 'adt', {'event': 'admission', 'event_date': '2028-09-01'})]
        recs += [visit('d1', 'emergency', '2028-04-01')]
        cls.path = build(recs, {k: v for k, v in patients.items()})
        semantic_layer.build(cls.path)

    def run_measure(self, mid, **kw):
        return measures.evaluate(self.path, mid, PERIOD, as_of='2029-08-15', **kw)

    def nd(self, mid, **kw):
        o = self.run_measure(mid, **kw)
        self.assertFalse(o['declined'], o.get('missing_inputs'))
        return o['overall']['numerator'], o['overall']['denominator'], o

    # ---- library ----
    def test_library_files_are_valid_and_cited(self):
        lib = measures.library()
        self.assertGreaterEqual(len(lib), 20)
        for m in lib.values():
            self.assertIn(m['status'], ('approximation', 'internal'))
            self.assertTrue(m['citations'] and all(c['url'].startswith('http') for c in m['citations']), m['id'])
            self.assertTrue(m['spec_year'])
            if 'HEDIS' in (m.get('certification') or '') and m['status'] == 'approximation':
                self.assertIn('not certified', m['certification'])
        sets = measures.measure_sets()
        self.assertTrue(set(sets['mssp_app_plus']['measures']) <= set(lib))

    def test_window_expressions(self):
        a = {'period_start': '2028-01-01', 'period_end': '2028-12-31', 'index': '2028-02-29', 'index_end': '2028-03-02'}
        self.assertEqual(measures.at('period_end-27m', a), '2026-09-30')
        self.assertEqual(measures.at('index+7d', a), '2028-03-07')
        self.assertEqual(measures.at('index-12m', a), '2027-02-28')
        self.assertEqual(measures.at('period_start-9y', a), '2019-01-01')

    # ---- patient measures (hand-computed) ----
    def test_glycemic_status(self):
        # denominator d1 (68) and d2 (58); d3 is 88, d4 uninsured. d1 latest 9.6 -> poor; d2 no A1c -> poor.
        self.assertEqual(self.nd('gsd_poor_control')[:2], (2, 2))
        self.assertEqual(self.nd('gsd_control_lt8')[:2], (0, 2))

    def test_controlling_blood_pressure_uses_lowest_reading_of_latest_day(self):
        n, d, o = self.nd('cbp', output='gaps')
        self.assertEqual((n, d), (1, 2))  # d1 latest day 2028-09-01: lowest 135/80 -> controlled; d2 145/95 not
        self.assertEqual([g['patient_id'] for g in o['rows']], ['d2'])

    def test_kidney_health_evaluation_needs_both_tests(self):
        n, d, o = self.nd('ked', output='gaps')
        self.assertEqual((n, d), (1, 2))  # d1 eGFR + uACR; d2 eGFR only; d3 is 88 -> outside 18-85
        self.assertIn('uACR', o['rows'][0]['evidence'])

    def test_statin_measures(self):
        self.assertEqual(self.nd('spd_statin_diabetes')[:2], (1, 2))   # d1 dispensed, d2 none
        self.assertEqual(self.nd('spc_statin_cvd')[:2], (0, 1))        # c1 ASCVD; statin returned to stock is not a fill
        self.assertEqual(self.nd('supd_statin_use_diabetes')[:2], (1, 2))  # d1 and d2 have >= 2 metformin fills

    def test_breast_cancer_screening(self):
        # women 42-74: d1 68, w1 58, w2 63, c2 48. Mammograms: w1 2027-03-01 (inside 27 months from 2026-09-30), c2 2028-06-01.
        n, d, o = self.nd('bcs', output='gaps')
        self.assertEqual((n, d), (2, 4))
        self.assertEqual(sorted(g['patient_id'] for g in o['rows']), ['d1', 'w2'])

    def test_colorectal_cancer_screening_any_modality(self):
        # 46-75: d1 d2 w1 w2 c1 c2; c2 excluded (Z85.038). w1 colonoscopy 2020 (10y), d2 FIT 2028; w2 FIT 2027 too old.
        n, d, o = self.nd('col')
        self.assertEqual((n, d, o['overall']['excluded']), (2, 5, 1))
        self.assertTrue(any('Stool DNA' in w for w in o['warnings']))  # absent routes are named, not silently zero

    def test_depression_screening(self):
        self.assertEqual(self.nd('dsf_depression_screening_followup')[:2], (1, 2))  # d1 screened, c1 visited but not screened

    def test_pdc_adherence(self):
        n, d, o = self.nd('pdc_diabetes', output='gaps')
        self.assertEqual((n, d), (1, 2))  # d2 twelve 30-day fills -> PDC 360/366; d1 two fills -> 60/366
        self.assertEqual(o['rows'][0]['patient_id'], 'd1')
        self.assertIn('PDC 0.16', o['rows'][0]['evidence'])
        self.assertTrue(self.run_measure('pdc_statins')['overall']['denominator'] == 0)  # one fill only: not eligible

    # ---- event measures ----
    def test_ed_follow_up_multiple_chronic_conditions(self):
        # c1 (HF + CKD) ED 03-01 (follow-up 03-05: met), 03-04 (inside the 8-day window: skipped), 06-01 (no follow-up),
        # 09-01 (admitted same day: excluded). d1's ED visit: no qualifying chronic conditions.
        n, d, o = self.nd('fmc_ed_followup', output='gaps')
        self.assertEqual((n, d, o['overall']['excluded']), (1, 2, 1))
        self.assertEqual(o['rows'][0]['index_date'], '2028-06-01')

    def test_readmission_and_follow_up_after_discharge(self):
        self.assertEqual(self.nd('pcr_readmissions')[:2], (1, 2))       # discharge 09-04 readmitted 09-20
        self.assertEqual(self.nd('fuh_7day_followup')[:2], (1, 2))      # visit 09-08 after the first discharge only
        self.assertEqual(self.nd('trc_medication_reconciliation')[:2], (1, 2))  # reconciliation 09-10 within 30 days of 09-04 (and of 09-22? no: before it)
        self.assertEqual(self.nd('trc_notification_inpatient_admission')[:2], (1, 2))

    def test_pqi_composites_per_100000_member_years(self):
        # 7 enrolled adults x 12 months = 7 member-years; admissions: HF (PQI 08) and pneumonia (PQI 11).
        o = self.run_measure('pqi_90_overall_composite')
        self.assertEqual((o['overall']['admissions'], o['overall']['member_years']), (2, 7.0))
        self.assertAlmostEqual(o['overall']['rate_per_100000'], round(2 / 7 * 1e5, 1))
        self.assertEqual(self.run_measure('pqi_92_chronic_composite')['overall']['admissions'], 1)
        self.assertEqual(self.run_measure('pqi_91_acute_composite')['overall']['admissions'], 1)

    # ---- declines ----
    def test_declines_name_missing_inputs(self):
        o = self.run_measure('cahps_patient_experience')
        self.assertTrue(o['declined'])
        self.assertTrue(any('CAHPS' in x for x in o['missing_inputs']))
        m = copy.deepcopy(measures.library()['bcs'])
        m['numerator']['criteria'][0].update(cpt=['77099'], streams=[])
        o = measures.evaluate(self.path, m, PERIOD, as_of='2029-08-15')
        self.assertTrue(o['declined'])
        self.assertIn('Screening mammography', o['missing_inputs'][0])
        m = copy.deepcopy(measures.library()['dsf_depression_screening_followup'])
        o = measures.evaluate(self.path, m, {'start': '2026-01-01', 'end': '2026-12-31'}, as_of='2029-08-15')
        self.assertTrue(o['declined'])  # no screens dated in 2026: a rate would count silence as care
        self.assertIn('no records dated', o['missing_inputs'][0])

    # ---- wiring ----
    def test_vbc_quality_uses_library_and_keeps_internal_measures(self):
        r = vbc.execute(self.path, None, {'metric': 'quality', 'measure': 'gsd_poor_control', 'group_by': 'practice', 'period': PERIOD}, '2029-08-15')
        rows = {x['group']: x for x in r['rows']}
        self.assertEqual((rows['PR1']['numerator'], rows['PR2']['numerator']), (1, 1))
        self.assertIn('citations', r)
        r = vbc.execute(self.path, None, {'metric': 'quality', 'measure': 'cahps_patient_experience', 'period': PERIOD}, '2029-08-15')
        self.assertTrue(r['declined'])
        r = vbc.execute(self.path, None, {'metric': 'quality', 'measure': 'mssp_app_plus', 'period': PERIOD}, '2029-08-15')
        self.assertEqual({x['measure'] for x in r['rows']}, set(measures.measure_sets()['mssp_app_plus']['measures']))
        internal = vbc.execute(self.path, None, {'metric': 'quality', 'period': PERIOD}, '2029-08-15')
        self.assertEqual({x['measure'] for x in internal['rows']}, {'diabetes_a1c_poor_control', 'bp_control', 'statin_therapy_diabetes'})
        with self.assertRaises(ValueError):
            vbc.validate({'metric': 'quality', 'measure': 'no_such_measure'})

    def test_answer_contract_for_rates_gaps_and_declines(self):
        from clinical_core.agere.clinical import analysis_graph

        def answer(params, question):
            plan = {'coverage': 'supported_observation', 'unmet_requirements': [], 'interpretation': question,
                    'nodes': [{'id': 'answer', 'operation': 'vbc', 'inputs': [], 'parameters': params}]}
            report = vbc.execute(self.path, None, {**params, 'period': PERIOD}, '2029-08-15')
            return analysis_graph.build_answer(plan, [{'result': report}], {}, question)
        a = answer({'metric': 'quality', 'measure': 'cbp'}, 'What is our controlling high blood pressure rate?')
        self.assertEqual((a['status'], a['answer_type'], a['value']), ('answered', 'value', 50.0))
        a = answer({'metric': 'quality', 'measure': 'bcs', 'output': 'gaps'}, 'Show me the breast cancer screening gaps')
        self.assertEqual((a['answer_type'], sorted(r['patient_id'] for r in a['table'])), ('table', ['d1', 'w2']))
        self.assertTrue(a['overall']['citations'])
        a = answer({'metric': 'quality', 'measure': 'cahps_patient_experience'}, 'What is our CAHPS score?')
        self.assertEqual(a['status'], 'unsupported')
        self.assertTrue(any('CAHPS' in m for m in a['missing']))

    def test_outreach_care_gaps_from_library(self):
        r = outreach.execute(self.path, None, {'capacity_patients': 10, 'measures': ['col']}, '2029-08-15')
        codes = {c['code'] for w in r['worklist'] for c in w['reasons']}
        self.assertIn('GAP_M_COL', codes)
        self.assertEqual(r['context']['measure_library']['measures'], ['col'])

    def test_compiler_routes_measure_questions(self):
        profile = source_profile.load(self.path)
        cases = {'What is our colorectal cancer screening rate by practice?': ('col', 'practice', None),
                 'Show me the breast cancer screening gaps by PCP': ('bcs', 'pcp', 'gaps'),
                 'Which members are on statins with PDC below 80%?': ('pdc_statins', 'none', 'gaps'),
                 'What is our PQI 92 rate in 2028?': ('pqi_92_chronic_composite', 'none', None),
                 'How are we doing on the APP Plus measures?': ('mssp_app_plus', 'none', None),
                 'Which practices have the most open colorectal cancer screening gaps?': ('col', 'practice', None)}
        for q, (mid, group, output) in cases.items():
            g, info = local_compiler.compile(q, profile, '2029-08-15')
            self.assertIsNotNone(g, (q, info))
            p = g['nodes'][-1]['parameters']
            self.assertEqual((g['nodes'][-1]['operation'], p['metric'], p['measure'], p['group_by'], p.get('output')), ('vbc', 'quality', mid, group, output), q)
        g, info = local_compiler.compile('What is our CAHPS score?', profile, '2029-08-15')
        self.assertEqual(g['coverage'], 'unsupported')


if __name__ == '__main__':
    unittest.main()
