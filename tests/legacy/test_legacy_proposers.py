"""legacy_proposers: the off-path planner components as checked proposers / helpers. No model, network or paid call:
providers are stubbed; graphs are validated by the real analysis_graph validator."""
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from clinical_core.agere.clinical import legacy_proposers as L
from clinical_core.agere.clinical import semantic_analyst, together, economical_planner
from clinical_core.agere.clinical.economical_planner import economics
from clinical_core.agere.clinical.question_input import QuestionNotUnderstood

AS_OF = '2025-06-30'


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        with sqlite3.connect(self.path/'stack.sqlite3') as db:
            db.execute('CREATE TABLE patients (id TEXT)')
            db.executemany('INSERT INTO patients VALUES (?)', [('P-1002',), ('P-1003',)])
        (self.path/'semantic.sqlite3').write_bytes(b'')
        self.ran = []
        self.stack = SimpleNamespace(path=self.path, allow_live=True, receipts=self.path/'r',
                                     retriever=SimpleNamespace(meta={'snapshot': {'as_of': AS_OF}}, encoder=None),
                                     run_graph=self.run_graph)
        self.ledger = SimpleNamespace(close=lambda: None)
        self.fast_answer = {'status': 'answered', 'answer_type': 'count', 'count': 7}

    def tearDown(self):
        self.tmp.cleanup()

    def run_graph(self, graph, question=None):
        self.ran.append(graph)
        return {'answer': dict(self.fast_answer), 'results': [], 'new_paid_api_calls': 0}

    @contextmanager
    def analyst(self, run, rec=None):
        calls = {'analyze': 0, 'reconcile': []}

        def analyze(question, as_of, wh, *, send, model, **kw):
            calls['analyze'] += 1
            return run

        def reconcile(question, as_of, first, second, wh, *, send, model, seed_key, labels):
            calls['reconcile'].append((first, second, labels))
            return rec
        with patch.object(together, 'warehouse', lambda p: SimpleNamespace(as_of=AS_OF)), \
                patch.object(L, '_send', lambda *a: None), \
                patch.object(semantic_analyst, 'analyze', analyze), patch.object(semantic_analyst, 'reconcile', reconcile):
            yield calls


def run_of(answer, sql='SELECT COUNT(*) AS n FROM patient WHERE d <= :as_of', rows=None, usd=0.01):
    return {'answer': answer, 'model': 'claude-haiku-5-5', 'estimated_cost_usd': usd, 'new_paid_api_calls': 2,
            'tool_calls': [{'sql': 'SELECT bad', 'result': {'error': 'no such table'}},
                           {'sql': sql, 'result': {'columns': ['n'], 'rows': rows if rows is not None else [{'n': 7}]}}]}


class SemanticAnalyst(Fixture):
    def test_answer_becomes_executable_sql_graph_with_priced_steps(self):
        with self.analyst(run_of(self.fast_answer)) as calls:
            graph, receipt = L.semantic_analyst_tier(self.stack, 'How many patients?', self.ledger)
        node = graph['nodes'][0]
        self.assertEqual((node['operation'], node['parameters']['returns']), ('sql', 'count'))
        self.assertIn(f"'{AS_OF}'", node['parameters']['query'])
        self.assertNotIn(':as_of', node['parameters']['query'])
        self.assertEqual((receipt['route'], receipt['new_paid_api_calls'], calls['reconcile']), ('semantic_analyst', 2, []))
        self.assertAlmostEqual(economics(receipt)['known_api_estimate_usd'], 0.01)

    def test_stated_answer_that_differs_from_its_sql_goes_to_opus_reconcile(self):
        rec = {'picked': 'neither', 'reason': 'r', 'answer': {'status': 'answered', 'answer_type': 'count', 'count': 9},
               'reconcile_cost_usd': 0.05, 'reconcile_error': None, 'reconcile_sql': ['SELECT 9 AS n'],
               'result': {'new_paid_api_calls': 1, 'tool_calls': [{'sql': 'SELECT 9 AS n', 'result': {'rows': [{'n': 9}]}}]}}
        with self.analyst(run_of({**self.fast_answer, 'count': 8}), rec) as calls:
            graph, receipt = L.semantic_analyst_tier(self.stack, 'How many patients?', self.ledger)
        self.assertEqual(calls['reconcile'][0][2], ('analyst', 'executed'))
        self.assertEqual(calls['reconcile'][0][1]['answer']['count'], 7)
        self.assertEqual(graph['nodes'][0]['parameters']['query'], 'SELECT 9 AS n')
        self.assertEqual(receipt['new_paid_api_calls'], 3)
        self.assertAlmostEqual(economics(receipt)['known_api_estimate_usd'], 0.06)

    def test_unsupported_is_a_named_decline(self):
        with self.analyst(run_of({'status': 'unsupported', 'answer_type': 'unsupported', 'missing': ['no claims feed']})):
            graph, _ = L.semantic_analyst_tier(self.stack, 'What is the PMPM?', self.ledger)
        self.assertEqual((graph['coverage'], graph['nodes'], graph['unmet_requirements']), ('unsupported', [], ['no claims feed']))

    def test_offline_or_no_semantic_layer_proposes_nothing(self):
        with self.analyst(run_of(self.fast_answer)) as calls:
            self.assertIsNone(L.semantic_analyst_tier(self.stack, 'q', None))
            self.assertIsNone(L.semantic_analyst_tier(SimpleNamespace(**{**vars(self.stack), 'allow_live': False}), 'q', self.ledger))
            (self.path/'semantic.sqlite3').unlink()
            self.assertIsNone(L.semantic_analyst_tier(self.stack, 'q', self.ledger))
        self.assertEqual(calls['analyze'], 0)

    def test_no_answer_or_failed_sql_proposes_nothing(self):
        with self.analyst({**run_of(None), 'error': 'ValueError: bad JSON'}):
            self.assertIsNone(L.semantic_analyst_tier(self.stack, 'q', self.ledger))


class ScopedReview(Fixture):
    def test_exact_command_compiles_free(self):
        graph, receipt = L.scoped_review_tier(self.stack, 'Review respiratory sources for patient P-1002')
        self.assertEqual([n['operation'] for n in graph['nodes']], ['cohort', 'domain_review'])
        self.assertEqual((receipt['route'], receipt['new_paid_api_calls'], receipt['steps']), ('scoped_local_candidate', 0, []))
        self.assertEqual(economics(receipt)['new_api_estimate_usd'], 0)

    def test_anything_else_abstains(self):
        self.assertIsNone(L.scoped_review_tier(self.stack, 'How many diabetics?'))
        self.assertIsNone(L.scoped_review_tier(self.stack, 'Review respiratory sources for patient P-9999'))


class StackPlanner(Fixture):
    def plan(self, steps, coverage='supported_observation', unmet=()):
        from clinical_core.agere.clinical.stack_planner import validate
        return validate({'interpretation': 'HF review', 'coverage': coverage, 'unmet_requirements': list(unmet), 'steps': steps,
                         'cohort': None, 'analysis_window': None}, AS_OF)

    def step(self, op, **kw):
        return {'operation': op, 'query': '', 'patient_id': '', 'drug_class': '', 'view': 'all', 'purpose': op, **kw}

    def test_registry_plan_becomes_a_graph(self):
        plan = self.plan([self.step('heart_failure_review'), self.step('evidence_search', query='edema', patient_id='P-1002'),
                          self.step('medication_evidence', drug_class='sglt2')])
        with patch('clinical_core.agere.clinical.stack_planner.propose', lambda *a, **k: (plan, {'cache_hit': False,
                   'cost_estimate': {'estimated_usd': 0.02}})):
            graph, receipt = L.stack_planner_tier(self.stack, 'Review HF patients', self.ledger)
        ops = [(n['operation'], n['inputs']) for n in graph['nodes']]
        self.assertEqual(ops, [('cohort', []), ('analyze', ['population']), ('cohort', []), ('retrieve', ['patient_0']),
                               ('analyze', ['population'])])
        self.assertEqual(graph['nodes'][2]['parameters']['specification']['all'][-1]['value'], 'P-1002')
        self.assertEqual((receipt['route'], receipt['new_paid_api_calls']), ('stack_planner', 1))
        self.assertAlmostEqual(economics(receipt)['known_api_estimate_usd'], 0.02)

    def test_unsupported_plan_is_a_decline_and_offline_is_none(self):
        plan = self.plan([], 'unsupported', ['no risk model'])
        with patch('clinical_core.agere.clinical.stack_planner.propose', lambda *a, **k: (plan, {'cache_hit': True})):
            graph, receipt = L.stack_planner_tier(self.stack, 'q', self.ledger)
            self.assertIsNone(L.stack_planner_tier(self.stack, 'q', None))
        self.assertEqual((graph['coverage'], graph['unmet_requirements'], receipt['new_paid_api_calls']), ('unsupported', ['no risk model'], 0))


class LegacyLadder(Fixture):
    def fast(self, route='local_compiler', nodes=True):
        graph = L._sql_graph('q', 'SELECT 7 AS n', 'count', AS_OF, 'fast') if nodes else \
            L._decline_graph('q', ['x'], AS_OF)
        return graph, {'route': route, 'new_paid_api_calls': 1, 'steps': [{'stage': 'metric_router',
                       'receipt': {'cost_estimate': {'estimated_usd': 0.001}}}]}

    def ladder(self, *results):
        seen = []
        results = list(results)

        def legacy_plan(stack, question, ledger, frontier):
            seen.append(economical_planner.deferring())
            r = results.pop(0)
            if isinstance(r, Exception): raise r
            return r
        return patch.object(economical_planner, 'legacy_plan', legacy_plan), seen

    def test_offline_runs_the_ladder_alone(self):
        p, seen = self.ladder(self.fast())
        with p, self.analyst(run_of(self.fast_answer)) as calls:
            graph, receipt = L.legacy_ladder_tier(SimpleNamespace(**{**vars(self.stack), 'allow_live': False}), 'q', None, None)
        self.assertEqual((receipt['route'], seen, calls['analyze']), ('local_compiler', [False], 0))

    def test_agreement_keeps_the_fast_plan_and_adds_analyst_cost(self):
        p, seen = self.ladder(self.fast())
        with p, self.analyst(run_of(self.fast_answer)) as calls:
            graph, receipt = L.legacy_ladder_tier(self.stack, 'How many patients?', self.ledger, None)
        self.assertEqual(graph['nodes'][0]['parameters']['query'], 'SELECT 7 AS n')
        self.assertEqual((receipt['route'], receipt['new_paid_api_calls'], seen, calls['reconcile']),
                         ('legacy_cost_ladder:local_compiler', 3, [True], []))
        self.assertAlmostEqual(economics(receipt)['known_api_estimate_usd'], 0.011)

    def test_disagreement_is_reconciled_and_the_pick_answers(self):
        rec = {'picked': 'analyst', 'reason': 'r', 'answer': None, 'reconcile_cost_usd': 0.04, 'reconcile_error': None,
               'reconcile_sql': [], 'result': {'new_paid_api_calls': 1, 'tool_calls': []}}
        p, _ = self.ladder(self.fast())
        with p, self.analyst(run_of({**self.fast_answer, 'count': 8}, rows=[{'n': 8}]), rec) as calls:
            graph, receipt = L.legacy_ladder_tier(self.stack, 'How many patients?', self.ledger, None)
        self.assertEqual(calls['reconcile'][0][2], ('analyst', 'against'))
        self.assertIn('patient', graph['nodes'][0]['parameters']['query'])
        self.assertEqual((receipt['route'], receipt['new_paid_api_calls'], receipt['legacy']['picked']),
                         ('legacy_cost_ladder:semantic_analyst', 4, 'analyst'))
        rec['picked'] = 'against'
        p, _ = self.ladder(self.fast())
        with p, self.analyst(run_of({**self.fast_answer, 'count': 8}, rows=[{'n': 8}]), rec):
            graph, receipt = L.legacy_ladder_tier(self.stack, 'How many patients?', self.ledger, None)
        self.assertEqual(graph['nodes'][0]['parameters']['query'], 'SELECT 7 AS n')

    def test_deferred_answers_by_analyst_else_full_ladder(self):
        p, seen = self.ladder(self.fast('deferred_to_analyst', nodes=False))
        with p, self.analyst(run_of(self.fast_answer)):
            graph, receipt = L.legacy_ladder_tier(self.stack, 'How many patients?', self.ledger, None)
        self.assertEqual((receipt['route'], self.ran), ('legacy_cost_ladder:semantic_analyst', []))
        p, seen = self.ladder(self.fast('deferred_to_analyst', nodes=False), self.fast('hosted_verified_sql'))
        with p, self.analyst({**run_of(None), 'error': 'x'}):
            graph, receipt = L.legacy_ladder_tier(self.stack, 'How many patients?', self.ledger, None)
        self.assertEqual((receipt['route'], seen, receipt['legacy']['fallback']), ('legacy_cost_ladder:hosted_verified_sql', [True, False], True))

    def test_not_understood_proposes_nothing(self):
        p, _ = self.ladder(QuestionNotUnderstood('no'))
        with p:
            self.assertIsNone(L.legacy_ladder_tier(SimpleNamespace(**{**vars(self.stack), 'allow_live': False}), 'q', None, None))

    def test_missing_frontier_never_crashes(self):
        with self.assertRaises(QuestionNotUnderstood):
            L._no_frontier({})


class Helpers(Fixture):
    def test_learned_router_hint_ranks_engines(self):
        from clinical_core.agere.clinical import learned_router
        from clinical_core.agere.clinical.capability_registry import PRODUCT
        labels = learned_router.LABELS
        p = np.linspace(0.1, 1.0, len(labels))[None, :]
        self.stack.retriever.encoder = SimpleNamespace(identity='enc')
        with patch.object(learned_router, 'load', lambda: {'encoder_identity': 'enc'}), \
                patch.object(learned_router, 'features', lambda q, e: None), \
                patch.object(learned_router, 'probabilities', lambda m, X: p), patch.dict(os.environ, {'AGERE_ENGINE_LEAN': 'on'}):
            ranked = L.learned_router_hint(self.stack, 'why are readmissions up')
        self.assertEqual(ranked, [x for x in reversed(labels) if x in PRODUCT])
        self.assertEqual(L.learned_router_hint(SimpleNamespace(retriever=SimpleNamespace(encoder=None)), 'q'), [])

    def test_router_order_puts_operator_first_on_a_lean(self):
        order = ('a', 'metric_router', 'operator_router', 'b')
        with patch.object(economical_planner, '_engine_lean', lambda s, q: {'lean': True, 'label': 'explain'}):
            self.assertEqual(L.router_order(self.stack, 'q', order)[0], ('a', 'operator_router', 'metric_router', 'b'))
        with patch.object(economical_planner, '_engine_lean', lambda s, q: {'lean': False}):
            self.assertEqual(L.router_order(self.stack, 'q', order)[0], order)
        with patch.object(economical_planner, '_engine_lean', lambda s, q: None):
            self.assertEqual(L.router_order(self.stack, 'q', order), (order, None))

    def test_vbc_context(self):
        from clinical_core.agere.clinical import vbc_knowledge
        hit = {'id': 'attr-mssp', 'title': 'MSSP assignment', 'citations': [{'publisher': 'CMS', 'url': 'https://cms.gov/x'}]}
        with patch.object(vbc_knowledge, 'context_for', lambda q, **k: [hit]):
            text = L.vbc_context('How are MSSP patients attributed?')
        self.assertIn('MSSP assignment [attr-mssp] (CMS: https://cms.gov/x)', text)
        with patch.object(vbc_knowledge, 'context_for', lambda q, **k: []):
            self.assertEqual(L.vbc_context('q'), '')
        with patch.object(vbc_knowledge, 'context_for', lambda q, **k: 1 / 0):
            self.assertEqual(L.vbc_context('q'), '')


class ModelRoles(unittest.TestCase):
    def test_reports_the_built_cascade_and_ledger(self):
        from clinical_core.agere.clinical import proposers as P
        from clinical_core.agere.clinical.stack_runtime import Stack, ROOT
        roles = Stack.model_roles(SimpleNamespace(allow_live=True))
        self.assertEqual([c['tier'] for c in roles['cascade']], list(P.ORDER))
        self.assertEqual(roles['not_proposers'], P.NOT_PROPOSERS)
        self.assertNotIn('retired_question_routes', roles)
        ledger = json.loads((ROOT/'config/agere/component_ledger.json').read_text())['components']
        self.assertEqual(roles['component_ledger']['components'], len(ledger))
        self.assertEqual(sum(roles['component_ledger']['by_wiring'].values()), len(ledger))
        self.assertTrue(all(isinstance(x, str) for x in roles['planning_order']))  # graph_workbench joins it
        with patch.dict(os.environ, {'AGERE_LOCAL_TYPED': 'off'}):
            self.assertFalse(Stack.model_roles(SimpleNamespace(allow_live=False))['qwen_status']['qwen_typed_interpreter'])
        offline = Stack.model_roles(SimpleNamespace(allow_live=False))
        self.assertFalse(any(c['enabled'] for c in offline['cascade'] if c['paid']))


if __name__ == '__main__':
    unittest.main()
