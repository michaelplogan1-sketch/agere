"""trial_feasibility: funnel arithmetic, criterion impact, sites, diversity, enrolment forecast on a hand-built fixture; routing; wiring."""
import json, os, sqlite3, unittest
from datetime import date, timedelta
from pathlib import Path

from clinical_core.agere import ask_view
from clinical_core.agere.clinical import analysis_graph, graph_contracts, local_compiler, semantic_layer, source_profile, trial_feasibility as TF
from clinical_core.agere.clinical import operator_router as router
from clinical_core.agere.clinical.capability_registry import PRODUCT
from clinical_core.agere.clinical.graph_planner import CONTRACTS
from test_aggregate_explain import build, pcp

ROOT = Path(__file__).resolve().parents[2]
AS_OF = '2029-08-15'
REAL_V20 = Path(os.environ.get('RWE_STACK') or next(
    (p / 'artifacts' / 'agere-healthcare-v20' / 'stack' for p in (ROOT, *ROOT.parents) if (p / 'artifacts' / 'agere-healthcare-v20' / 'stack').exists()),
    ROOT / 'artifacts' / 'agere-healthcare-v20' / 'stack'))
A1C = [7.0, 8.0, 9.0, 11.0]


def fixture():
    """40 patients. p00-p19 have type 2 diabetes (dated 2028-09-01 + 15 i days); latest A1c (2029-05-01) cycles 7/8/9/11 by i % 4;
    latest eGFR 25 when i % 5 == 0 else 60; SGLT2 fills in the last 180 days when i % 3 == 0. Odd i: Black, Hispanic, Spanish,
    practice PR2; even i: White, not Hispanic, English, PR1."""
    patients, recs = {}, []
    for i in range(40):
        pid = f'p{i:02d}'
        odd = i % 2 == 1
        patients[pid] = {'age_reported': 30 + i, 'sex_reported': 'F' if odd else 'M', 'race': 'Black or African American' if odd else 'White',
                         'ethnicity': 'Hispanic or Latino' if odd else 'Not Hispanic or Latino', 'preferred_language': 'Spanish' if odd else 'English'}
        recs.append(pcp(pid, f'dr{i % 4}', 'PR2' if odd else 'PR1'))
        recs.append((pid, 'labs', {'test': 'eGFR', 'value': 25 if i % 5 == 0 else 60, 'unit': 'mL/min/1.73m2', 'event_date': '2029-05-01'}))
        if i < 20:
            recs.append((pid, 'conditions', {'description': 'Type 2 diabetes mellitus', 'icd10': 'E11.9',
                                             'event_date': (date(2028, 9, 1) + timedelta(days=15 * i)).isoformat()}))
            recs.append((pid, 'labs', {'test': 'HbA1c', 'value': 6.0, 'unit': '%', 'event_date': '2028-01-01'}))
            recs.append((pid, 'labs', {'test': 'HbA1c', 'value': A1C[i % 4], 'unit': '%', 'event_date': '2029-05-01'}))
        if i % 3 == 0:
            recs.append((pid, 'pharmacy_dispenses', {'medication_name': 'empagliflozin 10 mg tablet', 'status': 'dispensed',
                                                     'days_supply_reported': 30, 'event_date': '2029-06-01'}))
    path = build(recs, patients)
    semantic_layer.build(path)
    return path


CRIT = [{'type': 'condition', 'concept': 'type_2_diabetes'}, {'type': 'lab', 'concept': 'hba1c', 'low': 7.5, 'high': 10.5},
        {'type': 'lab', 'concept': 'egfr', 'op': 'ge', 'value': 30}, {'type': 'medication', 'kind': 'exclusion', 'classes': ['sglt2_inhibitor']},
        {'type': 'unevaluated', 'text': 'EF 40% or less'}]


class Funnel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = fixture()
        cls.r = TF.execute(cls.path, None, {'criteria': CRIT, 'trial': 'T2D trial', 'consent_rate': 0.5,
                                            'goals': {'race': {'Black or African American': 0.5}}}, AS_OF)

    def test_funnel_arithmetic(self):
        f = self.r['funnel']
        self.assertEqual([x['remaining'] for x in f], [40, 20, 10, 8, 5, 5])
        for a, b in zip(f, f[1:]):
            self.assertEqual(b['entering'], a['remaining'])
            self.assertEqual(b['entering'] - b['removed'], b['remaining'])
        self.assertEqual(f[2]['removed_missing_data'], 0)
        self.assertEqual(f[5]['kind'], 'not applied')
        expected = [i for i in range(20) if i % 4 in (1, 2) and i % 5 and i % 3]
        self.assertEqual(self.r['eligible'], len(expected))
        self.assertEqual(self.r['eligible_patient_ids'], [f'p{i:02d}' for i in expected])

    def test_order_free_impact(self):
        imp = {r['criterion']: r for r in self.r['criterion_impact']}
        sole_sglt2 = sum(1 for i in range(20) if i % 4 in (1, 2) and i % 5 and i % 3 == 0)
        self.assertEqual(imp['exclude sglt2 inhibitor fills >= 1 in the last 180 days']['removes_alone'], sole_sglt2)
        sole_egfr = sum(1 for i in range(20) if i % 4 in (1, 2) and i % 5 == 0 and i % 3)
        self.assertEqual(imp['latest egfr >= 30 in the last 365 days']['removes_alone'], sole_egfr)

    def test_sites_diversity_and_forecast(self):
        sites = {s['site']: s['eligible'] for s in self.r['sites']}
        self.assertEqual((sites['PR1'], sites['PR2']), (2, 3))
        race = {d['group']: d for d in self.r['diversity'] if d['dimension'] == 'race'}
        black = race['Black or African American']
        self.assertEqual((black['eligible'], black['eligible_share'], black['source_share']), (3, 0.6, 0.5))
        self.assertAlmostEqual(black['representation_ratio'], 1.2)
        self.assertTrue(black['goal_met'])
        fc = self.r['forecast']
        self.assertEqual(fc['newly_eligible'], 5)  # everyone became eligible with the 2029-05-01 A1c
        self.assertAlmostEqual(fc['expected_per_month'], round(5 / 12, 2))
        self.assertEqual(next(h for h in fc['history'] if h['month'] == '2029-05')['newly_eligible'], 5)
        self.assertLess(fc['ci95_per_month'][0], fc['expected_per_month'])
        self.assertAlmostEqual(fc['expected_enrolled_per_month'], round(5 / 12 * 0.5, 2))
        self.assertTrue(any('Not applied' in w for w in self.r['warnings']))

    def test_unknown_lab_is_counted_apart(self):
        r = TF.execute(self.path, None, {'criteria': [{'type': 'lab', 'concept': 'ldl', 'op': 'lt', 'value': 100}]}, AS_OF)
        self.assertEqual((r['funnel'][1]['removed'], r['funnel'][1]['removed_missing_data']), (40, 40))
        r = TF.execute(self.path, None, {'criteria': [{'type': 'age', 'min': 40}, {'type': 'lab', 'concept': 'ldl', 'kind': 'exclusion', 'op': 'gt', 'value': 190}]}, AS_OF)
        self.assertEqual(r['funnel'][2]['unverified_kept'], 30)
        self.assertEqual(r['eligible'], 30)

    def test_graph_wiring_and_view(self):
        plan = {'interpretation': 'feasibility', 'coverage': 'supported_observation', 'unmet_requirements': [],
                'nodes': [{'id': 'answer', 'operation': 'trial_feasibility', 'inputs': [], 'parameters': {'criteria': CRIT}, 'purpose': 'TF'}]}
        checked = analysis_graph.validate(plan, AS_OF)
        answer = analysis_graph.build_answer(checked, [{'result': self.r}], {}, plan['interpretation'])
        json.dumps(answer, allow_nan=False, default=str)
        self.assertEqual(answer['status'], 'partial')  # a stated criterion was not computable
        self.assertEqual(answer['count'], 5)
        self.assertIn('trial_feasibility', answer['overall'])
        self.assertIn('vizFunnel', ask_view.page())
        for bad in ({'criteria': []}, {'criteria': [{'type': 'lab', 'concept': 'hba1c'}]}, {'criteria': [{'type': 'unevaluated', 'text': 'EF < 40'}]}):
            with self.assertRaises(ValueError): TF.validate(bad)


class Routing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = source_profile.load(fixture())

    def plan(self, q): return local_compiler.compile(q, self.profile, AS_OF)

    def test_registry_integration(self):
        for where in (PRODUCT, analysis_graph.OPERATIONS, CONTRACTS, graph_contracts.TYPED, analysis_graph.NO_BENCHMARK_OPERATIONS):
            self.assertIn('trial_feasibility', where)
        self.assertLess(len(json.dumps(router.tool('trial_feasibility'))), 20000)

    def test_phrasings_route_here(self):
        cases = {
            'How many patients meet the inclusion and exclusion criteria for a HFrEF trial (EF 40% or less, NT-proBNP elevated, eGFR above 30, on stable GDMT)?':
                {('condition', 'heart_failure_reduced_ef'), ('unevaluated', None), ('lab', 'nt_probnp'), ('lab', 'egfr'), ('medication', None)},
            'How many patients are eligible for a type 2 diabetes trial requiring A1c between 7.5 and 10.5 and eGFR of 30 or more?':
                {('condition', 'type_2_diabetes'), ('lab', 'hba1c'), ('lab', 'egfr')},
            'Trial feasibility: adults aged 40-75 with CKD and UACR above 200, not on an SGLT2 inhibitor':
                {('age', None), ('condition', 'chronic_kidney_disease'), ('lab', 'uacr'), ('medication', None)},
            'Eligibility funnel for our COPD study: adults with an ED visit in the last 12 months': {('age', None), ('condition', 'copd'), ('encounter', 'emergency_visit')},
            'Which sites have the most eligible patients for an atrial fibrillation trial?': {('condition', 'atrial_fibrillation')},
            'How many women aged 45-70 with obesity qualify for our study, excluding diabetes?':
                {('age', None), ('sex', None), ('condition', 'obesity'), ('condition', 'diabetes')},
            'Expected enrollment per month for a heart failure trial with NT-proBNP above 400': {('condition', 'heart_failure'), ('lab', 'nt_probnp')},
            'Diversity by race and ethnicity of patients eligible for our CKD trial with eGFR between 25 and 60':
                {('condition', 'chronic_kidney_disease'), ('lab', 'egfr')},
            'Which inclusion criteria remove the most patients for a hypertension trial requiring systolic blood pressure of 150 or higher?':
                {('condition', 'hypertension'), ('lab', 'systolic_bp')},
        }
        for q, want in cases.items():
            g, info = self.plan(q)
            self.assertIsNotNone(g, (q, info))
            self.assertEqual([n['operation'] for n in g['nodes']], ['trial_feasibility'], (q, g))
            p = analysis_graph.validate(g, AS_OF)['nodes'][-1]['parameters']
            got = {(c['type'], c.get('concept') or None) for c in p['criteria']}
            self.assertEqual(got, want, q)
        g, _ = self.plan('Trial feasibility: adults aged 40-75 with CKD and UACR above 200, not on an SGLT2 inhibitor')
        med = next(c for c in g['nodes'][0]['parameters']['criteria'] if c['type'] == 'medication')
        self.assertEqual(med['kind'], 'exclusion')
        g, _ = self.plan('Diversity by race and ethnicity of patients eligible for our CKD trial with eGFR between 25 and 60')
        self.assertEqual(g['nodes'][0]['parameters']['group_by'], 'race_ethnicity')

    def test_acceptance_phrasings(self):
        cases = json.loads((ROOT / 'bench' / 'acceptance' / 'trial_feasibility.json').read_text())
        self.assertEqual(len(cases), 8)
        for case in cases:
            g, info = self.plan(case['question'])
            self.assertIsNotNone(g, (case['question'], info))
            self.assertEqual([n['operation'] for n in g['nodes']], [case['expect_operation']], case['question'])
            analysis_graph.validate(g, AS_OF)

    def test_unstated_criteria_are_asked_for(self):
        for q in ('Which criteria remove the most patients from the eligibility funnel for our NASH trial?',
                  'Does the eligible population for our trial meet the diversity action plan goals by race, ethnicity, sex and age?'):
            g, info = self.plan(q)
            self.assertEqual((g['coverage'], info['operation']), ('unsupported', 'trial_feasibility'), q)
            self.assertTrue(any('inclusion/exclusion criteria' in m for m in g['unmet_requirements']))

    def test_literal_counts_and_other_engines_not_stolen(self):
        for q in ('How many adults with type 2 diabetes have an A1c between 7.5% and 10.5% and an eGFR of 30 or more?',
                  'How many newly diagnosed heart failure patients per month should we expect to screen over the next year?',
                  'Did the randomized trial of care management reduce ED visits?', 'What is target trial emulation?',
                  'Emulate a target trial of SGLT2 vs DPP-4 inhibitors on eGFR'):
            self.assertIsNone(local_compiler._trial_feasibility_question(q, self.profile, AS_OF), q)


def _v20_ready():
    try:
        db = sqlite3.connect(f"file:{REAL_V20 / 'semantic.sqlite3'}?mode=ro", uri=True)
        db.execute('SELECT count(*) FROM patient').fetchone(); db.close()
        return True
    except Exception:
        return False


@unittest.skipUnless(_v20_ready(), 'warehouse v2.0 not present')
class RealV20(unittest.TestCase):
    def test_smoke(self):
        db = sqlite3.connect(f"file:{REAL_V20 / 'semantic.sqlite3'}?mode=ro", uri=True)
        try:
            as_of = dict(db.execute('SELECT key, value FROM meta'))['as_of']
            r = TF.run(db, None, {'criteria': [{'type': 'age', 'min': 18}, {'type': 'condition', 'concept': 'type_2_diabetes'},
                                               {'type': 'lab', 'concept': 'hba1c', 'low': 7.5, 'high': 10.5}]}, as_of)
        finally:
            db.close()
        self.assertEqual(r['funnel'][-1]['remaining'], r['eligible'])
        self.assertGreater(r['funnel'][2]['remaining'], 50)
        self.assertTrue(any(d['dimension'] == 'race' for d in r['diversity']))


if __name__ == '__main__':
    unittest.main()
