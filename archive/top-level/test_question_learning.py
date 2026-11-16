"""Question tasks preserve source, review and reserved-family boundaries."""
import copy
import json
import sqlite3
import unittest
from unittest.mock import patch

from clinical_core.contracts import digest
from clinical_core.agere.clinical import question_learning as q
import test_learning_queue as fixtures
from test_learning_queue import PROTECTED


class QuestionLearningTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.QueueTests(); self.fixture.setUp(); self.fixture.build()
        self.stack = self.fixture.stack
        self.candidate = self.make_candidate('b')
        self.mock = patch.object(q.candidates, 'inspect', side_effect=lambda path, key: copy.deepcopy(self.candidate))
        self.mock.start()

    def tearDown(self):
        self.mock.stop(); self.fixture.tearDown()

    def make_candidate(self, pid):
        r = self.fixture.sources[pid]
        return {'id': 'a'*64, 'binding': {'snapshot': self.fixture.meta['snapshot'], 'patient_id': pid,
            'receipt_id': 'receipt', 'need_id': 'need'}, 'original_question': 'Was the visit completed?',
            'candidates': [{'record_id': r['record_id'], 'record_hash': digest(r), 'passages': [
                {'rank': 1, 'text': r['text'], 'source': {'record_id': r['record_id'], 'patient_id': pid,
                    'field': 'text', 'start': 0, 'end': len(r['text'])}}]}]}

    def request(self):
        return {'candidate_id': 'a'*64, 'passage_rank': 1, 'quote': 'visit', 'occurrence': 1, 'concept': 'completed care'}

    def test_exact_unicode_target_replay_and_no_review_or_inference(self):
        with patch.object(q.reader, 'read') as infer:
            first=q.prepare(self.stack,self.request()); second=q.prepare(self.stack,self.request())
            self.assertEqual(first,second); infer.assert_not_called()
        self.assertEqual(first['target']['start'],self.fixture.sources['b']['text'].rindex('visit'))
        result=q.inspect(self.stack,first['id'])
        self.assertEqual(result['context']['target_quote'],'visit');self.assertEqual(result['readings'],[])
        self.assertFalse(result['qualification']['fit_eligible_at_creation'])
        self.assertFalse(result['qualification']['automatic_acceptance'])
        self.assertEqual(q.learning.audit(self.stack)['recorded_review_revisions'],0)
        self.assertEqual(q.listing(self.stack)['total'],1)

    def test_protected_family_and_outer_validation_are_excluded_before_source_read(self):
        for pid,reason in [('c','nontraining_partition'),('d','protected_evaluation_family')]:
            self.candidate=self.make_candidate(pid)
            with patch.object(q.reader,'identity',return_value=q.reader.identity()), patch.object(q.reader,'source') as source:
                preview=q.preview(self.stack,'a'*64)
                self.assertEqual(preview['exclusion_reason'],reason);self.assertEqual(preview['passages'],[])
                with self.assertRaisesRegex(ValueError,reason):q.prepare(self.stack,self.request())
                source.assert_not_called()
        self.assertEqual(q.listing(self.stack)['total'],0)

    def test_changed_question_marks_task_stale_without_overwriting_it(self):
        first=q.prepare(self.stack,self.request()); self.candidate['original_question']='Changed question'
        with self.assertRaisesRegex(ValueError,'dependencies changed'):q.inspect(self.stack,first['id'])
        self.assertEqual(q.listing(self.stack)['rows'][0]['status'],'stale')
        new=q.prepare(self.stack,self.request());self.assertNotEqual(first['id'],new['id'])
        self.assertEqual(q.listing(self.stack)['total'],2)

    def test_changed_source_cannot_reuse_task(self):
        first=q.prepare(self.stack,self.request())
        db=sqlite3.connect(self.stack/'stack.sqlite3')
        r={**self.fixture.sources['b'],'text':'The visit changed.'}
        with db:db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(r),r['record_id']))
        db.close()
        with self.assertRaises(ValueError):q.inspect(self.stack,first['id'])
        self.assertEqual(q.listing(self.stack)['rows'][0]['status'],'stale')

    def test_protection_change_blocks_existing_and_new_tasks(self):
        first=q.prepare(self.stack,self.request())
        with patch.object(q.learning,'protections',return_value={**PROTECTED,'register_sha256':'changed'}):
            with self.assertRaisesRegex(ValueError,'protections changed'):q.inspect(self.stack,first['id'])
            with self.assertRaisesRegex(ValueError,'protections changed'):q.prepare(self.stack,self.request())

    def test_receipts_are_append_only(self):
        first=q.prepare(self.stack,self.request());db=q.db(self.stack)
        try:
            for sql in ('UPDATE tasks SET payload=? WHERE id=?','DELETE FROM tasks WHERE id=?'):
                with self.assertRaises(sqlite3.IntegrityError):db.execute(sql,('{}',first['id']) if sql.startswith('UPDATE') else (first['id'],))
        finally:db.close()

    def test_generated_structured_passage_is_not_a_received_text_target(self):
        self.candidate['candidates'][0]['passages'][0]['source']['field']='structured_fields'
        self.assertFalse(q.preview(self.stack,'a'*64)['passages'])
        with self.assertRaisesRegex(ValueError,'received text'):q.prepare(self.stack,self.request())

    def test_phrase_must_belong_to_exact_selected_passage(self):
        for changes in ({'quote':'absent'}, {'occurrence':True}, {'occurrence':-1}, {'passage_rank':2},
                        {'concept':''}, {'labels':{'statement':'patient_affirmed'}}):
            with self.assertRaises(ValueError):q.prepare(self.stack,{**self.request(),**changes})
        passage=self.candidate['candidates'][0]['passages'][0]
        passage['text']='The visit happened.';passage['source'].update(start=2,end=21)
        with self.assertRaisesRegex(ValueError,'occurrence'):q.prepare(self.stack,self.request())
        result=q.prepare(self.stack,{**self.request(),'occurrence':0})
        self.assertEqual(result['target']['start'],6)

    def test_source_role_is_preserved_without_becoming_patient_truth(self):
        r=self.fixture.sources['b'];r['text']='Signature:\nPalliative Medicine\n'
        db=sqlite3.connect(self.stack/'stack.sqlite3')
        with db:db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(r),r['record_id']))
        db.close();self.candidate=self.make_candidate('b')
        result=q.prepare(self.stack,{**self.request(),'quote':'Palliative','occurrence':0})
        self.assertEqual(result['source_structure']['role'],'signature')
        self.assertTrue(result['source_structure']['clinical_account_excluded'])
        self.assertIsNone(result['qualification']['labels'])

    def test_race_before_save_rejects_changed_provenance(self):
        real=q.inputs;calls=0
        def inputs(path,key):
            nonlocal calls
            calls+=1
            if calls==2:self.candidate['original_question']='Changed'
            return real(path,key)
        with patch.object(q,'inputs',side_effect=inputs):
            with self.assertRaisesRegex(ValueError,'changed during'):q.prepare(self.stack,self.request())
        self.assertEqual(q.listing(self.stack)['total'],0)

    def test_current_readiness_keeps_acquisition_and_quality_apart(self):
        r=q.readiness(self.stack)
        self.assertEqual(r['patient_eligibility']['eligible_acquisition_training'],2)
        self.assertEqual(r['patient_eligibility']['nontraining_partition'],1)
        self.assertEqual(r['patient_eligibility']['protected_evaluation_family'],1)
        self.assertEqual(r['attached_students']['status'],'not_attached')
        self.assertEqual(r['source_reviews']['distinct_reviewed_occurrences'],0)
        self.assertFalse(r['automatic_acceptance'])


if __name__=='__main__': unittest.main()
