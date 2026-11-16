"""Fair provider/practice profiling (hierarchical Bayes + Buhlmann-Straub credibility).

Estimator correctness on simulated data with known unit effects (shrinkage beats raw rates; credible and rank
intervals cover the truth near the nominal rate), validation errors, the operator end to end on a small synthetic
store, compiler routing, graph integration, and a read-only smoke test on the real stack when it is present."""
import json
import sqlite3
import unittest
from pathlib import Path

import numpy as np

from clinical_core.agere.clinical import (analysis_graph, capability_registry, graph_contracts, graph_planner, local_compiler,
                                          profile, semantic_layer, source_profile)
from test_aggregate_explain import build, pcp, visit

AS_OF = '2029-08-15'
REAL_STACK = Path('/Users/mikel/clinical-insights-local/artifacts/agere-healthcare-v123/stack')
ROOT = Path(__file__).resolve().parents[2]


def simulate(kind, rep, J=40):
    """Member-level data for J units with known relative effects theta (1 = network)."""
    rng = np.random.default_rng(1000 + rep)
    sizes = np.maximum(5, rng.lognormal(4.0, 1.0, J)).astype(int)
    if kind == 'count':
        theta, base = rng.gamma(8, 1 / 8, J), 0.4
    elif kind == 'binomial':
        theta, base = rng.beta(30, 70, J) / 0.3, 0.3
    else:
        theta, base = rng.normal(1, 0.15, J), 500.0
    units, y, w = [], [], []
    for j in range(J):
        m = sizes[j]
        if kind == 'count': yy = rng.poisson(theta[j] * base * rng.gamma(2, 0.5, m))  # member heterogeneity: overdispersed
        elif kind == 'binomial': yy = rng.binomial(1, min(theta[j] * base, 1), m)
        else: yy = 12 * base * theta[j] * rng.lognormal(-0.5, 1.0, m)  # skewed member costs
        units += [f'u{j:02d}'] * m; y += list(yy); w += [12.0 if kind == 'cost' else 1.0] * m
    y, w = np.array(y, float), np.array(w)
    level = y.sum() / w.sum()
    truth = theta * base / level
    return units, y, w, level * w, truth


class Estimators(unittest.TestCase):
    """30 replicate networks of 40 units each per model (sizes 5..~1,000 members)."""

    def measure(self, kind, method='bayes', reps=30):
        out = []
        for rep in range(reps):
            units, y, w, e, truth = simulate(kind, rep)
            r = profile.analyse(units, y, w, e, kind, method)
            true_rank = len(truth) - np.argsort(np.argsort(truth))
            out.append((np.sqrt(np.mean((r['raw'] - truth) ** 2)), np.sqrt(np.mean((r['mean'] - truth) ** 2)),
                        np.mean((r['lo'] <= truth) & (truth <= r['hi'])),
                        np.mean((r['rank_lo'] <= true_rank) & (true_rank <= r['rank_hi']))))
        return [float(np.mean(c)) for c in zip(*out)]

    def test_poisson_gamma_shrinkage_and_coverage(self):
        raw, post, cov, rcov = self.measure('count')
        self.assertLess(post, 0.8 * raw)  # measured: 0.224 vs 0.329
        self.assertTrue(0.90 <= cov <= 0.99, cov)  # measured 0.93
        self.assertTrue(0.90 <= rcov <= 1.0, rcov)  # measured 0.96

    def test_beta_binomial_shrinkage_and_coverage(self):
        raw, post, cov, rcov = self.measure('binomial')
        self.assertLess(post, 0.6 * raw)  # measured: 0.122 vs 0.266
        self.assertTrue(0.90 <= cov <= 0.99, cov)  # measured 0.95
        self.assertTrue(0.90 <= rcov <= 1.0, rcov)

    def test_normal_cost_model_shrinkage_and_coverage(self):
        raw, post, cov, rcov = self.measure('cost')
        self.assertLess(post, 0.7 * raw)  # measured: 0.117 vs 0.219
        self.assertTrue(0.88 <= cov <= 0.99, cov)  # measured 0.93 (skewed member costs, units as small as 5)
        self.assertTrue(0.90 <= rcov <= 1.0, rcov)

    def test_credibility_matches_bayes_point_estimates_and_shrinks(self):
        raw, post, cov, _ = self.measure('count', 'credibility', reps=10)
        self.assertLess(post, 0.8 * raw)
        self.assertGreater(cov, 0.85)  # normal-approximation intervals: slightly under nominal, reported as such

    def test_buhlmann_straub_textbook_quantities(self):
        # Two units, equal weights: EPV = pooled within variance, VHM from the between-unit spread.
        units = ['a'] * 3 + ['b'] * 3
        x = [1.0, 2.0, 3.0, 5.0, 6.0, 7.0]
        r = profile.buhlmann_straub(units, x, [1.0] * 6)
        self.assertAlmostEqual(r['epv'], 1.0)  # ((1+0+1) + (1+0+1)) / 4
        # sum W (X - Xbar)^2 = 3*4 + 3*4 = 24; VHM = (24 - 1*1) / (6 - 18/6) = 23/3
        self.assertAlmostEqual(r['vhm'], 23 / 3)
        self.assertAlmostEqual(r['k'], 3 / 23)
        self.assertTrue(np.allclose(r['Z'], 3 / (3 + 3 / 23)))
        self.assertAlmostEqual(float(r['estimate'][0]), r['Z'][0] * 2 + (1 - r['Z'][0]) * 4)

    def test_no_between_unit_variation_gives_zero_credibility_and_wide_honest_intervals(self):
        rng = np.random.default_rng(3)
        units = [f'u{j}' for j in range(20) for _ in range(50)]
        y = rng.poisson(0.3, len(units)).astype(float)
        r = profile.analyse(units, y, np.ones(len(units)), np.full(len(units), y.mean()), 'count')
        self.assertTrue(np.all(r['Z'] == 0) or r['hyper']['credibility_k'] > 100)
        self.assertTrue(np.all(r['flag'] == 'indistinguishable'))
        self.assertTrue(np.all(r['hi'] - r['lo'] > 0.1))  # not collapsed onto the mean

    def test_a_clear_outlier_is_flagged_truly_high_with_rank_one(self):
        rng = np.random.default_rng(5)
        units, y = [], []
        for j in range(15):
            lam = 1.2 if j == 0 else 0.3
            units += [f'u{j:02d}'] * 200; y += list(rng.poisson(lam, 200))
        y = np.array(y, float)
        r = profile.analyse(units, y, np.ones(len(y)), np.full(len(y), y.mean()), 'count')
        i = r['labels'].index('u00')
        self.assertEqual(r['flag'][i], 'truly_high')
        self.assertEqual(int(r['rank_lo'][i]), 1)
        self.assertGreater(r['p_above'][i], 0.999)


class Validation(unittest.TestCase):
    def test_defaults(self):
        p = profile.validate({'metric': 'ed_visits'})
        self.assertEqual((p['unit'], p['adjust'], p['method'], p['threshold'], p['top']), ('pcp', 'age_sex', 'bayes', None, 200))
        self.assertEqual(profile.validate({'metric': 'a1c_poor_control'})['adjust'], 'none')
        self.assertEqual(profile.validate({'metric': 'a1c_poor_control', 'threshold': 20})['threshold'], 0.2)  # percent -> share

    def test_errors(self):
        for bad in ({}, {'metric': 'mortality'}, {'metric': 'ed_visits', 'unit': 'hospital'}, {'metric': 'ed_visits', 'adjust': 'race'},
                    {'metric': 'ed_visits', 'method': 'frequentist'}, {'metric': 'ed_visits', 'threshold': -1},
                    {'metric': 'a1c_poor_control', 'threshold': 250}, {'metric': 'ed_visits', 'period': {'start': '2028'}},
                    {'metric': 'ed_visits', 'top': 0}, {'metric': 'ed_visits', 'extra': 1}, {'metric': 'ed_visits', 'focus': 3}):
            with self.assertRaises(ValueError, msg=bad):
                profile.validate(bad)

    def test_graph_rejects_period_after_snapshot(self):
        plan = {'interpretation': 'x', 'coverage': 'supported_observation', 'unmet_requirements': [],
                'nodes': [{'id': 'p', 'operation': 'profile', 'inputs': [], 'purpose': 'x',
                           'parameters': {'metric': 'ed_visits', 'period': {'start': '2029-01-01', 'end': '2030-01-01'}}}]}
        with self.assertRaises(ValueError):
            analysis_graph.validate(plan, AS_OF)


class Fixture(unittest.TestCase):
    """Six PCPs in three practices; drA0 has a large panel with heavy ED use."""

    @classmethod
    def setUpClass(cls):
        patients, recs = {}, []
        n = 0
        for practice, clinicians in (('PR1', ('drA0', 'drA1')), ('PR2', ('drB0', 'drB1')), ('PR3', ('drC0', 'drC1'))):
            for clin in clinicians:
                for k in range(40 if clin == 'drA0' else 8):
                    pid = f'p{n}'; n += 1
                    age = 30 + (k * 7) % 50
                    # The governed age definition uses received birth_date at
                    # AS_OF; a feed's reported age cannot establish eligibility.
                    patients[pid] = {'age_reported': age, 'birth_date': f'{2029-age}-01-01',
                                     'sex_reported': 'F' if k % 2 else 'M'}
                    recs.append((pid, 'coverage', {'coverage_type': 'commercial', 'status': 'active', 'effective_start': '2028-01-01',
                                                   'event_date': '2028-01-01'}))
                    recs.append(pcp(pid, clin, practice))
                    visits = 3 if clin == 'drA0' else (1 if k % 4 == 0 else 0)
                    for v in range(visits):
                        recs.append(visit(pid, 'emergency', f'2029-0{v + 2}-1{k % 9}'))
                    if k % 3 == 0:
                        recs.append((pid, 'conditions', {'description': 'Diabetes mellitus type 2', 'event_date': '2027-01-01'}))
                        a1c = 10.1 if clin == 'drB0' else 7.0
                        recs.append((pid, 'labs', {'test': 'HbA1c', 'value': a1c, 'unit': '%', 'event_date': '2029-03-01'}))
        recs.append(('p0', 'adt', {'event': 'emergency_registration', 'event_date': '2029-02-10'}))  # the compiler expects an ADT stream
        cls.path = build(recs, patients)
        semantic_layer.build(cls.path)
        cls.ids = sorted(patients)

    def test_operator_end_to_end_flags_the_heavy_ed_pcp(self):
        r = profile.execute(self.path, None, {'metric': 'ed_visits', 'unit': 'pcp'}, AS_OF)
        rows = {u['group']: u for u in r['units']}
        self.assertEqual(set(rows), {'drA0', 'drA1', 'drB0', 'drB1', 'drC0', 'drC1'})
        a = rows['drA0']
        self.assertEqual(a['members'], 40)
        self.assertEqual(a['observed'], 120)
        self.assertEqual(a['flag'], 'truly_high')
        self.assertEqual(a['rank_ci95'][0], 1)
        for u in r['units']:
            self.assertLessEqual(u['ci95_low'], u['posterior_rate'])
            self.assertLessEqual(u['posterior_rate'], u['ci95_high'])
            self.assertTrue(0 <= u['credibility_z'] <= 1)
        # shrinkage: small panels' posterior lies between their raw rate and the network rate
        for g in ('drB1', 'drC1'):
            u = rows[g]
            lo, hi = sorted([u['adjusted_raw_rate'], r['network_rate']])
            self.assertTrue(lo - 1e-6 <= u['posterior_rate'] <= hi + 1e-6, u)
        text = profile.summary(r)
        self.assertIn('drA0', text)
        self.assertIn('truly high', text)

    def test_population_input_and_focus_and_threshold(self):
        r = profile.execute(self.path, self.ids[:60], {'metric': 'ed_visits', 'unit': 'practice', 'focus': 'pr1', 'threshold': 100}, AS_OF)
        self.assertEqual(r['members'], 60)
        self.assertEqual(r['focus']['group'], 'PR1')
        self.assertIn('p_above_threshold', r['units'][0])
        self.assertTrue(profile.summary(r).startswith('PR1:'))
        missing = profile.execute(self.path, None, {'metric': 'ed_visits', 'focus': 'drZ9'}, AS_OF)
        self.assertIn('drZ9', ' '.join(missing['notes']))

    def test_quality_share_and_credibility_method(self):
        r = profile.execute(self.path, None, {'metric': 'a1c_poor_control', 'unit': 'pcp', 'method': 'credibility'}, AS_OF)
        rows = {u['group']: u for u in r['units']}
        self.assertEqual(rows['drB0']['observed'], 3)  # 3 diabetics, all A1c > 9
        self.assertEqual(r['parameters']['adjust'], 'none')
        self.assertIn('Buhlmann-Straub', r['method'])

    def test_graph_answer_contract(self):
        plan = analysis_graph.validate({'interpretation': 'x', 'coverage': 'supported_observation', 'unmet_requirements': [],
                                        'nodes': [{'id': 'answer', 'operation': 'profile', 'inputs': [], 'purpose': 'x',
                                                   'parameters': {'metric': 'ed_visits', 'unit': 'pcp'}}]}, AS_OF)
        report = profile.execute(self.path, None, plan['nodes'][0]['parameters'], AS_OF)
        answer = analysis_graph.build_answer(plan, [{'node': plan['nodes'][0], 'result': report}], {}, 'which PCPs truly have high ED use?')
        answer['headline'] = analysis_graph.headline(answer)
        answer = json.loads(json.dumps(answer, allow_nan=False, default=str))
        self.assertEqual((answer['status'], answer['answer_type']), ('answered', 'table'))
        self.assertTrue(answer['headline'].startswith('Of 6 PCPs'))
        o = answer['overall']['profile']
        self.assertEqual(o['counts']['truly_high'] + o['counts']['truly_low'] + o['counts']['indistinguishable'], 6)
        for row in answer['table']:
            self.assertTrue({'group', 'raw_rate', 'posterior_rate', 'ci95_low', 'ci95_high', 'p_above_network', 'rank', 'rank_ci95',
                             'credibility_z', 'credibility_rate', 'flag'} <= set(row))


class Routing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Fixture.setUpClass()
        cls.profile = source_profile.load(Fixture.path)

    def compile(self, q):
        return local_compiler.compile(q, self.profile, AS_OF)

    def node(self, q):
        g, info = self.compile(q)
        self.assertIsNotNone(g, f'{q}: {info.get("reason")}')
        self.assertEqual(info['route'], 'local_compiler', info.get('reason'))
        self.assertEqual(g['nodes'][-1]['operation'], 'profile', q)
        analysis_graph.validate(g, AS_OF)
        return g['nodes'][-1]['parameters'], g

    def test_phrasings(self):
        cases = [
            ('Which PCPs have truly high ED use after accounting for panel size and case mix?', {'metric': 'ed_visits', 'unit': 'pcp', 'adjust': 'age_sex'}),
            ('Rank practices on A1c poor control fairly', {'metric': 'a1c_poor_control', 'unit': 'practice'}),
            ("Is Dr drA0's admission rate really an outlier?", {'metric': 'admissions', 'unit': 'pcp', 'focus': 'dra0'}),
            ('credibility-weighted PMPM by practice', {'metric': 'pmpm', 'unit': 'practice', 'method': 'credibility'}),
            ('Which providers are outliers on readmissions, adjusted for panel size?', {'metric': 'readmission_rate', 'unit': 'pcp'}),
            ('shrunk ED visit rates for each PCP', {'metric': 'ed_visits', 'unit': 'pcp', 'method': 'bayes'}),
            ('Compare practices fairly on hospitalizations adjusted for comorbidity burden', {'metric': 'admissions', 'unit': 'practice', 'adjust': 'age_sex_conditions'}),
            ('Which doctors are truly worse on blood pressure control?', {'metric': 'bp_control', 'unit': 'pcp'}),
            ('Which practices have ED use truly above 80 per 1,000?', {'metric': 'ed_visits', 'unit': 'practice', 'threshold': 80.0}),
        ]
        for q, want in cases:
            with self.subTest(q=q):
                params, _ = self.node(q)
                for k, v in want.items():
                    self.assertEqual(params[k], v, (q, params))

    def test_population_restriction(self):
        params, g = self.node('Which PCPs have truly high ED use among diabetics?')
        self.assertEqual(g['nodes'][0]['operation'], 'cohort')
        self.assertEqual(g['nodes'][-1]['inputs'], [g['nodes'][0]['id']])

    def test_declines_and_non_profile_questions(self):
        g, info = self.compile('Rank our PCPs fairly')  # profiling intent, no measure named: declined, not guessed
        self.assertIsNone(g)
        self.assertEqual(info['route'], 'fallback')
        g, info = self.compile('What would our shared savings be with a benchmark of $90 PMPM and a sharing rate of 50%, by practice?')
        self.assertNotEqual((g or {'nodes': [{}]})['nodes'][-1].get('operation'), 'profile')
        g, info = self.compile('How many ED visits by practice?')  # plain literal/aggregate question stays on its route
        self.assertTrue(g is None or g['nodes'][-1]['operation'] != 'profile')

    def test_acceptance_file(self):
        rows = json.loads((ROOT / 'bench' / 'acceptance' / 'profile.json').read_text())
        self.assertGreaterEqual(len(rows), 8)
        for row in rows:
            with self.subTest(q=row['question']):
                self.assertEqual(row['expect_operation'], 'profile')
                self.node(row['question'])


class Integration(unittest.TestCase):
    def test_registered_everywhere(self):
        self.assertIn('profile', analysis_graph.OPERATIONS)
        self.assertIn('profile', graph_contracts.TYPED)
        self.assertEqual(graph_contracts.input_contract('profile')['count'], [0, 1])
        self.assertIn('profile', graph_planner.CONTRACTS)
        self.assertEqual(capability_registry.PRODUCT['profile']['population'], 'optional')
        schema = graph_contracts.parameter_schema('profile', {'cohort_fields': {'record_fields': {}}, 'evidence_catalog': {}})
        self.assertEqual(set(schema['properties']['metric']['enum']), set(profile.METRICS))

    def test_console_renders_profile_answers(self):
        from clinical_core.agere import ask_view
        html = ask_view.page()
        self.assertIn('function vizProfile', html)
        self.assertIn('if(o.profile)return vizProfile(a)', html)
        self.assertEqual(html.count('</script>'), 2)


@unittest.skipUnless((REAL_STACK / 'semantic.sqlite3').exists() and (REAL_STACK / 'run.json').exists(), 'real stack not present')
class RealStack(unittest.TestCase):
    """Read-only: runs only when the semantic layer is current (otherwise ensure() would rebuild it)."""

    @classmethod
    def setUpClass(cls):
        db = sqlite3.connect(f'file:{REAL_STACK / "semantic.sqlite3"}?mode=ro', uri=True)
        try:
            meta = dict(db.execute('SELECT key, value FROM meta'))
        finally:
            db.close()
        run = json.loads((REAL_STACK / 'run.json').read_text())
        if meta.get('version') != semantic_layer.VERSION or meta.get('source_digest') != run['source_digest']:
            raise unittest.SkipTest('real semantic layer is not current; not rebuilding it')

    def test_ed_by_pcp(self):
        r = profile.execute(REAL_STACK, None, {'metric': 'ed_visits', 'unit': 'pcp'})
        self.assertGreater(r['units_total'], 10)
        self.assertEqual(sum(r['counts'].values()), r['units_total'])
        for u in r['units']:
            self.assertLessEqual(u['ci95_low'], u['ci95_high'])
            self.assertLessEqual(u['rank_ci95'][0], u['rank_ci95'][1])
        self.assertTrue(profile.summary(r))

    def test_acceptance_phrasings_route_against_the_real_data_dictionary(self):
        prof = json.loads((REAL_STACK / 'source-profile.json').read_text()) if (REAL_STACK / 'source-profile.json').exists() else None
        run = json.loads((REAL_STACK / 'run.json').read_text())
        if not prof or prof.get('schema') != source_profile.VERSION or prof.get('source_digest') != run['source_digest']:
            self.skipTest('saved source profile is not current; not rebuilding it')
        for row in json.loads((ROOT / 'bench' / 'acceptance' / 'profile.json').read_text()):
            with self.subTest(q=row['question']):
                g, info = local_compiler.compile(row['question'].replace('drA0', 'WH-CLIN-1-primary-2'), prof, AS_OF)
                self.assertIsNotNone(g, info.get('reason'))
                self.assertEqual(g['nodes'][-1]['operation'], 'profile')

    def test_pmpm_by_practice_credibility(self):
        r = profile.execute(REAL_STACK, None, {'metric': 'pmpm', 'unit': 'practice', 'method': 'credibility'})
        self.assertGreater(r['units_total'], 1)
        self.assertTrue(all(0 <= u['credibility_z'] <= 1 for u in r['units']))


if __name__ == '__main__':
    unittest.main()
