"""Request admission, definition preservation, and paid-call accounting; no network."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from clinical_core.agere.clinical import context_student as C, conversation, nl2sql, shared_planner as S
from clinical_core.agere.clinical.question_input import QuestionNotUnderstood, failure
import test_shared_planner as fixtures


class RequestAdmission(unittest.TestCase):
    def test_oversized_request_never_touches_ledger_or_transport(self):
        ledger, transport = Mock(), Mock()
        with self.assertRaises(C.RequestSizeLimitError) as caught:
            C.send({'model': 'test', 'text': 'é' * 70000}, 'claude', ledger, Path('/unused'),
                   parse=lambda value: value, transport=transport)
        self.assertGreater(caught.exception.request_bytes, caught.exception.max_bytes)
        self.assertEqual(caught.exception.max_bytes, 128000)
        self.assertFalse(ledger.mock_calls)
        transport.assert_not_called()

    def test_explicit_role_bound_preserves_default_and_uses_complete_body(self):
        response = {'content': [{'type': 'text', 'text': '{"ok":true}'}]}
        ledger = Mock()
        ledger.db.execute.return_value.fetchone.return_value = ('validated', json.dumps(response))
        with tempfile.TemporaryDirectory() as directory:
            default = nl2sql.HostedModel(ledger, Path(directory))
            planner = nl2sql.HostedModel(ledger, Path(directory), request_max_bytes=S.REQUEST_MAX_BYTES)
            with patch.object(C.urllib.request, 'urlopen', side_effect=AssertionError('Network prohibited')):
                with self.assertRaises(C.RequestSizeLimitError):
                    default('system', 'x' * 140000)
                self.assertFalse(ledger.mock_calls)
                text, stats = planner('system', 'x' * 140000)
                self.assertEqual(text, '{"ok":true}')
                self.assertTrue(stats['cache_hit'])
                ledger.reset_mock()
                with self.assertRaises(C.RequestSizeLimitError):
                    planner('system', 'x' * S.REQUEST_MAX_BYTES)
                self.assertFalse(ledger.mock_calls)

    def test_exact_duplicate_projection_retains_all_definitions_and_original(self):
        original = {
            'selected_schemas': {
                'patient': {'columns': ['id', 'birth_date'], 'meaning': 'Received patients', 'key': 'id'},
                'different': {'columns': ['id', 'new_field'], 'meaning': 'More specific meaning'},
                'absent': {'meaning': 'No discovered metadata'},
            },
            'discovery_metadata': {'relations': {
                'patient': {'columns': {'id': {'type': 'TEXT'}, 'birth_date': {'type': 'DATE'}},
                            'meaning': 'Received patients', 'joins': ['condition.patient_id']},
                'different': {'columns': {'id': {'type': 'TEXT'}}, 'meaning': 'General meaning'},
            }},
            'analytical_operators': {'complete': 'A governed calculation', 'different': 'Extra caveat'},
            'selected_operator_contracts': {
                'complete': {'meaning': 'A governed calculation', 'parameters': {'required': ['as_of']}},
                'different': {'meaning': 'Base calculation'},
            },
            'governed_measures': {'age': {'definition': 'birth_date at as_of', 'version': 'v1'}},
            'named_concepts': {'drug': {'definition': 'governed picked-up fill'}},
        }
        untouched = copy.deepcopy(original)
        compact = S.schema_prompt_view(original)
        self.assertEqual(original, untouched)
        # Resolve every reference to recover the original schema meaning/field set.
        for name, schema in compact['selected_schemas'].items():
            for field in ('columns', 'meaning'):
                reference = schema.get(field + '_in')
                if reference:
                    value = compact
                    for part in reference.split('.'):
                        value = value[part]
                    expected = original['selected_schemas'][name][field]
                    self.assertEqual(set(value), set(expected)) if field == 'columns' else self.assertEqual(value, expected)
                elif field in original['selected_schemas'][name]:
                    self.assertEqual(schema[field], original['selected_schemas'][name][field])
        for name in ('discovery_metadata', 'selected_operator_contracts', 'governed_measures', 'named_concepts'):
            self.assertEqual(compact[name], original[name])
        self.assertEqual(compact['analytical_operators']['different'], 'Extra caveat')
        self.assertEqual(compact['selected_schemas']['patient']['key'], 'id')
        self.assertNotIn('columns_in', compact['selected_schemas']['absent'])

    def test_unsent_preflight_is_not_paid_and_prior_costs_remain_known(self):
        for prior in ([], [{'api_usd': 0.24, 'api_cost_known': True},
                          {'api_usd': 0.28, 'api_cost_known': True}]):
            attempts = copy.deepcopy(prior)
            model = Mock(side_effect=C.RequestSizeLimitError(300000, S.REQUEST_MAX_BYTES))
            model.hosted = True
            with self.assertRaises(C.RequestSizeLimitError):
                S.tracked_call(model, attempts, 'system', 'user')
            receipt = S.attempt_receipt(attempts)
            self.assertEqual(receipt['new_paid_api_calls'], len(prior))
            self.assertAlmostEqual(receipt['known_api_estimate_usd'], sum(a['api_usd'] for a in prior))
            self.assertTrue(receipt['api_cost_known'])
            self.assertFalse(attempts[-1]['request_sent'])

    def test_transport_failure_still_has_unknown_cost(self):
        model = Mock(side_effect=TimeoutError('transport timed out'))
        model.hosted = True
        attempts = [{'api_usd': 0.24, 'api_cost_known': True}]
        with self.assertRaises(TimeoutError):
            S.tracked_call(model, attempts, 'system', 'user')
        receipt = S.attempt_receipt(attempts)
        self.assertEqual(receipt['new_paid_api_calls'], 2)
        self.assertEqual(receipt['known_api_estimate_usd'], 0.24)
        self.assertFalse(receipt['api_cost_known'])


class PlannerAdmission(unittest.TestCase):
    def setUp(self):
        fixtures.SharedPlanner.setUp(self)

    def test_size_failure_preserves_question_scope_and_explains_admission(self):
        seen = []
        question = 'List sensor records below 20, only in 2029, excluding CKD, by payer'
        def model(system, user, **kwargs):
            seen.append(json.loads(user))
            raise C.RequestSizeLimitError(300000, S.REQUEST_MAX_BYTES)
        with self.assertRaises(QuestionNotUnderstood) as caught:
            S.checked_interpret(self.catalog, question, population=['A'], model=model, force_model=True)
        self.assertEqual(seen[0]['question'], question)
        self.assertEqual(seen[0]['saved_cohort_size'], 1)
        receipt = caught.exception.planner_receipt
        self.assertFalse(receipt['request_admission']['request_sent'])
        self.assertEqual(receipt['new_paid_api_calls'], 0)
        self.assertTrue(receipt['api_cost_known'])
        result = failure(caught.exception)
        self.assertEqual(result['status'], 'not_understood')
        self.assertIn('complete planning context', result['error'])
        self.assertNotIn('synthetic research bound', result['error'])

    def test_retry_keeps_constraints_and_prior_feedback(self):
        question = 'List sensor records below 20, only in 2029, excluding CKD, by payer'
        seen = []
        def model(system, user, **kwargs):
            seen.append(json.loads(user))
            if len(seen) == 1:
                return 'not a valid plan', {'api_usd': 0.24, 'api_cost_known': True}
            raise C.RequestSizeLimitError(300000, S.REQUEST_MAX_BYTES)
        with self.assertRaises(QuestionNotUnderstood) as caught:
            S.checked_interpret(self.catalog, question, population=['A'], model=model, force_model=True)
        self.assertEqual(len(seen), 2)
        self.assertTrue(seen[1]['validator_feedback'])
        self.assertTrue(all(p['question'] == question and p['saved_cohort_size'] == 1 for p in seen))
        self.assertEqual(caught.exception.planner_receipt['new_paid_api_calls'], 1)
        self.assertEqual(caught.exception.planner_receipt['known_api_estimate_usd'], 0.24)
        self.assertTrue(caught.exception.planner_receipt['api_cost_known'])

    def test_conversation_logs_two_completed_calls_and_unsent_repair(self):
        self.stack.allow_live = True
        self.stack.receipts = self.path
        question = 'Review sensor evidence and preserve the full requested workflow'
        model = Mock(side_effect=[
            (json.dumps({'need_tables': ['new_sensor_feed']}), {'api_usd': 0.24, 'api_cost_known': True}),
            ('invalid proposal', {'api_usd': 0.28, 'api_cost_known': True}),
            C.RequestSizeLimitError(300000, S.REQUEST_MAX_BYTES),
        ])
        model.hosted = True
        model.identity = 'offline-provider-fixture'
        with patch.object(nl2sql, 'HostedModel', return_value=model) as constructor, \
                patch('clinical_core.agere.gateway.BudgetLedger'):
            with self.assertRaises(QuestionNotUnderstood):
                conversation.handle_compose(self.stack, {'question': question, 'conversation_id': 'bound-failure'})
        self.assertEqual(constructor.call_args.kwargs['request_max_bytes'], S.REQUEST_MAX_BYTES)
        with sqlite3.connect(self.path / 'learning.sqlite3') as db:
            self.assertEqual(db.execute('SELECT status,paid_calls,usd FROM outcomes').fetchone(),
                             ('not_understood', 2, 0.52))
        with sqlite3.connect(self.path / 'answers.sqlite3') as db:
            self.assertEqual(db.execute('SELECT question,status,api_calls,api_usd FROM answers').fetchone(),
                             (question, 'not_understood', 2, 0.52))

    def test_oversized_audit_stops_without_a_paid_interpretation_retry(self):
        question = 'Count patients'
        plan = S.request_language.parse(self.catalog, question)
        model = Mock(side_effect=[
            (json.dumps(plan), {'api_usd': 0.24, 'api_cost_known': True}),
            C.RequestSizeLimitError(300000, S.REQUEST_MAX_BYTES),
            AssertionError('Admission failure must not trigger another interpretation'),
        ])
        model.hosted = True
        model.identity = 'offline-provider-fixture'
        with self.assertRaises(QuestionNotUnderstood) as caught:
            S.checked_interpret(self.catalog, question, model=model, force_model=True)
        self.assertEqual(model.call_count, 2)
        self.assertEqual(model.call_args.args[0], S.AUDIT_SYSTEM)
        receipt = caught.exception.planner_receipt
        self.assertEqual(receipt['new_paid_api_calls'], 1)
        self.assertEqual(receipt['known_api_estimate_usd'], 0.24)
        self.assertTrue(receipt['api_cost_known'])
        self.assertFalse(receipt['request_admission']['request_sent'])

    def test_discovery_receipt_survives_audit_admission_failure(self):
        question = 'Count patients'
        plan = S.request_language.parse(self.catalog, question)
        model = Mock(side_effect=[
            (json.dumps({'need_discovery': {'terms': ['patient']}}), {'api_usd': 0.12, 'api_cost_known': True}),
            (json.dumps(plan), {'api_usd': 0.24, 'api_cost_known': True}),
            C.RequestSizeLimitError(300000, S.REQUEST_MAX_BYTES),
            AssertionError('Unexpected retry'),
        ])
        model.hosted = True
        with self.assertRaises(QuestionNotUnderstood) as caught:
            S.checked_interpret(self.catalog, question, model=model, force_model=True)
        self.assertEqual(model.call_count, 3)
        receipt = caught.exception.planner_receipt
        self.assertEqual(receipt['new_paid_api_calls'], 2)
        self.assertEqual(receipt['known_api_estimate_usd'], 0.36)
        self.assertTrue(receipt['api_cost_known'])
        discovery = receipt['evidence_discovery']
        self.assertEqual(len(discovery), 1)
        self.assertEqual(discovery[0]['catalog_search']['source_identity'], self.catalog.identity)
        self.assertFalse(receipt['request_admission']['request_sent'])


if __name__ == '__main__':
    unittest.main()
