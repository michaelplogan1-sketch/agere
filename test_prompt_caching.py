"""Prompt-cache layout: the static catalog prefix is identical across questions; the remainder carries the question."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_shared_planner as fixtures
from clinical_core.agere.clinical import nl2sql, shared_planner as S


class PromptCaching(unittest.TestCase):
    def setUp(self):
        fixtures.SharedPlanner.setUp(self)

    def payload(self, question):
        return S.planner_user(S.schema_slice(self.catalog, question), question=question, validator_feedback='')

    def test_static_prefix_is_identical_and_payload_stays_valid_json(self):
        a, b = self.payload('How many patients have diabetes?'), self.payload('Average length of stay by payer in 2029')
        cut = lambda u: u[:u.index(S.CACHE_BOUNDARY) + len(S.CACHE_BOUNDARY)]
        self.assertEqual(cut(a), cut(b))
        for u, q in ((a, 'How many patients have diabetes?'), (b, 'Average length of stay by payer in 2029')):
            data = json.loads(u)
            self.assertEqual(data['question'], q)
            self.assertNotIn(q, cut(u))
            self.assertIn('governed_measures', data['catalog_static'])
            self.assertIn('selected_schemas', data['catalog'])
            self.assertNotIn('governed_measures', data['catalog'])

    def test_hosted_request_caches_only_the_static_prefix(self):
        sent = {}
        def fake_send(body, provider, ledger, out, **kw):
            sent.update(body)
            return '{}', {'cache_hit': False, 'cost_estimate': {'estimated_usd': 0.0}}
        user = self.payload('How many patients have diabetes?')
        with tempfile.TemporaryDirectory() as d, patch('clinical_core.agere.clinical.context_student.send', fake_send):
            nl2sql.HostedModel(None, Path(d), model='claude-opus-5-5')('system', user, max_tokens=10)
        blocks = sent['messages'][0]['content']
        self.assertEqual(blocks[0]['cache_control'], {'type': 'ephemeral', 'ttl': '1h'})
        self.assertNotIn('cache_control', blocks[1])
        self.assertEqual(blocks[0]['text'] + blocks[1]['text'], user)
        self.assertIn('How many patients have diabetes?', blocks[1]['text'])


if __name__ == '__main__':
    unittest.main()
