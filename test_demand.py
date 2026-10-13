"""Demand forecasting: model recovery and interval coverage on simulated series, data-gap screening, validation,
the operator end to end on a fixture stack, answer/console fields, compiler routing and a real-release smoke run."""
import json, math, unittest
from pathlib import Path

import numpy as np

from clinical_core.agere import ask_view
from clinical_core.agere.clinical import analysis_graph, capability_registry, demand, graph_contracts, graph_planner, local_compiler, \
    semantic_layer, source_profile
from test_aggregate_explain import AS_OF, build, pcp, visit


def months(start, n):
    out, k = [], start
    for _ in range(n): out.append(k); k = demand.next_period(k, 'month')
    return out


def weeks(start, n):
    out, k = [], start
    for _ in range(n): out.append(k); k = demand.next_period(k, 'week')
    return out


class Methods(unittest.TestCase):
    def test_periods(self):
        self.assertEqual(demand.period_of('2029-08-15', 'week'), '2029-08-13')
        self.assertEqual(demand.period_of('2029-08-15', 'month'), '2029-08')
        self.assertEqual(demand.next_period('2029-12', 'month'), '2030-01')
        self.assertEqual(demand.next_period('2029-01', 'month', -1), '2028-12')
        self.assertEqual(demand.period_days('2028-02', 'month'), 29)

    def test_recovers_known_seasonal_process(self):
        # Poisson counts, mean 50 x exp(0.35 sin(2 pi t / 12) + 0.004 t): a seasonal model must win and track the true mean.
        n, h = 72, 6
        t = np.arange(n + h)
        truth = 50 * np.exp(0.35 * np.sin(2 * np.pi * t / 12) + 0.004 * t)
        wins, errs = 0, []
        for seed in range(5):
            y = list(np.random.default_rng(seed).poisson(truth[:n]).astype(float))
            s = demand.forecast_series(months('2023-01', n), y, 'month', h)
            self.assertEqual(s['status'], 'forecast')
            self.assertTrue(s['seasonal_models_considered'])
            wins += 'seasonal' in s['model'] or 'winters' in s['model']
            bt = s['backtest']
            self.assertEqual(bt['baseline'], 'seasonal_naive')
            self.assertLess(bt['selected_mase'], bt['baseline_mase'])  # beats the seasonal naive baseline
            point = np.array([r['point'] for r in s['forecast']])
            errs.append(float(np.mean(np.abs(point - truth[n:]) / truth[n:])))
            self.assertFalse([d for d in s['data_issues'] if d['kind'] in ('gap', 'spike')])
        self.assertGreaterEqual(wins, 4)
        self.assertLess(np.mean(errs), 0.10)

    def test_interval_coverage_near_nominal(self):
        # Stationary overdispersed counts: sequentially calibrated conformal intervals cover close to their nominal level.
        hits80 = hits95 = total = 0
        for seed in range(24):
            rng = np.random.default_rng(100 + seed)
            lam = 20 * rng.gamma(20, 1 / 20, size=60)
            y = list(rng.poisson(lam).astype(float))
            s = demand.forecast_series(weeks('2028-01-03', 60), y, 'week', 4)
            c80, c95 = s['backtest']['coverage']['80'], s['backtest']['coverage']['95']
            hits80 += c80['conformal_sequential'] * c80['evaluated']; total += c80['evaluated']
            hits95 += c95['conformal_sequential'] * c95['evaluated'] if c95['evaluated'] else 0
            f = s['forecast'][0]
            self.assertTrue(f['lo95'] <= f['lo80'] <= f['point'] <= f['hi80'] <= f['hi95'])
        self.assertGreater(total, 1000)
        self.assertTrue(0.72 <= hits80 / total <= 0.88, hits80 / total)

    def test_gap_and_spike_detected_fit_on_recent_regime(self):
        rng = np.random.default_rng(7)
        y = list(rng.poisson(15, 60).astype(float))
        y[20] = 95.0                                      # one-month batch spike
        for i in range(25, 45): y[i] = 0.0                # feed gap
        y[45:] = list(rng.poisson(10, 15).astype(float))  # resumes at a new level
        per = months('2024-01', 60)
        s = demand.forecast_series(per, y, 'month', 6)
        kinds = {d['kind']: d['periods'] for d in s['data_issues']}
        self.assertEqual(kinds['gap'], f'{per[25]}..{per[44]}')
        self.assertEqual(kinds['spike'], per[20])
        self.assertEqual(s['regime']['start'], per[45])
        self.assertEqual(s['status'], 'forecast')
        self.assertTrue(all(h['status'] in ('gap', 'spike', 'before_regime') for h in s['history'][:45]))
        self.assertLess(s['forecast'][0]['point'], 14)   # the post-gap level, not the old one
        self.assertIn('backtest', s)

    def test_trailing_gap_and_short_regime_decline(self):
        y = [12.0] * 20 + [0.0] * 6
        s = demand.forecast_series(months('2027-01', 26), y, 'month', 3)
        self.assertEqual(s['status'], 'insufficient'); self.assertIn('feed gap', s['reason'])
        y = [12.0] * 20 + [0.0] * 6 + [11.0, 13.0, 12.0]
        s = demand.forecast_series(months('2027-01', 29), y, 'month', 3)
        self.assertEqual(s['status'], 'insufficient'); self.assertIn('usable', s['reason'])

    def test_level_break_moves_regime(self):
        rng = np.random.default_rng(3)
        y = list(rng.poisson(10, 40).astype(float)) + list(rng.poisson(25, 20).astype(float))
        s = demand.forecast_series(months('2024-01', 60), y, 'month', 3)
        self.assertIn('level_break', {d['kind'] for d in s['data_issues']})
        self.assertGreaterEqual(s['regime']['start'], months('2024-01', 60)[38])
        self.assertGreater(s['forecast'][0]['point'], 18)

    def test_validation(self):
        for bad in [{}, {'measure': 'mortality'}, {'measure': 'ed_visits', 'frequency': 'day'}, {'measure': 'ed_visits', 'horizon': 30},
                    {'measure': 'ed_visits', 'frequency': 'week', 'horizon': 200}, {'measure': 'ed_visits', 'group_by': 'pcp'},
                    {'measure': 'bed_days', 'target_occupancy': 1.5}, {'measure': 'discharges', 'minutes_per_unit': -5},
                    {'measure': 'ed_visits', 'colour': 'red'}]:
            with self.assertRaises(ValueError, msg=bad): demand.validate(bad)
        p = demand.validate({'measure': 'ed_visits'})
        self.assertEqual((p['frequency'], p['horizon'], p['group_by'], p['target_occupancy'], p['staffing']), ('month', 6, 'none', 0.85, False))
        self.assertEqual(demand.validate({'measure': 'ed_visits', 'frequency': 'week'})['horizon'], 13)
        self.assertTrue(demand.validate({'measure': 'discharges', 'minutes_per_unit': 45, 'fte_hours_per_week': 36})['staffing'])

    def test_planner_schema_payload_validates(self):
        from test_graph_contracts import catalog
        schema = graph_contracts.parameter_schema('demand', catalog())
        self.assertIn('demand', graph_contracts.TYPED)
        self.assertEqual(set(schema['required']), set(schema['properties']))
        self.assertEqual(graph_contracts.input_contract('demand')['count'], [0, 1])
        full = {'measure': 'bed_days', 'frequency': 'month', 'horizon': 3, 'group_by': 'none', 'top': 0, 'target_occupancy': 0,
                'peak_quantile': 0, 'staffing': False, 'minutes_per_unit': None, 'fte_hours_per_week': None}
        p = demand.validate(full)
        self.assertEqual((p['target_occupancy'], p['peak_quantile'], p['top']), (0.85, 0.95, 8))
        self.assertIn('demand', graph_planner.CONTRACTS)
        self.assertEqual(capability_registry.PRODUCT['demand']['population'], 'optional')
        self.assertIn('demand', analysis_graph.OPERATIONS)


def ed_records():
    """ED visits 2026-01..2029-08 at ~12/month with a feed gap 2027-06..2028-03 and a spike in 2027-03; stays with and without discharge."""
    rng = np.random.default_rng(11)
    recs = []
    for k in months('2026-01', 44):
        if '2027-06' <= k <= '2028-03': continue
        n = 60 if k == '2027-03' else int(rng.poisson(12))
        for j in range(n):
            day = f'{k}-{1 + int(rng.integers(0, 28)):02d}'
            if day <= AS_OF: recs.append(visit(f'p{j % 6}', 'emergency', day, 'H1' if j % 3 else 'H2'))
    recs += [visit('p0', 'inpatient', '2029-03-01', 'H1', '2029-03-05'), visit('p1', 'observation', '2029-04-02', 'H1', '2029-04-03'),
             visit('p2', 'inpatient', '2029-05-01')]  # p2: no received discharge
    recs += [pcp(f'p{i}', 'drA', 'PR2' if i % 2 else 'PR1') for i in range(6)]
    recs += [(f'p{i}', 'coverage', {'coverage_type': 'medicare' if i % 2 else 'commercial', 'event_date': '2026-01-01'}) for i in range(6)]
    recs += [('p0', 'adt', {'event': 'emergency_registration', 'event_date': '2028-01-01'})]
    return recs


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = build(ed_records(), {f'p{i}': {'age_reported': 60, 'sex_reported': 'F'} for i in range(6)})
        semantic_layer.build(cls.path)
        cls.ids = [f'p{i}' for i in range(6)]

    def test_ed_forecast_end_to_end(self):
        r = demand.execute(self.path, None, {'measure': 'ed_visits', 'horizon': 6})
        main = r['series'][0]
        self.assertEqual(main['status'], 'forecast')
        self.assertEqual(main['regime']['start'], '2028-04')
        kinds = {d['kind']: d['periods'] for d in main['data_issues']}
        self.assertEqual(kinds['gap'], '2027-06..2028-03')
        self.assertEqual(kinds['spike'], '2027-03')
        self.assertTrue(main['forecast'][0]['in_progress'])          # 2029-08 is incomplete at the snapshot
        self.assertEqual(main['forecast'][0]['period'], '2029-08')
        self.assertEqual(len(main['forecast']), 7)
        self.assertIn('naive', main['backtest']['mase'])
        self.assertIn('Fitted on', demand.summary(r))
        self.assertIn('MASE', demand.summary(r))
        json.dumps(r)

    def test_beds_capacity_and_unknown_discharge_flagged(self):
        r = demand.execute(self.path, self.ids, {'measure': 'bed_days', 'horizon': 2, 'target_occupancy': 0.8})
        self.assertEqual(r['flags']['stays_without_received_discharge'], 1)
        self.assertIn('no received discharge', r['notes'][0])
        main = r['series'][0]
        # 4 + 1 nights observed; the series is too short to forecast, and says so instead of guessing
        self.assertEqual(sum(h['value'] for h in main['history']), 5)
        self.assertEqual(main['status'], 'insufficient')

    def test_capacity_arithmetic(self):
        p = demand.validate({'measure': 'bed_days', 'frequency': 'month', 'target_occupancy': 0.85, 'minutes_per_unit': 30, 'fte_hours_per_week': 35})
        rows = demand.capacity_rows([{'period': '2029-09', 'point': 300.0, 'hi80': 360.0}], 'month', p, 'bed_days')
        r = rows[0]
        self.assertEqual(r['average_census'], 10.0)
        self.assertEqual(r['beds_at_target_occupancy'], math.ceil(10 / 0.85))
        self.assertEqual(r['peak_day_beds'], 15)    # Poisson(10): P(X <= 15) = 0.951
        self.assertAlmostEqual(r['fte'], 300 * 30 / 60 / (35 * 30 / 7), places=2)

    def test_staffing_never_invents_ratios(self):
        plan = analysis_graph.validate({'interpretation': 'q', 'coverage': 'supported_observation', 'unmet_requirements': [], 'nodes': [
            {'id': 'demand', 'operation': 'demand', 'inputs': [], 'purpose': 'staff', 'parameters': {'measure': 'ed_visits', 'staffing': True}}]}, AS_OF)
        report = demand.execute(self.path, None, plan['nodes'][0]['parameters'])
        answer = analysis_graph.build_answer(plan, [{'node': plan['nodes'][0], 'result': report}], {}, 'q')
        self.assertEqual(answer['status'], 'partial')
        self.assertTrue(any('does not invent staffing ratios' in m for m in answer['missing']))
        self.assertFalse(any('fte' in c for c in report['capacity']))

    def test_answer_and_console_fields(self):
        plan = analysis_graph.validate({'interpretation': 'q', 'coverage': 'supported_observation', 'unmet_requirements': [], 'nodes': [
            {'id': 'population', 'operation': 'cohort', 'inputs': [], 'purpose': 'all', 'parameters': {'specification': {'all': [], 'any': [], 'exclude': []}}},
            {'id': 'demand', 'operation': 'demand', 'inputs': ['population'], 'purpose': 'f', 'parameters': {'measure': 'ed_visits', 'group_by': 'facility'}}]}, AS_OF)
        report = demand.execute(self.path, self.ids, plan['nodes'][-1]['parameters'])
        answer = analysis_graph.build_answer(plan, [{'node': plan['nodes'][0], 'result': {}}, {'node': plan['nodes'][1], 'result': report}], {}, 'q')
        answer['headline'] = analysis_graph.headline(answer)
        answer = json.loads(json.dumps(answer, allow_nan=False, default=str))
        self.assertEqual((answer['status'], answer['answer_type']), ('answered', 'table'))
        self.assertTrue(answer['headline'].startswith('ED visits forecast for the next 6 months'))
        self.assertEqual(set(answer['table'][0]) >= {'group', 'period', 'point', 'lo80', 'hi80', 'lo95', 'hi95'}, True)
        series = answer['overall']['demand']
        self.assertEqual([s['group'] for s in series][:1], ['all'])
        self.assertTrue({'H1', 'H2'} <= {s['group'] for s in series})
        self.assertTrue(all({'period', 'value', 'status'} <= set(h) for h in series[0]['history']))
        self.assertIn('backtest', series[0])
        self.assertEqual(analysis_graph.benchmark_references(answer, 'q', 'demand'), [])
        page = ask_view.PAGE
        self.assertIn('function vizDemand', page); self.assertIn('if(o.demand)return vizDemand(a)', page)


class Compiler(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        recs = [visit('p0', 'emergency', '2028-01-01'), ('p0', 'adt', {'event': 'emergency_registration', 'event_date': '2028-01-01'}),
                ('p0', 'coverage', {'coverage_type': 'medicare', 'event_date': '2026-01-01'})]
        cls.profile = source_profile.load(build(recs, {'p0': {'age_reported': 70, 'sex_reported': 'F'}}))

    def compile(self, q):
        return local_compiler.compile(q, self.profile, AS_OF)

    def demand(self, q):
        g, info = self.compile(q)
        self.assertEqual(info.get('route'), 'local_compiler', (q, info.get('reason')))
        self.assertEqual(g['nodes'][-1]['operation'], 'demand', q)
        return g, g['nodes'][-1]['parameters']

    def test_phrasings(self):
        _, p = self.demand('Forecast ED visits for the next 6 months')
        self.assertEqual((p['measure'], p['frequency'], p['horizon']), ('ed_visits', 'month', 6))
        _, p = self.demand('How many beds will we need next quarter?')
        self.assertEqual((p['measure'], p['frequency'], p['horizon']), ('bed_days', 'month', 3))
        _, p = self.demand('Projected primary care appointment demand by practice')
        self.assertEqual((p['measure'], p['group_by']), ('appointments', 'practice'))
        _, p = self.demand('Weekly admissions forecast with uncertainty')
        self.assertEqual((p['measure'], p['frequency'], p['horizon']), ('inpatient_admissions', 'week', 13))
        _, p = self.demand("How many nurses do we need for next month's discharges?")
        self.assertEqual((p['measure'], p['horizon'], p['staffing'], p['minutes_per_unit']), ('discharges', 1, True, None))
        _, p = self.demand("How many care managers do we need for next month's discharges at 45 minutes per discharge and 36 hours per week?")
        self.assertEqual((p['minutes_per_unit'], p['fte_hours_per_week']), (45.0, 36.0))
        _, p = self.demand('How many beds will we need next quarter at 90% occupancy?')
        self.assertEqual(p['target_occupancy'], 0.9)
        g, p = self.demand('Forecast ED visits for medicare patients by facility over the next 12 weeks')
        self.assertEqual((p['frequency'], p['horizon'], p['group_by']), ('week', 12, 'facility'))
        self.assertEqual(g['nodes'][0]['operation'], 'cohort')
        g, p = self.demand('Predict ED volume for diabetics next 3 months')
        self.assertEqual(g['nodes'][0]['parameters']['specification']['all'][0]['concept'], 'diabetes')

    def test_does_not_capture_patient_lab_forecasts(self):
        for q in ['Which CKD patients are projected to drop below eGFR 30 within a year?',
                  'Which diabetics are likely to have an A1c above 9 next year?',
                  'Forecast eGFR for CKD patients over the next 6 months',
                  'Who is predicted to cross a systolic of 160?']:
            g, info = self.compile(q)
            self.assertNotIn('demand', [n['operation'] for n in (g or {}).get('nodes', [])], q)

    def test_declines_when_unclear(self):
        for q in ['Forecast patient volume', 'Forecast ED visits by pcp', 'Forecast ED visits and admissions next month']:
            g, info = self.compile(q)
            self.assertEqual(info.get('route'), 'fallback', q)
            self.assertIsNone(g, q)

    def test_other_questions_unchanged(self):
        g, _ = self.compile('How many ED visits in 2028?')
        self.assertEqual(g['nodes'][-1]['operation'], 'aggregate')
        g, _ = self.compile('who should our care managers call this week? we have 40 slots')
        self.assertEqual(g['nodes'][-1]['operation'], 'outreach')
        g, _ = self.compile('What does the readmission curve look like by payer?')
        self.assertEqual(g['nodes'][-1]['operation'], 'survival')

    def test_acceptance_phrasings(self):
        cases = json.loads((Path(__file__).resolve().parent / 'bench' / 'acceptance' / 'demand.json').read_text())
        self.assertEqual(len(cases), 8)
        for c in cases:
            g, info = self.compile(c['question'])
            self.assertEqual(c['expect_operation'], 'demand')
            self.assertEqual((g or {'nodes': [{}]})['nodes'][-1].get('operation'), 'demand', (c['question'], info.get('reason')))


def _stack():
    for root in (Path(__file__).resolve().parent, Path('/Users/mikel/clinical-insights-local')):
        s = root / 'artifacts' / 'agere-healthcare-v123' / 'stack'
        if (s / 'semantic.sqlite3').exists() and (s / 'run.json').exists(): return s
    return None


STACK = _stack()


@unittest.skipUnless(STACK, 'real synthetic release not present')
class RealRelease(unittest.TestCase):
    def test_ed_forecast_skips_generator_gap(self):
        original = semantic_layer.ensure
        semantic_layer.ensure = lambda path: STACK / 'semantic.sqlite3'  # read the release as built (mode=ro); never rebuild it from a test
        try:
            r = demand.execute(STACK, None, {'measure': 'ed_visits', 'frequency': 'week', 'horizon': 13})
        finally:
            semantic_layer.ensure = original
        main = r['series'][0]
        self.assertEqual(main['status'], 'forecast')
        gaps = [d['periods'] for d in main['data_issues'] if d['kind'] == 'gap']
        self.assertTrue(any(g.split('..')[0] <= '2027-06-01' and g.split('..')[1] >= '2028-06-01' for g in gaps), gaps)
        self.assertGreaterEqual(main['regime']['start'], '2028-09-01')
        self.assertIn('naive', main['backtest']['mase'])
        self.assertTrue(all(row['lo95'] >= 0 for row in r['table']))


if __name__ == '__main__':
    unittest.main()
