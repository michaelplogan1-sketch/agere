"""Outreach worklist: optimiser correctness, reason codes, capacity handling, graph/compiler wiring, bandit loop."""
import itertools, json, math, random, unittest
from pathlib import Path

import numpy as np

from clinical_core.agere.clinical import analysis_graph, graph_contracts, local_compiler, outreach, outreach_bandit, semantic_layer, source_profile
from test_aggregate_explain import build, pcp, visit

AS_OF = '2029-08-15'
STACK = Path(__file__).resolve().parent / 'artifacts' / 'agere-healthcare-v123' / 'stack'


def row(pid, value, minutes=15, practice='A', reasons=None):
    reasons = reasons or [{'code': 'GAP_STATIN', 'category': 'care_gap', 'value_if_engaged': value / 0.5, 'label': '', 'evidence': ''}]
    return {'patient_id': pid, 'expected_value': value, 'value_if_engaged': value / 0.5, 'engagement_probability': 0.5,
            'minutes': minutes, 'practice': practice, 'pcp': 'x', 'reasons': reasons, 'categories': sorted({r['category'] for r in reasons})}


def params(**kw):
    return outreach.validate(kw)


def brute(rows, feasible, value):
    best, arg = -1.0, None
    for k in range(len(rows) + 1):
        for s in itertools.combinations(range(len(rows)), k):
            if feasible(s):
                v = value(s)
                if v > best + 1e-9: best, arg = v, s
    return best, arg


class Optimiser(unittest.TestCase):
    def test_knapsack_dp_is_optimal_against_brute_force(self):
        rng = random.Random(11)
        for trial in range(25):
            n = rng.randint(3, 11)
            values = [round(rng.uniform(10, 900), 2) for _ in range(n)]
            weights = [rng.choice([10, 15, 20, 25, 30, 45, 60]) for _ in range(n)]
            cap = rng.randint(20, 150)
            chosen, curve = outreach.knapsack(values, weights, cap)
            self.assertLessEqual(sum(weights[i] for i in chosen), cap)
            got = sum(values[i] for i in chosen)
            opt, _ = brute(values, lambda s: sum(weights[i] for i in s) <= cap, lambda s: sum(values[i] for i in s))
            self.assertAlmostEqual(got, opt, places=6)
            self.assertAlmostEqual(curve[cap], opt, places=6)
            # the DP value curve is the optimum at every smaller budget too
            b = cap // 2
            opt_b, _ = brute(values, lambda s: sum(weights[i] for i in s) <= b, lambda s: sum(values[i] for i in s))
            self.assertAlmostEqual(curve[b], opt_b, places=6)

    def test_exact_sort_with_practice_floors_and_caps_is_optimal(self):
        rng = random.Random(5)
        for trial in range(30):
            n = rng.randint(4, 10)
            rows = [row(f'p{i}', round(rng.uniform(1, 100), 2), practice=rng.choice('ABC')) for i in range(n)]
            k = rng.randint(1, n)
            floor = rng.choice([0, 0, 1])
            cap = rng.choice([None, 2, 3])
            if cap is not None and cap < floor: cap = floor
            chosen = outreach.exact_cardinality(rows, k, floor, cap)
            practices = {r['practice'] for r in rows}
            def feasible(s, k=k, floor=floor, cap=cap):
                if len(s) > k: return False
                count = {p: sum(rows[i]['practice'] == p for i in s) for p in practices}
                if cap is not None and any(c > cap for c in count.values()): return False
                # a floor binds only as far as the practice has patients and slots allow
                need = {p: min(floor, sum(r['practice'] == p for r in rows)) for p in practices}
                return sum(need.values()) > k or all(count[p] >= need[p] for p in practices)
            opt, _ = brute(rows, feasible, lambda s: sum(rows[i]['expected_value'] for i in s))
            self.assertTrue(feasible(tuple(chosen)))
            self.assertAlmostEqual(sum(rows[i]['expected_value'] for i in chosen), opt, places=6)

    def test_greedy_on_submodular_objective_meets_bound_and_certificate_covers_optimum(self):
        rng = random.Random(2)
        for trial in range(15):
            n = rng.randint(5, 10)
            rows = []
            for i in range(n):
                cats = rng.sample(outreach.CATEGORIES, rng.randint(1, 2))
                reasons = [{'code': 'X', 'category': c, 'value_if_engaged': rng.uniform(50, 600), 'label': '', 'evidence': ''} for c in cats]
                r = row(f'p{i}', round(rng.uniform(50, 500), 2), practice=rng.choice('AB'), reasons=reasons)
                rows.append(r)
            k = rng.randint(2, 4)
            p = params(capacity_patients=k, saturation=1.5, method='greedy')
            chosen, receipt, obj = outreach.solve(rows, p, k, None)
            opt, _ = brute(rows, lambda s: len(s) <= k, obj.value)
            got = obj.value(chosen)
            self.assertGreaterEqual(got, (1 - 1 / math.e) * opt - 1e-9)       # Nemhauser-Wolsey-Fisher
            self.assertGreaterEqual(receipt['certified_upper_bound'] + 1e-6, opt)  # data-dependent certificate is a true bound
            self.assertLessEqual(got, opt + 1e-9)
            # diminishing returns: the objective is submodular on random chains
            a = set(rng.sample(range(n), 2)); b = a | set(rng.sample(range(n), 2)); j = next(x for x in range(n) if x not in b) if len(b) < n else None
            if j is not None:
                self.assertGreaterEqual(obj.value(list(a | {j})) - obj.value(list(a)) + 1e-9, obj.value(list(b | {j})) - obj.value(list(b)))

    def test_hour_budget_greedy_certificate_with_mixed_constraints(self):
        rng = random.Random(9)
        rows = [row(f'p{i}', round(rng.uniform(20, 400), 2), minutes=rng.choice([10, 15, 30, 55]), practice=rng.choice('AB')) for i in range(9)]
        p = params(capacity_hours=1.5, capacity_patients=4, max_per_practice=3)
        chosen, receipt, obj = outreach.solve(rows, p, 4, 90)
        self.assertEqual(receipt['method'], 'lazy_greedy')
        feasible = lambda s: len(s) <= 4 and sum(rows[i]['minutes'] for i in s) <= 90 and all(sum(rows[i]['practice'] == q for i in s) <= 3 for q in 'AB')
        self.assertTrue(feasible(tuple(chosen)))
        opt, _ = brute(rows, feasible, obj.value)
        self.assertGreaterEqual(receipt['certified_upper_bound'] + 1e-6, opt)

    def test_overlap_discount_and_minutes(self):
        self.assertAlmostEqual(outreach.combine([100, 400, 200], 0.5), 400 + 100 + 25)
        self.assertEqual(outreach.combine([300], 0.0), 300)
        self.assertEqual(outreach.minutes_for(['GAP_STATIN', 'GAP_GDMT_BB', 'SDOH_FOOD']), 25 + 2 * outreach.ASSUMPTIONS['extra_reason_minutes'])

    def test_parameter_validation(self):
        self.assertEqual(params()['capacity_patients'], 50)
        self.assertIsNone(params(capacity_hours=10)['capacity_patients'])
        for bad in ({'capacity_patients': 0}, {'capacity_hours': -1}, {'require': ['vibes']}, {'min_per_practice': 3, 'max_per_practice': 2},
                    {'group_by': 'zip'}, {'unknown': 1}, {'overlap_discount': 2}):
            with self.assertRaises(ValueError): outreach.validate(bad)


def med(pid, name, day='2029-05-01'):
    return (pid, 'pharmacy_dispenses', {'medication_name': name, 'status': 'dispensed', 'event_date': day})


def lab(pid, test, value, unit, day):
    return (pid, 'labs', {'test': test, 'value': value, 'unit': unit, 'event_date': day})


class Worklist(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        patients = {pid: {'age_reported': age, 'sex_reported': sex} for pid, age, sex in
                    [('dm', 60, 'F'), ('htn', 55, 'M'), ('hf_gap', 70, 'M'), ('hf_ok', 68, 'F'), ('ckd', 66, 'F'), ('sdoh', 40, 'M'),
                     ('util', 72, 'F'), ('well', 35, 'M')]}
        cov = lambda pid: (pid, 'coverage', {'coverage_type': 'medicare', 'status': 'active', 'effective_start': '2028-01-01', 'event_date': '2028-01-01'})
        recs = [cov(p) for p in patients] + [pcp(p, 'drA' if i % 2 else 'drB', 'PR1' if i % 2 else 'PR2') for i, p in enumerate(patients)]
        recs += [('dm', 'conditions', {'description': 'Diabetes mellitus type 2', 'event_date': '2027-01-01'}),
                 lab('dm', 'HbA1c', 9.8, '%', '2029-06-01'),
                 ('htn', 'conditions', {'description': 'Essential hypertension', 'event_date': '2027-01-01'}),
                 ('htn', 'vitals', {'blood_pressure_systolic': 152, 'blood_pressure_diastolic': 94, 'event_date': '2029-07-01'}),
                 ('hf_gap', 'conditions', {'description': 'Heart failure', 'event_date': '2027-01-01'}),
                 ('hf_ok', 'conditions', {'description': 'Heart failure', 'event_date': '2027-01-01'}),
                 med('hf_ok', 'carvedilol 12.5 mg tablet'), med('hf_ok', 'sacubitril-valsartan 49-51 mg tablet'),
                 med('hf_ok', 'spironolactone 25 mg tablet'), med('hf_ok', 'empagliflozin 10 mg tablet'),
                 ('sdoh', 'social_screens', {'food_difficulty_reported': True, 'transport': 'reliable', 'event_date': '2029-03-01'}),
                 visit('util', 'inpatient', '2029-01-10', 'H1', '2029-01-14'), visit('util', 'inpatient', '2029-04-10', 'H1', '2029-04-13'),
                 visit('util', 'emergency', '2029-02-01'), visit('util', 'emergency', '2029-06-01'),
                 lab('well', 'HbA1c', 5.2, '%', '2029-06-01')]
        for i, v in enumerate([48, 45, 42, 39, 37, 35, 33]):  # steady eGFR decline toward 30
            recs.append(lab('ckd', 'eGFR', v, 'mL/min/1.73m2', f'{2026 + (i + 6) // 4}-{((i + 6) % 4) * 3 + 1:02d}-15'))
        cls.path = build(recs, patients)
        semantic_layer.build(cls.path)
        cls.result = outreach.execute(cls.path, None, {'capacity_patients': 10}, AS_OF)
        cls.rows = {w['patient_id']: w for w in cls.result['worklist']}

    def codes(self, pid):
        return [r['code'] for r in self.rows[pid]['reasons']]

    def test_reason_codes_are_explained(self):
        self.assertIn('GAP_A1C_GT9', self.codes('dm'))
        self.assertIn('GAP_STATIN', self.codes('dm'))
        self.assertIn('GAP_KED', self.codes('dm'))
        self.assertIn('GAP_BP_UNCONTROLLED', self.codes('htn'))
        self.assertTrue({'GAP_GDMT_BB', 'GAP_GDMT_RAAS', 'GAP_GDMT_MRA', 'GAP_GDMT_SGLT2'} <= set(self.codes('hf_gap')))
        self.assertNotIn('hf_ok', self.rows, 'all four GDMT classes observed: no GDMT gap')
        self.assertIn('SDOH_FOOD', self.codes('sdoh'))
        self.assertNotIn('SDOH_TRANSPORT', self.codes('sdoh'))
        self.assertIn('RISING_EGFR', self.codes('ckd'))
        self.assertEqual(self.codes('util')[0], 'UTIL_AVOIDABLE')
        self.assertEqual(self.codes('well'), ['UTIL_AVOIDABLE'])  # risk-model expectation only: no gap, trend or need
        for w in self.result['worklist']:
            self.assertTrue(all(r['evidence'] for r in w['reasons']))
            self.assertEqual(w['top_reasons'], [r['code'] for r in w['reasons'][:3]])
            self.assertAlmostEqual(w['expected_value'], round(w['engagement_probability'] * w['value_if_engaged'], 2), delta=0.02)
            self.assertIn(w['practice'], ('PR1', 'PR2'))
        # rising-risk evidence states the forecast
        ckd = next(r for r in self.rows['ckd']['reasons'] if r['code'] == 'RISING_EGFR')
        self.assertIn('P(cross 30)', ckd['evidence'])

    def test_worklist_is_ranked_and_complete(self):
        values = [w['expected_value'] for w in self.result['worklist']]
        self.assertEqual(values, sorted(values, reverse=True))
        self.assertEqual([w['priority'] for w in self.result['worklist']], list(range(1, len(values) + 1)))
        self.assertEqual(self.result['learning_loop']['status'], 'needs_outcome_data')
        self.assertEqual(self.result['optimization']['method'], 'exact_sort')

    def test_capacity_slots_and_hours(self):
        two = outreach.execute(self.path, None, {'capacity_patients': 2}, AS_OF)
        self.assertEqual(two['selected'], 2)
        self.assertEqual([w['patient_id'] for w in two['worklist']], [w['patient_id'] for w in self.result['worklist'][:2]])
        hrs = outreach.execute(self.path, None, {'capacity_hours': 1.0}, AS_OF)
        self.assertLessEqual(hrs['capacity']['minutes_used'], 60)
        self.assertEqual(hrs['optimization']['method'], 'exact_knapsack_dp')
        curve = hrs['portfolio']['capacity_curve']
        self.assertEqual([c['expected_value'] for c in curve], sorted(c['expected_value'] for c in curve))  # more capacity never hurts
        self.assertIn('value_lost_per_unit_cut', hrs['portfolio'])
        capped = outreach.execute(self.path, None, {'capacity_patients': 10, 'max_per_practice': 1}, AS_OF)
        self.assertEqual(capped['selected'], 2)
        gaps = outreach.execute(self.path, None, {'capacity_patients': 10, 'require': ['social_need']}, AS_OF)
        self.assertEqual([w['patient_id'] for w in gaps['worklist']], ['sdoh'])
        cohort = outreach.execute(self.path, ['dm', 'htn'], {'capacity_patients': 10}, AS_OF)
        self.assertEqual({w['patient_id'] for w in cohort['worklist']}, {'dm', 'htn'})

    def test_portfolio_variance(self):
        port = self.result['portfolio']
        self.assertGreater(port['sd'], 0)
        self.assertLess(port['interval_80'][0], port['expected_value'])
        self.assertAlmostEqual(port['expected_value'], sum(w['expected_value'] for w in self.result['worklist']), delta=0.1)

    def test_graph_validate_build_answer_and_headline(self):
        plan = {'interpretation': 'outreach', 'coverage': 'supported_observation', 'unmet_requirements': [],
                'nodes': [{'id': 'outreach', 'operation': 'outreach', 'inputs': [], 'parameters': {'capacity_patients': 3}, 'purpose': 'worklist'}]}
        valid = analysis_graph.validate(plan, AS_OF)
        self.assertEqual(valid['nodes'][0]['parameters']['capacity_patients'], 3)
        result = outreach.execute(self.path, None, valid['nodes'][0]['parameters'], AS_OF)
        answer = analysis_graph.build_answer(valid, [{'node': valid['nodes'][0], 'result': result}], {}, 'who should we call? 3 slots')
        self.assertEqual((answer['status'], answer['answer_type'], answer['count']), ('answered', 'table', 3))
        self.assertNotIn('reasons', answer['table'][0])
        self.assertTrue({'patient_id', 'priority', 'expected_value', 'top_reasons', 'suggested_action', 'practice', 'pcp'} <= set(answer['table'][0]))
        self.assertTrue(analysis_graph.headline(answer).startswith('3 of '))
        with self.assertRaises(ValueError):
            analysis_graph.validate({**plan, 'nodes': [{**plan['nodes'][0], 'parameters': {'capacity_patients': -4}}]}, AS_OF)
        self.assertIn('outreach', graph_contracts.TYPED)
        self.assertEqual(graph_contracts.input_contract('outreach')['count'], [0, 1])
        schema = graph_contracts.parameter_schema('outreach', {'cohort_fields': {'record_fields': {}}, 'evidence_catalog': {}})
        self.assertEqual(set(schema['properties']['require']['items']['enum']), set(outreach.CATEGORIES))


class Compiler(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = build([('p1', 'conditions', {'description': 'Diabetes mellitus type 2', 'event_date': '2027-01-01'}),
                          ('p2', 'conditions', {'description': 'Heart failure', 'event_date': '2027-01-01'})],
                         {'p1': {'age_reported': 60}, 'p2': {'age_reported': 70}})
        cls.profile = source_profile.load(cls.path)

    def compile(self, q):
        g, info = local_compiler.compile(q, self.profile, AS_OF)
        self.assertIsNotNone(g, info)
        self.assertEqual(info['route'], 'local_compiler', info)
        analysis_graph.validate(g, AS_OF)
        return g

    def test_outreach_questions_compile(self):
        g = self.compile('who should our care managers call this week? we have 40 slots')
        self.assertEqual([(n['operation'], n['parameters']) for n in g['nodes']], [('outreach', {'capacity_patients': 40})])
        g = self.compile('build an outreach list for diabetics with open gaps, max 100')
        self.assertEqual(g['nodes'][0]['parameters']['specification']['all'][0]['concept'], 'diabetes')
        self.assertEqual(g['nodes'][-1]['parameters'], {'capacity_patients': 100, 'require': ['care_gap']})
        self.assertEqual(g['nodes'][-1]['inputs'], ['population'])
        g = self.compile('prioritize heart failure patients for nurse follow-up with 20 hours of capacity')
        self.assertEqual(g['nodes'][0]['parameters']['specification']['all'][0]['concept'], 'heart_failure')
        self.assertEqual(g['nodes'][-1]['parameters'], {'capacity_hours': 20})
        g = self.compile('which 50 patients give the most value from outreach by practice')
        self.assertEqual(g['nodes'][-1]['parameters'], {'capacity_patients': 50, 'group_by': 'practice'})
        g = self.compile('Care management: who should we call next week? 25 calls, at least 2 per practice')
        self.assertEqual(g['nodes'][-1]['parameters'], {'capacity_patients': 25, 'min_per_practice': 2})
        for q in ['Who should we prioritize for outreach?', 'who should our care managers call this week?']:  # no capacity: engine default
            g = self.compile(q)
            self.assertEqual([(n['operation'], n['parameters']) for n in g['nodes']], [('outreach', {})])
            self.assertEqual(outreach.validate(g['nodes'][-1]['parameters'])['capacity_patients'], 50)

    def test_outreach_list_without_capacity_is_not_hijacked(self):
        for q in ['Pull me the list of diabetics whose most recent A1c is 8 or above',
                  'Nephrology outreach list please: everyone with an eGFR under 30',
                  'How many women between 50 and 74 do we have on Medicare or Medicaid? Sizing a mammography outreach.']:
            self.assertIsNone(local_compiler._outreach_question(q.lower(), self.profile, AS_OF), q)  # no stated capacity
        g, info = local_compiler.compile('Did our care management outreach calls actually cause fewer ED visits? I want the causal effect.', self.profile, AS_OF)
        self.assertEqual(g['coverage'], 'unsupported')


class Bandit(unittest.TestCase):
    def test_thompson_sampling_converges_to_best_arm_in_simulation(self):
        rates = {('care_gap_call', 'diabetes'): 0.20, ('gdmt_review', 'heart_failure'): 0.45, ('sdoh_referral', 'any'): 0.30}
        bandit, history, regret = outreach_bandit.simulate(rates, rounds=60, batch=25, seed=4)
        last = history[-10:]
        share = sum(a.get(('gdmt_review', 'heart_failure'), 0) for a in last) / sum(sum(a.values()) for a in last)
        self.assertGreater(share, 0.8)
        summary = {tuple(s['arm']): s for s in bandit.summary()}
        self.assertGreater(summary[('gdmt_review', 'heart_failure')]['p_best'], 0.95)
        self.assertLess(regret, 0.25 * 60 * 25 * (0.45 - 0.20))  # far below uniform allocation's regret
        lo, hi = summary[('gdmt_review', 'heart_failure')]['ci95']
        self.assertTrue(lo <= 0.45 <= hi)

    def test_status_is_labelled_and_log_updates(self):
        self.assertEqual(outreach_bandit.learning_status()['status'], 'needs_outcome_data')
        log = [{'arm': ['call', 'hf'], 'responded': True}] * 3 + [{'arm': ['call', 'hf'], 'responded': False}]
        s = outreach_bandit.learning_status(log)
        self.assertEqual(s['status'], 'learning_from_logged_outcomes')
        self.assertEqual((s['arms'][0]['attempts'], s['arms'][0]['responses']), (4, 3))
        with self.assertRaises(ValueError): outreach_bandit.ThompsonOutreach([('a', 'b'), ('a', 'b')])


@unittest.skipUnless(STACK.exists(), 'synthetic release not present')
class RealStack(unittest.TestCase):
    def test_real_release_worklist(self):
        r = outreach.execute(STACK, None, {'capacity_patients': 40})
        self.assertEqual(r['selected'], 40)
        self.assertEqual(r['optimization']['certified_ratio'], 1.0)
        self.assertTrue(all(w['top_reasons'] for w in r['worklist']))


if __name__ == '__main__':
    unittest.main()
