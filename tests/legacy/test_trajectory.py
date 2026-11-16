"""Trajectory detection: a planted eGFR decline is flagged; noise and single spikes are not."""
import random, unittest

from clinical_core.agere.clinical import semantic_layer, trajectory
from test_aggregate_explain import build


class Trajectory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rng = random.Random(3)
        recs, patients = [], {}
        for pid, series in {
            'decline': [62, 61, 63, 60, 48, 46, 45, 44],          # material drop mid-series
            'stable': [60 + rng.uniform(-3, 3) for _ in range(8)],  # noise only
            'spike': [60, 61, 59, 60, 62, 60, 61, 40],              # one low value at the end
        }.items():
            patients[pid] = {'age_reported': 60, 'sex_reported': 'F'}
            for i, v in enumerate(series):
                recs.append((pid, 'labs', {'test': 'eGFR', 'value': v, 'unit': 'mL/min/1.73m2', 'event_date': f'2028-{i + 1:02d}-15'}))
        cls.path = build(recs, patients)
        semantic_layer.build(cls.path)

    def test_only_sustained_material_decline_is_flagged(self):
        r = trajectory.execute(self.path, None, {'concept': 'egfr', 'direction': 'down'})
        self.assertEqual([p['patient_id'] for p in r['patients']], ['decline'])
        self.assertEqual(r['patients_with_series'], 3)
        self.assertLess(r['patients'][0]['slope_per_year'], 0)

    def test_direction_filter(self):
        self.assertEqual(trajectory.execute(self.path, None, {'concept': 'egfr', 'direction': 'up'})['flagged'], 0)


if __name__ == '__main__':
    unittest.main()


class Forecast(unittest.TestCase):
    def test_steady_decline_is_projected_across_threshold(self):
        from clinical_core.agere.clinical.trajectory import forecast
        pts = [(738000 + 60 * i, 50.0 - 2.0 * i) for i in range(8)]  # -12/yr, last value 36
        f = forecast(pts, 0.08, 3.0, 365, 30.0, below=True)
        self.assertLess(f['slope_per_year'], -6)
        self.assertGreater(f['p_cross'], 0.5)

    def test_stable_series_is_not_projected(self):
        from clinical_core.agere.clinical.trajectory import forecast
        pts = [(738000 + 60 * i, 55.0 + (0.5 if i % 2 else -0.5)) for i in range(8)]
        self.assertLess(forecast(pts, 0.08, 3.0, 365, 30.0, below=True)['p_cross'], 0.05)
