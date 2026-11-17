"""The clarifying conversation (clarify.py) with mocked models: asks only what the definitions and defaults leave open,
remembers the exchange, escalates from the small model only when needed, stops after three exchanges, hands one explicit
question to the stack and never computes, and saves the person's choices as defaults."""
import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from clinical_core.agere.clinical import clarification
from clinical_core.agere.tiered import app, clarify, customer, methods, models, router, store

READINGS = [{'id': 'reading1', 'label': 'Admissions', 'question': 'How many inpatient admissions were there at our hospitals in 2029?'},
            {'id': 'reading2', 'label': 'ED visits', 'question': 'How many emergency department visits were there at our hospitals in 2029?'}]
QUESTION = 'How many visits did we have in 2029?'


def ask(text, *labels, **extra):
    return {'status': 'ask', 'ask': text, 'options': [{'label': l, 'meaning': l} for l in labels], 'readings': READINGS, 'choices': [], 'confident': True, **extra}


def ready(question, choices=()):
    return {'status': 'ready', 'question': question, 'readings': [{'label': 'As clarified', 'question': question}], 'choices': list(choices), 'confident': True}


class Model:
    """Scripted replies per model; records every prompt it is shown."""
    def __init__(self, slm=(), llm=()):
        self.replies = {models.SLM: list(slm), models.LLM: list(llm)}
        self.calls = []

    def __call__(self, model, system, user, timeout=180):
        self.calls.append((model, user))
        got = self.replies[model].pop(0)
        if isinstance(got, Exception): raise got
        return (got if isinstance(got, str) else json.dumps(got)), {'model': model, 'tokens': 100, 'usd': 0.001 if model == models.SLM else 0.01, 'seconds': 0.1}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.patches = [mock.patch.object(clarify, 'DEFAULTS', str(Path(self.dir.name) / 'defaults.sqlite3')),
                        mock.patch.object(customer, 'relevant', lambda q: [('Visit', 'A visit is an inpatient admission or an ED visit; certified by finance.')]),
                        mock.patch.object(store, 'own_facilities', lambda: ['WH-HOSPITAL-0', 'WH-HOSPITAL-1']),
                        mock.patch.object(clarify, '_clock', lambda: '2030-05-27')]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.dir.cleanup()

    def model(self, **kw):
        m = Model(**kw)
        p = mock.patch.object(models, '_claude', m); p.start(); self.addCleanup(p.stop)
        return m


class Conversation(Base):
    def test_the_small_model_asks_with_the_customer_definitions_and_own_facilities_in_front_of_it(self):
        m = self.model(slm=[ask('Do you mean admissions or ED visits?', 'Admissions', 'ED visits')])
        turn = clarify.begin(QUESTION, READINGS)
        self.assertEqual(turn['action'], 'ask'); self.assertEqual([o['label'] for o in turn['options']], ['Admissions', 'ED visits'])
        self.assertEqual([c[0] for c in m.calls], [models.SLM])   # no frontier call when the small model's turn is good
        prompt = m.calls[0][1]
        self.assertIn('certified by finance', prompt); self.assertIn('WH-HOSPITAL-0', prompt); self.assertIn(READINGS[0]['question'], prompt)

    def test_a_small_model_turn_that_fails_a_check_or_is_unsure_goes_to_the_frontier_model(self):
        for bad in (ask('Which?', 'Only one'), {**ask('Which?', 'A', 'B'), 'confident': False}, 'not json at all',
                    models.ProviderUnavailable('timeout')):
            m = self.model(slm=[bad], llm=[ask('Do you mean admissions or ED visits?', 'Admissions', 'ED visits')])
            turn = clarify.begin(QUESTION, READINGS)
            self.assertEqual(turn['action'], 'ask'); self.assertEqual([c[0] for c in m.calls], [models.SLM, models.LLM])

    def test_a_provider_limit_stops_it_and_two_failures_fall_back(self):
        self.model(slm=[models.ProviderLimit('weekly limit')])
        with self.assertRaises(models.ProviderLimit): clarify.begin(QUESTION, READINGS)
        self.model(slm=['nope'], llm=['nope'])
        self.assertIsNone(clarify.begin(QUESTION, READINGS))

    def test_it_remembers_the_answers_and_never_asks_the_same_thing_twice(self):
        m = self.model(slm=[ask('Admissions or ED visits?', 'Admissions', 'ED visits'),
                            ask('Admissions or ED visits?', 'Admissions', 'ED visits')],   # asked again: refused by code
                       llm=[ask('By admission date or discharge date?', 'Admission date', 'Discharge date')])
        turn = clarify.begin(QUESTION, READINGS)
        turn = clarify.reply(turn['state'], '', turn['options'][0])
        self.assertEqual(turn['action'], 'ask'); self.assertEqual(turn['ask'], 'By admission date or discharge date?')
        self.assertIn('You asked: Admissions or ED visits? | They replied: Admissions', m.calls[1][1])

    def test_after_three_exchanges_it_stops_asking_and_the_open_readings_are_computed_side_by_side(self):
        asks = [ask(f'Question {i}?', 'A', 'B') for i in range(1, 5)]
        self.model(slm=asks)
        turn = clarify.begin(QUESTION, READINGS)
        for i in range(3):
            self.assertEqual(turn['action'], 'ask')
            turn = clarify.reply(turn['state'], 'not sure')
        self.assertEqual(turn['action'], 'side_by_side'); self.assertEqual([r['question'] for r in turn['readings']], [r['question'] for r in READINGS])
        self.assertEqual(len(turn['state']['exchanges']), 3)

    def test_the_explicit_question_may_not_carry_a_number_nobody_said(self):
        m = self.model(slm=[ready('How many inpatient admissions were there at our hospitals in 2029? About 4120.')],
                       llm=[ready('How many inpatient admissions were there at our hospitals in 2029?')])
        turn = clarify.begin(QUESTION, READINGS)
        self.assertEqual(turn['action'], 'ready'); self.assertNotIn('4120', turn['question'])
        self.assertIn('4120', turn['state']['steps'][0]['reason'])

    def test_the_datas_own_date_anchors_last_year(self):
        m = self.model(slm=[ask('Which?', 'A', 'B')])
        clarify.begin('How many visits last year?', READINGS)
        self.assertIn("'last year' is 2029", m.calls[0][1])

    def test_what_the_person_answered_is_saved_even_when_the_model_lists_no_choices(self):
        self.model(slm=[ask('Which visits?', 'Hospital visits', 'Appointments'), ready('How many hospital visits were there in 2029?')])
        turn = clarify.begin(QUESTION, READINGS)
        clarify.reply(turn['state'], '', turn['options'][0])
        self.assertEqual(clarify.defaults(), [{'about': 'which visits?', 'choice': 'Hospital visits'}])

    def test_a_period_is_never_saved_as_a_default(self):
        clarify.save_defaults([{'about': 'Which year?', 'choice': '2025'}, {'about': 'which visits', 'choice': 'hospital visits'},
                               {'about': 'which stay', 'choice': 'The readmission must start in 2025'}])
        self.assertEqual(clarify.defaults(), [{'about': 'which visits', 'choice': 'hospital visits'}])

    def test_choices_are_saved_as_defaults_and_settle_the_next_question_without_asking(self):
        self.model(slm=[ready('How many inpatient admissions were there at our hospitals in 2029?', [{'about': 'Which visits', 'choice': 'inpatient admissions'}])])
        clarify.begin(QUESTION, READINGS)
        self.assertEqual(clarify.defaults(), [{'about': 'which visits', 'choice': 'inpatient admissions'}])
        m = self.model(slm=[ready('How many inpatient admissions were there at our hospitals in 2028?')])
        turn = clarify.begin('How many visits did we have in 2028?', READINGS)
        self.assertEqual(turn['action'], 'ready'); self.assertIn('which visits: inpatient admissions', m.calls[0][1])
        self.assertEqual(clarify.defaults(person='someone else'), [])


class Stack:
    def __init__(self, path):
        self.path = Path(path)
        self.retriever = types.SimpleNamespace(meta={'snapshot': {'as_of': '2030-05-27'}})


def answered(question, n):
    return {'answer': {'status': 'answered', 'answer_type': 'count', 'headline': f'{n}.', 'count': n, 'facts': {'answered_by': 'a recipe'}, 'limitations': []},
            'results': [], 'workflow': [], 'question': question, 'route': 'tiered_core', 'new_paid_api_calls': 0,
            'graph': {'interpretation': question, 'coverage': 'supported_observation', 'unmet_requirements': [], 'nodes': [{'id': 'tiered_0', 'operation': 'sql'}]},
            'planner_receipt': {'route': 'tiered_core', 'final_tier': 'recipes', 'known_api_estimate_usd': 0.0, 'new_paid_api_calls': 0, 'frontier_called': False},
            'planning_economics': {'known_api_estimate_usd': 0.0}, 'tiered_core': {'final_tier': 'recipes', 'seconds': 0.1, 'escalations': []}}


class InTheApp(Base):
    def setUp(self):
        super().setUp()
        self.stack = Stack(self.dir.name)
        unclear = {'status': 'needs_clarification', 'final_tier': 'clarify', 'steps': [], 'escalations': [], 'seconds': 0.1, 'ask': 'Which?', 'rule': 'model',
                   'readings': [{**r, 'meaning': r['question']} for r in READINGS]}
        self.asked = []
        def try_answer(stack, q):
            self.asked.append((q, models.NO_READINGS.get(), clarification.MODE.get()))
            return answered(q, 4120 if 'admission' in q else 9001)
        for p in [mock.patch.dict(os.environ, {'AGERE_TIERED_CORE': 'on', 'AGERE_TIERED_LEDGER': str(Path(self.dir.name) / 'l.sqlite3'),
                                                'AGERE_TIERED_BOTTLES': str(Path(self.dir.name) / 'b.sqlite3')}),
                  mock.patch.object(methods, 'claims', lambda *a, **k: False), mock.patch.object(methods, 'needs', lambda *a, **k: []),
                  mock.patch.object(methods, 'worth_asking', lambda *a, **k: False), mock.patch.object(router, 'answer', lambda *a, **k: dict(unclear)),
                  mock.patch.object(app, 'try_answer', try_answer)]:
            p.start(); self.addCleanup(p.stop)
        app.DEADLINE.set(None)

    def test_a_back_and_forth_then_one_explicit_question_runs_through_the_stack(self):
        self.model(slm=[ask('Admissions or ED visits?', 'Admissions', 'ED visits'),
                        ask('At our four hospitals only, or every facility?', 'Ours only', 'Every facility'),
                        ready('How many inpatient admissions were there at our hospitals in 2029?', [{'about': 'which facilities', 'choice': 'our hospitals only'}])])
        first = app._single(self.stack, QUESTION)
        self.assertEqual(first['answer']['status'], 'clarify'); self.assertEqual(first['answer']['headline'], 'Admissions or ED visits?')
        saved = first['clarification_checkpoint']
        self.assertEqual(saved['tiered']['kind'], 'conversation'); self.assertEqual(self.asked, [])   # nothing computed while asking
        second = app.resume(self.stack, saved, {'option': saved['pause']['request']['options'][0]})
        self.assertEqual(second['answer']['headline'], 'At our four hospitals only, or every facility?')
        third = app.resume(self.stack, second['clarification_checkpoint'], {'text': 'ours only'})
        self.assertEqual(self.asked, [('How many inpatient admissions were there at our hospitals in 2029?', True, 'autonomous')])
        self.assertEqual(third['answer']['count'], 4120)
        talk = third['planner_receipt']['clarifying_conversation']
        self.assertEqual([x['reply'] for x in talk['exchanges']], ['Admissions', 'ours only'])
        self.assertIn('saved as your defaults', third['answer']['facts']['clarified'])
        self.assertAlmostEqual(third['planner_receipt']['known_api_estimate_usd'], 0.003)
        self.assertEqual(clarify.defaults(), [{'about': 'which facilities', 'choice': 'our hospitals only'}])

    def test_out_of_exchanges_each_reading_is_computed_and_shown_side_by_side(self):
        self.model(slm=[ask(f'Question {i}?', 'A', 'B') for i in range(1, 5)])
        got = app._single(self.stack, QUESTION)
        for _ in range(3):
            got = app.resume(self.stack, got['clarification_checkpoint'], {'text': 'either'})
        self.assertEqual(got['answer']['answer_type'], 'composite')
        self.assertEqual([s['answer']['count'] for s in got['answer']['sections']], [4120, 9001])
        self.assertIn('side by side', got['answer']['limitations'][0])

    def test_when_no_tier_and_no_takeover_answers_a_person_is_talked_with_not_left_with_nothing(self):
        from clinical_core.agere.tiered import takeover
        unanswered = {'status': 'unanswered', 'final_tier': None, 'steps': [], 'escalations': [], 'seconds': 0.1, 'recipe': None, 'validation': None, 'missing': 'x'}
        self.model(slm=[ask('Do you mean ED notes or nursing notes?', 'ED notes', 'Nursing notes')])
        with mock.patch.object(router, 'answer', lambda *a, **k: dict(unanswered)), mock.patch.object(takeover, 'answer', lambda *a, **k: None), \
                mock.patch.dict(os.environ, {'AGERE_TIERED_TAKEOVER': 'on'}):
            got = app._single(self.stack, 'How many notes mention a fall?')
        self.assertEqual(got['answer']['status'], 'clarify'); self.assertEqual(got['clarification_checkpoint']['tiered']['kind'], 'conversation')

    def test_the_frontier_models_own_question_becomes_the_conversation(self):
        from clinical_core.agere.tiered import takeover
        unanswered = {'status': 'unanswered', 'final_tier': None, 'steps': [], 'escalations': [], 'seconds': 0.1, 'recipe': None, 'validation': None, 'missing': 'x'}
        asked = {'ask': 'Which?', 'options': ['ED notes', 'Nursing notes'], 'steps': [], 'seconds': 1.0, 'usd': 0.0, 'tokens': 0, 'queries': [], 'model': 'm'}
        m = self.model(slm=[ask('ED notes or nursing notes?', 'ED notes', 'Nursing notes')])
        with mock.patch.object(router, 'answer', lambda *a, **k: dict(unanswered)), mock.patch.object(takeover, 'answer', lambda *a, **k: dict(asked)), \
                mock.patch.dict(os.environ, {'AGERE_TIERED_TAKEOVER': 'on'}):
            got = app._single(self.stack, 'How many notes mention a fall?')
        self.assertEqual(got['clarification_checkpoint']['tiered']['kind'], 'conversation'); self.assertIn('- ED notes: ED notes', m.calls[0][1])

    def test_too_many_documents_to_read_asks_the_person_to_narrow_it(self):
        wide = {'status': 'unanswered', 'final_tier': None, 'steps': [], 'seconds': 0.1, 'recipe': None, 'validation': None, 'missing': 'x',
                'escalations': [{'from': 'llm', 'reason': 'execution error: The plan selects 73529 documents; this tier reads at most 150 per question'}]}
        m = self.model(slm=[ask('Which period and which notes?', 'ED notes in 2029', 'All notes in 2029')])
        with mock.patch.object(router, 'answer', lambda *a, **k: dict(wide)):
            got = app._single(self.stack, 'How many patients had a fall?')
        self.assertEqual(got['clarification_checkpoint']['tiered']['kind'], 'conversation')
        self.assertIn('73529 documents', m.calls[0][1])

    def test_without_a_model_the_one_card_question_is_still_asked(self):
        self.model(slm=['x'], llm=['y'])
        got = app._single(self.stack, QUESTION)
        self.assertEqual(got['answer']['status'], 'clarify'); self.assertEqual(got['clarification_checkpoint']['tiered']['kind'], 'reading')
        self.assertEqual(got['answer']['headline'], 'Which?')


if __name__ == '__main__':
    unittest.main()
