"""Population simulator (clinical_core/agere/simulator): reproducibility, do-nothing = baseline, zero effect = do-nothing,
the GDMT pillar combination never multiplies hazard ratios, intervals widen with parameter uncertainty, compare-to-actual
and the workspace API route. Small synthetic sqlite fixtures (test_plans.build_fixture plus generated history)."""
import json
import math
import random
import sqlite3
import tempfile
import unittest
from pathlib import Path

import numpy as np

import test_plans
from clinical_core.agere.simulator import compare_to_actual, effects as fx, simulate
from clinical_core.agere.simulator.data import History

AS_OF = test_plans.AS_OF
MONTHS = [f'{y}-{m:02d}' for y in range(2027, 2031) for m in range(1, 13) if f'{y}-{m:02d}' <= '2030-05']


def add_history(path, background=160, hf_extra=24, pt_extra=10, seed=7):
    """Members, admissions / ED visits with paid claims and other spending for the fixture patients plus a background
    population; extra HF patients missing every pillar and extra ED low back pain patients so the plans have cohorts."""
    rnd = random.Random(seed)
    db = sqlite3.connect(path)
    db.execute('ALTER TABLE payer_claim ADD COLUMN paid_cents INTEGER')
    db.execute('ALTER TABLE payer_claim ADD COLUMN principal_diagnosis TEXT')
    db.execute('ALTER TABLE patient ADD COLUMN sex_reported TEXT')
    db.execute('CREATE TABLE utilization_event(patient_id TEXT, kind TEXT, event_date TEXT, end_date TEXT)')
    db.execute('CREATE TABLE member_month(patient_id TEXT, month TEXT, payer_id TEXT, product TEXT, line_of_business TEXT, '
               'plan_id TEXT, attribution_practice_id TEXT, attribution_npi TEXT)')
    hfx = [f'HFX{i:02d}' for i in range(hf_extra)]
    ptx = [f'PTX{i:02d}' for i in range(pt_extra)]
    bg = [f'BG{i:03d}' for i in range(background)]
    for pid in hfx + ptx + bg:
        db.execute('INSERT INTO patient(patient_id, birth_date, preferred_language) VALUES (?,?,?)',
                   (pid, f'{rnd.randint(1940, 1985)}-0{rnd.randint(1, 9)}-15', 'en'))
    db.execute("UPDATE patient SET sex_reported = CASE WHEN abs(random()) % 2 = 0 THEN 'F' ELSE 'M' END")
    for i, pid in enumerate(hfx + ptx):
        medicare = pid.startswith('PTX') and i % 3 == 0
        db.execute('INSERT INTO enrollment_span(record_id, patient_id, member_id, payer_id, product, line_of_business, coverage_start, '
                   'coverage_end, termination_reason, attribution_practice_id, attribution_npi, available_date) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                   (f'ES-{pid}', pid, f'M-{pid}', 'PAY-MCR' if medicare else 'PAY-COM', 'medicare_ffs' if medicare else 'commercial',
                    'medicare' if medicare else 'commercial', '2026-06-01', None, None, 'PRAC-A', '1111111111', '2026-06-05'))
    for pid in hfx:
        db.execute('INSERT INTO condition VALUES (?,?,?,?)', (f'CN-{pid}', pid, 'I50.22', '2028-03-01'))
    for i, pid in enumerate(ptx):  # discharged ED visit for low back pain, no PT afterwards
        d = f'2030-0{1 + i % 4}-1{i % 9}'
        db.execute("INSERT INTO payer_claim(payer_claim_id, record_id, patient_id, place_of_service, service_from, service_to, "
                   "counts_in_metrics, paid_cents, principal_diagnosis) VALUES (?,?,?,?,?,?,1,?,?)",
                   (f'CL-ED-{pid}', f'RC-ED-{pid}', pid, '23', d, d, 90000, 'M54.50'))
        db.execute('INSERT INTO payer_claim_diagnosis VALUES (?,?,?,?,?)', (f'CL-ED-{pid}', pid, d, 1, 'M54.50'))
        db.execute('INSERT INTO utilization_event VALUES (?,?,?,?)', (pid, 'ed_visit', d, d))
    fixture = [r[0] for r in db.execute('SELECT patient_id FROM patient')]
    n = 0
    for pid in fixture:
        hf = pid.startswith(('HF',)) or (pid.startswith('BG') and int(pid[2:]) < 30)
        frail = rnd.gammavariate(2.0, 0.5)
        ra, re_ = (0.035 if hf else 0.012) * frail, (0.07 if hf else 0.04) * frail
        for m in MONTHS:
            db.execute('INSERT INTO member_month VALUES (?,?,?,?,?,?,?,?)', (pid, m, 'PAY', 'commercial', 'commercial', 'P1', 'PRAC-A', '1'))
            for kind, rate, cost in (('inpatient', ra, 900000), ('ed_visit', re_, 120000)):
                if rnd.random() < rate:
                    d = f'{m}-{rnd.randint(1, 27):02d}'
                    n += 1
                    db.execute('INSERT INTO utilization_event VALUES (?,?,?,?)', (pid, kind, d, d))
                    dx = 'I50.9' if hf and rnd.random() < 0.5 else 'R07.9'
                    db.execute("INSERT INTO payer_claim(payer_claim_id, record_id, patient_id, place_of_service, type_of_bill, service_from, "
                               "service_to, counts_in_metrics, paid_cents, principal_diagnosis) VALUES (?,?,?,?,?,?,?,1,?,?)",
                               (f'CE{n}', f'RCE{n}', pid, '23' if kind == 'ed_visit' else None, '0111' if kind == 'inpatient' else '0131',
                                d, d, int(cost * rnd.uniform(0.5, 1.5)), dx))
            if rnd.random() < 0.7:
                n += 1
                d = f'{m}-{rnd.randint(1, 27):02d}'
                db.execute("INSERT INTO payer_claim(payer_claim_id, record_id, patient_id, place_of_service, service_from, service_to, "
                           "counts_in_metrics, paid_cents, principal_diagnosis) VALUES (?,?,?,?,?,?,1,?,?)",
                           (f'CO{n}', f'RCO{n}', pid, '11', d, d, int(rnd.expovariate(1 / 15000.0) * frail), 'I10'))
    db.commit()
    db.close()


def build(path):
    test_plans.build_fixture(path)
    add_history(path)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / 'semantic.sqlite3'
        build(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def sim(self, **kw):
        args = dict(program_kind='pharmacy_gdmt', as_of=AS_OF, horizon_months=12, iterations=300, funnel_scenarios=60, seed=11)
        args.update(kw)
        return simulate(self.db, **args)

    @staticmethod
    def row(out, arm):
        return next(r for r in out['summary_table'] if r['arm'] == arm)


class Reproducibility(Base):
    def test_same_seed_same_result_different_seed_differs(self):
        a, b = self.sim(), self.sim()
        self.assertEqual(json.dumps(a['summary_table'], sort_keys=True), json.dumps(b['summary_table'], sort_keys=True))
        self.assertEqual(a['id'], b['id'])
        c = self.sim(seed=12)
        self.assertNotEqual(json.dumps(a['summary_table'], sort_keys=True), json.dumps(c['summary_table'], sort_keys=True))

    def test_plan_cohort_and_arms(self):
        out = self.sim()
        self.assertGreaterEqual(out['cohort']['patients'], 20)
        ids = [a['id'] for a in out['arms']]
        self.assertEqual(ids[0], 'do_nothing')
        self.assertIn('plan', ids)
        self.assertIn('pharmd_barrier_review', ids)  # nobody routed there -> a forced (hypothetical) arm
        self.assertTrue(out['narrative'])
        self.assertIn('calibration', out)
        self.assertIn('c_statistic_any', out['calibration']['outcomes']['admissions'])
        self.assertTrue(any(p['id'] == 'gdmt.combination_hr' and p['kind'] == 'cited' for p in out['parameters']))


class DoNothingIsBaseline(Base):
    def test_do_nothing_matches_the_baseline_expectation(self):
        out = self.sim(parameter_uncertainty=False, iterations=3000, funnel_scenarios=20)
        dn, base = self.row(out, 'do_nothing'), out['baseline']
        self.assertAlmostEqual(dn['admissions']['mean'], base['expected_admissions'], delta=0.05 * base['expected_admissions'] + 0.2)
        self.assertAlmostEqual(dn['ed_visits']['mean'], base['expected_ed_visits'], delta=0.05 * base['expected_ed_visits'] + 0.2)
        self.assertAlmostEqual(dn['medical_cost']['mean'], base['expected_medical_cost'], delta=0.06 * base['expected_medical_cost'])


class ZeroEffect(Base):
    ZERO_COSTS = {f'cost.{p}.{k}': 0.0 for p in ('pharmacy_gdmt_titration', 'pharmd_barrier_review', 'access_virtual_pt_msk',
                                                  'plan_benefit_virtual_pt')
                  for k in ('per_outreach', 'per_enrolled', 'per_attended', 'per_completed')}
    ZERO_COSTS.update({'oap.access_virtual_pt_msk.annual_allowed': 0.0, 'oap.access_virtual_pt_msk.co_management_per_enrolled': 0.0})

    def test_gdmt_with_no_effect_equals_do_nothing(self):
        out = self.sim(parameters={'gdmt.combination_hr': 1.0, **self.ZERO_COSTS})
        dn = self.row(out, 'do_nothing')
        for arm in ('plan', 'pharmd_barrier_review'):
            r = self.row(out, arm)
            self.assertEqual(r['admissions'], dn['admissions'])
            self.assertEqual(r['ed_visits'], dn['ed_visits'])
            self.assertEqual(r['medical_cost'], dn['medical_cost'])
            self.assertEqual(r['admissions_avoided']['p95'], 0)
            self.assertEqual(r['net_savings']['mean'], 0)
            self.assertEqual(r['shared_savings_change']['mean'], 0)

    def test_pt_with_no_effect_equals_do_nothing(self):
        out = self.sim(program_kind='virtual_pt', parameters={'pt.back_pain_ed_rr': 1.0, 'pt.back_pain_other_spend_rr': 1.0, **self.ZERO_COSTS})
        dn = self.row(out, 'do_nothing')
        for r in out['summary_table'][1:]:
            self.assertEqual(r['ed_visits'], dn['ed_visits'])
            self.assertEqual(r['medical_cost'], dn['medical_cost'])
            self.assertEqual(r['net_savings']['mean'], 0)


class PillarCombination(unittest.TestCase):
    def setUp(self):
        self.cfg = fx.load_config()
        self.hrs = {p: self.cfg['parameters'][f'gdmt.pillar_hr.{p}']['value'] for p in fx.PILLARS}
        self.combo = self.cfg['parameters']['gdmt.combination_hr']['value']

    def test_all_four_pillars_reproduce_the_combination_not_the_product(self):
        full = float(fx.combined_hr(fx.PILLARS, self.hrs, self.combo, self.cfg))
        product = math.prod(self.hrs.values())
        self.assertAlmostEqual(full, 0.36, places=9)
        self.assertAlmostEqual(product, 0.244, delta=0.002)  # the forbidden number the negative-knowledge pack names
        self.assertGreater(abs(full - product), 0.1)

    def test_changing_single_pillar_hrs_never_changes_the_four_pillar_effect(self):
        for scale in (0.8, 0.9, 1.05):
            hrs = {p: min(0.99, v * scale) for p, v in self.hrs.items()}
            self.assertAlmostEqual(float(fx.combined_hr(fx.PILLARS, hrs, self.combo)), self.combo, places=9)

    def test_subsets_are_bounded_by_the_combination_and_monotone(self):
        from itertools import combinations
        for k in range(1, 5):
            for s in combinations(fx.PILLARS, k):
                hr = float(fx.combined_hr(s, self.hrs, self.combo))
                self.assertGreaterEqual(hr, self.combo - 1e-12)
                self.assertLess(hr, 1.0)
                for extra in set(fx.PILLARS) - set(s):
                    self.assertLess(float(fx.combined_hr(s + (extra,), self.hrs, self.combo)), hr)
        # never the product of the subset's single-trial HRs (e.g. SGLT2i + MRA)
        two = float(fx.combined_hr(('sglt2i', 'mra'), self.hrs, self.combo))
        self.assertNotAlmostEqual(two, self.hrs['sglt2i'] * self.hrs['mra'], places=3)

    def test_guard_rejects_the_forbidden_value(self):
        with self.assertRaises(fx.ForbiddenEffectError):
            fx.assert_not_forbidden(0.244, self.cfg)
        with self.assertRaises(fx.ForbiddenEffectError):
            fx.combined_hr(fx.PILLARS, self.hrs, 0.25, self.cfg)
        fx.assert_not_forbidden([0.36, 0.29, 0.46], self.cfg)
        self.assertTrue(fx.check_config(self.cfg))


class PillarGuardInEngine(Base):
    def test_engine_refuses_a_combined_effect_equal_to_the_product(self):
        with self.assertRaises(fx.ForbiddenEffectError):
            self.sim(parameters={'gdmt.combination_hr': 0.244, 'gdmt.p_pillar_started_completed': 1.0,
                                 'gdmt.p_pillar_started_attended': 1.0}, iterations=100, funnel_scenarios=20)


class Uncertainty(Base):
    def test_intervals_widen_with_parameter_uncertainty(self):
        fixed = self.sim(parameter_uncertainty=False, iterations=600, funnel_scenarios=60)
        unc = self.sim(parameter_uncertainty=True, iterations=600, funnel_scenarios=60)
        for metric in ('net_savings', 'gross_medical_savings', 'program_cost'):
            w0 = self.row(fixed, 'plan')[metric]['p95'] - self.row(fixed, 'plan')[metric]['p05']
            w1 = self.row(unc, 'plan')[metric]['p95'] - self.row(unc, 'plan')[metric]['p05']
            self.assertGreater(w1, w0, metric)
        for metric in ('medical_cost', 'admissions'):
            w0 = self.row(fixed, 'do_nothing')[metric]['p95'] - self.row(fixed, 'do_nothing')[metric]['p05']
            w1 = self.row(unc, 'do_nothing')[metric]['p95'] - self.row(unc, 'do_nothing')[metric]['p05']
            self.assertGreaterEqual(w1, w0, metric)

    def test_effect_reduces_events_and_costs_pharmacy(self):
        out = self.sim(iterations=400)
        plan = self.row(out, 'plan')
        self.assertGreater(plan['admissions_avoided']['mean'], 0)
        self.assertGreater(plan['program_cost']['mean'], 0)
        self.assertGreater(plan['pharmacy_cost_added']['mean'], 0)
        self.assertLess(plan['admissions']['mean'], self.row(out, 'do_nothing')['admissions']['mean'])


class CompareToActual(Base):
    def test_backdated_simulation_is_compared_with_later_data(self):
        out = self.sim(as_of='2029-06-01', horizon_months=6, iterations=200, funnel_scenarios=20)
        rep = compare_to_actual(self.db, out, '2029-12-01')
        self.assertEqual(rep['observed_through'], '2029-12-01')
        self.assertEqual({g['group'] for g in rep['groups']}, {'treated_in_data', 'not_treated_in_data'})
        for m in ('admissions', 'ed_visits', 'medical_cost'):
            self.assertIn('percentile_of_observed', rep['do_nothing_calibration'][m])
        self.assertTrue(rep['narrative'])
        with self.assertRaises(ValueError):
            compare_to_actual(self.db, out, '2031-01-01')  # beyond the data


class NoLeakage(Base):
    def test_backdated_prediction_ignores_data_after_as_of(self):
        a = self.sim(as_of='2029-06-01', horizon_months=6, iterations=100, funnel_scenarios=10)
        db = sqlite3.connect(self.db)
        try:  # add events after the as_of; the backdated simulation must not move
            db.executemany('INSERT INTO utilization_event VALUES (?,?,?,?)', [('HFX01', 'inpatient', '2029-08-01', '2029-08-03')] * 5)
            db.commit()
            b = self.sim(as_of='2029-06-01', horizon_months=6, iterations=100, funnel_scenarios=10)
        finally:
            db.execute("DELETE FROM utilization_event WHERE patient_id = 'HFX01' AND event_date = '2029-08-01'")
            db.commit()
            db.close()
        self.assertEqual(a['summary_table'], b['summary_table'])
        self.assertEqual(a['calibration']['outcomes'], b['calibration']['outcomes'])


class Api(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        d = test_plans._workspace(self.root)
        add_history(d / 'semantic.sqlite3')

    def tearDown(self):
        self.tmp.cleanup()

    def test_simulate_route_and_compare(self):
        from clinical_core.agere import workspace_api
        code, out = workspace_api.handle('POST', '/api/workspaces/plan-clinic/simulate',
                                         {'program_kind': 'pharmacy_gdmt', 'as_of': '2029-06-01', 'horizon_months': 6, 'iterations': 100,
                                          'funnel_scenarios': 10, 'alternatives': ['plan', 'pharmd_barrier_review']}, root=self.root)
        self.assertEqual(code, 200, out)
        self.assertIn('summary_table', out)
        self.assertIn('distributions', out)
        self.assertNotIn('inputs', out)
        self.assertIsNone(out['per_patient'])
        self.assertEqual([r['arm'] for r in out['summary_table']], ['do_nothing', 'plan', 'pharmd_barrier_review'])
        self.assertTrue((Path(self.root) / 'plan-clinic' / out['saved']).is_file())
        code, rep = workspace_api.handle('POST', '/api/workspaces/plan-clinic/simulate',
                                         {'simulation_id': out['id'], 'observed_through': '2029-12-01'}, root=self.root)
        self.assertEqual(code, 200, rep)
        self.assertEqual(rep['simulation_id'], out['id'])
        self.assertEqual(workspace_api.handle('POST', '/api/workspaces/plan-clinic/simulate', {'simulation_id': 'SIM-NOPE'}, root=self.root)[0], 404)
        self.assertEqual(workspace_api.handle('POST', '/api/workspaces/plan-clinic/simulate', {}, root=self.root)[0], 400)
        self.assertEqual(workspace_api.handle('POST', '/api/workspaces/plan-clinic/simulate',
                                              {'program_kind': 'pharmacy_gdmt', 'horizon_months': 99}, root=self.root)[0], 400)
        self.assertEqual(workspace_api.handle('POST', '/api/workspaces/plan-clinic/simulate', {'program_kind': 'pharmacy_gdmt'},
                                              root=self.root, can_write=False)[0], 403)

    def test_workspace_simulate_accepts_kind_or_plan(self):
        from clinical_core.agere.onboarding.workspace import Workspace
        from clinical_core.agere.plans import router
        ws = Workspace('plan-clinic', self.root)
        a = ws.simulate('virtual_pt', horizon_months=6, iterations=100, funnel_scenarios=10, as_of='2030-05-27', save=False)
        self.assertEqual(a['program_kind'], 'virtual_pt')
        plan = router.route(ws.semantic_path, router.candidates(ws.semantic_path, 'virtual_pt', '2030-05-27'), 'virtual_pt', '2030-05-27')
        b = ws.simulate(plan, horizon_months=6, iterations=100, funnel_scenarios=10, save=False)
        self.assertEqual(a['summary_table'], b['summary_table'])


if __name__ == '__main__':
    unittest.main()


class AccessOap(unittest.TestCase):
    def test_oap_paid_in_full_at_threshold_and_prorated_below(self):
        import numpy as np
        oat, withheld = 0.5, 0.5
        for share, expect in ((0.6, 1.0), (0.5, 1.0), (0.25, 0.75), (0.0, 0.5)):
            paid = (1 - withheld) + withheld * np.minimum(1.0, share / oat)
            self.assertAlmostEqual(float(paid), expect)
