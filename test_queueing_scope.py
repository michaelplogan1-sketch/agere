"""Ordinary authored fixtures: queueing scope and admission, no provider calls."""
import sqlite3
import unittest
from unittest.mock import patch

from clinical_core.agere.clinical import queueing as q, analysis_graph as G
from clinical_core.agere.clinical import cohorts, graph_contracts


class QueueingScope(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.executescript('''
          CREATE TABLE visit(patient_id, type_concept, event_date, end_date,
                             start_time, end_time, organization_id);
          INSERT INTO visit VALUES
            ('A','emergency_visit','2026-01-05','2026-01-05','09:00','09:30','O1'),
            ('B','emergency_visit','2026-01-05','2026-01-05','09:00','11:00','O1'),
            ('A','emergency_visit','2026-02-02','2026-02-02','09:00','10:30','O1'),
            ('A','emergency_visit','2026-01-06','2026-01-06','09:00','12:00','O2');
          CREATE TABLE appointment(patient_id, scheduled_date, organization_id, kept);
          INSERT INTO appointment VALUES
            ('A','2026-01-05T09:00','O1',1),('B','2026-01-05T10:00','O1',1),
            ('A','2026-02-02T09:00','O1',1),('A','2026-01-06T09:00','O1',0);
          CREATE TABLE stay(patient_id, admit_date, discharge_date, discharge_known,
                            admit_time_utc, discharge_time_utc, los_days, organization_id);
          INSERT INTO stay VALUES
            ('A','2026-01-05','2026-01-06',1,'2026-01-05T09:00','2026-01-06T09:00',1,'O1'),
            ('B','2026-01-05','2026-01-08',1,'2026-01-05T09:00','2026-01-08T09:00',3,'O1'),
            ('A','2026-02-02','2026-02-03',1,'2026-02-02T09:00','2026-02-03T09:00',1,'O1');
        ''')

    def run_engine(self, ids, **params):
        return q.engine_execute(self.db, ids, {'source':'ed', 'organization_id':'O1',
                                'lag_adjustment':False, **params}, '2026-01-31')

    def test_selected_cohort_and_snapshot_exclude_other_arrivals_and_durations(self):
        report = self.run_engine(['A'])
        self.assertEqual(report['answer']['inputs']['arrivals_used'], 1)
        self.assertEqual(report['answer']['inputs']['mean_service_minutes'], 30)
        self.assertEqual(report['input_scope']['patient_count'], 1)

    def test_empty_population_never_means_everyone(self):
        self.assertEqual(self.run_engine([])['inference_status'], 'insufficient_data')

    def test_omitted_population_keeps_all_patients_within_snapshot_and_organization(self):
        self.assertEqual(self.run_engine(None)['answer']['inputs']['arrivals_used'], 2)

    def test_date_only_arrivals_are_counted_and_uncertainty_is_visible(self):
        self.db.execute("UPDATE visit SET start_time=NULL,end_time=NULL WHERE patient_id='A'")
        r = self.run_engine(['A'], mean_service_minutes=30)
        self.assertEqual(r['answer']['inputs']['arrivals_used'], 1)
        self.assertEqual(r['answer']['inputs']['date_only_arrivals'], 1)
        self.assertTrue(any('hour-of-day pattern is unknown' in x for x in r['warnings']))

    def test_inclusive_event_window_preserves_dates_without_arrivals(self):
        report = self.run_engine(['A'], window={'start':'2026-01-01','end':'2026-01-31'})
        inp = report['answer']['inputs']
        self.assertEqual(inp['arrivals_used'], 1)
        self.assertEqual(inp['weekly_arrivals_base'], .25)  # one of four Mondays, not one per week
        self.assertEqual(report['input_scope']['window'], {'start':'2026-01-01','end':'2026-01-31'})

    def test_summary_keeps_positive_small_rates_and_loads_visible(self):
        report = self.run_engine(['A'], window={'start':'2026-01-01','end':'2026-01-31'})
        text = q.summary(report)
        self.assertIn('0.25/h, offered load 0.125', text)
        self.assertNotIn('(0.0/h', text)

    def test_appointments_and_beds_keep_the_same_scope(self):
        for source, minutes in [('appointment',30), ('beds',1440)]:
            with self.subTest(source=source):
                r = self.run_engine(['A'], source=source, mean_service_minutes=minutes)
                self.assertEqual(r['answer']['inputs']['arrivals_used'], 1)
                self.assertEqual(r['answer']['inputs']['mean_service_minutes'], minutes)

    def test_service_completion_after_snapshot_cannot_leak_into_estimate(self):
        self.db.execute("UPDATE visit SET end_date='2026-02-01', end_time='09:00' WHERE patient_id='A' AND event_date='2026-01-05'")
        r = self.run_engine(['A'])
        self.assertEqual(r['inference_status'], 'insufficient_data')
        self.db.execute("UPDATE stay SET discharge_date='2026-02-01', discharge_time_utc='2026-02-01T09:00',los_days=27 WHERE patient_id='A' AND admit_date='2026-01-05'")
        r = self.run_engine(['A'], source='beds')
        self.assertEqual(r['inference_status'], 'insufficient_data')

    def test_forecast_uses_same_cohort_and_cutoff(self):
        forecast = {'series':[{'status':'forecast','forecast':[{'point':2,'lo80':1,'hi80':3}]}]}
        with patch('clinical_core.agere.clinical.demand.execute', return_value=forecast) as call:
            value, error = q._demand_forecast('fixture', q.validate({'source':'ed'}), ['A'], '2026-01-31')
        self.assertIsNone(error)
        self.assertEqual(value['point'], 2)
        self.assertEqual(call.call_args.args[1], ['A'])
        self.assertEqual(call.call_args.args[3], '2026-01-31')

    def test_forecast_cannot_substitute_a_different_event_definition_or_scope(self):
        for params in [{'source':'appointment'}, {'source':'beds'}, {'source':'clinic'},
                       {'source':'ed','organization_id':'O1'},
                       {'source':'ed','window':{'start':'2026-01-01','end':'2026-01-31'}}]:
            with self.subTest(params=params), patch('clinical_core.agere.clinical.demand.execute') as call:
                value, error = q._demand_forecast('fixture', q.validate(params), ['A'], '2026-01-31')
                self.assertIsNone(value)
                self.assertTrue(error)
                call.assert_not_called()

    def test_manual_scenario_is_explicit_and_never_overrides_empty_cohort(self):
        p = {'hourly_rates':[2]*24, 'mean_service_minutes':30}
        self.assertEqual(self.run_engine([], **p)['inference_status'], 'insufficient_data')
        report = self.run_engine(['A'], **p)
        self.assertEqual(report['input_scope']['basis'], 'user_supplied_scenario')
        self.assertFalse(report['input_scope']['patient_linkage_verified'])
        self.assertTrue(any('dedicated' in x for x in report['assumptions']))

    def test_invalid_and_future_windows_are_rejected(self):
        for window in [{'start':'2026-02-01','end':'2026-01-01'}, {'start':'bad','end':''},
                       {'start':'2026-01-01','end':'2026-02-01'}]:
            with self.subTest(window=window), self.assertRaises(ValueError):
                self.run_engine(['A'], window=window)


class QueueingAdmission(unittest.TestCase):
    def test_population_input_is_valid_for_shared_planner_shape(self):
        plan = {'interpretation':'staffing for the selected population', 'coverage':'supported_observation',
                'unmet_requirements':[], 'nodes':[
                    {'id':'population','operation':'cohort','inputs':[], 'purpose':'population',
                     'parameters':{'specification':cohorts.empty()}},
                    {'id':'staffing','operation':'queueing','inputs':['population'],'purpose':'staffing',
                     'parameters':{'source':'ed','mean_service_minutes':30}}]}
        self.assertEqual(G.validate(plan, '2026-01-31')['nodes'][1]['inputs'], ['population'])

    def test_schema_exposes_event_window(self):
        catalog = {'cohort_fields':{'record_fields':{}}, 'evidence_catalog':{}}
        self.assertIn('window', graph_contracts.parameter_schema('queueing', catalog)['properties'])


class QueueingSharedExecution(unittest.TestCase):
    def setUp(self):
        import test_analysis_graph as fixtures
        from clinical_core.agere.clinical import semantic_layer
        self.fixture = fixtures.GraphExecution()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.stack = self.fixture.stack
        self.stack.allow_live = False
        self.stack.run_graph = lambda plan, **kw: G.execute(self.stack, plan, **kw)
        with sqlite3.connect(semantic_layer.ensure(self.stack.path)) as db:
            db.executemany('''INSERT INTO visit(encounter_id,patient_id,type_concept,event_date,end_date,
                             start_time,end_time,organization_id) VALUES(?,?,?,?,?,?,?,?)''', [
                ('one','p0','emergency_visit','2026-05-04','2026-05-04','09:00','09:30','O1'),
                ('two','p1','emergency_visit','2026-05-04','2026-05-04','09:00','11:00','O1'),
                ('future','p0','emergency_visit','2026-10-01','2026-10-01','09:00','13:00','O1')])

    def run_shared(self, population, **params):
        from clinical_core.agere.clinical import request_plan as R, request_language as L, shared_planner as S
        cat = R.Catalog.load(self.stack.path)
        question = 'List patients; estimate staffing with the queueing engine'
        plan = L.parse(cat, 'List patients')
        plan['analyses'] = [{'operation':'queueing', 'quote':'estimate staffing with the queueing engine',
                             'parameters':{'source':'ed','lag_adjustment':False, **params}}]
        contract = R.compile_request(cat, question, plan, population=population)
        graph = S.graph_for(contract)
        receipt = {'request_contract':contract, 'interpretation':{'execution_accepted':True,
                   'method':'authored_fixture'}, 'new_paid_api_calls':0}
        return S.execute_contract(self.stack, question, graph, receipt, population=population)

    def test_authored_shared_contract_executes_exact_saved_scope_and_replays(self):
        result = self.run_shared(['p0'])
        self.assertEqual(result['answer']['status'], 'answered')
        detail = result['results'][-1]['result']
        self.assertEqual(detail['answer']['inputs']['arrivals_used'], 1)
        self.assertEqual(detail['answer']['inputs']['mean_service_minutes'], 30)
        self.assertEqual(detail['input_scope']['patient_count'], 1)
        self.assertEqual(detail['input_scope']['as_of'], '2026-09-30')
        self.assertIn('input_membership', detail)
        self.assertEqual(self.run_shared(['p0'])['answer'], result['answer'])
        other = self.run_shared(['p1'])['results'][-1]['result']
        self.assertEqual(other['answer']['inputs']['mean_service_minutes'], 120)
        self.assertEqual(result['new_paid_api_calls'], 0)

    def test_shared_empty_cohort_is_unsupported_not_expanded(self):
        result = self.run_shared([])
        self.assertNotEqual(result['answer']['status'], 'answered')
        self.assertEqual(result['results'][-1]['result']['input_scope']['patient_count'], 0)
        self.assertEqual(result['answer']['patient_ids'], [])

    def test_shared_mismatched_forecast_remains_partial_with_historical_scope(self):
        with patch('clinical_core.agere.clinical.demand.execute', side_effect=AssertionError('wrong forecast')) as call:
            result = self.run_shared(['p0'], organization_id='O1', demand_forecast=True)
        call.assert_not_called()
        self.assertEqual(result['answer']['status'], 'partial')
        self.assertTrue(any('forecast' in text.lower() for text in result['answer']['missing']))
        detail = result['results'][-1]['result']
        self.assertIsNone(detail['answer']['inputs']['forecast'])
        self.assertEqual(detail['input_scope']['basis'], 'received_records')
        self.assertFalse(result['answer']['actionable_cohort'])
        self.assertIn('Historical capacity estimate only', q.summary(detail))

    def test_real_demand_path_reports_insufficient_forecast_history(self):
        result = self.run_shared(['p0'], demand_forecast=True)
        self.assertEqual(result['answer']['status'], 'partial')
        self.assertTrue(any('no forecast' in text for text in result['answer']['missing']))
        self.assertEqual(result['new_paid_api_calls'], 0)

    def test_nested_forecast_model_change_invalidates_cached_graph(self):
        import test_analysis_graph as fixture
        forecast = {'series':[{'status':'forecast','forecast':[{'point':2,'lo80':1,'hi80':3}]}]}
        plan = fixture.graph([self.fixture.base, fixture.node('staff', 'queueing', ['base'],
                              {'source':'ed','demand_forecast':True})])
        with patch('clinical_core.agere.clinical.demand.execute', return_value=forecast) as call:
            with patch('clinical_core.agere.clinical.demand.default_model', return_value='baseline'):
                first = G.execute(self.stack, plan)
                self.assertTrue(G.execute(self.stack, plan)['cache_hit'])
            with patch('clinical_core.agere.clinical.demand.default_model', return_value='changed'):
                second = G.execute(self.stack, plan)
        self.assertEqual(call.call_count, 2)
        self.assertFalse(second['cache_hit'])
        self.assertNotEqual(first['result_id'], second['result_id'])


if __name__ == '__main__':
    unittest.main()
