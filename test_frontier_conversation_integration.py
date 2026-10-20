"""Owner integration checks across execution, artifacts and request accounting."""
import copy
import importlib
import re
import sqlite3
import unittest
from unittest.mock import Mock, patch

import test_shared_planner as fixtures
from clinical_core.agere.clinical import conversation, request_language, request_plan, shared_planner
from clinical_core.agere.deliverables import answer_context

composer = importlib.import_module('clinical_core.agere.deliverables.compose')


class FrontierConversationIntegration(unittest.TestCase):
    setUp = fixtures.SharedPlanner.setUp

    def prepare(self, *, known=True, brief=None):
        base = 'List patients with diabetes and age > 65'
        brief = brief or 'create a polished executive dashboard for our leadership team with a dark theme'
        self.question = base + '; ' + brief
        plan = request_language.parse(self.catalog, base)
        plan['workflow'] = [{'kind': 'deliverable', 'title': 'Executive review',
                             'quote': brief, 'template': 'command', 'unresolved': []}]
        contract = request_plan.compile_request(self.catalog, self.question, plan)
        receipt = {'route': 'shared_planner', 'version': shared_planner.VERSION,
                   'policy_identity': shared_planner.policy_identity(),
                   'request_contract': contract, 'interpretation': {'execution_accepted': True},
                   'new_paid_api_calls': 1, 'known_api_estimate_usd': 0.125,
                   'api_cost_known': known}
        self.executed = shared_planner.execute_contract(
            self.stack, self.question, shared_planner.graph_for(contract), receipt)
        self.stack.allow_live = True
        self.stack.compose = lambda *args, **kwargs: copy.deepcopy(self.executed)

    def sender(self, body):
        self.assertEqual(body['tool_choice'], {'type': 'none'})
        prompt = body['messages'][0]['content']
        rid = re.search(r'"receipt":"(R-[0-9a-f]{10})","tool":"saved_answer"', prompt).group(1)
        return {'content': [{'type': 'text', 'text': (
            'TITLE: Executive review\n```html\n<h1>Executive review</h1>'
            '<p>Synthetic saved evidence</p>'
            f'<p>Patients <span data-bind="{rid}:answer.count"></span></p>\n```'
        )}], 'usage': {'input_tokens': 100, 'output_tokens': 100}}, {
            'cost_estimate': {'estimated_usd': 0.25}}

    def ask(self, sender=None):
        with patch.object(composer, 'ledgered_sender', return_value=sender or self.sender):
            return conversation.handle_compose(self.stack, {'question': self.question})

    def usage(self):
        with sqlite3.connect(self.path / 'answers.sqlite3') as db:
            answer = db.execute('SELECT api_calls,api_usd FROM answers ORDER BY rowid DESC LIMIT 1').fetchone()
        with sqlite3.connect(self.path / 'learning.sqlite3') as db:
            learning = db.execute('SELECT paid_calls,usd FROM outcomes ORDER BY rowid DESC LIMIT 1').fetchone()
        return answer, learning

    def test_paid_planner_and_artifact_reach_both_logs_with_exact_patients(self):
        self.prepare()
        result = self.ask()
        self.assertEqual(result['answer']['patient_ids'], ['A', 'C'])
        step = result['workflow'][0]
        self.assertEqual((step['status'], step['generation']), ('ready', 'frontier'))
        self.assertEqual(result['new_paid_api_calls'], 2)
        self.assertEqual(result['planning_economics']['new_api_estimate_usd'], 0.375)
        self.assertFalse(result['planning_economics']['total_cost_known'])
        self.assertEqual(self.usage(), ((2, 0.375), (2, 0.375)))
        context = answer_context.load(self.path, result['action_context']['id'])
        _, bundle, _, _ = composer.load(
            answer_context.workspace(self.path, context), step['deliverable_id'])
        saved = next(bundle.results[rid] for rid, r in bundle.receipts.items() if r['tool'] == 'saved_answer')
        self.assertEqual(saved['patient_ids'], ['A', 'C'])

    def test_unknown_planner_cost_remains_unknown_after_paid_artifact(self):
        self.prepare(known=False)
        result = self.ask()
        economics = result['planning_economics']
        self.assertIsNone(economics['new_api_estimate_usd'])
        self.assertEqual(economics['known_api_estimate_usd'], 0.375)
        self.assertFalse(economics['api_cost_known'])
        self.assertEqual(self.usage(), ((2, None), (2, None)))

    def test_unknown_artifact_cost_keeps_known_planner_subtotal(self):
        self.prepare()
        sender = Mock(side_effect=TimeoutError('fixture transport failure'))
        result = self.ask(sender)
        self.assertEqual(result['workflow'][0]['status'], 'blocked')
        self.assertIsNone(result['planning_economics']['new_api_estimate_usd'])
        self.assertEqual(result['planning_economics']['known_api_estimate_usd'], 0.125)
        self.assertEqual(self.usage(), ((2, None), (2, None)))
        self.assertEqual(sender.call_count, 1)

    def test_saved_workflow_replay_has_no_new_artifact_charges(self):
        self.prepare()
        first = self.ask()
        self.executed['new_paid_api_calls'] = 0
        self.executed['planning_economics'] = shared_planner.economics(
            {'known_api_estimate_usd': 0, 'api_cost_known': True})
        sender = Mock(side_effect=AssertionError('Saved artifact must not regenerate'))
        repeated = self.ask(sender)
        self.assertEqual(first['action_context']['id'], repeated['action_context']['id'])
        self.assertEqual(first['workflow'][0]['deliverable_id'], repeated['workflow'][0]['deliverable_id'])
        self.assertEqual(repeated['new_paid_api_calls'], 0)
        self.assertEqual(repeated['planning_economics']['new_api_estimate_usd'], 0)
        self.assertEqual(repeated['workflow'][0]['costs']['total_usd'], 0.25)
        self.assertEqual(self.usage(), ((0, 0), (0, 0)))
        sender.assert_not_called()

    def test_failed_planning_attempts_reach_both_logs(self):
        from clinical_core.agere.clinical.question_input import QuestionNotUnderstood
        self.question = 'An unfamiliar request with unavailable requirements'
        for known in (True, False):
            with self.subTest(known=known):
                error = QuestionNotUnderstood('Fixture interpretation declined')
                error.planner_receipt = {
                    'new_paid_api_calls': 6, 'known_api_estimate_usd': 0.6,
                    'api_cost_known': known,
                }
                self.stack.compose = Mock(side_effect=error)
                with self.assertRaises(QuestionNotUnderstood):
                    self.ask()
                cost = 0.6 if known else None
                self.assertEqual(self.usage(), ((6, cost), (6, cost)))

    def test_design_brief_cannot_hide_new_scope_or_calculations(self):
        for brief in (
            'create an executive dashboard only for uninsured patients',
            'create a dashboard for patients admitted after 2025',
            'create a dashboard showing average cost per patient',
        ):
            with self.subTest(brief=brief):
                self.prepare(brief=brief)
                sender = Mock(side_effect=AssertionError('Unresolved analytical requirement'))
                result = self.ask(sender)
                self.assertEqual(result['answer']['patient_ids'], ['A', 'C'])
                self.assertEqual(result['workflow'][0]['status'], 'needs_definition')
                sender.assert_not_called()

    def test_named_concept_quote_cannot_change_polarity_or_drop_another_concept(self):
        for base, changed in (
            ('List patients with diabetes', 'without diabetes'),
            ('List patients with diabetes', 'with diabetes and CKD'),
            ('List patients on a statin', 'on a statin and insulin'),
        ):
            with self.subTest(changed=changed):
                plan = request_language.parse(self.catalog, base)
                old_quote = plan['where']['quote']
                plan['where']['quote'] = changed
                with self.assertRaisesRegex(request_plan.PlanError, 'Named concept wording'):
                    request_plan.compile_request(self.catalog, base.replace(old_quote, changed), plan)


if __name__ == '__main__':
    unittest.main()
