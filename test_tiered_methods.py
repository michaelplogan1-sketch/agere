"""Model-free checks for the methods rung: the wider application's analysis methods inside the tiered core."""
import os
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from clinical_core.agere.clinical import local_compiler, source_profile
from clinical_core.agere.tiered import app, ledger as ledger_store, methods, models


def graph(*ops):
    return {'interpretation': 'q', 'coverage': 'supported_observation', 'unmet_requirements': [],
            'nodes': [{'id': f'n{i}', 'operation': op, 'inputs': [], 'parameters': {}} for i, op in enumerate(ops)]}


class Stack:
    def __init__(self, path, result=None, paid=0, wait=0.0, fail=None):
        self.path, self.result, self.paid, self.wait, self.fail, self.ran = Path(path), result, paid, wait, fail, []
        self.retriever = types.SimpleNamespace(meta={'snapshot': {'as_of': '2030-05-27'}})

    def run_graph(self, plan, *, question=None):
        self.ran.append(question)
        if self.wait: time.sleep(self.wait)
        if self.fail: raise ValueError(self.fail)
        return {'answer': self.result or {'status': 'answered', 'answer_type': 'table', 'headline': 'About 11 discharges.', 'table': [{'day': 1, 'discharges': 2}],
                                          'limitations': ['Bed counts are not received.']},
                'results': [], 'graph': plan, 'new_paid_api_calls': self.paid}


class Methods(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {'AGERE_TIERED_METHODS': 'on', 'AGERE_TIERED_LEDGER': str(Path(self.dir.name) / 'ledger.sqlite3')})
        self.env.start()
        self.compiled = {'Forecast discharges for the next 7 days': graph('discharge_forecast'), 'How many ED visits were there in 2029?': graph('cohort', 'aggregate'),
                         'Time to readmission for heart failure patients': graph('cohort', 'survival'), 'Detect anomalies in monthly ED visits': graph('monitor')}
        self.patches = [mock.patch.object(local_compiler, 'compile', lambda q, profile, as_of: (self.compiled.get(q), {'route': 'local_compiler'})),
                        mock.patch.object(source_profile, 'load', lambda path: {})]
        for p in self.patches: p.start()
        methods._PLANS.clear(); methods._MENU.clear()

    def tearDown(self):
        for p in self.patches: p.stop()
        self.env.stop(); self.dir.cleanup(); methods._PLANS.clear(); methods._MENU.clear()

    def test_compiler_names_a_method_and_counting_plans_are_left_to_the_tiers(self):
        stack = Stack(self.dir.name)
        self.assertEqual(methods.plan(stack, 'Forecast discharges for the next 7 days')[2], ['discharge_forecast'])
        self.assertEqual(methods.plan(stack, 'Time to readmission for heart failure patients')[2], ['survival'])
        self.assertIsNone(methods.plan(stack, 'How many ED visits were there in 2029?'))
        self.assertIsNone(methods.plan(stack, 'Something the compiler does not read'))

    def test_off_switch_and_missing_warehouse(self):
        with mock.patch.dict(os.environ, {'AGERE_TIERED_METHODS': 'off'}):
            self.assertIsNone(methods.plan(Stack(self.dir.name), 'Forecast discharges for the next 7 days'))
        self.assertIsNone(methods.plan(None, 'Forecast discharges for the next 7 days'))

    def test_a_recipe_match_keeps_the_question_in_the_recipe_tiers(self):
        from clinical_core.agere.tiered import recipes
        stack = Stack(self.dir.name)
        with mock.patch.object(recipes, 'match', lambda q, db=None: None):
            self.assertTrue(methods.claims(stack, 'Forecast discharges for the next 7 days'))
        with mock.patch.object(recipes, 'match', lambda q, db=None: ('utilization_count', {})):
            self.assertFalse(methods.claims(stack, 'Forecast discharges for the next 7 days'))

    def test_run_returns_the_method_result(self):
        got = methods.run(Stack(self.dir.name), 'Forecast discharges for the next 7 days')
        self.assertEqual(got['methods'], ['discharge_forecast']); self.assertEqual(got['result']['answer']['status'], 'answered')

    def test_paid_slow_failed_and_unanswered_methods_are_declined(self):
        q = 'Forecast discharges for the next 7 days'
        self.assertIn('paid', methods.run(Stack(self.dir.name, paid=1), q)['declined'])
        self.assertIn('ValueError', methods.run(Stack(self.dir.name, fail='bad parameters'), q)['declined'])
        self.assertIn('unsupported', methods.run(Stack(self.dir.name, result={'status': 'unsupported'}), q)['declined'])
        with mock.patch.object(methods, 'MAX_SECONDS', 0.05):
            self.assertIn('did not finish', methods.run(Stack(self.dir.name, wait=0.5), q)['declined'])

    def test_app_result_is_marked_as_the_methods_rung_and_costs_nothing(self):
        stack = Stack(self.dir.name)
        with mock.patch.object(methods, 'fits', lambda *a, **k: (True, {'seconds': 0.0, 'usd': 0.0, 'model': None, 'label': None})):
            got = app._method(stack, 'Forecast discharges for the next 7 days')
        self.assertEqual(got['tiered_core']['final_tier'], 'methods'); self.assertEqual(got['new_paid_api_calls'], 0)
        self.assertEqual(got['planning_economics']['known_api_estimate_usd'], 0.0)
        self.assertIn('discharge forecast', got['answer']['facts']['answered_by'])
        self.assertIn('Bed counts are not received.', got['answer']['limitations'])
        self.assertNotIn('verification', got['answer'])
        self.assertEqual(ledger_store.Ledger().rows('app_method')[-1]['final_tier'], 'methods')

    def test_system_one_saying_the_method_does_not_fit_declines_it(self):
        stack = Stack(self.dir.name)
        with mock.patch.object(methods, '_system_one', lambda prompt, options, ledger: ('no', 0.9, 0.0001)), \
                mock.patch.object(methods, '_menu', lambda s: {'uses': {}, 'ops': {}, 'text': ''}):
            self.assertIsNone(app._method(stack, 'Forecast discharges for the next 7 days'))
        self.assertEqual(stack.ran, [])

    def test_system_one_outage_does_not_block_a_method_code_named(self):
        with mock.patch.object(methods, '_system_one', lambda prompt, options, ledger: (None, 0.0, 0.0)), \
                mock.patch.object(methods, '_menu', lambda s: {'uses': {}, 'ops': {}, 'text': ''}):
            self.assertIsNotNone(app._method(Stack(self.dir.name), 'Forecast discharges for the next 7 days'))

    def _assist(self, reply, s1=('yes', 0.95, 0.0001)):
        step = {'model': 'm', 'tokens': 10, 'usd': 0.002, 'seconds': 0.1}
        menu = {'ops': {'monitor': 'control charts', 'discharge_forecast': 'forecast'}, 'uses': {}, 'text': 'menu'}
        with mock.patch.object(models, '_claude', lambda m, s, u: (reply, dict(step))), mock.patch.object(methods, '_menu', lambda s: menu), \
                mock.patch.object(methods, '_system_one', lambda prompt, options, ledger: s1):
            return methods.assisted(Stack(self.dir.name), 'Is anything odd going on with ED visits?')

    def test_assisted_restatement_is_used_only_when_code_and_system_one_agree(self):
        ok = '{"method": "monitor", "restated": "Detect anomalies in monthly ED visits"}'
        self.assertEqual(self._assist(ok)[0], 'Detect anomalies in monthly ED visits')
        self.assertEqual(self._assist(ok, s1=('no', 0.9, 0.0))[1]['method'], 'restatement_not_confirmed')
        self.assertEqual(self._assist(ok, s1=(None, 0.4, 0.0))[1]['method'], 'restatement_not_confirmed')
        self.assertEqual(self._assist('{"method": null}')[1]['method'], 'not_a_method_question')
        self.assertEqual(self._assist('not json')[1]['method'], 'small_model_unusable')
        self.assertEqual(self._assist('{"method": "made_up", "restated": "Detect anomalies in monthly ED visits"}')[1]['method'], 'pick_unusable')

    def test_assisted_rejects_added_values_and_a_restatement_the_compiler_reads_differently(self):
        added = self._assist('{"method": "monitor", "restated": "Detect anomalies in monthly ED visits in 2029"}')
        self.assertIsNone(added[0]); self.assertEqual(added[1]['method'], 'restatement_adds_values'); self.assertEqual(added[1]['invented'], ['2029'])
        other = self._assist('{"method": "monitor", "restated": "Forecast discharges for the next 7 days"}')
        self.assertEqual(other[1]['method'], 'restatement_adds_values')  # the 7 is not in the question
        wrong = self._assist('{"method": "discharge_forecast", "restated": "Detect anomalies in monthly ED visits"}')
        self.assertIsNone(wrong[0]); self.assertEqual(wrong[1]['method'], 'compiler_did_not_name_the_method')

    def test_assisted_answer_is_marked_unverified_and_shows_the_restatement(self):
        how = {'tier': 'methods', 'model': 'm', 'method': 'small_model_restated_confirmed_by_system_one', 'usd': 0.003, 'tokens': 10, 'seconds': 1.0}
        got = app._method(Stack(self.dir.name), 'Is anything odd going on with ED visits?', restated='Detect anomalies in monthly ED visits', how=how)
        self.assertEqual(got['answer']['verification'], 'unverified'); self.assertEqual(got['new_paid_api_calls'], 1)
        self.assertEqual(got['planner_receipt']['recipe']['restated'], 'Detect anomalies in monthly ED visits')
        self.assertIn('restated the question as', got['answer']['limitations'][0])

    def test_a_recognised_question_with_a_missing_input_tells_the_user_what_to_add(self):
        from clinical_core.agere.tiered import recipes
        stack = Stack(self.dir.name)
        wanted = ['program track: MSSP BASIC level A-E or MSSP ENHANCED', 'benchmark_pmpm (contract-specific)']
        compiled = lambda q, profile, as_of: ({'coverage': 'unsupported', 'unmet_requirements': wanted, 'nodes': []}, {'route': 'local_requires_input', 'requires_input': wanted})
        with mock.patch.object(local_compiler, 'compile', compiled), mock.patch.object(recipes, 'match', lambda q, db=None: None):
            self.assertEqual(methods.needs(stack, 'Contract performance for MSSP'), wanted)
            self.assertIsNone(methods.plan(stack, 'Contract performance for MSSP'))
            got = app._single(stack, 'Contract performance for MSSP')
        self.assertEqual(got['answer']['status'], 'unsupported'); self.assertEqual(got['tiered_core']['needs_input'], wanted)
        self.assertIn('benchmark_pmpm', got['answer']['headline']); self.assertEqual(got['new_paid_api_calls'], 0); self.assertEqual(stack.ran, [])
        with mock.patch.object(local_compiler, 'compile', compiled), mock.patch.object(recipes, 'match', lambda q, db=None: ('x', {})):
            methods._PLANS.clear(); self.assertIsNone(methods.needs(stack, 'Contract performance for MSSP'))

    def test_wording_gate(self):
        from clinical_core.agere.tiered import recipes
        stack = Stack(self.dir.name)
        with mock.patch.object(recipes, 'match', lambda q, db=None: None):
            self.assertTrue(methods.worth_asking(stack, 'Is anything unusual going on with ED visits lately?'))
            self.assertTrue(methods.worth_asking(stack, 'How many patients have diabetes?'))  # asked only if the cheap tiers leave it unanswered
            with mock.patch.dict(os.environ, {'AGERE_TIERED_METHODS_ASSIST_GATE': 'words'}):
                self.assertFalse(methods.worth_asking(stack, 'How many patients have diabetes?'))
            self.assertFalse(methods.worth_asking(stack, 'What is the risk for patient P-HF07?'))
            self.assertFalse(methods.worth_asking(stack, 'Forecast discharges for the next 7 days'))  # code already names a method


if __name__ == '__main__':
    unittest.main()
