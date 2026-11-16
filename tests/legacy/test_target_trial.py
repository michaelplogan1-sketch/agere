"""target_trial: target trial emulation recovers planted effects under confounding; protocol, balance, E-value, warnings, routing."""
import json, os, random, sqlite3, unittest
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from clinical_core.agere import ask_view
from clinical_core.agere.clinical import analysis_graph, graph_contracts, local_compiler, semantic_layer, source_profile, target_trial as TT
from clinical_core.agere.clinical import operator_router as router
from clinical_core.agere.clinical.capability_registry import PRODUCT
from clinical_core.agere.clinical.graph_planner import CONTRACTS
from test_aggregate_explain import build, visit

ROOT = Path(__file__).resolve().parents[2]
AS_OF = '2029-08-15'
REAL_V20 = Path(os.environ.get('RWE_STACK') or next(
    (p / 'artifacts' / 'agere-healthcare-v20' / 'stack' for p in (ROOT, *ROOT.parents) if (p / 'artifacts' / 'agere-healthcare-v20' / 'stack').exists()),
    ROOT / 'artifacts' / 'agere-healthcare-v20' / 'stack'))


def _cov(pid):
    return (pid, 'coverage', {'coverage_type': 'medicare', 'status': 'active', 'effective_start': '2025-01-01', 'event_date': '2025-01-01'})


def _fills(pid, name, start, months, supply=30):
    return [(pid, 'pharmacy_dispenses', {'medication_name': name, 'status': 'dispensed', 'days_supply_reported': supply,
                                         'event_date': (start + timedelta(days=30 * m)).isoformat()}) for m in range(months)]


def lab_fixture(n=360, effect=3.0, seed=5, switchers=0):
    """New users of empagliflozin (A) vs sitagliptin (B). Confounder: CKD (documented 2026-06-01) lowers baseline eGFR,
    speeds decline (-4 vs -1 per year) and makes A more likely (0.75 vs 0.3). A raises eGFR by `effect` over its first
    year (linear ramp). Returns (path, effect)."""
    rng = random.Random(seed)
    patients, recs = {}, []
    for i in range(n):
        pid = f't{i:03d}'
        ckd = rng.random() < 0.4
        patients[pid] = {'age_reported': 55 + rng.randrange(25), 'sex_reported': 'F' if i % 2 else 'M'}
        recs.append(_cov(pid))
        recs += _fills(pid, 'levothyroxine 50 mcg tablet', date(2026, 1, 5), 1)  # starts the fill feed
        if ckd: recs.append((pid, 'conditions', {'description': 'Chronic kidney disease stage 3', 'icd10': 'N18.3', 'event_date': '2026-06-01'}))
        recs.append((pid, 'conditions', {'description': 'Type 2 diabetes mellitus', 'icd10': 'E11.9', 'event_date': '2026-03-01'}))
        a = rng.random() < (0.75 if ckd else 0.3)
        t0 = date(2028, 1, 1) + timedelta(days=rng.randrange(180))
        recs += _fills(pid, 'empagliflozin 10 mg tablet' if a else 'sitagliptin 100 mg tablet', t0, 18)
        if switchers and not a and i % 7 == 0 and switchers > 0:
            recs += _fills(pid, 'empagliflozin 10 mg tablet', t0 + timedelta(days=150), 6)
        base = (45 if ckd else 75) + rng.gauss(0, 4)
        slope = 4.0 if ckd else 1.0
        d = date(2026, 7, 1) + timedelta(days=rng.randrange(20))
        while d <= date(2029, 8, 1):
            v = base - slope * (d - date(2026, 7, 1)).days / 365.25 + rng.gauss(0, 1.5)
            if a and d > t0: v += effect * min((d - t0).days, 365) / 365
            recs.append((pid, 'labs', {'test': 'eGFR', 'value': round(v, 1), 'unit': 'mL/min/1.73m2', 'event_date': d.isoformat()}))
            d += timedelta(days=45)
    path = build(recs, patients)
    semantic_layer.build(path)
    return path, effect


def event_fixture(n=600, hr=0.5, seed=8, horizon=540):
    """Atorvastatin 40-80 mg (high intensity, A) vs simvastatin 20 mg (moderate, B) new users. Confounder: age >= 70 makes
    A more likely (0.7 vs 0.3) and triples the admission hazard. Admissions: exponential with hazard x hr on A.
    Urgent-care visits (negative control) are unaffected by arm. Returns (path, true marginal risk ratio at the horizon):
    the conditional hazard ratio is not collapsible, so the marginal contrast checked is the risk ratio over everyone
    (here the overlap-weighted population equals the whole population: e(1 - e) = 0.21 in both age strata)."""
    rng = random.Random(seed)
    patients, recs, risk = {}, [], [0.0, 0.0]
    for i in range(n):
        pid = f'e{i:03d}'
        old = rng.random() < 0.45
        patients[pid] = {'age_reported': (74 if old else 52) + rng.randrange(6), 'sex_reported': 'F' if i % 2 else 'M'}
        recs.append(_cov(pid))
        recs += _fills(pid, 'levothyroxine 50 mcg tablet', date(2026, 1, 5), 1)
        a = rng.random() < (0.7 if old else 0.3)
        t0 = date(2028, 1, 1) + timedelta(days=rng.randrange(120))
        recs += _fills(pid, 'atorvastatin 40 mg tablet' if a else 'simvastatin 20 mg tablet', t0, 16)
        base = (3 if old else 1) * 0.35 / 365
        risk[0] += 1 - np.exp(-base * hr * horizon); risk[1] += 1 - np.exp(-base * horizon)
        lam = base * (hr if a else 1.0)
        t = rng.expovariate(lam)
        if t < 700:
            day = t0 + timedelta(days=int(t) + 1)
            recs.append(visit(pid, 'inpatient', day.isoformat(), 'H1', (day + timedelta(days=3)).isoformat()))
        u = rng.expovariate((2 if old else 1) * 0.5 / 365)
        if u < 700: recs.append(visit(pid, 'urgentcare', (t0 + timedelta(days=int(u) + 1)).isoformat()))
        for m in range(0, 40, 6):  # routine visits keep encounter feeds alive
            recs.append(visit(pid, 'primary_care', (date(2026, 2, 1) + timedelta(days=30 * m + i % 20)).isoformat()))
    path = build(recs, patients)
    semantic_layer.build(path)
    return path, risk[0] / risk[1]


LAB = {'arms': ['sglt2_inhibitor', 'dpp4_inhibitor'], 'outcome': {'type': 'lab_change', 'lab': 'egfr', 'horizon_days': 365},
       'population_conditions': ['type_2_diabetes'], 'bootstrap': 200}


class LabChangeRecoversPlantedEffect(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path, cls.truth = lab_fixture()
        cls.r = TT.execute(cls.path, None, LAB, AS_OF)

    def test_adjusted_estimate_recovers_truth_and_naive_is_biased(self):
        itt = self.r['analyses']['intention_to_treat']
        self.assertEqual(itt['status'], 'estimated', self.r['warnings'])
        lo, hi = itt['ci95']
        self.assertTrue(lo <= self.truth <= hi, itt)
        self.assertAlmostEqual(itt['estimate'], self.truth, delta=0.8)
        self.assertLess(itt['unadjusted_difference'], self.truth - 0.8)  # confounding by CKD biases the crude contrast
        self.assertAlmostEqual(self.r['analyses']['per_protocol']['estimate'], self.truth, delta=0.9)

    def test_balance_and_negative_control(self):
        b = {x['covariate']: x for x in self.r['balance']}
        self.assertGreater(abs(b['has_chronic_kidney_disease']['smd_before']), 0.5)
        self.assertLess(abs(b['has_chronic_kidney_disease']['smd_after']), 0.02)  # overlap weights balance means (exactly without the ridge)
        nc = self.r['analyses']['negative_control']
        self.assertEqual(nc['status'], 'estimated')
        self.assertTrue(nc['assumed_null_consistent'], nc)

    def test_protocol_e_value_and_language(self):
        comps = [x['component'] for x in self.r['protocol']]
        self.assertEqual(comps, ['Eligibility', 'Treatment strategies', 'Assignment', 'Time zero', 'Follow-up', 'Outcome', 'Causal contrasts', 'Analysis'])
        self.assertIn('immortal-time', self.r['protocol'][3]['emulation'])
        self.assertGreater(self.r['analyses']['intention_to_treat']['e_value'], 1)
        self.assertIn('not a randomised or identified causal effect', self.r['reading'])
        self.assertTrue(self.r['warnings'][0].startswith('Synthetic data') or any(w.startswith('Synthetic') for w in self.r['warnings']))
        self.assertEqual(self.r['eligibility_funnel']['eligible'], sum(self.r['sample'].values()))

    def test_matching_and_iptw_agree(self):
        for adj in ('matching', 'iptw'):
            r = TT.execute(self.path, None, {**LAB, 'adjustment': adj, 'bootstrap': 100, 'negative_control': 'none'}, AS_OF)
            self.assertAlmostEqual(r['analyses']['intention_to_treat']['estimate'], self.truth, delta=1.0, msg=adj)

    def test_graph_wiring_and_view(self):
        plan = {'interpretation': 'SGLT2 vs DPP-4 on eGFR', 'coverage': 'supported_observation', 'unmet_requirements': [],
                'nodes': [{'id': 'answer', 'operation': 'target_trial', 'inputs': [], 'parameters': dict(LAB), 'purpose': 'Target trial'}]}
        checked = analysis_graph.validate(plan, AS_OF)
        self.assertEqual(checked['nodes'][0]['parameters']['adjustment'], 'overlap')
        answer = analysis_graph.build_answer(checked, [{'result': self.r}], {}, plan['interpretation'])
        self.assertEqual(answer['answer_type'], 'table')
        self.assertIn('target_trial', answer['overall'])
        self.assertIn('Target trial emulation', answer['value'])
        html = ask_view.render() if hasattr(ask_view, 'render') else None
        if html: self.assertIn('vizTargetTrial', html)
        bad = {**plan, 'nodes': [{**plan['nodes'][0], 'parameters': {'arms': ['sglt2_inhibitor'], 'outcome': {'type': 'lab_change', 'lab': 'egfr'}}}]}
        with self.assertRaises(ValueError): analysis_graph.validate(bad, AS_OF)


class TimeToEventRecoversHazardRatio(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path, cls.rr = event_fixture()
        cls.r = TT.execute(cls.path, None, {'arms': ['high_intensity_statin', 'moderate_intensity_statin'],
                                            'outcome': {'type': 'admission', 'horizon_days': 540}, 'bootstrap': 200}, AS_OF)

    def test_risk_ratio_interval_covers_truth_and_hazard_ratio_is_protective(self):
        itt = self.r['analyses']['intention_to_treat']
        self.assertEqual(itt['scale'], 'hazard_ratio')
        lo, hi = itt['risk_ratio_ci95']
        self.assertTrue(lo <= self.rr <= hi, (self.rr, itt))
        self.assertLess(itt['ci95'][1], 1.0)  # planted conditional HR 0.5; the marginal HR is attenuated but protective
        self.assertGreater(itt['estimate'], 0.4)
        self.assertLess(itt['risk_treated'], itt['risk_comparator'])
        self.assertTrue(itt['e_value'] > 1)
        self.assertEqual(self.r['sample']['high_intensity_statin'] + self.r['sample']['moderate_intensity_statin'], 600)

    def test_negative_control_and_notes(self):
        nc = self.r['analyses']['negative_control']
        self.assertEqual(nc['status'], 'estimated')
        self.assertTrue(nc['ci95'][0] <= 1 <= nc['ci95'][1], nc)
        self.assertTrue(any('death records' in w for w in self.r['warnings']))
        self.assertTrue(any('all-cause' in w for w in self.r['warnings']))


class Units(unittest.TestCase):
    def test_statin_intensity(self):
        self.assertEqual(TT.statin_intensity('atorvastatin 80 MG Oral Tablet'), 'high')
        self.assertEqual(TT.statin_intensity('rosuvastatin calcium 10 mg'), 'moderate')
        self.assertEqual(TT.statin_intensity('simvastatin 10 mg tablet'), 'low')
        self.assertIsNone(TT.statin_intensity('Atorvastatin'))

    def test_e_value_formula(self):
        self.assertAlmostEqual(TT.e_value(2.0), 2 + np.sqrt(2))
        self.assertAlmostEqual(TT.e_value(0.5), 2 + np.sqrt(2))
        self.assertEqual(TT.e_value_ci(0.8, 1.2), 1.0)

    def test_overlap_weights_balance_exactly_and_cox_matches_known(self):
        rng = np.random.default_rng(0)
        x = rng.normal(size=500); a = (rng.random(500) < 1 / (1 + np.exp(-x))).astype(int)
        ps = TT.logistic(x[:, None], a, ridge=0.0)
        w = TT.ps_weights(ps, a, 'overlap')
        self.assertLess(abs(TT.smd(x, a, w)), 1e-6)
        t = rng.exponential(1 / np.where(a == 1, 0.5, 1.0)); e = np.ones(500, int)
        self.assertAlmostEqual(np.exp(TT.weighted_cox(t, e, a, np.ones(500))), 0.5, delta=0.12)

    def test_validation(self):
        for bad in ({'arms': ['statin', 'statin'], 'outcome': {'type': 'admission'}},
                    {'arms': ['high_intensity_statin', 'arb'], 'outcome': {'type': 'admission'}},
                    {'arms': ['arb', 'ace_inhibitor'], 'outcome': {'type': 'lab_change'}},
                    {'arms': ['arb', 'ace_inhibitor'], 'outcome': {'type': 'admission'}, 'adjustment': 'stratify'}):
            with self.assertRaises(ValueError): TT.validate(bad)

    def test_too_few_patients_blocks(self):
        path, _ = lab_fixture(n=14, seed=2)
        r = TT.execute(path, None, {**LAB, 'bootstrap': 50}, AS_OF)
        self.assertEqual(r['inference'], 'not_supported')
        self.assertIn('cannot support', r['reading'])


class Routing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path, _ = lab_fixture(n=30, seed=1)
        cls.profile = source_profile.load(cls.path)

    def plan(self, q): return local_compiler.compile(q, self.profile, AS_OF)

    def test_registry_integration(self):
        self.assertIn('target_trial', PRODUCT)
        self.assertIn('target_trial', analysis_graph.OPERATIONS)
        self.assertIn('target_trial', CONTRACTS)
        self.assertIn('target_trial', graph_contracts.TYPED)
        self.assertIn('target_trial', analysis_graph.NO_BENCHMARK_OPERATIONS)
        self.assertLess(len(json.dumps(router.tool('target_trial'))), 20000)

    def test_phrasings_route_here(self):
        cases = {
            'Does starting a GLP-1 agonist slow eGFR decline compared with starting an SGLT2 inhibitor?': (['glp1_agonist', 'sglt2_inhibitor'], 'lab_change'),
            'GLP-1 vs SGLT2 on eGFR decline': (['glp1_agonist', 'sglt2_inhibitor'], 'lab_change'),
            'Among new users, do SGLT2 inhibitors reduce heart failure hospitalization compared with DPP-4 inhibitors?': (['sglt2_inhibitor', 'dpp4_inhibitor'], 'admission'),
            'Compare metformin versus sulfonylurea on A1c': (['metformin', 'sulfonylurea'], 'lab_change'),
            'Statin intensity and LDL: high-intensity vs moderate-intensity statins': (['high_intensity_statin', 'moderate_intensity_statin'], 'lab_change'),
            'Emulate a target trial of ACE inhibitors versus ARBs on systolic blood pressure': (['ace_inhibitor', 'arb'], 'lab_change'),
            'Comparative effectiveness of SGLT2 inhibitors vs GLP-1 agonists for ED visits': (['sglt2_inhibitor', 'glp1_agonist'], 'ed_visit'),
            'Are patients started on an ARB less likely to be admitted than those started on an ACE inhibitor?': (['arb', 'ace_inhibitor'], 'admission'),
            'Is high-intensity statin therapy associated with lower LDL than lower-intensity statins in new users?': (['high_intensity_statin', 'lower_intensity_statin'], 'lab_change'),
            'What is the effect of metformin compared with a sulfonylurea on HbA1c among type 2 diabetics?': (['metformin', 'sulfonylurea'], 'lab_change'),
        }
        for q, (arms, kind) in cases.items():
            g, info = self.plan(q)
            self.assertIsNotNone(g, (q, info))
            self.assertEqual([n['operation'] for n in g['nodes']], ['target_trial'], (q, g))
            p = analysis_graph.validate(g, AS_OF)['nodes'][-1]['parameters']
            self.assertEqual((p['arms'], p['outcome']['type']), (arms, kind), q)

    def test_acceptance_phrasings(self):
        cases = json.loads((ROOT / 'bench' / 'acceptance' / 'target_trial.json').read_text())
        self.assertEqual(len(cases), 8)
        for case in cases:
            g, info = self.plan(case['question'])
            self.assertIsNotNone(g, (case['question'], info))
            self.assertEqual([n['operation'] for n in g['nodes']], [case['expect_operation']], case['question'])
            analysis_graph.validate(g, AS_OF)

    def test_missing_pieces_are_named(self):
        g, info = self.plan('Emulate a target trial comparing SGLT2 inhibitors with DPP-4 inhibitors')
        self.assertEqual(g['coverage'], 'unsupported')
        self.assertTrue(any('outcome' in m for m in g['unmet_requirements']), g)
        g, _ = self.plan('Run a target trial emulation of empagliflozin on eGFR')
        self.assertTrue(any('comparator' in m for m in g['unmet_requirements']), g)

    def test_program_effect_survival_and_knowledge_not_stolen(self):
        profile = {**self.profile, 'streams': {**self.profile['streams'], 'care_management_contacts': {'fields': {}}}}
        for q in ('Did starting an SGLT2 inhibitor reduce heart-failure admissions in our patients with HFrEF?',
                  'Estimate the effect of starting an SGLT2 inhibitor on eGFR decline', 'Did care management reduce ED visits?'):
            g, _ = local_compiler.compile(q, profile, AS_OF)
            self.assertEqual([n['operation'] for n in g['nodes']][-1:], ['program_effect'], q)
        for q in ('What is time to insulin initiation after metformin start for type 2 diabetes?',
                  'Readmission curve for heart failure discharges by payer', 'What is target trial emulation and why does time zero matter?',
                  'What share of patients with LDL of 190 or more are on high-intensity statin therapy?',
                  'How many patients are on an ACE inhibitor versus an ARB?'):
            self.assertIsNone(local_compiler._target_trial_question(q.lower(), self.profile), q)


def _v20_ready():
    try:
        db = sqlite3.connect(f"file:{REAL_V20 / 'semantic.sqlite3'}?mode=ro", uri=True)
        db.execute('SELECT count(*) FROM medication').fetchone(); db.close()
        return True
    except Exception:
        return False


@unittest.skipUnless(_v20_ready(), 'warehouse v2.0 not present')
class RealV20(unittest.TestCase):
    """Read-only smoke on the v2.0 semantic layer (opened directly with mode=ro; never rebuilt from a test)."""
    def test_smoke(self):
        db = sqlite3.connect(f"file:{REAL_V20 / 'semantic.sqlite3'}?mode=ro", uri=True)
        try:
            as_of = dict(db.execute('SELECT key, value FROM meta'))['as_of']
            r = TT.run(db, None, {'arms': ['ace_inhibitor', 'arb'], 'outcome': {'type': 'lab_change', 'lab': 'systolic_bp'}, 'bootstrap': 100}, as_of)
        finally:
            db.close()
        self.assertTrue(r['protocol'])
        self.assertIn(r['inference'], ('not_supported', 'limited', 'supported_under_assumptions'))
        self.assertTrue(any(w.startswith('Synthetic') for w in r['warnings']))


if __name__ == '__main__':
    unittest.main()
