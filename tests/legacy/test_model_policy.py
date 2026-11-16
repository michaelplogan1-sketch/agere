"""Authorized Sonnet/Opus requests use the role ladder; legacy Haiku names resolve to Sonnet."""
import os
import unittest
from unittest import mock

from clinical_core.agere import model_policy as M


class Policy(unittest.TestCase):
    def test_nested_roles_reset_without_losing_outer_escalation(self):
        with M.question_path(), M.escalation():
            self.assertEqual(M.claude_model(M.HAIKU), M.OPUS)
            with M.first_rung():
                self.assertEqual(M.claude_model(M.OPUS), M.SONNET)
            self.assertEqual(M.claude_model(M.SONNET), M.OPUS)
        self.assertEqual(M.claude_model(M.HAIKU), M.SONNET)

    def test_question_path_opus_becomes_sonnet(self):
        with mock.patch.dict(os.environ, {'AGERE_QUESTION_OPUS': 'off'}), M.question_path():
            self.assertEqual(M.claude_model('claude-opus-5-5'), 'claude-sonnet-5-5')
            self.assertEqual(M.claude_model('claude-haiku-5-5'), 'claude-sonnet-5-5')
            self.assertEqual(M.claude_model('claude-sonnet-5-5'), 'claude-sonnet-5-5')

    def test_deliverables_keep_opus(self):
        with M.deliverable():
            self.assertEqual(M.claude_model('claude-opus-5-5'), 'claude-opus-5-5')
        self.assertEqual(M.claude_model('claude-opus-5-5'), 'claude-opus-5-5')

    def test_override(self):
        with mock.patch.dict(os.environ, {'AGERE_QUESTION_OPUS': 'on'}):
            self.assertEqual(M.claude_model('claude-opus-5-5'), 'claude-opus-5-5')

    def test_shared_sender_sends_sonnet(self):
        from clinical_core.agere.clinical import context_student as C
        sent = {}

        class Stop(Exception): pass

        def capture(body, *a, **k):
            sent.update(body); raise Stop
        with M.question_path(), mock.patch.object(C, 'digest', side_effect=capture):
            with self.assertRaises(Stop):
                C.send({'model': 'claude-opus-5-5', 'max_tokens': 10, 'messages': []}, 'claude', None, None, parse=lambda r: r)
        self.assertEqual(sent['body']['model'], 'claude-sonnet-5-5')


if __name__ == '__main__':
    unittest.main()
