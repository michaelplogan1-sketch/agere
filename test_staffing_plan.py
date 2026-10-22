"""staffing_plan: forecast -> Erlang hour-of-week requirement -> minimum-hours shift MILP, routes and answer contract."""
import unittest
from unittest import mock

from clinical_core.agere.clinical import engine_suite, staffing_plan as S

AS_OF = '2030-05-27'


def fake_queueing(path, patient_ids, params, as_of=None):
    rates = [0.0] * 168
    for d in range(7):
        for h in range(8, 20): rates[d * 24 + h] = 2.0  # 2 arrivals/h in the day, none overnight
    return {'answer': {'objective': 'wait', 'inputs': {'arrival_rates_per_hour': rates, 'arrival_scale_applied': 1.0, 'service_rate_per_hour': 2.0,
                                                       'mean_service_minutes': 30.0, 'service_source': 'test', 'arrival_rate_source': 'test'},
                       'assumption_checks': {}}, 'warnings': [], 'assumptions': ['Erlang C test assumption']}


def fake_demand(path, patient_ids, params, as_of=None):
    weekly = 168.0
    return {'series': [{'status': 'forecast', 'model': 'test', 'forecast': [
        {'period': '2030-05-27', 'point': 100.0, 'in_progress': True},
        *[{'period': f'2030-06-{3 + 7 * i:02d}', 'point': weekly * (1 + 0.5 * i), 'lo80': weekly * 0.8, 'hi80': weekly * 1.5} for i in range(params['horizon'])]]}]}


class Milp(unittest.TestCase):
    def test_covers_every_hour_at_minimum_hours(self):
        req = [0] * 168
        for d in range(7):
            for h in range(8, 16): req[d * 24 + h] = 2
        sol = S.shift_milp(req, [8, 12])
        self.assertTrue(all(c >= r for c, r in zip(sol['coverage'], req)))
        self.assertEqual(sol['paid_hours'], 7 * 2 * 8)  # two 8h shifts per day is exact
        self.assertEqual(sol['slack_hours'], 0)
        self.assertEqual(S.posted(sol['shifts'])[0]['start'], '08:00')

    def test_floor_and_empty(self):
        self.assertEqual(S.shift_milp([0] * 168, [8])['paid_hours'], 0)
        self.assertEqual(S.shift_milp([0] * 168, [8], floor=1)['paid_hours'], 168)


class Engine(unittest.TestCase):
    def test_validate(self):
        self.assertEqual(S.validate({})['weeks'], 4)
        for bad in ({'weeks': 0}, {'source': 'x'}, {'shift_lengths': [3]}, {'nope': 1}, {'staff_to': 'max'}):
            with self.assertRaises(ValueError): S.validate(bad)

    def test_execute_plans_each_forecast_week_and_gap(self):
        with mock.patch('clinical_core.agere.clinical.queueing.engine_execute', fake_queueing), \
             mock.patch('clinical_core.agere.clinical.demand.execute', fake_demand):
            r = S.execute('/nowhere', None, {'weeks': 2, 'current_fte': 5, 'cost_per_hour_usd': 50}, AS_OF)
        self.assertEqual([w['week_of'] for w in r['table']], ['2030-06-03', '2030-06-10'])
        w1, w2 = r['table']
        self.assertGreater(w2['paid_hours'], w1['paid_hours'])          # 50% more demand needs more hours
        self.assertGreaterEqual(w1['hi80_paid_hours'], w1['paid_hours'])
        self.assertEqual(w1['cost_usd'], w1['paid_hours'] * 50)
        self.assertEqual(r['gap_vs_current'][0]['current_hours'], 200)
        a = S.answer(r, {'coverage': 'supported_observation'}, 'q')
        self.assertEqual(a['status'], 'answered'); self.assertIn('FTE', a['value'])

    def test_forecast_failure_heals_to_history(self):
        with mock.patch('clinical_core.agere.clinical.queueing.engine_execute', fake_queueing), \
             mock.patch('clinical_core.agere.clinical.demand.execute', lambda *a, **k: {'series': [{'status': 'insufficient', 'reason': 'short'}]}):
            r = S.execute('/nowhere', None, {'weeks': 1}, AS_OF)
        self.assertEqual(r['status'], 'planned'); self.assertTrue(any('historical weekly volume' in w for w in r['warnings']))


class Route(unittest.TestCase):
    def test_positive(self):
        g, info = S.route('Build a nurse shift roster for the ED for the next 3 weeks based on the visit forecast, 12-hour shifts', {}, AS_OF)
        self.assertEqual(g['nodes'][0]['operation'], 'staffing_plan')
        self.assertEqual(g['nodes'][0]['parameters']['weeks'], 3)
        self.assertEqual(g['nodes'][0]['parameters']['shift_lengths'], [12])
        S.validate(g['nodes'][0]['parameters'])
        g, _ = engine_suite.compile_route('How many FTE should we post for urgent care next month given forecasted demand?', {}, AS_OF)
        self.assertEqual(g['nodes'][0]['parameters']['visit_types'], ['urgent_care_visit'])

    def test_negative(self):
        for q in ('How many ED nurses do we need each hour to keep waits under 30 minutes?',   # queueing
                  'Forecast ED visits for the next 6 months',                                   # demand
                  'How many RNs per shift next week given the census forecast and a 1:5 ratio?',  # demand (ratios)
                  'Which appointments next week are likely no-shows?',                          # scheduling
                  'What is an FTE?'):
            self.assertIsNone(S.route(q, {}, AS_OF), q)


if __name__ == '__main__':
    unittest.main()
