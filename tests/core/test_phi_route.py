"""PHI route guard: each route not listed as covered is refused when PHI is flagged; nothing is refused otherwise."""
import os
import unittest
from unittest import mock

from clinical_core.agere import phi_route
from clinical_core.agere.tiered import models


class Guard(unittest.TestCase):
    def env(self, **kw):
        return mock.patch.dict(os.environ, {'AGERE_PHI_GUARD': 'on', 'AGERE_PHI_DATA': 'on', **kw})

    def test_every_excluded_route_is_refused_with_phi(self):
        excluded = [r for r, (ok, _) in phi_route.ROUTES.items() if not ok]
        self.assertEqual(len(excluded), 8)
        with self.env():
            for r in excluded + ['some_new_route']:
                with self.assertRaises(phi_route.PHIRouteRefused, msg=r): phi_route.check(r)
            self.assertEqual(phi_route.check('messages_api'), 'messages_api')

    def test_nothing_refused_without_phi_or_when_off(self):
        with self.env(AGERE_PHI_DATA='off'):
            self.assertEqual(phi_route.check('batch_api'), 'batch_api')
        with self.env(AGERE_PHI_GUARD='off'):
            self.assertEqual(phi_route.check('subscription_cli'), 'subscription_cli')

    def test_model_call_on_the_subscription_transport_is_refused_with_phi(self):
        with self.env(AGERE_CLAUDE_TRANSPORT='subscription'), mock.patch('clinical_core.agere.subscription_transport.run_cli') as cli:
            with self.assertRaises(phi_route.PHIRouteRefused): models._claude(models.LLM, 'system', 'user')
            cli.assert_not_called()

    def test_model_call_records_its_route(self):
        with mock.patch.dict(os.environ, {'AGERE_PHI_GUARD': 'off', 'AGERE_CLAUDE_TRANSPORT': 'subscription'}), \
             mock.patch('clinical_core.agere.subscription_transport.run_cli', return_value={'result': '{}', 'usage': {}}):
            _, step = models._claude(models.LLM, 'system', 'user')
        self.assertEqual(step['route'], 'subscription_cli')


if __name__ == '__main__':
    unittest.main()
