"""Settlement bridge: realized -> forecast quality gate -> modeled utilization waterfall, dedupe across work items, route."""
import sqlite3
import unittest
from unittest import mock

from clinical_core.agere.clinical import settlement_bridge as B, engine_suite, measures, outreach, vbc

AS_OF = '2030-05-27'


def cand(pid, reach, reasons):
    rs = [{'code': c, 'value_if_engaged': v} for c, v in reasons]
    return {'patient_id': pid, 'engagement_probability': reach, 'reasons': rs,
            'expected_value': reach * outreach.combine([v for _, v in reasons], 0.5)}


CANDS = [cand('A', 0.5, [('UTIL_AVOIDABLE', 2000), ('GAP_BP_UNCONTROLLED', 300), ('GAP_STATIN', 350)]),
         cand('B', 0.8, [('GAP_BP_UNCONTROLLED', 300)]), cand('C', 0.4, [('UTIL_AVOIDABLE', 1000)])]


class Pieces(unittest.TestCase):
    def test_attainment_and_quality(self):
        self.assertEqual(B.attainment(0.35, 0.7, 'higher_is_better'), 0.5)
        self.assertEqual(B.attainment(0.30, 0.15, 'lower_is_better'), 0.5)
        self.assertEqual(B.attainment(0.9, 0.7, 'higher_is_better'), 1.0)
        rows = [{'rate': 0.35, 'projected_rate': 0.7, 'target': 0.7, 'direction': 'higher_is_better'},
                {'rate': 0.3, 'projected_rate': 0.3, 'target': None, 'direction': 'higher_is_better'}]
        self.assertEqual((B.quality_score(rows), B.quality_score(rows, 'projected_rate')), (None,None))
        self.assertEqual((B.quality_score(rows[:1]),B.quality_score(rows[:1],'projected_rate')),(.5,1))

    def test_utilization_counted_once_per_patient(self):
        chosen, per, d = B.work_items(CANDS, 10)
        self.assertEqual(d['utilization_once_per_patient'], 0.5 * 2000 + 0.4 * 1000)
        self.assertEqual(d['naive_utilization_per_work_item'], 0.5 * 2000 * 3 + 0.4 * 1000)
        self.assertLess(d['overlap_discounted_planning_value'], d['naive_planning_value_all_reasons'])
        self.assertEqual(len(B.work_items(CANDS, 1)[0]), 1)

    def test_terms_for_heals_missing_option(self):
        terms, note = B.terms_for('ACO REACH')
        self.assertIn('assumed', note)
        self.assertEqual(B.terms_for('MSSP ENHANCED')[1], None)
        with self.assertRaises(ValueError): B.terms_for('No Such Program')

    def test_validate(self):
        for bad in ({'measures': ['zzz']}, {'targets': {'cbp': 2}}, {'capacity_patients': 0}, {'program': 'nope'}, {'x': 1}):
            with self.assertRaises(ValueError): B.validate(bad)


class Run(unittest.TestCase):
    def test_waterfall_sums_and_labels(self):
        people = {p: {'months': ['2030-01']} for p in 'ABC'}
        rep = {'declined': False, 'overall': {'numerator': 35, 'denominator': 100},
               'care_gaps': [{'patient_id': 'A'}, {'patient_id': 'B'}] * 20}
        with mock.patch.object(vbc, 'eligibility', return_value=people), mock.patch.object(vbc, 'attribution', return_value={}), \
                mock.patch.object(vbc, '_cost', return_value=([{'member_months': 3600, 'allowed_pmpm_completed': 900.0}], '')), \
                mock.patch.object(outreach, 'candidates', return_value=(CANDS, {})), \
                mock.patch.object(measures, 'Context', return_value=None), mock.patch.object(measures, 'evaluate_db', return_value=rep):
            r = B.run(sqlite3.connect(':memory:'), None, B.validate({'benchmark_pmpm': 1200, 'measures': ['cbp'], 'population': 'all',
                                                                      'closure_if_engaged': 1.0}), AS_OF)
        w = {x['label']: x['dollars'] for x in r['waterfall']}
        self.assertAlmostEqual(w['current_scenario'] + w['forecast'] + w['modeled'], w['projected'], places=1)
        self.assertGreater(w['forecast'], 0)   # closures raise the cbp attainment
        self.assertGreater(w['modeled'], 0)
        self.assertEqual(r['quality_score']['current'], 0.5)
        self.assertEqual(r['gross_utilization_savings']['dollars'], 1400.0)
        self.assertEqual(r['dedupe']['adjustment_utilization'], 2000.0)
        self.assertEqual(r['patient_ids'], ['A', 'B', 'C'])
        ans = B.answer(r, {'coverage': 'supported_observation'}, 'q')
        self.assertEqual(ans['number'], w['projected']); self.assertIn('current settlement scenario', ans['value'])
        self.assertFalse(r['actual_settlement_established'])


class Route(unittest.TestCase):
    def test_routes(self):
        g, _ = engine_suite.compile_route('What would working the worklist earn us under ACO REACH?', {}, AS_OF)
        self.assertEqual((g['nodes'][0]['operation'], g['nodes'][0]['parameters']), ('settlement_bridge', {'program': 'ACO REACH'}))
        g, _ = engine_suite.compile_route('Bridge our gap closure work to shared savings dollars with a $1,050 benchmark', {}, AS_OF)
        self.assertEqual(g['nodes'][0]['parameters']['benchmark_pmpm'], 1050.0)
        self.assertIsNone(B.route('What is our settlement under ACO REACH Global 2026 with a benchmark of $1,100 PMPM?', {}, AS_OF))
        self.assertIsNone(B.route('Who should we call this week for outreach?', {}, AS_OF))


if __name__ == '__main__':
    unittest.main()
