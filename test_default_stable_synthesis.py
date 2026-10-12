"""New contexts avoid a redundant legacy reconciliation; old answers replay."""
import json
import unittest
from unittest.mock import patch
import test_case_passes as fixture
from clinical_core.agere.clinical import source_synthesis as router, availability_source_stage as stage, availability_reconciliation as stable


class DefaultStable(unittest.TestCase):
    def setUp(self):
        fixture.CasePasses.setUp(self)
        (self.path/'run.json').write_text(json.dumps({'synthetic':True,'snapshot':{'as_of':'2026-03-01'}}))
        self.db.execute('CREATE TABLE patients(id TEXT)');self.db.execute("INSERT INTO patients VALUES ('p')");self.db.commit()
    raw=fixture.CasePasses.raw
    answer=fixture.CasePasses.answer
    sender=fixture.CasePasses.sender
    run_legacy=fixture.CasePasses.run_synthesis

    def run_default(self,sender=None):
        with patch.object(stage.single,'send',side_effect=sender or self.sender),patch.object(stage.single,'token_check',return_value={'input_tokens':500,'provider_count_cached':False}):
            return router.synthesize(self.path,self.row['id'],self.ledger,allow_live=True)

    def test_new_context_calls_source_then_stable_once(self):
        seen=[]
        def send(body,*args,**kwargs):
            seen.append(json.loads(body['messages'][0]['content']));return self.sender(body,*args,**kwargs)
        out=self.run_default(send)
        self.assertEqual(out['schema'],stable.VERSION)
        self.assertEqual(sum(w['stage']=='source_pass' for w in seen),len(out['passes']))
        reconciliation=[w for w in seen if w['stage']=='excerpt_reconciliation']
        self.assertTrue(reconciliation);self.assertTrue(all(w['schema']==stable.VERSION for w in reconciliation))
        self.assertEqual(out['new_paid_api_calls'],len(seen))
        self.assertEqual(router.inspect(self.path,out['id'])['id'],out['id'])
        replay=router.synthesize(self.path,self.row['id'],self.ledger)
        self.assertTrue(replay['cache_hit']);self.assertEqual(replay['new_paid_api_calls'],0)

    def test_preexisting_legacy_answer_replays_unchanged(self):
        old=self.run_legacy()
        with patch.object(stage,'synthesize') as dispatch:
            replay=router.synthesize(self.path,self.row['id'],self.ledger,allow_live=True)
            dispatch.assert_not_called()
        self.assertEqual(replay['id'],old['id']);self.assertEqual(replay['new_paid_api_calls'],0)
        self.assertEqual(router.inspect(self.path,old['id']),old)

    def test_failed_reconciliation_reuses_saved_source_stage_on_resume(self):
        original=stable.synthesize
        with patch.object(stable,'synthesize',side_effect=ValueError('Fixture reconciliation unavailable')):
            with self.assertRaises(ValueError):self.run_default()
        partial=router.progress(self.path,self.row['id'])
        self.assertEqual(partial['status'],'source_passes_complete_reconciliation_pending')
        before=self.ledger.receipt()['calls']['validated'];out=self.run_default()
        self.assertEqual(self.ledger.receipt()['calls']['validated']-before,len(out['reconciliation']['groups']))
        self.assertEqual(out['new_paid_api_calls'],len(out['reconciliation']['groups']))

    def test_one_pass_needs_no_reconciliation(self):
        self.row['model_context']['evidence']=self.row['model_context']['evidence'][:3]
        kept={u['source_ref'] for u in self.row['model_context']['evidence']}
        self.row['model_context']['sources']={k:v for k,v in self.row['model_context']['sources'].items() if k in kept}
        self.db.executemany('DELETE FROM records WHERE id=?',[(r['record_id'],) for r in self.records if r['record_id'] not in kept]);self.db.commit()
        out=self.run_default();self.assertEqual(out['schema'],stage.VERSION)
        self.assertEqual(out['new_paid_api_calls'],1);self.assertEqual(out['coverage']['reconciliation_groups'],0)
        self.assertNotIn('record_scope',out)
        self.assertEqual(router.inspect(self.path,out['id']),out)

    def test_stage_tampering_and_source_change_invalidates_derived_answer(self):
        out=self.run_default();parent=router.inspect(self.path,out['parent_answer_id'])
        parent['findings'][0]['text']='Changed after validation';stage.location(self.path,parent['id']).write_text(json.dumps(parent))
        with self.assertRaisesRegex(ValueError,'integrity'):router.inspect(self.path,out['id'])

    def test_source_failure_saves_partial_and_never_silently_retries(self):
        from clinical_core.contracts import digest
        def fail(body,provider,ledger,out,*,parse):
            key=digest({'contract':'context-student/1','provider':provider,'body':body});ledger.reserve(key,provider,body['model']);ledger.finish(key,error='fixture');raise RuntimeError('fixture')
        with self.assertRaises(RuntimeError):self.run_default(fail)
        self.assertEqual(router.progress(self.path,self.row['id'])['status'],'incomplete')
        with patch.object(stage.single,'send') as send:
            with self.assertRaisesRegex(ValueError,'no silent'):router.synthesize(self.path,self.row['id'],self.ledger,allow_live=True)
            send.assert_not_called()


if __name__=='__main__':unittest.main()
