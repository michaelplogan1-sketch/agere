"""Grouped reanswer integration; model output is explicitly stubbed in these tests."""
import copy
import json
import unittest
from unittest.mock import patch
from clinical_core.contracts import digest
from clinical_core.agere.clinical import (question_decision as Q, decision_reassessment as R,
    source_plan_answers as A, plan_answer as P, source_synthesis as S)
import test_decision_reassessment as fixtures

NATIVE_BINDING = Q.source_binding
NATIVE_INSPECT = S.inspect

class Grouped(unittest.TestCase):
    setUp=fixtures.Reassessment.setUp
    patch=fixtures.Reassessment.patch
    prepare=fixtures.Reassessment.prepare
    queue=fixtures.Reassessment.queue
    put=fixtures.Reassessment.put
    start=fixtures.Reassessment.start
    preview=fixtures.Reassessment.preview

    def grouped(self):
        self.start();self.put('new');self.output={}
        def assemble(retriever,p):
            groups=[]
            for rid in p['selected_record_ids']:
                src={'record_id':rid,'record_hash':p['selected_versions'][rid]['record_hash'],
                     'patient_id':'p1','source_table':'medication_lists','available_date':'2026-10-01'}
                unit={'id':'unit-'+rid,'source_ref':rid,'kind':'narrative','field':'text','start':0,'text':'Explicit stub account'}
                c={'patient_id':'p1','parameters':{'question':p['review_question']},'record_scope':{'record_ids':[rid]},
                   'dispatch':{'source_review_allowed':True},'omissions':[],
                   'model_context':{'snapshot':self.binding['source_versions']['snapshot'],'sources':{rid:{'source':src}},'evidence':[unit]}}
                c['id']=digest(c);self.contexts[c['id']]=c
                groups.append({'context':c,'request_fit':'bounded_plan_prepared','request_fit_error':None})
                a=copy.deepcopy(self.answer);a.update(status='complete',patient_id='p1',context_id=c['id'],context_digest=digest(c),question=p['review_question'],record_scope=c['record_scope'])
                a['findings'][0]['citations'][0].update(source=src,citation_verified=True,source_unit_id=unit['id'])
                a['passes']=[{'pass_number':1,'result':{'unit_reviews':[{'source_unit_id':unit['id'],'status':'discussed'}]}}]
                a.update(coverage={'complete_declared_scope':True,'received_units':1,'submitted_units':1,'model_accounted_units':1,
                    'unassessed_units':[],'cross_group_relations_assessed':False},usage={},token_check={'input_tokens':1},review_steps=[])
                a.pop('id');a['id']=digest(a);self.output[a['id']]=a
            return groups
        Q.source_plans.contexts.side_effect=assemble
        self.patch(Q.source_plans.C,'inspect',side_effect=lambda path,key:copy.deepcopy(self.contexts[key]))
        self.patch(S,'inspect',side_effect=lambda path,key:copy.deepcopy(self.output[key]) if key in self.output else NATIVE_INSPECT(path,key))
        self.patch(Q,'source_binding',side_effect=NATIVE_BINDING)
        S.configure_plan_answers(self.retriever)
        return R.prepare(self.retriever,{**self.origin,'preview_id':self.preview()['id']})

    def selected(self,p):
        ids=[A.attach(self.retriever,{'plan_id':p['source_plan_id'],'answer_id':a})['attachment']['id'] for a in self.output]
        snap=A.prepare(self.retriever,{'plan_id':p['source_plan_id'],'attachment_ids':ids})['snapshot']
        return P.prepare(self.retriever,{'snapshot_id':snap['id']})

    def test_all_groups_reach_original_question_buyer_and_replacement_work(self):
        p=self.grouped();a=self.selected(p)
        self.assertEqual(p,R.prepare(self.retriever,{**self.origin,'preview_id':self.preview()['id']}))
        r=R.refresh(self.retriever,{'preparation_id':p['result_id'],'answer_id':a['id'],'coverage':self.req['coverage']})
        new=Q.inspect(self.retriever,r['replacement_decision_id'])
        self.assertEqual(new['question'],self.row['question']);self.assertEqual(new['analysis_window'],self.row['analysis_window'])
        self.assertEqual(new['buyer_id'],self.row['buyer_id']);self.assertEqual(len(new['source_versions']['records']),2)
        self.assertIn('Cross-group relationships remain unassessed',str(r['unresolved']))
        w=R.queue(self.retriever,{'reassessment_id':r['result_id'],'option_id':new['matching']['options'][0]['id'],'actor':'test operator'})
        self.assertNotEqual(w['replacement_task_id'],self.task['id'])
        self.assertEqual(Q.care_readiness.task(self.path,self.task['id']),self.task)
        self.assertEqual(Q.load(self.path,self.row['id']),self.row)

    def test_one_group_answer_cannot_replace_complete_question(self):
        p=self.grouped();key=next(iter(self.output))
        with self.assertRaisesRegex(ValueError,'one of several'):R.refresh(self.retriever,{'preparation_id':p['result_id'],'answer_id':key,'coverage':self.req['coverage']})
        att=A.attach(self.retriever,{'plan_id':p['source_plan_id'],'answer_id':key})['attachment']['id']
        with self.assertRaisesRegex(ValueError,'every source-plan group'):A.prepare(self.retriever,{'plan_id':p['source_plan_id'],'attachment_ids':[att]})

    def test_unaccounted_unit_prevents_selection(self):
        p=self.grouped();key=next(iter(self.output));self.output[key]['passes']=[]
        with self.assertRaisesRegex(ValueError,'exactly once'):A.attach(self.retriever,{'plan_id':p['source_plan_id'],'answer_id':key})

    def test_new_source_invalidates_plan_and_answer(self):
        p=self.grouped();a=self.selected(p);self.put('later')
        with self.assertRaisesRegex(ValueError,'changed'):P.inspect(self.retriever,a['id'])
        with self.assertRaisesRegex(ValueError,'changed'):R.refresh(self.retriever,{'preparation_id':p['result_id'],'answer_id':a['id'],'coverage':self.req['coverage']})

    def test_work_change_invalidates_preparation(self):
        p=self.grouped()
        with patch.object(Q,'bound_task',return_value={**self.task,'revision':99}):
            with self.assertRaisesRegex(ValueError,'changed'):Q.source_plans.inspect(self.retriever,{'id':p['source_plan_id']})

    def test_omission_or_blocked_group_prevents_preparation(self):
        self.grouped();assemble=Q.source_plans.contexts.side_effect
        for field in ['omissions','dispatch']:
            def incomplete(*args):
                groups=assemble(*args)
                groups[0]['context'][field]=[{'record':'missing'}] if field=='omissions' else {'source_review_allowed':False}
                return groups
            with patch.object(Q.source_plans,'contexts',side_effect=incomplete):
                with self.assertRaisesRegex(ValueError,'without omissions'):R.prepare(self.retriever,{**self.origin,'preview_id':self.preview()['id']})

    def test_legacy_compatibility_does_not_accept_other_implementations(self):
        self.assertTrue(R.compatible({'schema':R.PREPARATION,'implementation':R.LEGACY_IMPLEMENTATION}))
        self.assertFalse(R.compatible({'schema':R.GROUP_PREPARATION,'implementation':R.LEGACY_IMPLEMENTATION}))
        self.assertFalse(R.compatible({'schema':R.PREPARATION,'implementation':'unknown'}))

    def test_plan_adapter_rejects_nonstring_ids_before_sql(self):
        for value in (None, [], {}, 42):
            with self.assertRaisesRegex(ValueError,'source-plan ID'):
                Q.source_plans.inspect(self.retriever,{'id':value})

if __name__=='__main__':unittest.main()
