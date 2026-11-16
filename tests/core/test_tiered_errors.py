"""Provider failures by type: an outage in one rung is recorded and the question climbs; a usage limit stops everything;
malformed frontier parameters are refused, not crashed on. No model is called."""
import unittest
from unittest import mock

from clinical_core.agere.tiered import models, router
from tests.core.needs_data import needs_warehouse

Q = 'Which widget colour sold best last spring among left-handed buyers?'   # matches no built-in recipe or saved one


@needs_warehouse
class Errors(unittest.TestCase):
    def test_an_outage_in_a_small_model_rung_climbs_to_the_frontier(self):
        recipe = {'sql': 'SELECT 1 AS n', 'params': {}, 'grain': []}
        with mock.patch.object(models, 's1_classify', side_effect=models.ProviderUnavailable('timeout')), \
             mock.patch.object(models, 'llm_recipe', return_value=(recipe, {'tier': 'llm', 'outcome': 'proposed', 'tokens': 0, 'usd': 0.0})) as llm:
            o = router.answer(Q, tiers=('system1', 'recipes', 'slm', 'llm'))
        self.assertEqual(llm.call_count, 1)
        self.assertIn('error', [s.get('outcome') for s in o['steps'] if s.get('tier') == 'system1'])

    def test_a_usage_limit_stops_everything(self):
        with mock.patch.object(models, 's1_classify', side_effect=models.ProviderLimit('weekly limit')), \
             mock.patch.object(models, 'llm_recipe') as llm:
            with self.assertRaises(models.ProviderLimit):
                router.answer(Q, tiers=('system1', 'recipes', 'slm', 'llm'))
        llm.assert_not_called()

    def test_malformed_frontier_parameters_are_refused(self):
        self.assertIsNone(router._llm_params({'params': {'year': {'type': 'int', 'value': 'twenty'}}}))
        self.assertIsNone(router._llm_params({'params': {'year': {'type': 'year'}}}))
        self.assertEqual(router._llm_params({'params': {'n': {'type': 'int', 'value': '5'}, 'year': {'type': 'year', 'value': 2029}}}), {'n': 5, 'year': '2029'})
        bad = {'sql': 'SELECT :year AS y', 'params': {'year': {'type': 'int', 'value': None}}, 'grain': []}
        with mock.patch.object(models, 'llm_recipe', return_value=(bad, {'tier': 'llm', 'outcome': 'proposed', 'tokens': 0, 'usd': 0.0})):
            o = router.answer(Q, tiers=('llm',))
        self.assertEqual(o['status'], 'partial')
        self.assertIn('not well formed', o.get('missing', ''))

    def test_transport_failures_become_typed(self):
        with mock.patch('clinical_core.agere.subscription_transport.run_cli', side_effect=RuntimeError("You've hit your weekly limit")):
            with self.assertRaises(models.ProviderLimit):
                models._claude('m', 's', 'u')
        with mock.patch('clinical_core.agere.subscription_transport.run_cli', side_effect=RuntimeError('connection reset')):
            with self.assertRaises(models.ProviderUnavailable):
                models._claude('m', 's', 'u')


if __name__ == '__main__':
    unittest.main()
