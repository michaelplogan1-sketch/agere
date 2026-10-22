"""change_attribution: the joint patient-state / source-observation tensor model separates a stream-wide feed outage, a local
feed problem and a group-wide real change; routes claim real-vs-outage wording only; monitor gets the annotation."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import numpy as np

from clinical_core.agere.clinical import change_attribution as C

ROOT = Path(__file__).resolve().parent
STACK = Path('/Users/mikel/clinical-insights-local/artifacts/agere-healthcare-v28-sample-0.30/stack')


def tensor(seed=0, G=6, S=5, T=24):
    r = np.random.default_rng(seed)
    c = r.normal(4, 0.5, (G, S)); a = r.normal(0, 0.03, (G, T)); b = r.normal(0, 0.03, (S, T))
    mu = np.exp(c[:, :, None] + a[:, None, :] + b[None, :, :])
    mu[:, 1, -2] *= 0.3   # stream 1 outage in every group
    mu[2, :, -1] *= 0.35  # group 2: every stream down together (real change)
    mu[4, 3, -3] *= 0.2   # one group's interface for one stream
    return r.poisson(mu), [f'g{i}' for i in range(G)], [f's{j}' for j in range(S)], [f'2029-{(t % 12) + 1:02d}' if t < 12 else f'2030-{t - 11:02d}' for t in range(T)]


class Model(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Y, cls.g, cls.s, cls.m = tensor()
        cls.rep = C.attribute_tensor(Y, cls.g, cls.s, cls.m)
        cls.by = {(a['group'], a['stream'], a['month']): a for a in cls.rep['anomalies']}

    def test_stream_wide_outage_is_source(self):
        hits = [a for a in self.rep['anomalies'] if a['stream'] == 's1' and a['month'] == self.m[-2]]
        self.assertGreaterEqual(len(hits), 5)
        self.assertTrue(all(a['attribution'] == 'source_outage' for a in hits))

    def test_group_wide_change_is_patient_change(self):
        hits = [a for a in self.rep['anomalies'] if a['group'] == 'g2' and a['month'] == self.m[-1]]
        self.assertGreaterEqual(len(hits), 3)
        self.assertTrue(all(a['attribution'] == 'patient_change' for a in hits))

    def test_single_cell_is_local_feed(self):
        a = self.by[('g4', 's3', self.m[-3])]
        self.assertEqual(a['attribution'], 'local_feed')
        self.assertGreater(a['probabilities']['local_feed'], 0.9)

    def test_no_change_no_anomalies(self):
        r = np.random.default_rng(5)
        mu = np.exp(r.normal(4, 0.5, (5, 4)))[:, :, None] * np.ones(20)
        rep = C.attribute_tensor(r.poisson(mu), list('abcde'), list('wxyz'), [str(i) for i in range(20)])
        self.assertLessEqual(len(rep['anomalies']), 1)
        self.assertLess(rep['dispersion_phi'], 1.5)

    def test_fit_recovers_states(self):
        Y, *_ = tensor(seed=2)
        c, a, b, mu = C.fit(Y[:, :, :-3])
        self.assertLess(C.dispersion(Y[:, :, :-3].astype(float), mu, np.ones_like(mu, bool)), 1.5)  # Pearson chi2/df ~ 1: the three-way state model fits


class Warehouse(unittest.TestCase):
    def fixture(self, outage=True):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        db = sqlite3.connect(Path(tmp.name) / 'semantic.sqlite3')
        db.executescript('CREATE TABLE record(record_id TEXT, patient_id TEXT, stream TEXT, event_date TEXT, organization_id TEXT, payload TEXT);'
                         'CREATE TABLE member_month(patient_id TEXT, month TEXT, line_of_business TEXT);')
        r = np.random.default_rng(1); rows = []; n = 0
        months = C.months_for('2030-05-27', 3, 18)
        for pr in range(4):
            for k in range(40):
                pid = f'P{pr}-{k}'
                rows.append((f'h{n}', pid, 'encounters', '2028-01-05', f'WH-PRACTICE-{pr}', '{}')); n += 1
                for mi, m in enumerate(months):
                    for st, lam in (('labs', 3), ('encounters', 2), ('notes', 2)):
                        if outage and st == 'labs' and mi == len(months) - 1: lam = 0.3
                        for _ in range(r.poisson(lam)):
                            rows.append((f'r{n}', pid, st, f'{m}-10', f'WH-PRACTICE-{pr}', '{}')); n += 1
        db.executemany('INSERT INTO record VALUES(?,?,?,?,?,?)', rows); db.commit(); db.close()
        return tmp.name

    def test_execute_attributes_lab_outage(self):
        r = C.execute(self.fixture(), None, {'focus_streams': ['labs']}, '2030-05-27')
        self.assertTrue(r['anomalies'])
        self.assertTrue(all(a['attribution'] == 'source_outage' and a['stream'] == 'labs' for a in r['anomalies']))
        ans = C.answer(r, {'coverage': 'supported_observation'}, 'q')
        self.assertIn('feed outage', ans['value'])

    def test_monitor_annotation_never_raises(self):
        out = C.annotate_monitor({'feeds': [{'stream': 'labs'}]}, self.fixture(), '2030-05-27')
        self.assertIn('anomalies', out['change_attribution'])
        bad = C.annotate_monitor({'feeds': [{'stream': 'labs'}]}, '/nonexistent', '2030-05-27')
        self.assertEqual(bad['change_attribution']['status'], 'not_computed')
        self.assertNotIn('change_attribution', C.annotate_monitor({'feeds': []}, '/nonexistent', '2030-05-27'))

    def test_validate(self):
        with self.assertRaises(ValueError): C.validate({'bogus': 1})
        with self.assertRaises(ValueError): C.validate({'group_by': 'zip'})
        self.assertEqual(C.validate({})['group_by'], 'home_practice')


class Routes(unittest.TestCase):
    def test_acceptance_and_no_stealing(self):
        from clinical_core.agere.clinical.local_compiler import compile as lc
        prof = json.loads((STACK / 'source-profile.json').read_text()) if (STACK / 'source-profile.json').exists() else {}
        for f in ('change_attribution', 'monitor', 'market'):
            for c in json.loads((ROOT / 'bench/acceptance' / f'{f}.json').read_text()):
                ours = C.route(c['question'], prof, '2030-05-27') is not None
                self.assertEqual(ours, c['expect_operation'] == 'change_attribution', c['question'])
        g, _ = C.route('Is the drop in lab volume real or a feed outage?', prof, '2030-05-27')
        self.assertEqual(g['nodes'][0]['parameters']['focus_streams'], ['labs'])
        for q in ('What is a feed outage?', 'Is the lab feed behaving normally?', 'How many labs last month?'):
            self.assertIsNone(C.route(q, prof, '2030-05-27'), q)


if __name__ == '__main__':
    unittest.main()
