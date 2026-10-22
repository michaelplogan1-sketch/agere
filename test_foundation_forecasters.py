import unittest
import numpy as np
from clinical_core.agere.clinical import demand, foundation_forecasters as ff


class ChallengerTests(unittest.TestCase):
    def test_models_forecast_quantiles(self):
        y = np.array([10, 12, 14, 13, 15, 17, 16, 18, 20, 19, 21, 23] * 3, float)
        for name in ff.enabled():
            q = ff.quantiles(name, y, 6)
            self.assertEqual(q.shape, (6, 9))
            self.assertTrue(np.all(np.isfinite(q)))
            m = ff.Challenger(y, name)
            self.assertEqual(m.point(6).shape, (6,))
            sims = m.simulate(6, 50, np.random.default_rng(0))
            self.assertEqual(sims.shape, (50, 6))

    def test_candidates_include_challengers_and_off_switch(self):
        self.assertTrue({'chronos2', 'timesfm25'} <= {n for n, _ in demand.candidates(40, 'month', True)})
        import os
        os.environ['AGERE_DEMAND_CHALLENGERS'] = 'off'
        try:
            self.assertFalse({'chronos2', 'timesfm25'} & {n for n, _ in demand.candidates(40, 'month', True)})
        finally: del os.environ['AGERE_DEMAND_CHALLENGERS']

    def test_forecast_series_challenger_only_if_it_wins(self):
        import os
        os.environ['AGERE_DEMAND_TIMESTRAL'] = 'off'
        try:
            rng = np.random.default_rng(3)
            periods = [f'2026-{m:02d}' for m in range(1, 13)] + [f'2027-{m:02d}' for m in range(1, 13)] + [f'2028-{m:02d}' for m in range(1, 13)]
            y = list(50 + 10 * np.sin(np.arange(36) * 2 * np.pi / 12) + rng.normal(0, 2, 36))
            out = demand.forecast_series(periods, [float(v) for v in y], 'month', 6)
            self.assertEqual(out['status'], 'forecast')
            mase = out['backtest']['mase']
            self.assertIn('chronos2', mase)
            best_found = min(mase, key=mase.get)
            if out['model_selection'] == 'challenger_backtest':
                self.assertIn(out['model'], ff.CHALLENGERS)
                self.assertLess(mase[out['model']], mase[out['backtest_pick']])
            else:
                self.assertEqual(out['model'], out['backtest_pick'])
        finally: del os.environ['AGERE_DEMAND_TIMESTRAL']


if __name__ == '__main__':
    unittest.main()
