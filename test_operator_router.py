"""Operator router: any phrasing reaches a registered engine; the runtime validators stay authoritative."""
import json, unittest
from unittest import mock

from clinical_core.agere.clinical import operator_router as router
from clinical_core.agere.clinical.analysis_graph import validate, OPERATIONS
from clinical_core.agere.clinical.capability_registry import PRODUCT
from clinical_core.agere.clinical.graph_planner import CONTRACTS
from clinical_core.agere.clinical import graph_contracts

AS_OF = '2029-08-15'


class Registry(unittest.TestCase):
    def test_every_product_capability_is_fully_integrated(self):
        for op in PRODUCT:
            self.assertIn(op, OPERATIONS, op)
            self.assertIn(op, CONTRACTS, f'{op} missing from frontier planner contracts')
            self.assertIn(op, graph_contracts.TYPED, op)

    def test_each_request_fits_the_request_bound(self):
        self.assertLess(len(json.dumps(router.menu())), 12000)
        for op in PRODUCT: self.assertLess(len(json.dumps(router.tool(op))), 20000, op)
        self.assertEqual({t['name'] for t in router.menu()}, {*PRODUCT, 'literal_lookup', 'not_answerable'})


class Graphs(unittest.TestCase):
    def ok(self, name, args):
        return validate(router.to_graph('q', {'name': name, 'input': args}), AS_OF)

    def test_population_atoms_with_roles(self):
        g = self.ok('survival', {'population': [{'kind': 'concept', 'concept': 'heart_failure'},
                                                {'kind': 'patient', 'field': 'age', 'op': 'ge', 'value': 65},
                                                {'kind': 'concept', 'concept': 'copd', 'role': 'exclude'}],
                                 'parameters': {'index': {'type': 'discharge'}, 'event': {'type': 'readmission'}}})
        spec = g['nodes'][0]['parameters']['specification']
        self.assertEqual((len(spec['all']), len(spec['exclude'])), (2, 1))

    def test_required_population_defaults_to_everyone(self):
        g = self.ok('trajectory', {'parameters': {'concept': 'egfr', 'direction': 'worse'}})
        self.assertEqual(g['nodes'][0]['operation'], 'cohort')

    def test_nested_outcome_specification(self):
        self.ok('discover', {'parameters': {'outcome': [{'kind': 'measurement', 'concept': 'hba1c', 'op': 'gt', 'value': 9}]}})

    def test_no_population_operator(self):
        g = self.ok('monitor', {'parameters': {'check': 'control_chart', 'series': ['ed_rate']}})
        self.assertEqual([n['operation'] for n in g['nodes']], ['monitor'])

    def test_heals_string_parameters_and_nested_population(self):
        g = self.ok('explain', {'parameters': json.dumps({'outcome': {'type': 'readmission'},
                                                          'population': [{'kind': 'concept', 'concept': 'heart_failure'}]})})
        self.assertEqual(g['nodes'][0]['parameters']['specification']['all'][0]['concept'], 'heart_failure')

    def test_heals_malformed_parameter_strings(self):
        appended = '{"index": {"type": "discharge"}, "event": {"type": "readmission"}}, "population": [{"kind": "concept", "concept": "heart_failure"}]}'
        g = self.ok('survival', {'parameters': appended})
        self.assertEqual(g['nodes'][0]['parameters']['specification']['all'][0]['concept'], 'heart_failure')
        self.ok('survival', {'parameters': '{"index": {"type": "discharge"}, "event": {"type": "readmission"}}}'})

    def test_social_need_outcome(self):
        g = self.ok('discover', {'parameters': {'outcome': [{'kind': 'social_need', 'need': 'food_difficulty'}], 'direction': 'high'}})
        outcome = g['nodes'][-1]['parameters']['outcome']
        self.assertEqual(outcome['all'][0]['conditions'][0]['field'], 'food_difficulty_reported')
        g = self.ok('survival', {'population': [{'kind': 'social_need', 'need': 'transport_difficulty'}],
                                 'parameters': {'index': {'type': 'discharge'}, 'event': {'type': 'readmission'}}})
        self.assertEqual(len(g['nodes'][0]['parameters']['specification']['any']), 2)  # both transport instruments

    def test_unregistered_operator_refused(self):
        with self.assertRaises(ValueError):
            router.to_graph('q', {'name': 'sql', 'input': {'parameters': {}}})


class Route(unittest.TestCase):
    def test_literal_questions_pass_through(self):
        with mock.patch.object(router, '_ask', return_value=({'name': 'literal_lookup', 'input': {}}, {'api_usd': 0.001})):
            graph, info = router.route('How many ED visits in 2029?', AS_OF, None, None, validate)
        self.assertIsNone(graph); self.assertEqual(info['decision'], 'literal_lookup')

    def test_invalid_plan_is_repaired_once_with_the_validator_error(self):
        bad = {'name': 'trajectory', 'input': {'parameters': {'concept': 'not_a_lab'}}}
        good = {'name': 'trajectory', 'input': {'parameters': {'concept': 'egfr', 'direction': 'worse'}}}
        calls = []
        def fake(question, as_of, ledger, out, op=None, feedback=None, model=None):
            calls.append((op, feedback))
            if op is None: return {'name': 'trajectory', 'input': {}}, {'api_usd': 0.001}
            return (good if feedback else bad), {'api_usd': 0.002}
        with mock.patch.object(router, '_ask', side_effect=fake):
            graph, info = router.route('whose kidney function is sliding', AS_OF, None, None, validate)
        self.assertIsNotNone(graph); self.assertEqual(len(calls), 3); self.assertTrue(calls[2][1])
        self.assertAlmostEqual(router.cost(info), 0.005)

    def test_missing_user_inputs_are_named_not_guessed(self):
        terms = {'name': 'vbc', 'input': {'parameters': {'metric': 'shared_savings', 'group_by': 'practice'}}}
        with mock.patch.object(router, '_ask', side_effect=lambda *a, op=None, **k: (({'name': 'vbc', 'input': {}} if op is None else terms), {'api_usd': 0.0})):
            graph, info = router.route('Split the savings fairly across practices', AS_OF, None, None, validate)
        self.assertIsNone(graph); self.assertIn('contract terms', info['requires_input'])

    def test_two_invalid_plans_fall_through(self):
        bad = {'name': 'trajectory', 'input': {'parameters': {'concept': 'not_a_lab'}}}
        with mock.patch.object(router, '_ask', side_effect=lambda *a, op=None, **k: (({'name': 'trajectory', 'input': {}} if op is None else bad), {'api_usd': 0.0})):
            graph, info = router.route('q', AS_OF, None, None, validate)
        self.assertIsNone(graph); self.assertEqual(len(info['rejected']), 2)


if __name__ == '__main__':
    unittest.main()
