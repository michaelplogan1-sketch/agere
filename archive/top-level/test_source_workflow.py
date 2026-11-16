"""Ordinary fulfillment preserves partial source-review delivery and its costs."""
import copy
import json
import sqlite3
import unittest
from unittest.mock import patch

import test_source_disposition as source_fixtures
from clinical_core.agere.deliverables import answer_context as A, workflow as W
from clinical_core.agere.clinical import typed_document_review as T


class SourceWorkflow(unittest.TestCase):
    def setUp(self):
        self.fixture = source_fixtures.SourceDisposition('test_suspension_blocks_program_work_but_not_unrelated_answers')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.path, self.stack = self.fixture.path, self.fixture.stack
        context = A.load(self.path, self.fixture.key)
        self.result = {key: copy.deepcopy(context.get(key)) for key in
                       ('answer', 'graph', 'investigation', 'planner_receipt')}
        self.result['workflow'] = [
            {'kind': 'deliverable', 'title': 'Patient source review PDF', 'template': 'command', 'purpose': 'inspection'},
            {'kind': 'simulation', 'title': 'Program review', 'program': 'pharmd'}]
        self.result['answer']['table']=[{'patient_id':pid} for pid in context['patient_ids']]

    def test_ordinary_fulfillment_keeps_context_and_delivers_source_report(self):
        self.assertEqual(self.fixture.suspend(), 1)
        result = W.fulfill(self.stack, self.fixture.question, self.result)
        self.assertIsNotNone(result.get('action_context'))
        self.assertEqual(result['answer']['status'], 'partial')
        self.assertEqual(result['answer']['captured_status'], 'answered')
        self.assertTrue(result['source_review_required'])
        self.assertEqual(result['workflow'][0]['generation'], 'saved_report')
        self.assertEqual(result['workflow'][0]['status'], 'ready')
        self.assertEqual(result['workflow'][1]['status'], 'blocked')
        self.assertEqual(result.get('new_paid_api_calls', 0), 0)
        self.assertTrue(result['workflow_continuation']['attempts'][0]['rendered'])
        context = A.load(self.path, result['action_context']['id'], current=True, source_review=True)
        self.assertEqual(A.work(self.path, context), [])

    def test_explicit_frontier_choice_is_blocked_without_substitution_or_dispatch(self):
        self.assertEqual(self.fixture.suspend(), 1)
        with patch.object(A, 'compose_frontier', side_effect=AssertionError('No frontier dispatch')):
            result = W.fulfill(self.stack, self.fixture.question, self.result, generation='frontier')
        self.assertEqual(result['answer']['status'], 'partial')
        self.assertTrue(result['action_context'])
        self.assertTrue(all(step['status']=='blocked' for step in result['workflow']))
        self.assertFalse(any(step.get('deliverable_id') for step in result['workflow']))
        self.assertEqual(result.get('new_paid_api_calls', 0), 0)
        self.assertFalse(result['workflow_continuation']['attempts'][0]['rendered'])

    def test_unavailable_store_keeps_context_and_operational_error_without_rendering(self):
        with sqlite3.connect(T.observation_path(self.path)) as db:
            db.execute('DELETE FROM observations WHERE id=?', (self.fixture.observation,))
        result = W.fulfill(self.stack, self.fixture.question, self.result)
        self.assertTrue(result['action_context'])
        self.assertIn('Source-reading status is unavailable', result['action_context_error'])
        self.assertEqual(result['answer']['status'], 'partial')
        self.assertFalse(any(step.get('deliverable_id') for step in result['workflow']))
        self.assertTrue(all(step['status']=='blocked' for step in result['workflow']))

    def test_prior_analysis_costs_are_not_lost_or_charged_again(self):
        self.assertEqual(self.fixture.suspend(), 1)
        result = copy.deepcopy(self.result)
        result.update(new_paid_api_calls=3, planning_economics={
            'known_api_estimate_usd':.25, 'new_api_estimate_usd':.25, 'api_cost_known':True})
        delivered = W.fulfill(self.stack, self.fixture.question, result)
        self.assertEqual(delivered['new_paid_api_calls'], 3)
        self.assertEqual(delivered['planning_economics']['known_api_estimate_usd'], .25)
        self.assertEqual(delivered['workflow_continuation']['attempts'][0]['new_paid_api_calls'], 3)

    def test_initialized_workflow_refreshes_source_view_and_keeps_prior_work_and_costs(self):
        initial = W.fulfill(self.stack, self.fixture.question, self.result, generation='saved_report')
        key = initial['action_context']['id']
        context = A.load(self.path, key)
        original_work = A.work(self.path, context)
        prior = W.read(self.path, key)
        prior['known_api_estimate_usd'] = .42
        prior['new_paid_api_calls'] = 2
        prior['api_cost_known'] = False
        W._save(self.path, key, prior)
        self.assertEqual(self.fixture.suspend(), 1)
        delivered = W.fulfill(self.stack, self.fixture.question, self.result, generation='saved_report')
        self.assertEqual(delivered['action_context']['id'], key)
        self.assertEqual(delivered['workflow'][0]['status'], 'ready')
        self.assertEqual(delivered['workflow'][1]['status'], 'blocked')
        self.assertNotEqual(delivered['workflow'][0]['deliverable_id'], initial['workflow'][0]['deliverable_id'])
        self.assertEqual(delivered['new_paid_api_calls'], 0)
        saved = W.read(self.path, key)
        self.assertEqual(saved['known_api_estimate_usd'], .42)
        self.assertEqual(saved['new_paid_api_calls'], 2)
        self.assertFalse(saved['api_cost_known'])
        self.assertFalse(delivered['planning_economics']['all_recorded_api_cost_known'])
        self.assertFalse(delivered['planning_economics']['retained_workflow_costs'][key]['api_cost_known'])
        self.assertEqual(saved['source_review_history'][0], prior)
        self.assertEqual(A.work(self.path, A.load(self.path, key)), original_work)

    def test_source_review_mode_cannot_bypass_generation_or_program_guards(self):
        self.assertEqual(self.fixture.suspend(), 1)
        with self.assertRaisesRegex(ValueError, 'saved inspection report only'):
            W.materialize(self.path, self.fixture.key, generation='frontier', source_review=True)

    def test_ordinary_controller_keeps_reference_and_source_report(self):
        from clinical_core.agere.clinical import conversation
        self.assertEqual(self.fixture.suspend(), 1)
        self.stack.compose=lambda question,**kwargs:copy.deepcopy(self.result)
        result=conversation.handle_compose(self.stack,{'question':self.fixture.question,
            'conversation_id':None,'delivery_generation':'saved_report'})
        self.assertTrue(result['action_context'])
        self.assertNotIn('action_context_error',result)
        self.assertEqual(result['answer']['status'],'partial')
        self.assertEqual(result['workflow'][0]['status'],'ready')
        self.assertEqual(result['workflow'][1]['status'],'blocked')

    def test_source_loss_between_fulfillment_and_materialization_keeps_reference(self):
        self.assertEqual(self.fixture.suspend(), 1)
        original=W.materialize
        def lose_store(*args,**kwargs):
            T.observation_path(self.path).unlink()
            return original(*args,**kwargs)
        with patch.object(W,'materialize',side_effect=lose_store):
            result=W.fulfill(self.stack,self.fixture.question,self.result)
        self.assertTrue(result['action_context'])
        self.assertIn('Source-reading status is unavailable',result['action_context_error'])
        self.assertTrue(all(step['status']=='blocked' for step in result['workflow']))
        self.assertFalse(result['workflow_continuation']['attempts'][0]['rendered'])

    def test_corrupt_store_is_an_operational_error_with_an_inspectable_context(self):
        T.observation_path(self.path).write_bytes(b'Not a SQLite source journal')
        result=W.fulfill(self.stack,self.fixture.question,self.result)
        self.assertTrue(result['action_context'])
        self.assertIn('Source-reading status is unavailable',result['action_context_error'])
        self.assertTrue(all(step['status']=='blocked' for step in result['workflow']))

    def test_data_change_during_retained_render_never_stamps_ready_or_current(self):
        from clinical_core.agere.deliverables import saved_report
        self.assertEqual(self.fixture.suspend(), 1)
        original=saved_report.create
        def change_then_render(*args,**kwargs):
            with sqlite3.connect(self.path/'semantic.sqlite3') as db:
                db.execute("UPDATE meta SET value='changed-during-render' WHERE key='source_digest'")
            return original(*args,**kwargs)
        with patch.object(saved_report,'create',side_effect=change_then_render):
            result=W.fulfill(self.stack,self.fixture.question,self.result)
        self.assertTrue(result['action_context'])
        self.assertIn('data changed',result['action_context_error'])
        self.assertTrue(all(step['status']=='blocked' for step in result['workflow']))
        self.assertNotIn('source_review_identity',W.read(self.path,result['action_context']['id']))

    def test_monitor_and_program_results_remain_inspection_only(self):
        self.assertEqual(self.fixture.suspend(), 1)
        self.result['workflow'].append({'kind':'monitor','title':'Watch source cohort','mode':'work'})
        self.result['program_results']=[{'answer':{'status':'answered'},'program':'authored'}]
        result=W.fulfill(self.stack,self.fixture.question,self.result)
        self.assertEqual(result['workflow'][2]['status'],'blocked')
        self.assertTrue(result['program_results'][0]['source_review_required'])
        self.assertFalse(result['program_results'][0]['clinical_execution_authorized'])

    def test_worklist_exports_carry_source_disposition_not_unqualified_positive_readings(self):
        from clinical_core.agere.deliverables import patient_dashboard
        self.assertEqual(self.fixture.suspend(), 1)
        result=W.fulfill(self.stack,self.fixture.question,self.result)
        context=A.load(self.path,result['action_context']['id'])
        export=json.loads(patient_dashboard.exports(context)['json'])
        row=next(row for row in export['worklist_rows'] if row['patient_id']=='A')
        self.assertEqual(row['answer_source_reading_status'],'suspended')
        self.assertEqual(row['patient_source_check_status'],'suspended_source_reading')
        self.assertIn('unresolved',row['source_interpretation_notice'])
        financial={'question':'Inspect reported amounts','source':context['source'],
            'answer':{'status':'partial','table':[{'payer':'Synthetic payer','reported_amount':10}]},
            'source_disposition':{'status':'suspended'}}
        financial_row=json.loads(patient_dashboard.exports(financial)['json'])['worklist_rows'][0]
        self.assertEqual(financial_row['reported_amount'],10)
        self.assertEqual(financial_row['answer_source_reading_status'],'suspended')
        self.assertIn('suspended source reading',financial_row['source_interpretation_notice'])

    def test_evaluation_preview_keeps_the_source_downgrade_without_delivery(self):
        from tempfile import TemporaryDirectory
        from clinical_core.agere.clinical import evaluation
        self.assertEqual(self.fixture.suspend(), 1)
        with TemporaryDirectory() as scratch, patch.dict('os.environ',{'AGERE_EVALUATION_SCRATCH':scratch}):
            with evaluation.scope(stack_path=self.path):
                result=W.fulfill(self.stack,self.fixture.question,self.result)
        self.assertEqual(result['answer']['status'],'partial')
        self.assertEqual(result['answer']['captured_status'],'answered')
        self.assertTrue(result['source_review_required'])
        self.assertFalse(any(step.get('deliverable_id') for step in result['workflow']))

    def test_aggregate_source_review_preserves_producing_context_links(self):
        child=A.capture(self.path,self.fixture.question,self.fixture.result)['id']
        W._save(self.path,child,{'initialized':True,'steps':[{'kind':'deliverable','title':'Child evidence','status':'ready'}]})
        root=A.capture(self.path,self.fixture.question,self.result)['id']
        links=[{'index':0,'context_id':child,'node_index':0,'node_id':'child-output','kind':'deliverable'}]
        W.save_program_workflow(self.path,root,links)
        target=A.root(self.path)/root/'workflow.json'
        original=target.read_bytes()
        self.assertEqual(self.fixture.suspend(), 1)
        result=W.materialize(self.path,root,generation='saved_report',source_review=True)
        self.assertTrue(result['program_links'])
        self.assertEqual(result['steps'][0]['context_id'],child)
        self.assertEqual(result['steps'][0]['status'],'blocked')
        self.assertEqual(target.read_bytes(),original)


if __name__ == '__main__':
    unittest.main()
