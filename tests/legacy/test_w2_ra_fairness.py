import sqlite3
import unittest

import numpy as np

from clinical_core.agere.clinical import ra_fairness as F, risk_adjustment as ra


class FairnessTest(unittest.TestCase):
    def test_predictive_ratio(self):
        pr, lo, hi = F.predictive_ratio([1, 1, 1, 1], [2, 2, 2, 2])
        self.assertAlmostEqual(pr, 0.5); self.assertAlmostEqual(lo, 0.5); self.assertAlmostEqual(hi, 0.5)
        self.assertEqual(F.predictive_ratio([1], [0]), (None, None, None))

    def test_group_search_finds_undercompensated(self):
        rng = np.random.default_rng(3)
        rows, pred, actual = [], [], []
        for i in range(400):
            sick = i % 4 == 0
            rows.append({'raf': 1.0, 'groups': [('condition', 'chf')] if sick else [('condition', 'none')]})
            pred.append(1000.0); actual.append(rng.normal(2000 if sick else 667, 50))
        table = F.group_table(rows, np.array(pred), np.array(actual))
        by = {r['group']: r for r in table}
        self.assertEqual(by['chf']['status'], 'undercompensated')
        self.assertEqual(by['none']['status'], 'overcompensated')
        self.assertEqual(table[0]['group'], 'chf')

    def test_fair_regression_zeroes_group_residual(self):
        rng = np.random.default_rng(4)
        n = 500
        raf = rng.uniform(0.3, 2, n); g = rng.random(n) < 0.2
        y = 1000 * raf + 800 * g + rng.normal(0, 100, n)          # group cost the design does not see
        X = np.column_stack([np.ones(n), raf])
        fit = F.fair_regression(X, y, [np.where(g)[0]])
        self.assertGreater(abs(fit['group_mean_residual']['ols'][0]), 300)
        self.assertLess(abs(fit['group_mean_residual']['fair'][0]), 5)
        self.assertLessEqual(fit['r2_fair'], fit['r2_ols'])       # fairness costs some fit

    def test_diagnose_on_tiny_db(self):
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE patient(patient_id TEXT, race TEXT, ethnicity TEXT)')
        db.execute('CREATE TABLE record(patient_id TEXT, stream TEXT, payload TEXT, available_date TEXT)')
        db.execute('CREATE TABLE concept_patient(patient_id TEXT, concept TEXT)')
        pats = []
        for i in range(80):
            pid = f'P{i}'; black = i % 2 == 0
            db.execute('INSERT INTO patient VALUES (?,?,?)', (pid, 'Black' if black else 'White', 'Not Hispanic'))
            cents = 300000 if black else 100000
            db.execute("INSERT INTO record VALUES (?,?,?,?)", (pid, 'medical_claims',
                       '{"status": "paid", "service_date": "2029-03-01", "allowed_cents": %d, "payer_paid_cents": %d}' % (cents, cents), '2029-04-01'))
            pats.append({'patient_id': pid, 'payment_raf': 1.0, 'demographic_payment_raf': 0.4, 'payment_hcc_count': {'V28': 1},
                         'age': 70, 'sex': 'F', 'coverage_type': 'medicare'})
        out = F.diagnose(db, pats, '2029-01-01', '2029-12-31')
        self.assertEqual(out['status'], 'answered')
        under = [(r['dimension'], r['group']) for r in out['undercompensated']]
        self.assertIn(('race', 'Black'), under)
        self.assertIsNotNone(out['fair_regression'])
        self.assertIn('undercompensated', F.summary(out))

    def test_engine_accepts_fairness_focus(self):
        self.assertEqual(ra.validate({'focus': 'fairness'})['focus'], 'fairness')


if __name__ == '__main__':
    unittest.main()
