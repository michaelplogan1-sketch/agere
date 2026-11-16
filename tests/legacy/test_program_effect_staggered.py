"""program_effect: synthetic DiD and Callaway-Sant'Anna recover known effects on simulated panels (and beat the naive estimators
they are meant to replace), and both run end to end through execute() from the semantic layer."""
import math, random, unittest
from datetime import date, timedelta

import numpy as np

from clinical_core.agere.clinical import program_effect, semantic_layer
from test_aggregate_explain import build, visit, pcp

AS_OF = '2029-08-15'
START = date(2028, 6, 1)


# ---- simulated panels ---------------------------------------------------------------
def factor_panel(seed, n_co=40, T=30, T0=20, effect=2.0, noise=0.3, trend=0.5):
    """Y_it = a_i + b_t + loading_i * trend * t + noise. The treated unit's loading (0.9) is high relative to the control mean (0.5),
    so equal-weight DiD violates parallel trends; a weighted combination of high-loading controls can match it."""
    rng = np.random.default_rng(seed)
    load = np.concatenate([rng.uniform(0.0, 1.0, n_co), [0.9]])
    a = rng.normal(0, 2, n_co + 1)
    t = np.arange(T)
    Y = a[:, None] + np.sin(t / 3.0)[None, :] + load[:, None] * trend * t[None, :] + rng.normal(0, noise, (n_co + 1, T))
    Y[-1, T0:] += effect
    treated = np.zeros(n_co + 1, bool); treated[-1] = True
    return Y, treated, T0


def weighted_twfe(Y, treated, T0, omega, lam):
    """omega x lambda weighted two-way FE coefficient on treated x post, solved by weighted least squares with dummies."""
    N, T = Y.shape
    w_u = np.where(treated, 1.0 / treated.sum(), 0.0); w_u[~treated] = omega
    w_t = np.concatenate([lam, np.full(T - T0, 1.0 / (T - T0))])
    rows, ys, ws = [], [], []
    for i in range(N):
        for t in range(T):
            x = np.zeros(N + T); x[i] = 1; x[N + t] = 1
            d = float(treated[i] and t >= T0)
            rows.append(np.append(x, d)); ys.append(Y[i, t]); ws.append(w_u[i] * w_t[t])
    A, y, w = np.array(rows), np.array(ys), np.sqrt(np.array(ws))
    sol = np.linalg.lstsq(A * w[:, None], y * w, rcond=None)[0]
    return float(sol[-1])


class SyntheticDiD(unittest.TestCase):
    def test_recovers_constant_effect(self):
        errs = []
        for seed in range(6):
            Y, tr, T0 = factor_panel(seed)
            r = program_effect.sdid(Y, tr, T0, draws=60, seed=seed)
            errs.append(r['effect'] - 2.0)
            self.assertEqual(r['se_method'], 'placebo')
            self.assertGreater(r['se'], 0)
            self.assertAlmostEqual(float(r['unit_weight_vector'].sum()), 1.0, places=6)
            self.assertAlmostEqual(float(r['time_weight_vector'].sum()), 1.0, places=6)
            self.assertTrue((r['unit_weight_vector'] >= -1e-12).all() and (r['time_weight_vector'] >= -1e-12).all())
        self.assertLess(abs(float(np.mean(errs))), 0.3, errs)
        self.assertLess(max(abs(e) for e in errs), 0.7, errs)

    def test_beats_plain_did_under_violated_parallel_trend(self):
        sd, dd = [], []
        for seed in range(8):
            Y, tr, T0 = factor_panel(seed + 100)
            r = program_effect.sdid(Y, tr, T0, draws=20, seed=seed)
            sd.append(abs(r['effect'] - 2.0)); dd.append(abs(r['did_unweighted'] - 2.0))
        self.assertLess(float(np.mean(sd)), 0.5 * float(np.mean(dd)), (sd, dd))
        self.assertLess(float(np.mean(dd)), 6.0)
        self.assertGreater(float(np.mean(dd)), 1.0, 'the design must actually violate parallel trends for plain DiD')
        self.assertLess(r['pre_fit_rmse'], r['pre_fit_rmse_unweighted_did'])

    def test_estimator_equals_weighted_two_way_fe(self):
        Y, tr, T0 = factor_panel(5, n_co=12, T=14, T0=9)
        r = program_effect.sdid(Y, tr, T0, draws=10)
        self.assertAlmostEqual(r['effect'], weighted_twfe(Y, tr, T0, r['unit_weight_vector'], r['time_weight_vector']), places=6)

    def test_jackknife_when_several_units_treated(self):
        Y, tr, T0 = factor_panel(9, n_co=30)
        Y = np.vstack([Y, Y[-1:] + np.random.default_rng(1).normal(0, 0.3, (1, Y.shape[1]))])
        tr = np.append(tr, True)
        r = program_effect.sdid(Y, tr, T0)
        self.assertEqual(r['se_method'], 'jackknife'); self.assertEqual(r['treated_units'], 2)
        self.assertTrue(math.isfinite(r['se']) and r['se'] > 0)
        self.assertLess(abs(r['effect'] - 2.0), 1.0)
        lo, hi = r['ci95']; self.assertLess(lo, hi)

    def test_null_effect_ci_covers_zero(self):
        Y, tr, T0 = factor_panel(21, effect=0.0)
        r = program_effect.sdid(Y, tr, T0, draws=120, seed=3)
        lo, hi = r['ci95']; self.assertTrue(lo <= 0 <= hi, r['effect'])


def cs_panel(seed, n_never=300, n_g=(300, 300), cohorts=(3, 7), T=12, noise=0.5):
    """Cohort-specific dynamic effects: early cohort tau(e) = 4 (e+1) ... heterogeneous in cohort and time. Truth returned per (g, t)."""
    rng = np.random.default_rng(seed)
    G = np.concatenate([np.full(n_never, np.inf)] + [np.full(n, float(g)) for n, g in zip(n_g, cohorts)])
    n = len(G); a = rng.normal(0, 1, n); b = 0.3 * np.arange(T)
    Y = a[:, None] + b[None, :] + rng.normal(0, noise, (n, T))
    def tau(g, t): return (2.0 * (t - g + 1) if g == cohorts[0] else 1.0) if t >= g else 0.0
    truth = {}
    for i in range(n):
        if np.isfinite(G[i]):
            for t in range(T): Y[i, t] += tau(int(G[i]), t)
    for g in cohorts:
        for t in range(T): truth[(g, t)] = tau(g, t)
    return Y, G, truth


def twfe(Y, G):
    n, T = Y.shape
    D = (np.arange(T)[None, :] >= G[:, None]).astype(float)
    Dd = D - D.mean(1, keepdims=True) - D.mean(0, keepdims=True) + D.mean()
    Yd = Y - Y.mean(1, keepdims=True) - Y.mean(0, keepdims=True) + Y.mean()
    return float((Dd * Yd).sum() / (Dd ** 2).sum())


class CallawaySantAnna(unittest.TestCase):
    def test_recovers_heterogeneous_cohort_effects_where_twfe_is_biased(self):
        Y, G, truth = cs_panel(0)
        T = Y.shape[1]
        r = program_effect.callaway_santanna(Y, G, draws=500, seed=1)
        gt = {(c['group'], c['time']): c for c in r['group_time']}
        for (g, t), tv in truth.items():
            if t == g - 1: continue
            c = gt[(g, t)]
            self.assertLess(abs(c['att'] - tv), max(4.0 * c['se'], 0.25), (g, t, c['att'], tv))
        # truth for the simple (cohort-size weighted, post-treatment) aggregation
        cells = [(g, t) for g in (3, 7) for t in range(g, T)]
        true_simple = sum(truth[c] for c in cells) / len(cells)
        self.assertLess(abs(r['simple']['att'] - true_simple), 0.3, (r['simple'], true_simple))
        lo, hi = r['simple']['ci95']; self.assertTrue(lo <= true_simple <= hi, (r['simple'], true_simple))
        # by cohort
        by = {c['group']: c['att'] for c in r['by_cohort']}
        self.assertAlmostEqual(by[3], float(np.mean([truth[(3, t)] for t in range(3, T)])), delta=0.3)
        self.assertAlmostEqual(by[7], 1.0, delta=0.25)
        # dynamic: e=0 averages the two cohorts' first-period effects (2 and 1)
        dyn = {d['event_time']: d for d in r['dynamic']}
        self.assertAlmostEqual(dyn[0]['att'], 1.5, delta=0.25)
        self.assertTrue(all(d['uniform_ci95'][0] <= d['att'] <= d['uniform_ci95'][1] for d in r['dynamic']))
        self.assertEqual(r['pretrend']['status'], 'passed', r['pretrend'])
        # the two-way fixed-effects coefficient is far from the truth on the same panel
        bias_twfe = abs(twfe(Y, G) - true_simple)
        self.assertGreater(bias_twfe, 0.8, bias_twfe)
        self.assertLess(abs(r['simple']['att'] - true_simple), 0.25 * bias_twfe)

    def test_twfe_negative_weights_all_treated_eventually(self):
        """No never-treated units: not-yet-treated controls still recover cohort 1's effect; TWFE misses its sign pattern."""
        Y, G, truth = cs_panel(1, n_never=0, n_g=(400, 400), cohorts=(3, 8))
        r = program_effect.callaway_santanna(Y, G, control_group='not_yet_treated', draws=300, seed=2)
        c3 = [c for c in r['group_time'] if c['group'] == 3 and 3 <= c['time'] < 8]
        self.assertTrue(c3)
        for c in c3: self.assertLess(abs(c['att'] - truth[(3, c['time'])]), max(4 * c['se'], 0.3), c)
        with self.assertRaises(ValueError):
            program_effect.callaway_santanna(Y, G, control_group='never_treated')
        post_true = [truth[(g, t)] for g in (3, 8) for t in range(g, Y.shape[1])]
        self.assertGreater(abs(twfe(Y, G) - float(np.mean(post_true))), 0.5)

    def test_null_and_placebo_pretrends(self):
        Y, G, _ = cs_panel(4, cohorts=(3, 7)); 
        Y0 = Y.copy()
        for i in np.flatnonzero(np.isfinite(G)):
            for t in range(Y.shape[1]):
                if t >= G[i]: Y0[i, t] -= (2.0 * (t - G[i] + 1) if G[i] == 3 else 1.0)
        r = program_effect.callaway_santanna(Y0, G, draws=400, seed=1)
        lo, hi = r['simple']['ci95']; self.assertTrue(lo <= 0 <= hi, r['simple'])

    def test_covariate_estimators_remove_confounding(self):
        """Treated cohort selected on covariate x, which also drives the trend: unconditional DiD is biased, all three adjusted estimators are not."""
        rng = np.random.default_rng(11)
        n, T, g = 3000, 8, 4
        x = rng.normal(0, 1, n)
        p = 1 / (1 + np.exp(-1.2 * x))
        G = np.where(rng.random(n) < p, float(g), np.inf)
        Y = rng.normal(0, 0.5, (n, T)) + 0.5 * x[:, None] * np.arange(T)[None, :]
        Y[np.isfinite(G), g:] += 1.5
        X = x[:, None]
        naive = program_effect.callaway_santanna(Y, G, draws=100)['simple']['att']
        self.assertGreater(abs(naive - 1.5), 0.5)
        for est in program_effect.CS_ESTIMATORS:
            r = program_effect.callaway_santanna(Y, G, X, estimator=est, draws=200, seed=3)
            s = r['simple']
            self.assertLess(abs(s['att'] - 1.5), 0.2, (est, s))
            self.assertGreater(s['se'], 0)
            self.assertTrue(s['ci95'][0] - 0.15 <= 1.5 <= s['ci95'][1] + 0.15, (est, s))


# ---- end to end through execute() ----------------------------------------------------
def fixture(staggered, n=480, effect=0.3, seed=5):
    """ED visits as a Poisson process; 12 practices of 40 patients; every 5th patient receives a care-management contact (status agrees).
    staggered: contacts spread over 2028-11..2029-06 (two cohorts-plus); otherwise within Feb 2029. After the contact the rate is x effect."""
    rng = random.Random(seed)
    patients, recs = {}, []
    for i in range(n):
        pid = f'p{i:03d}'
        prac = i % 12
        patients[pid] = {'age_reported': 72 if i % 2 else 45, 'sex_reported': 'F' if (i // 2) % 2 else 'M'}
        recs.append(pcp(pid, f'dr{prac}', f'PR{prac}'))
        recs.append((pid, 'coverage', {'coverage_type': 'medicare', 'status': 'active', 'effective_start': '2026-01-01', 'event_date': '2026-01-01'}))
        base = (1.2 + 0.1 * prac) / 30
        t0 = None
        if i % 5 == 0:
            t0 = (date(2028, 11, 1) + timedelta(days=rng.randrange(240)) if staggered else date(2029, 2, 1) + timedelta(days=rng.randrange(28)))
            recs.append((pid, 'care_management_contacts', {'status': 'agrees', 'event_date': t0.isoformat()}))
        d = START
        while d < date(2029, 8, 15):
            lam = base * effect if t0 and d > t0 else base
            if rng.random() < lam: recs.append(visit(pid, 'emergency', d.isoformat()))
            d += timedelta(days=1)
        for m in range(14): recs.append(visit(pid, 'primary_care', (START + timedelta(days=30 * m + i % 20)).isoformat()))
    path = build(recs, patients)
    semantic_layer.build(path)
    return path


PARAMS = {'program': 'care_management', 'outcome': 'ed_visits', 'pre_days': 180, 'post_days': 150, 'bootstrap': 300}


class Execute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conc = fixture(False)
        cls.stag = fixture(True)

    def test_default_method_unchanged(self):
        r = program_effect.execute(self.conc, None, PARAMS, AS_OF)
        self.assertEqual(r['parameters'].get('method'), None)
        self.assertIsNotNone(r['did']); self.assertNotIn('estimation_method', r)

    def test_validate_rejects_bad_values(self):
        for bad in ({'method': 'x'}, {'method': 'sdid', 'control_group': 'x'}, {'estimator': 'ipw'}, {'method': 'callaway_santanna', 'covariates': ['zip']}):
            with self.assertRaises(ValueError): program_effect.validate({**PARAMS, **bad})

    def test_sdid_execute(self):
        r = program_effect.execute(self.conc, None, {**PARAMS, 'method': 'sdid'}, AS_OF)
        self.assertEqual(r['estimation_method'], 'sdid')
        s = r['sdid']; self.assertEqual(s['status'], 'estimated', r['warnings'])
        self.assertLess(s['effect'], -0.2); self.assertGreater(s['se'], 0)
        self.assertEqual(s['se_method'], 'placebo')
        for k in ('ci95', 'unit_weights', 'time_weights', 'pre_fit', 'did_unweighted'): self.assertIn(k, s)
        self.assertIn(r['inference'], ('limited', 'supported_under_assumptions'))
        self.assertTrue(r['assumptions'] and r['reading'].startswith('Estimated effect'))
        self.assertEqual(r['rows'][0]['analysis'], 'synthetic_difference_in_differences')

    def test_cs_execute_and_auto(self):
        r = program_effect.execute(self.stag, None, {**PARAMS, 'method': 'callaway_santanna'}, AS_OF)
        c = r['callaway_santanna']; self.assertEqual(c['status'], 'estimated', r['warnings'])
        self.assertLess(c['simple']['att'], -0.2)
        self.assertGreaterEqual(len(c['by_cohort']), 2)
        self.assertTrue(c['group_time'] and c['dynamic'] and 'uniform_critical_value' in c)
        a = program_effect.execute(self.stag, None, {**PARAMS, 'method': 'auto'}, AS_OF)
        self.assertEqual(a['estimation_method'], 'callaway_santanna'); self.assertIn('staggered', a['method_selection'])
        b = program_effect.execute(self.conc, None, {**PARAMS, 'method': 'auto'}, AS_OF)
        self.assertEqual(b['estimation_method'], 'sdid')
        d = program_effect.execute(self.stag, None, {**PARAMS, 'method': 'callaway_santanna', 'control_group': 'not_yet_treated',
                                                     'covariates': ['age_band', 'chronic_conditions'], 'estimator': 'doubly_robust'}, AS_OF)
        self.assertEqual(d['callaway_santanna']['status'], 'estimated', d['warnings'])

    def test_too_few_exposed_not_supported(self):
        r = program_effect.execute(self.conc, ['p000', 'p004', 'p001', 'p002', 'p003'], {**PARAMS, 'method': 'sdid'}, AS_OF)
        self.assertEqual(r['inference'], 'not_supported'); self.assertTrue(r['blocking_warnings'])


if __name__ == '__main__':
    unittest.main()
