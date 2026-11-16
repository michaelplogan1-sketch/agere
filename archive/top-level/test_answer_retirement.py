"""Lifecycle checks use disposable warehouses and explicit synthetic answer doubles."""
import copy
import json
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import test_program_work as fixture
import test_care_capabilities as buyer_fixture
from clinical_core.agere.clinical import answer_retirement as r, program_work as w, program_work_v1 as legacy
from clinical_core.agere.clinical import synthesis_work, care_capabilities as care

A='a'*64; B='b'*64; C='c'*64

class Retirement(unittest.TestCase):
    def setUp(self):
        fixture.ProgramWork.setUp(self);self.addCleanup(self.temp.cleanup)
        self.stale=set()
        self.verifier=patch.object(synthesis_work,'verify',side_effect=self.verify).start()
        patch.object(synthesis_work,'contexts',side_effect=lambda path,ids,pids:{pids[0]:self.context(pids[0],*ids)}).start()
        self.addCleanup(patch.stopall)
        self.attach(A,B)
    def context(self,pid,*ids):
        return {'schema':synthesis_work.VERSION,'patient_id':pid,'code_identity':synthesis_work.identity(),
                'authority':synthesis_work.AUTHORITY,'answers':[{'answer_id':key} for key in sorted(ids)]}
    def verify(self,path,values):
        if any(a['answer_id'] in self.stale for v in values for a in v['answers']):raise ValueError('Synthetic dependency changed')
    def attach(self,*ids):
        p=w.prepare(self.root,'hf_review',['p00'],synthesis_contexts={'p00':self.context('p00',*ids)})
        w.materialize(self.root,p['proposal_id'],'engineering fixture');return p
    def item(self):return w.list_items(self.root,program='hf_review')['items'][0]
    def inspected(self):return w.inspect_item(self.root,self.item()['id'])
    def prepare(self,removed=(A,),replacements=(),reason='withdrawn_for_review',**kw):
        return r.prepare(self.root,dict(item_id=self.item()['id'],expected_revision=self.item()['revision'],answer_ids=list(removed),replacement_ids=list(replacements),reason=reason,note='Engineering lifecycle fixture',**kw))
    def apply(self,plan,request_id='retire-1'):
        return r.apply(self.root,dict(plan_id=plan['id'],actor='engineering fixture',request_id=request_id))
    def active(self):return [a['answer_id'] for a in (self.inspected()['assessment'].get('synthesis_context') or {}).get('answers',[])]
    def test_stale_answer_removed_other_answer_and_original_history_unchanged(self):
        old=self.inspected();self.stale.add(A);plan=self.prepare(reason='stale_dependencies')
        self.assertEqual(self.item(),old['item']);self.assertEqual(plan['retained_answer_ids'],[B])
        result=self.apply(plan);self.assertEqual(self.active(),[B]);self.assertTrue(result['item']['reassessment_required'])
        self.assertFalse(result['human_review_recorded']);self.assertEqual(result['external_actions_executed'],0)
        after=self.inspected();self.assertEqual(after['history'][:-1],old['history'])
        db=w.database(self.root)
        try:self.assertEqual(w.load(db,'work_assessments',old['assessment']['id']),old['assessment'])
        finally:db.close()
        self.assertEqual(after['history'][-1]['action'],'machine_context_retired')
        self.assertEqual(r.inspect(self.root,self.item()['id'])['retirements'],[result['retirement']])
    def test_replacement_verified_and_unrelated_answer_retained(self):
        self.apply(self.prepare(replacements=(C,),reason='replaced_for_review'))
        self.assertEqual(self.active(),[B,C]);self.verifier.assert_any_call(self.root,[self.context('p00',B,C)])
    def test_last_answer_removal_does_not_store_empty_context(self):
        self.apply(self.prepare(removed=(A,B)));self.assertNotIn('synthesis_context',self.inspected()['assessment'])
        self.assertEqual(r.inspect(self.root,self.item()['id'])['answers'],[])
        self.assertEqual(w.materialize(self.root,w.prepare(self.root,'hf_review',['p00'])['proposal_id'],'fixture')['changes']['unchanged'],1)
    def test_stale_reason_cannot_be_used_for_current_answer(self):
        with self.assertRaisesRegex(ValueError,'failed current verification'):self.prepare(reason='stale_dependencies')
    def test_recovered_dependency_invalidates_stale_preview(self):
        self.stale.add(A);p=self.prepare(reason='stale_dependencies');self.stale.clear()
        with self.assertRaisesRegex(w.WorkConflict,'now verifies'):self.apply(p)
        self.assertEqual(self.active(),[A,B])
    def test_stale_unrelated_answer_blocks_retirement(self):
        self.stale.add(B)
        with self.assertRaisesRegex(ValueError,'dependency changed'):self.prepare()
    def test_stale_replacement_blocks_prepare_and_apply(self):
        self.stale.add(C)
        with self.assertRaisesRegex(ValueError,'dependency changed'):self.prepare(replacements=(C,))
        self.stale.clear();p=self.prepare(replacements=(C,));self.stale.add(C)
        with self.assertRaisesRegex(ValueError,'dependency changed'):self.apply(p)
        self.assertEqual(self.active(),[A,B])
    def test_concurrent_attachment_invalidates_preview(self):
        p=self.prepare();self.attach(C)
        with self.assertRaisesRegex(w.WorkConflict,'Work changed'):self.apply(p)
        self.assertEqual(self.active(),[A,B,C])
    def test_concurrent_retirements_exactly_one_wins_and_idempotency(self):
        p=self.prepare();q=self.prepare(removed=(B,))
        def apply(pair):
            try:return self.apply(*pair)
            except w.WorkConflict:return 'conflict'
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(apply,[(p,'one'),(q,'two')]))
        self.assertEqual(results.count('conflict'),1)
        win=(p,'one') if results[0]!='conflict' else (q,'two')
        self.assertTrue(self.apply(*win)['idempotent_replay'])
        with self.assertRaises(w.WorkConflict):self.apply(q if win[0]==p else p,win[1])
        self.assertEqual(len(r.inspect(self.root,self.item()['id'])['retirements']),1)
    def test_current_evidence_and_implementation_changes_invalidate_preview(self):
        p=self.prepare()
        with patch.object(r,'identity',return_value='different'):
            with self.assertRaisesRegex(w.WorkConflict,'implementation changed'):self.apply(p)
        fixture.ProgramWork.mutate(self)
        with self.assertRaisesRegex(w.WorkConflict,'evidence changed'):self.apply(p)
    def test_guard_prevents_ordinary_drop_and_reintroduction_via_both_engines(self):
        old=self.attach(A,B);self.apply(self.prepare())
        for engine,exception in [(w,w.WorkConflict),(legacy,sqlite3.IntegrityError)]:
            with self.assertRaisesRegex(exception,'Retired answer'):engine.materialize(self.root,old['proposal_id'],'fixture')
        new=w.prepare(self.root,'hf_review',['p00'],synthesis_contexts={'p00':self.context('p00',A)})
        with self.assertRaisesRegex(w.WorkConflict,'Retired answer'):w.materialize(self.root,new['proposal_id'],'fixture')
        with self.assertRaisesRegex(w.WorkConflict,'would be lost'):w.materialize(self.root,self.proposal['proposal_id'],'fixture')
        self.assertEqual(self.active(),[B])
    def test_rollback_removes_audit_and_work_update_together(self):
        p=self.prepare();before=self.inspected()
        with patch.object(r.S,'append_event',side_effect=RuntimeError('Injected failure')):
            with self.assertRaises(RuntimeError):self.apply(p)
        self.assertEqual(self.inspected(),before)
        self.assertEqual(r.inspect(self.root,self.item()['id'])['retirements'],[])
        self.assertFalse(self.apply(p)['idempotent_replay'])
    def test_retirement_records_are_immutable(self):
        self.apply(self.prepare());db=r.database(self.root)
        try:
            for table in ('answer_retirement_plans','answer_retirements','answer_retirement_requests'):
                for statement in ('DELETE FROM '+table,'UPDATE '+table+' SET payload=payload'):
                    with self.assertRaisesRegex(sqlite3.IntegrityError,'immutable'):db.execute(statement)
        finally:db.close()
    def test_pending_reassessment_blocks_progress_and_closed_work_reopens(self):
        item=self.item();item=fixture.ProgramWork.event(self,item,'assign',owner='Synthetic owner',due_date=None)
        item=fixture.ProgramWork.event(self,item,'start_review',note='Engineering fixture')
        item=fixture.ProgramWork.event(self,item,'finish_review',note='Engineering fixture',disposition='no_action_supported',record_ids=['rp00'])
        self.assertEqual(item['state'],'review_closed')
        result=self.apply(self.prepare());self.assertEqual(result['item']['state'],'review_needed');self.assertEqual(result['item']['cycle'],item['cycle']+1)
        with self.assertRaisesRegex(ValueError,'reconcil'):
            fixture.ProgramWork.event(self,result['item'],'start_review',note='Engineering fixture')
    def test_bad_scope_fields_replacements_and_revision(self):
        for overrides in ({'answer_ids':[C]},{'answer_ids':[A,A]},{'replacement_ids':[A]},{'answer_ids':['x']},{'reason':'forged'}, {'reason':'replaced_for_review'}, {'expected_revision':True},{'note':''},{'extra':True}):
            req=dict(item_id=self.item()['id'],expected_revision=self.item()['revision'],answer_ids=[A],replacement_ids=[],reason='withdrawn_for_review',note='Engineering fixture');req.update(overrides)
            with self.assertRaises((ValueError,TypeError)):r.prepare(self.root,req)
    def test_buyer_origins_block_prior_assessments_but_not_other_work_or_answers(self):
        first=self.item()['assessment_id'];self.attach(C);old=self.item()['assessment_id']
        self.apply(self.prepare())
        for aid in (first,old):
            with self.assertRaisesRegex(w.WorkConflict,'retired machine context'):r.assert_origin_active(self.root,{'kind':'assessment','id':aid})
        r.assert_origin_active(self.root,{'kind':'assessment','id':self.item()['assessment_id']})
        r.assert_origin_active(self.root,{'kind':'answer','id':A})
        r.assert_origin_active(self.root,{'kind':'assessment','id':'unrelated'})
    def test_other_contexts_are_preserved_and_staleness_is_not_erased(self):
        from clinical_core.agere.clinical import selection_context, program_accountability
        decision={'patient_id':'p00','fixture':'decision'};account={'patient_id':'p00','fixture':'accountability'}
        with patch.object(selection_context,'verify') as dv,patch.object(program_accountability,'verify_contexts') as av:
            p=w.prepare(self.root,'hf_review',['p00'],decision_contexts={'p00':decision},accountability_contexts={'p00':account})
            w.materialize(self.root,p['proposal_id'],'fixture');plan=self.prepare()
            av.side_effect=ValueError('Stale accountability context')
            with self.assertRaisesRegex(ValueError,'Stale accountability'):self.apply(plan)
            av.side_effect=None;self.apply(plan);a=self.inspected()['assessment']
            self.assertEqual(a['decision_context'],decision);self.assertEqual(a['accountability_context'],account)
            dv.assert_any_call(self.root,[decision]);av.assert_any_call(self.root,[account])
    def test_retired_answer_replacement_is_rejected_before_preview(self):
        self.apply(self.prepare())
        with self.assertRaisesRegex(w.WorkConflict,'Retired answer'):self.prepare(removed=(B,),replacements=(A,))
    def test_functional_and_registered_program_engines(self):
        import test_functional_programs as functional_fixture
        import test_domain_programs as domain_fixture
        for cls,program in [(functional_fixture.FunctionalPrograms,'functional_support_review'),(domain_fixture.DomainPrograms,'respiratory_source_review')]:
            with self.subTest(program=program):
                f=cls();f.setUp()
                try:
                    old=w.prepare(f.root,program,['p0'],synthesis_contexts={'p0':self.context('p0',A,B)})
                    w.materialize(f.root,old['proposal_id'],'engineering fixture');item=w.list_items(f.root,program=program)['items'][0]
                    plan=r.prepare(f.root,dict(item_id=item['id'],expected_revision=item['revision'],answer_ids=[A],replacement_ids=[C],reason='replaced_for_review',note='Engineering fixture'))
                    result=r.apply(f.root,dict(plan_id=plan['id'],actor='engineering fixture',request_id='engine'))
                    a=w.inspect_item(f.root,item['id'])['assessment'];self.assertEqual(a['synthesis_context'],self.context('p0',B,C));self.assertTrue(result['item']['reassessment_required'])
                    with self.assertRaisesRegex(w.WorkConflict,'Retired answer'):w.materialize(f.root,old['proposal_id'],'engineering fixture')
                finally:f.doCleanups()
    def test_buyer_task_becomes_stale_and_cannot_prepare_handoff(self):
        f=buyer_fixture.CareCapabilities();f.setUp();self.addCleanup(f.doCleanups)
        care.save_portfolio(self.root,copy.deepcopy(f.save_request))
        req=copy.deepcopy(f.req);req['origin']={'kind':'assessment','id':self.item()['assessment_id'],'finding_ids':[]}
        original=care.origin;self.addCleanup(setattr,care,'origin',original);r.install_origin_guard();r.install_origin_guard()
        plan=care.prepare(self.root,req);task=care.enqueue(self.root,{'plan_id':plan['id'],'option_id':plan['options'][0]['id'],'actor':'engineering fixture'})['task']
        p=self.prepare();self.assertEqual(p['downstream_tasks'][0]['task_id'],task['id']);self.apply(p)
        view=care.task_view(self.root,'buyer1')['tasks'][0];self.assertFalse(view['dependencies_current'])
        with self.assertRaisesRegex(w.WorkConflict,'retired machine context'):
            care.task_event(self.root,{'task_id':task['id'],'expected_revision':task['revision'],'request_id':'handoff','action':'prepare_handoff','actor':'engineering fixture','note':'Engineering fixture'})
        req['origin']['id']=self.item()['assessment_id'];self.assertTrue(care.prepare(self.root,req)['options'])

if __name__=='__main__':unittest.main()
