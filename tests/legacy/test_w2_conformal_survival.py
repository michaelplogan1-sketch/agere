import math
import unittest

import numpy as np

from clinical_core.agere.clinical import survival


def simulate(n, seed, rate=1 / 60.0, admin_lo=30, admin_hi=150, censor_rate=1 / 400.0):
    """Exponential event times, administrative censoring uniform in [admin_lo, admin_hi] and random dropout."""
    rng = np.random.default_rng(seed)
    rows, truth = [], []
    for i in range(n):
        g = 'a' if i % 2 else 'b'
        t = rng.exponential(1 / rate if g == 'a' else 2 / rate)
        admin = int(rng.integers(admin_lo, admin_hi))
        drop = rng.exponential(1 / censor_rate)
        c = min(admin, drop)
        obs, ev = (t, 1) if t <= c else (c, 0)
        rows.append((f'P{i}', '2030-01-01', round(obs, 3), ev, None if ev else 'snapshot', g, admin))
        truth.append(t)
    return rows, truth


class ConformalSurvivalTest(unittest.TestCase):
    def test_lower_bound_covers_true_times(self):
        rows, truth = simulate(3000, 1)
        units = [r[:5] for r in rows]
        grp = {r[0]: r[5] for r in rows}; adm = {r[0]: r[6] for r in rows}
        b = survival.conformal_lower_bounds(units, lambda r: grp[r[0]], lambda r: adm[r[0]], alpha=0.1)
        self.assertEqual(b['status'], 'ok')
        # true coverage against the (never observed) event times: P(T >= L) >= 0.9 up to sampling error
        cov = np.mean([t >= b['groups'][grp[r[0]]]['lower_bound_days'] for r, t in zip(rows, truth)])
        self.assertGreaterEqual(cov, 0.87)
        self.assertGreater(b['groups']['b']['lower_bound_days'], b['groups']['a']['lower_bound_days'])  # slower group, later bound
        self.assertGreaterEqual(b['coverage']['empirical_ipcw'], 0.85)
        self.assertLessEqual(max(g['lower_bound_days'] for g in b['groups'].values()), b['c0'])

    def test_too_few_calibration_units_is_stated_not_raised(self):
        rows, _ = simulate(20, 2)
        b = survival.conformal_lower_bounds([r[:5] for r in rows], lambda r: 'all', lambda r: 100, alpha=0.1)
        self.assertEqual(b['status'], 'insufficient')
        self.assertIn('calibration units', b['reason'])

    def test_validate_defaults_on_and_checks_alpha(self):
        p = survival.validate({'index': {'type': 'discharge'}, 'event': {'type': 'readmission'}})
        self.assertTrue(p['lower_bound']); self.assertEqual(p['alpha'], 0.1)
        self.assertEqual(survival.validate(p), p)
        with self.assertRaises(ValueError):
            survival.validate({'index': {'type': 'discharge'}, 'event': {'type': 'readmission'}, 'alpha': 0.9})

    def test_summary_states_the_bound(self):
        report = {'parameters': {'time_points': [30], 'index': {'type': 'discharge'}}, 'table': [], 'groups': [], 'excluded': {},
                  'event_label': 'readmission', 'time_zero': 'discharge', 'groups_total': 0, 'logrank': None, 'cox': None}
        self.assertIn('No analysable', survival.summary(report))
        report['table'] = [{'group': 'all', 'day': 30, 'cumulative_incidence': 0.1, 'ci95_low': 0.05, 'ci95_high': 0.15}]
        report['groups'] = [{'group': 'all', 'median_days': None, 'units': 100, 'events': 10}]
        report['conformal_lower_bounds'] = {'status': 'ok', 'alpha': 0.1, 'c0': 90, 'groups': {'all': {'lower_bound_days': 12.0}},
                                            'coverage': {'empirical_ipcw': 0.91, 'test_units': 50}}
        text = survival.summary(report)
        self.assertIn('Conformal 90% lower bound', text); self.assertIn('all 12 days', text); self.assertIn('91.0%', text)


if __name__ == '__main__':
    unittest.main()
