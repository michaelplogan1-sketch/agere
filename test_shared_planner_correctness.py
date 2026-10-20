"""A–C development regressions. No sealed-bank questions or reference answers."""
import copy
import json
import shutil
import sqlite3
import subprocess
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import test_shared_planner as fixtures
from clinical_core.agere.clinical import (
    age, request_plan as R, request_language as L, shared_planner as S,
    request_vocabulary as V, request_shapes as H, sql_query, learning, conversation)


class Correctness(unittest.TestCase):
    def setUp(self):
        fixtures.SharedPlanner.setUp(self)
        with sqlite3.connect(self.path/'semantic.sqlite3') as db:
            db.executescript("""
                ALTER TABLE patient ADD COLUMN age_latest;
                UPDATE patient SET age_latest=1;
                ALTER TABLE claim ADD COLUMN first_status DEFAULT 'paid';
                ALTER TABLE claim ADD COLUMN final_status DEFAULT 'paid';
                UPDATE claim SET first_status='denied' WHERE ever_denied=1;
                ALTER TABLE appointment ADD COLUMN ever_no_show DEFAULT 0;
                UPDATE appointment SET ever_no_show=1 WHERE final_status='no_show';
                ALTER TABLE condition ADD COLUMN event_date DEFAULT '2029-04-05';
                CREATE TABLE member_month(patient_id,month,payer_id);
                INSERT INTO member_month VALUES ('A','2029-01','P1'),('A','2029-02','P1'),('B','2029-01','P1'),
                    ('C','2029-01','P2'),('OUTSIDE','2029-01','P3');
                CREATE TABLE current_record(patient_id,stream);
                INSERT INTO current_record VALUES ('C','death_records');
            """)
        self.catalog = R.Catalog.load(self.path)
        self.catalog.stream_fields['death_records'] = []
        self.stack.receipts = self.path
        # Tiny-warehouse harness executes the very same registered graph node.
        def graph_run(graph, question=None):
            c = graph['nodes'][0]['parameters']['contract']
            population = sql_query.execute(self.path, {'query': c['membership_query'], 'returns': 'patient_set'},
                                           patient_ids=c['population'], target=self.catalog.target)['patient_ids']
            report = V.execute(self.path, population, graph['nodes'][1]['parameters'])
            return {'answer': report['answer'], 'results': [{'node': graph['nodes'][1], 'result': report}]}
        self.stack.run_graph = graph_run

    ask = fixtures.SharedPlanner.ask

    def test_birth_date_calculation_boundaries_and_stale_age_guard(self):
        for born, cutoff, expected in [('1964-10-20','2029-10-19',64),('1964-10-20','2029-10-20',65),
                                       ('2000-02-29','2029-02-28',28),('2000-02-29','2029-03-01',29),
                                       (None,'2030-01-01',None),('bad','2030-01-01',None),
                                       ('2031-01-01','2030-01-01',None)]:
            self.assertEqual(age.age_at(born, cutoff), expected)
        answer = self.ask('Count patients over 65')['answer']
        self.assertEqual(answer['count'], 3)
        self.assertNotIn('age_latest', self.ask('Average age of patients')['request_contract']['query'])
        self.assertEqual(self.ask('Average age of patients')['answer']['value'], 76.25)
        with self.assertRaisesRegex(ValueError, 'Stale feed age'):
            sql_query.execute(self.path, {'query': 'SELECT avg(age_latest) FROM patient', 'returns': 'value'}, target=self.catalog.target)

    def test_claims_only_ed_and_coverage_share_the_exact_filter(self):
        with sqlite3.connect(self.catalog.target) as db:
            db.execute("INSERT INTO utilization_event VALUES('D','ed_visit','2029-02-01','claim',0)")
            db.execute("INSERT INTO utilization_event VALUES('D','ed_visit','2028-02-01','claim',0)")
        for noun in ('ED visits', 'ER visits', 'emergency visits'):
            answer = self.ask('Count '+noun+' in 2029')['answer']
            self.assertEqual(answer['count'], 5)
            self.assertIn('D', answer['patient_ids'])
            self.assertEqual(answer['source_coverage']['events'], 5)
            self.assertEqual(answer['source_coverage']['outside_ehr'], 1)

    def test_governed_values_and_composed_denominators(self):
        self.assertEqual(self.ask('denial rate 2029')['answer']['value'], 80)
        self.assertEqual(self.ask('no-show rate in 2029')['answer']['value'], 50)
        self.assertEqual(self.ask('readmission rate 2029')['answer']['value'], 100/3)
        self.assertEqual(self.ask('LOS 2029')['answer']['value'], 17/3)
        rows = self.ask('denial rate 2029 by payer')['answer']['table']
        self.assertEqual(rows, [{'payer': 'P1', 'value': 100*2/3}, {'payer': 'P2', 'value': 100.0}, {'payer': 'P3', 'value': 100.0}])
        self.assertEqual(self.ask('denial rate 2029 for payer = "P1" excluding ckd')['answer']['value'], 100*2/3)
        self.assertEqual(self.ask('no-show rate in 2028')['answer']['value'], None)
        # PMPM must scope both numerator and denominator to the same payer.
        pmpm = self.ask('PMPM in 2029 by payer')['answer']['table']
        with sqlite3.connect(self.catalog.target) as db:
            expected = db.execute("SELECT 350.0/(2*(365.0/30.4375))").fetchone()[0]
        self.assertAlmostEqual(pmpm[0]['value'], expected)

    def test_governed_drug_fill_definition_and_period(self):
        with sqlite3.connect(self.catalog.target) as db:
            db.execute("INSERT INTO medication VALUES('C','statin','listed','active',0,'2029-01-01')")
            db.execute("INSERT INTO medication VALUES('D','statin','dispensed','dispensed',1,'2029-01-01')")
        self.assertEqual(self.ask('patients on a statin')['answer']['patient_ids'], ['A','B'])
        self.assertEqual(self.ask('patients on a statin in 2028')['answer']['patient_ids'], [])
        self.assertEqual(self.ask('patients with diabetes in 2029 excluding ckd')['answer']['patient_ids'], ['A','B'])

    def test_problem_list_diagnoses_survive_unknown_normalization_flags(self):
        with sqlite3.connect(self.catalog.target) as db:
            db.execute('ALTER TABLE condition ADD COLUMN diagnosis_basis TEXT')
            for concept in ('diabetes', 'chronic_kidney_disease', 'heart_failure'):
                db.executemany('INSERT INTO condition VALUES(?,?,?,?,?)', [
                    ('B', concept, None, '2029-01-01', 'ehr_problem_list'),
                    ('D', concept, None, '2029-01-01', 'laboratory_evidence'),
                    ('D', concept, 0, '2029-01-01', None)])
        self.catalog = R.Catalog.load(self.path)
        for name in ('diabetes', 'ckd', 'heart failure'):
            answer = self.ask('List patients with '+name)['answer']
            self.assertIn('B', answer['patient_ids'])
            self.assertNotIn('D', answer['patient_ids'])
            self.assertTrue(any('lab-inferred' in x for x in answer['limitations']))

    def test_analytical_summaries_do_not_turn_inputs_into_action_lists(self):
        from clinical_core.agere.deliverables import answer_context as A
        for q in ('What was our no-show rate in 2029?',
                  'Average length of stay by payer in 2029', 'PMPM by payer in 2029', 'ED visits in 2029'):
            result = self.ask(q)
            self.assertEqual(result['answer']['status'], 'answered')
            self.assertFalse(result['answer']['actionable_cohort'])
            self.assertEqual(result['answer']['patient_ids'], [])
            self.assertEqual(result['answer']['analysis_population_count'], 5)
            context = A.capture(self.path, q, result)
            self.assertEqual(A.load(self.path, context['id'])['patient_ids'], [])
            with self.assertRaisesRegex(ValueError, 'analytical summary'):
                A.create_work(self.path, {'context_id': context['id'], 'program': 'custom',
                                         'patient_ids': ['A'], 'owner': 'fixture', 'note': ''})
        # No selected membership is not permission to substitute engine inputs.
        answer = S.engine_answer({}, {'answer': {'status': 'answered', 'answer_type': 'value', 'value': 4}}, ['A','B'])
        self.assertEqual(answer['patient_ids'], [])
        self.assertFalse(answer['actionable_cohort'])

    def test_registered_definitions_cannot_be_model_rewritten(self):
        request = L.parse(self.catalog, 'denial rate 2029')
        request['analyses'][0]['parameters']['definition_digest'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'definition changed'):
            R.compile_request(self.catalog, 'denial rate 2029', request)
        from clinical_core.agere.clinical.analysis_graph import OPERATIONS
        self.assertIn('governed_metric', OPERATIONS)

    def test_raf_missing_birth_date_is_unknown_not_reported_age(self):
        import test_risk_adjustment as risk_fixtures
        from clinical_core.agere.clinical import risk_adjustment
        path = risk_fixtures.fixture()
        self.addCleanup(shutil.rmtree, path)
        with sqlite3.connect(path/'semantic.sqlite3') as db:
            db.execute("UPDATE patient SET birth_date=NULL, age_reported=90 WHERE patient_id='A'")
        report = risk_adjustment.execute(path, ['A'], {}, risk_fixtures.AS_OF)
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['population']['patients_in_scope'], 1)
        self.assertEqual(report['population']['patients_scored'], 0)
        self.assertEqual(report['unknown_age_patient_ids'], ['A'])
        self.assertIsNone(report['population']['payment_raf']['mean'])

    def test_production_graph_executes_governed_node_and_preserves_version(self):
        import test_analysis_graph as graph_fixtures
        from clinical_core.agere.clinical import analysis_graph, semantic_layer
        fixture = graph_fixtures.GraphExecution()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        semantic_layer.build(fixture.root)
        with sqlite3.connect(fixture.root/'semantic.sqlite3') as db:
            db.execute("INSERT INTO appointment(appointment_id,patient_id,scheduled_date,kept,ever_no_show) VALUES('a','p0','2026-05-01',1,0)")
            db.execute("INSERT INTO appointment(appointment_id,patient_id,scheduled_date,kept,ever_no_show) VALUES('b','p1','2026-05-01',0,1)")
        fixture.stack.allow_live = False
        fixture.stack.run_graph = lambda graph, question=None: analysis_graph.execute(fixture.stack, graph, question=question)
        answer = S.compose(fixture.stack, 'no-show rate in 2026', model=Mock(side_effect=AssertionError('Model called')))['answer']
        self.assertEqual(answer['value'], 50)
        self.assertEqual(answer['computed_as']['metric_id'], 'no_show_rate')
        self.assertEqual(answer['computed_as']['version'], 2)

    def teacher_request(self, question):
        return {'source': {'table':'claim','quote':'claims'},
                'select':[{'kind':'count','alias':'claims','quote':'tally'}],
                'where': L.combine('and', [
                    L.atom('claim.allowed_cents','>',100,'allowed cents exceed 100'),
                    L.atom('claim.service_date','>=','2029-01-01','in 2029'),
                    L.atom('claim.service_date','<','2030-01-01','in 2029')], question),
                'group_by':[], 'order_by':[], 'limit':None, 'unresolved':[]}

    def learn(self):
        question = 'Give me the tally of claims whose allowed cents exceed 100 in 2029'
        plan = self.teacher_request(question)
        teacher = Mock(side_effect=[(json.dumps(plan), {'api_usd': .1, 'api_cost_known': True}),
                                    (json.dumps({'equivalent':True,'issues':[]}), {'api_usd': .1, 'api_cost_known': True})])
        teacher.identity = 'fixture_hosted'
        self.stack.allow_live = True
        with patch('clinical_core.agere.clinical.nl2sql.HostedModel', return_value=teacher), \
             patch('clinical_core.agere.gateway.BudgetLedger'):
            result = S.compose(self.stack, question)
        self.assertEqual(teacher.call_count, 2)
        self.assertEqual(result['new_paid_api_calls'], 2)
        return question, result

    def test_checked_shape_new_question_scope_policy_and_correction(self):
        question, first = self.learn()
        ident = first['learned_request_shape']
        new = question.replace('100','15000')
        result = self.ask(new)
        self.assertEqual(result['planner_receipt']['interpretation']['interpreter'], 'checked_request_shape')
        self.assertEqual(result['answer']['count'], 2)
        self.assertEqual(result['new_paid_api_calls'], 0)
        moved = self.ask(new.replace('2029','2028'))
        self.assertEqual(moved['answer']['count'], 0)
        self.assertIsNone(H.lookup(self.path, self.catalog, new, 'changed-policy'))
        self.assertIsNone(H.lookup(self.path, self.catalog, new, S.policy_identity(), ['A']))
        changed_source = copy.deepcopy(self.catalog)
        changed_source.identity['source_digest'] = 'new-source'
        self.assertIsNone(H.lookup(self.path, changed_source, new, S.policy_identity()))
        self.assertIsNone(H.lookup(self.path, self.catalog, new+' excluding preferred mornings', S.policy_identity()))
        learning.record_correction(self.path, '  '+new.upper().replace(' ', '  ')+'?', 'Wrong interpretation')
        self.assertFalse(H.valid(self.path, ident))
        self.assertIsNone(H.lookup(self.path, self.catalog, new, S.policy_identity()))

    def test_partial_or_unaccepted_interpretation_never_seeds_shape(self):
        question = 'Give me the tally of claims whose allowed cents exceed 100 in 2029'
        contract = R.compile_request(self.catalog, question, self.teacher_request(question))
        self.assertIsNone(H.save(self.path,self.catalog,contract,
                                {'review':'model_semantic_review','execution_accepted':False},S.policy_identity()))
        self.assertFalse(list(H._db(self.path).execute('SELECT id FROM request_shapes')))

    def test_normalized_correction_invalidates_original_exact_cache(self):
        question, first = self.learn()
        learning.record_correction(self.path, '  '+question.upper().replace(' ', '  ')+'?', 'Wrong interpretation')
        self.assertFalse(H.valid(self.path, first['learned_request_shape']))
        teacher = Mock(side_effect=[
            (json.dumps(self.teacher_request(question)), {'api_usd': .1, 'api_cost_known': True}),
            (json.dumps({'equivalent': True, 'issues': []}), {'api_usd': .1, 'api_cost_known': True})])
        teacher.identity = 'fixture_hosted'
        with patch('clinical_core.agere.clinical.nl2sql.HostedModel', return_value=teacher), \
             patch('clinical_core.agere.gateway.BudgetLedger'):
            result = S.compose(self.stack, question)
        self.assertEqual(teacher.call_count, 2)
        self.assertNotIn('learned_request_shape', result)
        self.assertFalse(result['planner_receipt']['interpretation'].get('exact_contract_cache_hit'))

    def test_governed_schema_discovery_and_failed_second_call_accounting(self):
        context = S.schema_slice(self.catalog, 'denial rate', operations=['governed_metric'])
        self.assertIn('parameter_schema', context['selected_operator_contracts']['governed_metric'])
        self.assertIn('drug_fill', context['named_concepts'])
        model = Mock(side_effect=[
            (json.dumps({'need_operations': ['governed_metric']}), {'api_usd': .25, 'api_cost_known': True}),
            RuntimeError('Provider failed before returning a receipt')])
        with self.assertRaises(RuntimeError) as raised:
            S.checked_interpret(self.catalog, 'An unfamiliar business question', model=model)
        self.assertEqual(raised.exception.planner_receipt['new_paid_api_calls'], 2)
        self.assertEqual(raised.exception.planner_receipt['known_api_estimate_usd'], .25)
        self.assertFalse(raised.exception.planner_receipt['api_cost_known'])

    def test_failed_hosted_attempts_are_counted_in_conversation(self):
        model = Mock(return_value=('invalid json', {'api_usd': .25, 'api_cost_known': True}))
        self.stack.compose = lambda q, **kw: S.compose(self.stack, q, model=model)
        with self.assertRaises(S.QuestionNotUnderstood) as raised:
            conversation.handle_compose(self.stack, {'question':'An unfamiliar business question','conversation_id':'fail'})
        receipt = raised.exception.planner_receipt
        self.assertEqual(receipt['new_paid_api_calls'], 6)
        self.assertEqual(receipt['known_api_estimate_usd'], 1.5)
        with sqlite3.connect(self.path/'learning.sqlite3') as db:
            self.assertEqual(db.execute('SELECT status,paid_calls,usd FROM outcomes').fetchone(), ('not_understood',6,1.5))
        self.assertFalse(S.attempt_receipt([{'api_usd':0.1}])['api_cost_known'])

    def test_interpretation_releases_service_lock_and_local_busy_is_immediate(self):
        lock = threading.RLock()
        self.stack.answer_lock = lock
        entered, release, fast_done = threading.Event(), threading.Event(), threading.Event()
        def slow():
            with lock:
                with S.interpretation_window(self.stack):
                    entered.set()
                    release.wait(3)
                self.assertTrue(lock._is_owned())
        worker = threading.Thread(target=slow); worker.start()
        self.assertTrue(entered.wait(1))
        def fast():
            with lock:
                self.assertEqual(self.ask('Count patients')['answer']['count'], 5)
                fast_done.set()
        other = threading.Thread(target=fast); other.start()
        try: self.assertTrue(fast_done.wait(1))
        finally: release.set(); worker.join(); other.join()
        with S._MODEL_LOCK:
            before = time.perf_counter()
            with self.assertRaisesRegex(S.QuestionNotUnderstood, 'busy'):
                S.isolated_local(self.catalog, 'unfamiliar', None)
            self.assertLess(time.perf_counter()-before, .2)
        with patch.object(S.subprocess, 'run', side_effect=subprocess.TimeoutExpired('worker', .01)):
            with self.assertRaisesRegex(S.QuestionNotUnderstood, 'time limit'):
                S.isolated_local(self.catalog, 'unfamiliar', None)
        self.assertFalse(S._MODEL_LOCK.locked())


if __name__ == '__main__':
    unittest.main()
