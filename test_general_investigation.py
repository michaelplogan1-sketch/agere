"""Shared investigation behavior, with scripted providers and received-evidence fixtures. No question banks or live calls."""
import json
import math
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from clinical_core.agere.clinical import backstop_agent as B, semantic_analyst as S, system_one_gate as G, proposers as P, answer_budget as AB


def final(answer):
    return {'content': [{'type': 'text', 'text': json.dumps(answer)}], 'usage': {'input_tokens': 10, 'output_tokens': 10}}


def tool(name, args, ident='t1'):
    return {'content': [{'type': 'tool_use', 'id': ident, 'name': name, 'input': args}], 'usage': {'input_tokens': 10, 'output_tokens': 10}}


class Investigation(unittest.TestCase):
    def run_loop(self, replies, review, execute=lambda use: {'value': 1}, max_tools=None):
        sent = []
        def send(body):
            sent.append(json.loads(json.dumps(body)))
            item = next(replies)
            if isinstance(item, Exception): raise item
            return item, 0.01, False
        return S.tool_loop('original question', [], [{'name': 'query_sql'}], execute, send=send, model='scripted',
                           max_tools=max_tools, format_retries=1, review=review), sent

    def test_repairs_retain_original_question_and_all_tool_evidence_across_domains(self):
        for label in ('clinical status and reason', 'paid claims and reversal amount', 'groups, rates and comparison'):
            with self.subTest(label=label):
                replies = iter([tool('query_sql', {'sql': 'SELECT 1'}), final({'status': 'answered', 'headline': label}),
                                tool('query_sql', {'sql': 'SELECT 2'}, 't2'), final({'status': 'answered', 'facts': {'first': 1, 'second': 2}})])
                def review(answer, calls):
                    return {'status': 'confirmed', 'feedback': None} if len(calls) == 2 else {'status': 'repair', 'feedback': 'The second requested part is missing.'}
                result, sent = self.run_loop(replies, review, lambda use: {'value': int(use['input']['sql'][-1])})
                self.assertIsNone(result['error'])
                self.assertEqual(result['answer']['facts'], {'first': 1, 'second': 2})
                self.assertEqual(len(result['answer_reviews']), 2)
                self.assertEqual(result['new_paid_api_calls'], 4)
                self.assertAlmostEqual(result['estimated_cost_usd'], 0.04)
                self.assertEqual(sent[-1]['messages'][0]['content'], 'original question')
                observations = [json.loads(block['content']) for message in sent[-1]['messages'] if isinstance(message['content'], list)
                                for block in message['content'] if block.get('type') == 'tool_result']
                self.assertEqual(observations, [{'value': 1}, {'value': 2}])
                self.assertEqual(sent[2]['tool_choice']['type'], 'auto')

    def test_same_rejected_answer_without_new_evidence_stops_as_partial(self):
        answer = {'status': 'answered', 'headline': 'Only one part.'}
        result, sent = self.run_loop(iter([final(answer), final(answer)]), lambda a, c: {'status': 'repair', 'feedback': 'Missing requested comparison.'})
        self.assertEqual(len(sent), 2)
        self.assertEqual(result['review_stopped'], 'no_progress')
        self.assertEqual(result['answer']['status'], 'partial')
        self.assertEqual(result['answer']['verification'], 'unverified')

    def test_provider_failure_during_repair_preserves_evidence_and_partial_answer(self):
        replies = iter([tool('query_sql', {'sql': 'SELECT 1'}), final({'status': 'answered', 'value': 1}), RuntimeError('subscription rate limit')])
        result, _ = self.run_loop(replies, lambda a, c: {'status': 'repair', 'feedback': 'Missing the second output.'})
        self.assertIn('subscription rate limit', result['error'])
        self.assertEqual(result['answer']['status'], 'partial')
        self.assertEqual(len(result['tool_calls']), 1)
        self.assertIsNone(result['estimated_cost_usd'])
        self.assertAlmostEqual(result['known_cost_usd'], 0.02)

    def test_json_extracted_from_prose_still_gets_reviewed(self):
        seen = []
        reply = {'content': [{'type': 'text', 'text': 'Answer follows: {"status":"answered","value":3}'}]}
        result, _ = self.run_loop(iter([reply]), lambda a, c: seen.append(a) or {'status': 'confirmed', 'feedback': None})
        self.assertEqual(seen[0]['value'], 3)
        self.assertEqual(result['format_repair'], 'extracted')

    def test_investigation_has_no_default_twenty_call_quota(self):
        replies = iter([*(tool('query_sql', {'sql': f'SELECT {i}'}, str(i)) for i in range(25)), final({'status': 'answered', 'value': 25})])
        result, _ = self.run_loop(replies, lambda a, c: {'status': 'confirmed', 'feedback': None})
        self.assertIsNone(result['error'])
        self.assertEqual(len(result['tool_calls']), 25)


class ScopeAndEvidence(unittest.TestCase):
    def test_named_patient_scopes_governed_tools_and_cannot_escape_a_selected_cohort(self):
        from clinical_core.agere.clinical import note_evidence
        with patch.object(note_evidence, 'connect', return_value=object()):
            tools = B.Tools(SimpleNamespace(path='unused'), None, '2030-01-01', patients=['P1'])
            self.assertEqual(tools.cohort, ['P1'])
            self.assertEqual(tools.scope, {'P1'})
            with self.assertRaises(ValueError): B.Tools(SimpleNamespace(), None, '2030-01-01', patients=['P2'], cohort=['P1'])

    def test_an_unscoped_request_replaces_the_previous_temp_cohort(self):
        import sqlite3
        wh = SimpleNamespace(db=sqlite3.connect(':memory:'), _authorize=lambda *a: sqlite3.SQLITE_OK)
        wh.db.execute('CREATE TABLE patient(patient_id)')
        wh.db.execute("INSERT INTO patient VALUES ('P1'),('P2')")
        B.define_cohort(wh, ['P1'])
        self.assertEqual(wh.db.execute('SELECT count(*) FROM cohort').fetchone()[0], 1)
        B.define_cohort(wh, None)
        self.assertEqual(wh.db.execute('SELECT count(*) FROM cohort').fetchone()[0], 2)

    def test_explicit_empty_cohort_does_not_become_the_whole_warehouse(self):
        from clinical_core.agere.clinical import note_evidence, shared_planner, decision_context
        with patch.object(note_evidence, 'connect', return_value=object()):
            tools = B.Tools(SimpleNamespace(path='unused'), None, '2030-01-01', cohort=[])
        self.assertEqual(tools.cohort, [])
        with self.assertRaises(ValueError): tools.in_scope('someone_else')
        seen = []
        with patch.object(shared_planner, '_compose', side_effect=lambda s, q, **kw: seen.append(kw) or {'answer': {'status': 'answered', 'count': 0}}), \
             patch.object(decision_context, 'finalize', side_effect=lambda p, r, s: r):
            tools.ask_agere('How many patients?')
        self.assertEqual(seen[0]['population'], [])

    def test_document_judgment_does_not_silently_discard_the_thirteenth_document(self):
        from clinical_core.agere.clinical import system_one
        tools = B.Tools.__new__(B.Tools)
        tools.as_of, tools.ledger, tools.stack, tools.judged = '2030-01-01', None, SimpleNamespace(allow_live=False), []
        tools.read = {str(i): {'text': f'document {i}', 'date': '2029-01-01'} for i in range(13)}
        with patch.object(system_one, 'decide', return_value={'label': 'yes', 'calibrated_probabilities': {'yes': .9}}) as decide:
            result = tools.judge_documents('Relevant?', {'yes': 'relevant'}, list(tools.read))
        self.assertEqual(decide.call_count, 13)
        self.assertEqual(len(result['documents']), 13)

    def test_answer_checker_receives_late_claims_facts_and_all_rows(self):
        answer = {'headline': 'x' * 2200 + ' final clinical claim', 'facts': {'last_requested_part': '$42'},
                  'table': [{'group': i, 'amount': i} for i in range(6)]}
        text = G.render('q', None, {'answer': answer}, limit=None)
        self.assertIn('final clinical claim', text)
        self.assertIn('last_requested_part', text)
        self.assertIn('"group": 5', text)


class FinalReview(unittest.TestCase):
    def review(self, coverage='answers_fully', support='supported'):
        tools = SimpleNamespace(read={'N1': {'text': 'Insulin was stopped.', 'date': '2029-01-01'}}, rows_by_record={})
        answer = {'status': 'answered', 'headline': 'Insulin continued.', 'sources': [{'record_id': 'N1', 'quote': 'Insulin was stopped.'}]}
        model = SimpleNamespace(decide=lambda *a, **kw: {'label': support, 'decided_by': 'jev', 'probabilities': {support: .95}, 'provider': 'jev'})
        with patch.object(G, 'ready', return_value=True), patch.object(G, 's1', return_value=model), \
             patch.object(G, 'check', return_value={'label': coverage, 'calls': 1, 'cost_usd': .0002}):
            return B.review_final(SimpleNamespace(), 'What happened to insulin?', answer, [{'result': {'text': 'Insulin was stopped.'}}], tools,
                                  ledger=None, as_of='2030-01-01')

    def test_a_real_quote_cannot_confirm_a_contradicted_headline(self):
        result = self.review(support='contradicted')
        self.assertEqual(result['status'], 'repair')
        self.assertIn('contradicted', result['feedback'])

    def test_partial_coverage_requests_more_investigation(self):
        result = self.review(coverage='answers_partially')
        self.assertEqual(result['status'], 'repair')
        self.assertIn('every requested part', result['feedback'])

    def test_complete_supported_answer_records_a_model_review(self):
        self.assertEqual(self.review()['status'], 'confirmed')

    def test_missing_reviewer_is_not_a_successful_review(self):
        with patch.object(G, 'ready', return_value=False):
            result = B.review_final(SimpleNamespace(), 'q', {'status': 'answered'}, [], SimpleNamespace(read={}), ledger=None, as_of='2030-01-01')
        self.assertEqual(result['status'], 'unavailable')

    def test_no_default_time_deadlines(self):
        with patch.dict(os.environ, {'AGERE_ANSWER_BUDGET_SECONDS': '', 'AGERE_BACKSTOP_SECONDS': ''}):
            self.assertEqual(AB.default_seconds(), math.inf)
            self.assertIsNone(P.backstop_limit())

    def test_unconfirmed_review_cannot_be_overridden_by_quote_presence(self):
        cascade = P.Cascade.__new__(P.Cascade)
        cascade.stack, cascade.question, cascade.population, cascade.log = SimpleNamespace(allow_live=True), 'q', None, []
        cascade.usd, cascade.calls, cascade.cost_known = 0, 0, True
        cascade._ledger, cascade._owned = object(), False
        cascade.catalog = SimpleNamespace(identity={'as_of': '2030-01-01'})
        run = {'answer': {'status': 'answered', 'answer_type': 'note', 'headline': 'Continued.',
                          'sources': [{'record_id': 'N1', 'quote': 'Stopped.'}],
                          'investigation_review': {'status': 'repair', 'feedback': 'Contradicted.'}}, 'tool_calls': []}
        with patch.object(P, 'claude_available', return_value=True), patch.object(B, 'answer', return_value=run):
            contract, _ = cascade.tier_backstop_agent()
        self.assertEqual(contract['executed']['answer']['verification'], 'unverified')
        self.assertEqual(contract['executed']['answer']['status'], 'partial')

    def test_certified_patient_answer_can_still_be_sent_for_completeness_repair(self):
        cascade = P.Cascade.__new__(P.Cascade)
        cascade.stack, cascade.question, cascade.population, cascade.log, cascade.claude_down = SimpleNamespace(allow_live=True), 'q', None, [], None
        cascade._ledger, cascade._owned = object(), False
        first_answer = {'status': 'answered', 'facts': {'one': 1}, 'table': [{'group': 'A', 'n': 1}]}
        first = ({'executed': {'answer': first_answer, 'answer_checks': {'system_one': {'label': 'answers_partially', 'p': .9}}}}, {})
        replacement = ({'executed': {'answer': {'status': 'answered', 'facts': {'one': 1, 'two': 2}}}}, {})
        with patch.object(P, 'claude_available', return_value=True), patch.object(P.Cascade, 'runnable', return_value=True), \
             patch.object(P.Cascade, 'attempt', return_value=replacement):
            got, name = cascade.second_look(['backstop_agent'], 'semantic_cache', first)
        self.assertEqual(name, 'backstop_agent')
        self.assertIs(got, replacement)
        self.assertEqual(cascade.second_look_hint['earlier_answer']['answer'], first_answer)


class SubscriptionBoundary(unittest.TestCase):
    def test_subscription_limit_is_not_retried_on_another_model(self):
        from clinical_core.agere.gateway import call_with_fallback, ProviderUnavailable
        attempted = []
        def call(model):
            attempted.append(model)
            raise ProviderUnavailable('weekly limit', kind='rate_limit')
        with patch.dict(os.environ, {'AGERE_CLAUDE_TRANSPORT': 'subscription'}):
            with self.assertRaises(ProviderUnavailable):
                call_with_fallback(call, 'claude-sonnet-5-5', fallback='claude-haiku-5-5', sleep=lambda t: self.fail('retried'))
        self.assertEqual(attempted, ['claude-sonnet-5-5'])

    def test_subscription_limit_stops_the_cascade(self):
        from clinical_core.agere.gateway import ProviderUnavailable
        cascade = P.Cascade.__new__(P.Cascade)
        cascade.claude_down, cascade.claude_models = None, None
        with patch.dict(os.environ, {'AGERE_CLAUDE_TRANSPORT': 'subscription'}), \
             patch.object(P.Cascade, 'call_tier', side_effect=ProviderUnavailable('weekly limit', kind='rate_limit')):
            with self.assertRaises(ProviderUnavailable): cascade.attempt('backstop_agent', None)


if __name__ == '__main__': unittest.main()
