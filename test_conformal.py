"""Conformal calibration: finite-sample coverage of the math on simulated exchangeable data, Mondrian group coverage,
ACI under drift, probability calibration, and the wiring into trajectory / outreach / vbc / the calibration operation
on synthetic fixture stacks (time-split: nothing after a prediction's issue date is used to calibrate it)."""
import json, math, random, unittest
from datetime import date
from pathlib import Path

import numpy as np

from clinical_core.agere.clinical import (analysis_graph, calibration, capability_registry, conformal, graph_contracts, graph_planner,
                                          local_compiler, outreach, semantic_layer, source_profile, trajectory, vbc)
from test_aggregate_explain import build, pcp, visit

AS_OF = '2029-08-15'
ROOT = Path(__file__).resolve().parent


class SplitConformal(unittest.TestCase):
    def test_marginal_coverage_at_least_nominal_across_seeds(self):
        for alpha in (0.1, 0.2):
            covs = []
            for seed in range(300):
                rng = np.random.default_rng(seed)
                n_cal, n_test = 50, 200
                x = rng.uniform(0, 4, n_cal + n_test)
                y = 2 * x + rng.standard_t(3, n_cal + n_test) * (0.5 + x)   # heavy tails, heteroscedastic, model misspecified
                pred = 2.2 * x
                cal = conformal.split_conformal(y[:n_cal], pred[:n_cal], alpha)
                lo, hi = conformal.interval(pred[n_cal:], cal['q'])
                covs.append(conformal.coverage(y[n_cal:], lo, hi))
            # E[coverage] >= 1 - alpha exactly; the mean over 300 seeds has SE ~ 0.003
            self.assertGreaterEqual(np.mean(covs), 1 - alpha - 0.006, alpha)
            self.assertLess(np.mean(covs), 1 - alpha + 1 / 51 + 0.01)  # and not wildly conservative (upper bound 1 - alpha + 1/(n+1))

    def test_normalized_scores_cover_and_adapt_width(self):
        covs, ratio = [], []
        for seed in range(200):
            rng = np.random.default_rng(1000 + seed)
            x = rng.uniform(0, 4, 300)
            sd = 0.2 + x
            y = x + rng.normal(0, sd)
            cal = conformal.split_conformal(y[:100], x[:100], 0.1, sd[:100])
            lo, hi = conformal.interval(x[100:], cal['q'], sd[100:])
            covs.append(conformal.coverage(y[100:], lo, hi))
            ratio.append(np.mean((hi - lo)[x[100:] > 3]) / np.mean((hi - lo)[x[100:] < 1]))
        self.assertGreaterEqual(np.mean(covs), 0.894)
        self.assertGreater(np.mean(ratio), 3)

    def test_quantile_edge_cases(self):
        self.assertEqual(conformal.conformal_quantile([1, 2, 3], 0.1), math.inf)  # n too small for 90%
        self.assertEqual(conformal.conformal_quantile(list(range(1, 20)), 0.1), 18.0)  # ceil(20 x 0.9) = 18th

    def test_cv_plus_and_jackknife_plus(self):
        fit = lambda X, y: np.polyfit(X[:, 0], y, 1)
        predict = lambda m, X: np.polyval(m, X[:, 0])
        covs_cv, covs_jk = [], []
        for seed in range(60):
            rng = np.random.default_rng(seed)
            X = rng.uniform(0, 1, (40, 1)); y = 3 * X[:, 0] + rng.normal(0, 0.3, 40)
            Xn = rng.uniform(0, 1, (100, 1)); yn = 3 * Xn[:, 0] + rng.normal(0, 0.3, 100)
            lo, hi = conformal.cv_plus(X, y, fit, predict, Xn, 0.1, folds=10, seed=seed)
            covs_cv.append(conformal.coverage(yn, lo, hi))
            lo, hi = conformal.jackknife_plus(X, y, fit, predict, Xn, 0.1)
            covs_jk.append(conformal.coverage(yn, lo, hi))
        self.assertGreaterEqual(np.mean(covs_cv), 0.8)   # guarantee 1 - 2 alpha
        self.assertGreaterEqual(np.mean(covs_jk), 0.8)
        self.assertGreaterEqual(np.mean(covs_jk), 0.88)  # in practice close to 1 - alpha

    def test_cqr_corrects_undercovering_quantile_band(self):
        covs = []
        for seed in range(200):
            rng = np.random.default_rng(seed)
            x = rng.uniform(0, 2, 400); y = x + rng.normal(0, 1, 400)
            lo, hi = x - 0.5, x + 0.5  # a too-narrow "quantile regression" band
            q = conformal.cqr(y[:150], lo[:150], hi[:150], 0.1)
            covs.append(conformal.coverage(y[150:], lo[150:] - q, hi[150:] + q))
        self.assertGreaterEqual(np.mean(covs), 0.894)


class Mondrian(unittest.TestCase):
    def test_group_conditional_coverage(self):
        pooled_noisy, mondrian_cov = [], {'quiet': [], 'noisy': []}
        for seed in range(200):
            rng = np.random.default_rng(seed)
            g = rng.choice(['quiet', 'noisy'], 600, p=[0.8, 0.2])
            y = rng.normal(0, np.where(g == 'noisy', 4.0, 1.0))
            s = np.abs(y)
            cal, test = slice(0, 300), slice(300, 600)
            table = conformal.mondrian(s[cal], g[cal], 0.1, min_group=20)
            q = np.array([conformal.mondrian_q(table, x) for x in g[test]])
            hit = s[test] <= q
            for name in mondrian_cov: mondrian_cov[name].append(hit[g[test] == name].mean())
            pooled = conformal.conformal_quantile(s[cal], 0.1)
            pooled_noisy.append((s[test] <= pooled)[g[test] == 'noisy'].mean())
        for name, covs in mondrian_cov.items():
            self.assertGreaterEqual(np.mean(covs), 0.89, name)
        self.assertLess(np.mean(pooled_noisy), 0.8)  # pooled conformal undercovers the noisy group

    def test_small_groups_fall_back_to_pooled(self):
        table = conformal.mondrian(np.arange(100.0), ['a'] * 95 + ['b'] * 5, 0.1, min_group=30)
        self.assertTrue(table['groups']['b']['pooled'])
        self.assertEqual(table['groups']['b']['q'], table['pooled_q'])
        self.assertEqual(conformal.mondrian_q(table, 'never seen'), table['pooled_q'])


class Adaptive(unittest.TestCase):
    def test_aci_tracks_a_drifting_series(self):
        rng = np.random.default_rng(4)
        T = 3000
        sd = np.linspace(1.0, 4.0, T)  # noise grows steadily: a frozen calibration falls behind
        y = rng.normal(0, sd)
        pred = np.zeros(T)
        a = conformal.aci(y, pred, alpha=0.1, gamma=0.01, warmup=100)
        self.assertLess(abs(a['coverage'] - 0.9), 0.02)
        frozen = conformal.split_conformal(y[:100], pred[:100], 0.1)
        frozen_cov = conformal.coverage(y[100:], *conformal.interval(pred[100:], frozen['q']))
        self.assertLess(frozen_cov, 0.75)
        late = slice(T - 500, T)
        self.assertGreater(np.mean((np.abs(y) <= (a['hi'] - pred))[late]), 0.86)


class Probabilities(unittest.TestCase):
    def test_isotonic_is_monotone_and_venn_abers_recalibrates(self):
        rng = np.random.default_rng(7)
        true = rng.uniform(0, 1, 4000)
        raw = true ** 3  # badly miscalibrated (too low) but correctly ranked
        y = (rng.uniform(0, 1, 4000) < true).astype(float)
        iso = conformal.Isotonic(raw[:2000], y[:2000])
        grid = np.linspace(0, 1, 50)
        self.assertTrue(np.all(np.diff(iso(grid)) >= -1e-12))
        p0, p1, merged = conformal.venn_abers(raw[:2000], y[:2000], raw[2000:])
        self.assertTrue(np.all(p0 <= p1 + 1e-12))
        self.assertTrue(np.all((merged >= p0 - 1e-9) & (merged <= p1 + 1e-9)))
        self.assertLess(conformal.ece(merged, y[2000:]), conformal.ece(raw[2000:], y[2000:]) / 3)
        self.assertLess(conformal.brier(merged, y[2000:]), conformal.brier(raw[2000:], y[2000:]))
        rows = conformal.reliability(merged, y[2000:], 10)
        self.assertEqual(sum(r['cases'] for r in rows), 2000)

    def test_conformal_risk_control_bounds_miss_rate(self):
        misses = []
        for seed in range(200):
            rng = np.random.default_rng(seed)
            y = rng.uniform(0, 1, 800) < 0.3
            p = np.clip(np.where(y, 0.6, 0.3) + rng.normal(0, 0.2, 800), 0, 1)
            lam = conformal.risk_control_threshold(p[:400], y[:400], 0.1)
            misses.append(np.mean(p[400:][y[400:]] < lam))
        self.assertLessEqual(np.mean(misses), 0.1 + 0.005)
        self.assertIsNone(conformal.risk_control_threshold([0.2, 0.3], [0, 0], 0.1))


# ---- fixture stack: three years of labs, utilization, claims and enrolment -------------------------------------
def _fixture():
    rng = random.Random(12)
    patients, recs = {}, []
    months = [(y, m) for y in range(2026, 2030) for m in range(1, 13) if (y, m) <= (2029, 8)]
    for i in range(140):
        pid = f'p{i:03d}'
        age = rng.choice([30, 50, 70, 80])
        patients[pid] = {'age_reported': age, 'sex_reported': rng.choice('FM')}
        recs.append((pid, 'coverage', {'coverage_type': 'medicare' if age >= 65 else 'commercial', 'status': 'active',
                                       'effective_start': '2025-06-01', 'event_date': '2025-06-01'}))
        recs.append(pcp(pid, f'dr{i % 4}', f'PR{i % 4}'))
        if i % 3 == 0: recs.append((pid, 'conditions', {'description': 'Diabetes mellitus type 2', 'event_date': '2025-01-01'}))
        base, slope = rng.uniform(25, 80), rng.uniform(-9, 2)
        for k in range(15):  # quarterly eGFR, 2026-01 .. 2029-07
            y, m = 2026 + (k * 3) // 12, (k * 3) % 12 + 1
            v = max(5.0, base + slope * k / 4 + rng.gauss(0, 3.5))
            recs.append((pid, 'labs', {'test': 'eGFR', 'value': round(v, 1), 'unit': 'mL/min/1.73m2', 'event_date': f'{y}-{m:02d}-1{rng.randint(0, 9)}'}))
        frail = 1 + (age >= 70) + (i % 3 == 0)
        for y, m in months:
            day = f'{y}-{m:02d}-{rng.randint(10, 25)}'
            if rng.random() < 0.02 * frail: recs.append(visit(pid, 'emergency', day))
            if rng.random() < 0.006 * frail: recs.append(visit(pid, 'inpatient', day, 'H1', f'{y}-{m:02d}-27'))
            if rng.random() < 0.3:
                cents = int(rng.lognormvariate(10, 1) * frail)
                recs.append((pid, 'medical_claims', {'service_date': day, 'allowed_cents': cents, 'payer_paid_cents': int(cents * 0.8),
                                                     'status': 'paid', 'event_date': day, 'available_date': day}))
    for y, m in months:  # someone uses the ED every month: no feed gap in this fixture
        recs.append(visit('p000', 'emergency', f'{y}-{m:02d}-05'))
    path = build(recs, patients)
    semantic_layer.build(path)
    return path


class Wiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = _fixture()

    def test_trajectory_forecast_carries_conformal_fields_and_states_coverage(self):
        r = trajectory.execute(self.path, None, {'concept': 'egfr', 'threshold': 30.0, 'horizon_days': 365})
        c = r['conformal']
        self.assertTrue(c['available'], c)
        self.assertIsNotNone(c['backtest_coverage'])
        self.assertGreater(c['backtest_coverage'], 0.75)
        self.assertIn('held-out values', c['statement'])
        self.assertTrue(r['patients'], 'fixture has declining patients')
        for row in r['patients']:
            self.assertTrue({'forecast', 'forecast_sd', 'p_cross'} <= set(row))  # existing fields untouched
            self.assertLessEqual(row['conformal_low'], row['forecast'] + 0.01)
            self.assertGreaterEqual(row['conformal_high'], row['forecast'] - 0.01)
            self.assertFalse(any(k.startswith('_') for k in row))
        self.assertIn('Split conformal', r['method'])
        plan = analysis_graph.validate({'interpretation': 't', 'coverage': 'supported_observation', 'unmet_requirements': [], 'nodes': [
            {'id': 'population', 'operation': 'cohort', 'inputs': [], 'purpose': 'all', 'parameters': {'specification': {'all': [], 'any': [], 'exclude': []}}},
            {'id': 'answer', 'operation': 'trajectory', 'inputs': ['population'], 'purpose': 'forecast',
             'parameters': {'concept': 'egfr', 'threshold': 30.0, 'horizon_days': 365}}]}, AS_OF)
        answer = analysis_graph.build_answer(plan, [{'node': plan['nodes'][0], 'result': {}}, {'node': plan['nodes'][1], 'result': r}], {}, 'q')
        self.assertRegex(answer['value'], r'90% intervals covered \d+\.\d% of \d+ held-out values')
        self.assertEqual(answer['overall']['calibration']['statement'], c['statement'])
        json.dumps(answer, allow_nan=False, default=str)

    def test_time_split_never_uses_later_data(self):
        series = {'a': {'2028-01-01': [50.0], '2028-06-01': [48.0], '2028-12-01': [47.0], '2029-03-01': [40.0], '2029-08-01': [10.0]}}
        origin = date(2029, 1, 1).toordinal()
        pairs = trajectory.backtest_pairs(series, origin, 120, 0.08, 3.0, 3, 30.0, True)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]['y'], 40.0)  # last value inside (origin, origin + window], not the 2029-08 one
        future_free = trajectory._predict([(date.fromisoformat(d).toordinal(), v[0]) for d, v in series['a'].items()][:3], 0.08, 3.0,
                                          date(2029, 3, 1).toordinal() - date(2028, 12, 1).toordinal())
        self.assertAlmostEqual(pairs[0]['mean'], future_free[0])

    def test_outreach_rows_carry_conformal_expected_value_interval(self):
        r = outreach.execute(self.path, None, {'capacity_patients': 20}, AS_OF)
        c = r['conformal']
        self.assertTrue(c['available'], c)
        self.assertEqual(c['calibration_origin'], '2028-08-15')
        self.assertLessEqual(date.fromisoformat(c['backtest_origin']).toordinal(), date(2028, 8, 15).toordinal() - 365)
        self.assertIsNotNone(c['backtest_coverage'])
        self.assertGreater(c['backtest_coverage'], 0.75)
        self.assertTrue(set(c['backtest_by_group']))
        self.assertIn('egfr', c['rising_risk'])
        for w in r['worklist']:
            lo, hi = w['expected_value_interval_90']
            self.assertLessEqual(lo, w['expected_value'] + 0.01)
            self.assertGreaterEqual(hi, w['expected_value'] - 0.01)
            ulo, uhi = w['utilization_value_interval_90']
            self.assertLessEqual(ulo, uhi)
            for reason in w['reasons']:
                self.assertFalse(any(k.startswith('_') for k in reason))
        self.assertIn('covered', r['headline'])
        self.assertIn('Mondrian', r['method'])
        json.dumps(r, allow_nan=False, default=str)

    def test_outreach_feed_gap_is_not_used_for_calibration(self):
        path = build([(f'q{i}', 'coverage', {'coverage_type': 'medicare', 'status': 'active', 'effective_start': '2026-01-01',
                                             'event_date': '2026-01-01'}) for i in range(30)] +
                     [visit('q1', 'emergency', '2029-07-01')], {f'q{i}': {'age_reported': 70} for i in range(30)})
        semantic_layer.build(path)
        r = outreach.execute(path, None, {'capacity_patients': 5}, AS_OF)
        self.assertFalse(r['conformal']['available'])
        self.assertIn('Conformal calibration unavailable', r['conformal']['statement'])

    def test_vbc_risk_rows_gain_intervals(self):
        r = vbc.execute(self.path, None, {'metric': 'risk', 'group_by': 'age_band'}, AS_OF)
        c = r['conformal']
        self.assertTrue(c['available'], c)
        self.assertIsNotNone(c['backtest_coverage'])
        for row in r['rows']:
            self.assertIn('mean_risk_score', row)
            self.assertIn('predicted_cost_per_member_year', row)
            lo, hi = row['member_interval_90_median']
            self.assertLessEqual(lo, hi)
        self.assertIn('held-out values', r['method'])

    def test_calibration_operation_end_to_end(self):
        plan = analysis_graph.validate({'interpretation': 'how calibrated', 'coverage': 'supported_observation', 'unmet_requirements': [],
                                        'nodes': [{'id': 'answer', 'operation': 'calibration', 'inputs': [], 'parameters': {}, 'purpose': 'calibration'}]}, AS_OF)
        report = calibration.execute(self.path, None, plan['nodes'][0]['parameters'], AS_OF)
        self.assertEqual(len(report['rows']), 5)
        answer = analysis_graph.build_answer(plan, [{'node': plan['nodes'][0], 'result': report}], {}, 'How well calibrated are our risk scores?')
        self.assertEqual((answer['status'], answer['answer_type']), ('answered', 'table'))
        self.assertIn('conformal intervals covered', answer['value'])
        self.assertTrue(all({'prediction', 'backtest_coverage', 'brier_model', 'ece_calibrated', 'aci_coverage'} <= set(r) for r in answer['table']))
        self.assertIn('Conformal', analysis_graph.headline(answer) + ' ' + answer['value'].replace('conformal', 'Conformal'))
        json.dumps(answer, allow_nan=False, default=str)
        one = calibration.execute(self.path, None, calibration.validate({'concept': 'egfr', 'threshold': 45, 'direction': 'below'}), AS_OF)
        self.assertEqual([r['prediction'].split(' ')[0] for r in one['rows']], ['egfr'])
        with self.assertRaises(ValueError): calibration.validate({'target': 'everything'})
        with self.assertRaises(ValueError): calibration.validate({'concept': 'egfr'})


class Integration(unittest.TestCase):
    def test_registries_and_contracts(self):
        self.assertIn('calibration', analysis_graph.OPERATIONS)
        self.assertIn('calibration', graph_contracts.TYPED)
        self.assertEqual(graph_contracts.input_contract('calibration')['count'], [0, 1])
        schema = graph_contracts.parameter_schema('calibration', {'cohort_fields': {'record_fields': {}}, 'evidence_catalog': {}})
        self.assertEqual(set(schema['properties']['target']['enum']), set(calibration.TARGETS))
        self.assertIn('calibration', graph_planner.CONTRACTS)
        self.assertIn('calibration', capability_registry.PRODUCT)
        self.assertTrue(analysis_graph.code_identity())
        from clinical_core.agere import ask_view
        html = ask_view.page()
        self.assertIn('data-calibration', html)  # console renders the calibration statement under every answer that has one
        self.assertIn('expected_value_interval_90', html)

    def test_acceptance_phrasings_route_to_calibration(self):
        path = build([('p1', 'conditions', {'description': 'Diabetes mellitus type 2', 'event_date': '2027-01-01'})], {'p1': {'age_reported': 60}})
        profile = source_profile.load(path)
        cases = json.loads((ROOT / 'bench' / 'acceptance' / 'calibration.json').read_text())
        self.assertEqual(len(cases), 8)
        for case in cases:
            g, info = local_compiler.compile(case['question'], profile, AS_OF)
            self.assertEqual([n['operation'] for n in g['nodes']], [case['expect_operation']], case['question'])
            analysis_graph.validate(g, AS_OF)
        for q in ('What is the externally validated, calibrated probability that P-4007 will be readmitted next year?',
                  'How many patients have an A1c over 9?', 'Who should our care managers call this week? 40 slots'):
            self.assertIsNone(local_compiler._calibration_question(q), q)


if __name__ == '__main__':
    unittest.main()
