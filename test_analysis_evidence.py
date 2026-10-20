"""Independent development cases for completeness, not clinical qualification."""
import unittest

import test_shared_planner as fixtures
from test_general_analysis import Q, spec
from clinical_core.agere.clinical import general_analysis as G, request_plan as R


class AnalysisEvidence(unittest.TestCase):
    def setUp(self):
        fixtures.SharedPlanner.setUp(self)

    def test_required_but_unused_step_does_not_satisfy_question(self):
        s = spec()
        s['steps'][1]['sql'] = 'SELECT COUNT(*) AS patients FROM patient'
        with self.assertRaisesRegex(R.PlanError, 'ED visits.*not used'):
            G.prepare(self.catalog, Q, s)
        c = G.prepare(self.catalog, Q, s, relaxed=True)
        self.assertEqual(c['evidence_completeness']['status'], 'incomplete')
        self.assertEqual(c['evidence_completeness']['unused_steps'], ['ed_counts'])
        self.assertTrue(c['analysis']['unresolved'])

    def test_nested_dependencies_and_count_star_are_recorded(self):
        c = G.prepare(self.catalog, Q, spec())
        self.assertEqual(c['evidence_completeness']['active_steps'], ['answer', 'ed_counts'])
        self.assertEqual(c['evidence_completeness']['status'], 'compiled')
        s = {'steps': [{'name': 'total', 'sql': 'SELECT COUNT(*) AS n FROM claim'}],
             'returns': 'count', 'requirements': [{'quote': 'Count claims', 'applied_in': ['total']}], 'unresolved': []}
        c = G.prepare(self.catalog, 'Count claims', s)
        self.assertIn('claim', c['sources'])

    def test_text_mentions_do_not_fake_step_participation(self):
        s = spec()
        s['steps'][1]['sql'] = "SELECT COUNT(*) AS patients FROM patient WHERE 'ed_counts' <> '' /* ed_counts */"
        with self.assertRaisesRegex(R.PlanError, 'not used'):
            G.prepare(self.catalog, Q, s)

    def test_unused_optional_step_and_supported_empty_result(self):
        s = spec('2027')
        s['steps'].append({'name': 'optional', 'sql': 'SELECT * FROM claim'})
        c = G.prepare(self.catalog, Q.replace('2029', '2027'), s)
        self.assertEqual(c['evidence_completeness']['unused_steps'], ['optional'])
        self.assertEqual(c['evidence_completeness']['status'], 'compiled')
        report, _ = G.preview(self.catalog, c)
        self.assertEqual(report['value'], 0)


if __name__ == '__main__':
    unittest.main()
