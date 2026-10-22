"""uplift: cross-fitted T/X-learners recover a known subgroup effect, RATE/Qini separate signal from noise, trimming works."""
import unittest

import numpy as np

from clinical_core.agere.clinical import uplift

NAMES = uplift.FEATURES


def simulate(n=5000, hetero=True, seed=11, confound=0.5, effect=1.5):
    """Binary outcome. Treatment is confounded by prior ED use. With `hetero`, only members with >= 3 chronic conditions
    benefit (logit reduced by `effect`); otherwise treatment does nothing. Returns X, t, y, true benefit (risk reduction)."""
    r = np.random.default_rng(seed)
    age = r.normal(60, 12, n); female = r.integers(0, 2, n).astype(float); medicare = (age > 65).astype(float)
    prior_ed = r.poisson(0.6, n).astype(float); prior_adm = r.poisson(0.25, n).astype(float)
    chronic = r.poisson(2.2, n).astype(float)
    baseline = 0.3 * prior_ed + 0.5 * prior_adm + 0.2 * chronic + r.normal(0, 0.1, n)
    X = np.column_stack([age, female, medicare, prior_ed, prior_adm, chronic, baseline])
    e = 1 / (1 + np.exp(-(-0.4 + confound * (prior_ed - 0.6))))
    t = (r.random(n) < e).astype(int)
    sub = (chronic >= 3).astype(float)
    lin = -1.4 + 0.45 * prior_ed + 0.4 * prior_adm + 0.1 * (chronic - 2.2) + 0.01 * (age - 60)
    tau_logit = -effect * sub if hetero else np.zeros(n)
    p0 = 1 / (1 + np.exp(-lin)); p1 = 1 / (1 + np.exp(-(lin + tau_logit)))
    y = (r.random(n) < np.where(t == 1, p1, p0)).astype(float)
    return X, t, y, p0 - p1, sub


def run(X, t, y, **kw):
    kw.setdefault('bootstrap', 300)
    return uplift.analyze(X, t, y, 'logistic', names=NAMES, **kw)


class KnownSubgroup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.X, cls.t, cls.y, cls.truth, cls.sub = simulate()
        cls.res = run(cls.X, cls.t, cls.y)

    def _true_by_decile(self, learner):
        m = self.res['members']
        pred = m['tau_x' if learner == 'x' else 'tau_t']
        ok = m['in_overlap']
        idx = np.flatnonzero(ok)
        order = idx[np.argsort(pred[idx])]  # most negative tau (largest benefit) first
        dec = np.array_split(order, 10)
        return self.truth[dec[0]].mean(), self.truth[dec[-1]].mean(), self.sub[dec[0]].mean()

    def test_learners_recover_subgroup(self):
        for l in ('x', 't'):
            top, bottom, sub_share = self._true_by_decile(l)
            self.assertGreater(top, bottom + 0.08, (l, top, bottom))
            self.assertGreater(sub_share, 0.9, (l, sub_share))  # top-decile members are the benefiting subgroup

    def test_rate_and_qini_exclude_zero_and_calibrate(self):
        for l, e in self.res['learners'].items():
            self.assertTrue(e['rate_autoc']['excludes_zero'] and e['rate_autoc']['ci95'][0] > 0, (l, e['rate_autoc']))
            self.assertTrue(e['qini']['excludes_zero'] and e['qini']['ci95'][0] > 0, (l, e['qini']))
            cal = e['calibration']
            self.assertEqual(len(cal), 10)
            self.assertGreater(cal[0]['observed_benefit'], cal[-1]['observed_benefit'])
            self.assertGreater(e['top_minus_bottom_decile']['ci95'][0], 0)
            self.assertGreater(e['calibration_slope'], 0.3)

    def test_ate_close_to_truth(self):
        lo, hi = self.res['ate_benefit']['ci95']
        keep = self.res['members']['in_overlap']
        self.assertTrue(lo - 0.02 <= self.truth[keep].mean() <= hi + 0.02, (lo, hi, self.truth[keep].mean()))

    def test_surrogate_reasons_point_at_chronic_burden(self):
        coef = self.res['surrogate']['coefficients']
        self.assertEqual(max(coef, key=lambda k: abs(coef[k])), 'chronic_conditions')

    def test_cross_fitting_is_deterministic(self):
        again = run(self.X, self.t, self.y)
        self.assertEqual(again['learners']['x']['rate_autoc'], self.res['learners']['x']['rate_autoc'])


class NoHeterogeneity(unittest.TestCase):
    def test_rate_ci_covers_zero_when_treatment_does_nothing(self):
        covered = 0
        for seed in (22, 23, 24, 25, 26, 27):
            X, t, y, _, _ = simulate(hetero=False, seed=seed)
            res = run(X, t, y, seed=seed)
            for l, e in res['learners'].items():
                lo, hi = e['rate_autoc']['ci95']
                covered += lo <= 0 <= hi
        self.assertGreaterEqual(covered, 11, covered)  # 12 intervals at nominal 95%; allow one miss

    def test_primary_flagged_not_detected(self):
        X, t, y, _, _ = simulate(hetero=False, seed=31)
        e = run(X, t, y, seed=31)['learners']['x']
        self.assertLessEqual(e['rate_autoc']['ci95'][0], 0.0)


class Overlap(unittest.TestCase):
    def test_trimming_drops_extreme_propensity_members(self):
        X, t, y, _, _ = simulate(confound=2.5, seed=41)
        loose = run(X, t, y, trim=0.0, bootstrap=50)
        tight = run(X, t, y, trim=0.15, bootstrap=50)
        self.assertEqual(loose['kept'], loose['n'])
        self.assertLess(tight['kept'], tight['n'])
        self.assertEqual(tight['kept'] + tight['trimmed_treated'] + tight['trimmed_control'], tight['n'])
        m = tight['members']
        self.assertTrue(np.all((m['propensity'][m['in_overlap']] >= 0.15) & (m['propensity'][m['in_overlap']] <= 0.85)))
        self.assertTrue(np.all(~m['in_overlap'][(m['propensity'] < 0.15) | (m['propensity'] > 0.85)]))
        self.assertGreater(tight['propensity']['auc'], 0.7)

    def test_too_few_after_trimming(self):
        X, t, y, _, _ = simulate(n=300, confound=6.0, seed=42)
        res = run(X, t, y, trim=0.25, bootstrap=50)
        self.assertEqual(res['status'], 'too_few_after_trimming')


class Glm(unittest.TestCase):
    def test_irls_ridge_logistic_and_poisson_recover_coefficients(self):
        r = np.random.default_rng(0)
        X = r.normal(size=(20000, 2))
        yl = (r.random(20000) < 1 / (1 + np.exp(-(0.3 + 1.0 * X[:, 0] - 0.5 * X[:, 1])))).astype(float)
        b0, b = uplift.fit_glm(X, yl, 'logistic', 0.01)
        self.assertAlmostEqual(b[0], 1.0, delta=0.08); self.assertAlmostEqual(b[1], -0.5, delta=0.08); self.assertAlmostEqual(b0, 0.3, delta=0.08)
        yp = r.poisson(np.exp(-0.5 + 0.4 * X[:, 0])).astype(float)
        b0, b = uplift.fit_glm(X, yp, 'poisson', 0.01)
        self.assertAlmostEqual(b[0], 0.4, delta=0.05); self.assertAlmostEqual(b0, -0.5, delta=0.05)


class ExecuteOnSemanticLayer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import test_program_effect
        cls.path, _ = test_program_effect.fixture()
        cls.params = {'program': 'care_management', 'outcome': 'ed_visits', 'post_days': 90, 'pre_days': 180, 'bootstrap': 100,
                      'capacity_patients': 10, 'trim': 0.02}
        cls.r = uplift.execute(cls.path, None, cls.params, test_program_effect.AS_OF)

    def test_shape_and_conventions(self):
        r = self.r
        for k in ('schema', 'parameters', 'as_of', 'answer', 'warnings', 'assumptions', 'inference_status', 'sample', 'overlap', 'ate',
                  'learners', 'segments', 'members', 'targeting', 'method', 'limitations'):
            self.assertIn(k, r)
        self.assertEqual(r['schema'], uplift.VERSION)
        self.assertIn(r['inference_status'], ('estimated_under_assumptions', 'limited'))
        self.assertEqual(set(r['learners']), set(uplift.LEARNERS))  # default 'all': T, X, DML, causal forest
        self.assertEqual(len(r['segments']), 10)
        self.assertLessEqual(len(r['answer']['recommended_targeting']), 10)
        for w in r['answer']['recommended_targeting']:
            self.assertIn('reasons', w); self.assertGreater(w['expected_value_usd'], 0)

    def test_recommendations_are_untreated_members_in_priority_order(self):
        from clinical_core.agere.clinical import program_effect
        wl = self.r['answer']['recommended_targeting']
        self.assertEqual([w['priority'] for w in wl], list(range(1, len(wl) + 1)))
        vals = [w['expected_value_usd'] for w in wl]
        self.assertEqual(vals, sorted(vals, reverse=True))
        treated_ids = {f'p{i:03d}' for i in range(0, 240, 5)}
        self.assertFalse(treated_ids & {w['patient_id'] for w in wl})

    def test_too_few_members_is_not_supported(self):
        r = uplift.execute(self.path, [f'p{i:03d}' for i in range(10)], self.params, '2029-08-15')
        self.assertEqual(r['inference_status'], 'not_supported')
        self.assertEqual(r['answer']['recommended_targeting'], [])

    def test_validate_rejects_unknown_and_bad_values(self):
        with self.assertRaises(ValueError): uplift.validate({'program': 'care_management', 'bogus': 1})
        with self.assertRaises(ValueError): uplift.validate({'program': 'nope'})
        with self.assertRaises(ValueError): uplift.validate({'program': 'care_management', 'trim': 0.9})
        with self.assertRaises(ValueError): uplift.validate({'treated_patient_ids': ['a']})
        p = uplift.validate({})
        self.assertEqual((p['program'], p['outcome'], p['folds'], p['capacity_patients']), ('care_management', 'ed_or_admission', 5, 50))


if __name__ == '__main__':
    unittest.main()
