"""Evolutionary policy search (clinical_core/agere/evolve): the sandbox rejects imports / I/O / timeouts, the evaluator is
reproducible on the simulator fixture, the archive keeps the best, a planted better policy is found from a fixed candidate
list without model calls, and the router config switch (off) leaves routing unchanged. Synthetic fixtures only."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import test_plans
import test_simulator
from clinical_core.agere.evolve import archive as archmod, baselines, evaluator as ev, loop, routing, sandbox
from clinical_core.agere.evolve.proposer import ListProposer, extract_code
from clinical_core.agere.plans import router
from clinical_core.agere.simulator import engine

PROGRAMS = [{'id': 'prog_a', 'name': 'A', 'role': 'standard', 'hours': 3.0}, {'id': 'prog_b', 'name': 'B', 'role': 'barrier_review', 'hours': 1.5}]
ROWS = [{'x': float(i), 'routed_program': 'prog_a'} for i in range(5)]
GOOD = 'import math\n\ndef prioritize(p):\n    return math.log1p(p["x"])\n'


class SandboxTests(unittest.TestCase):
    def test_valid_policy_runs(self):
        scores, choices = sandbox.run(GOOD, ROWS, PROGRAMS)
        self.assertEqual(len(scores), 5)
        self.assertAlmostEqual(scores[4], np.log1p(4.0))
        self.assertEqual(choices, ['prog_a'] * 5)

    def test_choose_program_and_skip(self):
        code = ('def prioritize(p):\n    return None if p["x"] < 2 else p["x"]\n\n'
                'def choose_program(p, programs):\n    return "prog_b" if p["x"] > 3 else programs[0]["id"]\n')
        scores, choices = sandbox.run(code, ROWS, PROGRAMS)
        self.assertEqual(scores[:2], [None, None])
        self.assertEqual(choices[4], 'prog_b')

    def test_rejects_imports_io_and_escapes(self):
        bad = {
            'import os\ndef prioritize(p):\n    return 1\n': 'import',
            'from subprocess import run\ndef prioritize(p):\n    return 1\n': 'import',
            'def prioritize(p):\n    open("/tmp/x", "w").write("y")\n    return 1\n': 'open',
            'def prioritize(p):\n    print(p)\n    return 1\n': 'print',
            'def prioritize(p):\n    return ().__class__.__bases__[0]\n': 'attribute',
            'def prioritize(p):\n    return __import__("os").getpid()\n': '__import__',
            'def prioritize(p):\n    return eval("1")\n': 'eval',
            'def prioritize(p):\n    return getattr(p, "keys")\n': 'getattr',
            'def prioritize(p):\n    return "{0.__class__}".format(p)\n': 'format',
            'class A:\n    pass\ndef prioritize(p):\n    return 1\n': 'ClassDef',
            'def score(p):\n    return 1\n': 'prioritize',
        }
        for code, why in bad.items():
            with self.subTest(why=why):
                with self.assertRaises(sandbox.SandboxError) as cm:
                    sandbox.run(code, ROWS, PROGRAMS)
                self.assertIn(why.lower(), str(cm.exception).lower())

    def test_runtime_import_of_other_modules_fails(self):
        # Even if static checks were bypassed, the worker's importer only knows math and statistics.
        from clinical_core.agere.evolve import sandbox_worker as w
        with self.assertRaises(ImportError):
            w._importer('os')

    def test_timeout_and_bad_outputs(self):
        with self.assertRaises(sandbox.SandboxError) as cm:
            sandbox.run('def prioritize(p):\n    while True:\n        pass\n', ROWS, PROGRAMS, timeout_s=1.5)
        self.assertIn('timeout', str(cm.exception))
        with self.assertRaises(sandbox.SandboxError):
            sandbox.run('def prioritize(p):\n    return "high"\n', ROWS, PROGRAMS)
        with self.assertRaises(sandbox.SandboxError):
            sandbox.run('def prioritize(p):\n    return 1\n\ndef choose_program(p, programs):\n    return "other"\n', ROWS, PROGRAMS)
        with self.assertRaises(sandbox.SandboxError):
            sandbox.run('def prioritize(p):\n    return 1 / 0\n', ROWS, PROGRAMS)

    def test_extract_code(self):
        text = 'Idea one\n```python\ndef prioritize(p):\n    return 1\n```\nand\n```python\nx = 1\n```'
        self.assertEqual(extract_code(text), ['def prioritize(p):\n    return 1\n'])


def synthetic_dataset(n=120, seeds=(1, 2, 3, 4), N=60, cap=10, seed=0):
    """A Dataset with fabricated simulator columns: value is driven by a hidden mix of two features; wave delay shrinks savings."""
    rng = np.random.default_rng(seed)
    rows, pids = [], []
    for i in range(n):
        risk = float(rng.gamma(2.0, 0.01))
        miss = int(rng.integers(1, 5))
        rows.append({'monthly_admission_rate': round(risk, 5), 'monthly_ed_rate': round(2 * risk, 5), 'missing_count': miss, 'noise': float(rng.random()),
                     'line_of_business': ['commercial', 'medicare', 'medicaid'][i % 3], 'age_band': ['<65', '65-74', '75+'][i % 3],
                     'routed_program': 'prog_a', 'task_priority': 1 + miss // 2, 'router_rank': i / (n - 1)})
        pids.append(f'S{i:03d}')
    task = ev.Task(capacity_per_quarter=cap, iterations=N, equity_min_group=5, heldout_fraction=0.3)
    cols = {}
    for s in seeds:
        r = np.random.default_rng(1000 + s)
        for v, mult in (('prog_a', 1.0), ('prog_b', 0.6)):
            for q in range(task.n_waves):
                base = np.array([row['monthly_admission_rate'] * 4e5 * (0.25 + 0.25 * row['missing_count']) for row in rows]) * mult
                saved = np.clip(base[None, :] * (1 - 0.25 * q) + r.normal(0, 300, (N, n)), 0, None)
                cost = np.full((N, n), 362.0 * (0.7 if v == 'prog_b' else 1.0))
                adm = saved / 15000.0
                close = np.tile(np.array([0.3 + 0.1 * row['missing_count'] for row in rows]) * mult, (N, 1))
                cols[(s, v, q)] = {'saved': saved.astype(np.float32), 'program_cost': cost.astype(np.float32),
                                   'pharmacy_cost': np.zeros((N, n), np.float32), 'admissions_avoided': adm.astype(np.float32),
                                   'ed_visits_avoided': np.zeros((N, n), np.float32), 'new_therapy': close.astype(np.float32),
                                   'pillars_started': close.astype(np.float32)}
    return ev.Dataset(task, pids, rows, PROGRAMS, cols, seeds, 'synthetic', '2030-05-27')


PLANTED = '''def prioritize(p):
    v = p['monthly_admission_rate'] * 4e5 * (0.25 + 0.25 * p['missing_count'])
    return v if v > 362.0 else None
'''
DECOYS = [
    'def prioritize(p):\n    return p["noise"]\n',
    'def prioritize(p):\n    return -p["monthly_admission_rate"]\n',
    'import os\ndef prioritize(p):\n    return 1\n',
    'def prioritize(p):\n    return p["missing_count"]\n',
]


class ArchiveTests(unittest.TestCase):
    def entry(self, eid, f, beh=(0.5, 0.5)):
        return {'id': eid, 'code': f'# {eid}\ndef prioritize(p):\n    return {f}\n', 'status': 'valid', 'behavior': beh,
                'fitness': {o: float(f) for o in ev.OBJECTIVES}}

    def test_keeps_best_per_niche_and_overall(self):
        a = archmod.Archive(ev.OBJECTIVES)
        a.add(self.entry('a', 1))
        self.assertTrue(a.add(self.entry('b', 5)))
        self.assertEqual(a.add(self.entry('c', 3)), [])          # same niche, worse: not kept as elite
        a.add(self.entry('d', 2, (0.05, 0.95)))                    # other niche: kept for diversity
        self.assertEqual(a.best('net_savings')[0]['id'], 'b')
        self.assertEqual({e['id'] for e in a.elites()}, {'b', 'd'})
        self.assertEqual(a.add({**self.entry('e', 9), 'code': self.entry('b', 5)['code']}), [])   # duplicate code ignored
        self.assertEqual(a.summary()['niches_filled'], 2)

    def test_infeasible_ranks_below_feasible(self):
        bad = {'passed': False, 'worst_shortfall': 0.1}
        good = {'passed': True, 'worst_shortfall': 0.0}
        self.assertLess(ev.fitness({'net_savings': 1e9}, bad, 'net_savings'), ev.fitness({'net_savings': -1e6}, good, 'net_savings'))


class PlantedPolicyTests(unittest.TestCase):
    def test_planted_better_policy_is_found_without_model_calls(self):
        ds = synthetic_dataset()
        prop = ListProposer(DECOYS[:2] + [PLANTED] + DECOYS[2:], per_call=2)
        logs = []
        with mock.patch('clinical_core.agere.clinical.context_student.send', side_effect=AssertionError('no model calls')):
            arch = loop.evolve(ds, prop, train_seeds=[1, 2], generations=3, objectives=['net_savings'],
                               baselines={'router_order': baselines.ROUTER_ORDER, 'random': baselines.RANDOM}, log=logs.append)
        best = arch.best('net_savings')[0]
        self.assertEqual(best['code'].strip(), PLANTED.strip())
        self.assertGreater(best['fitness']['net_savings'], arch.by_hash[archmod.Archive.code_hash(baselines.ROUTER_ORDER)]['fitness']['net_savings'])
        self.assertEqual(sum(1 for e in arch.entries if e['status'] == 'invalid'), 1)
        rep = loop.heldout_report(ds, arch, {'router_order': baselines.ROUTER_ORDER}, [1, 2], [3, 4], objectives=['net_savings'], log=logs.append)
        d = rep['policies']['best:net_savings']['vs_baselines']['router_order']['heldout']['net_savings']
        self.assertGreater(d['mean'], 0)
        self.assertIn('rule_summary', rep['champions']['net_savings'])

    def test_capacity_and_waves(self):
        ds = synthetic_dataset(cap=10)
        E = ev.Evaluator(ds)
        res = E.evaluate(baselines.ROUTER_ORDER, 'train')
        cap, _ = ev.capacity_for(ds.task, len(ds.splits['train']), len(ds.pids))
        self.assertTrue(all(c <= cap for c in res['by_wave']))
        self.assertEqual(res['contacted'], min(len(ds.splits['train']), cap * ds.task.n_waves))
        a = res['_assignment']
        self.assertTrue(set(i for i, _, _ in a) <= set(ds.splits['train'].tolist()))

    def test_knapsack_baseline_matches_outreach_knapsack_for_count_capacity(self):
        ds = synthetic_dataset()
        scores, choices = sandbox.run(baselines.outreach_code(), ds.rows, ds.programs)
        idx = list(ds.splits['train'])
        k = 15
        order = baselines.knapsack_order([scores[i] for i in idx], [1] * len(idx), k)
        mine = sorted(idx, key=lambda i: (-scores[i], i))[:k]
        self.assertEqual(sorted(idx[j] for j in order), sorted(mine))

    def test_equity_floor(self):
        ds = synthetic_dataset()
        only_commercial = 'def prioritize(p):\n    return 1.0 if p["line_of_business"] == "commercial" else None\n'
        res = ev.Evaluator(ds).evaluate(only_commercial, 'train')
        self.assertFalse(res['equity']['passed'])
        self.assertLess(res['fitness']['net_savings'], -1e11)


class SimulatorFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / 'semantic.sqlite3'
        test_simulator.build(cls.db)
        cls.task = ev.Task(as_of=test_plans.AS_OF, capacity_per_quarter=6, iterations=60, funnel_scenarios=10, equity_min_group=3)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_evaluator_reproducible(self):
        a = ev.Dataset.build(self.db, self.task, [5], log=None)
        b = ev.Dataset.build(self.db, self.task, [5], log=None)
        self.assertNotIn('HFX00', json.dumps(a.rows))           # features never carry patient ids
        ra = ev.Evaluator(a).evaluate(baselines.HIGHEST_RISK, 'all', draws=True)
        rb = ev.Evaluator(b).evaluate(baselines.HIGHEST_RISK, 'all', draws=True)
        self.assertEqual(ra['metrics'], rb['metrics'])
        self.assertEqual(ra['intervals'], rb['intervals'])
        self.assertGreater(ra['contacted'], 0)

    def test_draws_decompose_engine_totals_and_offsets_delay_effect(self):
        arms = [{'id': 'v', 'force_program': 'pharmacy_gdmt_titration'}]
        kw = dict(program_kind='pharmacy_gdmt', as_of=test_plans.AS_OF, iterations=80, funnel_scenarios=10, seed=3, alternatives=arms)
        plain = engine.simulate(self.db, **kw)
        out = engine.simulate(self.db, return_draws=True, **kw)
        self.assertEqual(plain['summary_table'], out['summary_table'])  # the hook changes nothing unless offsets are given
        row = next(r for r in out['summary_table'] if r['arm'] == 'v')
        d = out['draws']['arms']['v']
        self.assertAlmostEqual(float(d['admissions_avoided'].sum(1).mean()), row['admissions_avoided']['mean'], places=1)
        self.assertAlmostEqual(float(d['program_cost'].sum(1).mean()), row['program_cost']['mean'], delta=1.0)
        late = engine.simulate(self.db, start_offset_days=200, **kw)
        r2 = next(r for r in late['summary_table'] if r['arm'] == 'v')
        self.assertLess(r2['admissions_avoided']['mean'], row['admissions_avoided']['mean'])
        self.assertNotEqual(late['id'], plain['id'])

    def test_config_switch_off_leaves_routing_unchanged(self):
        self.assertFalse(json.loads(routing.CONFIG.read_text())['enabled'])
        ids = router.candidates(self.db, 'pharmacy_gdmt', test_plans.AS_OF)
        default = router.route(self.db, ids, 'pharmacy_gdmt', test_plans.AS_OF)
        with mock.patch.object(router, '_outreach_policy', lambda plan, *a, **k: plan):
            bypass = router.route(self.db, ids, 'pharmacy_gdmt', test_plans.AS_OF)
        self.assertEqual(json.dumps(default, sort_keys=True, default=str), json.dumps(bypass, sort_keys=True, default=str))
        self.assertNotIn('outreach_policy', default)
        # enabled switch but an unapproved policy: still unchanged
        with tempfile.TemporaryDirectory() as d:
            pol = Path(d) / 'p.json'
            pol.write_text(json.dumps({'program_kind': 'pharmacy_gdmt', 'code': GOOD, 'approved': False}))
            cfg = Path(d) / 'cfg.json'
            cfg.write_text(json.dumps({'enabled': True, 'policies': {'pharmacy_gdmt': str(pol)}}))
            with mock.patch.object(routing, 'CONFIG', cfg):
                again = router.route(self.db, ids, 'pharmacy_gdmt', test_plans.AS_OF)
        self.assertEqual(router.digest(again), router.digest(default))
        with self.assertRaises(ValueError):
            routing.apply(default, self.db, {'program_kind': 'pharmacy_gdmt', 'code': GOOD, 'approved': False})

    def test_approved_policy_ranks_and_waitlists(self):
        ids = router.candidates(self.db, 'pharmacy_gdmt', test_plans.AS_OF)
        pol = {'id': 'test:policy', 'program_kind': 'pharmacy_gdmt', 'approved': True, 'code': baselines.HIGHEST_RISK,
               'task': {'capacity_per_quarter': 3, 'wave_days': 91, 'horizon_months': 6}}
        plan = router.route(self.db, ids, 'pharmacy_gdmt', test_plans.AS_OF, outreach_policy=pol)
        sel = [r for r in plan['patients'] if (r.get('outreach_policy') or {}).get('selected')]
        self.assertEqual(len(sel), 6)                              # 3 per quarter x 2 quarters
        self.assertEqual([r['outreach_policy']['rank'] for r in sel], list(range(1, 7)))
        self.assertEqual({r['outreach_policy']['start_offset_days'] for r in sel}, {0, 91})
        self.assertGreater(plan['summary']['by_route'].get('capacity_waitlist', 0), 0)
        out = engine.simulate(self.db, plan, iterations=60, funnel_scenarios=10, seed=2, alternatives=['plan'])
        self.assertEqual(out['cohort']['patients'], 6)
        self.assertEqual(out['start_offset_days']['patients_delayed'], 3)


if __name__ == '__main__':
    unittest.main()
