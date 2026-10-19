"""General routing regressions on independently known tiny fixtures; no model calls or benchmark keys."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from clinical_core.agere.hybrid import live
from clinical_core.agere.clinical import conversation, economical_planner
from clinical_core.agere.clinical.question_input import MAX_QUESTION_CHARS, validate_question
from test_hybrid import contract


class FrontierReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'semantic.sqlite3'
        with sqlite3.connect(self.db) as con:
            con.executescript("CREATE TABLE patient(patient_id TEXT); INSERT INTO patient VALUES ('a'),('b'),('c');")
        self.wh = SimpleNamespace(path=self.db)

    def run_case(self, original, review, question='Count every delivered patient. Definition: Include every delivered patient exactly once.', enabled=True):
        calls = []

        @contextmanager
        def sender():
            yield None

        def analyst(q, semantic, send, model, **kwargs):
            calls.append((model, kwargs))
            item = original if kwargs.get('run') else review
            return item, {'estimated_cost_usd': .01, 'new_paid_api_calls': 1}

        with patch.dict(os.environ, {'AGERE_HYBRID_REVIEW': 'on' if enabled else 'off'}), \
                patch.object(live, 'analyst_run', side_effect=analyst):
            out = live.answer(question, '2030-05-27', self.wh, sender, answer_type='count', docs_dir=live.DOCS)
        return out, calls

    def test_defined_choice_gets_one_executed_frontier_review(self):
        original = {'contract': contract(readings=['only current records', 'every delivered record']), 'sql': None}
        review = {'contract': contract(), 'sql': 'SELECT count(*) FROM patient',
                  'definition_resolution': {'quote': 'Include every delivered patient exactly once.',
                                            'explanation': 'The explicit definition settles the inclusion choice.'}}
        out, calls = self.run_case(original, review)
        self.assertEqual(out['answer'], {'count': 3})
        self.assertEqual(out['decision'], 'frontier_reviewed')
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[-1][0], 'claude-opus-5-5')
        self.assertEqual(out['new_paid_api_calls'], 4)
        self.assertAlmostEqual(out['cost_usd'], .04)

    def test_uncited_or_invented_resolution_cannot_override_clarification(self):
        original = {'contract': contract(readings=['by claim', 'by person']), 'sql': None}
        for resolution in ({}, {'quote': 'This quote does not appear in the user request.', 'explanation': 'assumed'}):
            out, calls = self.run_case(original, {'contract': contract(), 'sql': 'SELECT count(*) FROM patient',
                                                  'definition_resolution': resolution})
            self.assertIsNone(out['answer'])
            self.assertEqual(out['decline']['kind'], 'clarify')
            self.assertEqual(len(calls), 4)

    def test_sql_failure_can_be_repaired_once_but_not_fabricated(self):
        original = {'contract': contract(), 'sql': 'SELECT missing_column FROM patient'}
        out, calls = self.run_case(original, {'contract': contract(), 'sql': 'SELECT count(*) FROM patient'})
        self.assertEqual(out['answer'], {'count': 3})
        self.assertEqual(len(calls), 4)
        out, calls = self.run_case(original, original)
        self.assertIsNone(out['answer'])
        self.assertEqual(len(calls), 4)

    def test_missing_inputs_and_disabled_candidate_do_not_add_calls(self):
        missing = {'contract': contract(missing=['No delivered admission timestamp']), 'sql': None}
        out, calls = self.run_case(missing, None)
        self.assertEqual(out['decline']['kind'], 'unsupported')
        self.assertEqual(len(calls), 3)
        out, calls = self.run_case(None, None, enabled=False)
        self.assertIsNone(out['answer'])
        self.assertEqual(len(calls), 3)

    def test_default_policy_repairs_contract_failure_once(self):
        original = {'contract': contract(unit='ratio', denominator=None), 'sql': 'SELECT count(*) FROM patient'}
        repair = {'contract': contract(), 'sql': 'SELECT count(*) FROM patient'}
        out, calls = self.run_case(original, repair, enabled=False)
        self.assertEqual(out['answer'], {'count': 3})
        self.assertEqual(out['decision'], 'escalated')
        self.assertEqual(len(calls), 4)
        self.assertEqual({model for model, _ in calls}, {'claude-opus-5-5'})


class DefinitionLengthTests(unittest.TestCase):
    def test_definition_survives_planning_conversation_and_storage(self):
        question = 'Count patients. Definition: ' + ('Use the supplied population and dates. ' * 65) + 'Exclude cancelled records.'
        self.assertGreater(len(question), 1500)
        self.assertTrue(economical_planner.normalize(question).endswith('exclude cancelled records'))
        resolved = conversation.resolve(question, None)
        self.assertEqual(resolved['standalone'], question)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            conversation.record(path, 'c', question, resolved)
            with sqlite3.connect(path / 'conversations.sqlite3') as con:
                row = con.execute('SELECT question, resolved_question FROM turns').fetchone()
            self.assertEqual(row, (question, question))

    def test_invalid_input_rejected_before_dispatch(self):
        for value in ('', '   ', None, 'x' * (MAX_QUESTION_CHARS + 1)):
            with self.assertRaises(ValueError):
                validate_question(value)

    def test_full_local_pipeline_preserves_long_original_question(self):
        from test_workspace_stack import native_workspace
        from clinical_core.agere.onboarding import workspace_stack
        question = 'Count all patients. Definition: ' + ('Each person is counted once. ' * 65)
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {
                'AGERE_LOCAL_MODEL': 'off', 'AGERE_CASCADE': 'off', 'AGERE_BOTTLED': 'off',
                'AGERE_SEMANTIC_CACHE': 'off', 'AGERE_LEDGER_PATH': str(Path(d) / 'ledger.sqlite3')}):
            ws = native_workspace(Path(d) / 'workspaces', n=6)
            stack = workspace_stack.stack_for(ws, encoder=False)
            out = stack.compose(question)
            self.assertEqual(out['question'], question)
            self.assertEqual(out['new_paid_api_calls'], 0)
            self.assertEqual(out['answer']['count'], 6)
