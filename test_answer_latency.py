"""Production latency: a 30 s answer budget, tiers started only when their expected seconds fit, heavy tiers last, stop at
confirmation, an honest early reply at the deadline, and a bounded serialized background continuation that saves its answer.
Fake tiers and a fake clock; no model is loaded."""
import os
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from clinical_core.agere.clinical import answer_budget as AB, background_continuation as BG, claim_verification as CV
from clinical_core.agere.clinical import proposers as P, tier_timing as TT
from clinical_core.agere.clinical.question_input import QuestionNotUnderstood, failure

QUESTION = 'how many patients have diabetes'
ENV = {'AGERE_S1_ROUTER': 'off', 'AGERE_LOCAL_TYPED': 'off', 'AGERE_LOCAL_NORMALIZER': 'off', 'AGERE_CASCADE_ROUTER': 'off'}


class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


def answer_contract(certified=True, s1=None):
    checks = {'certificate': {'status': 'certified' if certified else 'none'}}
    if s1: checks['system_one_cross_check'] = s1
    graph = {'coverage': 'supported_observation', 'nodes': [{'id': 'a', 'operation': 'sql', 'parameters': {
        'query': "select count(*) from patients where condition = 'diabetes'", 'returns': 'count'}}]}
    executed = {'answer': {'status': 'answered', 'answer_type': 'count', 'count': 3, 'headline': '3', 'missing': []}, 'answer_checks': checks}
    contract = {'kind': 'checked_graph', 'graph': graph, 'executed': executed, 'id': 'id1', 'legacy_receipt': {}}
    return contract, {'interpreter': 'x', 'review': 'literal_check_then_output_check', 'execution_accepted': True, 'attempts': []}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        env = patch.dict(os.environ, {'AGERE_PROPOSER_ON': 'bottled_sql,hybrid_analysts,qwen_rewrite_compiler'})  # the heavy tiers these tests exercise are off the default path: switched on here
        env.start(); self.addCleanup(env.stop)
        self.clock = Clock()
        self.reached = []
        TT._cache.clear()

    def cascade(self, live=True):
        stack = SimpleNamespace(allow_live=live, path=self.dir.name, retriever=SimpleNamespace(meta={'snapshot': {'as_of': '2030-05-27'}}))
        return P.Cascade(stack, SimpleNamespace(identity={}), QUESTION, ledger=SimpleNamespace(close=lambda: None))

    def fake_tiers(self, costs=None, answers=None, expected=None):
        """Every tier advances the fake clock by costs[name] (default 0.1 s) and answers (contract) only when named in answers."""
        costs, answers, expected = costs or {}, answers or {}, expected or {}

        def stub(name):
            def tier(c):
                self.reached.append(name)
                self.clock.t += costs.get(name, 0.1)
                return answers[name]() if name in answers else None
            return tier
        patches = [patch.multiple(P.Cascade, **{'tier_' + n: stub(n) for n in P.ORDER}),
                   patch.object(P.Cascade, 'expected', lambda c, name: expected.get(name, 0.5)),
                   patch.object(P.Cascade, 'order', lambda c: P.ORDER),
                   patch.object(AB, 'start', lambda seconds=None, **k: AB.AnswerBudget(clock=self.clock).start(30 if seconds is None else seconds)),
                   patch.dict(os.environ, {**ENV, 'AGERE_ANSWER_BUDGET_SECONDS': '', 'AGERE_BACKGROUND_CONTINUE': 'off'})]
        for p in patches:
            p.start(); self.addCleanup(p.stop)


class Production(Fixture):
    def test_default_budget_is_30_seconds(self):
        with patch.dict(os.environ, {'AGERE_ANSWER_BUDGET_SECONDS': ''}):
            self.assertEqual(AB.default_seconds(), 30.0)
        with patch.dict(os.environ, {'AGERE_ANSWER_BUDGET_SECONDS': '120'}):
            self.assertEqual(AB.default_seconds(), 120.0)  # benchmarks keep setting it higher

    def test_a_cheap_tier_that_answers_stops_the_cascade(self):
        self.fake_tiers(answers={'semantic_cache': lambda: answer_contract()})
        contract, interp = self.cascade().run()
        self.assertEqual(self.reached[-1], 'semantic_cache')
        self.assertNotIn('local_normalizer', self.reached)
        timing = interp['answer_timing']
        self.assertIn('semantic_cache', timing['tier_seconds'])
        self.assertEqual(timing['deferred_tiers'], [])
        self.assertEqual(timing['budget_seconds'], 30.0)
        self.assertIn('total_seconds', timing)

    def test_a_heavy_tier_is_skipped_when_its_expected_cost_exceeds_the_remaining_budget(self):
        # 20 s spent before the heavy tiers: 10 s left; the 20 s local models and the 30 s atomic tier do not fit, 2 s tiers still run
        est = {n: 20.0 for n in P.COSTLY if n != 'atomic_system_one'} | {'atomic_system_one': 20.0}
        self.fake_tiers(costs={'local_compiler': 20.0}, expected=est)
        c = self.cascade()
        with self.assertRaises(QuestionNotUnderstood) as ctx:
            c.run()
        for n in P.COSTLY: self.assertNotIn(n, self.reached)
        self.assertIn('specialist_families_late', self.reached)  # cheap tiers still ask (cohort_family_late is off the default path)
        deferred = {d['tier']: d for d in c.deferred}
        on = {t for t in P.COSTLY if P.enabled(t, SimpleNamespace(allow_live=True))}
        self.assertEqual(set(P.COSTLY) & set(deferred), on)  # none dropped: every enabled heavy tier listed (switched-off tiers never run)
        self.assertEqual(deferred['bottled_sql']['expected_seconds'], 20.0)
        entry = next(e for e in c.log if e['proposer'] == 'bottled_sql')
        self.assertEqual(entry['outcome'], 'skipped')
        self.assertTrue(entry['detail'].startswith('skipped: budget'))
        self.assertIn('expected ~20s', entry['detail'])

    def test_deadline_returns_tiers_tried_and_deferred_tiers(self):
        self.fake_tiers(costs={'local_compiler': 31.0})  # the budget is gone after the compiler
        c = self.cascade()
        with self.assertRaises(QuestionNotUnderstood) as ctx:
            c.run()
        after = P.ORDER[P.ORDER.index('local_compiler') + 1:]
        self.assertEqual([d['tier'] for d in c.deferred], [t for t in after if P.enabled(t, c.stack)])
        out = failure(ctx.exception)
        self.assertEqual([d['tier'] for d in out['deferred_tiers']], [d['tier'] for d in c.deferred])
        self.assertTrue(all(t['tier'] and not t['detail'].startswith('skipped: budget') for t in out['tiers_tried']))
        self.assertIn('local_compiler', {t['tier'] for t in out['tiers_tried']} | {e['proposer'] for e in c.log})
        self.assertIn('total_seconds', out['answer_timing'])
        self.assertEqual(out['background_continuation']['queued'], False)  # AGERE_BACKGROUND_CONTINUE=off in this fixture

    def test_deadline_with_an_executed_candidate_returns_it_partial(self):
        def offer():
            contract, interp = answer_contract(False)
            self.cascade_obj.budget.offer((contract, {**interp, 'interpreter': 'bottled_sql'}), score=1, label='bottled_sql', reasons=['shape'])
        self.fake_tiers(costs={'semantic_cache': 31.0})
        c = self.cascade_obj = self.cascade()
        with patch.object(P.Cascade, 'tier_semantic_cache', lambda c: (offer(), setattr(self.clock, 't', self.clock.t + 31), None)[2]):
            contract, interp = c.run()
        self.assertTrue(interp['partial'])
        self.assertEqual(interp['partial_reason'], 'answer_time_budget')
        self.assertTrue(interp['answer_timing']['deferred_tiers'])


class Estimates(Fixture):
    def test_tier_medians_update_and_seed_with_conservative_defaults(self):
        path = self.dir.name
        self.assertEqual(TT.estimate(path, 'bottled_sql'), 20.0)
        self.assertEqual(TT.estimate(path, 'atomic_system_one'), 20.0)
        self.assertEqual(TT.estimate(path, 'evidence_review'), 10.0)
        self.assertEqual(TT.estimate(path, 'semantic_cache'), 2.0)
        TT.record(path, {'bottled_sql': [4.0]})
        self.assertEqual(TT.estimate(path, 'bottled_sql'), 20.0)       # one measurement does not outvote two default stand-ins
        TT.record(path, {'bottled_sql': [4.0, 6.0]})
        self.assertEqual(TT.estimate(path, 'bottled_sql'), 4.0)        # median of 4, 4, 6
        TT.record(path, {'bottled_sql': [8.0] * 15})
        self.assertEqual(TT.estimate(path, 'bottled_sql'), 8.0)        # only the last 15 count
        self.assertEqual(TT.estimate('/nonexistent/stack', 'bottled_sql'), 20.0)  # no store: defaults, no error

    def test_a_cascade_measures_its_tiers_and_stores_them(self):
        self.fake_tiers()
        with patch.object(P.Cascade, 'expected', lambda c, name: 0.5):
            c = self.cascade()
            with self.assertRaises(QuestionNotUnderstood): c.run()
        stored = TT._load(self.dir.name)
        self.assertIn('exact_local_plan', stored)
        self.assertIn('bottled_sql', stored)

    def test_the_expected_cost_comes_from_the_store(self):
        TT.record(self.dir.name, {'bottled_sql': [3.0, 3.0, 3.0]})
        self.assertEqual(self.cascade().expected('bottled_sql'), 3.0)


class Ordering(Fixture):
    def test_costly_tiers_wait_for_stages_a_b_and_the_local_compiler(self):
        order = ('local_normalizer', 'bottled_sql', *P.ORDER)  # a route asked for the local models first
        order = tuple(dict.fromkeys(order))
        moved = self.cascade().heavy_last(order)
        self.assertEqual(sorted(moved), sorted(order))
        cheap = [i for i, t in enumerate(moved) if t == 'local_compiler' or (t not in P.COSTLY and P.stage_of(t) <= 1)]
        self.assertTrue(all(moved.index(t) > max(cheap) for t in P.COSTLY))
        self.assertEqual(self.cascade().heavy_last(P.ORDER), P.ORDER)  # the designed order is already in that shape

    def test_costly_tiers_follow_the_learned_routers_estimate_inside_a_stage(self):
        c = self.cascade()
        c.learned_routing = {'predicted': {'bottled_sql': 0.9, 'qwen_typed_interpreter': 0.1, 'local_normalizer': 0.5},
                             'movable': ['bottled_sql', 'qwen_typed_interpreter']}
        moved = c.heavy_last(P.ORDER)
        self.assertLess(moved.index('bottled_sql'), moved.index('qwen_typed_interpreter'))
        self.assertEqual(sorted(moved), sorted(P.ORDER))
        slots = [P.ORDER.index('bottled_sql'), P.ORDER.index('qwen_typed_interpreter')]
        self.assertEqual(sorted(moved.index(t) for t in ('bottled_sql', 'qwen_typed_interpreter')), sorted(slots))  # slots kept, tiers swapped


class FakeCascade:
    """What claim_verification.verify needs from the cascade."""
    def __init__(self, budget=None, expected=2.0):
        self.log, self.claude_down, self.population, self.question = [], None, None, QUESTION
        self.stack, self.budget, self._expected, self.asked = SimpleNamespace(allow_live=False, path='/nonexistent'), budget, expected, []
    def expected(self, name): return self._expected
    def tier_seconds(self, name, budget): return None
    def note(self, name, outcome, detail, started): self.log.append({'proposer': name, 'outcome': outcome, 'detail': detail})
    def call_tier(self, name, limit): self.asked.append(name); return None
    def spend(self, *a): pass


CONFIRMED = {'verdict': 'confirmed', 'votes': {'jev': {'label': 'answers_fully', 'p': 0.9}, 'perplexity': {'label': 'answers_fully', 'p': 0.9}}}
ORDER = ('local_compiler', 'local_free_sql', 'qwen_typed_interpreter', 'atomic_system_one')


class StopAtConfirmation(unittest.TestCase):
    def test_a_system_one_confirmed_answer_is_not_re_run(self):
        c = FakeCascade()
        contract, interp = answer_contract(certified=False, s1=CONFIRMED)
        out_contract, out = CV.verify(c, ORDER, 'local_compiler', (contract, interp))
        self.assertEqual(c.asked, [])  # no heavy re-run
        self.assertEqual(out['claim_verification']['verification'], 'confirmed by System One')
        self.assertEqual(out['claim_verification']['status'], 'confirmed')

    def test_a_certified_answer_is_not_re_run(self):
        c = FakeCascade()
        CV.verify(c, ORDER, 'local_compiler', answer_contract(certified=True))
        self.assertEqual(c.asked, [])

    def test_a_partial_vote_keeps_the_second_opinion_search(self):
        partial = {**CONFIRMED, 'votes': {**CONFIRMED['votes'], 'perplexity': {'label': 'answers_partially', 'p': 0.9}}}
        c = FakeCascade(budget=AB.AnswerBudget(clock=Clock()).start(30))
        _, out = CV.verify(c, ORDER, 'local_compiler', answer_contract(certified=False, s1=partial))
        self.assertTrue(c.asked)
        self.assertNotIn('verification', out['claim_verification'])

    def test_uncertified_unconfirmed_second_opinions_only_within_the_remaining_budget(self):
        clock = Clock()
        budget = AB.AnswerBudget(clock=clock).start(30)
        clock.t += 25  # 5 s left; the verifiers are expected to take 20 s
        c = FakeCascade(budget=budget, expected=20.0)
        _, out = CV.verify(c, ORDER, 'local_compiler', answer_contract(certified=False))
        self.assertEqual(c.asked, [])
        self.assertEqual([e['tier'] for e in out['claim_verification']['skipped_for_budget']], ['local_free_sql', 'qwen_typed_interpreter', 'atomic_system_one'])
        self.assertTrue(all(e['detail'].startswith('skipped: budget') for e in c.log))


class Background(Fixture):
    def fake(self, question, tiers=('bottled_sql',)):
        return SimpleNamespace(deferred=[{'tier': t} for t in tiers], stack=SimpleNamespace(path=self.dir.name), catalog=None, question=question,
                               population=None, hosted_model=None, memory_identity=None, order_used=tuple(tiers), log=[], claude_down=None)

    def setUp(self):
        super().setUp()
        BG._queue.clear(); BG._recent.clear()
        self.assertTrue(BG.wait_idle(5))

    def test_the_queue_is_bounded_serialized_and_deduplicated(self):
        gate, started, order, live, peak = threading.Event(), threading.Event(), [], [0], [0]

        def run_job(job):
            live[0] += 1; peak[0] = max(peak[0], live[0]); order.append(job['question']); started.set()
            gate.wait(5)
            live[0] -= 1
            return {'answered': False}
        with patch.dict(os.environ, {'AGERE_BACKGROUND_CONTINUE': 'on', 'AGERE_BACKGROUND_QUEUE_MAX': '2', 'AGERE_BACKGROUND_DELAY_SECONDS': '0'}), patch.object(BG, 'run_job', run_job):
            first = BG.enqueue(self.fake('q0'))
            self.assertTrue(first['queued'])
            self.assertTrue(started.wait(5))  # q0 is running; the queue behind it is empty
            self.assertTrue(BG.enqueue(self.fake('q1'))['queued'])
            self.assertTrue(BG.enqueue(self.fake('q2'))['queued'])
            full = BG.enqueue(self.fake('q3'))
            self.assertFalse(full['queued']); self.assertIn('queue full', full['reason'])
            dup = BG.enqueue(self.fake('q1'))
            self.assertFalse(dup['queued']); self.assertIn('already', dup['reason'])
            self.assertFalse(BG.enqueue(self.fake('q0'))['queued'])  # the running one too
            gate.set()
            self.assertTrue(BG.wait_idle(10))
        self.assertEqual(order, ['q0', 'q1', 'q2'])  # one at a time, in arrival order
        self.assertEqual(peak[0], 1)
        self.assertEqual([r['question'] for r in BG.recent()], ['q0', 'q1', 'q2'])

    def test_off_switch_and_nothing_deferred(self):
        with patch.dict(os.environ, {'AGERE_BACKGROUND_CONTINUE': 'off'}):
            self.assertFalse(BG.enqueue(self.fake('q'))['queued'])
        with patch.dict(os.environ, {'AGERE_BACKGROUND_CONTINUE': 'on'}):
            self.assertFalse(BG.enqueue(self.fake('q', tiers=()))['queued'])

    def test_a_deadline_queues_one_continuation_that_saves_its_answer(self):
        saved, asked = [], []
        self.fake_tiers(costs={'local_compiler': 31.0})
        # the foreground tier list stubs answer nothing; the deferred tier bottled_sql answers when the background worker asks it
        with patch.dict(os.environ, {'AGERE_BACKGROUND_CONTINUE': 'on', 'AGERE_BACKGROUND_DELAY_SECONDS': '0'}), \
                patch.object(P.Cascade, 'tier_bottled_sql', lambda c: asked.append(threading.current_thread().name) or answer_contract(certified=True)), \
                patch.object(BG, 'save', lambda cascade, contract, interpretation: saved.append((cascade.question, interpretation['proposer']['name'])) or True):
            c = self.cascade()
            with self.assertRaises(QuestionNotUnderstood) as ctx:
                c.run()
            self.assertTrue(c.background['queued'])
            self.assertIn('bottled_sql', c.background['tiers'])
            self.assertTrue(BG.wait_idle(10))
            self.assertEqual(failure(ctx.exception)['background_continuation']['queued'], True)
        self.assertEqual(saved, [(QUESTION, 'bottled_sql')])
        self.assertEqual(asked, ['agere-background-continuation'])  # on the worker thread, not the question's
        self.assertEqual(BG.recent()[-1]['saved'], True)
        self.assertEqual(BG.recent()[-1]['tier'], 'bottled_sql')

    def test_a_graph_answer_is_saved_through_the_verified_store(self):
        calls = []
        c = self.cascade()
        contract, interp = answer_contract(certified=True)
        interp = {**interp, 'proposer': {'name': 'bottled_sql'}}
        with patch('clinical_core.agere.clinical.learning_hooks.after_answer', lambda stack, q, result, **k: calls.append((q, result)) or {'saved_pair': {'saved': True}}):
            self.assertTrue(BG.save(c, contract, interp))
        question, result = calls[0]
        self.assertEqual(question, QUESTION)
        self.assertEqual(result['answer']['status'], 'answered')
        self.assertEqual(result['graph'], contract['graph'])
        self.assertEqual(result['planner_receipt']['interpretation'], interp)

    def test_a_typed_contract_is_saved_for_replay_and_paraphrase_reuse(self):
        c = self.cascade()
        contract = {'kind': 'request', 'request': {'source': 'x'}}
        interp = {'interpreter': 'qwen_typed_interpreter', 'execution_accepted': True}
        with patch('clinical_core.agere.clinical.shared_planner.save_accepted', lambda *a, **k: True) as save:
            self.assertTrue(BG.save(c, contract, interp))


if __name__ == '__main__':
    unittest.main()


class BackgroundIsFreeByDefault(unittest.TestCase):
    def test_claude_tiers_are_not_queued_unless_asked(self):  # token economics: misses never start paid Claude work on their own
        from types import SimpleNamespace
        from clinical_core.agere.clinical import background_continuation as BC
        c = SimpleNamespace(deferred=[{'tier': 'atomic_system_one'}, {'tier': 'hosted_typed_interpreter'}, {'tier': 'metric_router'}])
        with patch.dict(os.environ, {'AGERE_BACKGROUND_CONTINUE': 'off'}, clear=False):
            os.environ.pop('AGERE_BACKGROUND_CLAUDE', None)
            self.assertEqual(BC.enqueue(c)['tiers'], ['atomic_system_one'])
            os.environ['AGERE_BACKGROUND_CLAUDE'] = 'on'
            self.assertEqual(BC.enqueue(c)['tiers'], ['atomic_system_one', 'hosted_typed_interpreter', 'metric_router'])
            os.environ.pop('AGERE_BACKGROUND_CLAUDE', None)
