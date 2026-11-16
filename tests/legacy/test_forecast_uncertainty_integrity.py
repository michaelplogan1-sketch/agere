"""Authored probability and state-space identities; no learned forecaster or patient data."""
import math
from statistics import NormalDist
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np
from clinical_core.agere.clinical import conformal as C, trajectory as T, foundation_forecasters as F, demand as D


class UniformGrid:
    def uniform(self, low, high, shape):
        n = math.prod(shape)
        return (low + (high - low) * (np.arange(n) + 0.5) / n).reshape(shape)


class Uncertainty(unittest.TestCase):
    def points(self):
        return [(i * 30, 10.0) for i in range(36)]

    def test_close_scores_keep_exact_rank_and_tie_classes(self):
        scores = np.array([0.50001] * 100 + [0.50002] * 100)
        labels = np.array([0] * 100 + [1] * 100)
        p0, p1, merged = C.venn_abers(scores, labels, [0.500009, 0.500021])
        np.testing.assert_allclose(p0, [0, 100 / 101])
        np.testing.assert_allclose(p1, [1 / 101, 1])
        np.testing.assert_allclose(merged, [1 / 102, 101 / 102])

    def test_exact_rank_cache_matches_uncached_ties_gaps_and_permutations(self):
        scores = np.array([0.2, 0.2, 0.5, 0.7, 0.7])
        labels = np.array([0, 1, 0, 1, 1])
        tests = np.array([0.1, 0.199999, 0.2, 0.200001, 0.4, 0.5, 0.6, 0.7, 0.700001, 0.99])
        exact = []
        for t in tests:
            values = []
            for label in (0, 1):
                x, y = C._pav(np.append(scores, t), np.append(labels, label))
                values.append(y[np.searchsorted(x, t)])
            exact.append(values)
        actual = C.venn_abers(scores, labels, tests)
        np.testing.assert_array_equal(actual[0], np.array(exact)[:, 0])
        np.testing.assert_array_equal(actual[1], np.array(exact)[:, 1])
        reordered = C.venn_abers(scores[::-1], labels[::-1], tests)
        for a, b in zip(actual, reordered):
            np.testing.assert_array_equal(a, b)

    def test_equivalent_ranks_need_only_two_isotonic_fits(self):
        with patch.object(C, '_pav', wraps=C._pav) as calls:
            result = C.venn_abers([0.1, 0.2], [0, 1], np.linspace(0.6, 0.9, 64))
        self.assertEqual(calls.call_count, 2)
        self.assertTrue(np.all(result[2] == result[2][0]))

    def test_unsettled_calibration_label_cannot_be_a_binary_outcome(self):
        with self.assertRaises(ValueError):
            C.venn_abers([0.1, 0.2], [0, np.nan], [0.5])

    def test_projection_retains_exact_future_process_variance(self):
        points = self.points()
        x, P, _ = T.kalman(points, 0, 1)
        h = 1095
        mean, sd = T._predict(points, 0, 1, h)
        row = np.array([1.0, h])
        expected_added = 0.25 / 365 * h + 1 / 365 ** 3 * h ** 3 / 3
        self.assertAlmostEqual(sd ** 2 - row @ P @ row, expected_added)
        self.assertAlmostEqual(expected_added, 9.75)
        self.assertAlmostEqual(mean, 10)
        self.assertAlmostEqual(sd, 4.3937621332153105)

    def test_zero_horizon_preserves_filtered_state_uncertainty(self):
        x, P, _ = T.kalman(self.points(), 0, 1)
        mean, sd = T._predict(self.points(), 0, 1, 0)
        self.assertAlmostEqual(mean, x[0])
        self.assertAlmostEqual(sd, math.sqrt(P[0, 0]))

    def test_live_calibration_receives_unrounded_probability(self):
        points = self.points()
        mean, sd = T._predict(points, 0, 1, 1095)
        threshold = mean + sd * NormalDist().inv_cdf(0.500021)
        row = T.forecast(points, 0, 1, 1095, threshold)
        self.assertEqual(row['p_cross'], 0.5)
        self.assertAlmostEqual(row['_p_cross'], 0.500021)
        scores = np.array([0.50001] * 100 + [0.50002] * 100)
        labels = np.array([0] * 100 + [1] * 100)
        result = T.apply_calibration({**row, '_mean': mean, '_sd': sd},
                                     {'available': True, '_q': 1, '_venn_abers': (scores, labels)})
        self.assertEqual(result['p_cross_calibrated'], 0.99)
        self.assertNotIn('_p_cross', result)

    def test_private_raw_probability_does_not_leak_without_calibration(self):
        row = T.apply_calibration(T.forecast(self.points(), 0, 1, 365, 6), {'available': False})
        self.assertNotIn('_p_cross', row)

    def test_outreach_calibration_keeps_raw_score_until_final_display(self):
        from clinical_core.agere.clinical import outreach as O
        rows = [{'reasons': [{'_concept': 'egfr', '_mean': 10, '_sd': 1,
                              '_p_cross': 0.500021, 'p_cross': 0.5}]}]
        scores = np.array([0.50001] * 100 + [0.50002] * 100)
        labels = np.array([0] * 100 + [1] * 100)
        O._apply_rising(rows, {'egfr': {'available': True, '_q': 1, '_venn_abers': (scores, labels)}})
        reason = rows[0]['reasons'][0]
        self.assertEqual(reason['p_cross_calibrated'], 0.99)
        self.assertFalse(any(k.startswith('_') for k in reason))

    def test_forecast_matches_unrounded_shared_projection(self):
        mean, sd = T._predict(self.points(), 0, 1, 1095)
        result = T.forecast(self.points(), 0, 1, 1095, 6)
        self.assertEqual(result['forecast_sd'], round(sd, 2))
        self.assertAlmostEqual(result['_p_cross'], NormalDist(mean, sd).cdf(6))

    def test_both_quantile_adapters_preserve_original_probability_axis(self):
        levels = F.LEVELS
        for cls in (F.Challenger, D.Timestral):
            stub = SimpleNamespace(_quantiles=lambda h: np.tile(levels * 100, (h, 1)), _levels=levels)
            samples = cls.simulate(stub, 1, 1000, UniformGrid())[:, 0]
            np.testing.assert_allclose(np.quantile(samples, [0.1, 0.9]), [10, 90], atol=0.05)
            self.assertEqual(stub.simulation_assumptions['tails'], 'linear_extrapolation')
            self.assertFalse(stub.simulation_assumptions['tail_coverage_qualified'])

    def test_full_probability_support_needs_no_tail_extrapolation(self):
        levels = np.linspace(0, 1, 21)
        samples, assumptions = F.quantile_samples(np.array([levels * 100]), levels, 1000, UniformGrid())
        np.testing.assert_allclose(np.quantile(samples[:, 0], [0.025, 0.975]), [2.5, 97.5], atol=0.05)
        self.assertEqual(assumptions['tails'], 'supplied_full_range')

    def test_invalid_probability_grid_is_not_renormalized(self):
        with self.assertRaises(ValueError):
            F.quantile_samples([[1, 2]], [0.2, 0.2], 100, UniformGrid())

    def test_thin_history_forecast_discloses_unprovided_tail_assumption(self):
        class AuthoredQuantileModel:
            def point(self, h):
                return np.full(h, 50.0)
            def simulate(self, h, n, rng):
                draws, self.simulation_assumptions = F.quantile_samples(np.tile(F.LEVELS * 100, (h, 1)), F.LEVELS, n, rng)
                return draws
        records = {'naive': [{'scaled_error': 1, 'actual': 50, 'point': 50, 'sd': 23,
                              'origin': 39, 'target': 40, 'model_cover': {0.8: True, 0.95: True}}]}
        with patch.object(D, 'backtest', return_value=(records, [39], False, 'naive')), \
                patch.object(D, 'candidates', return_value=[('naive', lambda y: AuthoredQuantileModel())]):
            result = D.forecast_series([f'{2020+i//12}-{i%12+1:02d}' for i in range(40)],
                                      [50.0] * 40, 'month', 1)
        self.assertEqual(result['simulation_assumptions']['tails'], 'linear_extrapolation')
        self.assertIn('unqualified_forecast_tails', [r['kind'] for r in result['data_issues']])


if __name__ == '__main__':
    unittest.main()
