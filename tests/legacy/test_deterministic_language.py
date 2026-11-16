"""Model-free wording/meaning checks on a separate nonclinical warehouse."""
import copy
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from clinical_core.agere.clinical import request_plan as R, request_language as L, shared_planner as S, conversation
from clinical_core.agere.clinical.question_input import QuestionNotUnderstood


class DeterministicLanguage(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)
        with sqlite3.connect(self.path/'semantic.sqlite3') as db:
            db.executescript('''
            CREATE TABLE shipment(shipment_id TEXT,final_status TEXT,scheduled_date TEXT);
            INSERT INTO shipment VALUES('A','dispatched','2029-03-01'),('B','waiting','2029-03-01'),
              ('C','dispatched','2028-03-01'),('D','dispatched','2029-09-01');
            CREATE TABLE invoice(invoice_id TEXT,allowed_cents INTEGER,service_date TEXT,final_status TEXT);
            INSERT INTO invoice VALUES('A',100,'2029-02-01','paid'),('B',250,'2029-03-01','paid'),
              ('C',999,'2028-02-01','unpaid');
            CREATE TABLE device(device_id TEXT,thermal_load REAL,measured_at TEXT);
            INSERT INTO device VALUES('A',8,'2029-02-01'),('B',12,'2029-03-01'),('C',2,'2028-02-01');
            CREATE TABLE technician(technician_id TEXT,specialty TEXT);
            INSERT INTO technician VALUES('A','Field Services'),('B','Bench Repair'),('C','Field Services');
            CREATE TABLE clock_test(record_id TEXT,scheduled_date TEXT,scheduled_at TEXT);
            INSERT INTO clock_test VALUES('A','2029-01-01','2028-01-01');
            ''')
        self.catalog=R.Catalog.load(self.path)
        self.stack=SimpleNamespace(path=self.path,allow_live=True,shared_request_planning=True)
        self.stack.compose=lambda q,**kwargs:S.compose(self.stack,q,**kwargs)
        # These tests cover the deterministic tier alone; the proposer cascade behind it is covered by
        # test_component_reachability and the proposer tests.
        cascade=patch.dict(os.environ,{'AGERE_PROPOSERS':'off'});cascade.start();self.addCleanup(cascade.stop)
        for target in ('clinical_core.agere.clinical.nl2sql.HostedModel',
                       'clinical_core.agere.clinical.nl2sql.LocalModel.get',
                       'clinical_core.agere.clinical.shared_planner.isolated_local','urllib.request.urlopen'):
            guard=patch(target,side_effect=AssertionError('Language model/network must not be used'))
            mocked=guard.start();self.addCleanup(guard.stop);self.addCleanup(mocked.assert_not_called)

    def answer(self,q):
        result=S.compose(self.stack,q)
        self.assertEqual(result['answer']['status'],'answered')
        self.assertEqual(result['new_paid_api_calls'],0)
        return result['answer']

    def test_unfamiliar_count_wording_and_new_source_labels(self):
        questions=[
            'How many shipments had a final recorded status of dispatched and were scheduled in 2029?',
            'Could you tally up shipments that had a final status of dispatched and were scheduled during 2029?',
            'Please tell me the number of shipments where final_status equals dispatched and scheduled_date is in 2029',
            'I would like you to count shipments with a final reported status of dispatched scheduled in 2029',
        ]
        for q in questions:
            with self.subTest(q=q):self.assertEqual(self.answer(q)['count'],2)

    def test_polarity_and_clock_mutations_change_the_answer(self):
        self.assertEqual(self.answer('Count shipments where final status is not dispatched scheduled in 2029')['count'],1)
        self.assertEqual(self.answer('Count shipments without final status of dispatched scheduled in 2029')['count'],1)
        self.assertEqual(self.answer('Count shipments with final status of dispatched scheduled in 2028')['count'],1)
        self.assertEqual(self.answer('Count shipments with final status of waiting scheduled in 2028')['count'],0)

    def test_aggregate_units_and_fronted_scope_on_different_schema(self):
        q='What was the total recorded allowed amount on invoices with service dates in 2029, in cents?'
        self.assertEqual(self.answer(q)['value'],350)
        q='For devices whose thermal load is no more than 10 and measured at is in 2029, what is the average thermal load?'
        self.assertEqual(self.answer(q)['value'],8)
        q='For devices whose thermal load is more than 10 and measured at is in 2029, what is the average thermal load?'
        self.assertEqual(self.answer(q)['value'],12)

    def test_category_case_and_inverted_word_order_are_grounded_in_received_values(self):
        self.assertEqual(self.answer('Count technicians who have field services as their listed specialty')['count'],2)
        self.assertEqual(self.answer('Count technicians whose listed specialty is "Field Services"')['count'],2)
        # Quoted category values retain exact literal storage semantics.
        self.assertEqual(self.answer('Count technicians whose specialty is "field services"')['count'],0)

    def test_two_entity_grains_save_and_reopen_without_a_union_cohort(self):
        q='Give me both the number of shipments with final status of dispatched scheduled in 2029 and the number of technicians whose specialty is "Field Services"'
        result=conversation.handle_compose(self.stack,{'question':q,'conversation_id':'no-model'})
        self.assertEqual(result['answer']['status'],'answered')
        self.assertEqual([s['answer']['count'] for s in result['answer']['sections']],[2,2])
        self.assertEqual(result['answer']['patient_ids'],[])
        self.assertFalse(result['answer']['actionable_cohort'])
        from clinical_core.agere.deliverables import answer_context as A
        context = A.load(self.path, result['action_context']['id'])
        self.assertFalse(A.scope_options(context)['result_scope_available'])
        self.assertFalse(result['action_context']['result_scope_available'])
        with self.assertRaises(QuestionNotUnderstood): A.reference_scope(context)
        self.assertEqual(result['new_paid_api_calls'],0)
        again=conversation.handle_compose(self.stack,{'question':q,'conversation_id':'no-model'})
        self.assertEqual([s['answer']['count'] for s in again['answer']['sections']],[2,2])
        self.assertTrue(again['planner_receipt']['interpretation']['exact_contract_cache_hit'])

    def test_nonpatient_count_does_not_become_an_empty_patient_cohort(self):
        from clinical_core.agere.deliverables import answer_context as A
        result=conversation.handle_compose(self.stack,{'question':'Count technicians whose specialty is "Field Services"','conversation_id':'provider-scope'})
        self.assertEqual(result['answer']['count'],2)
        self.assertFalse(result['action_context']['result_scope_available'])
        context=A.load(self.path,result['action_context']['id'])
        with self.assertRaises(QuestionNotUnderstood):A.reference_scope(context)
        self.assertFalse(A.listing(self.path)[0]['result_scope_available'])

    def test_ambiguity_unknown_scope_and_unsupported_units_do_not_call_a_model(self):
        self.stack.allow_live=False  # No hosted fallback when it is disabled on the server.
        questions=[
            'Count shipments that are urgent',
            'Count shipments in 2029 and count invoices',
            'Count clock tests scheduled in 2029',
            'Total allowed amount on invoices in 2029, in dollars',
            'Count technicians who have not Field Services as their specialty',
            'Count devices where thermal load > 10 and thermal load < 20 or thermal load = 2',
        ]
        for q in questions:
            with self.subTest(q=q),self.assertRaises(QuestionNotUnderstood):S.compose(self.stack,q)

    def test_comparator_defense_checks_worded_thresholds(self):
        q='Count devices where thermal load is no more than 10'
        plan=L.parse(self.catalog,q);plan['where']['op']='>'
        with self.assertRaisesRegex(R.PlanError,'comparator'):R.compile_request(self.catalog,q,plan)

    def test_explicit_frontier_parameter_does_not_bypass_deterministic_interpretation(self):
        result=S.compose(self.stack,'Count shipments',planning='frontier')
        self.assertEqual(result['answer']['count'],4)
        self.assertEqual(result['new_paid_api_calls'],0)


if __name__=='__main__':unittest.main()
