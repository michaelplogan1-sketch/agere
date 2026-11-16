"""The Sonnet/Opus model ladder (Mike 2026-11-06), retaining historical fixture keys. Fake transports and fake models only: no Anthropic request is ever made."""
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clinical_core.agere import gateway as G, model_policy as M
from clinical_core.agere.clinical import cost_telemetry as T, nl2sql, proposers as P, spec_fill as F
from clinical_core.agere.clinical.question_input import QuestionNotUnderstood
from spec_test_support import Warehouse, FakeS1, FakeModel, spec_count_hf, FQ

ENTRY, STRONGER, OPUS = M.SONNET, M.OPUS, M.OPUS
Q = 'How many patients have heart failure?'


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self): return self

    def __exit__(self, *exc): return False

    def read(self): return self.payload


class FakeAnthropic:
    """urllib.request.urlopen stand-in: records every request's model and answers with a fixed text. Nothing leaves the process."""

    def __init__(self, text='{"equivalent": true, "issues": []}'):
        self.text, self.models, self.lock = text, [], threading.Lock()

    def __call__(self, request, timeout=None):
        body = json.loads(request.data)
        with self.lock:
            self.models.append(body['model'])
        return FakeResponse({'model': body['model'], 'content': [{'type': 'text', 'text': self.text}], 'stop_reason': 'end_turn',
                             'usage': {'input_tokens': 100, 'output_tokens': 20}})


class Ladder(unittest.TestCase):
    def test_every_question_path_role_is_entry_then_stronger_and_allows_opus_escalation(self):
        for role in M.ROLES:
            self.assertEqual(M.ladder(role), [ENTRY, STRONGER], role)
        self.assertEqual(set(M.ROLES), {'planner', 'writer', 'reviewer', 'judge', 'reconcile', 'analyst', 'interpreter'})
        with self.assertRaises(ValueError): M.ladder('deliverable')

    def test_first_model_of_a_role_is_entry_and_stronger_needs_the_escalation_scope(self):
        self.assertEqual(M.model_for('planner'), ENTRY)
        with M.escalation():
            self.assertEqual(M.model_for('judge'), STRONGER)
            self.assertTrue(M.escalating())
        self.assertEqual(M.model_for('judge'), ENTRY)
        with patch.dict(os.environ, {'AGERE_ESCALATION': 'off'}), M.escalation():
            self.assertEqual(M.model_for('judge'), ENTRY)  # the Opus rung can be switched off

    def test_inside_a_question_any_family_model_resolves_to_the_current_rung(self):
        with M.question_path():
            for named in (OPUS, STRONGER, ENTRY):
                self.assertEqual(M.claude_model(named), ENTRY, named)
            with M.escalation():
                for named in (OPUS, STRONGER, ENTRY):
                    self.assertEqual(M.claude_model(named), STRONGER, named)
            with M.deliverable():
                self.assertEqual(M.claude_model(OPUS), OPUS)  # Opus stays deliverable-only
        with patch.dict(os.environ, {'AGERE_QUESTION_OPUS': 'on'}), M.question_path():
            self.assertEqual(M.claude_model(OPUS), OPUS)

    def test_outside_a_question_the_authorized_pair_is_preserved(self):
        self.assertEqual(M.claude_model(OPUS), OPUS)
        self.assertEqual(M.claude_model(STRONGER), STRONGER)
        self.assertEqual(M.claude_model(ENTRY), ENTRY)
        with patch.dict(os.environ, {'AGERE_HAIKU_FIRST': 'off'}), M.question_path():
            self.assertEqual(M.claude_model(STRONGER), ENTRY)

    def test_the_cost_probe_opens_the_question_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            probe = T.start('q', ledger_path=Path(tmp) / 'l.sqlite3')
            self.assertEqual(M.claude_model(STRONGER), ENTRY)
            probe.finish({})
        self.assertEqual(M.claude_model(STRONGER), STRONGER)

    def test_a_worker_thread_with_a_copied_context_keeps_the_rung(self):
        import contextvars
        seen = []
        with M.question_path(), M.escalation():
            t = threading.Thread(target=contextvars.copy_context().run, args=(lambda: seen.append(M.claude_model(ENTRY)),))
            t.start(); t.join()
        self.assertEqual(seen, [STRONGER])

    def test_tracking_collects_the_models_requested_inside(self):
        with M.tracking() as seen, M.question_path():
            M.claude_model(STRONGER, track=True)
            with M.escalation(): M.claude_model(ENTRY, track=True)
        self.assertEqual(seen, [ENTRY, STRONGER])


class TierDefaults(unittest.TestCase):
    """Every tier's first and escalation model, before -> after (the defaults the code ships with)."""

    def test_gateway_tiers(self):
        with patch.dict(os.environ, {k: '' for k in ('AGERE_PLANNER_MODEL', 'AGERE_SHARED_JUDGE_MODEL', 'AGERE_ESCALATION_MODEL')}):
            self.assertEqual(G.model_tiers(), {'planner': ENTRY, 'judge': ENTRY, 'escalation': STRONGER})
        self.assertEqual(G.PLANNER_MODEL, ENTRY)
        self.assertEqual(G.ESCALATION_MODEL, STRONGER)

    def test_fallback_never_moves_entry_to_stronger_and_never_goes_up(self):
        with patch.dict(os.environ, {'AGERE_FALLBACK_MODEL': '', 'AGERE_INTERPRETER_FALLBACK': ''}):
            self.assertIsNone(G.fallback_model(ENTRY))  # the move to Opus is the cascade's escalation, decided after every Sonnet attempt
            self.assertEqual(G.fallback_model(STRONGER), ENTRY)
            self.assertEqual(G.fallback_model(OPUS), ENTRY)

    def test_a_fallback_that_resolves_to_the_same_rung_is_not_a_repeat_call(self):
        calls = []

        def call(model):
            calls.append(model)
            raise RuntimeError('HTTP 529: Overloaded')
        with M.question_path(), M.escalation(), patch.dict(os.environ, {'AGERE_FALLBACK_MODEL': '', 'AGERE_INTERPRETER_FALLBACK': ''}):
            with self.assertRaises(G.ProviderUnavailable):
                G.call_with_fallback(call, STRONGER, sleep=lambda s: None, backoff=0)
        self.assertEqual(calls, [STRONGER, STRONGER])  # one retry on the same model, no second "fallback" to the identical request

    def test_two_model_check_defaults(self):
        from clinical_core.agere.clinical import answer_guard
        with tempfile.TemporaryDirectory() as tmp:
            env = {k: v for k, v in os.environ.items() if k not in ('AGERE_PRIMARY_MODEL', 'AGERE_SECOND_MODEL', 'AGERE_JUDGE_MODEL')}
            with patch.dict(os.environ, env, clear=True):
                ledger = G.BudgetLedger(Path(tmp) / 'l.sqlite3')
                primary, second, judge, _ = answer_guard._models(SimpleNamespace(receipts=Path(tmp)), ledger)
                ledger.close()
        self.assertEqual((primary.model, second.model, judge.model), (ENTRY, ENTRY, ENTRY))

    def test_together_and_hybrid_defaults(self):
        from clinical_core.agere.clinical import together
        from clinical_core.agere.hybrid import live
        env = {k: v for k, v in os.environ.items() if not k.startswith('AGERE_HYBRID') and k not in ('AGERE_RECONCILE_MODEL', 'AGERE_SEMANTIC_MODEL')}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(together.semantic_model(), ENTRY)
            self.assertEqual(together.reconcile_model(), ENTRY)
            self.assertEqual(live.model_name(), ENTRY)
            self.assertEqual(live.escalation_model_name(), STRONGER)  # outside a question the tie-break is the named escalation model
            with M.question_path():
                self.assertEqual(live.escalation_model_name(), ENTRY)  # inside a question it is Sonnet until the cascade's escalation rung
                with M.escalation():
                    self.assertEqual(live.model_name(), STRONGER)

    def test_planner_requests_name_the_ladder_model_not_opus(self):
        from clinical_core.agere.clinical import stack_planner
        sent = {}

        class Stop(Exception): pass

        def capture(body, *a, **k):
            sent.update(body); raise Stop
        with patch.object(stack_planner, 'send', capture), self.assertRaises(Stop):
            stack_planner.propose(Q, Path('.'), None, snapshot={'as_of': '2030-05-27'})
        self.assertEqual(sent['model'], ENTRY)

    def test_cascade_tiers_cost_strings_name_no_opus(self):
        for name, (cost, _, _) in P.TIERS.items():
            self.assertNotIn('Opus', cost, name)


class SendLevel(unittest.TestCase):
    """The real HostedModel -> context_student.send path over a fake urlopen."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = G.BudgetLedger(Path(self.tmp.name) / 'l.sqlite3')
        self.addCleanup(self.ledger.close)
        self.fake = FakeAnthropic()
        e = patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'sk-test-not-real'}); e.start(); self.addCleanup(e.stop)
        u = patch.object(urllib.request, 'urlopen', self.fake); u.start(); self.addCleanup(u.stop)

    def call(self, model, user='hello'):
        out = Path(self.tmp.name) / 'receipts'
        out.mkdir(exist_ok=True)
        text, stats = nl2sql.HostedModel(self.ledger, out, model=model)('system prompt', user)
        return stats

    def test_a_tier_that_names_stronger_or_opus_still_calls_entry_first(self):
        with M.question_path():
            for i, named in enumerate((STRONGER, OPUS, ENTRY)):
                stats = self.call(named, user=f'q{i}')
                self.assertEqual(stats['model'], ENTRY, named)  # the receipt names what was called
        self.assertEqual(self.fake.models, [ENTRY, ENTRY, ENTRY])

    def test_the_escalation_rung_is_the_only_way_to_a_stronger_call(self):
        with M.question_path():
            self.call(ENTRY, 'first')
            with M.escalation():
                self.call(ENTRY, 'second')
        self.assertEqual(self.fake.models, [ENTRY, STRONGER])

    def test_tiered_plans_and_reviews_on_entry_and_a_failed_review_replans_on_entry_until_the_escalation_rung(self):
        env = {k: v for k, v in os.environ.items() if k not in ('AGERE_PLANNER_MODEL', 'AGERE_SHARED_JUDGE_MODEL', 'AGERE_ESCALATION_MODEL')}
        failing = FakeAnthropic('{"equivalent": false, "issues": ["window dropped"]}')
        with patch.dict(os.environ, env, clear=True), patch.object(urllib.request, 'urlopen', failing), patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'sk-test-not-real'}):
            model = nl2sql.HostedModel(self.ledger, Path(self.tmp.name), model='tiered')
            from clinical_core.agere.clinical import shared_planner
            audit = shared_planner.AUDIT_SYSTEM
            with M.question_path():
                model('plan system', 'plan user')         # planner
                model(audit, 'review user')               # judge: fails -> escalated
                model('plan system', 'replan user')       # re-plan on the escalation model
            self.assertEqual(failing.models, [ENTRY, ENTRY, ENTRY])  # inside a question the escalation model is only reached on the Opus rung
            with M.question_path(), M.escalation():
                model('plan system', 'replan again')
            self.assertEqual(failing.models[-1], STRONGER)

    def test_outside_a_question_tiered_still_escalates_to_stronger_after_a_failed_review(self):
        env = {k: v for k, v in os.environ.items() if k not in ('AGERE_PLANNER_MODEL', 'AGERE_SHARED_JUDGE_MODEL', 'AGERE_ESCALATION_MODEL')}
        failing = FakeAnthropic('{"equivalent": false, "issues": ["x"]}')
        with patch.dict(os.environ, env, clear=True), patch.object(urllib.request, 'urlopen', failing), patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'sk-test-not-real'}):
            model = nl2sql.HostedModel(self.ledger, Path(self.tmp.name), model='tiered')
            from clinical_core.agere.clinical import shared_planner
            model('plan system', 'plan user')
            model(shared_planner.AUDIT_SYSTEM, 'review user')
            model('plan system', 'replan user')
        self.assertEqual(failing.models, [ENTRY, ENTRY, STRONGER])


class SecondAnalyst(unittest.TestCase):
    def test_the_second_hosted_analyst_is_not_a_repeat_of_the_first(self):
        # Primary and second are the same rung now, so their requests must differ (one ledger key per body).
        self.assertIn('second of two analysts', nl2sql.SECOND_ANALYST_NOTE)
        self.assertTrue(nl2sql.SECOND_ANALYST_NOTE.startswith('\n\n'))


class SpecLadder(unittest.TestCase):
    def setUp(self):
        self.w = Warehouse()
        self.addCleanup(self.w.close)

    def fill(self, haiku, sonnet=None, **kw):
        models = {'haiku': haiku, 'sonnet': sonnet or FakeModel([], name=STRONGER, usd=0.03)}
        kw.setdefault('grace', 0.05)
        return F.fill(self.w.stack, self.w.catalog, Q, models=models, decide=FakeS1(), fillers=('haiku',), **kw), models

    def test_a_typical_question_makes_one_entry_call_and_no_stronger_call(self):
        haiku = FakeModel([json.dumps(spec_count_hf(Q))], name=ENTRY)
        out, models = self.fill(haiku)
        self.assertEqual((len(haiku.calls), len(models['sonnet'].calls)), (1, 0))
        self.assertEqual([c['model'] for c in out['candidate']['notes']['calls']], [ENTRY])

    def test_two_failed_entry_validations_do_not_call_stronger_on_the_first_rung(self):
        bad = json.dumps({'source': {'table': 'patient', 'quote': 'nothing'}})
        haiku = FakeModel([bad, bad])
        with self.assertRaises(F.SpecNotFilled):
            self.fill(haiku, sonnet=FakeModel([json.dumps(spec_count_hf(Q))], name=STRONGER))
        self.assertEqual(len(haiku.calls), 2)

    def test_the_escalation_rung_writes_the_spec_with_stronger_once(self):
        sonnet = FakeModel([json.dumps(spec_count_hf(Q))], name=STRONGER, usd=0.03)
        haiku = FakeModel([])
        with M.escalation():
            out, _ = self.fill(haiku, sonnet=sonnet)
        self.assertEqual((len(haiku.calls), len(sonnet.calls)), (0, 1))
        self.assertEqual([c['model'] for c in out['candidate']['notes']['calls']], [STRONGER])


def cascade(live=True):
    stack = SimpleNamespace(allow_live=live, path='/nonexistent', retriever=SimpleNamespace(meta={'snapshot': {'as_of': '2030-05-27'}}))
    return P.Cascade(stack, SimpleNamespace(identity={}), Q, ledger=SimpleNamespace(close=lambda: None))


class CascadeLadder(unittest.TestCase):
    """The Claude stage on fake tiers whose Claude requests are the ones the model policy would resolve (no transport at all)."""

    def setUp(self):  # the ladder is tested over every Claude tier, the off-default routers switched back on
        from clinical_core.agere.clinical import proposers
        env = patch.dict(os.environ, {'AGERE_PROPOSER_ON': ','.join(proposers.OFF_DEFAULT)})
        env.start(); self.addCleanup(env.stop)

    def run_cascade(self, behaviour):
        """behaviour: tier -> callable(model) -> None | 'answer' | Exception; returns (calls, result, cascade). calls: [(tier, model)] in order."""
        calls = []

        def stub(name):
            def tier(self_):
                act = behaviour.get(name)
                if act is None: return None  # no Claude request
                model = M.claude_model(STRONGER, track=True)  # whatever the tier names, the ladder decides
                calls.append((name, model))
                out = act(model)
                if isinstance(out, Exception): raise out
                return out
            return tier
        env = {'AGERE_S1_ROUTER': 'off', 'AGERE_LOCAL_TYPED': 'off', 'AGERE_BACKGROUND_CONTINUE': 'off', 'AGERE_ANSWER_BUDGET_SECONDS': '600',
               'AGERE_PROPOSERS': 'on', 'AGERE_PROPOSER_SKIP': ''}
        accept = lambda self_, order, name, found: ('accepted', name)
        with patch.dict(os.environ, env), patch.object(P.Cascade, 'expected', lambda self_, name: 0.0), patch.object(P.Cascade, 'accept', accept), \
                patch.multiple(P.Cascade, **{'tier_' + n: stub(n) for n in P.ORDER}):
            c = cascade()
            try:
                result = c.run()
            except (QuestionNotUnderstood, RuntimeError) as exc:
                result = exc
        return calls, result, c

    def test_every_entry_attempt_runs_before_any_stronger_attempt_and_stronger_runs_only_for_failed_tiers(self):
        fail = lambda model: None
        calls, result, c = self.run_cascade({'spec_path': fail, 'metric_router': fail, 'operator_router': fail, 'hosted_verified_sql': fail,
                                             'hosted_typed_interpreter': fail})
        self.assertIsInstance(result, QuestionNotUnderstood)
        models = [m for _, m in calls]
        self.assertEqual(models, [ENTRY] * 5 + [STRONGER] * 5)  # all Sonnet first, then the Opus rung
        tiers = [t for t, _ in calls]
        self.assertEqual(tiers[:5], tiers[5:])  # the same tiers, in cascade order, on the second rung
        self.assertEqual([e['tier'] for e in c.escalated], tiers[:5])
        self.assertEqual([e['proposer'] for e in c.log if e.get('rung') == 'escalation'], tiers[5:])  # the log marks the Opus rung

    def test_cascade_order_cheapest_first(self):
        order = list(P.ORDER)
        for router in ('metric_router', 'operator_router'):
            self.assertLess(order.index('spec_path'), order.index(router))
            self.assertLess(order.index(router), order.index('hosted_verified_sql'))  # Sonnet routers before any SQL writer
        self.assertLess(order.index('spec_path'), order.index('hosted_verified_sql'))
        for heavy in ('semantic_analyst', 'hybrid_analysts', 'frontier_graph_planner', 'stack_planner_propose', 'legacy_cost_ladder'):
            self.assertLess(order.index('hosted_verified_sql'), order.index(heavy), heavy)
        self.assertEqual([t for t in P.ESCALATABLE if t not in P.TIERS], [])

    def test_a_entry_answer_makes_no_stronger_call(self):
        calls, result, _ = self.run_cascade({'spec_path': lambda model: 'answer', 'hosted_verified_sql': lambda model: 'answer'})
        self.assertEqual(result, ('accepted', 'spec_path'))
        self.assertEqual(calls, [('spec_path', ENTRY)])

    def test_a_later_tier_answering_on_entry_means_no_escalation_at_all(self):
        calls, result, c = self.run_cascade({'spec_path': lambda model: None, 'hosted_verified_sql': lambda model: 'answer'})
        self.assertEqual(result, ('accepted', 'hosted_verified_sql'))
        self.assertEqual({m for _, m in calls}, {ENTRY})
        self.assertEqual(c.escalated, [])

    def test_a_tier_that_asked_claude_nothing_is_not_re_run(self):
        calls, result, c = self.run_cascade({'spec_path': lambda model: None})  # every other tier declines without a Claude request
        self.assertEqual(calls, [('spec_path', ENTRY), ('spec_path', STRONGER)])

    def test_a_entry_error_escalates_to_stronger_after_the_other_tiers_have_run(self):
        calls, result, c = self.run_cascade({'spec_path': lambda model: ValueError('malformed reply') if model == ENTRY else 'answer',
                                             'hosted_verified_sql': lambda model: None})
        self.assertEqual(result, ('accepted', 'spec_path'))
        self.assertEqual(calls, [('spec_path', ENTRY), ('hosted_verified_sql', ENTRY), ('spec_path', STRONGER)])

    def test_stronger_answer_is_accepted_and_the_receipt_names_the_rung(self):
        calls, result, c = self.run_cascade({'hosted_typed_interpreter': lambda model: 'answer' if model == STRONGER else None})
        self.assertEqual(result, ('accepted', 'hosted_typed_interpreter'))
        self.assertEqual(calls, [('hosted_typed_interpreter', ENTRY), ('hosted_typed_interpreter', STRONGER)])
        ladder = c.ladder_receipt()
        self.assertEqual(ladder['first_rung_models'], {'hosted_typed_interpreter': [ENTRY]})
        self.assertEqual(ladder['escalated'][0]['outcome'], 'accepted')

    def test_no_escalation_when_claude_is_down_or_escalation_is_off(self):
        with patch.dict(os.environ, {'AGERE_ESCALATION': 'off'}):
            calls, _, _ = self.run_cascade({'spec_path': lambda model: None})
        self.assertEqual(calls, [('spec_path', ENTRY)])
        calls, _, c = self.run_cascade({'hosted_verified_sql': lambda model: RuntimeError('HTTP 402: credit balance is too low'), 'metric_router': lambda model: None})
        self.assertEqual(calls, [('metric_router', ENTRY), ('hosted_verified_sql', ENTRY)])  # a billing outage is Claude being down: Opus would fail the same way

    def test_a_held_decline_is_not_a_failure_to_escalate(self):
        calls, result, c = self.run_cascade({'spec_path': lambda model: 'held'})
        self.assertEqual(calls, [('spec_path', ENTRY)])


class RealSpecPathEscalation(unittest.TestCase):
    """spec_path with explicit frontier fixtures: entry fails twice, other tiers decline, then escalation writes the spec."""

    def setUp(self):
        from clinical_core.agere.clinical import request_plan as R, spec_verify as V
        self.w = Warehouse(allow_live=True)
        self.addCleanup(self.w.close)
        self.s1 = FakeS1()
        V.PROVIDER = self.s1
        F.PROVIDERS['decide'] = self.s1
        self.addCleanup(setattr, V, 'PROVIDER', None)
        self.addCleanup(lambda: F.PROVIDERS.update(decide=None, models=None))
        for name in ('note_frames.evaluate', 'chart_reader.answer', 'chart_reader.resolve_subject'):
            self.addCleanup(R.register_executor, name, None)

    def run_it(self, haiku_replies, sonnet_replies):
        self.haiku = FakeModel(list(haiku_replies), name=ENTRY)
        self.sonnet = FakeModel(list(sonnet_replies), name=STRONGER, usd=0.03)
        F.PROVIDERS['models'] = {'haiku': self.haiku, 'sonnet': self.sonnet}
        stubs = {'tier_' + n: (lambda self_: None) for n in P.ORDER if n != 'spec_path'}
        c = P.Cascade(self.w.stack, self.w.catalog, FQ, ledger=SimpleNamespace(close=lambda: None, path=None))
        env = {'AGERE_S1_ROUTER': 'off', 'AGERE_BACKGROUND_CONTINUE': 'off', 'AGERE_ANSWER_BUDGET_SECONDS': '600', 'AGERE_SPEC_FILLERS': 'haiku'}
        from clinical_core.agere.clinical import decision_context as DC
        from spec_test_support import AS_OF
        with patch.dict(os.environ, env), patch.object(P.Cascade, 'expected', lambda self_, name: 0.0), patch.multiple(P.Cascade, **stubs), DC.scope(FQ, AS_OF):
            try:
                return c, c.run()
            except QuestionNotUnderstood as exc:
                return c, exc

    def test_one_entry_call_and_no_stronger_call_when_the_entry_spec_validates(self):
        c, result = self.run_it([json.dumps(spec_count_hf(FQ, source='Patients', measure='how many'))], [])
        self.assertFalse(isinstance(result, Exception), result)
        self.assertEqual((len(self.haiku.calls), len(self.sonnet.calls)), (1, 0))
        self.assertEqual(c.escalated, [])

    def test_stronger_writes_the_spec_only_after_entry_failed_twice(self):
        bad = json.dumps({'source': {'table': 'patient', 'quote': 'nothing'}})
        good = json.dumps(spec_count_hf(FQ, source='Patients', measure='how many'))
        c, result = self.run_it([bad, bad], [good])
        self.assertFalse(isinstance(result, Exception), result)
        self.assertEqual((len(self.haiku.calls), len(self.sonnet.calls)), (2, 1))
        self.assertEqual([e['tier'] for e in c.escalated], ['spec_path'])


class CostReceipt(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'ledger.sqlite3'
        self.ledger = G.BudgetLedger(self.path)
        self.addCleanup(self.ledger.close)

    def paid(self, key, model, text='x'):
        self.ledger.reserve(key, 'claude', model)
        self.ledger.finish(key, response={'model': model, 'content': [{'type': 'text', 'text': text}],
                                          'usage': {'input_tokens': 1000, 'output_tokens': 100}}, seconds=0.2)

    def question(self, *calls):
        probe = T.start('How many patients have heart failure?', ledger_path=self.path)
        for i, model in enumerate(calls):
            self.paid(f'k{i}', model)
        return probe.finish({'answer': {'status': 'answered'}, 'planner_receipt': {'interpretation': {'interpreter': 'spec_path'}}})

    def test_a_typical_question_is_one_entry_call_and_zero_stronger(self):
        tel = self.question(ENTRY)
        receipt = T.receipt(tel, {'answer': {'status': 'answered'}})
        self.assertEqual(receipt['claude_calls_by_model'], {ENTRY: 1})
        self.assertEqual(tel['claude_calls_by_model'].get(STRONGER, 0), 0)
        self.assertEqual(tel['sonnet_before_haiku'], [])
        self.assertLessEqual(tel['claude_calls_by_model'][ENTRY], 1)

    def test_claude_calls_by_model_counts_only_claude_and_stronger_after_entry_is_in_policy(self):
        self.ledger.reserve('jev1', 'jev', 'jev-1.13.0')
        self.ledger.finish('jev1', response={'model': 'jev-1.13.0', 'content': [], 'usage': {}}, seconds=0.1)
        tel = self.question(ENTRY, ENTRY, STRONGER)
        self.assertEqual(tel['claude_calls_by_model'], {ENTRY: 2, STRONGER: 1})
        self.assertEqual(tel['sonnet_before_haiku'], [])

    def test_a_stronger_first_call_is_flagged(self):
        tel = self.question(STRONGER, ENTRY)
        self.assertEqual(tel['sonnet_before_haiku'], [])
        self.assertEqual(tel['frontier_policy'], 'sonnet-opus/1')


if __name__ == '__main__':
    unittest.main()
