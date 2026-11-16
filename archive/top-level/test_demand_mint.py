"""Hierarchical forecast reconciliation (MinT) in the demand engine: summing matrix, shrinkage with more series than
observations, exact coherence, non-negativity, held-out accuracy on a synthetic 3-level hierarchy, and execute() parameters."""
import os, unittest

os.environ['AGERE_DEMAND_TIMESTRAL'] = 'off'   # classic candidates only: deterministic, no optional model download

import numpy as np

from clinical_core.agere.clinical import demand, semantic_layer
from test_demand import ed_records, months
from test_aggregate_explain import build

PATHS = [(), ('N',), ('S',), ('N', 'n1'), ('N', 'n2'), ('S', 's1'), ('S', 's2')]


def synthetic(seed, T=54):
    """Four noisy practice series sharing a seasonal level; regions and the total are sums (so the total is smoother)."""
    rng = np.random.default_rng(seed); t = np.arange(T)
    season = 1 + 0.25 * np.sin(2 * np.pi * t / 12) + 0.004 * t
    bottoms = np.array([rng.poisson(base * season) + np.round(rng.normal(0, 0.18 * base * season)) for base in (30, 45, 25, 60)])
    bottoms = np.maximum(bottoms, 0.0)
    return np.vstack([bottoms.sum(0), bottoms[:2].sum(0), bottoms[2:].sum(0), *bottoms])


def mase(actual, fc, train):
    scale = np.mean(np.abs(np.diff(train))); return float(np.mean(np.abs(actual - fc)) / scale)


class Structure(unittest.TestCase):
    def test_summing_matrix(self):
        S, parent, levels, bottoms = demand.hierarchy_structure(PATHS)
        self.assertEqual(S.shape, (7, 4)); self.assertEqual(bottoms, [3, 4, 5, 6])
        np.testing.assert_array_equal(S[0], [1, 1, 1, 1]); np.testing.assert_array_equal(S[1], [1, 1, 0, 0])
        np.testing.assert_array_equal(S[bottoms], np.eye(4)); self.assertEqual(parent, [None, 0, 0, 1, 1, 2, 2]); self.assertEqual(levels, [0, 1, 1, 2, 2, 2, 2])

    def test_projection_is_idempotent_on_coherent_input_and_methods_agree_when_coherent(self):
        S = demand.hierarchy_structure(PATHS)[0]; b = np.array([10.0, 20, 5, 15]); coherent = S @ b
        E = np.random.default_rng(0).normal(size=(30, 7))
        for m in ('ols', 'wls', 'mint_shrink'):
            G, info = demand.reconcile_gain(S, m, E)
            np.testing.assert_allclose(S @ G @ coherent, coherent, atol=1e-8); self.assertEqual(info['method'], m)
            np.testing.assert_allclose(G @ S, np.eye(4), atol=1e-8)   # unbiasedness: SGS = S

    def test_short_history_falls_back_to_wls(self):
        S = demand.hierarchy_structure(PATHS)[0]
        G, info = demand.reconcile_gain(S, 'mint_shrink', np.ones((demand.MIN_RESID - 1, 7)))
        self.assertEqual((info['method'], info['requested']), ('wls', 'mint_shrink'))
        self.assertEqual(demand.reconcile_gain(S, 'mint_shrink', None)[1]['method'], 'wls')

    def test_shrinkage_handles_more_series_than_observations(self):
        paths = [()] + [(f'r{i}',) for i in range(5)] + [(f'r{i}', f'p{j}') for i in range(5) for j in range(4)]   # 26 series
        S, parent, *_ = demand.hierarchy_structure(paths)
        T = 8; self.assertLess(T, S.shape[0])
        E = np.random.default_rng(1).normal(size=(T, S.shape[0])) * np.linspace(1, 5, S.shape[0])
        W, lam = demand.shrink_covariance(E)
        self.assertTrue(0 < lam <= 1); self.assertGreater(np.linalg.eigvalsh(W).min(), 0)   # sample covariance alone has rank <= T
        G, info = demand.reconcile_gain(S, 'mint_shrink', E[:T] if T >= demand.MIN_RESID else None)
        self.assertEqual(info['method'], 'mint_shrink'); self.assertEqual(info['residual_observations'], T)
        base = np.random.default_rng(2).normal(50, 9, size=(3, S.shape[0]))
        out = demand.project(S, G, base).T
        self.assertLess(demand.coherence_gap(out, parent), 1e-9)

    def test_non_negative_and_coherent_when_base_is_incoherent_and_negative(self):
        S, parent, *_ = demand.hierarchy_structure(PATHS)
        base = np.array([[2.0, 30, -4, -1, 25, 1, -6]])        # forces negative reconciled bottoms
        for m in ('ols', 'wls', 'mint_shrink'):
            G, _ = demand.reconcile_gain(S, m, np.random.default_rng(3).normal(size=(20, 7)))
            out = demand.project(S, G, base).T
            self.assertGreaterEqual(out.min(), 0); self.assertLess(demand.coherence_gap(out, parent), 1e-9)


class Forecasting(unittest.TestCase):
    H = 6

    def run_one(self, seed, method='mint_shrink'):
        Y = synthetic(seed); T = Y.shape[1]; train, test = Y[:, :T - self.H], Y[:, T - self.H:]
        periods = months('2022-01', train.shape[1])
        series, rec = demand.forecast_hierarchy(periods, [list(r) for r in train], PATHS, 'month', self.H, method, seed=seed)
        return series, rec, train, test

    def test_reconciled_forecasts_are_exactly_coherent_and_not_worse_held_out(self):
        base_m, rec_m = [], []
        for seed in range(4):
            series, rec, train, test = self.run_one(seed)
            self.assertTrue(rec['applied']); self.assertEqual(rec['reconciliation']['method'], 'mint_shrink')
            self.assertTrue(rec['coherence']['coherent']); self.assertLess(rec['coherence']['max_abs_gap'], 1e-6)
            self.assertGreaterEqual(rec['non_negativity']['min_reconciled'], 0)
            RP = np.array([[r['point'] for r in s['forecast']] for s in series]); BP = np.array([[r['point'] for r in s['base_forecast']] for s in series])
            self.assertLess(demand.coherence_gap(RP, [None, 0, 0, 1, 1, 2, 2]), 0.03)        # rounded to 2 dp: at most 0.01 per child
            for i, s in enumerate(series):
                self.assertTrue(s['reconciled']); self.assertLessEqual(s['forecast'][0]['lo80'], s['forecast'][0]['point'] + 1e-9)
                self.assertGreaterEqual(s['forecast'][0]['hi95'], s['forecast'][0]['hi80'] - 1e-9)
            base_m.append(np.mean([mase(test[i], BP[i], train[i]) for i in range(7)]))
            rec_m.append(np.mean([mase(test[i], RP[i], train[i]) for i in range(7)]))
        self.assertLessEqual(np.mean(rec_m), np.mean(base_m) * 1.01, (base_m, rec_m))

    def test_rolling_origin_report_base_vs_reconciled(self):
        series, rec, *_ = self.run_one(7)
        bt = rec['backtest']
        self.assertIsNotNone(bt); self.assertGreater(bt['origins'], 5)
        self.assertEqual(set(bt['by_level']), {0, 1, 2}); self.assertEqual(len(bt['by_node']), 7)
        self.assertLessEqual(bt['reconciled_mase'], bt['base_mase'] * 1.02, bt)

    def test_none_and_wls_and_ols(self):
        series, rec, *_ = self.run_one(0, 'none')
        self.assertFalse(rec['applied']); self.assertGreater(rec['coherence']['max_abs_gap'], 0)   # independent forecasts are incoherent
        self.assertNotIn('reconciled', series[0])
        for m in ('ols', 'wls'):
            series, rec, *_ = self.run_one(0, m)
            self.assertEqual(rec['reconciliation']['method'], m); self.assertTrue(rec['coherence']['coherent'])

    def test_too_short_node_gets_a_fallback_and_hierarchy_stays_coherent(self):
        Y = synthetic(5)[:, :30]; Y[6, :-3] = 0.0          # one practice only just started: engine cannot model it
        Y[0] = Y[3:].sum(0); Y[2] = Y[5] + Y[6]
        series, rec = demand.forecast_hierarchy(months('2022-01', 30), [list(r) for r in Y], PATHS, 'month', 3, 'mint_shrink')
        self.assertTrue(rec['applied']); self.assertTrue(rec['coherence']['coherent'])
        self.assertTrue(all(s['status'] == 'forecast' for s in series))


class Execute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = build(ed_records(), {f'p{i}': {'age_reported': 60, 'sex_reported': 'F'} for i in range(6)})
        semantic_layer.build(cls.path)

    def test_validate(self):
        p = demand.validate({'measure': 'ed_visits', 'hierarchy': ['facility', 'practice']})
        self.assertEqual((p['reconcile'], p['hierarchy']), ('mint_shrink', ['facility', 'practice']))
        self.assertEqual(demand.validate({'measure': 'ed_visits'})['reconcile'], 'none')
        self.assertEqual(demand.validate({'measure': 'ed_visits', 'group_by': 'facility', 'reconcile': 'ols'})['hierarchy'], ['facility'])
        for bad in ({'reconcile': 'mint'}, {'hierarchy': ['facility', 'facility']}, {'hierarchy': ['none']}, {'reconcile': 'ols'}, {'hierarchy': 'facility'}):
            with self.assertRaises(ValueError, msg=bad): demand.validate({'measure': 'ed_visits', **bad})

    def test_end_to_end_hierarchy(self):
        r = demand.execute(self.path, None, {'measure': 'ed_visits', 'horizon': 3, 'hierarchy': ['facility']})
        rc = r['reconciliation']
        self.assertTrue(rc['applied']); self.assertTrue(rc['coherence']['coherent']); self.assertEqual(rc['method_requested'], 'mint_shrink')
        self.assertEqual([s['group'] for s in r['series']][0], 'all'); self.assertEqual({s['parent'] for s in r['series'][1:]}, {'all'})
        by = {(t['group'], t['period']): t['point'] for t in r['table']}
        for per in {t['period'] for t in r['table']}:
            self.assertAlmostEqual(sum(v for (g, q), v in by.items() if q == per and g != 'all'), by[('all', per)], delta=0.02)
        self.assertTrue(all('reconciliation' in s for s in r['series'])); self.assertIn('Wickramasuriya', r['method'])
        self.assertIn('reconciled', demand.summary(r)); self.assertFalse(any(k.startswith('_') for s in r['series'] for k in s))

    def test_two_level_hierarchy_and_none(self):
        r = demand.execute(self.path, None, {'measure': 'ed_visits', 'horizon': 2, 'hierarchy': ['facility', 'practice'], 'reconcile': 'wls'})
        self.assertTrue(r['reconciliation']['coherence']['coherent']); self.assertEqual(r['reconciliation']['levels'], 3)
        r = demand.execute(self.path, None, {'measure': 'ed_visits', 'horizon': 2, 'hierarchy': ['facility'], 'reconcile': 'none'})
        self.assertFalse(r['reconciliation']['applied'])


if __name__ == '__main__':
    unittest.main()
