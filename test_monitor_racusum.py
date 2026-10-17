"""Risk-adjusted CUSUM and VLAD in the monitor engine: weights, simulated control limit / ARL0, detection of a step to OR 2,
VLAD drift, risk adjustment against case-mix false alarms, expected-risk models, parameters and an engine smoke run."""
import math, shutil, unittest

import numpy as np

from clinical_core.agere.clinical import monitor, aggregate
from test_risk_outcomes import fixture, AS_OF


def case_mix(n, rng, a=1.0, b=5.0):
    """Heterogeneous expected risks (right-skewed, 1%..80%)."""
    return np.clip(rng.beta(a, b, n), 0.01, 0.8)


def outcomes(p, rng, odds_ratio=1.0):
    """Bernoulli outcomes at expected risk p after multiplying the odds by odds_ratio."""
    odds = p / (1 - p) * odds_ratio
    return (rng.random(len(p)) < odds / (1 + odds)).astype(float)


class Weights(unittest.TestCase):
    def test_log_likelihood_ratio_weights(self):
        p, R = 0.2, 2.0
        w1 = monitor._ra_weights([p], [1], R)[0]; w0 = monitor._ra_weights([p], [0], R)[0]
        self.assertAlmostEqual(w1, math.log(R / (1 - p + R * p)))
        self.assertAlmostEqual(w0, math.log(1 / (1 - p + R * p)))
        # An event is worth more evidence of deterioration when it was unlikely; a non-event costs more when the case was risky.
        self.assertGreater(monitor._ra_weights([0.05], [1], R)[0], w1)
        self.assertLess(monitor._ra_weights([0.6], [0], R)[0], w0)
        np.testing.assert_allclose(monitor._ra_weights([0.1, 0.5], [1, 0], 1.0), 0.0, atol=1e-12)
        # Expected weight is positive when the true odds ratio is R, negative in control.
        self.assertLess(p * w1 + (1 - p) * w0, 0)

    def test_cusum_restarts_after_signal(self):
        c = monitor.ra_cusum([0.1] * 40, [1] * 40, 2.0, 2.0)          # event after event at 10% risk: signals repeatedly
        self.assertGreater(len(c['signals']), 2)
        self.assertTrue(all(c['path'][i] >= 2.0 for i in c['signals']))
        i = c['signals'][0]
        self.assertLess(c['path'][i + 1], c['path'][i])               # reset to zero, then climbs again
        self.assertEqual(monitor.ra_cusum([0.1] * 40, [0] * 40, 2.0, 2.0)['signals'], [])
        imp = monitor.ra_cusum([0.5] * 40, [0] * 40, 0.5, 2.0)        # improvement chart: OR 0.5, no events at 50% risk
        self.assertTrue(imp['signals'])


class ControlLimit(unittest.TestCase):
    def test_in_control_arl_matches_target(self):
        p = case_mix(6000, np.random.default_rng(1))
        lim = monitor.simulate_control_limit(p, 2.0, 1000, seed=5)
        self.assertGreater(lim['h'], 2.0); self.assertLess(lim['h'], 8.0)
        arl = monitor.simulate_arl(p, 2.0, lim['h'], n_chains=300, length=4000, seed=1234)   # different seed, resets
        self.assertAlmostEqual(arl / 1000, 1.0, delta=0.2)

    def test_arl_from_plain_cusum_function(self):
        rng = np.random.default_rng(2)
        p = case_mix(6000, rng)
        h = monitor.simulate_control_limit(p, 2.0, 500, seed=6)['h']
        signals = cases = 0
        for _ in range(50):
            pp = rng.choice(p, 4000); y = outcomes(pp, rng)
            signals += len(monitor.ra_cusum(pp, y, 2.0, h)['signals']); cases += 4000
        self.assertAlmostEqual(cases / signals / 500, 1.0, delta=0.25)

    def test_larger_arl0_needs_a_higher_limit(self):
        p = case_mix(4000, np.random.default_rng(3))
        hs = [monitor.simulate_control_limit(p, 2.0, a, seed=7)['h'] for a in (200, 1000, 5000)]
        self.assertLess(hs[0], hs[1]); self.assertLess(hs[1], hs[2])
        self.assertEqual(monitor.simulate_control_limit(p, 2.0, 1000, seed=7), monitor.simulate_control_limit(p, 2.0, 1000, seed=7))


class Detection(unittest.TestCase):
    def test_step_to_odds_ratio_two_at_case_500_signals(self):
        rng = np.random.default_rng(10)
        pool = case_mix(8000, rng)
        h = monitor.simulate_control_limit(pool, 2.0, 2000, seed=9)['h']
        delays, missed = [], 0
        for _ in range(100):
            p = rng.choice(pool, 1500)
            y = np.concatenate([outcomes(p[:500], rng), outcomes(p[500:], rng, 2.0)])
            hit = [i for i in monitor.ra_cusum(p, y, 2.0, h)['signals'] if i >= 500]
            if hit: delays.append(hit[0] - 500)
            else: missed += 1
        self.assertLessEqual(missed, 5)
        self.assertLess(float(np.median(delays)), 300)

    def test_in_control_unit_rarely_signals(self):
        rng = np.random.default_rng(11)
        pool = case_mix(8000, rng)
        h = monitor.simulate_control_limit(pool, 2.0, 5000, seed=9)['h']
        flagged = 0
        for _ in range(60):
            p = rng.choice(pool, 1000)
            flagged += bool(monitor.ra_cusum(p, outcomes(p, rng), 2.0, h)['signals'])
        self.assertLessEqual(flagged, 60 * 0.2 * 1.6)                # expected ~ 1000/5000 = 20% of units over 1,000 cases


class Vlad(unittest.TestCase):
    def test_vlad_drifts_negative_after_deterioration(self):
        rng = np.random.default_rng(21)
        p = case_mix(1500, rng)
        y = np.concatenate([outcomes(p[:500], rng), outcomes(p[500:], rng, 2.0)])
        v = monitor.vlad(p, y)
        self.assertLess(v['cum'][-1], v['cum'][499] - 20)
        self.assertLess(v['cum'][-1], -v['limits']['3.09'][-1])       # outside the 99.8% limit
        self.assertLess(abs(v['cum'][499]), 3 * v['sd'][499])         # in control before the step
        self.assertEqual(v['limits']['1.96'][10] / v['sd'][10], 1.96)
        chart = monitor.ra_chart('U', [(f'2029-01-{1 + i // 100:02d}', f'p{i}') for i in range(1500)], p, y,
                                 {'chart': 'vlad'}, {})
        sig = [s for s in chart['signals'] if s['direction'] == 'deterioration']
        self.assertTrue(sig); self.assertGreater(sig[0]['case_number'], 400)
        self.assertGreater(chart['vlad']['excess_events'], 20)

    def test_vlad_flat_in_control_and_improving(self):
        rng = np.random.default_rng(22)
        p = case_mix(1500, rng)
        v = monitor.vlad(p, outcomes(p, rng))
        self.assertLess(abs(v['z'][-1]), 3.09)
        better = monitor.vlad(p, outcomes(p, rng, 0.4))
        self.assertGreater(better['cum'][-1], 0)


def sick_healthy_cases(rng, n, sick_share):
    rows, eta = [], []
    for _ in range(n):
        sick = rng.random() < sick_share
        band = rng.choice(['18-44', '45-64', '65-74', '75+'])
        prior = int(rng.random() < (0.5 if sick else 0.1))
        conds = {'heart_failure', 'copd'} & set(aggregate.BURDEN_CONCEPTS) if sick else set()
        rows.append({'age_band': band, 'sex': 'M' if rng.random() < .5 else 'F', 'conditions': conds, 'prior': prior, 'observation': False})
        eta.append(-2.6 + 1.3 * sick + 0.7 * prior + {'18-44': -.3, '45-64': 0, '65-74': .2, '75+': .5}[band])
    return rows, 1 / (1 + np.exp(-np.array(eta)))


class CaseMix(unittest.TestCase):
    def test_risk_adjustment_prevents_false_alarms_for_sicker_unit(self):
        rng = np.random.default_rng(30)
        # Pooled data: 6 sick-only units and 6 healthy-ish units, identical true model (no unit effect).
        units = {}
        for k in range(12):
            rows, true_p = sick_healthy_cases(rng, 800, 1.0 if k < 6 else 0.15)
            units[k] = (rows, (rng.random(800) < true_p).astype(float))
        covs = [r for k in units for r in units[k][0]]; y = np.concatenate([units[k][1] for k in units])
        p, source = monitor.expected_risks(covs, y)
        self.assertEqual(source, 'risk_outcomes.fit_glm')
        h = monitor.simulate_control_limit(p, 2.0, 20000, seed=3)['h']
        crude = np.full(len(y), y.mean())                              # no case-mix adjustment: network average risk for everyone
        hc = monitor.simulate_control_limit(crude, 2.0, 20000, seed=3)['h']
        adj_flags = crude_flags = 0
        for k in range(6):
            sl = slice(k * 800, (k + 1) * 800)
            adj_flags += bool(monitor.ra_cusum(p[sl], y[sl], 2.0, h)['signals'])
            crude_flags += bool(monitor.ra_cusum(crude[sl], y[sl], 2.0, hc)['signals'])
        self.assertLessEqual(adj_flags, 1)
        self.assertGreaterEqual(crude_flags, 5)                        # the same sicker patients, unadjusted, look like deterioration
        self.assertLess(p[:4800].mean() / p[4800:].mean(), 10)
        self.assertGreater(p[:4800].mean(), 1.5 * p[4800:].mean())

    def test_fallback_logistic_model(self):
        rng = np.random.default_rng(31)
        rows, true_p = sick_healthy_cases(rng, 4000, 0.4)
        y = (rng.random(4000) < true_p).astype(float)
        pr = np.clip(monitor.fit_logistic(monitor._design_fallback(rows), y), 1e-4, 1 - 1e-4)
        self.assertAlmostEqual(pr.sum(), y.sum(), delta=1.0)
        self.assertGreater(np.corrcoef(pr, true_p)[0, 1], 0.8)
        saved, monitor._ro = monitor._ro, None
        try:
            p2, source = monitor.expected_risks(rows, y)
        finally:
            monitor._ro = saved
        self.assertEqual(source, 'monitor in-module logistic')
        np.testing.assert_allclose(p2, pr)


class Parameters(unittest.TestCase):
    def test_validate_defaults_and_errors(self):
        p = monitor.validate({'chart': 'ra_cusum'})
        self.assertEqual((p['chart'], p['outcome'], p['unit'], p['or_shift'], p['or_improve'], p['arl0']), ('ra_cusum', 'readmission', 'hospital', 2.0, None, 5000))
        self.assertEqual(monitor.validate({'check': 'vlad', 'group_by': 'practice', 'outcome': 'ed_revisit', 'or_improve': 0.5})['unit'], 'practice')
        self.assertEqual(monitor.validate({'check': 'vlad'})['chart'], 'vlad')
        self.assertNotIn('outcome', monitor.validate({'check': 'control_chart'}))     # existing behaviour unchanged
        for bad in ({'chart': 'x'}, {'chart': 'ra_cusum', 'or_shift': 0.5}, {'chart': 'ra_cusum', 'or_improve': 1.5}, {'chart': 'vlad', 'arl0': 5},
                    {'chart': 'ra_cusum', 'outcome': 'length_of_stay'}, {'chart': 'ra_cusum', 'unit': 'ward'}):
            with self.assertRaises(ValueError, msg=bad): monitor.validate(bad)


class EngineSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = fixture()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.path, ignore_errors=True)

    def test_ra_cusum_execute(self):
        r = monitor.execute(self.path, None, {'chart': 'ra_cusum', 'arl0': 200, 'min_cases': 10, 'or_improve': 0.5}, AS_OF)
        self.assertEqual(r['status'], 'answered')
        self.assertEqual({u['unit'] for u in r['units']}, {'HA', 'HB'})
        self.assertEqual(r['summary']['cases'], 94)
        self.assertGreater(r['control_limits']['h'], 0)
        self.assertIn('cusum_up', r['units'][0]); self.assertIn('cusum_down', r['units'][0])
        self.assertTrue(r['assumptions'])
        text, table = monitor.summarize(r)
        self.assertIn('Risk-adjusted CUSUM', text); self.assertEqual(len(table), 2)
        for s in r['signals']: self.assertTrue(s['date'] and s['unit'])

    def test_vlad_execute_and_unit_none(self):
        r = monitor.execute(self.path, None, {'check': 'vlad', 'min_cases': 10, 'unit': 'none'}, AS_OF)
        self.assertEqual(r['status'], 'answered'); self.assertEqual([u['unit'] for u in r['units']], ['all'])
        self.assertIn('vlad', r['units'][0])
        self.assertIn('VLAD', monitor.summarize(r)[0])

    def test_unsupported_mortality(self):
        r = monitor.execute(self.path, None, {'chart': 'ra_cusum', 'outcome': 'mortality'}, AS_OF)
        self.assertEqual(r['status'], 'unsupported')
        self.assertIn('death', monitor.summarize(r)[0])


if __name__ == '__main__':
    unittest.main()
