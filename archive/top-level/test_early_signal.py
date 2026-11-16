"""Early signal (matched filtering for known-shaped precursors): planted-template recovery at known SNR, empirical null
false-alarm rate, acute-data gap/spike exclusion, validation, graph integration, compiler routing and a real-stack smoke."""
import json, math, sqlite3, unittest
from pathlib import Path

import numpy as np

from clinical_core.agere.clinical import analysis_graph, early_signal as es, graph_contracts, local_compiler, semantic_layer, source_profile
from test_aggregate_explain import AS_OF, build, visit

ROOT = Path(__file__).resolve().parent


def planted(seed=0, P=3000, C=4, M=30, W=6, n_events=600, A=4.0):
    """Whitened unit-noise panel with a known template of norm 1 added at amplitude A before each event (optimal SNR = A)."""
    rng = np.random.default_rng(seed)
    h = np.zeros((C, W))
    h[0] = np.linspace(0.2, 1.0, W)          # rising contact volume
    h[1, -3:] = 0.8                          # late step
    h[3] = -np.linspace(0.0, 0.6, W)         # fading pickups
    h /= np.sqrt((h ** 2).sum())
    Z = rng.standard_normal((P, C, M)); Mk = np.ones((P, C, M))
    rows = rng.choice(P, n_events, replace=False)
    months = rng.integers(W + 1, M, n_events)
    for r, i in zip(rows, months): Z[r, :, i - W:i] += A * h
    return Z, Mk, h, list(zip(rows.tolist(), months.tolist())), sorted(set(range(P)) - set(rows.tolist()))


class MatchedFilter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.Z, cls.Mk, cls.h, cls.events, cls.null_rows = planted()
        cls.train, cls.test = cls.events[:300], cls.events[300:]
        cls.tpl = es.learn_template(cls.Z, cls.Mk, cls.train, 6)

    def test_recovers_planted_template_shape(self):
        T = self.tpl['template']
        cos = float((T * self.h).sum() / np.sqrt((T ** 2).sum()))
        self.assertGreater(cos, 0.95)
        self.assertTrue(np.all(T[2] == 0) or np.abs(T[2]).max() < 0.15)  # the empty channel is shrunk to (near) zero

    def test_detection_snr_matches_the_planted_amplitude(self):
        snr = np.array([es.matched(self.Z, self.Mk, self.tpl['template'], i - 1, 1, [r])[0][0] for r, i in self.test])
        self.assertAlmostEqual(float(snr.mean()), 4.0, delta=0.6)          # optimal SNR for a norm-1 template at amplitude 4
        self.assertAlmostEqual(float(snr.std()), 1.0, delta=0.25)         # unit-variance noise in the normalised statistic

    def test_null_false_alarm_rate_is_near_nominal(self):
        T = self.tpl['template']
        half = len(self.null_rows) // 2
        null = es.background(self.Z, self.Mk, T, self.null_rows[:half], range(5, 29, 3), 3)
        for alpha in (0.05, 0.01):
            thr = es.threshold(null, alpha)
            fresh = np.concatenate([es.matched(self.Z, self.Mk, T, t, 3, self.null_rows[half:])[0] for t in range(6, 29, 3)])
            rate = float((fresh >= thr).mean())
            tol = 4 * math.sqrt(alpha * (1 - alpha) / fresh.size) + 0.004
            self.assertAlmostEqual(rate, alpha, delta=tol, msg=f'alpha {alpha}')
        f = es.fap(null, fresh)
        self.assertAlmostEqual(float(f.mean()), 0.5, delta=0.03)          # FAP is ~uniform under the null
        hits = np.array([es.matched(self.Z, self.Mk, T, i - 1, 3, [r])[0][0] for r, i in self.test])
        self.assertGreater(float((hits >= es.threshold(null, 0.05)).mean()), 0.85)

    def test_sliding_alignment_recovers_the_lead(self):
        T = self.tpl['template']
        leads = [int(es.matched(self.Z, self.Mk, T, i - 2, 3, [r])[1][0]) for r, i in self.test if i - 2 >= 6]
        counts = np.bincount(leads, minlength=4)
        self.assertEqual(int(np.argmax(counts)), 2, counts)  # the true alignment is the most common best lead

    def test_masked_cells_carry_no_weight(self):
        Z, Mk = self.Z.copy(), self.Mk.copy()
        r, i = self.test[0]
        Z[r, 0, i - 6:i] = 1e6; Mk[r, 0, i - 6:i] = 0                      # a glitch in a masked channel-window
        a = es.matched(Z, Mk, self.tpl['template'], i - 1, 1, [r])[0][0]
        self.assertTrue(np.isfinite(a) and a < 20)

    def test_helpers(self):
        self.assertEqual(es.wilson(0, 0), (None, None))
        lo, hi = es.wilson(5, 10); self.assertTrue(lo < 0.5 < hi)
        self.assertEqual(es.auc([1, 2, 3, 4], [0, 0, 1, 1])['auc'], 1.0)
        self.assertEqual(es.auc([1, 1, 1, 1], [0, 1, 0, 1])['auc'], 0.5)
        self.assertEqual(es._matched_volume(np.array([3., 1, 1, 0]), np.array([True, True, False, False]), 2)['precision'], 0.75)


def _months(a, b):
    return [es._ms(i) for i in range(es._mi(a), es._mi(b) + 1)]


def synthetic_raw(seed=1, P=900, gap=('2027-01', '2027-12'), spike='2026-06', n_events=220):
    """A raw panel shaped like load()'s output: a planted pre-ED burst of phone messages and weight gain, an acute-data
    gap (no ED/urgent/admissions while primary care continues) and a one-month ED spike."""
    rng = np.random.default_rng(seed)
    months = _months('2026-01', '2029-07')
    M = len(months)
    g0, g1 = months.index(gap[0]), months.index(gap[1])
    counts = {ch: rng.poisson(lam, (P, M)).astype(float) for ch, lam in
              [('ed_visits', 0.01), ('urgent_care', 0.01), ('admissions', 0.004), ('pcp_visits', 0.5), ('telehealth', 0.3),
               ('phone_messages', 0.6), ('missed_appointments', 0.08), ('dispenses', 1.5)]}
    meas = {ch: np.full((P, M), np.nan) for ch, (k, _, _) in es.CHANNELS.items() if k == 'measure'}
    base_w = rng.normal(85, 12, P)
    obs = rng.random((P, M)) < 0.35
    meas['weight'][obs] = (base_w[:, None] + rng.normal(0, 1.0, (P, M)))[obs]
    ok = [k for k in range(8, M) if not g0 <= k <= g1]
    rows = rng.choice(P, n_events, replace=False)
    for r in rows:
        k = int(rng.choice(ok))
        counts['ed_visits'][r, k] += 1
        counts['phone_messages'][r, k - 3:k] += rng.poisson(2.0, 3)
        meas['weight'][r, k - 2:k] = base_w[r] + 3.0 + rng.normal(0, 1.0, 2)
    for ch in ('ed_visits', 'urgent_care', 'admissions'): counts[ch][:, g0:g1 + 1] = 0
    sk = months.index(spike)
    counts['ed_visits'][rng.choice(P, 150, replace=False), sk] += 1
    acute = (counts['ed_visits'] + counts['urgent_care'] + counts['admissions']).sum(0)
    total = acute + counts['pcp_visits'].sum(0)
    return {'start': es._mi(months[0]), 'months': months, 'pids': [f'S{i:04d}' for i in range(P)], 'counts': counts, 'measures': meas,
            'acute_monthly': {m: (int(acute[k]), int(total[k])) for k, m in enumerate(months)}}


class PanelAnalysis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = synthetic_raw()
        cls.p = es.validate({'outcome': 'ed_visit'})
        cls.report = es.analyse(cls.raw, '2029-07-31', cls.p)

    def test_gap_and_spike_are_detected_masked_and_never_indexed(self):
        r = self.report
        self.assertEqual([(g['start'], g['end']) for g in r['acute_data_gaps']], [('2027-01', '2027-12')])
        self.assertEqual(r['acute_spikes'], ['2026-06'])
        self.assertTrue(any('2027-01..2027-12' in n and 'masked' in n for n in r['notes']))
        geo = es.prepare(self.raw, '2029-07-31', self.p['channels'])
        bad = {geo['months'].index(m) for m in _months('2027-01', '2027-12') + ['2026-06'] if m in geo['months']}
        self.assertTrue(bad)
        for k in bad:
            self.assertFalse(geo['acute_observed'][k])
            self.assertFalse(geo['present']['ed_visits'][k])
            self.assertTrue(geo['present']['pcp_visits'][k])
        events = es.outcome_months(self.raw, geo, 'ed_visit')
        self.assertFalse(any(k in bad for ks in events.values() for k in ks))
        self.assertIn('2027-01..2027-12', es.summary(r))

    def test_learns_the_planted_channels_and_beats_chance(self):
        r = self.report
        top = [t['channel'] for t in r['templates'][:2]]
        self.assertIn('phone_messages', top)
        self.assertIn('weight', [t['channel'] for t in r['templates'][:4]])
        phone = next(t for t in r['templates'] if t['channel'] == 'phone_messages')
        self.assertGreater(min(phone['template'][-3:]), 0.3)
        ev = r['evaluation']
        self.assertEqual(ev['status'], 'evaluated')
        self.assertGreater(ev['matched_filter']['auc'], 0.65)
        self.assertGreater(ev['matched_filter']['auc'], ev['baseline']['auc'])
        self.assertGreater(ev['matched_filter']['recall'], ev['baseline']['recall'])
        self.assertLess(ev['null_false_alarm_rate_test'], 0.12)
        self.assertGreaterEqual(ev['matched_filter']['median_lead_months'], 1)
        self.assertLess(ev['cutoff'], ev['label_months'][:7])            # every label month is after the training cutoff
        self.assertEqual(ev['scoring_months'][0], ev['cutoff'])

    def test_ranked_patients_carry_snr_and_false_alarm_probability(self):
        rows = self.report['patients']
        self.assertEqual(len(rows), 25)
        self.assertEqual([x['snr'] for x in rows], sorted((x['snr'] for x in rows), reverse=True))
        for x in rows:
            self.assertTrue(0 < x['false_alarm_probability'] <= 1)
            self.assertEqual(x['alert'], x['snr'] >= self.report['threshold_snr'])
        self.assertEqual(self.report['flagged'] > 0, rows[0]['alert'])

    def test_population_with_few_events_falls_back_to_all_patients(self):
        r = es.analyse(self.raw, '2029-07-31', self.p, patient_ids=self.raw['pids'][:20])
        self.assertEqual(r['template_source'], 'all patients')
        self.assertTrue(any('templates learned from all patients' in n for n in r['notes']))
        self.assertLessEqual(r['patients_scored'], 20)

    def test_explicit_cutoff_and_partial_month(self):
        r = es.analyse(self.raw, '2029-07-31', es.validate({'outcome': 'ed_visit', 'train_cutoff': '2028-12'}))
        self.assertEqual(r['evaluation']['cutoff'], '2028-12')
        r = es.analyse(self.raw, '2029-07-15', self.p)
        self.assertEqual((r['partial_month'], r['panel']['end']), ('2029-07', '2029-06'))
        with self.assertRaises(ValueError):
            es.analyse(self.raw, '2029-07-31', es.validate({'train_cutoff': '2031-01'}))

    def test_answer_contract(self):
        plan = analysis_graph.validate({'interpretation': 'q', 'coverage': 'supported_observation', 'unmet_requirements': [], 'nodes': [
            {'id': 'answer', 'operation': 'early_signal', 'inputs': [], 'parameters': {'outcome': 'ed_visit'}, 'purpose': 'signal'}]}, AS_OF)
        answer = analysis_graph.build_answer(plan, [{'node': plan['nodes'][0], 'result': self.report}], {}, 'q')
        self.assertEqual((answer['status'], answer['answer_type']), ('answered', 'table'))
        self.assertEqual(answer['count'], self.report['flagged'])
        self.assertEqual(set(answer['patient_ids']), {x['patient_id'] for x in self.report['patients'] if x['alert']})
        self.assertTrue({'templates', 'evaluation', 'acute_data_gaps', 'threshold_snr'} <= set(answer['overall']['early_signal']))
        self.assertIn('matched filter', answer['method'])
        self.assertIn('Time-split check', analysis_graph.headline(answer))
        json.dumps(answer, allow_nan=False)
        self.assertEqual(analysis_graph.benchmark_references(answer, operation='early_signal'), [])


class Validation(unittest.TestCase):
    def test_defaults_and_bounds(self):
        p = es.validate({})
        self.assertEqual((p['outcome'], p['window_months'], p['horizon_months'], p['false_alarm_rate'], p['train_cutoff'], p['top']),
                         ('acute_care', 6, 3, 0.05, '', 25))
        self.assertEqual(p['channels'], list(es.CHANNELS))
        for bad in ({'outcome': 'death'}, {'window_months': 2}, {'window_months': 6.0}, {'horizon_months': 7}, {'false_alarm_rate': 0.9},
                    {'train_cutoff': '2029'}, {'train_cutoff': '2029-13'}, {'channels': ['weight']}, {'channels': ['weight', 'mood']},
                    {'top': 0}, {'extra': 1}):
            with self.assertRaises(ValueError, msg=bad):
                es.validate(bad)
        self.assertEqual(es.validate({'train_cutoff': '2029-01-15'})['train_cutoff'], '2029-01')

    def test_schema_shaped_payload_validates(self):
        from test_graph_contracts import catalog
        self.assertIn('early_signal', graph_contracts.TYPED)
        self.assertEqual(graph_contracts.input_contract('early_signal')['count'], [0, 1])
        schema = graph_contracts.parameter_schema('early_signal', catalog())
        self.assertEqual(set(schema['required']), set(schema['properties']))
        full = {'outcome': 'admission', 'window_months': 6, 'horizon_months': 3, 'false_alarm_rate': 0.05, 'train_cutoff': '', 'channels': [], 'top': 10}
        self.assertEqual(es.validate(full)['channels'], list(es.CHANNELS))
        with self.assertRaises(ValueError):
            analysis_graph.validate({'interpretation': 'q', 'coverage': 'supported_observation', 'unmet_requirements': [], 'nodes': [
                {'id': 'a', 'operation': 'early_signal', 'inputs': [], 'parameters': {'train_cutoff': '2030-01'}, 'purpose': 'x'}]}, AS_OF)

    def test_registered_everywhere(self):
        from clinical_core.agere.clinical.capability_registry import PRODUCT
        from clinical_core.agere.clinical.graph_planner import CONTRACTS
        self.assertIn('early_signal', PRODUCT); self.assertIn('early_signal', CONTRACTS)
        self.assertIn('early_signal', analysis_graph.OPERATIONS); self.assertIn('early_signal', analysis_graph.NO_BENCHMARK_OPERATIONS)
        self.assertTrue(analysis_graph.code_identity())
        from clinical_core.agere import ask_view
        page = ask_view.page()
        self.assertIn('if(o.early_signal)return vizEarlySignal(a)', page)  # console view: template chart + SNR-ranked patients
        self.assertIn('function sigChart(', page)

    def test_spike_rule(self):
        monthly = {'2026-08': (4, 400), '2026-09': (184, 3000), '2026-10': (6, 2500), '2029-04': (29, 2000), '2029-05': (63, 2000), '2029-06': (49, 2000)}
        self.assertEqual(es.acute_spikes(monthly), ['2026-09'])


class FixtureStack(unittest.TestCase):
    """load() against a real semantic layer built from a small fixture (the SQL paths, not the statistics)."""
    @classmethod
    def setUpClass(cls):
        patients = {f'p{i}': {'age_reported': 60, 'sex_reported': 'F'} for i in range(6)}
        recs = []
        for i in range(6):
            for m in range(1, 13):
                recs.append(visit(f'p{i}', 'Office Visit', f'2028-{m:02d}-10', 'PR1'))
                recs.append((f'p{i}', 'telephone_messages', {'text': 'call', 'event_date': f'2028-{m:02d}-12'}))
        recs += [visit('p0', 'emergency', '2028-09-03'), visit('p1', 'inpatient', '2028-10-01', 'H1', '2028-10-04')]
        cls.path = build(recs, patients)
        semantic_layer.build(cls.path)

    def test_load_and_execute(self):
        db = sqlite3.connect(f'file:{self.path / "semantic.sqlite3"}?mode=ro', uri=True)
        try:
            raw = es.load(db, '2028-12-31')
        finally:
            db.close()
        k = raw['months'].index('2028-09')
        p0 = raw['pids'].index('p0')
        self.assertEqual(raw['counts']['ed_visits'][p0, k], 1)
        self.assertEqual(raw['counts']['pcp_visits'][p0, k], 1)
        self.assertEqual(raw['counts']['phone_messages'][p0, k], 1)
        self.assertEqual(raw['counts']['admissions'][raw['pids'].index('p1'), raw['months'].index('2028-10')], 1)
        r = es.execute(self.path, None, {}, '2028-12-31')
        self.assertEqual(r['schema'], es.VERSION)
        self.assertEqual(r['evaluation']['status'], 'not_evaluable')  # two events: honest refusal, not a fit
        json.dumps(r, allow_nan=False)


class Compiler(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        recs = [visit('p0', 'emergency', '2028-01-01'), ('p0', 'adt', {'event': 'emergency_registration', 'event_date': '2028-01-01'}),
                ('p0', 'coverage', {'coverage_type': 'medicare', 'event_date': '2026-01-01'})]
        cls.profile = source_profile.load(build(recs, {'p0': {'age_reported': 70, 'sex_reported': 'F'}}))

    def route(self, q):
        g, info = local_compiler.compile(q, self.profile, AS_OF)
        self.assertIsNotNone(g, (q, info))
        self.assertEqual(g['nodes'][-1]['operation'], 'early_signal', q)
        analysis_graph.validate(g, AS_OF)
        return g

    def test_phrasings(self):
        g = self.route("Which patients look like they're heading toward a heart failure admission?")
        self.assertEqual(g['nodes'][0]['parameters']['specification']['all'][0]['concept'], 'heart_failure')
        self.assertEqual(g['nodes'][-1]['parameters']['outcome'], 'admission')
        self.assertEqual(self.route('Find patients showing the pattern that usually precedes an ED visit')['nodes'][-1]['parameters']['outcome'], 'ed_visit')
        self.assertEqual(self.route('Early warning signs of decompensation')['nodes'][-1]['parameters']['outcome'], 'acute_care')
        self.assertEqual(self.route('Who has the pre-admission signature?')['nodes'][-1]['parameters']['outcome'], 'admission')
        p = self.route('Which COPD patients are on the road to a hospitalization in the next 2 months?')['nodes'][-1]['parameters']
        self.assertEqual((p['outcome'], p['horizon_months']), ('admission', 2))
        self.route('Run a matched filter for precursors of acute care visits')
        p = self.route('Who is about to end up in the ED? use a false alarm rate of 1%')['nodes'][-1]['parameters']
        self.assertEqual((p['outcome'], p['false_alarm_rate']), ('ed_visit', 0.01))

    def test_acceptance_phrasings_route_locally(self):
        cases = json.loads((ROOT / 'bench' / 'acceptance' / 'early_signal.json').read_text())
        self.assertEqual(len(cases), 8)
        for c in cases:
            self.assertEqual(c['expect_operation'], 'early_signal')
            self.route(c['question'])

    def test_does_not_steal_other_operators(self):
        for q in ('Which patients have declining eGFR?', 'How long until discharged patients get a follow-up visit?',
                  'Who should our care managers call this week? we have 40 slots', 'Where do patients go after an ED visit?',
                  'How many patients had an ED visit in 2028?', 'Which heart failure patients are projected to drop below eGFR 30 within a year?'):
            g, _ = local_compiler.compile(q, self.profile, AS_OF)
            self.assertNotIn('early_signal', [n['operation'] for n in (g or {}).get('nodes', [])], q)


STACK = Path('/Users/mikel/clinical-insights-local/artifacts/agere-healthcare-v123/stack')


def _stack_ready():
    try:
        db = sqlite3.connect(f'file:{STACK / "semantic.sqlite3"}?mode=ro', uri=True)
        try:
            meta = dict(db.execute('SELECT key, value FROM meta'))
        finally:
            db.close()
        return meta.get('version') == semantic_layer.VERSION
    except sqlite3.Error:
        return False


@unittest.skipUnless((STACK / 'semantic.sqlite3').exists() and _stack_ready(), 'real synthetic release not present (or built by another semantic-layer version)')
class RealRelease(unittest.TestCase):
    def test_smoke(self):
        original = semantic_layer.ensure
        semantic_layer.ensure = lambda path: STACK / 'semantic.sqlite3'  # read the release as built; never rebuild it from a test
        try:
            r = es.execute(STACK, None, {})
        finally:
            semantic_layer.ensure = original
        self.assertGreater(r['patients_scored'], 1000)
        self.assertTrue(any(g['start'] <= '2027-06' <= g['end'] for g in r['acute_data_gaps']))
        self.assertIn('2026-09', r['acute_spikes'])
        self.assertIn(r['evaluation']['status'], ('evaluated', 'not_evaluable'))
        json.dumps(r, allow_nan=False)


if __name__ == '__main__':
    unittest.main()
