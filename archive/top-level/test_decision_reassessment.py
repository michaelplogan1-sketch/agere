"""Adverse scoped-reassessment contracts. All model content here is a test stub."""
import copy
import unittest
from unittest.mock import patch
from clinical_core.contracts import digest
from clinical_core.agere.clinical import decision_reassessment as R, question_decision as Q
import test_decision_source_review as fixtures


class Reassessment(unittest.TestCase):
    setUp=fixtures.Review.setUp
    patch=fixtures.Review.patch
    prepare=fixtures.Review.prepare
    queue=fixtures.Review.queue
    put=fixtures.Review.put
    start=fixtures.Review.start
    preview=fixtures.Review.preview
    def ready(self):
        self.start();self.put('r1',text='Changed received account')
        p=self.preview()
        return R.prepare(self.retriever,{**self.origin,'preview_id':p['id']})

    def test_exact_context_binding_rejects_wrong_question_and_later_sources(self):
        p=self.ready();c=self.contexts[p['context_id']]
        # Minimal source fixture gains snapshot before binding is exercised.
        a={'id':'b'*64,'status':'complete','patient_id':'p1','context_id':c['id'],'question':p['review_question'],'findings':[{'id':'new'}]}
        # hash-equivalence validation runs before the snapshot is needed.
        with self.assertRaises(ValueError):R.binding(self.retriever,{**a,'question':'other'},c)
        self.put('late')
        with self.assertRaisesRegex(ValueError,'scope changed'):R.binding(self.retriever,a,c)

    def test_reanswer_binding_preserves_original_window_and_exact_versions(self):
        self.start();self.put('r1',text='Changed')
        assemble=Q.source_plans.contexts.side_effect
        def complete(retriever,p):
            groups=assemble(retriever,p);c=groups[0]['context']
            c['model_context']['snapshot']=self.binding['source_versions']['snapshot']
            c.pop('id');c['id']=digest(c);self.contexts[c['id']]=c
            return groups
        with patch.object(Q.source_plans,'contexts',side_effect=complete):
            p=R.prepare(self.retriever,{**self.origin,'preview_id':self.preview()['id']})
        c=self.contexts[p['context_id']]
        a={'id':'b'*64,'status':'complete','patient_id':'p1','context_id':c['id'],'question':p['review_question'],'findings':[{'id':'new'}]}
        bound=R.binding(self.retriever,a,c)[2]
        self.assertEqual(bound['question'],self.row['question'])
        self.assertEqual(bound['analysis_window'],self.row['analysis_window'])
        self.assertEqual(bound['source_versions']['reanswer_preparation_id'],p['result_id'])
        changed=copy.deepcopy(c);changed['model_context']['sources']['r1']['source']['record_hash']='forged'
        with self.assertRaisesRegex(ValueError,'context or implementation'):R.binding(self.retriever,a,changed)

    def test_no_change_and_stale_previews_block(self):
        self.start();p=self.preview()
        with self.assertRaises(ValueError):R.prepare(self.retriever,{**self.origin,'preview_id':p['id']})
        self.put('r1',text='different')
        with self.assertRaisesRegex(ValueError,'preview changed'):R.prepare(self.retriever,{**self.origin,'preview_id':p['id']})

    def test_preparation_preserves_scope_and_is_idempotent(self):
        p=self.ready()
        self.assertEqual(p,R.prepare(self.retriever,{**self.origin,'preview_id':self.preview()['id']}))
        self.assertEqual(p['question'],self.row['question'])
        self.assertEqual(p['analysis_window'],self.row['analysis_window'])
        self.assertEqual(Q.care_readiness.task(self.path,self.task['id']),self.task)

    def test_multiple_groups_do_not_masquerade_as_complete_reanswer(self):
        self.start();self.put('new')
        old=Q.source_plans.contexts.side_effect
        with patch.object(Q.source_plans,'contexts',side_effect=lambda *a:old(*a)*2):
            with self.assertRaisesRegex(ValueError,'nonoverlapping'):R.prepare(self.retriever,{**self.origin,'preview_id':self.preview()['id']})

    def test_refresh_recomputes_and_links_work_without_reusing_changed_claim(self):
        p=self.ready()
        src={'record_id':'r1','record_hash':p['preview']['selected_versions']['r1']['record_hash'],'patient_id':'p1','source_table':'medication_lists','available_date':'2026-10-01'}
        answer=copy.deepcopy(self.answer);answer.update(id='b'*64,context_id=p['context_id'],question=p['review_question'])
        answer['findings'][0]['text']='Current changed documentary account'
        answer['findings'][0]['citations'][0]['source']=src
        context={'model_context':{'sources':{'r1':{'source':src}}}}
        bound={**self.binding,'answer_id':answer['id'],'context_id':p['context_id'],'answer_digest':digest(answer),
               'source_versions':{**self.binding['source_versions'],'records':{'r1':src['record_hash']}}}
        with patch.object(Q,'source_binding',return_value=(answer,context,bound)),patch.object(Q.care.source_synthesis,'inspect',return_value=answer):
            r=R.refresh(self.retriever,{'preparation_id':p['result_id'],'answer_id':answer['id'],'coverage':self.req['coverage']})
            self.assertEqual(r['finding_comparisons'][0]['status'],'superseded_by_current_answer_no_equivalence_claim')
            new=Q.load(self.path,r['replacement_decision_id']);self.assertNotEqual(new['id'],self.row['id'])
            work=R.queue(self.retriever,{'reassessment_id':r['result_id'],'option_id':new['matching']['options'][0]['id'],'actor':'test operator'})
            self.assertNotEqual(work['replacement_task_id'],self.task['id'])
            self.assertEqual(Q.care_readiness.task(self.path,self.task['id']),self.task)
            self.assertEqual(work,R.queue(self.retriever,{'reassessment_id':r['result_id'],'option_id':new['matching']['options'][0]['id'],'actor':'test operator'}))
            with self.assertRaises(ValueError):R.queue(self.retriever,{'reassessment_id':r['result_id'],'option_id':'not-an-option','actor':'test'})

    def test_exact_reproduced_finding_retains_digest_only_after_current_answer(self):
        self.start();self.put('new')
        p=R.prepare(self.retriever,{**self.origin,'preview_id':self.preview()['id']})
        answer=copy.deepcopy(self.answer);answer.update(id='b'*64,context_id=p['context_id'],question=p['review_question'])
        context=copy.deepcopy(self.context)
        src={**context['model_context']['sources']['r1']['source'],'record_id':'new','record_hash':p['preview']['selected_versions']['new']['record_hash']}
        context['model_context']['sources']['new']={'source':src}
        bound={**self.binding,'answer_id':answer['id'],'answer_digest':digest(answer),'context_id':p['context_id'],
            'source_versions':{**self.binding['source_versions'],'records':{k:v['source']['record_hash'] for k,v in context['model_context']['sources'].items()}}}
        with patch.object(Q,'source_binding',return_value=(answer,context,bound)),patch.object(Q.care.source_synthesis,'inspect',return_value=answer):
            result=R.refresh(self.retriever,{'preparation_id':p['result_id'],'answer_id':answer['id'],'coverage':self.req['coverage']})
        self.assertEqual(result['finding_comparisons'][0]['status'],'exact_result_reproduced')
        self.assertEqual(result['finding_comparisons'][0]['result_id'],self.row['finding_sources'][0]['finding_digest'])

    def test_refresh_refuses_another_task_revision_or_answer(self):
        p=self.ready()
        with self.assertRaisesRegex(ValueError,'exact prepared'):R.refresh(self.retriever,{'preparation_id':p['result_id'],'answer_id':self.answer['id'],'coverage':self.req['coverage']})
        with patch.object(Q,'bound_task',return_value={**self.task,'revision':50}):
            with self.assertRaisesRegex(ValueError,'work changed'):R.refresh(self.retriever,{'preparation_id':p['result_id'],'answer_id':self.answer['id'],'coverage':self.req['coverage']})


if __name__=='__main__':unittest.main()
