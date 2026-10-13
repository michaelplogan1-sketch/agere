"""program_effect: matched DiD recovers a known effect, placebo ~0, and the data-support warnings fire."""
import json, random, unittest
from datetime import date, timedelta
from pathlib import Path

from clinical_core.agere.clinical import analysis_graph, local_compiler, program_effect, semantic_layer, source_profile
from test_aggregate_explain import build, visit

AS_OF = '2029-08-15'
START = date(2028, 6, 1)


def _days(a, b):
    d = a
    while d < b:
        yield d
        d += timedelta(days=1)


def fixture(n=240, exposed_every=5, effect=0.5, gap=None, pre_rise=False, n_exposed=None, seed=3):
    """ED visits as a Poisson process per patient (rate depends on a latent risk level). Exposed patients get a care-management
    contact (status agrees) between 2029-02-01 and 2029-04-01; afterwards their ED rate is multiplied by `effect`.
    Returns (path, true DiD effect in visits per 30 days)."""
    rng = random.Random(seed)
    patients, recs, truth = {}, [], []
    exposed_count = 0
    for i in range(n):
        pid = f'p{i:03d}'
        patients[pid] = {'age_reported': 72 if i % 2 else 45, 'sex_reported': 'F' if (i // 2) % 2 else 'M'}
        recs.append((pid, 'coverage', {'coverage_type': 'medicare' if (i // 4) % 2 else 'commercial', 'status': 'active',
                                       'effective_start': '2026-01-01', 'event_date': '2026-01-01'}))
        base = (0.6 if i % 3 == 0 else 0.25) / 30  # visits per day
        exposed = i % exposed_every == 0 and (n_exposed is None or exposed_count < n_exposed)
        t0 = date(2029, 2, 1) + timedelta(days=rng.randrange(59)) if exposed else None
        if exposed:
            exposed_count += 1
            recs.append((pid, 'care_management_contacts', {'status': 'agrees', 'event_date': t0.isoformat()}))
            truth.append(30 * base * (effect - 1))
        elif i % 7 == 1:  # contacted but declined: excluded from the comparison pool
            recs.append((pid, 'care_management_contacts', {'status': 'declines', 'event_date': '2029-03-01'}))
        for d in _days(START, date(2029, 8, 15)):
            lam = base
            if exposed and pre_rise and t0 - timedelta(days=90) <= d < t0: lam = base * 3
            if exposed and d > t0: lam = base * effect
            if gap and gap[0] <= d.isoformat()[:7] <= gap[1]: lam = 0
            if rng.random() < lam: recs.append(visit(pid, 'emergency', d.isoformat()))
        for m in range(14):  # monthly primary care keeps the encounter feed flowing (reference volume for gap checks)
            recs.append(visit(pid, 'primary_care', (START + timedelta(days=30 * m + i % 20)).isoformat()))
    path = build(recs, patients)
    semantic_layer.build(path)
    return path, (sum(truth) / len(truth) if truth else None)


PARAMS = {'program': 'care_management', 'outcome': 'ed_visits', 'pre_days': 180, 'post_days': 120, 'bootstrap': 600}


class KnownEffect(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path, cls.truth = fixture()
        cls.r = program_effect.execute(cls.path, None, PARAMS, AS_OF)

    def test_did_recovers_true_effect(self):
        did = self.r['did']
        self.assertIsNotNone(did)
        lo, hi = did['ci95']
        self.assertLess(self.truth, 0)
        self.assertTrue(lo <= self.truth <= hi, (self.truth, did))
        self.assertAlmostEqual(did['effect'], self.truth, delta=0.15)

    def test_placebo_is_consistent_with_zero_and_pretrend_passes(self):
        self.assertEqual(self.r['placebo']['status'], 'consistent_with_zero', self.r['placebo'])
        self.assertLess(abs(self.r['placebo']['placebo_effect']), 0.1)
        self.assertEqual(self.r['pre_trend']['status'], 'passed', self.r['pre_trend'])

    def test_balance_reported_and_pool_excludes_decliners(self):
        names = {b['covariate'] for b in self.r['balance']}
        self.assertEqual(names, set(program_effect.COVARIATES))
        for b in self.r['balance']:
            self.assertIn('smd_before', b); self.assertIn('smd_after', b)
        self.assertGreater(self.r['sample']['excluded_from_pool_program_records'], 0)
        self.assertEqual(self.r['sample']['matched_exposed'], self.r['sample']['eligible_exposed'] - self.r['sample']['unmatched_exposed'])

    def test_language_and_inference(self):
        self.assertIn('under stated assumptions', self.r['reading'])
        self.assertIn('not an identified causal effect', self.r['reading'])
        self.assertNotEqual(self.r['inference'], 'not_supported')
        self.assertTrue(self.r['assumptions'])

    def test_synthetic_control_reported_when_adoption_concentrated(self):
        sc = self.r['synthetic_control']
        self.assertIn(sc['status'], ('estimated', 'not_feasible'))
        if sc['status'] == 'estimated':
            self.assertAlmostEqual(sum(sc['weights'].values()), 1.0, delta=0.05)

    def test_graph_wiring(self):
        plan = {'interpretation': 'Did care management reduce ED visits?', 'coverage': 'supported_observation', 'unmet_requirements': [],
                'nodes': [{'id': 'answer', 'operation': 'program_effect', 'inputs': [], 'parameters': dict(PARAMS), 'purpose': 'Program effect'}]}
        checked = analysis_graph.validate(plan, AS_OF)
        self.assertEqual(checked['nodes'][0]['parameters']['controls_per_exposed'], 3)
        answer = analysis_graph.build_answer(checked, [{'result': self.r}], {}, plan['interpretation'])
        self.assertEqual(answer['answer_type'], 'table')
        self.assertEqual(answer['table'][0]['analysis'], 'matched_difference_in_differences')
        self.assertIn('under stated assumptions', answer['value'])
        text = analysis_graph.headline(answer)
        self.assertIn('Estimated effect of care management', text)
        bad = {**plan, 'nodes': [{**plan['nodes'][0], 'parameters': {'program': 'unnamed', 'outcome': 'ed_visits'}}]}
        with self.assertRaises(ValueError):
            analysis_graph.validate(bad, AS_OF)


class NullEffect(unittest.TestCase):
    def test_no_effect_ci_covers_zero(self):
        path, truth = fixture(effect=1.0, seed=11)
        r = program_effect.execute(path, None, PARAMS, AS_OF)
        self.assertAlmostEqual(truth, 0.0)
        self.assertTrue(r['did']['ci95'][0] <= 0 <= r['did']['ci95'][1], r['did'])


class DataCannotSupport(unittest.TestCase):
    def test_too_few_exposed_gives_no_estimate(self):
        path, _ = fixture(n=80, n_exposed=4, seed=5)
        r = program_effect.execute(path, None, PARAMS, AS_OF)
        self.assertIsNone(r['did'])
        self.assertEqual(r['inference'], 'not_supported')
        self.assertTrue(any('Too few matched exposed' in w for w in r['warnings']))
        self.assertIn('cannot support', r['reading'])

    def test_feed_gap_inside_windows_blocks_inference(self):
        path, _ = fixture(gap=('2028-11', '2029-01'), seed=7)
        r = program_effect.execute(path, None, PARAMS, AS_OF)
        gaps = [f for f in r['feed_checks'] if f['kind'] == 'outcome_feed_gap']
        self.assertTrue(gaps and gaps[0]['months'][0] == '2028-11', r['feed_checks'])
        self.assertEqual(r['inference'], 'not_supported')
        self.assertTrue(any('feed gap' in w for w in r['blocking_warnings']))

    def test_failed_pre_trend_is_flagged(self):
        path, _ = fixture(pre_rise=True, effect=1.0, seed=9)
        r = program_effect.execute(path, None, {**PARAMS, 'caliper': 0.3}, AS_OF)
        self.assertEqual(r['pre_trend']['status'], 'failed', r['pre_trend'])
        self.assertEqual(r['inference'], 'not_supported')


class MedicationStartLab(unittest.TestCase):
    def test_new_user_design_recovers_lab_shift_and_excludes_prevalent(self):
        rng = random.Random(4)
        patients, recs, truth = {}, [], 5.0
        for i in range(160):
            pid = f'm{i:03d}'
            patients[pid] = {'age_reported': 66, 'sex_reported': 'F' if i % 2 else 'M'}
            recs.append((pid, 'coverage', {'coverage_type': 'medicare', 'status': 'active', 'effective_start': '2026-01-01', 'event_date': '2026-01-01'}))
            recs.append((pid, 'pharmacy_dispenses', {'medication_name': 'atorvastatin 20 mg', 'status': 'completed', 'event_date': '2028-01-05'}))
            level = 40 + (i % 5) * 8
            start = date(2029, 3, 1) + timedelta(days=i % 30) if i % 4 == 0 else None
            if i == 4: start = date(2028, 3, 1)  # inside the washout: possible prevalent user
            if start:
                recs.append((pid, 'pharmacy_dispenses', {'medication_name': 'empagliflozin 10 mg', 'status': 'completed', 'event_date': start.isoformat()}))
            for m in range(20):
                d = date(2028, 1, 15) + timedelta(days=30 * m)
                v = level + rng.gauss(0, 2) + (truth if start and d > start else 0)
                recs.append((pid, 'labs', {'test': 'eGFR', 'value': round(v, 1), 'unit': 'mL/min/1.73m2', 'event_date': d.isoformat()}))
        path = build(recs, patients)
        semantic_layer.build(path)
        r = program_effect.execute(path, None, {'program': 'medication_start', 'drug_class': 'sglt2_inhibitor', 'outcome': 'lab', 'lab': 'egfr',
                                                'pre_days': 180, 'post_days': 120, 'bootstrap': 600}, AS_OF)
        self.assertGreaterEqual(r['exposure']['excluded_possible_prevalent_users'], 1)
        self.assertAlmostEqual(r['did']['effect'], truth, delta=1.0)
        self.assertTrue(r['did']['ci95'][0] <= truth <= r['did']['ci95'][1])


class Compile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path, _ = fixture(n=40, seed=2)
        cls.profile = source_profile.load(cls.path)

    def compile(self, q):
        return local_compiler.compile(q, self.profile, AS_OF)

    def test_effect_questions_compile_to_program_effect(self):
        g, info = self.compile('Did care management reduce ED visits?')
        node = g['nodes'][-1]
        self.assertEqual(node['operation'], 'program_effect')
        self.assertEqual((node['parameters']['program'], node['parameters']['outcome']), ('care_management', 'ed_visits'))
        self.assertEqual(info['route'], 'local_compiler')
        q = 'Estimate the effect of starting an SGLT2 inhibitor on eGFR decline'
        g, _ = self.compile(q)  # the compile fixture has no dispense/claim/order stream
        self.assertEqual(g['coverage'], 'unsupported')
        profile = {**self.profile, 'streams': {**self.profile['streams'], 'pharmacy_dispenses': {'fields': {}}}}
        g, _ = local_compiler.compile(q, profile, AS_OF)
        p = g['nodes'][-1]['parameters']
        self.assertEqual((p['program'], p['drug_class'], p['outcome'], p['lab']), ('medication_start', 'sglt2_inhibitor', 'lab', 'egfr'))
        g, _ = self.compile('What was the impact of our care management program on PMPM?')
        self.assertEqual(g['nodes'][-1]['parameters']['outcome'], 'pmpm')

    def test_transitions_question_needs_received_stream(self):
        g, _ = self.compile('What was the impact of our transitions-of-care calls on 30-day admissions?')
        self.assertEqual(g['coverage'], 'unsupported')  # fixture has no transition_of_care_contacts stream
        self.assertTrue(any('transition_of_care_contacts' in m for m in g['unmet_requirements']))
        profile = {**self.profile, 'streams': {**self.profile['streams'], 'transition_of_care_contacts': {'fields': {}}}}
        g, _ = local_compiler.compile('What was the impact of our transitions-of-care calls on 30-day admissions?', profile, AS_OF)
        p = g['nodes'][-1]['parameters']
        self.assertEqual((p['program'], p['outcome'], p['post_days']), ('transitions_of_care', 'admissions', 30))

    def test_refusals_name_missing_inputs(self):
        for q, needle in [('Did our program reduce ED visits?', 'named program'),
                          ('Did the randomized trial of care management reduce ED visits?', 'randomised'),
                          ('Did care management reduce happiness?', 'outcome'),
                          ('Did our care management outreach calls actually cause fewer ED visits? I want the causal effect, not just a before/after.', 'causal'),
                          ('Board wants a number: how many ED visits did the care management outreach program prevent this year?', 'counterfactual'),
                          ('Did enrolling patients in care management actually cut their ED use? Give me the causal effect, not a before/after comparison.', 'causal'),
                          ('Exactly how many admissions were causally prevented by transportation assistance in this cohort? Provide the identified causal effect, not an association.', 'causal')]:
            g, info = self.compile(q)
            self.assertEqual(g['coverage'], 'unsupported', q)
            self.assertTrue(any(needle in m.lower() for m in g['unmet_requirements']), (q, g['unmet_requirements']))


STACK = Path('/Users/mikel/clinical-insights-local/artifacts/program-effect/stack')


class RealRelease(unittest.TestCase):
    @unittest.skipUnless((STACK / 'semantic.sqlite3').exists(), 'synthetic release copy not present')
    def test_care_management_on_release_reports_warnings(self):
        r = program_effect.execute(STACK, None, {'program': 'care_management', 'outcome': 'ed_visits', 'bootstrap': 300})
        self.assertTrue(r['warnings'])
        self.assertIn(r['inference'], ('limited', 'not_supported'))


if __name__ == '__main__':
    unittest.main()
