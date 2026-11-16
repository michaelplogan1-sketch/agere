"""causal_hte: residual projection agrees with ATE in this independent effect-modifier fixture only;
R-learner and honest causal forest rank the benefiting subgroup first;
uplift.analyze evaluates both through its RATE / Qini machinery."""
import unittest

import numpy as np

from clinical_core.agere.clinical import causal_hte as C, uplift
from test_uplift import simulate, NAMES


def linear_data(n=4000, seed=3):
    """Continuous outcome, confounded treatment, effect tau(x) = 1 + 2 x0 (ATE = 1)."""
    r = np.random.default_rng(seed)
    X = r.normal(size=(n, 4))
    e = 1 / (1 + np.exp(-0.8 * X[:, 1]))
    t = (r.random(n) < e).astype(int)
    tau = 1 + 2 * X[:, 0]
    y = 2 * X[:, 1] + X[:, 2] + tau * t + r.normal(0, 1, n)
    return X, t, y, tau


class DoubleML(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.X, cls.t, cls.y, cls.tau = linear_data()
        cls.fold = np.arange(len(cls.y)) % 5
        cls.res = C.fit(cls.X, cls.t, cls.y, cls.fold, n_trees=80)

    def test_projection_agrees_with_ate_when_effect_modifier_is_independent_of_propensity(self):
        a = self.res['plr_projection']
        self.assertLess(a['ci95'][0], 1.0); self.assertGreater(a['ci95'][1], 1.0)
        self.assertLess(a['se'], 0.1)

    def test_cate_tracks_truth(self):
        for l in ('dml', 'causal_forest'):
            corr = np.corrcoef(self.res['tau'][l], self.tau)[0, 1]
            self.assertGreater(corr, 0.7, (l, corr))

    def test_forest_importance_finds_effect_modifier(self):
        self.assertEqual(int(np.argmax(self.res['importance'])), 0)

    def test_oob_prediction(self):
        nu = self.res['nuisance']
        cf = C.CausalForest(n_trees=60, seed=1).fit(self.X, nu['residual_outcome'], nu['residual_treatment'])
        self.assertGreater(np.corrcoef(cf.oob_predict(self.X), self.tau)[0, 1], 0.7)


class UpliftIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.X, cls.t, cls.y, cls.truth, cls.sub = simulate(n=4000)
        cls.res = uplift.analyze(cls.X, cls.t, cls.y, 'logistic', names=NAMES, bootstrap=100, learners=uplift.LEARNERS, primary='causal_forest')

    def test_new_learners_rank_benefiting_subgroup_first(self):
        m = self.res['members']; ok = np.flatnonzero(m['in_overlap'])
        for l in ('dml', 'causal_forest'):
            e = self.res['learners'][l]
            self.assertGreater(e['rate_autoc']['ci95'][0], 0, (l, e['rate_autoc']))
        order = ok[np.argsort(-m['benefit'][ok])]
        self.assertGreater(self.sub[np.array_split(order, 10)[0]].mean(), 0.8)

    def test_plr_projection_benefit_sign_and_importance(self):
        self.assertGreater(self.res['plr_projection_benefit']['estimate'], 0)  # treatment reduces events: positive benefit
        imp = self.res['causal_forest_importance']
        self.assertAlmostEqual(sum(imp.values()), 1.0, places=6)
        self.assertTrue({'chronic_conditions', 'baseline_risk'} & set(sorted(imp, key=imp.get)[-2:]))  # baseline_risk is a chronic-count proxy

    def test_validate_learner_sets(self):
        self.assertEqual(uplift.validate({'learners': 'dml'})['primary_learner'], 'dml')
        self.assertEqual(uplift.validate({})['learners'], 'all')
        with self.assertRaises(ValueError): uplift.validate({'learners': 'econml'})


if __name__ == '__main__':
    unittest.main()
