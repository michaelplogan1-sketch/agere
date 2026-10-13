"""Statistical process control, feed anomalies and claims review flags (monitor operation)."""
import json, math, os, random, sqlite3, unittest
from pathlib import Path

from clinical_core.agere.clinical import monitor, semantic_layer, local_compiler, source_profile, briefing
from clinical_core.agere.clinical.analysis_graph import build_answer, headline, validate
from test_aggregate_explain import build, pcp

AS_OF = '2029-08-15'
STACK = Path(os.environ.get('MONITOR_STACK') or Path(__file__).resolve().parent / 'artifacts' / 'agere-healthcare-v123' / 'stack')


def points(zs):
    return [{'index': i, 'z': z, 'value': z} for i, z in enumerate(zs)]


def rules(eps):
    return {e['rule'] for e in eps}


class ControlCharts(unittest.TestCase):
    def test_stable_u_chart_with_varying_exposure_does_not_fire(self):
        rng = random.Random(3)
        ns = [rng.choice([800, 1200, 2000, 3500]) for _ in range(36)]
        counts = [round(0.01 * n + rng.choice([-1, 0, 1]) * math.sqrt(0.01 * n) * 0.5) for n in ns]
        chart = monitor.control_chart(counts, ns, kind='u')
        self.assertAlmostEqual(chart['center'], 0.01, delta=0.001)
        self.assertEqual(monitor.rule_signals(chart['points']), [])
        self.assertEqual(chart['gaps'], [])
        # Per-point limits follow the denominator: smaller exposure, wider limits.
        small = min(chart['points'], key=lambda p: p['n']); big = max(chart['points'], key=lambda p: p['n'])
        self.assertGreater(small['ucl'] - small['lcl'], big['ucl'] - big['lcl'])

    def test_p_chart_limits_use_each_denominator(self):
        chart = monitor.control_chart([5, 50, 10, 100, 20, 200], [100, 1000, 200, 2000, 400, 4000], kind='p', laney=False)
        self.assertAlmostEqual(chart['center'], 0.05)
        p100, p2000 = chart['points'][0], chart['points'][3]
        self.assertAlmostEqual(p100['ucl'], 0.05 + 3 * math.sqrt(0.05 * 0.95 / 100), places=6)
        self.assertAlmostEqual(p2000['ucl'], 0.05 + 3 * math.sqrt(0.05 * 0.95 / 2000), places=6)

    def test_sustained_shift_fires_and_baseline_ignores_it(self):
        ns = [1000] * 30
        counts = [10, 11, 9, 10, 12, 8, 10, 11, 9, 10, 10, 9, 11, 10, 10, 9, 11, 10, 9, 10, 10, 11] + [30] * 8
        chart = monitor.control_chart(counts, ns, kind='u')
        self.assertAlmostEqual(chart['center'] * 1000, 10, delta=0.5)   # Phase-I trimmed: the shift is not the baseline
        fired = rules(monitor.rule_signals(chart['points']))
        self.assertIn('beyond_3_sigma', fired)
        self.assertIn('8_in_a_row_one_side', fired)

    def test_western_electric_rules_individually(self):
        self.assertIn('beyond_3_sigma', rules(monitor.rule_signals(points([0, 0.5, -0.3, 3.4, 0.1]))))
        self.assertIn('2_of_3_beyond_2_sigma', rules(monitor.rule_signals(points([0, 2.3, 0.4, 2.5, 0]))))
        self.assertNotIn('2_of_3_beyond_2_sigma', rules(monitor.rule_signals(points([0, 2.3, -2.4, 0.2, 0]))))  # opposite sides
        self.assertIn('4_of_5_beyond_1_sigma', rules(monitor.rule_signals(points([1.2, 1.5, 0.2, 1.3, 1.1]))))
        self.assertIn('8_in_a_row_one_side', rules(monitor.rule_signals(points([0.2, 0.5, 0.1, 0.9, 0.3, 0.4, 0.2, 0.6]))))
        self.assertNotIn('8_in_a_row_one_side', rules(monitor.rule_signals(points([0.2, 0.5, 0.1, 0.9, 0.3, 0.4, 0.2]))))
        self.assertIn('6_trending', rules(monitor.rule_signals(points([-1.0, -0.6, -0.2, 0.1, 0.5, 0.9]))))
        self.assertNotIn('6_trending', rules(monitor.rule_signals(points([-1.0, -0.6, -0.2, 0.1, 0.5]))))
        # Pure noise within one sigma alternating sides: nothing fires.
        self.assertEqual(monitor.rule_signals(points([0.5, -0.4, 0.3, -0.6, 0.2, -0.1, 0.4, -0.5, 0.3, -0.2] * 3)), [])

    def test_run_probabilities_become_surprise_bits(self):
        ep = next(e for e in monitor.rule_signals(points([0.3] * 10)) if e['rule'] == '8_in_a_row_one_side')
        self.assertAlmostEqual(ep['p'], 2 * 0.5 ** 10)
        self.assertAlmostEqual(ep['bits'], 9.0, places=2)
        self.assertAlmostEqual(monitor.z_bits(3.0), -math.log2(2 * 0.0013499), places=2)

    def test_ewma_catches_small_sustained_shift_shewhart_misses(self):
        zs = [0.1, -0.2, 0.0, 0.2, -0.1, 0.0] + [1.5] * 10
        pts = points(zs)
        self.assertNotIn('beyond_3_sigma', rules(monitor.rule_signals(pts)))
        self.assertTrue(monitor.ewma_signals(pts))
        self.assertEqual(monitor.ewma_signals(points([0.2, -0.3, 0.1, -0.1, 0.25, -0.2] * 4)), [])

    def test_laney_widens_limits_for_overdispersed_large_denominators(self):
        rng = random.Random(9)
        ns = [50000] * 30
        counts = [round(n * 0.02 * (1 + rng.uniform(-0.1, 0.1))) for n in ns]   # +-10% month-to-month: extra-binomial
        plain = monitor.control_chart(counts, ns, kind='p', laney=False)
        laney = monitor.control_chart(counts, ns, kind='p')
        self.assertGreater(laney['phi'], 1.5)
        self.assertTrue(any(e['rule'] == 'beyond_3_sigma' for e in monitor.rule_signals(plain['points'])))
        self.assertFalse(any(e['rule'] == 'beyond_3_sigma' for e in monitor.rule_signals(laney['points'])))

    def test_zero_runs_are_feed_gaps_not_baseline(self):
        counts = [0, 0, 12, 10, 11, 9, 0, 0, 0, 0, 0, 10, 12, 11, 9, 10]
        chart = monitor.control_chart(counts, [1000] * 16, kind='u')
        kinds = {g['rule']: g for g in chart['gaps']}
        self.assertEqual((kinds['zero_run']['start'], kinds['zero_run']['end']), (6, 10))
        self.assertEqual(kinds['not_yet_started']['bits'], 0.0)              # a source that starts later is not a gap
        self.assertAlmostEqual(chart['center'] * 1000, 10.5, delta=0.6)       # gap months excluded from the center
        # A rare event with zeros consistent with its rate is not a gap.
        rare = monitor.control_chart([0, 1, 0, 0, 1, 0, 0, 0, 1, 0, 0, 1], [1000] * 12, kind='u')
        self.assertEqual([g for g in rare['gaps'] if g['rule'] == 'zero_run'], [])


class Benford(unittest.TestCase):
    def test_conforming_amounts_pass(self):
        rng = random.Random(1)
        amounts = [round(10 ** rng.uniform(2, 6)) for _ in range(3000)]   # log-uniform over four decades
        r = monitor.benford_test(amounts)
        self.assertEqual(r['status'], 'conforms')
        self.assertLess(r['mad'], 0.006)
        self.assertEqual(r['bits'], 0.0)

    def test_fabricated_digits_are_flagged(self):
        rng = random.Random(2)
        amounts = [rng.randint(1, 9) * 10 ** rng.randint(2, 5) + rng.randint(0, 99) for _ in range(2000)]  # uniform leading digit
        r = monitor.benford_test(amounts)
        self.assertEqual(r['status'], 'review_flag')
        self.assertEqual(r['conformity'], 'nonconformity')
        self.assertLess(r['p_value'], 1e-6)
        self.assertGreater(r['bits'], 20)

    def test_sample_size_and_applicability_guards(self):
        rng = random.Random(3)
        self.assertEqual(monitor.benford_test([rng.randint(1, 9) * 1000 for _ in range(80)])['status'], 'insufficient')
        fabricated_small = [rng.randint(1, 9) * 10 ** rng.randint(2, 5) + rng.randint(0, 99) for _ in range(150)]
        self.assertEqual(monitor.benford_test(fabricated_small)['status'], 'low_power')
        fee_schedule = [rng.choice([9500, 12000, 18000, 41000, 65000]) for _ in range(1000)]
        self.assertEqual(monitor.benford_test(fee_schedule)['status'], 'not_applicable')
        narrow = [rng.randint(15000, 60000) for _ in range(1000)]                  # less than two orders of magnitude
        self.assertEqual(monitor.benford_test(narrow)['status'], 'not_applicable')


def claim(cid, pid, org, code, billed, day='2029-03-01', payer='PAY1'):
    # (claim_id, patient, payer, service_date, first_status, final_status, billed, allowed, first_available, org, service_code)
    return (cid, pid, payer, day, 'paid', 'paid', billed, billed // 2, day, org, code)


class ClaimsIntegrity(unittest.TestCase):
    def test_peer_outlier_in_billing_intensity_for_service_mix(self):
        rng = random.Random(4)
        rows, i = [], 0
        for k in range(8):
            factor = 1.6 if k == 3 else 1.0
            for _ in range(60):
                code, base = rng.choice([('eval', 20000), ('lab', 3000)])
                rows.append(claim(f'c{i}', f'p{i % 40}', f'ORG-{k}', code, round(base * factor * rng.uniform(0.95, 1.05)))); i += 1
        r = monitor.peer_outliers(rows)
        flagged = [x['organization'] for x in r['rows'] if x['status'] == 'review_flag']
        self.assertEqual(flagged, ['ORG-3'])
        top = r['rows'][0]
        self.assertGreater(top['effect_vs_peers'], 1.4)

    def test_fee_schedule_identical_billing_raises_nothing(self):
        rows = [claim(f'c{i}', f'p{i}', f'ORG-{i % 6}', 'eval', 20000 + (i % 6) * 50) for i in range(600)]  # <2% differences
        self.assertFalse(any(x['status'] == 'review_flag' for x in monitor.peer_outliers(rows)['rows']))

    def test_duplicate_heuristics(self):
        rows = [claim('a', 'p1', 'ORG', 'eval', 20000), claim('b', 'p1', 'ORG', 'eval', 20000),        # exact duplicate
                claim('c', 'p2', 'ORG', 'eval', 20000), claim('d', 'p2', 'ORG', 'eval', 21000, day='2029-03-02'),  # near pair
                claim('e', 'p3', 'ORG', 'eval', 20000), claim('f', 'p3', 'ORG', 'eval', 20000, day='2029-05-01')]  # repeat visit
        r = monitor.duplicate_claims(rows)
        self.assertEqual((r['exact_sets'], r['extra_copies'], r['near_pairs']), (1, 1, 1))
        self.assertEqual(r['examples'][0]['claim_ids'], ['a', 'b'])


class Ranking(unittest.TestCase):
    def test_information_times_impact(self):
        self.assertEqual(monitor.score(0, 1000), 0)
        self.assertGreater(monitor.score(20, 1000), monitor.score(20, 10))
        self.assertGreater(monitor.score(30, 10), monitor.score(10, 10))
        self.assertAlmostEqual(monitor.kl_bits([0.5, 0.5], [0.5, 0.5]), 0.0)
        cards = [briefing.rank({'strength': 3.0, 'patients': 50}), briefing.rank({'strength': 3.0, 'patients': 5}),
                 briefing.rank({'strength': 1.0, 'patients': 50, 'evidence': {'q': 1e-6}})]
        self.assertGreater(cards[0]['score'], cards[1]['score'])
        self.assertAlmostEqual(cards[2]['surprise_bits'], -math.log2(1e-6), places=1)   # BH q drives discovery surprise

    def test_monitor_cards_skip_echoes_and_noise(self):
        watch = {'rows': [
            {'kind': 'data_feed', 'category': 'event_type_gap', 'series': 'ed_rate', 'stream': 'encounters', 'group': 'all', 'group_by': 'none',
             'salient': True, 'echo': False, 'excess_bits': 20.0, 'surprise_bits': 34.0, 'headline': 'ED gap', 'patients': 100, 'dollars': None,
             'follow_up': 'Is ED use out of control?', 'months': ['2027-01..2027-09'], 'rules': ['zero_run'], 'look_elsewhere_bits': 14.0},
            {'kind': 'data_feed', 'category': 'feed_gap', 'series': 'ed_rate', 'group': 'all', 'group_by': 'none', 'salient': True, 'echo': False,
             'excess_bits': 20.0, 'surprise_bits': 34.0, 'headline': 'same gap via the chart', 'patients': 100, 'dollars': None, 'follow_up': 'x'},
            {'kind': 'data_feed', 'category': 'feed_gap', 'series': 'ed_rate', 'group': 'P1', 'group_by': 'practice', 'salient': True, 'echo': True,
             'excess_bits': 20.0, 'surprise_bits': 34.0, 'headline': 'practice echo', 'patients': 10, 'dollars': None, 'follow_up': 'x'},
            {'kind': 'rate', 'category': 'rate_shift', 'series': 'pmpm', 'group': 'all', 'group_by': 'none', 'salient': False, 'echo': False,
             'excess_bits': -3.0, 'surprise_bits': 11.0, 'headline': 'noise', 'patients': 100, 'dollars': None, 'follow_up': 'x'}]}
        cards = briefing.monitor_cards(watch)
        self.assertEqual([c['headline'] for c in cards], ['ED gap'])
        self.assertEqual(cards[0]['kind'], 'data gap')


# ---- end to end on a small semantic store ----------------------------------------------------
def months_between(start, n):
    y, m = int(start[:4]), int(start[5:7])
    out = []
    for _ in range(n):
        out.append(f'{y:04d}-{m:02d}'); m += 1
        if m == 13: y, m = y + 1, 1
    return out


def fixture():
    """60 members on two practices from 2026-09, 35 months of visits and labs. Planted problems:
    ED events vanish for 2027-11..2028-04 while primary care keeps flowing (feed gap), one ED month at 8x (spike),
    LAB2 sends nothing for 2028-05..2028-09 (source gap), and practice PR2's admissions double for the last 9 months (rate)."""
    rng = random.Random(11)
    patients = {f'p{i}': {'age_reported': 40 + i % 40, 'sex_reported': 'F' if i % 2 else 'M'} for i in range(60)}
    recs = []
    for i in range(60):
        pid = f'p{i}'
        recs.append((pid, 'coverage', {'coverage_type': 'medicare' if i % 3 else 'commercial', 'status': 'active',
                                       'effective_start': '2026-09-01', 'event_date': '2026-09-01'}))
        recs.append(pcp(pid, 'drA' if i < 30 else 'drB', 'PR1' if i < 30 else 'PR2'))
    ms = months_between('2026-09', 35)
    gap = set(months_between('2027-11', 6)); lab_gap = set(months_between('2028-05', 5))
    for k, m in enumerate(ms):
        for i in range(60):
            pid = f'p{i}'
            if rng.random() < 0.5:
                recs.append((pid, 'encounters', {'type': 'primary_care', 'event_date': f'{m}-1{i % 9}', 'organization_id': 'PR1' if i < 30 else 'PR2',
                                                 'encounter_id': f'pc-{pid}-{m}'}))
            recs.append((pid, 'labs', {'test': 'HbA1c', 'value': 7.0, 'unit': '%', 'event_date': f'{m}-0{1 + i % 9}',
                                       'organization_id': 'LAB1' if i % 2 else 'LAB2'}))
        recs = [r for r in recs if not (r[1] == 'labs' and r[2].get('organization_id') == 'LAB2' and r[2]['event_date'][:7] in lab_gap)]
        n_ed = 0 if m in gap else (48 if m == '2027-06' else 6)
        for j in range(n_ed):
            pid = f'p{(k * 7 + j) % 60}'
            recs.append((pid, 'encounters', {'type': 'emergency', 'event_date': f'{m}-{10 + j % 18:02d}', 'organization_id': 'H1',
                                             'encounter_id': f'ed-{m}-{j}'}))
        late = k >= len(ms) - 9
        for j in range(4 if late else 2):
            pid = f'p{30 + (k * 3 + j) % 30}'
            recs.append((pid, 'encounters', {'type': 'inpatient', 'event_date': f'{m}-0{2 + j}', 'organization_id': 'H1',
                                             'encounter_id': f'ip-{m}-{j}', 'end_time': f'{m}-0{3 + j}T10:00:00'}))
        for j in range(2):
            pid = f'p{j}'
            recs.append((pid, 'encounters', {'type': 'inpatient', 'event_date': f'{m}-2{j}', 'organization_id': 'H1',
                                             'encounter_id': f'ipa-{m}-{j}', 'end_time': f'{m}-2{j + 2}T10:00:00'}))
    for i in range(400):  # claims with a planted exact duplicate
        pid = f'p{i % 60}'; m = ms[i % len(ms)]
        recs.append((pid, 'medical_claims', {'claim_id': f'C{i}', 'service_date': f'{m}-05', 'event_date': f'{m}-05', 'available_date': f'{m}-25',
                                             'status': 'denied' if i % 17 == 0 else 'paid', 'billed_cents': 20000 + (i % 7) * 1000,
                                             'allowed_cents': 9000, 'payer_paid_cents': 8000, 'source_system': 'PAY1' if i % 2 else 'PAY2',
                                             'service_organization_id': 'PR1' if i % 2 else 'PR2', 'service_code': 'eval'}))
    recs.append(('p0', 'medical_claims', {'claim_id': 'CDUP', 'service_date': f'{ms[0]}-05', 'event_date': f'{ms[0]}-05',
                                          'available_date': f'{ms[0]}-25', 'status': 'paid', 'billed_cents': 20000, 'allowed_cents': 9000,
                                          'payer_paid_cents': 8000, 'source_system': 'PAY2', 'service_organization_id': 'PR2', 'service_code': 'eval'}))
    path = build(recs, patients)
    semantic_layer.build(path)
    return path


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = fixture()
        cls.all = monitor.execute(cls.path, None, {'check': 'all'}, AS_OF)

    def finding(self, report, **match):
        return [f for f in report['rows'] if all(f.get(k) == v for k, v in match.items())]

    def test_event_type_gap_is_a_data_feed_finding(self):
        gaps = self.finding(self.all, category='event_type_gap', series='ed_rate')
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]['months'], ['2027-11..2028-04'])
        self.assertTrue(gaps[0]['salient'])
        chart = self.finding(self.all, series='ed_rate', group='all', kind='rate')
        self.assertFalse(any('2027-11' in m or '2028-01' in m for f in chart for m in f['months']))  # never read as a care change

    def test_single_month_spike_is_classified_as_data(self):
        spikes = self.finding(self.all, series='ed_rate', group='all', category='volume_spike')
        self.assertTrue(spikes and spikes[0]['months'][0].startswith('2027-06'))
        self.assertEqual(spikes[0]['kind'], 'data_feed')

    def test_source_system_gap_in_lab_feed(self):
        r = monitor.execute(self.path, None, {'check': 'feed', 'stream': 'labs', 'series': ['lab_volume'], 'group_by': 'source_system'}, AS_OF)
        feed = [f for f in r['rows'] if f.get('stream') == 'labs' and f['category'] == 'feed_gap']
        self.assertTrue(feed)
        self.assertIn('LAB2', feed[0]['sources'])
        self.assertNotIn('LAB1', feed[0]['sources'])
        status = {c['group']: c['status'] for c in r['chart_status']}
        self.assertEqual(status.get('LAB1'), 'in_control')
        self.assertEqual(status.get('LAB2'), 'data_feed_anomaly')

    def test_rate_shift_by_practice_is_a_rate_signal(self):
        r = monitor.execute(self.path, None, {'check': 'control_chart', 'series': ['admit_rate'], 'group_by': 'practice'}, AS_OF)
        status = {c['group']: c for c in r['chart_status']}
        self.assertEqual(status['PR2']['status'], 'rate_signal')
        self.assertNotEqual(status['PR1']['status'], 'rate_signal')

    def test_claims_checks_report_duplicates_and_guards(self):
        r = monitor.execute(self.path, None, {'check': 'claims'}, AS_OF)
        self.assertEqual(r['integrity']['duplicates']['extra_copies'], 1)
        self.assertTrue(all(t['status'] in ('insufficient', 'not_applicable', 'low_power') for t in r['integrity']['benford']))
        self.assertFalse(self.finding(r, category='benford_nonconformity'))  # priced amounts never become accusations

    def test_findings_carry_information_and_follow_ups_that_compile_back(self):
        profile = source_profile.load(self.path)
        for f in self.all['rows'][:10]:
            self.assertEqual(f['excess_bits'], round(f['surprise_bits'] - self.all['look_elsewhere_bits'], 2))
            g, info = local_compiler.compile(f['follow_up'], profile, AS_OF)
            self.assertEqual(g['nodes'][-1]['operation'], 'monitor', (f['follow_up'], info))
        scores = [f['score'] for f in self.all['rows']]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_graph_validate_and_answer_contract(self):
        plan = {'interpretation': 'Which practices are out of control on admissions?', 'coverage': 'supported_observation', 'unmet_requirements': [],
                'nodes': [{'id': 'answer', 'operation': 'monitor', 'inputs': [], 'purpose': 'SPC',
                           'parameters': {'check': 'control_chart', 'series': ['admit_rate'], 'group_by': 'practice'}}]}
        plan = validate(plan, AS_OF)
        result = monitor.execute(self.path, None, plan['nodes'][0]['parameters'], AS_OF)
        answer = build_answer(plan, [{'node': plan['nodes'][0], 'result': result}], {}, plan['interpretation'])
        self.assertEqual((answer['status'], answer['answer_type']), ('answered', 'table'))
        self.assertIn('PR2', answer['value'])
        self.assertTrue(headline(answer).startswith('1 of'))
        bad = json.loads(json.dumps(plan)); bad['nodes'][0]['parameters'] = {'check': 'control_chart', 'period': {'start': '2030-01-01'}}
        with self.assertRaises(ValueError): validate(bad, AS_OF)
        bad['nodes'][0]['parameters'] = {'check': 'astrology'}
        with self.assertRaises(ValueError): validate(bad, AS_OF)


class Compile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # (adt present: the discovery route reads its stream profile unconditionally)
        cls.path = build([('p1', 'labs', {'test': 'HbA1c', 'value': 7, 'unit': '%', 'event_date': '2028-01-01'}),
                          ('p1', 'adt', {'event': 'emergency_registration', 'event_date': '2028-01-01'}),
                          ('p1', 'encounters', {'type': 'emergency', 'event_date': '2028-01-01', 'organization_id': 'H1'}),
                          ('p1', 'medical_claims', {'claim_id': 'c', 'billed_cents': 100, 'status': 'paid', 'event_date': '2028-01-01'})],
                         {'p1': {'age_reported': 50}})
        cls.profile = source_profile.load(cls.path)

    def params(self, q):
        g, info = local_compiler.compile(q, self.profile, AS_OF)
        self.assertIsNotNone(g, info)
        self.assertEqual(g['nodes'][-1]['operation'], 'monitor', info)
        return g['nodes'][-1]['parameters']

    def test_natural_questions_compile_to_monitor(self):
        p = self.params('Which practices are out of control on ED use?')
        self.assertEqual((p['check'], p['series'], p['group_by']), ('control_chart', ['ed_rate'], 'practice'))
        p = self.params('Any anomalies in our claims this year?')
        self.assertEqual((p['check'], p['period']), ('claims', {'start': '2029-01-01', 'end': AS_OF}))
        p = self.params('Run a Benford check on billed amounts by payer')
        self.assertEqual((p['check'], p['amount'], p['group_by']), ('benford', 'billed', 'payer'))
        p = self.params('Is the lab feed from any source system behaving abnormally?')
        self.assertEqual((p['check'], p['stream'], p['group_by'], p['series']), ('feed', 'labs', 'source_system', ['lab_volume']))
        p = self.params('Are there duplicate claims?')
        self.assertEqual(p['check'], 'duplicates')
        p = self.params('Which payers are out of control on claim denials?')
        self.assertEqual((p['series'], p['group_by']), (['denial_rate'], 'payer'))
        p = self.params('Show an EWMA control chart of admissions by practice')
        self.assertEqual((p['method'], p['series']), ('ewma', ['admit_rate']))

    def test_other_questions_keep_their_routes(self):
        for q, op in (('What is our allowed PMPM by payer?', 'vbc'), ('Why did ED visits go up?', 'vbc'),
                      ('Where are ED visits concentrated?', 'discover')):
            g, info = local_compiler.compile(q, self.profile, AS_OF)
            self.assertEqual(g['nodes'][-1]['operation'], op, (q, info))


def _stack_ready():
    try:
        db = sqlite3.connect(f"file:{STACK / 'semantic.sqlite3'}?mode=ro", uri=True)
        meta = dict(db.execute('SELECT key, value FROM meta')); db.close()
        return meta.get('version') == semantic_layer.VERSION and \
            meta.get('source_digest') == json.loads((STACK / 'run.json').read_text())['source_digest']
    except Exception:
        return False


class RealRelease(unittest.TestCase):
    """Generator defects D2-D4 (SYNTHETIC_DATA_ISSUES.md) must surface as data-feed findings. Runs only when the release's
    semantic layer is already current (never triggers a rebuild); set MONITOR_STACK to point at a copy."""
    @unittest.skipUnless(_stack_ready(), 'synthetic release semantic layer not present or not current')
    def test_known_generator_gaps_are_caught(self):
        r = monitor.execute(STACK, None, {'check': 'all'})
        heads = [f for f in r['rows'] if f['salient']]
        ed_gap = next(f for f in heads if f['category'] == 'event_type_gap' and f['series'] == 'ed_rate')
        self.assertEqual(ed_gap['months'], ['2026-11..2028-09'])                                       # D2
        self.assertTrue(any(f.get('stream') == 'social_screens' and f['category'] == 'feed_gap' for f in heads))   # D3
        self.assertTrue(any(f['series'] == 'ed_rate' and f['category'] == 'volume_spike' and f['months'][0] == '2026-09'
                            for f in heads))                                                              # D4
        self.assertFalse(any(f['kind'] == 'rate' and f['series'] == 'ed_rate' and f['group'] == 'all' and f['salient'] for f in r['rows']))


if __name__ == '__main__':
    unittest.main()
