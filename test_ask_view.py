"""/ask workspace page: renders as one self-contained page with the demo script, and every answer type it draws carries the
fields the visuals read (survival curves, monitor chart points, outreach portfolio + reason labels, program-effect interval,
VBC settlement risk + fair allocation, trajectory rows). Fixtures are the operator suites' own synthetic stacks."""
import json
import re
import unittest

from clinical_core.agere import ask_view
from clinical_core.agere.clinical import analysis_graph, monitor, outreach, program_effect, survival, trajectory, vbc

import test_monitor
import test_outreach
import test_program_effect
import test_survival
import test_vbc

AS_OF = '2029-08-15'


def plan_for(operation, parameters, cohort=False):
    nodes = [{'id': 'population', 'operation': 'cohort', 'inputs': [], 'purpose': 'all',
              'parameters': {'specification': {'all': [], 'any': [], 'exclude': []}}}] if cohort else []
    nodes.append({'id': 'answer', 'operation': operation, 'inputs': ['population'] if cohort else [], 'parameters': parameters, 'purpose': operation})
    return analysis_graph.validate({'interpretation': operation, 'coverage': 'supported_observation', 'unmet_requirements': [], 'nodes': nodes}, AS_OF)


def answer_of(plan, report, question='q'):
    results = [{'node': n, 'result': {}} for n in plan['nodes'][:-1]] + [{'node': plan['nodes'][-1], 'result': report}]
    answer = analysis_graph.build_answer(plan, results, {}, question)
    answer['headline'] = analysis_graph.headline(answer)
    return json.loads(json.dumps(answer, allow_nan=False, default=str))  # what /api/stack/compose actually sends


class Page(unittest.TestCase):
    def setUp(self):
        self.html = ask_view.page()

    def test_single_self_contained_page(self):
        self.assertTrue(self.html.startswith('<!doctype html>'))
        self.assertIn('<title>Agere Workspace</title>', self.html)
        self.assertNotRegex(self.html, r'<script[^>]+\bsrc=|<link[^>]+stylesheet|@import')  # no external assets
        self.assertIn('prefers-color-scheme:dark', self.html)
        self.assertIn('max-width:800px', self.html)  # single-column layout for phones
        for route in ('/api/stack/compose', '/api/stack/conversation', '/api/stack/briefing', '/api/stack/economics', '/api/stack/correct'):
            self.assertIn(route, self.html)
        for hook in ('conversation_id', 'Answered as', 'Flag as wrong', 'Download CSV', 'Computed as', 'Benchmark reference', 'Standards'):
            self.assertIn(hook, self.html)

    def test_demo_script_is_embedded_and_well_formed(self):
        raw = re.search(r'<script id="demo-script" type="application/json">(.*?)</script>', self.html, re.S).group(1)
        steps = json.loads(raw)
        self.assertEqual(steps, ask_view.DEMO_STEPS)
        self.assertEqual(len(steps), 10)
        self.assertEqual(steps[0]['kind'], 'briefing')
        self.assertEqual(steps[-1]['kind'], 'followup')
        self.assertEqual(len({s['id'] for s in steps}), 10)
        for s in steps:
            self.assertIn(s['kind'], ('briefing', 'ask', 'followup'))
            self.assertTrue(s['label'] and s['note'])
            if s['kind'] != 'briefing': self.assertTrue(s['question'].strip())
        self.assertIn('40 slots', ' '.join(s.get('question', '') for s in steps))

    def test_script_has_no_closing_tag_injection(self):
        self.assertEqual(self.html.count('</script>'), 2)


class AnswerFields(unittest.TestCase):
    """Fields the console's visuals read, per answer type, on the operator suites' fixtures."""

    def test_monitor_exposes_chart_points(self):
        test_monitor.EndToEnd.setUpClass()
        plan = plan_for('monitor', {'check': 'control_chart', 'series': ['admit_rate'], 'group_by': 'practice'})
        report = monitor.execute(test_monitor.EndToEnd.path, None, plan['nodes'][0]['parameters'], AS_OF)
        a = answer_of(plan, report)
        charts = a['overall']['charts']
        self.assertEqual(len(charts), len(report['chart_status']))
        self.assertTrue(charts)
        c = charts[0]
        self.assertTrue({'series', 'label', 'group_by', 'group', 'status', 'center', 'points', 'rate_signal_months', 'data_signal_months'} <= set(c))
        self.assertTrue(all(set(p) == {'month', 'value', 'lcl', 'ucl'} for p in c['points']))
        self.assertEqual([p['month'] for p in c['points']], [p['month'] for p in report['chart_status'][0]['chart']])
        self.assertTrue(all('chart' not in row for row in a['table']))  # the table itself is unchanged
        self.assertTrue(any(c['status'] == 'rate_signal' and c['rate_signal_months'] for c in charts))

    def test_outreach_exposes_portfolio_and_reason_labels(self):
        test_outreach.Worklist.setUpClass()
        plan = plan_for('outreach', {'capacity_patients': 5})
        report = outreach.execute(test_outreach.Worklist.path, None, plan['nodes'][0]['parameters'], AS_OF)
        a = answer_of(plan, report, 'who should we call? 5 slots')
        o = a['overall']
        self.assertTrue({'portfolio', 'by_group', 'capacity', 'candidates', 'optimization', 'limitations', 'reason_labels'} <= set(o))
        codes = {c for row in a['table'] for c in row['top_reasons']}
        self.assertTrue(codes and codes <= set(o['reason_labels']))
        for code, info in o['reason_labels'].items():
            self.assertEqual((info['category'], info['label']), outreach.REASONS[code][:2])
        self.assertNotIn('reasons', a['table'][0])

    def test_survival_exposes_curves(self):
        test_survival.Fixture.setUpClass()
        plan = plan_for('survival', {'index': {'type': 'discharge'}, 'event': {'type': 'readmission'}, 'group_by': 'coverage_type'}, cohort=True)
        report = survival.execute(test_survival.Fixture.path, test_survival.Fixture.ids, plan['nodes'][-1]['parameters'])
        a = answer_of(plan, report)
        curves = a['overall']['curves']
        self.assertTrue(curves)
        for points in curves.values():
            self.assertTrue(all({'day', 'survival'} <= set(p) for p in points))
        self.assertTrue({'event_label', 'time_zero', 'groups', 'logrank'} <= set(a['overall']))

    def test_program_effect_exposes_interval_checks_and_warnings(self):
        path, _ = test_program_effect.fixture()
        plan = plan_for('program_effect', dict(test_program_effect.PARAMS))
        report = program_effect.execute(path, None, plan['nodes'][0]['parameters'], AS_OF)
        a = answer_of(plan, report, 'Did care management reduce ED visits?')
        did = a['overall']['did']
        self.assertTrue(isinstance(did['effect'], float) and len(did['ci95']) == 2)
        self.assertLessEqual(did['ci95'][0], did['ci95'][1])
        self.assertIn('inference', a['overall'])
        for k in ('placebo', 'pre_trend', 'synthetic_control', 'sample', 'balance'):
            self.assertIn(k, a['overall'])
        self.assertIsInstance(a['warnings'], list)
        self.assertIsInstance(a['assumptions'], list)

    def test_vbc_exposes_settlement_risk_and_fair_allocation(self):
        test_vbc.VBC.setUpClass()
        case = test_vbc.VBC('test_shared_savings_arithmetic')
        params = {'period': case.period, 'metric': 'shared_savings', 'group_by': 'practice', 'terms': {'benchmark_pmpm': 20, 'sharing_rate': 0.5}}
        plan = plan_for('vbc', params)
        report = vbc.execute(test_vbc.VBC.path, None, plan['nodes'][0]['parameters'], AS_OF)
        a = answer_of(plan, report, 'What would our shared savings be with a benchmark of $20 PMPM and a sharing rate of 50%, by practice?')
        row = a['table'][0]
        risk = row['settlement_risk']
        for k in ('p_earn_savings', 'p_owe_loss', 'p_within_corridor', 'net_payment_p5', 'net_payment_p50', 'net_payment_p95',
                  'expected_net_payment', 'value_at_risk_95'):
            self.assertIn(k, risk)
        self.assertAlmostEqual(risk['p_earn_savings'] + risk['p_owe_loss'] + risk['p_within_corridor'], 1.0, places=2)
        alloc = row['fair_allocation']
        self.assertTrue(alloc['allocation'])
        self.assertTrue(all({'group', 'shapley_share', 'pro_rata_share'} <= set(g) for g in alloc['allocation']))

    def test_trajectory_rows_carry_patient_ids(self):
        test_survival.Fixture.setUpClass()
        plan = plan_for('trajectory', {'concept': 'egfr', 'direction': 'down'}, cohort=True)
        report = trajectory.execute(test_survival.Fixture.path, test_survival.Fixture.ids, plan['nodes'][-1]['parameters'])
        a = answer_of(plan, report, 'Which CKD patients have a declining eGFR?')
        self.assertEqual(a['answer_type'], 'table')
        self.assertEqual(a['patient_ids'], [r['patient_id'] for r in a['table']])


if __name__ == '__main__':
    unittest.main()
