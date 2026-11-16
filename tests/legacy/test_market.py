"""Markets view ('market' operation): practice tickers, surprise x impact ranking, shrinkage, feed-gap exclusion, graph
contract, compiler routing, console fields and the briefing card. The fixture plants an idiosyncratic ED shock in one
practice and a network-wide admissions drift in every practice, plus an ED feed gap and a tiny practice with a spike."""
import json, math, os, sqlite3, time, unittest
from pathlib import Path
from unittest import mock

import numpy as np

from clinical_core.agere import ask_view
from clinical_core.agere.clinical import briefing, local_compiler, market, semantic_layer, source_profile
from clinical_core.agere.clinical.analysis_graph import build_answer, headline, validate
from clinical_core.agere.clinical.graph_contracts import input_contract, parameter_schema, TYPED
from clinical_core.agere.clinical.graph_planner import CONTRACTS
from clinical_core.agere.clinical.capability_registry import PRODUCT
from test_aggregate_explain import build, pcp

AS_OF = '2029-08-15'
HERE = Path(__file__).resolve().parents[2]


def months_between(start, n):
    y, m = int(start[:4]), int(start[5:7])
    out = []
    for _ in range(n):
        out.append(f'{y:04d}-{m:02d}'); m += 1
        if m == 13: y, m = y + 1, 1
    return out


MONTHS = months_between('2027-08', 24)           # ends 2029-07, the last complete month before AS_OF
GAP = set(MONTHS[6:11])                          # ED events vanish for five months while visits keep flowing
PRACTICES = ['PR1', 'PR2', 'PR3', 'PR4']


def fixture():
    patients, recs = {}, []
    members = {pr: [f'{pr.lower()}m{i}' for i in range(50)] for pr in PRACTICES}
    members['PR5'] = [f'pr5m{i}' for i in range(4)]   # tiny practice
    for pr, pids in members.items():
        for i, pid in enumerate(pids):
            patients[pid] = {'age_reported': 40 + i % 40, 'sex_reported': 'F' if i % 2 else 'M'}
            recs.append((pid, 'coverage', {'coverage_type': 'medicare' if i % 3 else 'commercial', 'status': 'active',
                                           'effective_start': f'{MONTHS[0]}-01', 'event_date': f'{MONTHS[0]}-01'}))
            recs.append(pcp(pid, f'dr{pr}', pr))
    last = MONTHS[-1]
    for k, m in enumerate(MONTHS):
        for pr in PRACTICES + ['PR5']:
            pids = members[pr]
            for j, pid in enumerate(pids[:20]):   # primary care keeps flowing every month
                recs.append((pid, 'encounters', {'type': 'primary_care', 'event_date': f'{m}-0{1 + j % 9}', 'organization_id': pr,
                                                 'encounter_id': f'pc-{pid}-{m}'}))
            if pr == 'PR5':
                n_ed, n_ip = (3 if m == last else 0), 0
            else:
                n_ed = 0 if m in GAP else (25 if (pr == 'PR3' and m == last) else 5)
                n_ip = 8 if m == last else 4                    # network-wide admissions drift in the last month
            for j in range(n_ed):
                pid = pids[(k * 7 + j) % len(pids)]
                recs.append((pid, 'encounters', {'type': 'emergency', 'event_date': f'{m}-{10 + j % 18:02d}', 'organization_id': 'H1',
                                                 'encounter_id': f'ed-{pr}-{m}-{j}'}))
            for j in range(n_ip):
                pid = pids[(k * 11 + j + 25) % len(pids)]
                recs.append((pid, 'encounters', {'type': 'inpatient', 'event_date': f'{m}-0{1 + j}', 'organization_id': 'H1',
                                                 'encounter_id': f'ip-{pr}-{m}-{j}', 'end_time': f'{m}-0{2 + j}T10:00:00'}))
    path = build(recs, patients)
    semantic_layer.build(path)
    return path


class Units(unittest.TestCase):
    def test_validation(self):
        p = market.validate({})
        self.assertEqual((p['unit'], p['metrics'], p['direction'], p['top']), ('practice', [], 'any', 25))
        for bad in ({'unit': 'zip'}, {'metrics': ['ed_rate', 'ed_rate']}, {'metrics': ['astrology']}, {'direction': 'sideways'},
                    {'period': {'end': '2029/01/01'}}, {'top': 0}, {'top': True}, {'colour': 'red'}):
            with self.assertRaises(ValueError, msg=bad): market.validate(bad)
        self.assertEqual(market.validate({'metrics': 'pmpm'})['metrics'], ['pmpm'])

    def test_gap_detection_splits_regimes_and_drops_spikes(self):
        months = months_between('2027-01', 20)
        num = np.array([5.0] * 20); den = np.array([100.0] * 20)
        num[5:9] = 0       # feed gap: zero run while exposure flows
        num[12] = 60       # single-month 12x spike
        num[19] = 0; num[18] = 0  # trailing zero run: the feed stopped, not care
        reason, regime = market.data_gaps(num, den, 'count', months)
        self.assertTrue(all(reason[i] == 'zero_run' for i in range(5, 9)))
        self.assertEqual(reason[12], 'volume_spike')
        self.assertEqual((reason[18], reason[19]), ('zero_run', 'zero_run'))
        self.assertEqual(regime, [9, 10, 11, 13, 14, 15, 16, 17])   # after the last gap; the spike month is left out
        self.assertFalse(set(regime) & {18, 19})

    def test_shrinkage_pulls_small_units_toward_the_network(self):
        den = {'big': np.full(12, 500.0), 'small': np.full(12, 5.0), 'mid': np.full(12, 200.0)}
        num = {'big': np.full(12, 50.0), 'small': np.full(12, 2.0), 'mid': np.full(12, 22.0)}
        k = market.prior_strength(num, den, 'count', regime=list(range(12)))
        self.assertGreater(k, 0)
        net = sum(num.values()) / sum(den.values())
        small = market.shrunk(num['small'], den['small'], net, k)
        big = market.shrunk(num['big'], den['big'], net, k)
        self.assertLess(abs(small[0] - net[0]), abs(0.4 - net[0]))         # 2/5 = 0.4 raw, pulled toward ~0.1
        self.assertAlmostEqual(big[0], 0.1, delta=0.01)                   # high volume barely moves

    def test_predictive_surprise_counts_and_shares(self):
        p_hi, kl_hi, _ = market.predictive('count', 30, 100, 0.1, 1.0)
        p_lo, kl_lo, _ = market.predictive('count', 11, 100, 0.1, 1.0)
        self.assertLess(p_hi, 1e-4); self.assertGreater(p_lo, 0.5); self.assertGreater(kl_hi, kl_lo)
        p_od, _, _ = market.predictive('count', 30, 100, 0.1, 4.0)       # overdispersion makes the same move less surprising
        self.assertGreater(p_od, p_hi)
        p_s, _, _ = market.predictive('share', 40, 100, 0.2, 1.0)
        self.assertLess(p_s, 1e-4)
        self.assertAlmostEqual(market.expected_rate('share', 0.2, 0.3, 0.3), 0.2, places=6)
        self.assertAlmostEqual(market.expected_rate('count', 0.1, 0.2, 0.4), 0.2, places=9)


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = fixture()
        cls.report = market.execute(cls.path, None, {'metrics': ['ed_rate', 'admit_rate']}, AS_OF)
        cls.by = {(t['unit'], t['metric']): t for t in cls.report['tickers']}

    def test_idiosyncratic_shock_outranks_network_wide_drift(self):
        units = [t for t in self.report['tickers'] if t['unit'] != market.NETWORK]
        top = units[0]
        self.assertEqual((top['unit'], top['metric'], top['month']), ('PR3', 'ed_rate', MONTHS[-1]))
        self.assertEqual(top['move_type'], 'idiosyncratic')
        admits = [t for t in units if t['metric'] == 'admit_rate' and t['unit'] in PRACTICES]
        self.assertTrue(admits)
        self.assertTrue(all(top['surprise_bits'] > a['surprise_bits'] for a in admits))
        self.assertTrue(all(a['move_type'] != 'idiosyncratic' for a in admits))   # moved with the network
        self.assertTrue(all(a['own_bits'] > a['surprise_bits'] for a in admits))
        net = self.report['network']
        self.assertTrue(net['admit_rate']['salient'])
        self.assertIn('network-wide', net['admit_rate']['statement'])
        self.assertIn('PR3', net['ed_rate']['idiosyncratic_units'])
        self.assertNotIn('network-wide', net['ed_rate']['statement'])
        for part in (False, True):   # adequately sized tickers first, each block by score
            scores = [t['score'] for t in self.report['tickers'] if t['low_volume'] == part]
            self.assertEqual(scores, sorted(scores, reverse=True))
        flags = [t['low_volume'] for t in self.report['tickers']]
        self.assertEqual(flags, sorted(flags))

    def test_ticker_statistics(self):
        t = self.by[('PR3', 'ed_rate')]
        self.assertEqual(t['direction'], 'worse')
        self.assertGreater(t['last'], t['prior'])
        self.assertGreater(t['change_baseline_pct'], 0.5)
        self.assertGreater(t['z'], 2)
        self.assertGreater(t['run_up'], 0.5)
        self.assertIsNotNone(t['beta'])
        self.assertGreater(self.by[('PR1', 'admit_rate')]['beta'], 0.5)       # admissions move with the network
        for key in ('trend_per_month', 'volatility', 'kl_bits', 'expected', 'excess_bits', 'impact', 'spark'):
            self.assertIn(key, t)
        self.assertEqual(json.loads(json.dumps(self.report, allow_nan=False, default=str))['schema'], market.VERSION)

    def test_low_volume_units_are_shrunk_and_never_headlined(self):
        t = self.by[('PR5', 'ed_rate')]
        self.assertTrue(t['low_volume'])
        self.assertFalse(t['salient'])
        self.assertLess(t['last'], t['observed_raw'])          # shrunk toward the network, not the raw 3-of-4 spike
        self.assertGreater(t['prior_strength'], 0)
        self.assertNotIn('PR5', [c['evidence']['unit'] for c in market.market_cards(self.report)])

    def test_gap_months_are_excluded(self):
        gaps = self.report['data_gaps']['ed_rate']
        bad = [g for g in gaps if g['reason'] in ('event_type_gap', 'zero_run')]
        self.assertTrue(bad)
        self.assertTrue(any(f"{MONTHS[6]}..{MONTHS[10]}" == g['months'] for g in bad), gaps)
        for t in self.report['tickers']:
            if t['metric'] == 'ed_rate':
                self.assertFalse(set(t['spark_months']) & GAP)
                self.assertGreater(t['spark_months'][0], MONTHS[10])       # the regime starts after the gap
                self.assertNotIn(t['month'], GAP)

    def test_direction_filter_and_skipped_metrics(self):
        r = market.execute(self.path, None, {'direction': 'better', 'metrics': ['ed_rate']}, AS_OF)
        self.assertTrue(all(t['direction'] == 'better' for t in r['tickers']))
        full = market.execute(self.path, None, {}, AS_OF)
        self.assertIn('pmpm', full['skipped_metrics'])                     # no claims in the fixture: said, not invented
        self.assertTrue(full['sectors'])

    def test_graph_validate_and_answer_contract(self):
        plan = {'interpretation': 'What moved this month?', 'coverage': 'supported_observation', 'unmet_requirements': [],
                'nodes': [{'id': 'answer', 'operation': 'market', 'inputs': [], 'purpose': 'market view',
                           'parameters': {'metrics': ['ed_rate', 'admit_rate']}}]}
        plan = validate(plan, AS_OF)
        self.assertEqual(plan['nodes'][0]['parameters']['unit'], 'practice')
        answer = build_answer(plan, [{'node': plan['nodes'][0], 'result': self.report}], {}, plan['interpretation'])
        self.assertEqual((answer['status'], answer['answer_type']), ('answered', 'table'))
        self.assertIn('PR3', answer['value'])
        self.assertEqual(answer['table'][0]['unit'], self.report['tickers'][0]['unit'])
        self.assertTrue({'network', 'sectors', 'data_gaps', 'tests_examined'} <= set(answer['overall']))
        self.assertTrue(headline(answer))
        json.dumps(answer, allow_nan=False, default=str)
        bad = json.loads(json.dumps(plan)); bad['nodes'][0]['parameters'] = {'period': {'end': '2030-01-01'}}
        with self.assertRaises(ValueError): validate(bad, AS_OF)
        bad['nodes'][0]['parameters'] = {'unit': 'galaxy'}
        with self.assertRaises(ValueError): validate(bad, AS_OF)
        with_pop = {'interpretation': 'x', 'coverage': 'supported_observation', 'unmet_requirements': [],
                    'nodes': [{'id': 'population', 'operation': 'cohort', 'inputs': [], 'purpose': 'all',
                               'parameters': {'specification': {'all': [], 'any': [], 'exclude': []}}},
                              {'id': 'answer', 'operation': 'market', 'inputs': ['population'], 'purpose': 'm', 'parameters': {}}]}
        validate(with_pop, AS_OF)

    def test_console_ticker_board_fields(self):
        html = ask_view.page()
        self.assertIn('function vizMarket', html)
        self.assertIn('function sparkline', html)
        self.assertEqual(html.count('</script>'), 2)
        plan = validate({'interpretation': 'm', 'coverage': 'supported_observation', 'unmet_requirements': [],
                         'nodes': [{'id': 'answer', 'operation': 'market', 'inputs': [], 'purpose': 'm', 'parameters': {}}]}, AS_OF)
        a = build_answer(plan, [{'node': plan['nodes'][0], 'result': self.report}], {}, 'm')
        a = json.loads(json.dumps(a, allow_nan=False, default=str))
        row = a['table'][0]
        for k in ('unit', 'metric', 'month', 'spark', 'last', 'change_prior_pct', 'change_baseline_pct', 'z', 'beta', 'surprise_bits',
                  'move_type', 'direction', 'low_volume', 'follow_up'):
            self.assertIn(k, row)
        self.assertTrue(a['overall']['network'] and a['overall']['data_gaps'])

    def test_briefing_card_present(self):
        cards = market.market_cards(self.report)
        self.assertTrue(cards)
        self.assertTrue(all(c['kind'] == 'market move' for c in cards))
        self.assertTrue(any(c['evidence']['unit'] == 'PR3' for c in cards))
        with mock.patch.object(briefing, 'DISCOVERY', []), mock.patch.object(briefing, 'TRAJECTORIES', []), \
                mock.patch.object(briefing, 'FORECASTS', []), mock.patch.object(briefing, 'CHANGES', []), \
                mock.patch.object(market, 'execute', lambda path, ids, p, as_of=None: self.report):
            b = briefing.build(self.path)
        moves = [c for c in b['cards'] if c['kind'] == 'market move']
        self.assertTrue(moves)
        self.assertTrue(all('score' in c and 'surprise_bits' in c for c in b['cards']))   # ranked with the other cards
        profile = source_profile.load(self.path)
        for c in moves:
            g, info = local_compiler.compile(c['follow_up'], profile, AS_OF)
            self.assertEqual(g['nodes'][-1]['operation'], 'market', (c['follow_up'], info))


class Compile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = build([('p1', 'labs', {'test': 'HbA1c', 'value': 7, 'unit': '%', 'event_date': '2028-01-01'}),
                          ('p1', 'adt', {'event': 'emergency_registration', 'event_date': '2028-01-01'}),
                          ('p1', 'encounters', {'type': 'emergency', 'event_date': '2028-01-01', 'organization_id': 'H1'}),
                          ('p1', 'medical_claims', {'claim_id': 'c', 'billed_cents': 100, 'status': 'paid', 'event_date': '2028-01-01'})],
                         {'p1': {'age_reported': 50}})
        cls.profile = source_profile.load(cls.path)

    def compile(self, q):
        return local_compiler.compile(q, self.profile, AS_OF)

    def params(self, q):
        g, info = self.compile(q)
        self.assertIsNotNone(g, info)
        self.assertEqual(g['nodes'][-1]['operation'], 'market', (q, info))
        return g['nodes'][-1]['parameters']

    def test_phrasings_compile_to_market(self):
        self.assertEqual(self.params('What moved this month?')['unit'], 'practice')
        self.assertEqual(self.params('Give me the market view of our practices')['metrics'], [])
        p = self.params('Which practices are trending worse on ED use and cost?')
        self.assertEqual((p['metrics'], p['direction']), (['ed_rate', 'pmpm'], 'worse'))
        p = self.params('Show me practice tickers for PMPM, ED rate, admissions and care-gap closure')
        self.assertEqual(set(p['metrics']), {'pmpm', 'ed_rate', 'admit_rate', 'a1c_poor', 'bp_control'})
        self.params("What's the most surprising change across the network?")
        self.assertEqual(self.params('Who are the top movers among our PCPs?')['unit'], 'pcp')
        p = self.params('Show me payer tickers for denials')
        self.assertEqual((p['unit'], p['metrics']), ('payer', ['denial_rate']))
        p = self.params('Which doctors are trending better on BP control?')
        self.assertEqual((p['unit'], p['direction'], p['metrics']), ('pcp', 'better', ['bp_control']))

    def test_acceptance_phrasings(self):
        cases = json.loads((HERE / 'bench' / 'acceptance' / 'market.json').read_text())
        self.assertEqual(len(cases), 8)
        for c in cases:
            self.assertEqual(c['expect_operation'], 'market')
            self.params(c['question'])

    def test_unclear_and_other_questions_are_not_taken(self):
        for q in ('What moved in the zebra feed?', 'Show me tickers for hemoglobin'):
            g, info = self.compile(q)
            self.assertTrue(g is None or g['nodes'][-1]['operation'] != 'market', (q, info))
        for q, op in (('Which practices are out of control on ED use?', 'monitor'), ('Is PMPM out of control?', 'monitor'),
                      ('Why did ED visits go up?', 'vbc'), ('What is our allowed PMPM by payer?', 'vbc'),
                      ('Where are ED visits concentrated?', 'discover')):
            g, info = self.compile(q)
            self.assertEqual(g['nodes'][-1]['operation'], op, (q, info))


class Registry(unittest.TestCase):
    def test_integrated_everywhere(self):
        self.assertIn('market', TYPED)
        self.assertEqual(input_contract('market')['count'], [0, 1])
        self.assertIn('market', CONTRACTS)
        self.assertEqual(PRODUCT['market']['population'], 'optional')
        schema = parameter_schema('market', {'cohort_fields': {'record_fields': {}}, 'evidence_catalog': {}})
        self.assertEqual(set(schema['properties']), {'unit', 'metrics', 'direction', 'period', 'top'})
        self.assertEqual(set(schema['properties']['metrics']['items']['enum']), set(market.METRICS))


def _stack():
    for base in [Path(os.environ['MARKET_STACK'])] if os.environ.get('MARKET_STACK') else \
            [HERE / 'artifacts' / 'agere-healthcare-v123' / 'stack'] + \
            [p / 'artifacts' / 'agere-healthcare-v123' / 'stack' for p in HERE.parents]:
        try:
            db = sqlite3.connect(f"file:{base / 'semantic.sqlite3'}?mode=ro", uri=True)
            meta = dict(db.execute('SELECT key, value FROM meta')); db.close()
            if meta.get('version') == semantic_layer.VERSION and \
                    meta.get('source_digest') == json.loads((base / 'run.json').read_text())['source_digest']:
                return base
        except Exception:
            continue
    return None


STACK = _stack()


class RealRelease(unittest.TestCase):
    """Read-only smoke on the synthetic release: runs only when its semantic layer is already current (never rebuilds).
    Generator defects (ED/inpatient absent 2026-11..2028-09, ED spike 2026-09) must be excluded, never reported as moves."""
    @unittest.skipUnless(STACK, 'synthetic release semantic layer not present or not current')
    def test_real_stack_market_view(self):
        started = time.perf_counter()
        r = market.execute(STACK, None, {})
        self.assertLess(time.perf_counter() - started, 60)
        ed = {g['months']: g['reason'] for g in r['data_gaps']['ed_rate']}
        self.assertEqual(ed.get('2026-11..2028-09'), 'event_type_gap')
        self.assertEqual(ed.get('2026-09'), 'volume_spike')
        gap = set(months_between('2026-11', 23)) | {'2026-09'}
        for t in r['tickers']:
            if t['metric'] in ('ed_rate', 'admit_rate'):
                self.assertFalse(set(t['spark_months']) & gap, t['unit'])
        self.assertTrue(r['tickers'])
        self.assertTrue(all(math.isfinite(t['score']) for t in r['tickers']))
        value, table = market.summarize(r)
        self.assertTrue(value and table)
        json.dumps(r, allow_nan=False, default=str)


if __name__ == '__main__':
    unittest.main()
