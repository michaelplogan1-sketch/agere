"""claims_review: payment integrity and revenue leakage, every flag ranked by expected dollars."""
import json, math, os, random, re, shutil, sqlite3, subprocess, unittest
from collections import Counter
from pathlib import Path

from clinical_core.agere.clinical import (analysis_graph, capability_registry, claims_review as cr, graph_contracts, graph_planner,
                                          local_compiler, semantic_layer, source_profile)
from clinical_core.agere.clinical.analysis_graph import build_answer, headline, validate
from test_aggregate_explain import build

AS_OF = '2029-08-15'
ROOT = Path(__file__).resolve().parents[2]
# The release lives in the main checkout; a worktree checkout has no artifacts of its own.
REAL_STACK = Path(os.environ.get('CLAIMS_REVIEW_STACK') or next(
    (p / 'artifacts' / 'agere-healthcare-v123' / 'stack' for p in (ROOT, *ROOT.parents) if (p / 'artifacts' / 'agere-healthcare-v123' / 'stack').exists()),
    ROOT / 'artifacts' / 'agere-healthcare-v123' / 'stack'))
CODES = {'practice': [('office_visit', 0.7, 180.0), ('vaccine_admin', 0.15, 60.0), ('venipuncture', 0.15, 25.0)],
         'specialty': [('consult', 0.8, 320.0), ('office_visit', 0.2, 180.0)], 'lab': [('lab_panel', 1.0, 90.0)]}
PAYERS = {'PAY-0': 0.62, 'PAY-1': 0.66, 'PAY-2': 0.70}


def simulate(seed, intensity=None, underpaid=0, mix=None, denial=None, duplicates=0):
    """In-memory claims shaped like claims_review.load(). intensity / mix / denial = provider ids to plant; underpaid = how
    many paid claims to pay at about half the payer's usual share of billed; duplicates = exact copies to add."""
    rng = random.Random(seed)
    providers = [(f'X-PRACTICE-{i}', 'practice') for i in range(8)] + [(f'X-SPECIALTY-{i}', 'specialty') for i in range(5)] + \
                [(f'X-LAB-{i}', 'lab') for i in range(4)]
    claims, n = [], 0
    for org, st in providers:
        codes = CODES[st]
        if mix == org: codes = [('vaccine_admin', 0.6, 60.0), ('office_visit', 0.25, 180.0), ('venipuncture', 0.15, 25.0)]
        for k in range(rng.randint(70, 110)):
            pid = f'{org}-pt{k}'
            for _ in range(1 + int(rng.expovariate(1 / 2.0))):
                r, acc = rng.random(), 0.0
                for code, share, base in codes:
                    acc += share
                    if r <= acc: break
                payer = rng.choice(sorted(PAYERS))
                billed = round(base * math.exp(rng.gauss(0, 0.12)) * (1.6 if org == intensity else 1.0), 2)
                ratio = PAYERS[payer] + rng.gauss(0, 0.012)
                day = f'2028-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}'
                p_deny = 0.18 if org == denial else 0.05
                n += 1
                claims.append(claim(f'C{n}', pid, payer, org, st, code, day, billed, round(billed * ratio, 2), rng.random() < p_deny, rng))
    paid = [c for c in claims if c['final_status'] == 'paid' and c['service_code'] == 'office_visit']
    planted = rng.sample(paid, underpaid) if underpaid else []
    for c in planted:
        c['allowed'] = round(c['allowed'] * 0.5, 2)
        c['history'][-1]['allowed'] = c['allowed']; c['live_payment'] = c['history'][-1]
    for i, c in enumerate(rng.sample([c for c in claims if c['final_status'] == 'paid'], duplicates) if duplicates else []):
        dup = json.loads(json.dumps({k: v for k, v in c.items() if k not in ('touches', 'live_payment', 'reversed_payments')}))
        dup['claim_id'] = f'DUP{i}'; dup['touches'] = Counter(); claims.append(cr.settle(dup))
    return claims, {c['claim_id'] for c in planted}


def claim(cid, pid, payer, org, st, code, day, billed, allowed, denied, rng):
    h = []
    if denied:
        h.append({'status': 'denied', 'date': day, 'paid': 0.0, 'allowed': 0.0, 'patient_responsibility': 0.0, 'record_id': cid + 'a', 'reverses': None})
        if rng.random() < 0.75:
            later = f'{day[:8]}{min(28, int(day[8:]) + 1):02d}' if day < '2028-12' else day
            h.append({'status': 'paid', 'date': later, 'paid': allowed, 'allowed': allowed, 'patient_responsibility': 0.0, 'record_id': cid + 'b', 'reverses': None})
    else:
        h.append({'status': 'paid', 'date': day, 'paid': allowed, 'allowed': allowed, 'patient_responsibility': 0.0, 'record_id': cid + 'a', 'reverses': None})
    final = h[-1]['status']
    return cr.settle({'claim_id': cid, 'patient_id': pid, 'payer': payer, 'provider': org, 'setting': st, 'service_code': code,
                      'service_date': day, 'billed': billed, 'allowed': allowed if final == 'paid' else 0.0, 'paid': allowed if final == 'paid' else 0.0,
                      'first_status': h[0]['status'], 'final_status': final, 'ever_denied': denied, 'denied_then_paid': denied and final == 'paid',
                      'history': h, 'denial_reasons': ['CO-16' if rng.random() < 0.6 else 'CO-197'] if denied else [], 'touches': Counter()})


def flags(report, category=None, subject=None):
    return [f for f in report['flags'] if (category is None or f['category'] == category) and (subject is None or f['subject'] == subject)]


class Statistics(unittest.TestCase):
    def test_benjamini_hochberg_matches_the_step_up_definition(self):
        q = cr.bh([0.01, 0.04, 0.03, 0.5])
        self.assertEqual([round(x, 4) for x in q], [0.04, 0.0533, 0.0533, 0.5])
        self.assertEqual(cr.bh([]), [])

    def test_second_digit_law_sums_to_one_and_detects_fabrication(self):
        self.assertAlmostEqual(sum(cr.SECOND_DIGIT), 1.0, places=9)
        rng = random.Random(1)
        natural = [math.exp(rng.uniform(0, 10)) for _ in range(5000)]
        self.assertEqual(cr.benford_second(natural)['conformity'] in ('close_conformity', 'acceptable_conformity', 'marginal_conformity'), True)
        rounded = [rng.choice([100, 150, 200, 250, 300]) * rng.choice([1, 10, 100]) for _ in range(5000)]
        self.assertEqual(cr.benford_second(rounded)['conformity'], 'nonconformity')
        self.assertEqual(cr.benford_second(natural[:50])['status'], 'insufficient')


class Simulation(unittest.TestCase):
    def test_planted_outlier_provider_is_recovered(self):
        claims, _ = simulate(11, intensity='X-PRACTICE-3')
        r = cr.review(claims, {'focus': 'providers'}, AS_OF)
        hit = flags(r, 'provider_billing_intensity')
        self.assertEqual([f['subject'] for f in hit], ['X-PRACTICE-3'])
        f = hit[0]
        self.assertGreater(f['evidence']['effect'], 1.4)
        self.assertGreater(f['dollars_at_stake'], 0)
        self.assertAlmostEqual(f['expected_dollars'], f['dollars_at_stake'] * f['probability'], delta=0.001 * f['dollars_at_stake'] + 0.01)
        self.assertNotIn('fraud', f['reason'].lower())

    def test_planted_service_mix_and_denial_outliers_are_recovered(self):
        claims, _ = simulate(5, mix='X-PRACTICE-6', denial='X-SPECIALTY-2')
        r = cr.review(claims, {'focus': 'providers'}, AS_OF)
        self.assertIn('X-PRACTICE-6', [f['subject'] for f in flags(r, 'provider_service_mix')])
        self.assertIn('X-SPECIALTY-2', [f['subject'] for f in flags(r, 'provider_denial_rate')])

    def test_planted_underpayments_are_recovered_with_dollars(self):
        claims, planted = simulate(7, underpaid=12)
        r = cr.review(claims, {'focus': 'underpayment'}, AS_OF)
        found = {f['evidence']['claim_id'] for f in flags(r, 'underpayment')}
        self.assertGreaterEqual(len(found & planted), 11)
        self.assertLessEqual(len(found - planted), 1)
        f = flags(r, 'underpayment')[0]
        lo, hi = f['evidence']['expected_interval']
        self.assertLess(lo, f['evidence']['expected']); self.assertGreater(hi, f['evidence']['expected'])
        self.assertLess(f['evidence']['allowed'], lo)
        self.assertGreater(r['underpayment']['shortfall'], 0)
        # Payer-specific expectations: the cells keep each payer's own rate (0.62 / 0.66 / 0.70 of billed).
        centers = {c['payer']: c['expected_center'] for c in r['underpayment']['cells'] if c['service_code'] == 'office_visit' and c['setting'] == 'practice'}
        self.assertAlmostEqual(centers['PAY-0'], 0.62, delta=0.01); self.assertAlmostEqual(centers['PAY-2'], 0.70, delta=0.01)

    def test_bh_controls_false_flags_on_null_data(self):
        statistical = ('provider_billing_intensity', 'provider_services_per_patient', 'provider_denial_rate', 'provider_service_mix', 'underpayment')
        false = []
        for seed in range(12):
            claims, _ = simulate(100 + seed)
            r = cr.review(claims, {'focus': 'all'}, AS_OF)
            false.append(sum(1 for f in r['flags'] if f['category'] in statistical))
        # Under the global null the FDR equals the familywise rate: at q = 0.05 most runs flag nothing.
        self.assertLessEqual(sum(false), 3, false)
        self.assertGreaterEqual(sum(x == 0 for x in false), 10, false)

    def test_flags_are_ranked_by_expected_dollars(self):
        claims, _ = simulate(3, intensity='X-SPECIALTY-1', underpaid=6, duplicates=3)
        r = cr.review(claims, {'focus': 'all'}, AS_OF)
        exp = [f['expected_dollars'] for f in r['flags'] if f['expected_dollars'] is not None]
        self.assertEqual(exp, sorted(exp, reverse=True))
        self.assertEqual([f['rank'] for f in r['flags']], list(range(1, len(r['flags']) + 1)))
        value, table = cr.summarize(r)
        self.assertIn('review flag', value); self.assertIn('never findings of fraud', value)
        self.assertEqual(table[0]['rank'], 1)

    def test_exact_duplicates_carry_high_probability_and_repeats_do_not(self):
        claims, _ = simulate(9, duplicates=5)
        r = cr.review(claims, {'focus': 'duplicates'}, AS_OF)
        dups = [p for p in r['duplicates']['pairs'] if p['kind'] == 'exact' and any(c.startswith('DUP') for c in p['claim_ids'])]
        self.assertEqual(len(dups), 5)
        self.assertTrue(all(p['probability'] >= 0.9 for p in dups))
        self.assertEqual(len(flags(r, 'duplicate_exact')), 5)

    def test_denials_pareto_outcomes_and_resolution_days(self):
        claims, _ = simulate(4)
        r = cr.review(claims, {'focus': 'denials'}, AS_OF)
        d = r['denials']
        self.assertEqual(d['denied_claims'], d['overturned'] + d['written_off_claims'] + d['pending_claims'])
        self.assertEqual(d['pending_claims'], 0)  # every 2028 denial is older than the write-off window
        self.assertEqual([x['reason_code'] for x in d['pareto']][0], 'CO-16')
        self.assertEqual(d['pareto'][-1]['cumulative_share'], 1.0)
        self.assertEqual(d['pareto'][0]['category'], 'missing_information')
        self.assertIn('p_value', d['payer_reason_independence'])
        self.assertTrue(all(x['days_to_resolution'] is None or x['days_to_resolution'] >= 0 for x in cr.denial_review(claims, {}, AS_OF)))
        self.assertTrue(flags(r, 'denial_write_off'))
        self.assertEqual(r['leakage_pareto'][0]['kind'], 'denial_write_off')

    def test_repricing_with_reversal_arriving_after_the_replacement(self):
        h = [{'status': 'paid', 'date': '2028-01-10', 'paid': 100.0, 'allowed': 150.0, 'patient_responsibility': 50.0, 'record_id': 'r1', 'reverses': None},
             {'status': 'paid', 'date': '2028-02-10', 'paid': 80.0, 'allowed': 120.0, 'patient_responsibility': 40.0, 'record_id': 'r2', 'reverses': None},
             {'status': 'reversed', 'date': '2028-02-10', 'paid': 100.0, 'allowed': 150.0, 'patient_responsibility': 50.0, 'record_id': 'r3', 'reverses': 'r1'}]
        c = cr.settle({'claim_id': 'c1', 'patient_id': 'p', 'payer': 'P', 'provider': 'X', 'setting': 'x', 'service_code': 's', 'service_date': '2028-01-05',
                       'billed': 200.0, 'allowed': 150.0, 'paid': 80.0, 'first_status': 'paid', 'final_status': 'reversed', 'ever_denied': False,
                       'denied_then_paid': False, 'history': h, 'denial_reasons': [], 'touches': Counter()})
        self.assertEqual((c['final_status'], c['received_final_status'], c['allowed']), ('paid', 'reversed', 120.0))
        row = cr.churn_review([c])[0]
        self.assertEqual((row['reversal_kind'], row['taken_back']), ('repriced', 30.0))


class Validation(unittest.TestCase):
    def test_parameters(self):
        p = cr.validate({})
        self.assertEqual((p['focus'], p['fdr'], p['rework_cost']), ('all', 0.05, None))
        for bad in ({'focus': 'astrology'}, {'fdr': 0.9}, {'colour': 1}, {'period': {'start': '2028-13'}}, {'min_claims': 2},
                    {'payer': 'x; drop table'}, {'period': {'start': '2029-01-01', 'end': '2028-01-01'}}, {'fdr': True}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                cr.validate(bad)

    def test_graph_validation(self):
        plan = {'interpretation': 'q', 'coverage': 'supported_observation', 'unmet_requirements': [],
                'nodes': [{'id': 'answer', 'operation': 'claims_review', 'inputs': [], 'purpose': 'claims', 'parameters': {'focus': 'denials'}}]}
        self.assertEqual(validate(plan, AS_OF)['nodes'][0]['parameters']['focus'], 'denials')
        bad = json.loads(json.dumps(plan)); bad['nodes'][0]['parameters'] = {'period': {'end': '2031-01-01'}}
        with self.assertRaises(ValueError): validate(bad, AS_OF)
        bad['nodes'][0]['parameters'] = {'focus': 'nope'}
        with self.assertRaises(ValueError): validate(bad, AS_OF)


def fixture():
    """A small warehouse: two payers, six practices, denials (one overturned, one written off), a duplicate, an underpayment."""
    rng = random.Random(2)
    recs, patients = [], {f'p{i}': {'age_reported': 40 + i % 30} for i in range(60)}
    n = 0
    for i in range(600):
        n += 1
        pid, org, payer = f'p{i % 60}', f'PR-{i % 6}', 'PAY1' if i % 3 else 'PAY2'
        day = f'2028-{1 + i % 12:02d}-{1 + i % 27:02d}'
        billed = round(18000 * math.exp(rng.gauss(0, 0.1)))
        allowed = round(billed * (0.6 if payer == 'PAY1' else 0.7) * (1 + rng.gauss(0, 0.01)))
        if i == 7: allowed = round(allowed * 0.4)
        base = {'claim_id': f'C{n}', 'service_date': day, 'billed_cents': billed, 'source_system': payer, 'service_organization_id': org,
                'service_code': 'office_visit', 'available_date': day}
        if i % 40 == 0:
            recs.append((pid, 'medical_claims', {**base, 'status': 'denied', 'event_date': day, 'allowed_cents': 0, 'payer_paid_cents': 0,
                                                 'adjustments': [{'group': 'CO', 'reason_code': '16', 'amount_cents': billed}]}))
            if i % 80 == 0:
                recs.append((pid, 'medical_claims', {**base, 'status': 'paid', 'event_date': f'2028-{1 + i % 12:02d}-28', 'allowed_cents': allowed,
                                                     'payer_paid_cents': allowed, 'available_date': f'2028-{1 + i % 12:02d}-28'}))
        else:
            recs.append((pid, 'medical_claims', {**base, 'status': 'paid', 'event_date': day, 'allowed_cents': allowed, 'payer_paid_cents': allowed,
                                                 'patient_responsibility_cents': 0}))
    recs.append(('p1', 'medical_claims', {'claim_id': 'CDUP', 'service_date': '2028-02-02', 'event_date': '2028-02-02', 'available_date': '2028-02-02',
                                          'status': 'paid', 'billed_cents': 18000, 'allowed_cents': 10800, 'payer_paid_cents': 10800,
                                          'source_system': 'PAY1', 'service_organization_id': 'PR-1', 'service_code': 'office_visit'}))
    recs.append(('p1', 'medical_claims', {'claim_id': 'CDUP2', 'service_date': '2028-02-02', 'event_date': '2028-02-02', 'available_date': '2028-02-03',
                                          'status': 'paid', 'billed_cents': 18000, 'allowed_cents': 10800, 'payer_paid_cents': 10800,
                                          'source_system': 'PAY1', 'service_organization_id': 'PR-1', 'service_code': 'office_visit'}))
    recs.append(('p0', 'labs', {'test': 'HbA1c', 'value': 7, 'unit': '%', 'event_date': '2028-01-01'}))
    recs.append(('p0', 'adt', {'event': 'emergency_registration', 'event_date': '2028-01-01'}))
    recs.append(('p0', 'encounters', {'type': 'emergency', 'event_date': '2028-01-01', 'organization_id': 'H1'}))
    path = build(recs, patients)
    semantic_layer.build(path)
    return path


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = fixture()
        cls.profile = source_profile.load(cls.path)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.path, ignore_errors=True)

    def test_execute_and_answer_contract(self):
        plan = validate({'interpretation': 'Where are we leaking money in claims?', 'coverage': 'supported_observation', 'unmet_requirements': [],
                         'nodes': [{'id': 'answer', 'operation': 'claims_review', 'inputs': [], 'purpose': 'leakage', 'parameters': {'focus': 'all'}}]}, AS_OF)
        report = cr.execute(self.path, None, plan['nodes'][0]['parameters'], AS_OF)
        self.assertEqual(report['claims'], 602)
        d = report['denials']
        self.assertEqual((d['denied_claims'], d['overturned'], d['written_off_claims']), (15, 8, 7))
        self.assertEqual(d['pareto'][0]['reason'], 'claim lacks information needed for adjudication')
        self.assertEqual(report['duplicates']['exact_pairs'], 1)
        self.assertIn('C8', {f['evidence'].get('claim_id') for f in flags(report, 'underpayment')})
        answer = build_answer(plan, [{'node': plan['nodes'][0], 'result': report}], {}, plan['interpretation'])
        self.assertEqual((answer['status'], answer['answer_type']), ('answered', 'table'))
        self.assertTrue(answer['overall']['claims_review']['leakage_pareto'])
        self.assertTrue(headline(answer).startswith(f"{len(report['flags'])} review flag"))
        self.assertEqual(analysis_graph.benchmark_references(answer, operation='claims_review'), [])
        json.dumps(answer)
        # A population input narrows the claims to those patients.
        sub = cr.execute(self.path, ['p1', 'p2'], {'focus': 'denials'}, AS_OF)
        self.assertLess(sub['claims'], 40)

    def test_follow_ups_compile_back_to_claims_review(self):
        report = cr.execute(self.path, None, {'focus': 'all'}, AS_OF)
        for q in cr.follow_up(report):
            g, info = local_compiler.compile(q, self.profile, AS_OF)
            self.assertIsNotNone(g, (q, info)); self.assertEqual(g['nodes'][-1]['operation'], 'claims_review', (q, info))

    def compile(self, q):
        return local_compiler.compile(q, self.profile, AS_OF)

    def test_compiler_routes_varied_phrasings(self):
        cases = {'Which providers bill unusually compared to peers?': 'providers', 'Any suspicious billing patterns?': 'all',
                 'Where are we leaking money in claims (denials, underpayments, rework)?': 'leakage',
                 'Which claims were underpaid versus what similar claims were paid?': 'underpayment',
                 'Explain the denial patterns by payer and reason': 'denials', 'Which payers underpay us the most?': 'underpayment',
                 'Show reversals and take-backs by payer': 'duplicates', 'How much did we write off in denials last year?': 'leakage'}
        for q, focus in cases.items():
            with self.subTest(q=q):
                g, info = self.compile(q)
                self.assertIsNotNone(g, info)
                self.assertEqual(g['nodes'][-1]['operation'], 'claims_review', info)
                self.assertEqual(g['nodes'][-1]['parameters']['focus'], focus)
        g, _ = self.compile('How much did we write off in denials last year?')
        self.assertEqual(g['nodes'][-1]['parameters']['period'], {'start': '2028-01-01', 'end': '2028-12-31'})

    def test_does_not_steal_counts_or_monitor_questions(self):
        for q in ('How many claims were denied and then paid?', 'How many claims were denied?', 'How many reversals were there?',
                  'What is the denial rate by payer?'):
            with self.subTest(q=q):
                g, info = self.compile(q)
                self.assertNotEqual((g or {'nodes': [{}]})['nodes'][-1].get('operation') if g and g['nodes'] else None, 'claims_review')
        for q in ('Any signs of duplicate claims?', "Run Benford's law on claim amounts", 'Which payers are out of control on claim denials?',
                  'Which service organizations are billing outliers versus their peers?'):
            with self.subTest(q=q):
                g, info = self.compile(q)
                self.assertEqual(g['nodes'][-1]['operation'], 'monitor', info)
        g, info = self.compile('Where do patients leak out of network after discharge?')
        self.assertNotEqual((g or {'nodes': [{}]})['nodes'][-1].get('operation'), 'claims_review')

    def test_acceptance_file(self):
        rows = json.loads((ROOT / 'bench' / 'acceptance' / 'claims_review.json').read_text())
        self.assertGreaterEqual(len(rows), 8)
        for row in rows:
            with self.subTest(q=row['question']):
                self.assertEqual(row['expect_operation'], 'claims_review')
                g, info = self.compile(row['question'])
                self.assertIsNotNone(g, info)
                self.assertEqual(g['nodes'][-1]['operation'], 'claims_review', info)


class Integration(unittest.TestCase):
    def test_registered_everywhere(self):
        self.assertIn('claims_review', analysis_graph.OPERATIONS)
        self.assertIn('claims_review', analysis_graph.NO_BENCHMARK_OPERATIONS)
        self.assertIn('claims_review', graph_contracts.TYPED)
        self.assertEqual(graph_contracts.input_contract('claims_review')['count'], [0, 1])
        self.assertIn('claims_review', graph_planner.CONTRACTS)
        self.assertEqual(capability_registry.PRODUCT['claims_review']['population'], 'optional')
        self.assertIn('monitor', capability_registry.PRODUCT['claims_review']['use'])
        schema = graph_contracts.parameter_schema('claims_review', {'cohort_fields': {'record_fields': {}}, 'evidence_catalog': {}})
        self.assertEqual(set(schema['properties']['focus']['enum']), set(cr.FOCUS))
        self.assertLess(len(json.dumps(schema)), 4000)
        source = (Path(analysis_graph.__file__)).read_text()
        block = source[source.index("if last['operation'] == 'claims_review':"):]
        block = block[:block.index("\n    if last['operation']", 10)]
        self.assertTrue(block.rstrip().endswith('return answer'))

    def test_console_renders_claims_review(self):
        from clinical_core.agere import ask_view
        html = ask_view.page()
        self.assertIn('function vizClaims', html)
        self.assertIn('function paretoChart', html)
        self.assertIn('if(o.claims_review)return vizClaims(a)', html)
        self.assertEqual(html.count('</script>'), 2)
        node = shutil.which('node') or str(Path.home() / '.local' / 'bin' / 'node')
        if not Path(node).exists(): self.skipTest('node not available for a syntax check')
        script = re.findall(r'<script>(.*?)</script>', html, re.S)[-1]
        tmp = Path(os.environ.get('TMPDIR', '/tmp')) / 'agere_claims_review_check.js'
        tmp.write_text(script)
        out = subprocess.run([node, '--check', str(tmp)], capture_output=True, text=True)
        tmp.unlink()
        self.assertEqual(out.returncode, 0, out.stderr[:500])


def _real_ready():
    try:
        db = sqlite3.connect(f"file:{REAL_STACK / 'semantic.sqlite3'}?mode=ro", uri=True)
        meta = dict(db.execute('SELECT key, value FROM meta')); db.close()
        return meta.get('version') == semantic_layer.VERSION and \
            meta.get('source_digest') == json.loads((REAL_STACK / 'run.json').read_text())['source_digest']
    except Exception:
        return False


@unittest.skipUnless(_real_ready(), 'real stack semantic layer not present or not current (never rebuilt here)')
class RealStack(unittest.TestCase):
    """Read-only smoke on the synthetic release."""

    @classmethod
    def setUpClass(cls):
        cls.report = cr.execute(REAL_STACK, None, {'focus': 'all'}, AS_OF)

    def test_review_runs_and_ranks(self):
        r = self.report
        self.assertGreater(r['claims'], 1000)
        self.assertTrue(r['denials']['pareto'])
        self.assertGreater(r['denials']['overturned'], 0)
        exp = [f['expected_dollars'] for f in r['flags'] if f['expected_dollars'] is not None]
        self.assertEqual(exp, sorted(exp, reverse=True))
        self.assertTrue(r['leakage_pareto'])
        value, _ = cr.summarize(r)
        self.assertIn('Denials:', value)

    def test_smoke_probe_keeps_its_route(self):
        prof = json.loads((REAL_STACK / 'source-profile.json').read_text()) if (REAL_STACK / 'source-profile.json').exists() else None
        run = json.loads((REAL_STACK / 'run.json').read_text())
        if not prof or prof.get('schema') != source_profile.VERSION or prof.get('source_digest') != run['source_digest']:
            self.skipTest('saved source profile is not current; not rebuilding it')
        g, info = local_compiler.compile('How many claims were denied and then paid?', prof, AS_OF)
        self.assertEqual((info['route'], [n['operation'] for n in g['nodes']]), ('local_compiler', ['sql']))
        db = sqlite3.connect(f"file:{REAL_STACK / 'semantic.sqlite3'}?mode=ro", uri=True)
        try:
            self.assertEqual(db.execute(g['nodes'][0]['parameters']['query']).fetchone()[0], 959)
        finally:
            db.close()
        for row in json.loads((ROOT / 'bench' / 'acceptance' / 'claims_review.json').read_text()):
            g, info = local_compiler.compile(row['question'], prof, AS_OF)
            self.assertEqual(g['nodes'][-1]['operation'], 'claims_review', (row['question'], info))


if __name__ == '__main__':
    unittest.main()
