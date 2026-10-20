"""Contract strategy: risk contracts as options, track selection and Nash negotiation (synthetic data only)."""
import json, unittest
from pathlib import Path
from unittest import mock

import numpy as np
from scipy.stats import norm

from clinical_core.agere import ask_view
from clinical_core.agere.clinical import analysis_graph, contract_strategy as CS, graph_contracts, local_compiler, programs, source_profile, vbc
from clinical_core.agere.clinical import operator_router as router
from clinical_core.agere.clinical.capability_registry import PRODUCT
from clinical_core.agere.clinical.graph_planner import CONTRACTS

ROOT = Path(__file__).resolve().parent
AS_OF = '2029-08-15'
P = {'confidence': 0.95, 'risk_aversion': 0.5}


def program_terms(name, members=3000):
    t = programs.contract_terms(name, {'participant_revenue': 1000000})
    t.pop('program')
    return CS._resolve_msr(t, members)


class Payoff(unittest.TestCase):
    def test_vectorised_payout_is_vbc_settle(self):
        rng = np.random.default_rng(3)
        sets = [{'sharing_rate': 0.5, 'msr': 0.02, 'two_sided': True, 'loss_rate': 0.4, 'savings_cap_pct': 0.1, 'loss_cap_pct': 0.05},
                {'sharing_rate': 0.6}, *[program_terms(n) for n in ('MSSP BASIC A', 'MSSP BASIC E', 'MSSP ENHANCED',
                                                                     'ACO REACH Professional', 'ACO REACH Global')]]
        for t in sets:
            a, b = rng.uniform(700, 1300, 400), rng.uniform(900, 1100, 400)
            np.testing.assert_allclose(CS.payout(a, b, 12000, t), [vbc.settle(x, y, 12000, t) for x, y in zip(a, b)], atol=1e-6)

    def test_upside_is_a_call_on_savings_closed_form(self):
        # payout = s x mm x (B - C) when C <= B(1 - MSR): E = s mm [(B - mu) Phi(z) + sigma phi(z)], z = (B(1-MSR) - mu) / sigma
        mu, sigma, B, s, msr, mm = 100.0, 6.0, 104.0, 0.5, 0.02, 1000.0
        cost = np.random.default_rng(0).normal(mu, sigma, 400_000)
        sim = CS.Simulation.from_draws(cost, mm=mm)
        st, _ = CS._evaluate(sim, {'sharing_rate': s, 'msr': msr}, B, 0.0, P)
        z = (B * (1 - msr) - mu) / sigma
        closed = s * mm * ((B - mu) * norm.cdf(z) + sigma * norm.pdf(z))
        self.assertAlmostEqual(st['expected_payout'] / closed, 1.0, delta=0.01)
        self.assertAlmostEqual(st['upside_value'], st['expected_payout'])
        self.assertEqual(st['downside_cost'], 0.0)

    def test_downside_is_a_short_put_closed_form(self):
        # owed = l x mm x (C - B) when C >= B(1 + MLR): E = l mm [(mu - B)(1 - Phi(z)) + sigma phi(z)], z = (B(1+MLR) - mu) / sigma
        mu, sigma, B, l, mlr, mm = 100.0, 6.0, 98.0, 0.4, 0.01, 1000.0
        cost = np.random.default_rng(1).normal(mu, sigma, 400_000)
        sim = CS.Simulation.from_draws(cost, mm=mm)
        st, _ = CS._evaluate(sim, {'sharing_rate': 0.5, 'msr': 0.02, 'mlr': mlr, 'two_sided': True, 'loss_rate': l}, B, 0.0, P)
        z = (B * (1 + mlr) - mu) / sigma
        closed = l * mm * ((mu - B) * (1 - norm.cdf(z)) + sigma * norm.pdf(z))
        self.assertAlmostEqual(st['downside_cost'] / closed, 1.0, delta=0.01)
        self.assertAlmostEqual(st['expected_payout'], st['upside_value'] - st['downside_cost'], places=6)

    def test_degenerate_deterministic_cost_and_caps(self):
        sim = CS.Simulation.from_draws(np.full(10, 90.0), mm=1000)
        st, _ = CS._evaluate(sim, {'sharing_rate': 0.5, 'msr': 0.02}, 100.0, 0.0, P)
        self.assertAlmostEqual(st['expected_payout'], 0.5 * 10 * 1000)
        self.assertEqual((st['sd'], st['value_at_risk'], st['cvar'], st['p_net_loss']), (0.0, 0.0, 0.0, 0.0))
        st, _ = CS._evaluate(sim, {'sharing_rate': 0.5, 'msr': 0.02, 'savings_cap_pct': 0.03}, 100.0, 0.0, P)
        self.assertAlmostEqual(st['expected_payout'], 0.03 * 100 * 1000)  # capped
        st, _ = CS._evaluate(sim, {'sharing_rate': 0.5, 'msr': 0.15}, 100.0, 0.0, P)
        self.assertEqual(st['expected_payout'], 0.0)  # inside the MSR corridor: the call is out of the money
        st, _ = CS._evaluate(sim, {'sharing_rate': 0.5, 'msr': 0.02}, 100.0, 7000.0, P)
        self.assertAlmostEqual(st['expected_net'], -2000.0)
        self.assertAlmostEqual(st['risk_adjusted_value'], -2000.0 - 0.5 * 2000.0)

    def test_var_and_cvar(self):
        var, cvar = CS.risk_measures(np.arange(-99, 101, dtype=float), 0.95)  # 200 outcomes, worst 10 are -99..-90
        self.assertAlmostEqual(var, -np.quantile(np.arange(-99, 101, dtype=float), 0.05))
        self.assertAlmostEqual(cvar, -np.mean(np.arange(-99, -89)), delta=0.6)

    def test_greeks_are_finite_differences_on_common_numbers(self):
        cost = np.random.default_rng(2).normal(100, 5, 20000)
        sim = CS.Simulation.from_draws(cost, mm=1000)
        t = {'sharing_rate': 0.5, 'msr': 0.0}
        g = CS.greeks(sim, t, 120.0, 0.0, P)
        self.assertAlmostEqual(g['delta_benchmark_per_usd_pmpm'], 0.5 * 1000, delta=5)  # deep in the money: delta = s x mm
        sim.trend_sd = 0.0
        self.assertAlmostEqual(g['delta_trend_per_point'], -0.5 * 1000 * 100 * 0.01, delta=10)  # d/dtrend of -s mm C(1+t)


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import test_vbc
        test_vbc.VBC.setUpClass()
        cls.path = test_vbc.VBC.path
        cls.profile = source_profile.load(cls.path)
        cls.params = {'focus': 'compare', 'benchmark_pmpm': 11.0, 'period': {'start': '2028-01-01', 'end': '2028-12-31'}, 'draws': 400,
                      'options': [{'label': 'Upside only', 'terms': {'sharing_rate': 0.5, 'msr': 0.02}},
                                  {'label': 'Two-sided', 'terms': {'sharing_rate': 0.7, 'msr': 0.02, 'two_sided': True, 'loss_rate': 0.5}}]}

    def test_simulation_is_reproducible_with_a_seed(self):
        a = CS.execute(self.path, None, self.params, AS_OF)
        b = CS.execute(self.path, None, self.params, AS_OF)
        self.assertEqual(json.dumps(a['options'], sort_keys=True), json.dumps(b['options'], sort_keys=True))
        c = CS.execute(self.path, None, {**self.params, 'seed': 99}, AS_OF)
        self.assertNotEqual([r['expected_net'] for r in a['options']], [r['expected_net'] for r in c['options']])
        self.assertEqual(a['simulation']['members'], 3)
        self.assertAlmostEqual(a['simulation']['base_pmpm'], round(360 / 36, 2), places=1)

    def test_answer_and_view_contract(self):
        plan = analysis_graph.validate({'interpretation': 'q', 'coverage': 'supported_observation', 'unmet_requirements': [],
                                        'nodes': [{'id': 'answer', 'operation': 'contract_strategy', 'inputs': [],
                                                   'parameters': {**self.params, 'focus': 'negotiate', 'care_management_pmpm': 0.2}, 'purpose': 'cs'}]}, AS_OF)
        self.assertEqual(analysis_graph.validate(plan, AS_OF)['nodes'][0]['parameters'], plan['nodes'][0]['parameters'])  # idempotent
        report = CS.execute(self.path, None, plan['nodes'][0]['parameters'], AS_OF)
        answer = analysis_graph.build_answer(plan, [{'node': plan['nodes'][0], 'result': report}], {}, 'q')
        answer['headline'] = analysis_graph.headline(answer)
        answer = json.loads(json.dumps(answer, allow_nan=False, default=str))
        self.assertEqual(answer['status'], 'answered')
        cs = answer['overall']['contract_strategy']
        self.assertEqual(set(cs['distribution']['shares']), {'Upside only', 'Two-sided'})
        self.assertTrue(cs['negotiation']['frontier'] and 'nash' in cs['negotiation'])
        self.assertIn('not this payer\'s offer', cs['negotiation']['typical_range_label'])
        self.assertTrue(all(k in answer['table'][0] for k in ('upside_value', 'downside_cost', 'cvar', 'delta_benchmark_per_usd_pmpm', 'vega_per_10pct_volatility')))
        self.assertEqual(analysis_graph.benchmark_references(answer, 'q', 'contract_strategy'), [])
        page = ask_view.page()
        self.assertIn('function vizContract', page)
        self.assertIn('if(o.contract_strategy)return vizContract(a)', page)


class TrackSelection(unittest.TestCase):
    def setUp(self):
        self.sim = CS.Simulation.from_draws(np.random.default_rng(4).normal(100, 4, 20000), mm=1000)
        self.sim.trend_sd = 0.02
        self.upside = {'sharing_rate': 0.5, 'msr': 0.02}
        self.two_sided = {'sharing_rate': 0.75, 'msr': 0.02, 'two_sided': True, 'loss_rate': 0.6}
        self.B = 103.0

    def rav(self, t, **kw):
        return CS._evaluate(self.sim, t, kw.pop('b', self.B), 0.0, P, **kw)[0]['risk_adjusted_value']

    def test_ranking_flips_at_the_computed_break_even(self):
        be = CS.break_even(self.sim, self.two_sided, self.upside, self.B, 0.0, P)
        b = be['benchmark_pmpm']
        self.assertIsNotNone(b)
        order = lambda bb: self.rav(self.two_sided, b=bb) > self.rav(self.upside, b=bb)
        self.assertNotEqual(order(b - 0.25), order(b + 0.25))
        self.assertTrue(order(b + 0.25))  # richer benchmark favours taking downside for the bigger share
        tr = be['trend_pct']
        self.assertIsNotNone(tr)
        order_t = lambda t: self.rav(self.two_sided, trend=t) > self.rav(self.upside, trend=t)
        self.assertNotEqual(order_t(tr - 0.003), order_t(tr + 0.003))

    def test_capacity_limits_the_loss_rate(self):
        cap = 1500.0
        r = CS.max_loss_rate(self.sim, self.two_sided, 101.0, 0.0, P, cap)
        self.assertTrue(0 < r < 1)
        var = lambda x: CS._evaluate(self.sim, {**self.two_sided, 'loss_rate': x}, 101.0, 0.0, P)[0]['value_at_risk']
        self.assertLessEqual(var(r), cap)
        self.assertGreater(var(min(1.0, r + 0.02)), cap)


class Negotiation(unittest.TestCase):
    def setUp(self):
        self.sim = CS.Simulation.from_draws(np.full(50, 90.0), mm=1000)  # G = (100 - 90) x 1000 = 10,000 certain savings

    def test_nash_solution_on_a_toy_problem(self):
        # U_aco = sG - K, U_payer = (1 - s)G -> argmax (sG - K)(1 - s)G = 1/2 + K/(2G) = 0.6 for K = 2,000
        n = CS.negotiate(self.sim, {}, 100.0, 2000.0, P, {'sharing_rate_min': 0.3, 'sharing_rate_max': 0.8, 'msr_min': 0.0, 'msr_max': 0.0})
        self.assertAlmostEqual(n['nash']['sharing_rate'], 0.6)
        self.assertAlmostEqual(n['nash']['aco_value'], 4000.0)
        self.assertAlmostEqual(n['nash']['payer_value'], 4000.0)
        self.assertAlmostEqual(n['break_even_sharing_rate'], 0.2, places=6)  # sG = K
        self.assertEqual(set(n['bounds_provenance'].values()), {'stated'})
        # payer reservation of 1,000 shifts the solution to (G + K - d_p) / 2G = 0.55
        n = CS.negotiate(self.sim, {}, 100.0, 2000.0, P, {'sharing_rate_min': 0.3, 'sharing_rate_max': 0.8, 'msr_min': 0.0, 'msr_max': 0.0,
                                                           'reservation_value': 1000.0})
        self.assertAlmostEqual(n['nash']['sharing_rate'], 0.55)
        # no agreement zone when spend exceeds the whole surplus
        n = CS.negotiate(self.sim, {}, 100.0, 12000.0, P, {'sharing_rate_max': 0.8, 'msr_max': 0.0})
        self.assertIsNone(n['nash'])
        self.assertFalse(n['agreement_zone'])

    def test_frontier_is_equal_value_and_typical_bounds_are_labelled(self):
        n = CS.negotiate(self.sim, {}, 100.0, 2000.0, P)
        self.assertTrue(all(v == 'typical range default' for v in n['bounds_provenance'].values()))
        self.assertIn("not this payer's offer", n['typical_range_label'])
        self.assertTrue(any('bailit' in s['id'] for s in n['typical_sources']))
        nash_net = n['nash']['aco_expected_net']
        for row in n['frontier']:
            s = row['sharing_rate_for_nash_value']
            if s is None or s > 1: continue
            st, _ = CS._evaluate(self.sim, {'sharing_rate': s, 'msr': row['msr'], 'two_sided': False}, 100.0, 2000.0, P)
            self.assertAlmostEqual(st['expected_net'], nash_net, delta=1.0)


class Inputs(unittest.TestCase):
    def test_benchmark_offers_and_spend_are_never_invented(self):
        with self.assertRaisesRegex(ValueError, 'needs supplied benchmark_pmpm'):
            CS.validate({'options': [{'program': 'MSSP ENHANCED'}]})
        with self.assertRaisesRegex(ValueError, 'needs supplied contract options'):
            CS.validate({'benchmark_pmpm': 1000})
        with self.assertRaisesRegex(ValueError, 'needs supplied care_management_pmpm'):
            CS.validate({'benchmark_pmpm': 1000, 'focus': 'negotiate', 'target': 'break_even'})
        with self.assertRaisesRegex(ValueError, 'needs supplied offer terms'):
            CS.validate({'benchmark_pmpm': 1000, 'options': [{'program': 'commercial_shared_savings_typical'}]})
        with self.assertRaisesRegex(ValueError, 'needs supplied program track'):
            CS.validate({'benchmark_pmpm': 1000, 'options': [{'program': 'MSSP'}]})
        with self.assertRaisesRegex(ValueError, 'participant_revenue'):
            CS.validate({'benchmark_pmpm': 1000, 'options': [{'program': 'MSSP BASIC'}]})
        p = CS.validate({'benchmark_pmpm': 1000, 'options': [{'program': 'MSSP BASIC', 'terms': {'participant_revenue': 1000000}}, {'program': 'ACO REACH'}]})
        self.assertEqual([o['label'] for o in p['options']], ['MSSP BASIC A', 'MSSP BASIC B', 'MSSP BASIC C', 'MSSP BASIC D', 'MSSP BASIC E',
                                                              'ACO REACH Professional', 'ACO REACH Global'])

    def test_router_requires_input_on_missing_benchmark(self):
        choice = {'name': 'contract_strategy', 'input': {'parameters': {'focus': 'compare', 'options': [{'program': 'MSSP BASIC'}, {'program': 'MSSP ENHANCED'}]}}}
        fake = lambda *a, op=None, **k: (({'name': 'contract_strategy', 'input': {}} if op is None else choice), {'api_usd': 0.0})
        with mock.patch.object(router, '_ask', side_effect=fake):
            graph, info = router.route('should we go enhanced', AS_OF, None, None, analysis_graph.validate)
        self.assertIsNone(graph)
        self.assertIn('benchmark_pmpm', info['requires_input'])
        choice['input']['parameters']['benchmark_pmpm'] = 1000
        choice['input']['parameters']['options'][0]['terms'] = {'participant_revenue': 1000000}
        with mock.patch.object(router, '_ask', side_effect=fake):
            graph, info = router.route('should we go enhanced at 1000', AS_OF, None, None, analysis_graph.validate)
        self.assertEqual(graph['nodes'][-1]['operation'], 'contract_strategy')
        self.assertEqual(len(graph['nodes'][-1]['parameters']['options']), 6)


class Routing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import test_vbc
        test_vbc.VBC.setUpClass()
        cls.profile = source_profile.load(test_vbc.VBC.path)

    def plan(self, q):
        return local_compiler.compile(q, self.profile, AS_OF)

    def test_registry_integration(self):
        self.assertIn('contract_strategy', PRODUCT)
        self.assertIn('contract_strategy', analysis_graph.OPERATIONS)
        self.assertIn('contract_strategy', CONTRACTS)
        self.assertIn('contract_strategy', graph_contracts.TYPED)
        self.assertIn('contract_strategy', analysis_graph.NO_BENCHMARK_OPERATIONS)
        self.assertLess(len(json.dumps(router.tool('contract_strategy'))), 20000)

    def test_strategy_phrasings_route_here(self):
        phrasings = {
            'Should we move from MSSP BASIC to ENHANCED with a $120 PMPM benchmark?': ('compare', 6),
            'Which ACO REACH option and risk corridor is best for us at a $118 benchmark?': ('compare', 2),
            'What is our upside/downside contract worth under MSSP BASIC level E with a benchmark of $120 PMPM?': ('value', 1),
            'What sharing rate should we negotiate to break even with $2 PMPM care-management spend and a $125 PMPM benchmark?': ('negotiate', 0),
            'How much downside risk can we take at 95% confidence under ENHANCED with a $120 benchmark?': ('capacity', 1),
            'Compare these two contract offers with a $122 PMPM benchmark: offer A 50% sharing with a 2% MSR upside only; '
            'offer B 60% sharing two-sided with a 40% loss rate': ('compare', 2),
            "Negotiate our commercial deal: benchmark $125 PMPM, the payer won't go above 60% sharing": ('negotiate', 0),
            'Is MSSP ENHANCED or BASIC level E better for us with a $119 PMPM benchmark?': ('compare', 2),
            'MSSP BASIC level E vs ENHANCED for us with a $120 benchmark': ('compare', 2),
            'Should we take the offer at 55% sharing with a 3% MSR and a $120 benchmark?': ('value', 1),
        }
        for q, (focus, n) in phrasings.items():
            # BASIC downside tracks need the revenue base for their loss caps.
            if 'BASIC' in q: q = q.rstrip('?') + ' with participant revenue of $1,000,000?'
            g, info = self.plan(q)
            self.assertIsNotNone(g, (q, info))
            self.assertEqual([x['operation'] for x in g['nodes']], ['contract_strategy'], q)
            p = analysis_graph.validate(g, AS_OF)['nodes'][-1]['parameters']
            self.assertEqual((p['focus'], len(p['options'])), (focus, n), q)
        g, _ = self.plan("Negotiate our commercial deal: benchmark $125 PMPM, the payer won't go above 60% sharing")
        self.assertEqual(g['nodes'][0]['parameters']['counterparty'], {'sharing_rate_max': 0.6})  # the payer's bound, not our offer
        g, _ = self.plan('Compare these two contract offers with a $122 PMPM benchmark: offer A 50% sharing with a 2% MSR upside only; '
                         'offer B 60% sharing two-sided with a 40% loss rate')
        a, b = (o['terms'] for o in g['nodes'][0]['parameters']['options'])
        self.assertEqual((a, b), ({'sharing_rate': 0.5, 'msr': 0.02, 'two_sided': False}, {'sharing_rate': 0.6, 'loss_rate': 0.4, 'two_sided': True}))

    def test_acceptance_phrasings(self):
        cases = json.loads((ROOT / 'bench' / 'acceptance' / 'contract_strategy.json').read_text())
        self.assertEqual(len(cases), 8)
        for case in cases:
            g, info = self.plan(case['question'])
            self.assertIsNotNone(g, (case['question'], info))
            if 'BASIC' in case['question']:
                self.assertEqual(g['coverage'], 'unsupported')
                self.assertIn('participant_revenue', ' '.join(g['unmet_requirements']))
                continue
            self.assertEqual([n['operation'] for n in g['nodes']], [case['expect_operation']], case['question'])
            analysis_graph.validate(g, AS_OF)

    def test_missing_inputs_are_named_not_assumed(self):
        g, info = self.plan('Should we move from MSSP BASIC to ENHANCED?')
        self.assertEqual((g['coverage'], info['operation']), ('unsupported', 'contract_strategy'))
        self.assertEqual(len(g['unmet_requirements']), 1)
        self.assertIn('benchmark_pmpm', g['unmet_requirements'][0])
        g, _ = self.plan('What sharing rate should we negotiate to break even with expected care-management spend?')
        self.assertEqual([m.split(' (')[0] for m in g['unmet_requirements']], ['Missing input: benchmark_pmpm', 'Missing input: care_management_pmpm'])
        g, _ = self.plan('Compare these two contract offers')
        self.assertTrue(any('contract options' in m for m in g['unmet_requirements']))

    def test_settlement_and_explanation_questions_are_not_stolen(self):
        vbc_questions = ['What would we earn under MSSP ENHANCED with a $1,000 benchmark?',
                         'Under MSSP BASIC level E with a $950 PMPM benchmark, what is our shared savings?',
                         'What would our payment be under MSSP Level A with a $900 benchmark?',
                         'How much would we owe or earn in the MSSP basic track level C with a $1,000 benchmark?',
                         'What is our settlement under ACO REACH Global 2026 with a benchmark of $1,100 PMPM?',
                         'What would our shared savings be with a benchmark of $90 PMPM and a sharing rate of 50%?']
        for q in vbc_questions:
            if 'level E' in q or 'level C' in q:
                q += ' with participant revenue of $1,000,000'
            g, _ = self.plan(q)
            self.assertEqual([n['operation'] for n in g['nodes']], ['vbc'], q)
        for q in ('What would we earn under MSSP ENHANCED?', 'What is our value at risk on the shared savings contract?',
                  'What is our minimum loss rate under our current track, and how much loss exposure do we carry?'):
            self.assertIsNone(local_compiler._contract_strategy_question(q, self.profile, AS_OF), q)
        for q in ('What is the difference between MSSP BASIC and ENHANCED?', 'How does the MSSP ENHANCED track work?',
                  'Explain prospective versus retrospective assignment'):
            g, _ = self.plan(q)
            self.assertEqual([n['operation'] for n in g['nodes']], ['knowledge'], q)
        for q in ('Which patients should we call first under our contract?', 'What is our PMPM by payer?', 'Which practices are worth a closer look?'):
            self.assertIsNone(local_compiler._contract_strategy_question(q, self.profile, AS_OF), q)


if __name__ == '__main__':
    unittest.main()
