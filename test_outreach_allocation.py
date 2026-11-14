"""Outreach allocation engine: MILP over (patient, channel, week) under channel capacity, fed by the bandit's learned rates."""
import tempfile, unittest

from clinical_core.agere.clinical import engine_suite as E, outreach_allocation as A
from test_outreach_learning import build, AS_OF


def fake_candidates(rows):
    def make(db, as_of, keep, discount):
        out = [r for r in rows if keep is None or r['patient_id'] in keep]
        return out, {'people_in_scope': len(out), 'risk_model_calibration_by_decile': []}
    return make


def cand(pid, value, practice='A', engage=0.5):
    return {'patient_id': pid, 'value_if_engaged': value, 'expected_value': value * engage, 'engagement_probability': engage,
            'practice': practice, 'risk_score': 1.0, 'reasons': [{'code': 'GAP_X'}], 'suggested_action': 'call'}


class Allocation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.db = build(self.tmp.name); self.addCleanup(self.db.close)
        self.rows = [cand(f'P{i}', 1000 - 10 * i, 'A' if i < 8 else 'B') for i in range(10)]

    def run_(self, p):
        return A.run(self.db, None, A.validate(p), AS_OF, candidates=fake_candidates(self.rows))

    def test_capacity_respected_and_bandit_rates_used(self):
        r = self.run_({'channels': [{'channel': 'phone', 'hours_per_week': 1, 'minutes_per_contact': 20},
                                    {'channel': 'mail', 'hours_per_week': 0, 'slots_per_week': 2}], 'weeks': 2})
        self.assertEqual(r['solver']['status'], 'optimal')
        self.assertEqual(r['assigned'], 10)  # 3 phone + 2 mail per week x 2 weeks = 10 slots
        per = {}
        for x in r['assignment']: per[(x['channel'], x['week'])] = per.get((x['channel'], x['week']), 0) + 1
        self.assertEqual(per, {('phone', 1): 3, ('phone', 2): 3, ('mail', 1): 2, ('mail', 2): 2})
        self.assertAlmostEqual(r['channel_rates']['phone|all'], 31 / 62, places=4)
        self.assertIn('bandit posterior', r['probability_basis']['phone'])
        top = {x['patient_id']: x for x in r['assignment']}
        self.assertEqual((top['P0']['channel'], top['P0']['week']), ('phone', 1))  # highest value -> best channel, earliest week
        self.assertGreaterEqual(r['objective_usd'] + 1e-6, r['greedy_baseline']['objective_usd'])

    def test_shadow_price_positive_when_binding_and_floor(self):
        r = self.run_({'channels': [{'channel': 'phone', 'hours_per_week': 2, 'minutes_per_contact': 20}], 'weeks': 1, 'min_per_practice': 2})
        self.assertEqual(r['assigned'], 6)
        self.assertGreaterEqual(sum(1 for x in r['assignment'] if x['practice'] == 'B'), 2)  # floor forces practice B in
        self.assertFalse(r['solver']['floor_dropped'])
        self.assertGreater(r['shadow_prices'][0]['usd_per_extra_hour_per_week'], 0)
        self.assertLess(r['objective_usd'], r['greedy_baseline']['objective_usd'])  # floor costs value vs unconstrained greedy
        self.assertFalse(r['greedy_baseline']['comparable'])
        self.assertIsNone(r['greedy_baseline']['milp_lift_usd'])

    def test_infeasible_floor_preserves_constraints_and_explains_a_separate_scenario(self):
        r = self.run_({'channels': [{'channel': 'phone', 'hours_per_week': 0.4, 'minutes_per_contact': 20}], 'weeks': 1, 'min_per_practice': 3})
        self.assertFalse(r['solver']['floor_dropped'])
        self.assertEqual(r['assigned'], 0)
        self.assertEqual(r['relaxed_scenario']['assigned'],1)
        self.assertEqual(r['status'],'partial')
        a = A.answer(r, {'coverage': 'supported_observation'}, 'q')
        self.assertEqual(a['status'],'partial');self.assertTrue(a['missing'])

    def test_unlogged_channel_falls_back_to_prior(self):
        r = self.run_({'channels': [{'channel': 'portal', 'hours_per_week': 1, 'minutes_per_contact': 10}], 'weeks': 1})
        self.assertIn('prior', r['probability_basis']['portal'])
        self.assertEqual(r['assignment'][0]['response_probability'], 0.5)
        a = A.answer(r, {'coverage': 'supported_observation'}, 'q')
        self.assertEqual(a['status'], 'answered'); self.assertIn('MILP optimal', a['value'])

    def test_validate(self):
        for bad in ({'weeks': 0}, {'channels': [{'channel': 'x', 'hours_per_week': 0, 'slots_per_week': 0}]},
                    {'channels': [{'channel': 'phone'}, {'channel': 'phone'}]}, {'zzz': 1}):
            with self.assertRaises(ValueError): A.validate(bad)
        self.assertEqual(len(A.validate({})['channels']), 3)

    def test_route(self):
        g, _ = E.compile_route('Allocate outreach across phone, text and mail for the next 3 weeks with 12 staff hours of phone a week', {}, AS_OF)
        n = g['nodes'][0]
        self.assertEqual(n['operation'], 'outreach_allocation')
        self.assertEqual(n['parameters']['weeks'], 3)
        self.assertEqual(n['parameters']['channels'][0], {'channel': 'phone', 'hours_per_week': 12.0, 'slots_per_week': 0})
        for q in ('Prioritize 100 patients for outreach across practices', 'How much should each clinic overbook?',
                  'Which outreach channel works best?', 'How many ED nurses do we need each hour to keep waits under 30 minutes?'):
            self.assertIsNone(A.route(q, {}, AS_OF), q)


if __name__ == '__main__':
    unittest.main()
