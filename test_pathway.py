"""Care-pathway operator: Markov estimator recovery on simulated sequences, absorbing-chain algebra, group tests, validation,
fixture end-to-end, compiler routing and a read-only smoke run on the real synthetic release."""
import json, os, sqlite3, unittest
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from clinical_core.agere.clinical import analysis_graph, local_compiler, pathway, semantic_layer, source_profile
from test_aggregate_explain import AS_OF, build, pcp, visit

STATES = ['primary_care', 'emergency', 'inpatient', 'end']
TRUE_P = np.array([[0.55, 0.15, 0.05, 0.25],   # primary care
                   [0.40, 0.10, 0.30, 0.20],   # ED
                   [0.60, 0.10, 0.05, 0.25],   # inpatient
                   [0.00, 0.00, 0.00, 1.00]])


def simulate(n_patients, P=TRUE_P, seed=7, start_state='primary_care', first_day=date(2020, 1, 1)):
    """Sequences from a known chain: each step 1-20 days after the last; 'end' = silence long past the 30-day window."""
    rng = np.random.default_rng(seed); seqs = {}
    for k in range(n_patients):
        d = first_day + timedelta(days=int(rng.integers(0, 365))); s = start_state; seq = []
        while s != 'end' and len(seq) < 60:
            seq.append((d.isoformat(), s))
            s = STATES[rng.choice(4, p=P[STATES.index(s)])]
            d += timedelta(days=int(rng.integers(1, 21)))
        seqs[f'p{k}'] = seq
    return seqs


def analyse(seqs, attrs=None, **params):
    p = pathway.validate({'window_days': 30, **params})
    return pathway.analyse(seqs, attrs or {pid: {'practice': 'PR1', 'coverage_type': 'x'} for pid in seqs}, p, '2029-08-15')


class Estimator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seqs = simulate(3000)
        cls.r = analyse(cls.seqs, start='emergency', targets=['inpatient'], max_steps=20, steps=2)

    def test_transition_probabilities_recovered_within_ci(self):
        cells = covered = 0
        for row in self.r['table']:
            truth = TRUE_P[STATES.index(row['from']), STATES.index(row['to'])]
            cells += 1; covered += row['ci95_low'] <= truth <= row['ci95_high']
            self.assertLessEqual(row['dirichlet_low'], row['probability']); self.assertGreaterEqual(row['dirichlet_high'], row['probability'])
            self.assertAlmostEqual(row['probability'], truth, delta=4 * (truth * (1 - truth) / row['row_total']) ** 0.5)  # 4 SE
        self.assertEqual(cells, 12)  # every non-zero true cell observed, no spurious cell
        self.assertGreaterEqual(covered, 9)
        self.assertEqual(self.r['excluded'], {})

    def test_interval_coverage_over_replicates(self):
        """Wilson and Dirichlet 95% intervals cover the true transition probability in ~95% of cells across replicates."""
        hits = Counter()
        n = 0
        for seed in range(40):
            for row in analyse(simulate(300, seed=100 + seed))['table']:
                truth = TRUE_P[STATES.index(row['from']), STATES.index(row['to'])]
                n += 1
                hits['wilson'] += row['ci95_low'] <= truth <= row['ci95_high']
                hits['dirichlet'] += row['dirichlet_low'] <= truth <= row['dirichlet_high']
        self.assertGreater(n, 400)
        for k in ('wilson', 'dirichlet'):
            self.assertTrue(0.91 <= hits[k] / n <= 0.99, (k, hits[k] / n))

    def test_absorption_matches_analytic_chain(self):
        # P(eventually inpatient from ED), with inpatient and end absorbing: first step from the ED row, then Q/R algebra.
        Q = TRUE_P[np.ix_([0, 1], [0, 1])]; R = TRUE_P[np.ix_([0, 1], [2])]
        v = TRUE_P[1]; truth = v[2] + v[[0, 1]] @ np.linalg.inv(np.eye(2) - Q) @ R[:, 0]
        e = self.r['chain']['eventual']['inpatient']
        self.assertLessEqual(e['ci95_low'], truth); self.assertGreaterEqual(e['ci95_high'], truth)
        self.assertAlmostEqual(e['probability'], truth, delta=0.03)
        by_step = [s['inpatient'] for s in self.r['chain']['by_step']]
        self.assertEqual(by_step, sorted(by_step))  # absorbed mass only accumulates
        self.assertAlmostEqual(by_step[0], TRUE_P[1, 2], delta=0.03)
        self.assertAlmostEqual(by_step[-1], e['probability'], delta=0.005)

    def test_absorption_algebra_exact(self):
        P = pathway.transition_matrix([[0, 1, 1], [1, 0, 1], [0, 0, 0]])  # end row unobserved -> absorbing
        res = pathway.absorption(P, ['a', 'b', 'end'], 'a', ['b'], 3)
        self.assertAlmostEqual(res['by_step'][0]['b'], 0.5)
        self.assertAlmostEqual(res['eventual']['b'], 0.5)       # from a: b directly or end
        self.assertAlmostEqual(res['expected_steps'], 1.0)
        res = pathway.absorption(P, ['a', 'b', 'end'], 'a', ['end'], 10)   # b transient: a<->b until end
        self.assertAlmostEqual(res['eventual']['end'], 1.0)
        self.assertAlmostEqual(res['expected_steps'], 2.0, places=6)  # 1 + 0.5 * row-sum of b in N = (I - Q)^-1

    def test_variants_reach_and_median_days(self):
        top = self.r['variants'][0]
        self.assertEqual(top['states'][0], 'emergency')
        self.assertAlmostEqual(sum(v['share'] for v in self.r['variants']) <= 1.0, True)
        reach = self.r['reach']['inpatient'][-1]
        self.assertEqual(reach['days'], 30)
        self.assertGreater(reach['share'], TRUE_P[1, 2] - 0.05)
        md = next(r for r in self.r['table'] if r['from'] == 'emergency' and r['to'] == 'inpatient')['median_days']
        self.assertTrue(8 <= md <= 13)  # uniform 1..20

    def test_second_order_test_does_not_reject_a_first_order_chain(self):
        r = analyse(simulate(1500, seed=3), order=2)
        self.assertGreater(r['second_order']['p_value'], 0.001)
        self.assertGreater(r['second_order']['df'], 0)

    def test_group_comparison_permutation(self):
        other = TRUE_P.copy(); other[1] = [0.10, 0.10, 0.60, 0.20]
        a, b = simulate(400, seed=1), simulate(400, P=other, seed=2)
        seqs = {**{f'a{k}': v for k, v in a.items()}, **{f'b{k}': v for k, v in b.items()}}
        attrs = {pid: {'practice': pid[0].upper(), 'coverage_type': 'x'} for pid in seqs}
        r = analyse(seqs, attrs, start='emergency', group_by='practice', permutations=199)
        ed = next(t for t in r['groups']['tests'] if t['from'] == 'emergency')
        self.assertLess(ed['permutation_p'], 0.01); self.assertLess(ed['q_value'], 0.05)
        same = analyse({**{f'a{k}': v for k, v in a.items()}, **{f'b{k}': v for k, v in simulate(400, seed=9).items()}},
                       attrs, group_by='practice', permutations=199)
        self.assertGreater(min(t['permutation_p'] for t in same['groups']['tests']), 0.01)


class Rules(unittest.TestCase):
    def test_validation(self):
        bad = [{'start': 'mortuary'}, {'start': 'diagnosis'}, {'index_concept': 'diabetes'}, {'window_days': 0},
               {'targets': ['diagnosis']}, {'expected': {'from': 'emergency', 'to': ['nowhere'], 'within_days': 7}},
               {'expected': {'from': 'emergency', 'to': ['primary_care'], 'within_days': 400}}, {'group_by': 'zip'}, {'unknown': 1},
               {'period': {'start': '2029-02-01', 'end': '2029-01-01'}}, {'horizons_days': [200]}]
        for p in bad:
            with self.assertRaises(ValueError, msg=p): pathway.validate(p)
        p = pathway.validate({'start': 'discharge'})
        self.assertTrue(p['discharge_events']); self.assertEqual(p['horizons_days'], [7, 30, 90])
        self.assertTrue(pathway.validate({'targets': ['referral']})['referral_events'])

    def test_planner_schema_payload_validates(self):
        from clinical_core.agere.clinical import graph_contracts, graph_planner
        from clinical_core.agere.clinical.capability_registry import PRODUCT
        from test_graph_contracts import catalog
        schema = graph_contracts.parameter_schema('pathway', catalog())
        self.assertIn('pathway', graph_contracts.TYPED); self.assertIn('pathway', graph_planner.CONTRACTS); self.assertIn('pathway', PRODUCT)
        self.assertEqual(graph_contracts.input_contract('pathway')['count'], [0, 1])
        self.assertEqual(set(schema['required']), set(schema['properties']))
        full = {'start': 'emergency', 'index_concept': '', 'anchor': 'each', 'window_days': 30, 'period': {'start': '', 'end': ''},
                'targets': [], 'max_steps': 5, 'horizons_days': [], 'expected': {'from': '', 'to': [], 'within_days': 0}, 'steps': 3, 'top': 10,
                'group_by': 'none', 'leakage': 'organization', 'order': 1, 'discharge_events': True, 'referral_events': False, 'include_other': False}
        self.assertEqual(set(full), set(schema['properties']))
        p = pathway.validate(full)
        self.assertEqual((p['expected'], p['targets'], p['horizons_days']), (None, ['emergency', 'inpatient'], [7, 30]))

    def test_acute_gap_detection(self):
        monthly = {f'2026-{m:02d}': (5, 300) for m in range(1, 11)}
        monthly.update({f'2026-{m:02d}': (0, 300) for m in (11, 12)})
        monthly.update({f'2027-{m:02d}': (0, 250) for m in range(1, 4)})
        monthly.update({'2027-04': (4, 280), '2019-01': (0, 3), '2019-02': (0, 2)})
        self.assertEqual(pathway.acute_gaps(monthly), [{'start': '2026-11', 'end': '2027-03', 'months': 5, 'visits_in_gap': 1350}])
        self.assertEqual(pathway.acute_gaps({'2026-01': (1, 100), '2026-02': (0, 100), '2026-03': (2, 100)}), [])  # one month is noise

    def test_gap_windows_are_excluded_and_stated(self):
        seqs = {'p1': [('2027-01-05', 'primary_care'), ('2027-01-20', 'primary_care')],
                'p2': [('2029-01-05', 'emergency'), ('2029-01-08', 'primary_care')]}
        p = pathway.validate({'window_days': 30, 'start': 'emergency'})
        gaps = [{'start': '2026-11', 'end': '2028-09', 'months': 23, 'visits_in_gap': 100}]
        r = pathway.analyse(seqs, {}, p, AS_OF, gaps)
        self.assertEqual(r['excluded']['transition_window_overlaps_acute_data_gap'], 2)
        self.assertEqual(r['transitions'], 2)  # p2 only: ED -> PCP, PCP -> end
        self.assertIn('2026-11..2028-09', pathway.summary(r)); self.assertIn('feed defect', r['method'] + ' '.join(r['notes']))
        r = pathway.analyse(seqs, {}, pathway.validate({'window_days': 30, 'gap_policy': 'include'}), AS_OF, gaps)
        self.assertEqual(r['transitions'], 4)


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        patients = {f'p{i}': {'age_reported': 60, 'sex_reported': 'F'} for i in range(6)}
        recs = [pcp(f'p{i}', 'drA', 'PR1' if i < 3 else 'PR2') for i in range(6)]
        recs += [(f'p{i}', 'coverage', {'coverage_type': 'medicare', 'event_date': '2026-01-01'}) for i in range(6)]
        # p0: ED -> PCP day 3 (conforms) ; p1: ED -> ED day 5 -> admitted same day, discharged 2028-03-09 -> PCP 03-12
        recs += [visit('p0', 'emergency', '2028-01-01'), visit('p0', 'Office Visit', '2028-01-04', 'PR1')]
        recs += [visit('p1', 'emergency', '2028-03-01'), visit('p1', 'emergency', '2028-03-06'),
                 visit('p1', 'inpatient', '2028-03-06', 'H1', '2028-03-09'), visit('p1', 'Office Visit', '2028-03-12', 'PR1')]
        # p2: ED then nothing ; p3: PCP -> outside dental (leakage) ; p4 primary care twice the same day (collapsed)
        recs += [visit('p2', 'emergency', '2028-05-01'), visit('p3', 'Office Visit', '2028-06-01', 'PR2'),
                 visit('p3', 'Office Visit', '2028-06-20', 'WH-OUTSIDE-DENTAL-1'),
                 visit('p4', 'Office Visit', '2028-07-01', 'PR2'), visit('p4', 'Office Visit', '2028-07-01', 'PR2')]
        cls.path = build(recs, patients); semantic_layer.build(cls.path); cls.ids = sorted(patients)

    def test_operator_end_to_end(self):
        params = {'start': 'emergency', 'window_days': 30, 'targets': ['emergency', 'inpatient'],
                  'expected': {'from': 'emergency', 'to': ['primary_care'], 'within_days': 7}}
        r = pathway.execute(self.path, self.ids, params)
        ed = {x['to']: x['count'] for x in r['table'] if x['from'] == 'emergency'}
        self.assertEqual(ed, {'primary_care': 1, 'emergency': 1, 'inpatient': 1, 'end': 1})
        self.assertEqual(next(x for x in r['table'] if x['from'] == 'inpatient')['to'], 'discharge')
        self.assertEqual(next(x for x in r['table'] if x['from'] == 'discharge')['to'], 'primary_care')
        c = r['conformance']
        self.assertEqual((c['occurrences'], c['conforming']), (4, 2))  # p0 day 3; p1's second ED visit -> PCP on day 6
        self.assertEqual(r['reach']['emergency'][0]['reached'], 1)        # p1 returns on day 5
        self.assertEqual(r['leakage']['to_outside'], 1)
        self.assertEqual(r['excluded'].get('same_day_repeat'), 1)
        self.assertIn('Markov', r['method'])

    def test_graph_answer_and_view(self):
        from clinical_core.agere import ask_view
        plan = analysis_graph.validate({'interpretation': 'ED pathway', 'coverage': 'supported_observation', 'unmet_requirements': [], 'nodes': [
            {'id': 'pathway', 'operation': 'pathway', 'inputs': [], 'parameters': {'start': 'emergency', 'window_days': 30}, 'purpose': 'path'}]}, AS_OF)
        report = pathway.execute(self.path, None, plan['nodes'][0]['parameters'])
        answer = analysis_graph.build_answer(plan, [{'node': plan['nodes'][0], 'result': report}], {}, 'q')
        self.assertEqual((answer['status'], answer['answer_type']), ('answered', 'table'))
        self.assertTrue(answer['value'].startswith('After ED'))
        self.assertIn('matrix', answer['overall'])
        self.assertTrue(analysis_graph.headline(answer).startswith('After ED'))
        self.assertEqual(analysis_graph.benchmark_references(answer, 'q', 'pathway'), [])
        json.dumps(answer, allow_nan=False, default=str)
        html = ask_view.page()
        self.assertIn('function vizPathway', html); self.assertIn('o.matrix&&o.states', html)
        with self.assertRaises(ValueError):
            analysis_graph.validate({'interpretation': 'x', 'coverage': 'supported_observation', 'unmet_requirements': [], 'nodes': [
                {'id': 'pathway', 'operation': 'pathway', 'inputs': [], 'parameters': {'period': {'start': '2030-01-01', 'end': ''}}, 'purpose': 'p'}]}, AS_OF)


class Compiler(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        recs = [visit('p0', 'emergency', '2028-01-01'), ('p0', 'adt', {'event': 'emergency_registration', 'event_date': '2028-01-01'}),
                ('p0', 'coverage', {'coverage_type': 'medicare', 'event_date': '2026-01-01'})]
        cls.profile = source_profile.load(build(recs, {'p0': {'age_reported': 70, 'sex_reported': 'F'}}))

    def route(self, q):
        g, info = local_compiler.compile(q, self.profile, AS_OF)
        self.assertEqual(info['route'], 'local_compiler', (q, info.get('reason')))
        self.assertEqual(g['nodes'][-1]['operation'], 'pathway', q)
        spec = g['nodes'][0]['parameters']['specification'] if g['nodes'][0]['operation'] == 'cohort' else None
        return spec, g['nodes'][-1]['parameters']

    def test_phrasings(self):
        spec, p = self.route('How do heart failure patients move through care after an ED visit?')
        self.assertEqual(([a['concept'] for a in spec['all']], p['start']), (['heart_failure'], 'emergency'))
        _, p = self.route('Where do patients leak out of network after discharge?')
        self.assertEqual((p['start'], p['leakage']), ('discharge', 'organization'))
        _, p = self.route('What happens after an ED visit - how many get primary care follow-up within 7 days vs return to the ED?')
        self.assertEqual(p['expected'], {'from': 'emergency', 'to': ['primary_care'], 'within_days': 7})
        self.assertEqual(p['targets'], ['emergency'])
        spec, p = self.route('Most common care paths for new diabetics')
        self.assertEqual((p['start'], p['index_concept'], p['anchor']), ('diagnosis', 'diabetes', 'first'))
        spec, p = self.route('Probability a CHF patient ends up admitted within 90 days starting from a PCP visit')
        self.assertEqual((p['start'], p['targets'], p['window_days'], p['horizons_days']), ('primary_care', ['inpatient'], 90, [90]))
        _, p = self.route('Show the patient journey after an urgent care visit by practice')
        self.assertEqual((p['start'], p['group_by']), ('urgent_care', 'practice'))
        _, p = self.route('Top 5 most common paths after an ED visit')
        self.assertEqual(p['top'], 5)
        _, p = self.route('How many patients leaked to other practices after a PCP visit?')
        self.assertEqual(p['leakage'], 'organization_or_practice')

    def test_declines_rather_than_guesses(self):
        g, info = local_compiler.compile('Patient flow by month', self.profile, AS_OF)  # ED / inpatient stage flow (flow_decomposition), never a guessed pathway
        self.assertNotEqual((g or {'nodes': [{}]})['nodes'][-1].get('operation'), 'pathway')
        for q in ('Where do patients go after an ED visit or a discharge?',
                  'Which journeys lead to readmission for medicare patients?', 'Care path after a PCP visit within 900 days'):
            g, info = local_compiler.compile(q, self.profile, AS_OF)
            self.assertEqual(info['route'], 'fallback', q)
            self.assertIsNone(g, q)

    def test_other_routes_unchanged(self):
        g, _ = local_compiler.compile('Time to readmission after discharge', self.profile, AS_OF)
        self.assertEqual(g['nodes'][-1]['operation'], 'survival')
        g, _ = local_compiler.compile('What is the 30-day readmission rate by payer?', self.profile, AS_OF)
        self.assertEqual(g['nodes'][-1]['operation'], 'aggregate')

    def test_acceptance_file(self):
        rows = json.loads((Path(__file__).resolve().parent / 'bench' / 'acceptance' / 'pathway.json').read_text())
        self.assertGreaterEqual(len(rows), 8)
        for row in rows:
            self.assertEqual(row['expect_operation'], 'pathway')
            self.route(row['question'])


STACK = Path(os.environ.get('PATHWAY_STACK', Path(__file__).resolve().parent / 'artifacts' / 'agere-healthcare-v123' / 'stack'))
if not (STACK / 'semantic.sqlite3').exists():
    STACK = Path('/Users/mikel/clinical-insights-local/artifacts/agere-healthcare-v123/stack')


def _stack_ready():
    try:
        db = sqlite3.connect(f"file:{STACK / 'semantic.sqlite3'}?mode=ro", uri=True)
        meta = dict(db.execute('SELECT key, value FROM meta')); db.close()
        return meta.get('version') == semantic_layer.VERSION and \
            meta.get('source_digest') == json.loads((STACK / 'run.json').read_text())['source_digest']
    except Exception:
        return False


@unittest.skipUnless(_stack_ready(), 'synthetic release semantic layer not present or not current (never rebuilt from a test)')
class RealRelease(unittest.TestCase):
    def test_ed_pathway_runs_and_states_the_acute_gap(self):
        original = semantic_layer.ensure
        semantic_layer.ensure = lambda path: STACK / 'semantic.sqlite3'  # read the release as built; never rebuild it
        try:
            r = pathway.execute(STACK, None, {'start': 'emergency', 'window_days': 30, 'targets': ['emergency', 'inpatient'],
                                              'expected': {'from': 'emergency', 'to': ['primary_care', 'specialty', 'telehealth'], 'within_days': 7}})
        finally:
            semantic_layer.ensure = original
        self.assertGreater(r['transitions'], 1000); self.assertGreater(r['anchors'], 100)
        self.assertTrue(any(g['start'] == '2026-11' and g['end'] == '2028-09' for g in r['acute_data_gaps']))
        self.assertTrue(all(0 <= x['probability'] <= 1 for x in r['table']))
        self.assertTrue(0 < r['conformance']['share'] < 1)
        self.assertIn('2026-11..2028-09', pathway.summary(r))


if __name__ == '__main__':
    unittest.main()
