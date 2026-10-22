import unittest

import numpy as np

from clinical_core.agere.clinical import profile as pf


class MisclassificationTest(unittest.TestCase):
    def test_probabilities_from_draws(self):
        rng = np.random.default_rng(0)
        sample = np.column_stack([rng.normal(1.5, 0.05, 4000), rng.normal(1.0, 0.02, 4000), rng.normal(1.05, 0.2, 4000)])
        m = pf.misclassification(sample, ['truly_high', 'indistinguishable', 'indistinguishable'], [1.6, 1.0, 1.4])
        self.assertLess(m['p_misclassified'][0], 0.01)            # clearly high, reported high
        self.assertLess(m['p_misclassified'][1], 0.01)            # tight around network, reported average
        self.assertGreater(m['p_misclassified'][2], 0.3)          # wide posterior: the average label is uncertain
        self.assertEqual(m['raw_class'][2], 'high')               # naive raw ranking calls it high
        np.testing.assert_allclose(m['p_high'] + m['p_average'] + m['p_low'], 1.0)
        self.assertTrue(all(m['p_misclassified_bayes'] <= m['p_misclassified'] + 1e-12))  # most-probable class minimises error

    def test_analyse_rows_and_summary(self):
        rng = np.random.default_rng(1)
        units, y, w = [], [], []
        for j, rate in enumerate([0.2, 0.2, 0.2, 0.2, 0.8]):
            for _ in range(200):
                units.append(f'U{j}'); w.append(1.0); y.append(rng.poisson(rate))
        e = np.full(len(y), np.mean(y))
        res = pf.analyse(units, y, w, e, 'count')
        mis = res['misclassification']
        self.assertEqual(mis['reported'][4], 'high')
        self.assertLess(mis['p_misclassified'][4], 0.05)
        row = pf._mis_row(mis, 4, 'ed_visits')
        self.assertEqual(row['classification'], 'worse')          # more ED use is worse
        self.assertTrue(row['credible_outlier'])
        self.assertEqual(pf._mis_row(mis, 4, 'bp_control')['classification'], 'better')
        units_rows = [{**pf._mis_row(mis, j, 'ed_visits'), 'p_above_network': float(res['p_above'][j])} for j in range(5)]
        s = pf._mis_summary(units_rows)
        self.assertEqual(s['units'], 5)
        self.assertAlmostEqual(s['expected_misclassified'], round(sum(r['p_misclassified'] for r in units_rows), 2))
        self.assertEqual(units_rows[0]['p_below_network'], round(1 - units_rows[0]['p_above_network'], 4))


if __name__ == '__main__':
    unittest.main()
