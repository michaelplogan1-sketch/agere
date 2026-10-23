"""System One in front of the Claude routers (s1_governed.py): a governed metric and an analytic engine chosen by typed choices.
Synthetic data only; System One is a fake (system_one.decide is patched), no network, no model."""
import os, time, unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault('AGERE_METRIC_ENCODER', 'off')
os.environ['AGERE_LOCAL_TYPED'] = 'off'
os.environ['AGERE_LOCAL_NORMALIZER'] = 'off'

from clinical_core.agere.clinical import s1_governed as S1G, metric_router as MR, operator_router as OR, proposers, economical_planner as planner
from clinical_core.agere.clinical.analysis_graph import validate

AS_OF = '2029-08-15'


class FakeS1:
    """Stands in for system_one.decide: `script(task_key, prompt, options)` -> (label, probability). Records every call."""

    def __init__(self, script, usd=0.0002):
        self.script, self.usd, self.calls = script, usd, []

    def decide(self, question, options, evidence, *, as_of, order=None, threshold=0.8, task_key=None, ledger=None, allow_live=True):
        label, p = self.script(task_key, question, options)
        self.calls.append({'task': task_key, 'prompt': question, 'options': list(options), 'label': label, 'p': p})
        rest = {k: (1 - p) / max(1, len(options) - 1) for k in options if k != label}
        step = {'provider': 'jev', 'status': 'answered', 'label': label, 'probability': p, 'cost_usd': self.usd, 'cache_hit': False}
        return {'label': label, 'probabilities': {label: p, **rest}, 'calibrated_probabilities': {label: p, **rest}, 'provider': 'jev',
                'cost_usd': self.usd, 'cache_hit': False, 'decided_by': 'jev' if p >= threshold else None, 'steps': [step], 'task_key': task_key}


def patched(fake):
    return mock.patch('clinical_core.agere.clinical.system_one.decide', fake.decide)


def pick(metric=None, slots=None, p=0.9):
    """A script: choose `metric` (a key in the option list) at probability p; slot choices come from `slots` {name: value}."""
    slots = slots or {}

    def script(task, prompt, options):
        if task == S1G.TASKS['metric']:
            return (metric if metric in options else S1G.NONE_OF), p
        if task == S1G.TASKS['slot']:
            name = prompt.split('which value of "', 1)[1].split('"', 1)[0] if 'which value of "' in prompt else 'period'
            return slots.get(name, S1G.NOT_STATED if S1G.NOT_STATED in options else 'all_time'), p
        raise AssertionError(task)
    return script


class Metric(unittest.TestCase):
    def route(self, question, script, **kw):
        fake = FakeS1(script, **kw)
        stack = SimpleNamespace(retriever=SimpleNamespace(encoder=None))
        with patched(fake):
            graph, info, rec = S1G.route_metric(stack, question, AS_OF, None)
        return graph, info, rec, fake

    def test_a_governed_metric_question_routes_to_the_metric_with_its_period(self):
        graph, info, rec, fake = self.route('How many ED visits were there in 2028?', pick('ed_visits', {'period': 'year'}))
        self.assertEqual(info['decision'], 'metric', info)
        self.assertEqual(info['metric'], 'ed_visits')
        self.assertEqual(info['resolution']['slots']['period'], {'preset': 'year', 'year': 2028})
        self.assertEqual(graph['nodes'][0]['operation'], 'sql')
        self.assertEqual(info['chosen_by'], 'system_one')
        self.assertEqual([c['step'] for c in info['system_one']['calls']], ['metric_choice', 'metric_slot:period'])
        self.assertIn(S1G.NONE_OF, info['system_one']['calls'][0]['options'])
        self.assertLessEqual(len(info['system_one']['calls'][0]['options']), 11)
        self.assertEqual(info['system_one']['new_paid_api_calls'], 2)
        self.assertAlmostEqual(info['system_one']['cost_usd'], 0.0004)
        self.assertAlmostEqual(MR.cost(info), 0.0004)

    def test_no_date_in_the_question_is_all_time_without_a_slot_call(self):
        graph, info, rec, fake = self.route('How many ED visits did we have?', pick('ed_visits'))
        self.assertEqual(info['decision'], 'metric', info)
        self.assertEqual(info['resolution']['slots']['period'], {'preset': 'all_time'})
        self.assertEqual([c['task'] for c in fake.calls], [S1G.TASKS['metric']])

    def test_a_question_no_metric_fits_returns_none(self):
        graph, info, rec, fake = self.route('How many ED visits were there in 2028?', pick(None))
        self.assertIsNone(graph)
        self.assertEqual(info['decision'], MR.NO_METRIC)
        self.assertEqual(len(fake.calls), 1)  # no slot calls after none_of_these
        self.assertEqual(rec.paid_calls, 1)    # the one call is still charged

    def test_low_probability_falls_through(self):
        graph, info, rec, fake = self.route('How many ED visits were there in 2028?', pick('ed_visits', {'period': 'year'}, p=0.45))
        self.assertIsNone(graph)
        self.assertEqual(info['decision'], MR.NO_METRIC)
        self.assertIn('bar 0.60', info['reason'])

    def test_the_acceptance_bar_is_an_env_knob(self):
        with mock.patch.dict(os.environ, {'AGERE_S1_METRIC_ACCEPT': '0.4'}):
            graph, info, rec, fake = self.route('How many ED visits were there in 2028?', pick('ed_visits', {'period': 'year'}, p=0.45))
        self.assertEqual(info['decision'], 'metric', info)

    def test_unsure_slot_declines_instead_of_guessing(self):
        def script(task, prompt, options):
            return ('ed_visits', 0.9) if task == S1G.TASKS['metric'] else ('year', 0.4)
        graph, info, rec, fake = self.route('How many ED visits were there in 2028?', script)
        self.assertIsNone(graph)
        self.assertIn('unsure about the time window', info['reason'])

    def test_a_window_that_cannot_be_read_is_not_guessed(self):
        graph, info, rec, fake = self.route('How many ED visits were there in 2027 or 2028?', pick('ed_visits', {'period': 'year'}))
        self.assertIsNone(graph)
        self.assertIn('names 2 years', info['reason'])

    def test_exact_fit_still_gates_the_pick(self):
        # the metric is chosen confidently, but the question restricts to women and the metric neither defines nor takes sex
        graph, info, rec, fake = self.route('How many ED visits did women have in 2028?', pick('ed_visits', {'period': 'year'}))
        self.assertIsNone(graph)
        self.assertIn('exact-fit gate', ' '.join(d['reason'] for d in info.get('declined', [])))

    def test_group_by_is_asked_only_when_the_question_breaks_down(self):
        asked = []

        def script(task, prompt, options):
            if task == S1G.TASKS['metric']: return 'measure_cbp', 0.9
            asked.append(prompt.split('which value of "', 1)[1].split('"', 1)[0] if 'which value of "' in prompt else 'period')
            return {'group_by': 'sex'}.get(asked[-1], 'all_time'), 0.9
        graph, info, rec, fake = self.route('What is our controlling blood pressure rate by sex?', script)
        self.assertEqual(info['decision'], 'metric', info)
        self.assertEqual(info['resolution']['slots']['group_by'], 'sex')
        asked.clear()
        graph, info, rec, fake = self.route('What is our controlling blood pressure rate?', script)
        self.assertEqual(asked, [])

    def test_metrics_with_population_atoms_stay_with_the_claude_router(self):
        lib = MR.routable()
        self.assertFalse(S1G.fillable(lib['patient_cohort']))
        self.assertNotIn('patient_cohort', S1G.metric_shortlist('How many diabetic patients do we have?', lib, 12))

    def test_a_validator_complaint_is_not_retried_with_system_one(self):
        rec = S1G.Recorder(None, AS_OF)
        ask = S1G.metric_ask('q', rec, MR.routable())
        choice, receipt = ask('q', AS_OF, feedback='slot period must be an object')
        self.assertEqual(choice['name'], MR.NO_METRIC)

    def test_period_parsing(self):
        self.assertEqual(S1G.period_value('in the last 6 months', 'trailing_months'), {'preset': 'trailing_months', 'months': 6})
        self.assertEqual(S1G.period_value('over the past two years', 'trailing_months'), {'preset': 'trailing_months', 'months': 24})
        self.assertEqual(S1G.period_value('in the past year', 'trailing_months'), {'preset': 'trailing_months', 'months': 12})
        self.assertEqual(S1G.period_value('in the last 2 weeks', 'trailing_days'), {'preset': 'trailing_days', 'days': 14})
        self.assertEqual(S1G.period_value('so far this year', 'ytd'), {'preset': 'ytd'})
        for q, preset in (('in the last few months', 'trailing_months'), ('last year', 'trailing_months'), ('in March', 'other_window')):
            with self.assertRaises(S1G.Unfillable): S1G.period_value(q, preset)


class Engine(unittest.TestCase):
    def route(self, question, script, **kw):
        fake = FakeS1(script, **kw)
        stack = SimpleNamespace(retriever=SimpleNamespace(encoder=None))
        with patched(fake):
            graph, info, rec = S1G.route_engine(stack, question, AS_OF, None, validate)
        return graph, info, rec, fake

    @staticmethod
    def engine(op, p=0.9):
        return lambda task, prompt, options: (op if op in options else S1G.NONE_OF, p) if task == S1G.TASKS['engine'] else (S1G.NOT_STATED, p)

    def test_an_engine_question_picks_the_engine(self):
        graph, info, rec, fake = self.route('Give me the daily briefing for today', self.engine('briefing'))
        self.assertEqual(info['decision'], 'briefing', info)
        self.assertEqual(info['chosen_by'], 'system_one')
        self.assertEqual(graph['nodes'][-1]['operation'], 'briefing')
        calls = info['system_one']['calls']
        self.assertEqual(calls[0]['step'], 'engine_choice')
        self.assertLessEqual(len(calls[0]['options']), 8 + 2)
        self.assertIn('literal_lookup', calls[0]['options'])
        self.assertIn(S1G.NONE_OF, calls[0]['options'])

    def test_numbers_are_parsed_into_the_one_parameter_that_holds_them(self):
        graph, info, rec, fake = self.route('Give me the briefing, top 7 items', self.engine('briefing'))
        self.assertEqual(graph['nodes'][-1]['parameters']['top'], 7)

    def test_a_number_nothing_can_hold_declines(self):
        graph, info, rec, fake = self.route('Give me the briefing for the 45 patients', self.engine('briefing'))
        self.assertIsNone(graph)
        self.assertIn('fit no single parameter', ' '.join(info['system_one']['notes']))

    def test_low_probability_falls_through(self):
        graph, info, rec, fake = self.route('Give me the daily briefing for today', self.engine('briefing', p=0.5))
        self.assertIsNone(graph)
        self.assertEqual(rec.paid_calls, 1)

    def test_literal_lookup_is_not_an_engine(self):
        graph, info, rec, fake = self.route('How many patients are on a statin?', self.engine('literal_lookup'))
        self.assertIsNone(graph)
        self.assertEqual(info['decision'], 'literal_lookup')

    def test_closed_set_parameters_are_asked_only_when_the_question_names_them(self):
        def script(task, prompt, options):
            if task == S1G.TASKS['engine']: return 'saved_monitors', 0.9
            return 'list', 0.9
        graph, info, rec, fake = self.route('Please list my saved monitors', script)
        self.assertEqual([c['task'] for c in fake.calls][0], S1G.TASKS['engine'])
        for call in fake.calls[1:]:
            self.assertEqual(call['task'], S1G.TASKS['param'])
            self.assertLessEqual(len(fake.calls), 4)

    def test_shortlist_is_small_and_never_empty_of_the_named_engine(self):
        out = S1G.engine_shortlist('Run a Kaplan-Meier survival analysis of time to readmission', 8)
        self.assertLessEqual(len(out), 8)
        self.assertIn('survival', out)


class Cascade(unittest.TestCase):
    def test_tiers_sit_before_every_claude_tier_and_are_not_claude(self):
        from clinical_core.agere.clinical.system_one_gate import CLAUDE_TIERS
        order = proposers.ORDER
        for name in ('s1_metric_router', 's1_operator_router'):
            self.assertIn(name, proposers.PAID)
            self.assertNotIn(name, CLAUDE_TIERS)
            for claude in CLAUDE_TIERS: self.assertLess(order.index(name), order.index(claude), (name, claude))
            self.assertIn(name, proposers.TIERS)
        self.assertLess(order.index('s1_metric_router'), order.index('s1_operator_router'))
        self.assertLess(order.index('qwen_typed_interpreter'), order.index('s1_metric_router'))

    def test_only_live_stacks_run_them(self):
        stack = SimpleNamespace(allow_live=False)
        self.assertFalse(proposers.enabled('s1_metric_router', stack))
        self.assertTrue(proposers.enabled('s1_metric_router', SimpleNamespace(allow_live=True)))
        self.assertFalse(S1G.ready(stack))

    def test_a_declined_tier_still_charges_its_calls(self):
        rec = S1G.Recorder(None, AS_OF)
        rec.paid_calls, rec.usd = 2, 0.0004
        cascade = proposers.Cascade.__new__(proposers.Cascade)
        cascade.calls, cascade.usd, cascade.cost_known = 0, 0.0, True
        cascade._ladder = SimpleNamespace(s1_rec=rec)
        self.assertIsNone(cascade.s1_tier('s1_metric_router', lambda: None))
        self.assertEqual((cascade.calls, round(cascade.usd, 6)), (2, 0.0004))
        cascade.s1_tier('s1_metric_router', lambda: None)  # a second look charges nothing twice
        self.assertEqual(cascade.calls, 2)

    def ladder(self):
        ladder = planner.Ladder.__new__(planner.Ladder)
        ladder.stack = SimpleNamespace(allow_live=True, path='.', retriever=SimpleNamespace(encoder=None), receipts=None)
        ladder.question, ladder.ledger, ladder.frontier = 'How many ED visits were there in 2028?', object(), None
        ladder.start, ladder.as_of, ladder.decline, ladder.metric_steps, ladder.s1_rec = time.perf_counter(), AS_OF, {}, [], None
        ladder._profile, ladder._compiled = object(), None
        return ladder

    def test_the_ladder_receipt_carries_the_options_probabilities_provider_and_cost(self):
        fake = FakeS1(pick('ed_visits', {'period': 'year'}))
        ladder = self.ladder()
        with patched(fake), mock.patch.dict(os.environ, {'AGERE_S1_GOVERNED': 'on'}), mock.patch.object(S1G, 'ready', return_value=True):
            graph, receipt = ladder.s1_metric_router()
        self.assertEqual(receipt['route'], 'metric_router')  # downstream gates, certificates and computed-as key on the route
        self.assertEqual(receipt['chosen_by'], 'system_one')
        self.assertEqual(receipt['new_paid_api_calls'], 2)
        self.assertEqual(receipt['system_one']['calls'][0]['provider'], 'jev')
        self.assertTrue(receipt['system_one']['calls'][0]['probabilities'])
        econ = planner.economics(receipt)
        self.assertAlmostEqual(econ['known_api_estimate_usd'], 0.0004)
        self.assertFalse(econ['failed_or_unpriced_api_cost'])
        self.assertEqual(receipt['metric_router']['resolution']['metric'], 'ed_visits')

    def test_the_ladder_engine_receipt_keeps_the_engine_choice_where_gate_policy_reads_it(self):
        fake = FakeS1(Engine.engine('briefing'))
        ladder = self.ladder()
        ladder.question = 'Give me the daily briefing for today'
        with patched(fake), mock.patch.object(S1G, 'ready', return_value=True):
            graph, receipt = ladder.s1_operator_router()
        self.assertEqual(receipt['route'], 'operator_router')
        self.assertEqual(receipt['local_compiler_declined']['operator_router']['chosen_by'], 'system_one')
        self.assertEqual(receipt['operator'], 'briefing')

    def test_a_decline_records_why_and_returns_none(self):
        ladder = self.ladder()
        with patched(FakeS1(pick(None))), mock.patch.object(S1G, 'ready', return_value=True):
            self.assertIsNone(ladder.s1_metric_router())
        self.assertEqual(ladder.decline['s1_metric_router']['decision'], MR.NO_METRIC)
        self.assertEqual(ladder.s1_rec.paid_calls, 1)

    def test_engine_lean_swaps_the_system_one_pair_too(self):
        from clinical_core.agere.clinical import legacy_proposers
        order = proposers.ORDER
        with mock.patch('clinical_core.agere.clinical.economical_planner._engine_lean', return_value={'lean': True}):
            out, _ = legacy_proposers.router_order(None, 'q', order)
        self.assertLess(out.index('operator_router'), out.index('metric_router'))
        self.assertLess(out.index('s1_operator_router'), out.index('s1_metric_router'))


if __name__ == '__main__':
    unittest.main()
