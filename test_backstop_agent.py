"""The final LLM backstop (2026-10-25): verified quotes, recoverable tool errors, acceptance without a veto, stated absence. No model is called."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from clinical_core.agere.clinical import backstop_agent as B, proposers as P


class FakeTools:
    def __init__(self, docs): self.read = {k: {'text': v} for k, v in docs.items()}
    def read_document(self, rid):
        if rid not in self.read: raise ValueError('no document')
        return {'text': self.read[rid]['text']}


class Quotes(unittest.TestCase):
    def test_a_quote_must_be_verbatim_in_its_document(self):
        tools = FakeTools({'N1': 'Plan: Stop losartan 100 mg due to hyperkalemia (K 5.9).'})
        answer = {'sources': [{'record_id': 'N1', 'date': '2029-08-05', 'quote': 'Stop losartan 100 mg  due to hyperkalemia'},
                              {'record_id': 'N1', 'quote': 'losartan was continued'},
                              {'record_id': 'N9', 'quote': 'anything'}]}
        kept, dropped = B.verified_sources(answer, tools)
        self.assertEqual([s['quote'] for s in kept], ['Stop losartan 100 mg  due to hyperkalemia'])
        self.assertEqual(dropped, 2)


class ShownRows(unittest.TestCase):
    def test_a_quote_of_a_row_a_tool_returned_is_kept(self):
        tools = FakeTools({})
        tools.seen = ['{"rows": [{"record_id": "R1", "payload": "{\\"medication_name_reported\\": \\"lisinopril 20 MG\\", \\"use\\": \\"reports_taking\\"}"}]}']
        kept, dropped = B.verified_sources({'sources': [{'record_id': 'R1', 'quote': '"medication_name_reported": "lisinopril 20 MG"'}]}, tools)
        self.assertEqual((len(kept), dropped), (1, 0))


class Snapping(unittest.TestCase):
    def test_a_near_verbatim_quote_is_healed_to_the_documents_own_words(self):
        doc = 'HPI: 64M with chest pain. ECG: sinus rhythm, ST depression in V4-V6, no ST elevation. Troponin 0.02.'
        tools = FakeTools({'N1': doc})
        kept, dropped = B.verified_sources({'sources': [{'record_id': 'N1', 'quote': 'ECG showed sinus rhythm, ST depression V4-V6 ... no ST elevation'}]}, tools)
        self.assertEqual(dropped, 0)
        self.assertTrue(kept[0]['quote_snapped'])
        for piece in kept[0]['quote'].split(' ... '): self.assertIn(piece, doc)
        self.assertIsNone(B.snap('patient refused insulin pump therapy', doc))


class Shape(unittest.TestCase):
    def test_numbers_in_the_headline_are_not_read_as_an_absent_value(self):
        a = B.shaped({'status': 'answered', 'answer_type': 'value', 'headline': '97 of 490 stays (19.8%)', 'value': None, 'count': None,
                      'table': {'columns': ['g', 'n'], 'rows': [['HF', 51]]}})
        self.assertEqual(a['table'], [{'g': 'HF', 'n': 51}])
        self.assertNotIn('value', a)
        self.assertEqual(a['answer_type'], 'composite')
        from clinical_core.agere.clinical import answer_format
        answer_format._honest_unknown(a)
        self.assertEqual(a['headline'], '97 of 490 stays (19.8%)')


class ToolErrors(unittest.TestCase):
    def test_bad_arguments_are_a_tool_error_not_a_crash(self):
        t = B.Tools.__new__(B.Tools)
        t.read, t.as_of = {}, '2030-05-27'
        with self.assertRaises(ValueError):
            t({'name': 'list_documents', 'input': {'patient': 'X'}})
        with self.assertRaises(ValueError):
            t({'name': 'nope', 'input': {}})


class EmulatedToolCalls(unittest.TestCase):
    TOOLS = [{'name': 'query_sql', 'input_schema': {}}]

    def test_a_tool_call_followed_by_more_text_is_still_a_tool_call(self):
        from clinical_core.agere import subscription_transport as ST
        text = '{"tool_use": {"name": "query_sql", "input": {"sql": "SELECT 1"}}}\nThen I will group by code.'
        self.assertEqual(ST._tool_call(text, self.TOOLS), {'name': 'query_sql', 'input': {'sql': 'SELECT 1'}})
        self.assertIsNone(ST._tool_call('{"status": "answered"}', self.TOOLS))

    def test_a_text_reply_that_is_a_tool_call_is_run_as_one(self):
        reply = {'content': [{'type': 'text', 'text': '{"tool_use": {"name": "query_sql", "input": {"sql": "SELECT 1"}}}'}], 'stop_reason': 'end_turn'}
        response, _, _ = B.as_tool_calls(lambda body: (reply, 0.0, True))({'tools': self.TOOLS})
        self.assertEqual(response['content'][0]['type'], 'tool_use')
        self.assertEqual(response['content'][0]['input'], {'sql': 'SELECT 1'})
        final = {'content': [{'type': 'text', 'text': '{"status": "answered"}'}]}
        self.assertIs(B.as_tool_calls(lambda body: (final, 0.0, False))({'tools': self.TOOLS})[0], final)


class EmptyReply(unittest.TestCase):
    def test_an_empty_reply_is_asked_again_with_a_changed_request(self):
        seen = []
        def send(body):
            seen.append(len(body['messages']))
            return ({'content': [{'type': 'text', 'text': ''}]} if len(seen) == 1 else {'content': [{'type': 'text', 'text': '{"status": "answered"}'}]}), 0.0, False
        response, _, _ = B.as_tool_calls(send)({'tools': [], 'messages': [{'role': 'user', 'content': 'q'}]})
        self.assertEqual(seen, [1, 2])
        self.assertEqual(response['content'][0]['text'], '{"status": "answered"}')


class SubQuestionIsolation(unittest.TestCase):
    def test_a_sub_question_does_not_replace_this_questions_budget(self):
        from clinical_core.agere.clinical import answer_budget, shared_planner, decision_context, proposers
        outer = answer_budget.start(120)
        def fake_compose(stack, q, **kw):
            self.assertTrue(proposers.FREE_ONLY.get())
            answer_budget.start(8)  # what a nested cascade does
            return {'answer': {'status': 'answered', 'headline': '578', 'value': 578}}
        t = B.Tools.__new__(B.Tools)
        t.stack, t.as_of, t.governed = SimpleNamespace(path='x'), '2030-05-27', []
        with patch.object(shared_planner, '_compose', fake_compose), patch.object(decision_context, 'finalize', lambda p, r, s: r):
            got = t.ask_agere('How many inpatient admissions were there in 2028?')
        self.assertEqual(got['value'], 578)
        self.assertIs(answer_budget.current(), outer)
        self.assertFalse(proposers.FREE_ONLY.get())


class Cascade(unittest.TestCase):
    def cascade(self):
        c = P.Cascade.__new__(P.Cascade)
        c.stack, c.question, c.population, c.log, c.deferred = SimpleNamespace(allow_live=True), 'Why was losartan stopped for P1?', None, [], []
        c.catalog = SimpleNamespace(identity={'as_of': '2030-05-27'})
        c.usd, c.calls, c.cost_known = 0.0, 0, True
        c._ledger, c._owned = object(), False
        return c

    def test_the_backstop_answer_is_accepted_with_checks_as_annotations(self):
        run = {'answer': {'status': 'answered', 'answer_type': 'note', 'headline': 'Stopped for hyperkalemia.',
                          'sources': [{'record_id': 'N1', 'quote': 'Stop losartan due to hyperkalemia'}]},
               'tool_calls': [{'sql': None}], 'model': 'claude-sonnet-5-5', 'new_paid_api_calls': 2, 'estimated_cost_usd': 0.01}
        with patch.object(P, 'claude_available', lambda: True), patch.object(B, 'answer', return_value=run):
            contract, interp = self.cascade().tier_backstop_agent()
        a = contract['executed']['answer']
        self.assertEqual(interp['interpreter'], 'backstop_agent')
        self.assertEqual(a['answer_type'], 'note_answer')
        self.assertEqual(a['sources'][0]['record_id'], 'N1')

    def test_unsupported_becomes_a_stated_absence_and_no_answer_is_a_rejection(self):
        run = {'answer': {'status': 'unsupported', 'answer_type': 'unsupported', 'headline': 'No HCC data.', 'missing': ['HCC']},
               'tool_calls': [], 'model': 'm', 'new_paid_api_calls': 1, 'estimated_cost_usd': 0.0}
        with patch.object(P, 'claude_available', lambda: True), patch.object(B, 'answer', return_value=run):
            contract, _ = self.cascade().tier_backstop_agent()
        self.assertEqual(contract['executed']['answer']['status'], 'not_available')
        c = self.cascade()
        with patch.object(P, 'claude_available', lambda: True), patch.object(B, 'answer', return_value={'answer': None, 'error': 'x'}), \
                patch.object(P.Cascade, 'reject', lambda self, name, reasons: None) as rej:
            self.assertIsNone(c.tier_backstop_agent())

    def test_off_switch(self):
        with patch.dict(os.environ, {'AGERE_BACKSTOP': 'off'}):
            self.assertIsNone(self.cascade().tier_backstop_agent())


if __name__ == '__main__':
    unittest.main()
