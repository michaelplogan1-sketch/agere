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


class Polarity(unittest.TestCase):
    def test_a_repair_never_flips_negation(self):
        tools = FakeTools({'N1': 'Patient does take insulin daily.'})
        kept, dropped = B.verified_sources({'sources': [{'record_id': 'N1', 'quote': 'Patient does not take insulin daily.'}]}, tools)
        self.assertEqual((kept, dropped), ([], 1))  # Codex review 2026-10-26 reproduction
        self.assertIsNone(B.snap('metformin stopped', 'Metformin continued at 500 mg.'))
        self.assertEqual(B.snap('Patient takes insulin  daily', 'Patient takes insulin daily.'), 'Patient takes insulin daily')


class Provenance(unittest.TestCase):
    def test_a_quote_of_a_row_is_kept_only_for_that_rows_own_record(self):
        tools = FakeTools({})
        tools.rows_by_record = {'R1': ['{"record_id": "R1", "payload": "{\\"medication_name_reported\\": \\"lisinopril 20 MG\\"}"}']}
        quote = '"medication_name_reported": "lisinopril 20 MG"'
        kept, dropped = B.verified_sources({'sources': [{'record_id': 'R1', 'quote': quote}]}, tools)
        self.assertEqual((len(kept), dropped), (1, 0))
        kept, dropped = B.verified_sources({'sources': [{'record_id': 'R2', 'quote': quote}]}, tools)  # audit repro: right quote, wrong record
        self.assertEqual((len(kept), dropped), (0, 1))

    def test_rows_with_record(self):
        self.assertEqual(B.rows_with_record({'columns': ['record_id', 'x'], 'rows': [['R1', 2]]}), [{'record_id': 'R1', 'x': 2}])
        self.assertEqual(B.rows_with_record({'rows': [{'record_id': 'R9'}]}), [{'record_id': 'R9'}])


class JudgeDocuments(unittest.TestCase):
    def test_system_one_reads_each_whole_document_and_code_aggregates(self):
        from clinical_core.agere.clinical import system_one
        t = B.Tools.__new__(B.Tools)
        t.as_of, t.ledger, t.stack, t.judged = '2030-05-27', None, SimpleNamespace(allow_live=True), []
        t.read = {'N1': {'text': 'x' * 3000 + ' amLODIPine 2.5 MG: reported status: not using.', 'date': '2027-05-10', 'available': '2027-05-10', 'type': 'discharge_summary'},
                  'N2': {'text': 'Unrelated visit.', 'date': '2026-01-01', 'available': '2026-01-01', 'type': 'progress'}}
        calls = []
        def decide(question, options, evidence, **kw):
            calls.append(len(evidence))
            hit = [e for e in evidence if 'not using' in e['text']]
            return {'label': 'no' if hit else 'unknown', 'calibrated_probabilities': {'no': 0.97, 'unknown': 0.9}, 'evidence_ids': [hit[0]['id']] if hit else [],
                    'decided_by': 'jev'}
        with patch.object(system_one, 'decide', decide):
            out = t.judge_documents('Was the patient reported to be using amlodipine?', {'yes': 'using', 'no': 'not using'}, ['N1', 'N2'])
        self.assertEqual(calls[0], 2)  # the whole 3,000+ character document in chunks, not one snippet
        self.assertEqual(out['latest']['record_id'], 'N1')
        self.assertEqual(out['latest']['label'], 'no')
        self.assertIn('not using', out['latest']['passage'])
        self.assertEqual(out['unknown_documents'], 1)


class Scope(unittest.TestCase):
    def test_document_tools_refuse_records_outside_the_request(self):
        t = B.Tools.__new__(B.Tools)
        t.as_of, t.scope = '2030-05-27', {'P1'}
        t.read = {'N1': {'patient_id': 'P1', 'text': 'ok', 'date': '2029-01-01'}, 'N2': {'patient_id': 'P2', 'text': 'other', 'date': '2029-01-01'},
                  'N3': {'patient_id': 'P1', 'text': 'later', 'date': '2031-01-01'}}
        self.assertEqual(t.read_document('N1')['text'], 'ok')
        with self.assertRaises(ValueError): t.read_document('N2')  # Codex review 2026-10-26: direct reads bypassed scope
        with self.assertRaises(ValueError): t.read_document('N3')
        t.scope = None
        self.assertEqual(t.read_document('N2')['text'], 'other')  # a population question reads any patient


class UncachedVisibility(unittest.TestCase):
    def test_an_uncached_record_is_checked_against_its_event_and_availability_dates(self):
        import sqlite3
        db = sqlite3.connect(':memory:'); db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE record (record_id, patient_id, stream, event_date, recorded_date, available_date, organization_id, payload)')
        db.execute('CREATE TABLE scanned_document_text (record_id, patient_id, text_source, text)')
        db.execute('CREATE TABLE scanned_document (record_id, event_date, available_date)')
        db.execute("INSERT INTO record VALUES ('R1','P1','note','2031-01-01',NULL,'2031-01-01',NULL,'{\"text\": \"future note\"}')")
        db.execute("INSERT INTO record VALUES ('R2','P1','note','2030-05-01',NULL,'2030-06-30',NULL,'{\"text\": \"received later\"}')")
        db.execute("INSERT INTO record VALUES ('R3','P1','note','2030-05-01',NULL,'2030-05-02',NULL,'{\"text\": \"visible\"}')")
        t = B.Tools.__new__(B.Tools)
        t.db, t.read, t.as_of, t.scope = db, {}, '2030-05-27', None
        with self.assertRaises(ValueError): t.read_document('R1')  # Codex review 2026-10-26: uncached reads carried no date
        with self.assertRaises(ValueError): t.read_document('R2')  # happened before as-of but received after it
        self.assertEqual(t.read_document('R3')['text'], 'visible')


class ModalityCheck(unittest.TestCase):
    def test_answers_without_sql_get_their_own_check(self):
        ok = [{'sql': None, 'result': {'count': 97, 'rows': [{'n': 490}]}}]
        self.assertEqual(P.modality_check({'status': 'answered', 'headline': '97 of 490 stays (19.8%)'}, ok)[0], 'grounded')
        self.assertEqual(P.modality_check({'status': 'answered', 'headline': '123 stays'}, ok)[0], 'unverified')
        self.assertEqual(P.modality_check({'status': 'answered', 'headline': 'Stopped.', 'sources': [{'record_id': 'N1', 'quote': 'stopped'}]}, [])[0],
                         'quote_verified')
        self.assertEqual(P.modality_check({'status': 'not_available', 'headline': 'No dialysis.'}, [])[0], 'unverified')
        self.assertEqual(P.modality_check({'status': 'not_available', 'headline': 'No dialysis.'}, ok)[0], 'absence_searched')


class Metrics(unittest.TestCase):
    def test_metrics_are_typed_and_an_unknown_id_is_a_correctable_tool_error(self):
        t = B.Tools.__new__(B.Tools)
        ids = {m['id'] for m in t.list_metrics()['metrics']}
        self.assertIn('ed_visits', ids)
        with self.assertRaises(ValueError):
            t.execute_metric('not_a_metric', '2029-01-01', '2029-12-31')


class Cohort(unittest.TestCase):
    def test_the_saved_population_is_a_view_the_agent_joins_to(self):
        import sqlite3
        class W:
            def __init__(self):
                self.db = sqlite3.connect(':memory:')
            def _authorize(self, *a): return sqlite3.SQLITE_OK
        w = W()
        B.define_cohort(w, ['P1', "O'Neil"])
        self.assertEqual(sorted(r[0] for r in w.db.execute('SELECT patient_id FROM cohort')), ["O'Neil", 'P1'])


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

    def test_native_invoke_markup_is_a_tool_call(self):
        from clinical_core.agere import subscription_transport as ST
        text = '<invoke name="query_sql">\n<parameter name="sql">SELECT 1</parameter>\n</invoke>'
        self.assertEqual(ST._tool_call(text, self.TOOLS), {'name': 'query_sql', 'input': {'sql': 'SELECT 1'}})
        self.assertIsNone(ST._tool_call('<invoke name="drop_table"><parameter name="x">1</parameter></invoke>', self.TOOLS))

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

    def test_partial_investigation_retains_terminal_review_and_draft_state(self):
        review={'status':'repair','feedback':'Coverage remains undecided.'}
        run={'answer':{'status':'partial','answer_type':'note','headline':'Received evidence is incomplete.',
                      'investigation_review':review},
             'tool_calls':[],'model':'fixture','new_paid_api_calls':0,'estimated_cost_usd':0,
             'answer_reviews':[review],'review_stopped':'unresolved_coverage',
             'unaccepted_draft':{'draft_digest':'current','returned_answer':'last_structurally_complete_candidate'},
             'error':'Retained interrupted response'}
        with patch.object(P,'claude_available',lambda:True),patch.object(B,'answer',return_value=run):
            contract,_=self.cascade().tier_backstop_agent()
        result=contract['executed'];investigation=result['investigation']
        for key in ('review_stopped','unaccepted_draft','error','answer_reviews'):
            self.assertEqual(investigation[key],run[key])
        self.assertEqual(result['answer']['status'],'partial')
        self.assertEqual(result['answer']['verification'],'unverified')

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

    def test_the_backstop_runs_before_the_generalist_analysts_whatever_the_routers_say(self):
        got = P.backstop_before_generalists(('spec_path', 'semantic_analyst', 'hosted_typed_interpreter', 'backstop_agent', 'legacy_cost_ladder'))
        self.assertEqual(got, ('spec_path', 'backstop_agent', 'semantic_analyst', 'hosted_typed_interpreter', 'legacy_cost_ladder'))
        self.assertEqual(P.backstop_before_generalists(('backstop_agent', 'semantic_analyst')), ('backstop_agent', 'semantic_analyst'))

    def test_an_unquoted_chart_answer_asks_the_backstop_and_a_quoted_one_replaces_it(self):
        q = 'Why was losartan stopped for SYN-B2064C3E-566?'
        bare = ({'executed': {'answer': {'status': 'answered', 'answer_type': 'note_answer', 'headline': 'Hyperkalemia.', 'sources': [{'record_id': 'N1'}]}}}, {})
        quoted = ({'executed': {'answer': {'status': 'answered', 'answer_type': 'note_answer', 'headline': 'Hyperkalemia.',
                                           'sources': [{'record_id': 'N1', 'quote': 'Stop losartan due to hyperkalemia'}]}}}, {})
        self.assertTrue(P.unquoted_chart_answer(q, bare[0]))
        self.assertFalse(P.unquoted_chart_answer(q, quoted[0]))
        self.assertFalse(P.unquoted_chart_answer('How many patients have diabetes?', bare[0]))
        c = self.cascade(); c.question = q; c.claude_down = None
        with patch.object(P, 'claude_available', lambda: True), patch.object(P.Cascade, 'runnable', lambda self, n: True), \
                patch.object(P.Cascade, 'attempt', lambda self, n, s: quoted):
            self.assertEqual(c.quoted_or_backstop(['chart_reader', 'backstop_agent'], 'chart_reader', bare), (quoted, 'backstop_agent'))
        with patch.object(P, 'claude_available', lambda: True), patch.object(P.Cascade, 'runnable', lambda self, n: True), \
                patch.object(P.Cascade, 'attempt', lambda self, n, s: None):
            self.assertEqual(c.quoted_or_backstop(['chart_reader', 'backstop_agent'], 'chart_reader', bare), (bare, 'chart_reader'))

    def test_the_backstop_answer_goes_through_the_answer_gate(self):
        run = {'answer': {'status': 'answered', 'answer_type': 'count', 'headline': '97 stays', 'count': 97},
               'tool_calls': [{'sql': 'SELECT 97'}], 'model': 'm', 'new_paid_api_calls': 1, 'estimated_cost_usd': 0.0}
        with patch.object(P, 'claude_available', lambda: True), patch.object(B, 'answer', return_value=run), \
                patch.object(P, 'answer_gate', lambda c, n, g, r, res: res):
            contract, _ = self.cascade().tier_backstop_agent()
        self.assertEqual(contract['executed']['answer']['verification'], 'checked')
        c = self.cascade()
        def reject(cascade, n, g, r, res):
            cascade.log.append({'proposer': 'backstop_agent', 'outcome': 'rejected', 'detail': 'zero-result probe'}); return None
        with patch.object(P, 'claude_available', lambda: True), patch.object(B, 'answer', return_value=run), patch.object(P, 'answer_gate', reject):
            contract, _ = c.tier_backstop_agent()  # one repair attempt, then shipped unverified, never as checked
        a = contract['executed']['answer']
        self.assertEqual((a['verification'], a['status']), ('unverified', 'partial'))
        self.assertTrue(any('zero-result probe' in l for l in a['limitations']))

    def test_one_patient_questions_reach_the_investigator_after_the_fast_tiers(self):
        got = P.investigator_first_for_one_patient(('exact_local_plan', 'chart_reader', 'paraphrase_reuse', 'evidence_retrieval', 'evidence_review',
                                                    'spec_path', 'backstop_agent', 'semantic_analyst'))
        self.assertEqual(got, ('exact_local_plan', 'chart_reader', 'paraphrase_reuse', 'backstop_agent', 'evidence_retrieval', 'evidence_review',
                               'spec_path', 'semantic_analyst'))

    def test_off_switch(self):
        with patch.dict(os.environ, {'AGERE_BACKSTOP': 'off'}):
            self.assertIsNone(self.cascade().tier_backstop_agent())


if __name__ == '__main__':
    unittest.main()
