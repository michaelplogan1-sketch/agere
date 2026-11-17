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


@needs_warehouse
class ModelReadings(unittest.TestCase):
    """The frontier model returns readings for an unclear question; each is answered; the person is asked only if they differ."""
    def run_with(self, a_sql, b_sql):
        two = {'tier': 'llm', 'outcome': 'readings', 'tokens': 0, 'usd': 0.0,
               'readings': [{'label': 'Our four hospitals', 'question': 'Q at our own hospitals'}, {'label': 'Every hospital', 'question': 'Q at every hospital'}]}
        def llm(question, **kw):
            if models.NO_READINGS.get():
                sql = a_sql if 'own' in question else b_sql
                return {'sql': sql, 'params': {}, 'grain': []}, {'tier': 'llm', 'outcome': 'proposed', 'tokens': 0, 'usd': 0.0}
            return None, two
        with mock.patch.object(models, 'llm_recipe', side_effect=llm):
            return router.answer(Q, tiers=('llm',))

    def test_different_answers_ask(self):
        o = self.run_with('SELECT 116 AS n', 'SELECT 459 AS n')
        self.assertEqual(o['status'], 'needs_choice')
        self.assertEqual([r['label'] for r in o['readings']], ['Our four hospitals', 'Every hospital'])

    def test_same_answer_is_given(self):
        o = self.run_with('SELECT 7 AS n', 'SELECT 7 AS n')
        self.assertEqual((o['status'], o['rows']), ('answered', [[7]]))
        self.assertTrue(o['readings_checked']['agree'])


@needs_warehouse
class AskFirst(unittest.TestCase):
    """With a person in the conversation, an unclear question goes back to them before anything is computed."""
    def test_rule_readings_are_asked_before_computing(self):
        with mock.patch.object(router, '_ladder') as ladder:
            o = router.answer('How many patients were admitted from the ED in 2029?', ask_first=True)
        ladder.assert_not_called()
        self.assertEqual(o['status'], 'needs_clarification')
        self.assertEqual([r['id'] for r in o['readings']], ['patients', 'events'])

    def test_model_readings_are_asked_before_computing_any(self):
        two = {'tier': 'llm', 'outcome': 'readings', 'tokens': 0, 'usd': 0.0,
               'readings': [{'label': 'Our four hospitals', 'question': 'Q at our own hospitals'}, {'label': 'Every hospital', 'question': 'Q at every hospital'}]}
        with mock.patch.object(models, 'llm_recipe', return_value=(None, two)) as llm:
            o = router.answer(Q, tiers=('llm',), ask_first=True)
        self.assertEqual(llm.call_count, 1)   # no reading was computed
        self.assertEqual((o['status'], [r['label'] for r in o['readings']]), ('needs_clarification', ['Our four hospitals', 'Every hospital']))

    def test_a_chosen_reading_is_not_asked_again(self):
        token = models.NO_READINGS.set(True)
        try:
            with mock.patch.object(router, '_ladder', return_value={'status': 'answered', 'rows': [[175]], 'steps': [], 'escalations': [], 'seconds': 0.0}) as ladder:
                o = router.answer('How many patients were admitted from the ED in 2029? Count each patient once.', ask_first=True)
        finally:
            models.NO_READINGS.reset(token)
        self.assertEqual((o['status'], ladder.call_count), ('answered', 1))
