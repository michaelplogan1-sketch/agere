"""Provider-unavailable classification, one retry with backoff, then the fallback model, then a typed error."""
import io
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from clinical_core.agere import gateway as G
from clinical_core.agere.clinical import nl2sql


def http(code, message=''):
    return urllib.error.HTTPError('https://api.anthropic.com/v1/messages', code, message, {}, io.BytesIO(b'{}'))


class Classification(unittest.TestCase):
    def test_provider_errors_are_classified(self):
        cases = {'billing': [http(402), RuntimeError('HTTP 400: Your credit balance is too low to access the Anthropic API.')],
                 'rate_limit': [http(429), RuntimeError('HTTP 429: rate_limit_error')],
                 'overloaded': [http(529), RuntimeError('HTTP 529: Overloaded')],
                 'server': [http(500), http(503), RuntimeError('HTTP 502: bad gateway')],
                 'timeout': [TimeoutError('read timed out'), urllib.error.URLError('down'), RuntimeError('TimeoutError: timed out')]}
        for kind, errors in cases.items():
            for exc in errors:
                self.assertEqual(G.classify_provider_error(exc), kind, repr(exc))

    def test_request_errors_are_not_provider_errors(self):
        for exc in (http(400, 'bad'), ValueError('Invalid wire graph node'), RuntimeError('HTTP 400: messages: field required'),
                    KeyError('nodes')):
            self.assertIsNone(G.classify_provider_error(exc), repr(exc))

    def test_fallback_model_defaults_to_next_family_model(self):
        with patch.dict(os.environ, {'AGERE_FALLBACK_MODEL': '', 'AGERE_INTERPRETER_FALLBACK': ''}):
            self.assertEqual(G.fallback_model('claude-opus-5-5'), 'claude-sonnet-5-5')
            self.assertIsNone(G.fallback_model('claude-sonnet-5-5'))
            self.assertIsNone(G.fallback_model('claude-haiku-5-5'))  # never falls back to a more expensive model
            self.assertIsNone(G.fallback_model('jev-1.13.0'))
        with patch.dict(os.environ, {'AGERE_FALLBACK_MODEL': 'claude-opus-5'}):
            self.assertEqual(G.fallback_model('claude-opus-5-5'), 'claude-opus-5')
            self.assertIsNone(G.fallback_model('claude-opus-5'))
        with patch.dict(os.environ, {'AGERE_FALLBACK_MODEL': '', 'AGERE_INTERPRETER_FALLBACK': 'claude-haiku-5-5'}):
            self.assertEqual(G.fallback_model('claude-opus-5-5'), 'claude-sonnet-5-5')
        self.assertTrue(issubclass(G.ProviderUnavailable, RuntimeError))


class Fallback(unittest.TestCase):
    def setUp(self):
        e = patch.dict(os.environ, {'AGERE_FALLBACK_MODEL': '', 'AGERE_INTERPRETER_FALLBACK': ''}); e.start(); self.addCleanup(e.stop)

    def test_cancelled_request_never_retries_or_calls_a_fallback_model(self):
        error=G.ProviderUnavailable('request_cancelled: retained prefix',kind='cancelled')
        with self.assertRaises(G.ProviderUnavailable) as raised:self.run_calls([error])
        self.assertIs(raised.exception,error)
        self.assertEqual(error.calls,['claude-opus-5-5']);self.assertEqual(error.sleeps,[])

    def run_calls(self, outcomes, model='claude-opus-5-5'):
        calls, sleeps = [], []
        def call(m):
            calls.append(m)
            out = outcomes.pop(0)
            if isinstance(out, Exception): raise out
            return out
        try:
            return G.call_with_fallback(call, model, sleep=sleeps.append, backoff=1.5), calls, sleeps
        except Exception as exc:
            exc.calls, exc.sleeps = calls, sleeps
            raise

    def test_transient_error_retries_once_with_backoff(self):
        result, calls, sleeps = self.run_calls([http(529), 'ok'])
        self.assertEqual((result, calls, sleeps), ('ok', ['claude-opus-5-5'] * 2, [1.5]))

    def test_second_failure_falls_back_to_the_alternate_model(self):
        result, calls, sleeps = self.run_calls([http(429), http(429), 'ok'])
        self.assertEqual(result, 'ok'); self.assertEqual(calls, ['claude-opus-5-5', 'claude-opus-5-5', 'claude-sonnet-5-5'])

    def test_billing_skips_the_pointless_retry(self):
        result, calls, sleeps = self.run_calls([RuntimeError('HTTP 400: credit balance is too low'), 'ok'])
        self.assertEqual((calls, sleeps), (['claude-opus-5-5', 'claude-sonnet-5-5'], []))

    def test_exhausted_raises_typed_provider_unavailable(self):
        with self.assertRaises(G.ProviderUnavailable) as ctx:
            self.run_calls([http(503), http(503), http(529)])
        self.assertEqual(ctx.exception.kind, 'overloaded')
        self.assertEqual([a['model'] for a in ctx.exception.attempts], ['claude-opus-5-5', 'claude-opus-5-5', 'claude-sonnet-5-5'])
        self.assertIn('provider_unavailable', str(ctx.exception))

    def test_request_errors_propagate_without_retry(self):
        with self.assertRaises(ValueError) as ctx:
            self.run_calls([ValueError('Invalid wire graph node'), 'never'])
        self.assertEqual(ctx.exception.calls, ['claude-opus-5-5'])

    def test_model_without_alternate_raises_after_retry(self):
        with self.assertRaises(G.ProviderUnavailable) as ctx:
            self.run_calls([http(500), http(500)], model='custom-model')
        self.assertEqual(len(ctx.exception.attempts), 2)


class HostedModelFallback(unittest.TestCase):
    def test_hosted_model_reports_the_model_that_served(self):
        seen = []
        def fake_send(body, provider, ledger, out, **kw):
            seen.append(body['model'])
            if body['model'] == 'claude-opus-5-5': raise RuntimeError('HTTP 529: Overloaded')
            return 'text', {'cost_estimate': {'estimated_usd': 0.001}}
        with tempfile.TemporaryDirectory() as d, patch('clinical_core.agere.clinical.context_student.send', fake_send), \
                patch.dict(os.environ, {'AGERE_PROVIDER_BACKOFF_SECONDS': '0', 'AGERE_FALLBACK_MODEL': ''}):
            m = nl2sql.HostedModel(None, Path(d), model='claude-opus-5-5')
            text, stats = m('sys', 'user')
        self.assertEqual(seen, ['claude-opus-5-5', 'claude-opus-5-5', 'claude-sonnet-5-5'])
        self.assertEqual((text, stats['model'], stats['fallback_from'], m.identity), ('text', 'claude-sonnet-5-5', 'claude-opus-5-5', 'claude-sonnet-5-5'))

    def test_graph_planner_call_uses_fallback(self):
        src = (Path(__file__).resolve().parents[2] / 'clinical_core/agere/clinical/graph_planner.py').read_text()
        self.assertIn('call_with_fallback(lambda m: send({**b, \'model\': m}', src)


if __name__ == '__main__':
    unittest.main()
