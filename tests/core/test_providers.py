"""The one key loader: the paid Anthropic key is kept only on the hosted server with the API transport. Uses throwaway files
with fake values; no real key is read."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from clinical_core.agere import providers


class Keys(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.keys = Path(self.tmp.name) / 'keys.txt'
        self.keys.write_text('fake-typesafe\nAnthropic: fake-anthropic\nPerplexity: fake-perplexity\n')
        self.env = Path(self.tmp.name) / 'more.env'
        self.env.write_text('export OPENAI_API_KEY="fake-openai"\nPERPLEXITY_API_KEY=not-this-one\n')

    def tearDown(self):
        self.tmp.cleanup()

    def load(self, **extra):
        env = {'AGERE_KEYS_FILE': str(self.keys), 'AGERE_ENV_FILE': str(self.env), **extra}
        with mock.patch.dict(os.environ, env, clear=True):
            providers.load_keys()
            return dict(os.environ)

    def test_a_workstation_never_holds_the_anthropic_key(self):
        got = self.load(AGERE_CLAUDE_TRANSPORT='')
        self.assertNotIn('ANTHROPIC_API_KEY', got)
        self.assertEqual((got['TYPESAFE_API_KEY'], got['PERPLEXITY_API_KEY'], got['OPENAI_API_KEY']), ('fake-typesafe', 'fake-perplexity', 'fake-openai'))

    def test_the_subscription_transport_gets_a_marker_not_a_key(self):
        self.assertEqual(self.load(AGERE_CLAUDE_TRANSPORT='subscription')['ANTHROPIC_API_KEY'], 'subscription-transport')

    def test_api_transport_off_the_hosted_server_holds_no_key(self):
        self.assertNotIn('ANTHROPIC_API_KEY', self.load(AGERE_CLAUDE_TRANSPORT='api'))

    def test_the_hosted_server_keeps_its_own_key(self):
        got = self.load(AGERE_HOSTED='1', AGERE_CLAUDE_TRANSPORT='api', ANTHROPIC_API_KEY='operator-placed')
        self.assertEqual(got['ANTHROPIC_API_KEY'], 'operator-placed')

    def test_claude_off_removes_it_everywhere(self):
        self.assertNotIn('ANTHROPIC_API_KEY', self.load(AGERE_HOSTED='1', AGERE_CLAUDE_TRANSPORT='api', AGERE_CLAUDE='off', ANTHROPIC_API_KEY='x'))


if __name__ == '__main__':
    unittest.main()
