"""System One gate (system_one_gate.py): checker on every executed proposal, question router, and the fallback when Claude is
unavailable. System One (Jev/Perplexity) is mocked; no network, model or warehouse.

Opt-in live smoke (AGERE_LIVE_SYSTEM_ONE=1, <= 40 calls): checker on 3 right + 3 wrong answer summaries, router on 10 questions.
"""
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from clinical_core.agere.clinical import answer_checks as A
from clinical_core.agere.clinical import proposers as P
from clinical_core.agere.clinical import system_one_gate as G
from clinical_core.agere.gateway import ProviderUnavailable
from clinical_core.agere.clinical.question_input import QuestionNotUnderstood

S1_SWITCHES = ('AGERE_S1_CHECK', 'AGERE_S1_ROUTER', 'AGERE_S1_FALLBACK', 'AGERE_S1_CHECK_REJECT', 'AGERE_S1_ROUTE_MIN',
               'AGERE_PROPOSERS', 'AGERE_PROPOSER_SKIP')


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if k not in S1_SWITCHES}
    return patch.dict(os.environ, {**env, 'AGERE_ANSWER_BUDGET_SECONDS': 'off', 'AGERE_BACKGROUND_CONTINUE': 'off', **extra}, clear=True)  # every tier attempted


class FakeS1:
    """decide(question, options, evidence, ...) -> the scripted label/probabilities; records every call."""
    def __init__(self, label, p=0.9, *, by='jev', cost=0.0002, cache_hit=False, raises=None, labels=None):
        self.label, self.p, self.by, self.cost, self.cache_hit, self.raises = label, p, by, cost, cache_hit, raises
        self.labels, self.calls = labels or {}, []

    def available(self): return {'jev': True, 'perplexity': True}

    def decide(self, question, options, evidence, **kw):
        self.calls.append({'question': question, 'options': dict(options), 'evidence': list(evidence), **kw})
        if self.raises: raise self.raises
        assert 'unknown' in options
        label = next((v for k, v in self.labels.items() if k in question), self.label)
        rest = (1 - self.p) / max(1, len(options) - 1)
        probs = {k: (self.p if k == label else rest) for k in options}
        step = {'label': label, 'probabilities': probs, 'provider': self.by, 'cost_usd': self.cost, 'cache_hit': self.cache_hit}
        return {**step, 'calibrated_probabilities': probs, 'decided_by': self.by, 'steps': [step]}


def use(fake): return patch.object(G, 's1', return_value=fake)


GRAPH = {'interpretation': 'count heart failure patients', 'coverage': 'supported_observation', 'nodes': [
    {'id': 'n1', 'operation': 'sql', 'parameters': {'query': "SELECT count(DISTINCT patient_id) FROM conditions WHERE code LIKE 'I50%'",
                                                    'filters': {'condition': 'heart failure'}, 'returns': 'count'}}]}
RESULT = {'answer': {'status': 'answered', 'answer_type': 'count', 'count': 42, 'headline': '42 patients'}, 'results': []}
LIVE = SimpleNamespace(allow_live=True, path='/nonexistent')


class Render(unittest.TestCase):
    def test_render_names_filters_sql_shape_and_headline(self):
        text = G.render('How many patients have heart failure?', GRAPH, RESULT)
        for part in ("SELECT count(DISTINCT patient_id)", 'heart failure', 'Answer shape: count', 'Headline: 42 patients', 'count: 42'):
            self.assertIn(part, text)
        self.assertLessEqual(len(G.render('q', {'nodes': [{'operation': 'review', 'parameters': {'description': 'x' * 5000}}]}, RESULT, limit=300)), 300)

    def test_sql_scope_is_not_clipped_to_a_prefix(self):
        sql = "SELECT COUNT(*) FROM patient WHERE alive=1 " + "AND alive=1 " * 100 + "AND event_date >= '2029-01-01' AND payer='Medicaid'"
        text = G.render('q', {'nodes': [{'operation': 'sql', 'parameters': {'query': sql, 'returns': 'count'}}]}, RESULT, limit=300)
        self.assertIn(sql, text)
        self.assertIn("payer='Medicaid'", text)
        self.assertGreater(len(text), 300)

    def test_checker_reads_the_context_query_that_ran_not_the_proposal(self):
        graph = {'nodes': [{'id': 'answer', 'operation': 'sql', 'parameters': {'query': 'SELECT 99 AS value'}}]}
        result = {'answer': {'answer_type': 'value', 'value': 1}, 'results': [
            {'node': graph['nodes'][0], 'result': {'schema': 'semantic-sql/1', 'query': 'SELECT 99 AS value',
             'decision_context': {'executed_query': 'SELECT 1 AS value'}}}]}
        text = G.render('q', graph, result)
        self.assertIn('SELECT 1 AS value', text)
        self.assertNotIn('SELECT 99 AS value', text)

    def test_every_sql_step_is_visible(self):
        graph = {'nodes': [{'id': str(i), 'operation': 'sql', 'parameters': {'query': f'SELECT {i}'}} for i in range(6)]}
        self.assertIn('SELECT 5', G.render('q', graph, RESULT))

    def test_render_without_plan(self):
        self.assertEqual(G.render('q', None, None), 'Nothing was computed.')


class Checker(unittest.TestCase):
    def test_confident_different_question_rejects(self):
        fake = FakeS1('answers_a_different_question', 0.9)
        with clean_env(), use(fake):
            out = G.check('How many patients have CKD?', GRAPH, RESULT, stack=LIVE)
        self.assertTrue(out['reject']); self.assertEqual(out['label'], 'answers_a_different_question')
        self.assertEqual((out['calls'], out['cost_usd'], out['provider']), (1, 0.0002, 'jev'))
        self.assertEqual(out['decided_by'],'jev')
        self.assertIn('How many patients have CKD?', fake.calls[0]['question'])
        self.assertIn('42 patients', fake.calls[0]['evidence'][0]['text'])
        self.assertEqual(fake.calls[0]['task_key'], 's1_answer_check')

    def test_threshold_from_env(self):
        with clean_env(), use(FakeS1('answers_a_different_question', 0.8)):
            self.assertFalse(G.check('q', GRAPH, RESULT, stack=LIVE)['reject'])
        with clean_env(AGERE_S1_CHECK_REJECT='0.7'), use(FakeS1('answers_a_different_question', 0.8)):
            self.assertTrue(G.check('q', GRAPH, RESULT, stack=LIVE)['reject'])

    def test_addresses_question_sums_full_and_partial(self):
        with clean_env(), use(FakeS1('answers_fully', 0.7)):
            out = G.check('q', GRAPH, RESULT, stack=LIVE)
        self.assertAlmostEqual(out['addresses_question'], 0.8, places=3); self.assertFalse(out['reject'])

    def test_reporting_provider_is_not_decision_authority(self):
        fake=FakeS1('answers_partially',.95)
        original=fake.decide
        def unsettled(*args,**kwargs):
            value=original(*args,**kwargs);value['decided_by']=None;return value
        fake.decide=unsettled
        with clean_env(),use(fake):out=G.check('q',GRAPH,RESULT,stack=LIVE)
        self.assertEqual(out['provider'],'jev');self.assertIsNone(out['decided_by'])
        self.assertFalse(out['reject'])

    def test_unsettled_different_question_vote_cannot_reject(self):
        mod=FakeS1('answers_a_different_question',.95)
        original=mod.decide
        def undecided(*args,**kwargs):
            value=original(*args,**kwargs);value['decided_by']=None;return value
        with patch.object(G,'s1',return_value=mod),patch.object(mod,'decide',side_effect=undecided):
            out=G.check('q',GRAPH,RESULT,stack=LIVE)
        self.assertEqual(out['label'],'answers_a_different_question')
        self.assertIsNone(out['decided_by']);self.assertFalse(out['reject'])


class Composition(unittest.TestCase):
    def test_row_judgments_share_state_and_only_confident_deferrals_trigger_repair(self):
        import json
        rows=[{'patient_id':'A','next':'Read the findings.','source':{'clinical_evidence_dependency':0}},
              {'patient_id':'B','next':'Request the unavailable document.'},
              {'patient_id':'C','next':'Uncertain wording.'}]
        class Decider:
            def judge_fields(self,subject,questions,evidence,**kwargs):
                self.questions=questions;self.state=json.loads(evidence[0]['text'])
                return {'judgments':{
                    'row_0':{'label':'unfinished','probability':.96,'decided_by':'jev'},
                    'row_1':{'label':'no_observed_omission','probability':.95,'decided_by':'jev'},
                    'row_2':{'label':'unfinished','probability':.95,'decided_by':None}},
                    'steps':[{'provider':'jev','cost_usd':.001}]}
        decider=Decider()
        with patch.object(G,'s1',return_value=decider):
            result=G.check_composition('Explain each patient.',{'table':rows},as_of_date='2030-01-01')
        self.assertEqual(list(decider.questions),['row_0','row_1','row_2'])
        self.assertEqual(decider.state['rows'][0]['outputs']['next'],rows[0]['next'])
        self.assertEqual(decider.state['rows'][0]['structured_evidence_fields'],['source'])
        self.assertEqual([t['patient_id']for t in result['targets']],['A'])
        self.assertEqual((result['calls'],result['cost_usd']),(1,.001))
        self.assertNotIn('clinical_evidence_dependency',str(decider.state))

    def test_empty_population_does_not_call_provider(self):
        with patch.object(G,'s1',side_effect=AssertionError('No call')):
            result=G.check_composition('Explain each patient.',{'table':[]},as_of_date='2030-01-01')
        self.assertEqual(result['targets'],[]);self.assertEqual(result['calls'],0)


class CheckHook(unittest.TestCase):
    """answer_checks.check runs System One on every executed proposal when it is ready; cost lands in verdict['cost']."""
    def run_check(self, fake, stack=LIVE, env=None):
        with clean_env(AGERE_CROSS_CHECK='off', **(env or {})), use(fake), patch.object(A, 'zero_result_probe', return_value=None):
            return A.check(stack, 'How many patients have heart failure?', GRAPH, {'route': 'x'}, RESULT, profile=None)

    def test_different_question_rejects_and_charges(self):
        v = self.run_check(FakeS1('answers_a_different_question', 0.95))
        self.assertFalse(v['accept'])
        self.assertTrue(any(r.startswith('System One check:') for r in v['reasons']))
        self.assertEqual(v['cost'], {'new_paid_api_calls': 1, 'usd': 0.0002})
        self.assertIn('system_one_check', v['checks_run']); self.assertEqual(v['checks_run'][-1], 'cascade_certificates')
        self.assertEqual(v['system_one']['label'], 'answers_a_different_question')

    def test_partial_annotates(self):
        v = self.run_check(FakeS1('answers_partially', 0.9))
        self.assertTrue(v['accept'])
        self.assertTrue(any(a.startswith('System One check:') for a in v['annotations']))

    def test_not_run_offline_or_switched_off_or_failing(self):
        fake = FakeS1('answers_a_different_question', 0.99)
        v = self.run_check(fake, stack=SimpleNamespace(allow_live=False, path='/nonexistent'))
        self.assertTrue(v['accept']); self.assertNotIn('system_one_check', v['checks_run']); self.assertEqual(fake.calls, [])
        v = self.run_check(fake, env={'AGERE_S1_CHECK': 'off'})
        self.assertTrue(v['accept']); self.assertEqual(fake.calls, [])
        v = self.run_check(FakeS1('x', raises=RuntimeError('down')))
        self.assertTrue(v['accept']); self.assertTrue(any('system_one_check failed' in a for a in v['annotations']))
        with clean_env(), patch.object(G, 's1', return_value=None):
            self.assertFalse(G.ready(LIVE, 'AGERE_S1_CHECK'))

    def test_gate_charges_the_cascade(self):
        spent = []
        cascade = SimpleNamespace(stack=LIVE, question='How many patients have heart failure?', ledger=None,
                                  catalog=SimpleNamespace(), spend=lambda n, usd: spent.append((n, usd)),
                                  reject=lambda name, reasons: spent.append(('rejected', name)))
        with clean_env(AGERE_CROSS_CHECK='off'), use(FakeS1('answers_a_different_question', 0.95)), \
                patch.object(A, 'zero_result_probe', return_value=None):
            self.assertIsNone(A.gate(cascade, 'semantic_cache', GRAPH, {'route': 'x'}, RESULT))
        self.assertIn((1, 0.0002), spent); self.assertIn(('rejected', 'semantic_cache'), spent)


class Router(unittest.TestCase):
    def setUp(self): G._ROUTE_CACHE.clear()

    def test_reorder_is_a_permutation_with_exact_plan_first(self):
        for label in list(G.ROUTES) + ['unknown']:
            out = G.reorder(P.ORDER, label)
            self.assertEqual(sorted(out), sorted(P.ORDER)); self.assertEqual(out[0], 'exact_local_plan')
        self.assertEqual(G.reorder(P.ORDER, 'chart_evidence')[1:3], ('evidence_retrieval', 'evidence_review'))
        self.assertEqual(G.reorder(P.ORDER, 'unknown'), P.ORDER)

    def test_route_cached_by_normalized_question(self):
        fake = FakeS1('why_explanation', 0.9)
        with clean_env(), use(fake):
            first, r1 = G.route_receipt('Why did ED visits rise in March?', P.ORDER, stack=LIVE)
            second, r2 = G.route_receipt('  why did ED visits rise in march ', P.ORDER, stack=LIVE)
        self.assertEqual(first, second); self.assertEqual(first[1], 'evidence_review')
        self.assertEqual(len(fake.calls), 1); self.assertEqual((r1['calls'], r2['calls'], r2['cached']), (1, 0, True))

    def test_unknown_low_p_error_and_offline_keep_order(self):
        with clean_env(), use(FakeS1('unknown', 0.9)):
            self.assertEqual(G.route('a', P.ORDER, stack=LIVE), P.ORDER)
        with clean_env(), use(FakeS1('metric', 0.3)):
            self.assertEqual(G.route('b', P.ORDER, stack=LIVE), P.ORDER)
        with clean_env(), use(FakeS1('metric', raises=RuntimeError('x'))):
            self.assertEqual(G.route('c', P.ORDER, stack=LIVE), P.ORDER)
        with clean_env(), use(FakeS1('metric', 0.9)):
            self.assertEqual(G.route('d', P.ORDER, stack=SimpleNamespace(allow_live=False)), P.ORDER)


def cascade(stack=LIVE, q='how many patients'):
    return P.Cascade(stack, SimpleNamespace(identity={}), q, ledger=SimpleNamespace(close=lambda: None))


def stubs(reached, behaviours=None):
    behaviours = behaviours or {}

    def stub(name):
        def tier(self):
            reached.append(name)
            fn = behaviours.get(name)
            return fn(self) if fn else None
        return tier
    return patch.multiple(P.Cascade, **{'tier_' + n: stub(n) for n in P.ORDER})


class CascadeOrder(unittest.TestCase):
    def setUp(self):
        G._ROUTE_CACHE.clear()
        env = patch.dict(os.environ, {'AGERE_PROPOSER_ON': 'bottled_sql,hybrid_analysts,qwen_rewrite_compiler,frontier_graph_planner,legacy_cost_ladder,stack_planner_propose'})  # tiers off the default path that these tests exercise
        env.start(); self.addCleanup(env.stop)

    def test_system_one_routing_reorders_and_is_charged(self):
        reached = []
        with clean_env(AGERE_LOCAL_TYPED='off'), use(FakeS1('chart_evidence', 0.9)), stubs(reached):
            c = cascade(q='what does the last note say about dyspnea')
            with self.assertRaises(QuestionNotUnderstood) as ctx:
                c.run()
        first = [t for t in reached if P.STAGES[P.stage_of(t)][0] != 'reuse_certified']  # reuse/certified tiers always run first (cascade stages)
        self.assertEqual(first[:2], ['evidence_retrieval', 'evidence_review'])  # then the System One route: evidence before free planners
        self.assertLess(reached.index('evidence_review'), reached.index('local_compiler'))
        self.assertTrue(all(P.stage_of(t) == len(P.STAGES) - 1 for t in reached[-9:]))  # Claude tiers last
        self.assertEqual(sorted(reached), sorted(t for t in P.ORDER if P.enabled(t, SimpleNamespace(allow_live=True))))  # never removes an enabled tier
        self.assertEqual(ctx.exception.planner_receipt['new_paid_api_calls'], 1)
        self.assertAlmostEqual(ctx.exception.planner_receipt['known_api_estimate_usd'], 0.0002)


def candidate(name, n):
    graph = {**GRAPH, 'interpretation': f'plan {n}'}
    result = {'answer': {**RESULT['answer'], 'count': n, 'headline': f'{n} patients'}, 'results': []}
    contract = {'kind': 'checked_graph', 'graph': graph, 'executed': result, 'id': f'id{n}', 'legacy_receipt': {}}
    return contract, {'interpreter': name, 'review': 'literal_check_then_output_check', 'execution_accepted': True, 'attempts': []}


def billing(self): raise ProviderUnavailable('provider_unavailable (billing): credit balance too low', kind='billing')


class Outage(unittest.TestCase):
    def setUp(self):
        G._ROUTE_CACHE.clear()
        env = patch.dict(os.environ, {'AGERE_PROPOSER_ON': 'bottled_sql,hybrid_analysts,qwen_rewrite_compiler,frontier_graph_planner,legacy_cost_ladder,stack_planner_propose'})  # tiers off the default path that these tests exercise
        env.start(); self.addCleanup(env.stop)

    def run_outage(self, fake, behaviours):
        reached = []
        with clean_env(AGERE_S1_ROUTER='off'), use(fake), stubs(reached, behaviours):
            c = cascade()
            try:
                return reached, c, c.run()
            except Exception as exc:  # noqa: BLE001
                return reached, c, exc

    def test_outage_skips_later_claude_tiers_and_raises_without_candidates(self):
        reached, c, out = self.run_outage(FakeS1('candidate_1'), {'hosted_typed_interpreter': billing})
        self.assertIsInstance(out, ProviderUnavailable)
        self.assertEqual(c.claude_down, 'billing')
        later = [n for n in P.ORDER[P.ORDER.index('hosted_typed_interpreter') + 1:] if n in G.CLAUDE_TIERS]
        self.assertTrue(later); self.assertFalse(set(later) & set(reached))
        self.assertIn('typed_decider', reached); self.assertIn('legacy_cost_ladder', reached)  # free / System One tiers keep going
        skipped = {e['proposer'] for e in c.log if e['outcome'] == 'skipped'}
        self.assertEqual(skipped, set(later))

    def test_fallback_picks_an_executed_candidate_marked_partial(self):
        fake = FakeS1('candidate_2', 0.85)

        def keep(name, n, reasons):
            def fn(self):
                self.keep(name, candidate(name, n), reasons); return None
            return fn
        soft = "an uncalibrated free planner's answer is shipped only when System One confirms it answers the question"
        reached, c, out = self.run_outage(fake, {'semantic_cache': keep('semantic_cache', 10, ['certificate: not certified']),
                                                 'bottled_sql': keep('bottled_sql', 20, [soft]),
                                                 'local_free_sql': keep('local_free_sql', 25, ['value check: the plan filters on values the warehouse never holds (x)']),
                                                 'learned_verified_sql': keep('learned_verified_sql', 30, ['System One check: different']),
                                                 'hosted_typed_interpreter': billing})
        self.assertIsInstance(out, tuple, out)
        contract, interp = out
        self.assertEqual(contract['executed']['answer']['count'], 20)
        self.assertTrue(interp['partial']); self.assertEqual(interp['partial_reason'], 'system_one_fallback_provider_unavailable')
        self.assertEqual(interp['proposer']['name'], 'bottled_sql')
        self.assertTrue(any('Claude was unavailable (billing)' in l for l in contract['executed']['answer']['limitations']))
        self.assertEqual(interp['cascade_usage']['new_paid_api_calls'], 1)
        opts = fake.calls[-1]['options']
        # neither the S1-rejected candidate nor the one a correctness check refuted is offered
        self.assertEqual(sorted(opts), ['candidate_1', 'candidate_2', 'unknown'])
        self.assertEqual(fake.calls[-1]['task_key'], 's1_fallback_pick')

    def test_fallback_unknown_or_unavailable_reports_the_outage(self):
        keep = {'semantic_cache': lambda self: self.keep('semantic_cache', candidate('semantic_cache', 1), ['certificate: x']),
                'hosted_typed_interpreter': billing}
        _, c, out = self.run_outage(FakeS1('unknown', 0.9), keep)
        self.assertIsInstance(out, ProviderUnavailable)
        self.assertEqual(out.planner_receipt['new_paid_api_calls'], 1)  # the System One call is still charged
        with clean_env(AGERE_S1_ROUTER='off', AGERE_S1_FALLBACK='off'), use(FakeS1('candidate_1')), stubs([], keep):
            with self.assertRaises(ProviderUnavailable):
                cascade().run()


@unittest.skipUnless(os.environ.get('AGERE_LIVE_SYSTEM_ONE') == '1', 'live System One smoke is opt-in (AGERE_LIVE_SYSTEM_ONE=1)')
class LiveSmoke(unittest.TestCase):
    """<= 40 live calls: 6 checker judgments (decide may escalate to Perplexity) + 10 router classifications."""
    def test_live(self):
        import json
        from run_integrated_stack import keys
        keys()
        G._ROUTE_CACHE.clear()
        stack = SimpleNamespace(allow_live=True, path='/nonexistent')
        sql = lambda q: {'nodes': [{'operation': 'sql', 'parameters': {'query': q}}]}
        ans = lambda t, h, **kw: {'answer': {'status': 'answered', 'answer_type': t, 'headline': h, **kw}, 'results': []}
        cases = [  # (question, graph, result, expected right?)
            ('How many patients have heart failure?', sql("SELECT count(DISTINCT patient_id) FROM conditions WHERE code LIKE 'I50%'"),
             ans('count', '312 patients', count=312), True),
            ('What share of diabetics had an HbA1c test in 2025?', sql(
                "SELECT 100.0*count(DISTINCT l.patient_id)/(SELECT count(DISTINCT patient_id) FROM conditions WHERE code LIKE 'E11%') "
                "FROM labs l JOIN conditions c USING(patient_id) WHERE c.code LIKE 'E11%' AND l.test='hba1c' AND l.year=2025"),
             ans('rate', '71.4% of 820 diabetic patients', value=71.4, denominator=820), True),
            ('List patients with an ED visit in the last 30 days', sql(
                "SELECT DISTINCT patient_id FROM encounters WHERE type='ED' AND date >= date('2026-09-30','-30 day')"),
             ans('patient_list', '18 patients', patient_ids=[f'p{i}' for i in range(18)]), True),
            ('How many patients have heart failure?', sql("SELECT count(DISTINCT patient_id) FROM conditions WHERE code LIKE 'N18%'"),
             ans('count', '204 patients', count=204), False),
            ('What share of diabetics had an HbA1c test in 2025?', sql("SELECT count(*) FROM labs WHERE test='ldl'"),
             ans('count', '5,120 lab rows', count=5120), False),
            ('List patients with an ED visit in the last 30 days', sql(
                "SELECT avg(paid_amount) FROM claims WHERE place_of_service='inpatient'"), ans('value', '$14,210 average inpatient paid', value=14210), False),
        ]
        rows, usd, calls = [], 0.0, 0
        for q, g, r, right in cases:
            out = G.check(q, g, r, stack=stack)
            usd += out['cost_usd']; calls += out['calls']
            ok = (out['label'] in ('answers_fully', 'answers_partially')) if right else out['label'] == 'answers_a_different_question'
            rows.append({'check': q[:40], 'expected_right': right, 'label': out['label'], 'p': out['p'], 'provider': out['provider'], 'ok': ok})
        questions = {
            'How many patients have a diagnosis code of I50.9?': 'literal_lookup',
            'What is our 30-day readmission rate for 2025?': 'metric',
            'Forecast next quarter ED utilization by practice': 'engine_analysis',
            "What did the cardiologist's last note say about patient p123's ejection fraction?": 'chart_evidence',
            'How many patients have uncontrolled hypertension?': 'definition_dependent',
            'Why did total cost of care rise in Q3?': 'why_explanation',
            'Break those down by payer': 'follow_up',
            "What's the weather in Nashville tomorrow?": 'out_of_data_scope',
            'Which members are on an SGLT2 inhibitor?': 'literal_lookup',
            'What share of attributed members had an annual wellness visit?': 'metric',
        }
        for q, want in questions.items():
            got = G.classify(q, stack=stack)
            usd += got['cost_usd']; calls += got['calls']
            rows.append({'route': q[:50], 'want': want, 'label': got['label'], 'p': got['p'], 'provider': got['provider'], 'ok': got['label'] == want})
        print(json.dumps({'rows': rows, 'checker_ok': sum(r['ok'] for r in rows[:6]), 'router_ok': sum(r['ok'] for r in rows[6:]),
                          'calls': calls, 'usd': round(usd, 6)}, indent=1))
        self.assertLessEqual(calls, 40)


if __name__ == '__main__':
    unittest.main()
