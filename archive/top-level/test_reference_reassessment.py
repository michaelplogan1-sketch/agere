import copy,json,sqlite3,unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import test_reference_lifecycle as fixture
from clinical_core.contracts import digest
from clinical_core.agere.clinical import reference_reassessment as S,reference_lifecycle as L,reference_applicability as R

class Reassessment(unittest.TestCase):
    acquire=fixture.Lifecycle.acquire
    event_request=fixture.Lifecycle.event_request
    commit=fixture.Lifecycle.commit
    successor=fixture.Lifecycle.successor
    def setUp(self):
        fixture.Lifecycle.setUp(self)
        self.original=self.acquire();self.prior=R.acquisitions(self.path,self.target)[0]
        self.assessment_target=R.target_for(self.row,self.row['questions'][1])
        self.assessment_context=self.acquire(target=self.assessment_target)
        self.assessment_prior=R.acquisitions(self.path,self.assessment_target)[0]
        self.event=self.commit();self.new=self.successor(self.event)
        self.new_target=R.target_for(self.new,self.new['questions'][0]);self.new_assessment=R.target_for(self.new,self.new['questions'][1])
        self.selection={'target':self.new_target,'prior_acquisition_id':self.prior['id']}
    def preparation_request(self,selection=None):
        selection=selection or self.selection;p=S.preview(self.path,selection)
        return {**selection,'preview_id':p['id']}
    def test_preview_and_successor_do_not_transfer_sources_or_answers(self):
        p=S.preview(self.path,self.selection);self.assertEqual(p,S.preview(self.path,self.selection))
        self.assertFalse(p['candidate']['wording_changed']);self.assertEqual(p['candidate']['record_ids'],['r1'])
        self.assertEqual(R.acquisitions(self.path,self.new_target),[]);self.assertEqual(S.listing(self.path,{'target':self.new_target})['preparations'],[])
        self.assertFalse((self.path/'reference-reassessment.sqlite3').exists())
    def test_explicit_recheck_reuses_exact_context_without_resolving_question(self):
        out=S.prepare(self.path,self.preparation_request())
        self.assertTrue(out['exact_context_reused']);self.assertFalse(out['connected_scope_changed'])
        self.assertEqual(out['acquisition']['context_id'],self.original['context_id']);self.assertEqual(out['answers_carried_forward'],0)
        self.assertEqual(out['provider_calls'],0);self.assertFalse(out['clinical_applicability_established'])
        self.assertEqual(len(R.acquisitions(self.path,self.new_target)),1);self.assertEqual(R.acquisitions(self.path,self.target)[0],self.prior)
        self.assertTrue(S.listing(self.path,{'target':self.new_target})['preparations'][0]['current'])
        with self.assertRaises(ValueError):R.D.save(self.path,{'target':self.new_target,'actor_kind':'machine','disposition':'addressed'})
    def test_changed_question_creates_new_context(self):
        req=self.preparation_request({'target':self.new_assessment,'prior_acquisition_id':self.assessment_prior['id']});out=S.prepare(self.path,req)
        self.assertTrue(out['preview']['candidate']['wording_changed']);self.assertFalse(out['exact_context_reused'])
        self.assertNotEqual(out['acquisition']['context_id'],self.assessment_context['context_id'])
        self.assertEqual(self.contexts[out['acquisition']['context_id']]['parameters']['question'],R.resolve(self.path,self.new_assessment)['question'])
    def test_exact_question_and_predecessor_link_required(self):
        for extra in [{'prior_acquisition_id':self.assessment_prior['id']},{'prior_acquisition_id':'0'*64},{'target':self.target},{'target':{**self.new_target,'question_id':'0'*64}}]:
            with self.subTest(extra=extra),self.assertRaises(ValueError):S.preview(self.path,{**self.selection,**extra})
    def test_preview_id_and_request_shape_rejected(self):
        req=self.preparation_request()
        for extra in [{'preview_id':'0'*64},{'record_ids':['r1']},{'clinical_applicability_established':True}]:
            with self.assertRaises(ValueError):S.prepare(self.path,{**req,**extra})
        self.assertEqual(R.acquisitions(self.path,self.new_target),[])
    def test_predecessor_context_changes_are_exposed_and_blocked(self):
        req=self.preparation_request();self.contexts[self.original['context_id']]['parameters']['question']='wrong question'
        rows=S.listing(self.path,{'target':self.new_target});self.assertFalse(rows['candidates'][0]['available_for_explicit_recheck'])
        with self.assertRaises(ValueError):S.prepare(self.path,req)
        self.assertEqual(R.acquisitions(self.path,self.new_target),[])
    def test_source_verification_failure_blocks_reuse(self):
        req=self.preparation_request()
        with patch.object(R.record_context,'verified',side_effect=ValueError('Received source changed')):
            with self.assertRaisesRegex(ValueError,'Received source changed'):S.prepare(self.path,req)
    def test_successor_withdrawal_preserves_receipt_and_marks_stale(self):
        saved=S.prepare(self.path,self.preparation_request())
        change={**self.change,'reference_id':self.new['reference']['id'],'event_kind':'local_withdrawal','question_changes':{}}
        p=L.preview(self.path,change);L.commit(self.path,{'change':change,'preview_id':p['id'],'request_id':'withdraw-successor'})
        listing=S.listing(self.path,{'target':self.new_target});self.assertIsNotNone(listing['unavailable_reason'])
        self.assertFalse(listing['preparations'][0]['current']);self.assertEqual(listing['preparations'][0]['id'],saved['id'])
        with self.assertRaises(ValueError):S.prepare(self.path,self.preparation_request())
    def test_prepared_context_change_marks_history_stale(self):
        out=S.prepare(self.path,self.preparation_request({'target':self.new_assessment,'prior_acquisition_id':self.assessment_prior['id']}))
        self.contexts[out['acquisition']['context_id']]['omissions']=['changed']
        listing=S.listing(self.path,{'target':self.new_assessment});self.assertFalse(listing['preparations'][0]['current'])
    def test_concurrent_recheck_is_idempotent(self):
        req=self.preparation_request()
        with ThreadPoolExecutor(max_workers=2) as pool:rows=list(pool.map(lambda _:S.prepare(self.path,req),range(2)))
        self.assertEqual(rows[0],rows[1]);self.assertEqual(S.prepare(self.path,req),rows[0])
        self.assertEqual(len(S.listing(self.path,{'target':self.new_target})['preparations']),1)
        self.assertEqual(len(R.acquisitions(self.path,self.new_target)),1)
    def test_sidecar_failure_is_recoverable_without_duplicate_acquisition(self):
        req=self.preparation_request()
        with patch.object(S,'database',side_effect=sqlite3.OperationalError('fixture unavailable')):
            with self.assertRaises(sqlite3.OperationalError):S.prepare(self.path,req)
        self.assertEqual(len(R.acquisitions(self.path,self.new_target)),1)
        saved=S.prepare(self.path,req);self.assertEqual(len(R.acquisitions(self.path,self.new_target)),1)
        self.assertEqual(S.listing(self.path,{'target':self.new_target})['preparations'][0]['id'],saved['id'])
    def test_final_source_change_does_not_write_receipt(self):
        req=self.preparation_request();original=S.preview;calls=[]
        def changing(*a):
            row=original(*a);calls.append(1)
            if len(calls)>1:row['candidate']['record_ids']=['r2']
            return row
        with patch.object(S,'preview',side_effect=changing):
            with self.assertRaisesRegex(ValueError,'during successor'):S.prepare(self.path,req)
        self.assertEqual(S.listing(self.path,{'target':self.new_target})['preparations'],[])
    def test_wrong_acquired_patient_or_question_rejected(self):
        req=self.preparation_request();old=R.D.acquisition_context
        def foreign(*args):
            row=old(*args);self.contexts[row['context_id']]['patient_id']='other';return row
        with patch.object(R.D,'acquisition_context',side_effect=foreign):
            with self.assertRaisesRegex(ValueError,'patient or question'):S.prepare(self.path,req)
    def test_receipt_immutable_and_tampering_fails(self):
        out=S.prepare(self.path,self.preparation_request());db=S.database(self.path)
        try:
            for sql in ['DELETE FROM preparations','UPDATE preparations SET target=target']:
                with self.assertRaises(sqlite3.IntegrityError):db.execute(sql)
            db.execute('DROP TRIGGER immutable_preparations_UPDATE');out['exact_context_reused']=False
            db.execute('UPDATE preparations SET payload=?',(json.dumps(out),));db.commit()
        finally:db.close()
        with self.assertRaisesRegex(ValueError,'integrity'):S.listing(self.path,{'target':self.new_target})
    def test_original_task_and_both_manifests_preserved(self):
        S.prepare(self.path,self.preparation_request())
        self.assertEqual(R.care_readiness.task(self.path,self.task['id']),self.task)
        self.assertEqual(R.load(self.path,self.row['id']),self.row);self.assertEqual(R.load(self.path,self.new['id']),self.new)
        self.assertEqual(R.acquisitions(self.path,self.target)[0],self.prior)

if __name__=='__main__':unittest.main()
