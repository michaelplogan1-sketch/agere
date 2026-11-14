"""Literal authored counts test support and scope, not predictive or clinical accuracy."""
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np
from clinical_core.agere.clinical import demand as D, capacity_simulation as C, hospital_ops as H, visit_groups as V
from test_hospital_ops_engines import visit
from test_demand import months


class LiteralModel:
    def point(self, h): return np.full(h, 10.)
    def simulate(self, h, n, rng): return np.tile(np.linspace(7, 13, n)[:, None], (1, h))


class CurrentPeriod(unittest.TestCase):
    def series(self, observed=120, period='2030-10'):
        records = [{'actual': 10., 'point': 10., 'sd': 1., 'scaled_error': 1., 'model_cover': {.8: True, .95: True}}]
        with patch.object(D, 'backtest', return_value=({'naive': records}, [1], False, 'naive')), \
             patch.object(D, 'candidates', return_value=[('naive', lambda y: LiteralModel())]), \
             patch.object(D, 'default_model', return_value='disabled'), patch.object(D, 'sequential_coverage', return_value=(None, 0)):
            return D.forecast_series(months('2027-10', 36), [10.] * 36, 'month', 2,
                                     {'period': period, 'observed_so_far': observed}, {'2030-11': 1000})

    def test_received_units_bound_points_samples_intervals_and_capacity(self):
        s = self.series(); row = s['forecast'][0]
        self.assertEqual(row['period'], '2030-10'); self.assertEqual(row['point'], 120)
        for field in ['lo80', 'hi80', 'lo95', 'hi95']: self.assertGreaterEqual(row[field], 120)
        self.assertGreaterEqual(s['_sims'][:, 0].min(), 120)
        self.assertFalse(s['current_period_support']['conditional_coverage_qualified'])
        p = {'minutes_per_unit': 60, 'fte_hours_per_week': 40}
        self.assertEqual(D.capacity_rows(s['forecast'], 'month', p, 'visits')[0]['workload_hours'], 120)
        # Future bookings retain their distinct semantics and do not become received units.
        self.assertEqual(s['forecast'][1]['point'], 10); self.assertEqual(s['forecast'][1]['already_booked'], 1000)

    def test_compatible_count_preserves_distribution_and_other_period_is_not_attached(self):
        s = self.series(2)
        self.assertEqual(s['forecast'][0]['point'], 10); self.assertEqual(s['_sims'][:, 0].min(), 7)
        mismatch = self.series(120, '2031-10')
        self.assertNotIn('current_period_support', mismatch)
        self.assertFalse(any('observed_so_far' in row for row in mismatch['forecast']))

    def test_hierarchy_preserves_support_and_exact_coherence_in_each_sample(self):
        paths = [(), ('A',), ('B',)]; counts = [120, 100, 20]
        def literal(periods, y, freq, horizon, partial, booked, seed):
            value = 10 if seed == 0 else 5
            return {'status': 'forecast', 'history': [], 'forecast': [], 'data_issues': [], '_pt': np.full(2, value),
                    '_sd': np.ones(2), '_sims': np.full((D.SIMS, 2), value), '_recs': [], '_offset': 0, '_yr': np.ones(36), '_lag': 1}
        with patch.object(D, 'forecast_series', side_effect=literal):
            series, receipt = D.forecast_hierarchy(months('2027-10', 36), [[10.] * 36] * 3, paths, 'month', 1, 'ols',
                [{'period': '2030-10', 'observed_so_far': n} for n in counts])
        for s, n in zip(series, counts):
            self.assertGreaterEqual(s['forecast'][0]['point'], n)
            self.assertGreaterEqual(s['_sims'][:, 0].min(), n)
            self.assertGreaterEqual(s['forecast'][0]['lo95'], n)
        np.testing.assert_allclose(series[0]['_sims'], series[1]['_sims'] + series[2]['_sims'])
        self.assertTrue(receipt['coherence']['coherent'])
        self.assertFalse(receipt['current_period_support']['conditional_coverage_qualified'])

    def test_unassigned_parent_count_projects_without_breaking_child_support(self):
        S, _, _, bottoms = D.hierarchy_structure([(), ('A',), ('B',)])
        out = D._support_hierarchy(np.array([[10., 5, 5], [30, 25, 5]]), S, bottoms, np.array([120, 20, 10]))
        np.testing.assert_allclose(out[:, 0], out[:, 1:].sum(axis=1))
        self.assertTrue(np.all(out >= [120, 20, 10]))


class Occupancy(unittest.TestCase):
    def rows(self):
        return [visit(i, start='2030-09-01', end=None, known=0, org='ORG') for i in range(100)] + [
            visit(101, start='2030-10-15', end='2030-10-16', org='ORG'),
            visit(102, start='2030-09-01', end='2030-09-30', org='ORG'),
            visit(103, start='2030-10-28', end=None, known=0, org='ORG'),
            visit(104, start='2030-09-01', end=None, known=0, org='OTHER')]

    def test_arrival_and_occupant_views_share_scope_but_not_time_selection(self):
        p = {'start': '2030-10-01', 'end': '2030-10-27', 'facility': 'ORG'}
        with patch.object(V, 'load', return_value=(self.rows(), 'authored')) as load, \
             patch.object(H, 'facilities', return_value=[]), patch.object(H, 'resolve_facility', return_value='ORG'):
            arrivals, _, window, _, _ = H.visits_in(None, '2030-10-27', p, ['P1'])
            occupants, _, _, _, _ = H.visits_in(None, '2030-10-27', p, ['P1'], overlap=True)
        self.assertEqual(len(arrivals), 1); self.assertEqual(len(occupants), 101)
        self.assertTrue(all(c.args[1:] == (['P1'], '2030-10-27') for c in load.call_args_list))
        twin = C.fit(arrivals, *window, occupancy_rows=occupants)
        self.assertEqual(twin['admissions'], 1); self.assertEqual(len(twin['los_hours']), 1)
        self.assertAlmostEqual(np.mean(twin['census']), 100 + 2 / 27)
        self.assertEqual(int(np.ceil(np.quantile(twin['census'], .95))), 101)

    def test_execute_uses_overlapping_census_and_staffed_beds_override_proxy(self):
        p = {'start': '2030-10-01', 'end': '2030-10-27', 'facility': 'ORG', 'replications': 2, 'horizon_days': 30, 'sensitivity': False}
        for beds, expected in [(None, 101), (130, 130)]:
            with self.subTest(beds=beds):
                db = sqlite3.connect(':memory:')
                with patch.object(H, 'connect', return_value=(db, '2030-10-27')), patch.object(V, 'load', return_value=(self.rows(), 'authored')), \
                     patch.object(H, 'facilities', return_value=[]), patch.object(H, 'resolve_facility', return_value='ORG'):
                    r = C.execute('/authored-only', ['P1'], {**p, 'beds': beds}, '2030-10-27')
                self.assertEqual(r['receipt']['admissions_fitted'], 1)
                self.assertEqual(r['receipt']['beds_baseline'], expected)
                self.assertAlmostEqual(r['receipt']['occupancy_basis']['mean_observed_census'], 100 + 2 / 27)


if __name__ == '__main__': unittest.main()
