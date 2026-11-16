"""Follow-up chains: every patient-level count keeps who it counted, so "of those, how many ..." narrows it.

No models, no network: the planner model, hosted tiers and local LLMs are patched out or off.
"""
import os
import sqlite3
import unittest
from unittest.mock import Mock, patch

import test_shared_planner as fixtures
from clinical_core.agere.clinical import (conversation as C, general_analysis as G, membership as M, request_plan as R,
                                          shared_planner as S, sql_query)
from clinical_core.agere.clinical.question_input import QuestionNotUnderstood
from clinical_core.agere.deliverables import answer_context as A

FOLLOW = 'Of those, how many are over 65?'


class Rewrite(unittest.TestCase):
    def test_patient_counts_rewrite_to_the_counted_expression(self):
        cases = [
            ("SELECT COUNT(DISTINCT c.patient_id) AS n FROM condition c WHERE c.concept='diabetes'", 'count',
             "SELECT DISTINCT c.patient_id AS patient_id FROM condition c WHERE c.concept='diabetes'", 'counted'),
            ("SELECT COUNT(*) FROM patient p WHERE p.sex='F'", 'count',
             "SELECT DISTINCT p.patient_id AS patient_id FROM patient p WHERE p.sex='F'", 'counted'),
            ("SELECT ROUND(100.0*COUNT(DISTINCT CASE WHEN m.v>9 THEN m.patient_id END)/COUNT(DISTINCT m.patient_id),1) FROM m", 'value',
             'SELECT DISTINCT CASE WHEN m.v>9 THEN m.patient_id END AS patient_id FROM m', 'numerator'),
        ]
        for sql, returns, expected, role in cases:
            with self.subTest(sql=sql):
                self.assertEqual(M.rewrite(sql, returns)[:2], (expected, role))

    def test_staged_step_counts_and_scoping_copies(self):
        staged = "WITH a AS (SELECT DISTINCT patient_id FROM condition), b AS (SELECT COUNT(*) AS n FROM a) SELECT * FROM b"
        self.assertIn('agere_members AS (SELECT DISTINCT a.patient_id', M.rewrite(staged, 'count')[0])
        scoped = ("WITH appointment AS (SELECT * FROM main.appointment WHERE patient_id IN (SELECT patient_id FROM population)) "
                  "SELECT final_status, COUNT(*) AS n FROM appointment GROUP BY final_status")
        self.assertIsNone(M.rewrite(scoped, 'table'))  # a scoped copy of an event table is still event rows

    def test_event_and_provider_rows_never_become_patients(self):
        for sql, returns in [("SELECT COUNT(*) FROM condition WHERE concept='x'", 'count'),
                             ("SELECT final_status, COUNT(*) FROM appointment GROUP BY final_status", 'table'),
                             ('SELECT COUNT(*) FROM technician', 'count'),
                             ("SELECT pcp, COUNT(DISTINCT patient_id) FROM patient GROUP BY pcp HAVING COUNT(*) > 2", 'table'),
                             ('SELECT SUM(allowed_cents) FROM claim', 'value')]:
            with self.subTest(sql=sql):
                self.assertIsNone(M.rewrite(sql, returns))


class Chains(unittest.TestCase):
    setUp = fixtures.SharedPlanner.setUp

    def run_sql(self, query, returns):
        report = sql_query.execute(self.path, {'query': query, 'returns': returns}, target=self.catalog.target)
        return report, M.derive(self.path, {'query': query}, report, target=self.catalog.target)

    def test_derive_checks_the_count_and_grouped_union(self):
        _, found = self.run_sql("SELECT COUNT(DISTINCT patient_id) FROM condition WHERE concept='diabetes' AND is_diagnosis=1", 'count')
        self.assertEqual(found['patient_ids'], ['A', 'B', 'C'])
        _, found = self.run_sql('SELECT pcp_practice_id, COUNT(*) AS n FROM patient GROUP BY pcp_practice_id', 'table')
        self.assertEqual(found['patient_ids'], ['A', 'B', 'C', 'D', 'OUTSIDE'])
        _, found = self.run_sql("SELECT 100.0*COUNT(DISTINCT CASE WHEN coverage_type='medicare' THEN patient_id END)/COUNT(*) FROM patient", 'value')
        self.assertEqual((found['patient_ids'], found['role']), (['A', 'C', 'OUTSIDE'], 'numerator'))
        _, found = self.run_sql("SELECT COUNT(*) FROM condition WHERE concept='diabetes'", 'count')
        self.assertIsNone(found)  # five diagnosis rows are not five patients

    def staged_count(self):
        """A count answered by the shared planner's staged-analysis path (the model is an authored fixture)."""
        question = 'How many patients have a recorded diabetes diagnosis?'
        spec = {'steps': [{'name': 'diabetic', 'purpose': 'Patients with a diabetes diagnosis',
                           'sql': "SELECT DISTINCT patient_id FROM condition WHERE concept='diabetes' AND is_diagnosis=1"},
                          {'name': 'answer', 'purpose': 'Count them', 'sql': 'SELECT COUNT(*) AS patients FROM diabetic'}],
                'returns': 'count', 'unresolved': [],
                'requirements': [{'quote': question, 'applied_in': ['diabetic', 'answer'], 'how': 'Diagnosis rows, distinct patients'}]}
        contract = G.prepare(self.catalog, question, spec)
        receipt = {'version': S.VERSION, 'policy_identity': S.policy_identity(), 'request_contract': contract,
                   'interpretation': {'execution_accepted': True}, 'new_paid_api_calls': 0}
        return G.execute(self.stack, question, G.graph_for(contract), receipt, economics=S.economics)

    def test_shared_planner_count_then_follow_up_narrows(self):
        result = self.staged_count()
        self.assertEqual((result['answer']['count'], result['answer']['patient_ids']), (3, ['A', 'B', 'C']))
        saved = A.capture(self.path, result['question'], result)
        self.assertTrue(saved['result_scope_available'])
        self.stack.compose = lambda q, **kw: S.compose(self.stack, q, model=Mock(side_effect=AssertionError('No model')), **kw)
        out = C.handle_compose(self.stack, {'question': FOLLOW, 'conversation_id': 'chain', 'answer_context_id': saved['id']})
        self.assertEqual(out['answer']['count'], 2)  # A (70) and C (80); B is exactly 65
        self.assertEqual(out['answer']['patient_ids'], ['A', 'C'])
        self.assertEqual(out['cohort_transform']['input_patient_ids'], ['A', 'B', 'C'])
        third = C.handle_compose(self.stack, {'question': 'Of those, how many have chronic kidney disease?', 'conversation_id': 'chain'})
        self.assertEqual((third['answer']['count'], third['answer']['patient_ids']), (1, ['C']))

    def test_conditional_count_retains_its_numerator(self):
        question = 'Count patients and how many are on medicare'
        plan = {'source': {'table': 'patient', 'quote': 'patients'},
                'select': [{'kind': 'count', 'alias': 'medicare', 'quote': 'how many',
                            'where': {'op': '=', 'field': 'patient.coverage_type', 'value': 'medicare', 'quote': 'on medicare'}}],
                'where': None, 'group_by': [], 'order_by': [], 'limit': None, 'unresolved': []}
        contract = R.compile_request(self.catalog, question, plan)
        members = sql_query.execute(self.path, {'query': contract['membership_query'], 'returns': 'patient_set'},
                                    target=self.catalog.target)['patient_ids']
        self.assertEqual(members, ['A', 'C', 'OUTSIDE'])

    def test_nonpatient_summary_stays_a_summary(self):
        result = self.staged_count()
        result['answer'] = {**result['answer'], 'patient_ids': []}
        saved = A.capture(self.path, result['question'], result)
        self.assertFalse(saved['result_scope_available'])
        with self.assertRaises(QuestionNotUnderstood):
            A.reference_scope(A.load(self.path, saved['id']))

    def test_capture_retains_ids_for_a_checked_graph_answer_without_them(self):
        query = "SELECT COUNT(DISTINCT patient_id) AS patients FROM condition WHERE concept='diabetes' AND is_diagnosis=1"
        graph = {'interpretation': 'q', 'coverage': 'supported_observation', 'unmet_requirements': [],
                 'nodes': [{'id': 'analyst_0', 'operation': 'sql', 'inputs': [], 'parameters': {'query': query, 'returns': 'count'},
                            'purpose': 'Independent analyst query'}]}
        contract = {'kind': 'checked_graph', 'graph': graph, 'source_identity': self.catalog.identity, 'id': 'x'}
        result = {'question': 'How many diabetics?', 'graph': graph, 'request_contract': contract,
                  'answer': {'status': 'answered', 'answer_type': 'count', 'count': 3, 'patient_ids': [], 'missing': []},
                  'planner_receipt': {'route': 'hybrid_analysts', 'interpretation': {'interpreter': 'hybrid_analysts'}}}
        saved = A.capture(self.path, result['question'], result)
        self.assertEqual(saved['patients'], 3)
        self.assertEqual(A.load(self.path, saved['id'])['patient_ids'], ['A', 'B', 'C'])
        self.assertEqual(result['answer']['patient_ids'], [])  # stored for follow-ups, not added to the shown answer


class CascadeChain(unittest.TestCase):
    """A count proposed by a cascade tier (checked_graph) executes through analysis_graph and keeps its patients."""
    def setUp(self):
        import test_analysis_graph as graph_fixtures
        from clinical_core.agere.clinical import analysis_graph, semantic_layer
        self.fixture = graph_fixtures.GraphExecution()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        semantic_layer.build(self.fixture.root)
        self.path = self.fixture.root
        with sqlite3.connect(self.path/'semantic.sqlite3') as db:
            db.executemany("UPDATE patient SET birth_date=? WHERE patient_id=?",
                           [('1950-01-01', 'p0'), ('1980-01-01', 'p1'), ('1940-01-01', 'p2'), ('1990-01-01', 'p3')])
            db.executemany("INSERT INTO condition(record_id,patient_id,source_table,concept) VALUES(?,?,'conditions','diabetes')",
                           [(f'c{i}', f'p{i}') for i in range(4)])
        self.stack = self.fixture.stack
        self.stack.allow_live, self.stack.shared_request_planning = False, True
        self.stack.run_graph = lambda graph, question=None: analysis_graph.execute(self.stack, graph, question=question)
        self.stack.compose = lambda q, **kw: S.compose(self.stack, q, **kw)
        skip = patch.dict(os.environ, {'AGERE_PROPOSER_SKIP': ','.join(n for n in __import__(
            'clinical_core.agere.clinical.proposers', fromlist=['ORDER']).ORDER if n != 'exact_local_plan')})
        skip.start(); self.addCleanup(skip.stop)

    def test_cascade_count_then_follow_up_narrows(self):
        first = 'How many patients with diabetes are there in our records?'
        query = "SELECT COUNT(DISTINCT patient_id) AS patients FROM condition WHERE concept = 'diabetes'"
        graph = {'interpretation': first, 'coverage': 'supported_observation', 'unmet_requirements': [],
                 'nodes': [{'id': 'count', 'operation': 'sql', 'inputs': [], 'parameters': {'query': query, 'returns': 'count'},
                            'purpose': 'Patients with diabetes'}]}
        original = S.checked_interpret
        def grammar(catalog, question, **kw):
            if question == first: raise QuestionNotUnderstood('Deliberate decline so a cascade tier answers')
            return original(catalog, question, **kw)
        with patch.object(S, 'checked_interpret', side_effect=grammar), \
             patch('clinical_core.agere.clinical.economical_planner.Ladder.exact_local',
                   return_value=(graph, {'route': 'exact_local_plan', 'new_paid_api_calls': 0, 'steps': []})):
            result = C.handle_compose(self.stack, {'question': first, 'conversation_id': 'cascade'})
            self.assertEqual(result['request_contract']['kind'], 'checked_graph')
            self.assertEqual((result['answer']['count'], result['answer']['patient_ids']), (4, ['p0', 'p1', 'p2', 'p3']))
            self.assertTrue(result['action_context']['result_scope_available'])
            out = C.handle_compose(self.stack, {'question': FOLLOW, 'conversation_id': 'cascade'})
        self.assertEqual(out['answer']['count'], 2)  # p0 and p2 at the 2026 snapshot
        self.assertEqual(out['answer']['patient_ids'], ['p0', 'p2'])
        self.assertEqual(out['cohort_transform']['input_patient_ids'], ['p0', 'p1', 'p2', 'p3'])


if __name__ == '__main__':
    unittest.main()
