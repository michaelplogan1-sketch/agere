"""Explicit summary input scope on authored sources; no models or evaluation banks."""
import json
import sqlite3
import unittest
from unittest.mock import Mock

import test_shared_planner as fixtures
from clinical_core.agere.clinical import conversation as C, general_analysis as G, shared_planner as S
from clinical_core.agere.deliverables import answer_context as A


class SummaryFollowup(unittest.TestCase):
    setUp = fixtures.SharedPlanner.setUp

    def summary(self, population=('A', 'B'), domain='financial'):
        queries = {
            'financial': ('Show total allowed claims', 'SELECT SUM(allowed_cents) AS amount FROM claim', 'value'),
            'clinical': ('Count recorded diabetes diagnoses', "SELECT COUNT(*) AS diagnoses FROM condition WHERE concept='diabetes'", 'count'),
            'access': ('Show appointment counts by status', 'SELECT final_status,COUNT(*) AS appointments FROM appointment GROUP BY final_status', 'table'),
        }
        question, sql, returns = queries[domain]
        population = None if population is None else list(population)
        spec = {'steps': [{'name': 'summary', 'purpose': question, 'sql': sql}], 'returns': returns,
                'requirements': [{'quote': question, 'applied_in': ['summary'], 'how': 'Received source summary'}], 'unresolved': []}
        contract = G.prepare(self.catalog, question, spec, population=population)
        receipt = {'version': S.VERSION, 'policy_identity': S.policy_identity(), 'request_contract': contract,
                   'interpretation': {'execution_accepted': True}, 'new_paid_api_calls': 0}
        result = G.execute(self.stack, question, G.graph_for(contract), receipt, population=population)
        return result, A.capture(self.path, question, result)

    def follow(self, context, question='count them', scope='input', conversation='scope-test'):
        return C.handle_compose(self.stack, {'question': question, 'conversation_id': conversation,
                              'answer_context_id': context['id'], 'answer_scope': scope})

    def test_three_domain_summaries_reopen_exact_input_without_action_authority(self):
        for domain in ('financial', 'clinical', 'access'):
            with self.subTest(domain=domain):
                result, context = self.summary(domain=domain)
                before = (A.root(self.path)/context['id']/'answer.json').read_bytes()
                self.assertEqual(result['answer']['patient_ids'], [])
                self.assertEqual(A.load(self.path, context['id'])['input_patient_ids'], ['A', 'B'])
                self.assertEqual(context['input_patients'], 2)
                self.assertFalse(context['result_scope_available'])
                self.stack.compose = Mock(side_effect=AssertionError('Literal follow-up must not call a model'))
                out = self.follow(context, conversation=domain)
                self.assertEqual(out['answer']['count'], 2)
                self.assertEqual(out['answer']['patient_ids'], ['A', 'B'])
                self.assertEqual(out['cohort_transform']['scope'], 'input')
                self.assertFalse(out['answer']['actionable_cohort'])
                child = A.load(self.path, out['action_context']['id'], current=True)
                self.assertEqual(child['input_patient_ids'], ['A', 'B'])
                self.assertFalse(A.supports_patient_work(child))
                self.assertEqual(before, (A.root(self.path)/context['id']/'answer.json').read_bytes())
                with self.assertRaisesRegex(ValueError, 'analytical summary'):
                    A.create_work(self.path, {'context_id': child['id'], 'program': 'custom', 'patient_ids': ['A'],
                                             'owner': 'Test', 'note': 'Must remain analytical'})

    def test_empty_explicit_input_stays_empty_and_unscoped_summary_has_no_input(self):
        _, empty = self.summary(population=[])
        self.assertEqual(empty['input_patients'], 0)
        self.assertEqual(self.follow(empty)['answer']['patient_ids'], [])
        _, unscoped = self.summary(population=None)
        self.assertIsNone(unscoped['input_patients'])
        with self.assertRaisesRegex(ValueError, 'saved input population'):
            self.follow(unscoped)

    def test_engine_summary_retains_transform_input_when_contract_omits_population(self):
        for ids in (['A', 'B'], []):
            result, _ = self.summary(population=ids)
            result['planner_receipt']['request_contract'].pop('population', None)
            result['cohort_transform'] = {'input_patient_ids': ids, 'operations': ['Summarize exact input']}
            saved = A.capture(self.path, 'Summarize exact input', result)
            data = A.load(self.path, saved['id'], current=True)
            self.assertEqual(data['input_patient_ids'], ids)
            self.assertEqual(A.reference_scope(data, 'input')['patient_ids'], ids)
            self.assertFalse(A.supports_patient_work(data))

    def test_conflicting_contract_and_transform_cannot_change_input(self):
        result, _ = self.summary()
        result['cohort_transform'] = {'input_patient_ids': ['OUTSIDE']}
        with self.assertRaisesRegex(ValueError, 'disagree'):
            A.capture(self.path, 'Conflicting scopes', result)

    def test_summary_result_reference_does_not_silently_use_its_input(self):
        _, context = self.summary()
        with self.assertRaisesRegex(ValueError, 'analytical summary'):
            self.follow(context, scope='result')
        with self.assertRaisesRegex(ValueError, 'analytical summary'):
            C.handle_compose(self.stack, {'question': 'count them', 'conversation_id': 'old-body',
                                          'answer_context_id': context['id']})

    def test_new_shared_query_is_bound_to_input_and_retains_ancestry(self):
        _, context = self.summary()
        self.stack.compose = lambda q, **kw: S.compose(self.stack, q, model=Mock(side_effect=AssertionError('No model')), **kw)
        out = self.follow(context, 'Count ED visits in 2029')
        self.assertEqual(out['answer']['count'], 3)  # A=2, B=1, outside excluded
        self.assertEqual(out['request_contract']['population'], ['A', 'B'])
        self.assertEqual(out['cohort_transform']['scope'], 'input')
        self.assertFalse(out['answer']['actionable_cohort'])
        with sqlite3.connect(self.path/'semantic.sqlite3') as db:
            db.execute("UPDATE meta SET value='changed' WHERE key='source_digest'")
        with self.assertRaisesRegex(ValueError, 'data changed'):
            self.follow(context)

    def test_invalid_scope_and_client_membership_are_rejected_before_execution(self):
        _, context = self.summary()
        self.stack.compose = Mock(side_effect=AssertionError('Invalid scope cannot execute'))
        base = {'question': 'Count patients', 'conversation_id': 'scope-shape', 'answer_context_id': context['id']}
        for extra in ({'answer_scope': 'unknown'}, {'answer_scope': ['input']},
                      {'answer_scope': 'input', 'patient_ids': ['OUTSIDE']}, {'scope': 'warehouse', 'answer_scope': 'input'}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                C.handle_compose(self.stack, {**base, **extra})
        with self.assertRaises(ValueError):
            C.compose_shape({'question': 'Count patients', 'conversation_id': 'scope-shape', 'answer_scope': 'input'})

    def test_failed_turn_keeps_last_valid_scope_and_original_summary_is_not_mutated(self):
        _, context = self.summary()
        first = self.follow(context)
        with self.assertRaises(ValueError):
            self.follow(context, scope='invalid')
        prior = C.prior_context(self.path, 'scope-test')
        self.assertEqual(prior['action_context']['id'], first['action_context']['id'])
        self.assertEqual(A.load(self.path, context['id'])['patient_ids'], [])

    def test_altered_input_membership_and_invalidated_ancestor_cannot_be_reused(self):
        _, context = self.summary()
        child = self.follow(context)['action_context']
        target = A.root(self.path)/context['id']/'answer.json'
        data = json.loads(target.read_text())
        data['input_patient_ids'] = ['OUTSIDE']
        target.write_text(json.dumps(data))
        self.assertTrue(A.load(self.path, context['id'])['invalidated'])
        for saved in (context, child):
            with self.subTest(saved=saved['id']), self.assertRaisesRegex(ValueError, 'invalidated'):
                self.follow(saved)


if __name__ == '__main__':
    unittest.main()
