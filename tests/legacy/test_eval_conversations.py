"""Evaluation mode keeps conversation turns and saved cohorts in a scratch directory outside the stack (2026-10-24): a benchmark follow-up sees
its parent, and the stack's own stores are never written."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clinical_core.agere.clinical import conversation, evaluation
from clinical_core.agere.deliverables import answer_context


class EvaluationConversations(unittest.TestCase):
    def test_turns_go_to_the_scratch_directory_and_are_read_back(self):
        with tempfile.TemporaryDirectory() as stack, tempfile.TemporaryDirectory() as scratch, \
                patch.dict(os.environ, {'AGERE_EVALUATION_SCRATCH': scratch}):
            resolution = {'standalone': 'How many patients have diabetes?', 'method': 'standalone', 'frame': None}
            answer = {'status': 'answered', 'headline': '990 patients'}
            with evaluation.scope(stack_path=stack):
                self.assertGreater(conversation.record(stack, 'run-1-b3-01', 'How many patients have diabetes?', resolution, {}, answer), 0)
                prior = conversation.prior_context(stack, 'run-1-b3-01')
                self.assertEqual(prior['resolved_question'], 'How many patients have diabetes?')
                self.assertTrue(str(answer_context.root(stack)).startswith(scratch))
            self.assertFalse((Path(stack) / 'conversations.sqlite3').exists())  # the stack's own store is untouched
            self.assertEqual(conversation.turns(stack, 'run-1-b3-01'), [])  # outside evaluation, the stack's store is read
            self.assertEqual(answer_context.root(stack), Path(stack) / 'answer-workspaces')


if __name__ == '__main__':
    unittest.main()
