"""Question-bound source changes; engineering fixtures, not clinical targets."""
import copy
import json
import sqlite3
import unittest
from unittest.mock import patch
from clinical_core.agere.clinical import question_decision as q
from clinical_core.contracts import digest
import test_question_decision as fixtures


class Review(unittest.TestCase):
    setUp=fixtures.Decision.setUp
    patch=fixtures.Decision.patch
    prepare=fixtures.Decision.prepare
    queue=fixtures.Decision.queue
    put=fixtures.SourceArrival.put

    def start(self):
        self.row=self.prepare();self.task=self.queue(self.row)
        self.origin={'decision_id':self.row['id'],'task_id':self.task['id']}
        self.contexts={}
        def assemble(retriever,p):
            row={'patient_id':p['patient_id'],'parameters':{'question':p['review_question']},
                'record_scope':{'record_ids':p['selected_record_ids']},'omissions':[],
                'dispatch':{'source_review_allowed':True},
                'model_context':{'sources':{rid:{'source':{'record_id':rid,'record_hash':p['selected_versions'][rid]['record_hash']}} for rid in p['selected_record_ids']}}}
            row['id']=digest(row);self.contexts[row['id']]=row
            return [{'context':row,'original_context_id':row['id'],'request_fit':'bounded_plan_prepared','request_fit_error':None}]
        self.patch(q.source_plans,'contexts',side_effect=assemble)
        self.patch(q.answers,'context',side_effect=lambda path,key:copy.deepcopy(self.contexts[key]))

    def preview(self):return q.review_preview(self.retriever,self.origin)
    def prepared(self):return q.prepare_review(self.retriever,{**self.origin,'preview_id':self.preview()['id']})

    def saved_answer(self,review):
        answer={'id':'b'*64,'patient_id':'p1','context_id':review['groups'][0]['context_id'],
            'question':review['preview']['review_question'],'status':'complete',
            'findings':[],'unanswered':['Controlled fixture has no clinical meaning.']}
        return answer

    def test_no_changes_do_not_create_more_context_or_calls(self):
        self.start();p=self.preview();self.assertFalse(p['can_prepare'])
        with self.assertRaisesRegex(ValueError,'No established'):self.prepared()
        q.source_plans.contexts.assert_not_called()

    def test_changed_citation_targets_finding_without_inventing_clinical_change(self):
        self.start();self.put('r1',text='Updated received source');self.put('new')
        p=self.preview()
        self.assertEqual(p['selected_record_ids'],['new','r1'])
        self.assertEqual(p['finding_impacts'][0]['changed_cited_records'],['r1'])
        self.assertTrue(p['finding_dependency_index_complete'])
        self.assertFalse(p['clinical_relevance_assessed'])
        review=self.prepared();self.assertEqual(review,self.prepared())
        self.assertEqual(review['question'],self.row['question'])
        self.assertEqual(review['analysis_window'],self.row['analysis_window'])
        self.assertFalse(review['original_question_reanswered'])
        self.assertEqual(q.care_readiness.task(self.path,self.task['id']),self.task)
        self.assertEqual(len(q.reopen(self.retriever,self.row['id'])['followups']),1)

    def test_new_sources_do_not_prove_old_findings_changed(self):
        self.start();self.put('new')
        p=self.preview();self.assertEqual(p['finding_impacts'][0]['changed_cited_records'],[])
        self.assertEqual(p['finding_impacts'][0]['status'],'direct_sources_unchanged_context_effect_unassessed')

    def test_original_date_window_excludes_old_and_unknown_dates(self):
        self.binding['analysis_window']={'basis':'source_event','start':'2026-09-01','end':'2026-10-10'}
        self.start();self.put('old',event_date='2025-01-01');self.put('undated',event_date=None)
        self.put('new',event_date='2026-10-05')
        p=self.preview();self.assertEqual(p['selected_record_ids'],['new','r1'])
        self.assertEqual({x['record_id']:x['reason'] for x in p['date_exclusions']},
            {'old':'outside_original_window','undated':'unknown_date'})
        self.assertEqual(p['analysis_window'],self.binding['analysis_window'])

    def test_absent_record_is_reported_without_fabricated_context(self):
        self.start();self.put('new')
        with sqlite3.connect(self.path/'stack.sqlite3') as db:db.execute("DELETE FROM records WHERE id='r1'")
        p=self.preview();self.assertEqual(p['absent_records'],['r1'])
        self.assertEqual(p['selected_record_ids'],['new'])
        self.assertEqual(p['finding_impacts'][0]['changed_cited_records'],['r1'])

    def test_stale_preview_and_caller_scope_cannot_change_original_question(self):
        self.start();self.put('new');p=self.preview();self.put('later')
        with self.assertRaisesRegex(ValueError,'preview changed'):
            q.prepare_review(self.retriever,{**self.origin,'preview_id':p['id']})
        with self.assertRaises(ValueError):q.review_preview(self.retriever,{**self.origin,'question':'Changed'})
        q.source_plans.contexts.assert_not_called()

    def test_partial_answer_attaches_to_original_task_without_replacing_decision(self):
        self.start();self.put('new');review=self.prepared();answer=self.saved_answer(review)
        request={'review_id':review['result_id'],'answer_id':answer['id']}
        with patch.object(q.answers,'inspect',return_value=answer):
            result=q.review_answer(self.retriever,request)
            self.assertEqual(result,q.review_answer(self.retriever,request))
        self.assertEqual(result['question'],self.row['question'])
        self.assertEqual(result['unresolved'],answer['unanswered'])
        self.assertFalse(result['original_question_reanswered'])
        self.assertFalse(result['task_state_changed'])
        self.assertFalse(result['clinical_execution_authorized'])
        self.assertEqual(q.load(self.path,self.row['id']),self.row)
        self.assertEqual(q.care_readiness.task(self.path,self.task['id']),self.task)

    def test_wrong_patient_context_question_or_failed_answer_rejected(self):
        self.start();self.put('new');review=self.prepared();answer=self.saved_answer(review)
        for change in [{'patient_id':'p2'},{'context_id':'other'},{'question':'other'},{'status':'failed'}]:
            with self.subTest(change=change),patch.object(q.answers,'inspect',return_value={**answer,**change}):
                with self.assertRaises(ValueError):q.review_answer(self.retriever,{'review_id':review['result_id'],'answer_id':answer['id']})

    def test_later_sources_invalidate_prepared_answer_attachment(self):
        self.start();self.put('new');review=self.prepared();self.put('later')
        with patch.object(q.answers,'inspect') as reader:
            with self.assertRaisesRegex(ValueError,'sources or work changed'):
                q.review_answer(self.retriever,{'review_id':review['result_id'],'answer_id':'b'*64})
            reader.assert_not_called()

    def test_work_revision_invalidates_prepared_answer_attachment(self):
        self.start();self.put('new');review=self.prepared()
        with patch.object(q,'bound_task',return_value={**self.task,'revision':99}):
            with self.assertRaisesRegex(ValueError,'sources or work changed'):
                q.review_answer(self.retriever,{'review_id':review['result_id'],'answer_id':'b'*64})

    def test_context_coverage_cannot_be_changed_before_attachment(self):
        self.start();self.put('new');review=self.prepared();answer=self.saved_answer(review)
        self.contexts[answer['context_id']]['omissions']=[{'record_id':'new'}]
        with patch.object(q.answers,'inspect',return_value=answer):
            with self.assertRaisesRegex(ValueError,'context changed or incomplete'):
                q.review_answer(self.retriever,{'review_id':review['result_id'],'answer_id':answer['id']})

    def test_reference_closure_retains_date_exclusions(self):
        from test_warehouse_interpretation import record
        self.binding['analysis_window']={'basis':'source_event','start':'2026-09-01','end':'2026-10-10'}
        linked=record('linked','medication_information_requests',patient_id='p1',
            event_date='2025-01-01',available_date='2026-10-01',requested_information='Fixture request')
        with sqlite3.connect(self.path/'stack.sqlite3') as db:
            db.execute('INSERT INTO records VALUES (?,?,?)',('linked','p1',json.dumps(linked)))
        self.start()
        arrived=record('new','pharmacy_information_responses',patient_id='p1',
            event_date='2026-10-05',available_date='2026-10-06',request_record_id='linked')
        with sqlite3.connect(self.path/'stack.sqlite3') as db:
            db.execute('INSERT INTO records VALUES (?,?,?)',('new','p1',json.dumps(arrived)))
        p=self.preview()
        self.assertTrue(any(e['to']=='linked' for e in p['reference_edges']))
        self.assertNotIn('linked',p['selected_record_ids'])
        self.assertIn({'record_id':'linked','reason':'outside_original_window'},p['date_exclusions'])

    def test_large_review_blocks_without_silently_dropping_records(self):
        self.start()
        for i in range(200):self.put('new-'+str(i))
        p=self.preview();self.assertEqual(len(p['selected_record_ids']),201)
        self.assertFalse(p['can_prepare'])
        with self.assertRaisesRegex(ValueError,'200 whole records'):self.prepared()
        q.source_plans.contexts.assert_not_called()

    def test_legacy_finding_index_remains_explicitly_partial(self):
        self.start();row=copy.deepcopy(self.row);row.pop('finding_sources');row.pop('id');row['id']=digest(row)
        with q.database(self.path) as db:
            db.execute('INSERT INTO question_decisions VALUES (?,?)',(row['id'],json.dumps(row)))
        self.origin={'decision_id':row['id'],'task_id':self.queue(row)['id']};self.put('new')
        self.assertFalse(self.preview()['finding_dependency_index_complete'])


if __name__=='__main__':unittest.main()
