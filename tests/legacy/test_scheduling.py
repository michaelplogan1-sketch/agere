"""Scheduling / yield management (no-show model, overbooking, reminders, access).

Overbooking optimum matches brute-force enumeration of every show/no-show outcome on simulated sessions; the
logistic no-show model recovers known coefficients and is calibrated on simulated data; validation errors; the
operator end to end on a small synthetic appointment store (beats the practice-rate baseline, detects the
cancellation defect); compiler routing (and plain no-show RATE questions stay literal metrics); graph and console
integration; and a read-only smoke test on the real stack when its semantic layer is current."""
import itertools
import json
import sqlite3
import unittest
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from clinical_core.agere.clinical import (analysis_graph, capability_registry, graph_contracts, graph_planner, local_compiler,
                                          scheduling, semantic_layer, source_profile)
from test_aggregate_explain import build

AS_OF = '2029-08-15'
REAL_STACK = Path('/Users/mikel/clinical-insights-local/artifacts/agere-healthcare-v123/stack')
ROOT = Path(__file__).resolve().parents[2]


def brute_force_curve(show_probs, capacity, extra_p, max_extra, value, cost):
    """EV(k) by enumerating every joint show outcome (2^(n+k) cases)."""
    out = []
    for k in range(max_extra + 1):
        probs = list(show_probs) + [extra_p] * k
        ev = 0.0
        for outcome in itertools.product((0, 1), repeat=len(probs)):
            pr = float(np.prod([p if o else 1 - p for p, o in zip(probs, outcome)]))
            s = sum(outcome)
            ev += pr * (value * min(s, capacity) - cost * max(s - capacity, 0))
        out.append(ev)
    return out


class Overbooking(unittest.TestCase):
    def test_poisson_binomial_matches_enumeration(self):
        rng = np.random.default_rng(1)
        for _ in range(20):
            p = rng.uniform(0.05, 0.95, rng.integers(1, 9))
            pmf = scheduling.poisson_binomial(p)
            want = np.zeros(len(p) + 1)
            for o in itertools.product((0, 1), repeat=len(p)):
                want[sum(o)] += np.prod([q if x else 1 - q for q, x in zip(p, o)])
            np.testing.assert_allclose(pmf, want, atol=1e-12)

    def test_optimum_matches_brute_force_on_simulated_sessions(self):
        rng = np.random.default_rng(7)
        for trial in range(40):
            cap = int(rng.integers(2, 8))
            booked = 1 - rng.beta(2, 8, int(rng.integers(max(1, cap - 2), cap + 1)))  # show probabilities
            extra = float(1 - rng.beta(2, 6))
            value, cost = float(rng.choice([1.0, 2.0])), float(rng.choice([0.5, 1.0, 2.0, 4.0]))
            curve = scheduling.session_curve(booked, cap, extra, value, cost, max_extra=5)
            brute = brute_force_curve(booked, cap, extra, 5, value, cost)
            with self.subTest(trial=trial):
                np.testing.assert_allclose([r['expected_value'] for r in curve], brute, atol=1e-3)
                self.assertEqual(scheduling.best_extra(curve)['extra'], int(np.argmax(np.round(brute, 9))))

    def test_newsvendor_critical_ratio_for_identical_patients(self):
        # identical show probability q: add an extra while P(S >= C) < v / (v + c) with S = shows among those already booked
        q, cap, v, c = 0.8, 10, 1.0, 2.0
        curve = scheduling.session_curve([q] * cap, cap, q, v, c, max_extra=10)
        k_star = scheduling.best_extra(curve)['extra']
        rule = 0
        while 1 - np.sum(scheduling.poisson_binomial([q] * (cap + rule))[:cap]) < v / (v + c): rule += 1
        self.assertEqual(k_star, rule)
        self.assertGreater(k_star, 0)

    def test_safety_cap_limits_overflow_probability(self):
        curve = scheduling.session_curve([0.7] * 8, 8, 0.7, 1.0, 0.5, max_extra=6)
        free, safe = scheduling.best_extra(curve), scheduling.best_extra(curve, 0.1)
        self.assertLessEqual(safe['p_overflow'], 0.1)
        self.assertLessEqual(safe['extra'], free['extra'])
        self.assertGreater(free['extra'], safe['extra'])


class Model(unittest.TestCase):
    def test_logistic_recovers_coefficients_and_is_calibrated_on_held_out_data(self):
        rng = np.random.default_rng(3)
        n = 20000
        X = np.column_stack([np.ones(n), rng.normal(size=n), rng.binomial(1, 0.2, n), rng.normal(size=n)])
        beta = np.array([-2.4, 0.5, 1.2, 0.0])
        y = rng.binomial(1, 1 / (1 + np.exp(-X @ beta)))
        w = scheduling.fit_logistic(X[:15000], y[:15000], l2=1.0)
        np.testing.assert_allclose(w, beta, atol=0.12)
        p = scheduling.predict(w, X[15000:])
        ev = scheduling.evaluate(p, y[15000:])
        ev.update(scheduling.calibration_slope(p, y[15000:]))
        self.assertLess(ev['ece'], 0.02)
        self.assertTrue(0.85 <= ev['slope'] <= 1.15, ev)
        self.assertGreater(ev['auc'], 0.65)
        # a constant (rate-only) baseline has no discrimination
        self.assertAlmostEqual(scheduling.auc(np.full(len(p), 0.1), y[15000:]), 0.5)

    def test_auc_matches_pairwise_definition(self):
        rng = np.random.default_rng(5)
        p, y = rng.integers(0, 5, 60) / 4, rng.binomial(1, 0.4, 60)
        pos, neg = p[y == 1], p[y == 0]
        want = np.mean([(a > b) + 0.5 * (a == b) for a in pos for b in neg])
        self.assertAlmostEqual(scheduling.auc(p, y), want)


class Validation(unittest.TestCase):
    def test_defaults(self):
        p = scheduling.validate({})
        self.assertEqual((p['focus'], p['horizon_days'], p['value_per_visit'], p['overflow_cost'], p['reminder_effect']), ('all', 14, 1.0, 2.0, 0.3))
        self.assertIsNone(p['capacity'])
        self.assertIsNone(p['l2'])

    def test_errors(self):
        for bad in ({'focus': 'cancellations'}, {'horizon_days': 0}, {'horizon_days': 1.5}, {'capacity': 0}, {'reminder_effect': 1.5},
                    {'overflow_cost': -1}, {'max_overflow_probability': 2}, {'unknown': 1}, {'practice': 3}, {'test_months': 1}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    scheduling.validate(bad)

    def test_graph_validates_and_rejects(self):
        plan = {'interpretation': 'x', 'coverage': 'supported_observation', 'unmet_requirements': [],
                'nodes': [{'id': 'answer', 'operation': 'scheduling', 'inputs': [], 'purpose': 'x', 'parameters': {'focus': 'overbooking'}}]}
        self.assertEqual(analysis_graph.validate(plan, AS_OF)['nodes'][0]['parameters']['overflow_cost'], 2.0)
        plan['nodes'][0]['parameters'] = {'focus': 'nope'}
        with self.assertRaises(ValueError):
            analysis_graph.validate(plan, AS_OF)


def simulate_store(seed=11, patients=260):
    """Appointments 2026-09..2029-10 at three practices. Transport need and long lead times raise no-show odds; cancellations
    only in 2026 (mirrors defect D5); a few upcoming bookings after the snapshot."""
    rng = np.random.default_rng(seed)
    pats, recs, truth = {}, [], {}
    start, end, snap = date(2026, 9, 1).toordinal(), date(2029, 10, 10).toordinal(), date.fromisoformat(AS_OF).toordinal()
    aid = 0
    for i in range(patients):
        pid = f'p{i}'
        pats[pid] = {'age_reported': int(rng.integers(20, 90)), 'sex_reported': 'F' if i % 2 else 'M'}
        transport = rng.random() < 0.25
        recs.append((pid, 'coverage', {'coverage_type': ['commercial', 'medicaid', 'medicare'][i % 3], 'status': 'active', 'event_date': '2026-01-01'}))
        recs.append((pid, 'social_screens', {'transport_difficulty_reported': int(transport), 'event_date': '2026-06-01'}))
        practice = f'WH-PRACTICE-{1 + i % 3}'
        for _ in range(int(rng.integers(6, 16))):
            sched = int(rng.integers(start, end))
            while date.fromordinal(sched).weekday() >= 5: sched += 1
            lead = int(rng.integers(1, 45))
            book = sched - lead
            a = f'A{aid}'; aid += 1
            logit = -3.0 + 1.8 * transport + 0.9 * (lead > 21) + (0.3 if practice.endswith('3') else 0.0)
            p = 1 / (1 + np.exp(-logit)); truth[a] = p
            base = {'appointment_id': a, 'organization_id': practice, 'scheduled_date': date.fromordinal(sched).isoformat(), 'purpose': 'condition_monitoring'}
            recs.append((pid, 'appointments', {**base, 'status': 'booked', 'event_date': date.fromordinal(book).isoformat(),
                                               'available_date': date.fromordinal(book).isoformat()}))
            if sched > snap: continue
            if sched < date(2027, 1, 1).toordinal() and rng.random() < 0.05:
                status = 'cancelled'
            else:
                status = 'no_show' if rng.random() < p else 'fulfilled'
            recs.append((pid, 'appointments', {**base, 'status': status, 'event_date': base['scheduled_date'],
                                               'available_date': (date.fromordinal(sched) + timedelta(days=2)).isoformat()}))
    return recs, pats, truth


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        recs, pats, cls.truth = simulate_store()
        recs.append(('p0', 'adt', {'event': 'emergency_registration', 'event_date': '2029-02-10'}))
        cls.path = build(recs, pats)
        semantic_layer.build(cls.path)
        cls.report = scheduling.execute(cls.path, None, {}, AS_OF)

    def test_model_beats_practice_rate_baseline_and_is_calibrated(self):
        m = self.report['model']
        self.assertGreater(m['model']['auc'], m['baseline']['auc'] + 0.08, m)
        self.assertLess(m['model']['brier'], m['baseline']['brier'])
        self.assertLess(m['model']['ece'], 0.04)
        self.assertTrue(m['beats_baseline'])
        coef = {c['feature']: c['coefficient'] for c in m['coefficients']}
        self.assertGreater(coef['need_transport'], 0.8)
        self.assertLessEqual(m['test_window'][1], AS_OF)
        self.assertGreater(self.report['counts']['test'], 300)

    def test_sessions_curves_and_reminders(self):
        r = self.report
        self.assertEqual({s['practice'] for s in r['sessions']}, {'WH-PRACTICE-1', 'WH-PRACTICE-2', 'WH-PRACTICE-3'})
        for s in r['sessions']:
            best = max(s['curve'], key=lambda x: x['expected_value'])
            self.assertAlmostEqual(s['curve'][s['recommended_extra']]['expected_value'], best['expected_value'], places=6)
        apps = r['appointments']
        self.assertTrue(apps)
        self.assertEqual([a['p_no_show'] for a in apps], sorted((a['p_no_show'] for a in apps), reverse=True))
        self.assertTrue(all(a['scheduled_date'] > AS_OF for a in apps))
        rem = r['reminders']
        self.assertGreaterEqual(rem['expected_averted'], rem['random_choice_averted'])
        # cheaper overflow -> more overbooking (monotone in the cost ratio)
        cheap = scheduling.execute(self.path, None, {'focus': 'overbooking', 'overflow_cost': 0.2, 'capacity': 8}, AS_OF)
        dear = scheduling.execute(self.path, None, {'focus': 'overbooking', 'overflow_cost': 5.0, 'capacity': 8}, AS_OF)
        for a, b in zip(cheap['sessions'], dear['sessions']):
            self.assertGreaterEqual(a['recommended_extra'], b['recommended_extra'])
        self.assertGreater(sum(s['recommended_extra'] for s in cheap['sessions']), 0)

    def test_practice_focus_horizon_and_defects(self):
        r = scheduling.execute(self.path, None, {'practice': '3', 'horizon_days': 7, 'focus': 'no_show_risk'}, AS_OF)
        self.assertEqual(r['practice'], 'WH-PRACTICE-3')
        self.assertEqual([s['practice'] for s in r['sessions']], ['WH-PRACTICE-3'])
        cutoff = (date.fromisoformat(AS_OF) + timedelta(days=7)).isoformat()
        self.assertTrue(all(a['practice'] == 'WH-PRACTICE-3' and a['scheduled_date'] <= cutoff for a in r['appointments']))
        codes = {d['code'] for d in self.report['data_defects']}
        self.assertIn('cancellations_stop', codes)
        self.assertIn('no_slot_inventory', codes)
        self.assertIn('third_next_available', self.report['access_unavailable'])
        self.assertIn('Data defect', scheduling.summary(self.report))

    def test_population_input_restricts_appointments(self):
        ids = [f'p{i}' for i in range(0, 260, 2)]
        r = scheduling.execute(self.path, ids, {'focus': 'no_show_risk'}, AS_OF)
        self.assertTrue(all(int(a['patient_id'][1:]) % 2 == 0 for a in r['appointments']))
        self.assertLess(r['counts']['appointments'], self.report['counts']['appointments'])

    def test_graph_answer_contract(self):
        for focus in ('all', 'reminders', 'access'):
            plan = analysis_graph.validate({'interpretation': 'x', 'coverage': 'supported_observation', 'unmet_requirements': [],
                                            'nodes': [{'id': 'answer', 'operation': 'scheduling', 'inputs': [], 'purpose': 'x',
                                                       'parameters': {'focus': focus}}]}, AS_OF)
            report = scheduling.execute(self.path, None, plan['nodes'][0]['parameters'], AS_OF)
            answer = analysis_graph.build_answer(plan, [{'node': plan['nodes'][0], 'result': report}], {}, 'how much should each clinic overbook?')
            answer['headline'] = analysis_graph.headline(answer)
            answer = json.loads(json.dumps(answer, allow_nan=False, default=str))
            with self.subTest(focus=focus):
                self.assertEqual((answer['status'], answer['answer_type']), ('answered', 'table'))
                self.assertTrue(answer['table'])
                o = answer['overall']['scheduling']
                self.assertTrue(o['sessions'][0]['curve'])
                self.assertEqual(analysis_graph.benchmark_references(answer, 'q', 'scheduling'), [])
        self.assertIn('p_no_show', json.loads(json.dumps(answer, default=str))['overall']['scheduling']['appointments'][0])

    def test_too_little_data_is_unsupported_not_an_error(self):
        r = scheduling.execute(self.path, ['p0', 'p1'], {}, AS_OF)
        self.assertIsNone(r['model'])
        self.assertIn('Not enough', scheduling.summary(r))


PHRASINGS = [
    ('How much should each clinic overbook?', {'focus': 'overbooking'}),
    ('Which appointments are likely no-shows tomorrow?', {'focus': 'no_show_risk', 'horizon_days': 1}),
    ('Which appointments next week are likely to be no-shows?', {'focus': 'no_show_risk', 'horizon_days': 7}),
    ('How many extra slots can practice 3 safely book?', {'focus': 'overbooking', 'practice': '3', 'max_overflow_probability': 0.1}),
    ("What's our access — days to third-next-available?", {'focus': 'access'}),
    ('Who should get a reminder call to cut no-shows?', {'focus': 'reminders'}),
    ('Predict no-shows for the next 10 days at practice 2', {'focus': 'no_show_risk', 'horizon_days': 10, 'practice': '2'}),
    ('Should practice 1 double-book, with 6 slots per session and an overflow cost of 3?', {'focus': 'overbooking', 'capacity': 6, 'overflow_cost': 3.0}),
    ('We can make 40 reminder calls; reminders cut no-shows by 25%. Who should we call?', {'focus': 'reminders', 'reminder_capacity': 40, 'reminder_effect': 0.25}),
]
NOT_SCHEDULING = ['What was our no-show rate for appointments scheduled in 2027?', 'What is the no-show rate by practice?',
                  'How many no-shows were there in 2028?', 'What was our cancellation rate in 2026?']


class Routing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Fixture.setUpClass()
        cls.profile = source_profile.load(Fixture.path)

    def test_phrasings(self):
        for q, want in PHRASINGS:
            with self.subTest(q=q):
                g, info = local_compiler.compile(q, self.profile, AS_OF)
                self.assertIsNotNone(g, info.get('reason'))
                self.assertEqual(info['route'], 'local_compiler')
                self.assertEqual(g['nodes'][-1]['operation'], 'scheduling')
                params = analysis_graph.validate(g, AS_OF)['nodes'][-1]['parameters']
                for k, v in want.items():
                    self.assertEqual(params[k], v, (q, params))

    def test_plain_no_show_metrics_are_not_stolen(self):
        for q in NOT_SCHEDULING:
            with self.subTest(q=q):
                g, info = local_compiler.compile(q, self.profile, AS_OF)
                self.assertTrue(g is None or g['nodes'][-1]['operation'] != 'scheduling', g)
        g, _ = local_compiler.compile(NOT_SCHEDULING[0], self.profile, AS_OF)
        self.assertEqual(g['nodes'][-1]['operation'], 'sql')
        self.assertIn('ever_no_show', g['nodes'][-1]['parameters']['query'])

    def test_acceptance_file(self):
        rows = json.loads((ROOT / 'bench' / 'acceptance' / 'scheduling.json').read_text())
        self.assertGreaterEqual(len(rows), 8)
        for row in rows:
            with self.subTest(q=row['question']):
                self.assertEqual(row['expect_operation'], 'scheduling')
                g, info = local_compiler.compile(row['question'], self.profile, AS_OF)
                self.assertIsNotNone(g, info.get('reason'))
                self.assertEqual(g['nodes'][-1]['operation'], 'scheduling')
                analysis_graph.validate(g, AS_OF)


class Integration(unittest.TestCase):
    def test_registered_everywhere(self):
        self.assertIn('scheduling', analysis_graph.OPERATIONS)
        self.assertIn('scheduling', analysis_graph.NO_BENCHMARK_OPERATIONS)
        self.assertIn('scheduling', graph_contracts.TYPED)
        self.assertEqual(graph_contracts.input_contract('scheduling')['count'], [0, 1])
        self.assertIn('scheduling', graph_planner.CONTRACTS)
        self.assertEqual(capability_registry.PRODUCT['scheduling']['population'], 'optional')
        schema = graph_contracts.parameter_schema('scheduling', {'cohort_fields': {'record_fields': {}}, 'evidence_catalog': {}})
        self.assertEqual(set(schema['properties']['focus']['enum']), set(scheduling.FOCUS))
        self.assertLessEqual(set(schema['properties']), set(scheduling.DEFAULTS))
        self.assertIn('scheduling', Path(analysis_graph.__file__).read_text().split('def code_identity')[1].split('def catalog')[0])

    def test_population_node_feeds_scheduling(self):
        plan = analysis_graph.validate({'interpretation': 'x', 'coverage': 'supported_observation', 'unmet_requirements': [], 'nodes': [
            {'id': 'population', 'operation': 'cohort', 'inputs': [], 'purpose': 'all', 'parameters': {'specification': {'all': [], 'any': [], 'exclude': []}}},
            {'id': 'answer', 'operation': 'scheduling', 'inputs': ['population'], 'purpose': 'x', 'parameters': {}}]}, AS_OF)
        self.assertEqual(plan['nodes'][-1]['inputs'], ['population'])

    def test_console_renders_scheduling_answers(self):
        from clinical_core.agere import ask_view
        html = ask_view.page()
        self.assertIn('function vizScheduling', html)
        self.assertIn('if(o.scheduling)return vizScheduling(a)', html)
        self.assertIn('function yieldChart', html)
        self.assertEqual(html.count('</script>'), 2)


def _current_real_stack():
    if not ((REAL_STACK / 'semantic.sqlite3').exists() and (REAL_STACK / 'run.json').exists()): return False
    db = sqlite3.connect(f'file:{REAL_STACK / "semantic.sqlite3"}?mode=ro', uri=True)
    try:
        meta = dict(db.execute('SELECT key, value FROM meta'))
    finally:
        db.close()
    run = json.loads((REAL_STACK / 'run.json').read_text())
    return meta.get('version') == semantic_layer.VERSION and meta.get('source_digest') == run['source_digest']


@unittest.skipUnless(_current_real_stack(), 'real stack absent or its semantic layer is not current (never rebuilt here)')
class RealStack(unittest.TestCase):
    """Read-only: the semantic layer is opened with mode=ro and only when ensure() would not rebuild it."""

    def test_model_overbooking_and_defects(self):
        r = scheduling.execute(REAL_STACK, None, {'practice': '3'})
        m = r['model']
        self.assertIsNotNone(m)
        self.assertGreater(m['model']['cases'], 1000)
        for k in ('auc', 'brier', 'ece', 'slope'):
            self.assertIsNotNone(m['model'][k]); self.assertIsNotNone(m['baseline'][k])
        self.assertEqual(r['practice'], 'WH-PRACTICE-3')
        self.assertTrue(r['sessions'] and r['sessions'][0]['curve'])
        self.assertIn('cancellations_stop', {d['code'] for d in r['data_defects']})
        self.assertTrue(scheduling.summary(r))

    def test_no_show_rate_probe_keeps_its_literal_route(self):
        prof = json.loads((REAL_STACK / 'source-profile.json').read_text()) if (REAL_STACK / 'source-profile.json').exists() else None
        run = json.loads((REAL_STACK / 'run.json').read_text())
        if not prof or prof.get('schema') != source_profile.VERSION or prof.get('source_digest') != run['source_digest']:
            self.skipTest('saved source profile is not current; not rebuilding it')
        g, info = local_compiler.compile('What was our no-show rate for appointments scheduled in 2027?', prof, AS_OF)
        self.assertEqual(info['route'], 'local_compiler')
        node = g['nodes'][-1]
        self.assertEqual(node['operation'], 'sql')
        db = sqlite3.connect(f'file:{REAL_STACK / "semantic.sqlite3"}?mode=ro', uri=True)
        try:
            value = db.execute(node['parameters']['query']).fetchone()[0]
        finally:
            db.close()
        self.assertAlmostEqual(value, 8.98, places=2)
        for row in json.loads((ROOT / 'bench' / 'acceptance' / 'scheduling.json').read_text()):
            with self.subTest(q=row['question']):
                g, info = local_compiler.compile(row['question'], prof, AS_OF)
                self.assertEqual(g['nodes'][-1]['operation'], 'scheduling', info.get('reason'))


if __name__ == '__main__':
    unittest.main()
