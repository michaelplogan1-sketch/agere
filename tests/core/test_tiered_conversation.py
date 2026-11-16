"""Model-free checks for the back-and-forth: the tiered core asks the user a question, takes the reply and suggests next steps."""
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from clinical_core.agere.clinical import clarification, local_compiler, source_profile
from clinical_core.agere.tiered import app, methods, models, recipes, router, takeover
from tests.core.needs_data import needs_warehouse

STEP = {'model': 'm', 'tokens': 10, 'usd': 0.01, 'seconds': 0.1}
UNANSWERED = {'status': 'unanswered', 'final_tier': None, 'steps': [], 'escalations': [], 'seconds': 0.1, 'recipe': None, 'validation': None, 'missing': 'no tier'}
ANSWER = {'text': 'There are 3.', 'columns': ['n'], 'rows': [[3]], 'sql': 'SELECT 3 AS n', 'queries': ['SELECT 3 AS n'], 'documents': [], 'caveats': [], 'unsupported': [],
          'steps': [dict(STEP)], 'usd': 0.01, 'tokens': 10, 'seconds': 1.0, 'model': 'm', 'next': ['How many were there in 2028?']}
WANTED = ['program track: MSSP BASIC level A-E or MSSP ENHANCED', 'benchmark_pmpm (contract-specific)']


class Stack:
    def __init__(self, path):
        self.path, self.ran = Path(path), []
        self.retriever = types.SimpleNamespace(meta={'snapshot': {'as_of': '2030-05-27'}})

    def run_graph(self, plan, *, question=None):
        self.ran.append(question)
        return {'answer': {'status': 'answered', 'answer_type': 'table', 'headline': 'Contract result.', 'table': [{'a': 1}], 'limitations': []}, 'results': [], 'graph': plan,
                'new_paid_api_calls': 0}


@needs_warehouse
class Conversation(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.stack = Stack(self.dir.name)
        def compiled(q, profile, as_of):
            if 'MSSP ENHANCED' in q: return {'coverage': 'supported_observation', 'unmet_requirements': [], 'nodes': [{'id': 'a', 'operation': 'vbc', 'inputs': [], 'parameters': {}}]}, {'route': 'local_compiler'}
            if 'Contract performance' in q: return {'coverage': 'unsupported', 'unmet_requirements': WANTED, 'nodes': []}, {'route': 'local_requires_input', 'requires_input': WANTED}
            return None, {'route': 'fallback'}
        menu = {'ops': {'monitor': 'control charts of rates', 'demand': 'volume forecasts', 'vbc': 'value-based care measures'}, 'uses': {}, 'text': 'menu',
                'examples': {'monitor': ['Detect anomalies in monthly ED visits'], 'demand': ['Forecast ED visits for the next 4 weeks'], 'vbc': ['What is the risk score of our population?']}}
        self.patches = [mock.patch.dict(os.environ, {'AGERE_TIERED_CORE': 'on', 'AGERE_TIERED_METHODS': 'on', 'AGERE_TIERED_TAKEOVER': 'on', 'AGERE_TIERED_METHODS_ASSIST': 'off',
                                                     'AGERE_TIERED_LEDGER': str(Path(self.dir.name) / 'ledger.sqlite3'), 'AGERE_TIERED_BOTTLES': str(Path(self.dir.name) / 'b.sqlite3')}),
                        mock.patch.object(local_compiler, 'compile', compiled), mock.patch.object(source_profile, 'load', lambda path: {}),
                        mock.patch.object(recipes, 'match', lambda q, db=None: None), mock.patch.object(methods, '_menu', lambda s: menu),
                        mock.patch.object(methods, 'fits', lambda *a, **k: (True, {'seconds': 0.0, 'usd': 0.0, 'model': None, 'label': None})),
                        mock.patch.object(router, 'answer', lambda *a, **k: dict(UNANSWERED))]
        for p in self.patches: p.start()
        methods._PLANS.clear(); app.DEADLINE.set(None)

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.dir.cleanup(); methods._PLANS.clear()

    def test_a_missing_input_becomes_a_question_with_choices_and_nothing_is_computed(self):
        got = app._single(self.stack, 'Contract performance for MSSP')
        self.assertEqual(got['answer']['status'], 'clarify'); self.assertEqual(got['tiered_core']['final_tier'], 'clarify')
        request = got['clarification_checkpoint']['pause']['request']
        self.assertIn('benchmark_pmpm', request['question']); self.assertEqual([o['id'] for o in request['options']], ['without', 'explain'])
        self.assertEqual(got['clarification_checkpoint']['kind'], 'tiered'); self.assertTrue(clarification.pending(got)); self.assertEqual(self.stack.ran, [])
        clarification.validate(request)

    def test_outside_an_interactive_request_it_does_not_ask(self):
        with clarification.interaction('autonomous'):
            got = app._single(self.stack, 'Contract performance for MSSP')
        self.assertEqual(got['answer']['status'], 'unsupported'); self.assertNotIn('clarification_checkpoint', got)
        with mock.patch.dict(os.environ, {'AGERE_TIERED_CLARIFY': 'off'}):
            self.assertNotIn('clarification_checkpoint', app._single(self.stack, 'Contract performance for MSSP'))

    def test_typed_details_are_joined_to_the_question_and_run_through_the_ladder(self):
        saved = app._single(self.stack, 'Contract performance for MSSP')['clarification_checkpoint']
        got = clarification.resume_investigator(self.stack, saved, {'text': 'MSSP ENHANCED, benchmark 950 PMPM'})
        self.assertEqual(got['tiered_core']['final_tier'], 'methods'); self.assertEqual(self.stack.ran, ['Contract performance for MSSP, MSSP ENHANCED, benchmark 950 PMPM'])
        self.assertIn('MSSP ENHANCED', got['answer']['facts']['your_reply']); self.assertNotIn('clarification_checkpoint', got)

    def test_answer_without_it_goes_to_the_frontier_model_with_the_missing_inputs_named_and_no_second_question(self):
        saved = app._single(self.stack, 'Contract performance for MSSP')['clarification_checkpoint']
        seen = {}
        def answer(question, cohort=None, why=None, may_ask=False, **kw): seen.update(why=why, may_ask=may_ask, question=question); return dict(ANSWER)
        with mock.patch.object(takeover, 'answer', answer):
            got = app.resume(self.stack, saved, {'option': {'id': 'without', 'label': 'Answer without it', 'meaning': 'Give the best answer'}})
        self.assertEqual(got['tiered_core']['final_tier'], 'takeover'); self.assertFalse(seen['may_ask']); self.assertEqual(seen['question'], 'Contract performance for MSSP')
        self.assertIn('benchmark_pmpm', seen['why']); self.assertIn('Do not invent', seen['why'])

    def test_the_frontier_model_may_ask_once_and_the_reply_is_answered_on_that_reading(self):
        calls = []
        def answer(question, cohort=None, why=None, may_ask=False, **kw):
            calls.append((may_ask, why))
            return {'ask': 'Which visits do you mean?', 'options': ['Emergency department visits', 'All outpatient visits'], 'queries': [], 'steps': [dict(STEP)],
                    'usd': 0.01, 'tokens': 10, 'seconds': 2.0, 'model': 'm'} if may_ask else dict(ANSWER)
        with mock.patch.object(takeover, 'answer', answer):
            asked = app._single(self.stack, 'How many visits were avoidable?')
            self.assertEqual(asked['answer']['status'], 'clarify'); self.assertEqual(asked['answer']['headline'], 'Which visits do you mean?')
            options = asked['clarification_checkpoint']['pause']['request']['options']
            self.assertEqual([o['meaning'] for o in options], ['Emergency department visits', 'All outpatient visits'])
            got = app.resume(self.stack, asked['clarification_checkpoint'], {'option': options[0]})
        self.assertEqual(got['tiered_core']['final_tier'], 'takeover'); self.assertEqual([c[0] for c in calls], [True, False])
        self.assertIn('Emergency department visits', calls[1][1]); self.assertEqual(got['answer']['next_questions'], ['How many were there in 2028?'])

    def test_when_nothing_answers_the_closest_analyses_are_offered_and_a_pick_runs(self):
        with mock.patch.object(takeover, 'answer', lambda *a, **k: None):
            asked = app._single(self.stack, 'Is anything odd with ED visit volumes?')
        self.assertEqual(asked['answer']['status'], 'clarify')
        labels = [o['label'] for o in asked['clarification_checkpoint']['pause']['request']['options']]
        self.assertIn('Detect anomalies in monthly ED visits', labels)
        seen = []
        with mock.patch.object(app, 'try_answer', lambda stack, q: seen.append(q) or {'answer': {'facts': {}}, 'planner_receipt': {}, 'tiered_core': {}}):
            app.resume(self.stack, asked['clarification_checkpoint'], {'option': {'id': 'monitor', 'label': 'x', 'meaning': 'y'}})
            app.resume(self.stack, asked['clarification_checkpoint'], {'text': 'Were ED visits unusually high in March 2029?'})
        self.assertEqual(seen, ['Detect anomalies in monthly ED visits', 'Were ED visits unusually high in March 2029?'])

    def test_one_time_budget_per_question_is_passed_to_the_last_rung_and_skips_it_when_spent(self):
        import time
        seen = {}
        def answer(question, cohort=None, why=None, may_ask=False, **kw): seen.update(kw); return dict(ANSWER)
        with mock.patch.object(takeover, 'answer', answer), clarification.interaction('autonomous'):
            app.try_answer(self.stack, 'How many visits were avoidable?')
            self.assertTrue(0 < seen['seconds'] <= app.QUESTION_SECONDS)
            seen.clear()
            app.DEADLINE.set(time.monotonic() + 5)  # five seconds of the question's budget left
            self.assertIsNone(app._takeover('How many visits were avoidable?', None, dict(UNANSWERED)))
            self.assertEqual(seen, {})

    def test_a_restatement_system_one_rejects_is_offered_as_a_next_question_not_used(self):
        how = {'tier': 'methods', 'model': 'm', 'method': 'restatement_not_confirmed', 'picked': 'vbc', 'restated': 'What are our shared savings under MSSP ENHANCED?',
               'usd': 0.001, 'tokens': 5, 'seconds': 1.0}
        with mock.patch.dict(os.environ, {'AGERE_TIERED_METHODS_ASSIST': 'on'}), mock.patch.object(methods, 'assisted', lambda *a, **k: (None, dict(how))), \
                mock.patch.object(takeover, 'answer', lambda *a, **k: dict(ANSWER)), clarification.interaction('autonomous'):
            got = app._single(self.stack, 'How is our accountable care contract doing?')
        self.assertEqual(got['tiered_core']['final_tier'], 'takeover'); self.assertEqual(got['answer']['next_questions'][0], 'What are our shared savings under MSSP ENHANCED?')
        self.assertIn('not exactly what you asked', got['answer']['facts']['a_built_in_method_answers_a_nearby_question'])

    def test_a_reply_that_still_cannot_be_answered_says_so(self):
        saved = app._single(self.stack, 'Contract performance for MSSP')['clarification_checkpoint']
        with mock.patch.object(takeover, 'answer', lambda *a, **k: None):
            got = app.resume(self.stack, saved, {'option': {'id': 'without', 'label': 'a', 'meaning': 'b'}})
        self.assertEqual(got['answer']['status'], 'unsupported'); self.assertIn('could not be answered', got['answer']['headline'])

    def test_takeover_protocol_ask_needs_options_and_is_off_unless_allowed(self):
        from clinical_core.agere.tiered import store
        import sqlite3
        db = sqlite3.connect(Path(self.dir.name) / 's.sqlite3')
        db.executescript("CREATE TABLE meta(key TEXT, value TEXT); INSERT INTO meta VALUES('as_of','2030-05-27'),('version','t'),('source_digest','d'); CREATE TABLE patient(patient_id TEXT); INSERT INTO patient VALUES('P-1');")
        db.commit(); db.close()
        ask = '{"ask": "Which year?", "options": ["2028", "2029"]}'
        with mock.patch.dict(os.environ, {'AGERE_TIERED_DB': str(Path(self.dir.name) / 's.sqlite3')}), mock.patch.object(store, 'TABLES', ('patient',)), \
                mock.patch.object(models, 'definitions', lambda question=None: ''):
            store.clear_caches()
            def run(replies, **kw):
                it = iter(replies)
                with mock.patch.object(models, '_claude', lambda m, s, u: (next(it), dict(STEP))): return takeover.answer('How many patients?', **kw)
            self.assertEqual(run([ask], may_ask=True)['options'], ['2028', '2029'])
            got = run([ask, '{"sql": "SELECT COUNT(*) AS n FROM patient"}', '{"answer": "There is 1 patient.", "table": 1, "next": ["How many patients were added in 2029?"]}'])
            self.assertEqual(got['rows'], [[1]]); self.assertEqual(got['next'], ['How many patients were added in 2029?'])
            one = run(['{"ask": "Which?", "options": ["only one"]}', '{"sql": "SELECT COUNT(*) AS n FROM patient"}', '{"answer": "There is 1 patient.", "table": 1}'], may_ask=True)
            self.assertEqual(one['rows'], [[1]])
            store.clear_caches()


if __name__ == '__main__':
    unittest.main()
