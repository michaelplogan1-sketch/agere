"""Discovery finds a planted concentration, ignores noise, and flags documentation gaps."""
import random, unittest

from clinical_core.agere.clinical import discover, semantic_layer
from test_aggregate_explain import build, pcp

W = {'basis': 'all_observed', 'start': '', 'end': ''}


class Discovery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rng = random.Random(7)
        patients, recs = {}, []
        for i in range(400):
            pid = f'p{i:03d}'
            practice = f'PR{i % 4}'
            patients[pid] = {'age_reported': rng.choice([30, 50, 70]), 'sex_reported': rng.choice('FM')}
            recs.append(pcp(pid, f'dr{i % 8}', practice))
            recs.append((pid, 'coverage', {'coverage_type': rng.choice(['commercial', 'medicare']), 'status': 'active',
                                           'event_date': '2027-01-01'}))
            # Planted: practice PR2 has 45% uncontrolled A1c vs ~10% elsewhere.
            p = 0.45 if practice == 'PR2' else 0.10
            # Documentation gap: practice PR3 sends almost no labs, so its absence-based outcome is unreliable.
            if practice == 'PR3' and rng.random() < 0.8: continue
            recs.append((pid, 'labs', {'test': 'HbA1c', 'value': 10.1 if rng.random() < p else 6.4, 'unit': '%', 'event_date': '2028-03-01'}))
        cls.path = build(recs, patients)
        semantic_layer.build(cls.path)
        cls.ids = sorted(patients)

    def test_planted_concentration_found_and_noise_ignored(self):
        spec = {'all': [{'kind': 'measurement', 'concept': 'hba1c', 'op': 'gt', 'value': 9, 'aggregate': 'any', 'window': W}],
                'any': [], 'exclude': []}
        r = discover.execute(self.path, self.ids, {'outcome': spec, 'segments': ['practice', 'pcp', 'sex', 'coverage_type'], 'pairs': False})
        practices = [c['segment'].get('practice') for c in r['cards'] if c['kind'] == 'concentration']
        self.assertIn('PR2', practices)
        self.assertNotIn('PR0', practices)
        self.assertFalse([c for c in r['cards'] if 'sex' in c['segment'] or 'coverage_type' in c['segment']])

    def test_absence_outcome_flags_documentation_gap(self):
        no_a1c = {'all': [], 'any': [], 'exclude': [{'kind': 'concept', 'concept': 'hba1c', 'window': W}]}
        r = discover.execute(self.path, self.ids, {'outcome': no_a1c, 'segments': ['practice'], 'pairs': False})
        pr3 = [c for c in r['cards'] if c['segment'].get('practice') == 'PR3']
        self.assertTrue(pr3)
        self.assertEqual(pr3[0]['kind'], 'documentation-limited')


if __name__ == '__main__':
    unittest.main()
