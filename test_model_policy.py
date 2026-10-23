"""Claude model policy: Opus requests on the question path become Sonnet; the deliverable composer keeps Opus."""
import os
import unittest
from unittest import mock

from clinical_core.agere import model_policy as M


class Policy(unittest.TestCase):
    def test_question_path_opus_becomes_sonnet(self):
        with mock.patch.dict(os.environ, {'AGERE_QUESTION_OPUS': 'off'}):
            self.assertEqual(M.claude_model('claude-opus-5-5'), 'claude-sonnet-5-5')
            self.assertEqual(M.claude_model('claude-haiku-5-5'), 'claude-haiku-5-5')
            self.assertEqual(M.claude_model('claude-sonnet-5-5'), 'claude-sonnet-5-5')

    def test_deliverables_keep_opus(self):
        with M.deliverable():
            self.assertEqual(M.claude_model('claude-opus-5-5'), 'claude-opus-5-5')
        self.assertEqual(M.claude_model('claude-opus-5-5'), 'claude-sonnet-5-5')

    def test_override(self):
        with mock.patch.dict(os.environ, {'AGERE_QUESTION_OPUS': 'on'}):
            self.assertEqual(M.claude_model('claude-opus-5-5'), 'claude-opus-5-5')

    def test_shared_sender_sends_sonnet(self):
        from clinical_core.agere.clinical import context_student as C
        sent = {}

        class Stop(Exception): pass

        def capture(body, *a, **k):
            sent.update(body); raise Stop
        with mock.patch.object(C, 'digest', side_effect=capture):
            with self.assertRaises(Stop):
                C.send({'model': 'claude-opus-5-5', 'max_tokens': 10, 'messages': []}, 'claude', None, None, parse=lambda r: r)
        self.assertEqual(sent['body']['model'], 'claude-sonnet-5-5')


if __name__ == '__main__':
    unittest.main()
