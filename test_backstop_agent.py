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


class ToolErrors(unittest.TestCase):
    def test_bad_arguments_are_a_tool_error_not_a_crash(self):
        t = B.Tools.__new__(B.Tools)
        t.read, t.as_of = {}, '2030-05-27'
        with self.assertRaises(ValueError):
            t({'name': 'list_documents', 'input': {'patient': 'X'}})
        with self.assertRaises(ValueError):
            t({'name': 'nope', 'input': {}})


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
