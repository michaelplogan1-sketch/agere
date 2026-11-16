"""Latency budgets: interactive-time answers, enforced per route class and operator.

Fixture part always runs (small store; catches pathological regressions such as per-record hashing).
Real-stack part runs only when AGERE_LATENCY_STACK names a stack directory (an APFS clone of the served
stack, e.g. `cp -cR artifacts/<build>/stack <scratch>/stack`); it is skipped otherwise and refuses the
active served stack. It executes with AGERE_RESULT_CACHE=off (computes everything, writes no result cache).

Timing is graph execution wall time (planning by the local compiler is timed separately). Each question runs
once cold and then warm; budgets apply to the warm pass (the long-lived service state) and a looser cold
ceiling applies to the first pass. `record_store.build` must have been run on the stack (it is part of the
semantic-layer build step) or the population scans fall back to the slow path and the budgets fail.
"""
import json, os, statistics, sys, time, unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent

# One place for every budget (seconds). p95 over the fixed question set, per class / operator.
BUDGETS = {
    'local_simple_p95': 1.0,       # local-compiler counts/aggregates (cohort, aggregate, sql, set ops)
    'analytic_p95': 5.0,           # every analytic operator graph
    'operator_p95': {              # per-operator node time, warm
        'cohort': 0.5, 'aggregate': 1.0, 'explain': 2.0, 'discover': 4.0, 'monitor': 5.0, 'market': 4.0,
        'claims_review': 5.0, 'vbc': 3.0, 'outreach': 5.0, 'calibration': 5.0, 'early_signal': 5.0,
    },
    'graph_setup_p95': 0.25,       # validation, catalog and identities before the first node
    'cold_max': 20.0,              # first execution of any question in a fresh process
    'fixture_p95': 0.5,            # tiny fixture store: cohort/aggregate/explain
}
SIMPLE_OPS = {'cohort', 'aggregate', 'sql', 'union', 'intersect', 'difference'}
SIMPLE_QUESTIONS = ['How many patients have diabetes?', 'How many patients have heart failure?', 'How many ED visits by practice?',
                    'Average latest A1c by practice', 'How many patients are on Medicaid?', 'Count of patients with hypertension by payer',
                    'How many patients had an inpatient admission in 2028?', 'How many patients with diabetes have an A1c over 9?',
                    'How many patients over 65 have COPD?']
ALL = {'basis': 'all_observed', 'start': '', 'end': ''}


def _graph(spec, op, params):
    return {'interpretation': 'latency budget', 'coverage': 'supported_observation', 'unmet_requirements': [],
            'nodes': [{'id': 'population', 'operation': 'cohort', 'inputs': [], 'parameters': {'specification': spec}, 'purpose': 'p'},
                      {'id': 'answer', 'operation': op, 'inputs': ['population'], 'parameters': params, 'purpose': 'a'}]}


EVERYONE = {'all': [], 'any': [], 'exclude': []}
ROUTED_GRAPHS = {  # analytic graphs the operator router produces (no hosted call needed to replay them)
    'explain readmission': _graph(EVERYONE, 'explain', {'outcome': {'type': 'readmission'}, 'window': {'start': '', 'end': ''}}),
    'explain ED among Medicaid': _graph({'all': [{'kind': 'patient', 'field': 'coverage', 'op': 'eq', 'value': 'Medicaid'}], 'any': [], 'exclude': []},
                                        'explain', {'outcome': {'type': 'concept', 'concept': 'emergency_visit', 'aggregate': 'any'}, 'window': {'start': '', 'end': ''}}),
    'explain uncontrolled A1c': _graph({'all': [{'kind': 'concept', 'concept': 'diabetes', 'window': ALL}], 'any': [], 'exclude': []}, 'explain',
                                       {'outcome': {'type': 'measurement', 'concept': 'hba1c', 'op': 'gt', 'value': 9, 'aggregate': 'latest'}, 'window': {'start': '', 'end': ''}}),
    'discover admissions practice x payer': _graph(EVERYONE, 'discover', {'outcome': {'all': [{'kind': 'concept', 'concept': 'inpatient_admission', 'window': ALL}], 'any': [], 'exclude': []},
                                                                         'segments': ['practice', 'coverage_type'], 'pairs': True, 'direction': 'high'}),
}


def pct(values, q):
    v = sorted(values)
    k = (len(v) - 1) * q; lo = int(k); hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


class FixtureBudget(unittest.TestCase):
    def test_population_operators_fast_on_fixture(self):
        from clinical_core.agere.clinical import aggregate, cohorts, explain, record_store
        from test_record_store import fixture
        path, ids = fixture()
        record_store.build(path)
        spec = {'all': [{'kind': 'concept', 'concept': 'diabetes', 'window': ALL}], 'any': [], 'exclude': []}
        times = []
        with patch.dict(os.environ, {'AGERE_RESULT_CACHE': 'off'}):
            for _ in range(5):
                for fn in (lambda: cohorts.select(path, spec),
                           lambda: aggregate.execute(path, ids, {'measure': 'events', 'concept': 'emergency_visit', 'group_by': 'pcp'}),
                           lambda: explain.execute(path, ids, {'outcome': {'type': 'readmission'}})):
                    t = time.perf_counter(); fn(); times.append(time.perf_counter() - t)
        self.assertLess(pct(times, .95), BUDGETS['fixture_p95'])


def _stack_path():
    raw = os.environ.get('AGERE_LATENCY_STACK')
    if not raw: return None
    path = Path(raw).resolve()
    try:
        active = Path(json.loads((ROOT / 'config/agere/active_warehouse.json').read_text())['build']).resolve() / 'stack'
    except (OSError, KeyError, ValueError):
        active = None
    if active is not None and path == active.resolve():
        raise unittest.SkipTest('Refusing the active served stack; point AGERE_LATENCY_STACK at a clone')
    return path if (path / 'stack.sqlite3').exists() else None


@unittest.skipUnless(os.environ.get('AGERE_LATENCY_STACK'), 'AGERE_LATENCY_STACK not set (clone of a served stack)')
class RealStackBudget(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = _stack_path()
        if path is None: raise unittest.SkipTest('No stack at AGERE_LATENCY_STACK')
        cls.env = patch.dict(os.environ, {'AGERE_RESULT_CACHE': 'off'}); cls.env.start()
        from clinical_core.agere.clinical.stack_runtime import Stack
        from clinical_core.agere.clinical import economical_planner, local_compiler, record_store, source_profile
        if record_store.token(path) is None: raise unittest.SkipTest('record_store not built or stale on this stack')
        cls.stack = Stack(path, rerank=False)
        as_of = cls.stack.retriever.meta['snapshot']['as_of']
        profile = source_profile.load(path)
        cls.cases = []  # (label, class, graph)
        for q in SIMPLE_QUESTIONS:
            g, _ = local_compiler.compile(q, profile, as_of)
            if g: cls.cases.append((q, 'local_simple', economical_planner.validate(g, as_of)))
        for f in sorted((ROOT / 'bench/acceptance').glob('*.json')):  # first two locally compiled questions per capability
            taken = 0
            for c in json.loads(f.read_text()):
                if taken == 2: break
                g, _ = economical_planner.local_plan(c['question'], as_of)
                if g is None:
                    g, _ = local_compiler.compile(c['question'], profile, as_of)
                    g = economical_planner.validate(g, as_of) if g else None
                if g:
                    ops = {n['operation'] for n in g['nodes']}
                    cls.cases.append((c['question'], 'local_simple' if ops <= SIMPLE_OPS else 'analytic', g)); taken += 1
        cls.cases += [(k, 'analytic', economical_planner.validate(g, as_of)) for k, g in ROUTED_GRAPHS.items()]
        cls.cold, cls.warm = {}, {}
        for label, kind, g in cls.cases:
            for store in (cls.cold, cls.warm):
                t = time.perf_counter(); r = cls.stack.run_graph(g, question=label)
                store[label] = (kind, time.perf_counter() - t, r['compute']['timings'])
        lines = []
        for label, (kind, s, t) in cls.warm.items():
            lines.append(f'{s:7.3f}s warm {cls.cold[label][1]:7.3f}s cold  {kind:12s} {label[:70]}')
        print('\n' + '\n'.join(lines), file=sys.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.stack.close(); cls.env.stop()

    def by_class(self, kind):
        return [s for k, s, _ in self.warm.values() if k == kind]

    def test_local_simple_p95(self):
        v = self.by_class('local_simple'); self.assertTrue(v)
        self.assertLess(pct(v, .95), BUDGETS['local_simple_p95'], sorted(v))

    def test_analytic_p95(self):
        v = self.by_class('analytic'); self.assertTrue(v)
        self.assertLess(pct(v, .95), BUDGETS['analytic_p95'], sorted(v))

    def test_operator_p95(self):
        nodes = {}
        for _, _, t in self.warm.values():
            for n in t['nodes']: nodes.setdefault(n['operation'], []).append(n['seconds'])
        over = {op: round(pct(v, .95), 3) for op, v in nodes.items()
                if op in BUDGETS['operator_p95'] and pct(v, .95) >= BUDGETS['operator_p95'][op]}
        self.assertFalse(over, over)

    def test_setup_p95(self):
        v = [t['setup_seconds'] for _, _, t in self.warm.values()]
        self.assertLess(pct(v, .95), BUDGETS['graph_setup_p95'])

    def test_cold_ceiling(self):
        self.assertLess(max(s for _, s, _ in self.cold.values()), BUDGETS['cold_max'])


if __name__ == '__main__':
    unittest.main()
