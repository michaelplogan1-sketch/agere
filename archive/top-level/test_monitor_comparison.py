"""Independent finite-count controls and shared native report composition."""
import copy
import math
import unittest
from unittest.mock import patch
from clinical_core.agere.clinical import monitor as M, analysis_graph as G, graph_contracts
import test_historical_discovery_scope as fixture


def row(month, events, exposure=100, group='all', series='ed_rate'):
    return {'series': series, 'group_by': 'none', 'group': group, 'month': month,
            'numerator': events, 'exposure': exposure, 'report_start': '2028-01', 'report_end': '2028-12'}


class ConditionalBaseline(unittest.TestCase):
    def test_exact_conditional_tail_matches_independent_binomial_enumeration(self):
        source = [row('2028-01', 2), row('2028-02', 10)]
        result = M.stable_rate_comparison(source)
        # Under equal rates and equal exposures, K~Binomial(12, 1/2).
        expected = 2 * sum(math.comb(12, k) for k in range(3)) / 2**12
        self.assertAlmostEqual(result[0]['stable_p_value'], expected)
        self.assertAlmostEqual(result[0]['stable_bonferroni_p'], expected * 2)
        self.assertFalse(result[0]['stable_family_flag'])
        self.assertTrue(result[0]['stable_unadjusted_flag'])
        self.assertEqual(source[0], row('2028-01', 2), 'Input observations are not rewritten')

    def test_exposure_not_equal_month_count_determines_null_probability(self):
        result = M.stable_rate_comparison([row('2028-01', 0, 100), row('2028-02', 10, 900)])
        p = .1
        mass = [math.comb(10, k)*p**k*(1-p)**(10-k) for k in range(11)]
        expected = sum(v for v in mass if v <= mass[0]*(1+1e-12))
        self.assertAlmostEqual(result[0]['stable_p_value'], expected)
        self.assertEqual(result[0]['stable_expected_events'], 1)
        self.assertEqual(result[0]['stable_pooled_rate_per_1000'], 10)

    def test_full_family_includes_all_requested_groups_and_non_signals(self):
        result = M.stable_rate_comparison([row('2028-01', 1), row('2028-02', 9),
                    row('2028-01', 5, group='other'), row('2028-02', 5, group='other')])
        self.assertTrue(all(r['stable_family_tests']==4 for r in result))
        self.assertAlmostEqual(result[0]['stable_bonferroni_p'], result[0]['stable_p_value']*4)
        self.assertEqual(result[2]['stable_p_value'], 1)

    def test_missing_exposure_and_single_available_month_are_not_zero_rate_tests(self):
        result = M.stable_rate_comparison([row('2028-01', 0, 0), row('2028-02', 2, 100)])
        self.assertTrue(all(r['stable_p_value'] is None for r in result))
        self.assertTrue(all(r['stable_family_flag'] is None for r in result))
        self.assertEqual(result[0]['stable_expected_events'], None)
        self.assertEqual(result[0]['stable_test_status'], 'insufficient_exposure')

    def test_zero_events_report_no_power_and_non_counts_refuse(self):
        result = M.stable_rate_comparison([row('2028-01', 0), row('2028-02', 0)])
        self.assertTrue(all(r['stable_test_status']=='no_events_no_power' for r in result))
        self.assertTrue(all(r['stable_p_value']==1 for r in result))
        for bad in (1.5, -1, float('inf')):
            with self.subTest(count=bad), self.assertRaises(ValueError):
                M.stable_rate_comparison([row('2028-01', bad)])

    def test_native_handoff_requires_exact_server_binding_and_preserves_uncertain_review(self):
        rows = [row('2028-01', 2), row('2028-02', 10)]
        rows = M.stable_rate_comparison(rows)
        native = {'rows': rows, 'registered_chart': {'chart_status': [{'status':'rate_signal'}]}}
        engine_answer = {'table': rows, 'overall': {'monitor_comparison':native}, 'computed_as': {'operation':'monitor'}}
        calls = [{'name':'execute_analysis','input':{'operation':'monitor'},'result':{'result_view':'native',
                  'engine_result': {'answer':engine_answer}}}]
        answer = {'status':'partial','table':copy.deepcopy(rows),'headline':'A model summary remains uncertain',
            'table_binding':{'result_view':'native','copied_from_executed_result':True},
            'investigation_review':{'completion':{'work':'complete','verification':'uncertain'}}}
        before = copy.deepcopy(answer)
        self.assertTrue(M.attach_comparison(answer,calls))
        self.assertEqual(answer['status'],before['status'])
        self.assertEqual(answer['headline'],before['headline'])
        self.assertEqual(answer['investigation_review'],before['investigation_review'])
        self.assertEqual(answer['overall']['monitor_comparison'],native)
        self.assertIn('1 registered charts',answer['warnings'][0])
        for mutation in ('row','view','proof','interpretation'):
            damaged=copy.deepcopy(before)
            if mutation=='row':damaged['table'][0]['numerator']+=1
            if mutation=='view':damaged['table_binding']['result_view']='different'
            if mutation=='proof':damaged['table_binding']['copied_from_executed_result']=False
            if mutation=='interpretation':damaged['table_binding']['interpreted_columns']=['invented']
            self.assertFalse(M.attach_comparison(damaged,calls))
            self.assertNotIn('monitor_comparison',damaged.get('overall',{}))
        unrelated={'headline':'Unrelated answer'}
        self.assertFalse(M.attach_comparison(unrelated,[]))
        self.assertEqual(unrelated,{'headline':'Unrelated answer'})
        for overall in ('legacy text', ['legacy'], None):
            legacy={'headline':'Unrelated answer','overall':overall}
            expected=copy.deepcopy(legacy)
            self.assertFalse(M.attach_comparison(legacy,[]))
            self.assertEqual(legacy,expected)


class SharedComparison(unittest.TestCase):
    setUp = fixture.HistoricalDiscovery.setUp
    period = fixture.HistoricalDiscovery.period

    def test_received_source_model_and_complete_unknown_rows_reach_shared_answer(self):
        people = {pid: {'months': self.months[:], 'coverage_type': 'current_coverage'} for pid in ['P0','P1','P2']}
        params = {'check': 'control_chart', 'series': ['ed_rate'], 'group_by': 'none', 'period': self.period,
                  'exposure_basis': 'historical_member_months', 'output': 'comparison'}
        with patch.object(M, '_connect', return_value=self.db), \
                patch.object(M.vbc, 'eligibility', return_value=people), \
                patch.object(M.vbc, 'attribution', return_value={}), \
                patch.object(M, 'event_type_gaps', return_value={}), \
                patch('clinical_core.agere.clinical.changepoint_qualification.note', return_value=None):
            report = M.execute(self.path, None, params, '2030-05-27')
        comparison = report['comparison']; rows = comparison['rows']
        self.assertEqual([r['month'] for r in rows], self.months[12:])
        absent = next(r for r in rows if r['month']=='2028-06')
        self.assertIsNone(absent['rate']); self.assertIsNone(absent['stable_p_value'])
        self.assertEqual(absent['events_without_recorded_exposure'], 3)
        capture = next(r for r in rows if r['month']=='2029-04')
        self.assertEqual(capture['received_source_records'], 0)
        self.assertEqual(capture['source_capture_status'], 'no_received_records_capture_unknown')
        self.assertFalse(comparison['causal_change_established'])
        self.assertFalse(comparison['clinical_qualification'])
        self.assertIn('Bonferroni', comparison['baseline']['multiplicity'])
        self.assertIn('does not qualify', comparison['registered_chart']['multiplicity'])
        self.assertEqual(comparison['source_observation']['window_months'], self.months[12:])
        graph = {'interpretation':'Authored source comparison', 'coverage':'supported_observation', 'unmet_requirements':[],
                 'nodes':[{'id':'answer','operation':'monitor','inputs':[],'parameters':params,'purpose':'Compare native methods'}]}
        answer = G.build_answer_core(graph,[{'node':graph['nodes'][0], 'result':report}],{},'Authored source comparison')
        self.assertEqual(answer['table'], rows)
        self.assertEqual(answer['count'], 24)
        self.assertEqual(answer['overall']['monitor_comparison'], comparison)
        self.assertEqual(answer['computed_as']['operation'], 'monitor')

    def test_registered_schema_and_invalid_non_count_comparisons(self):
        catalog={'cohort_fields':{'record_fields':{}},'evidence_catalog':{}}
        schema=graph_contracts.parameter_schema('monitor',catalog)
        self.assertIn('comparison',schema['properties']['output']['enum'])
        for params in ({'output':'comparison'}, {'check':'control_chart','output':'comparison','series':['pmpm']},
                       {'check':'feed','output':'comparison','series':['ed_rate']}):
            with self.subTest(params=params), self.assertRaises(ValueError):M.validate(params)


if __name__=='__main__': unittest.main()
