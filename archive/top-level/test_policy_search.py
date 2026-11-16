"""policy_search: model-free evolutionary policy search over simulator columns (tiny synthetic Dataset), routes, answer."""
import unittest

import numpy as np

from clinical_core.agere.clinical import engine_suite, policy_search as P
from clinical_core.agere.evolve import evaluator as ev, sandbox

AS_OF = '2030-05-27'


def tiny_dataset(n=60, seeds=(101, 201), iters=50):
    """Synthetic simulator columns: net value rises with admission rate; costs flat; gap closures with missing pillars."""
    rng = np.random.default_rng(0)
    task = ev.Task(capacity_per_quarter=10, iterations=iters, horizon_months=6, wave_days=91, equity_min_group=1000)
    rows, pids = [], []
    for i in range(n):
        rate = float(rng.uniform(0.0, 0.1))
        rows.append({'monthly_admission_rate': rate, 'monthly_ed_rate': 0.05, 'expected_monthly_cost': 500 + 4000 * rate, 'missing_count': int(i % 4) + 1,
                     'barrier_count': int(i % 5 == 0), 'task_priority': 1 + i % 3, 'router_rank': i / n, 'hf_admission_6m': rate > 0.08,
                     'hf_admissions_12m': int(rate > 0.08), 'ed_visits_12m': 1, 'interpreter_needed': False, 'medicaid': i % 7 == 0,
                     'age_80_plus': False, 'line_of_business': 'medicare', 'age_band': '75+', 'routed_program': 'pharmacy_gdmt_titration'})
        pids.append(f'P-{i:03d}')
    programs = [{'id': 'pharmacy_gdmt_titration', 'role': 'standard', 'hours': 1.0}, {'id': 'pharmd_barrier_review', 'role': 'barrier', 'hours': 1.0}]
    cols = {}
    for s in seeds:
        r2 = np.random.default_rng(s)
        for p in programs:
            for q in range(task.n_waves):
                saved = np.array([[20000 * r['monthly_admission_rate'] * (1 - 0.3 * q) for r in rows]] * iters) + r2.normal(0, 50, (iters, n))
                cols[(s, p['id'], q)] = {'saved': saved.astype(np.float32), 'program_cost': np.full((iters, n), 900, np.float32),
                                         'pharmacy_cost': np.zeros((iters, n), np.float32),
                                         'admissions_avoided': (saved / 15000).astype(np.float32), 'ed_visits_avoided': np.zeros((iters, n), np.float32),
                                         'new_therapy': np.array([[min(1.0, r['missing_count'] / 4)] * 1 for r in rows]).T.repeat(iters, 0).astype(np.float32),
                                         'pillars_started': np.zeros((iters, n), np.float32)}
    return ev.Dataset(task, pids, rows, programs, cols, list(seeds), 'digest', AS_OF)


class Genome(unittest.TestCase):
    def test_code_is_valid_sandbox_policy(self):
        ds = tiny_dataset()
        sc = P.feature_scales(ds.rows)
        code = P.code_for({'w': {'monthly_admission_rate': 1.0, 'barrier_count': -0.5}, 'threshold': -0.2, 'barrier_alt': True}, sc, 'pharmd_barrier_review')
        sandbox.validate(code)
        scores, choices = sandbox.run(code, ds.rows, ds.programs)
        self.assertIn(None, scores)                                     # threshold skips low scorers
        self.assertIn('pharmd_barrier_review', choices)

    def test_proposer_is_deterministic_and_evolves(self):
        ds = tiny_dataset()
        mk = lambda: P.GenomeProposer(P.feature_scales(ds.rows), per_call=3, seed=3)  # noqa: E731
        a, b = mk(), mk()
        ctx = {'generation': 2, 'objective': 'net_savings', 'parents': [], 'inspiration': []}
        self.assertEqual([c['code'] for c in a.propose(ctx)], [c['code'] for c in b.propose(ctx)])
        first = a.propose({**ctx, 'generation': 1})
        child = a.propose({**ctx, 'generation': 3, 'parents': [{'code': first[0]['code']}]})
        self.assertTrue(child and all(c['code'] not in [f['code'] for f in first] for c in child))


class Search(unittest.TestCase):
    def test_search_beats_or_matches_random_on_heldout(self):
        ds = tiny_dataset()
        p = P.validate({'generations': 2, 'population': 3})
        res = P.search(ds, p)
        pol = res['report']['policies']
        self.assertIn('best:net_savings', pol)
        self.assertGreaterEqual(pol['best:net_savings']['train']['metrics']['net_savings'], pol['baseline:random']['train']['metrics']['net_savings'])
        self.assertTrue(res['worklist'] and all(r['wave'] >= 1 for r in res['worklist']))
        self.assertGreater(res['candidates_evaluated'], 4)

    def test_validate(self):
        self.assertEqual(P.validate({})['objective'], 'net_savings')
        for bad in ({'objective': 'x'}, {'iterations': 10}, {'generations': 50}, {'zzz': 1}):
            with self.assertRaises(ValueError): P.validate(bad)

    def test_answer_says_simulated(self):
        report = {'objective': 'net_savings', 'parameters': P.validate({}), 'routed_patients': 60,
                  'champion': {'id': 'G01:net_savings:0', 'origin': 'genome', 'rule_summary': 'r', 'explanation': 'e', 'code': 'x'},
                  'search': {'candidates_evaluated': 9}, 'vs_baselines': {'random': {'mean': 10.0, 'p05': -1.0, 'p95': 20.0}},
                  'table': [{'policy': 'best:net_savings', 'heldout_net_savings_usd': 100.0, 'net_savings_p05': 1.0, 'net_savings_p95': 200.0,
                             'admissions_avoided': 0.5, 'gap_closures': 3.0, 'equity_passed': True}],
                  'patient_ids': ['P-1'], 'worklist': [], 'method': 'm', 'assumptions': P.ASSUMPTIONS}
        a = P.answer(report, {'coverage': 'supported_observation'}, 'q')
        self.assertTrue(a['value'].startswith('SIMULATED'))
        self.assertIn('SIMULATED', a['limitations'][0])


class Route(unittest.TestCase):
    def test_positive(self):
        g, _ = engine_suite.compile_route('Use evolutionary search to find an outreach policy that maximizes simulated admissions avoided with 25 patients per quarter', {}, AS_OF)
        n = g['nodes'][0]
        self.assertEqual(n['operation'], 'policy_search')
        self.assertEqual(n['parameters'], {'objective': 'admissions_avoided', 'capacity_per_quarter': 25})
        P.validate(n['parameters'])

    def test_negative(self):
        for q in ('Who should we call this week for outreach?', 'Who benefits most from care management?',
                  'What is an evolutionary algorithm?', 'How much should each clinic overbook?',
                  'What is our outreach policy?'):
            self.assertIsNone(P.route(q, {}, AS_OF), q)


if __name__ == '__main__':
    unittest.main()
