"""Headline/facts binding edits retain the complete entity draft and row targets."""
import copy,json,unittest
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere.clinical import backstop_agent as B,execution_state as E,semantic_analyst as S,system_one_gate as G
from clinical_core.contracts import digest


class AnswerProseRepair(unittest.TestCase):
    def setUp(self):
        self.calls=[{'name':'query_sql','input':{'sql':'SELECT clinic_id,events FROM received'},
            'result':{'result_view':'agere_result_1','columns':['clinic_id','events'],
                'rows':[['A',2],['B',None]],'truncated':False}},
            {'name':'query_sql','input':{'sql':'SELECT COUNT(*) AS clinics FROM received'},
             'result':{'result_view':'agere_result_2','columns':['clinics'],'rows':[[2]],'truncated':False}}]
        self.draft={'status':'partial','answer_type':'table','headline':'2 clinics are represented.',
            'table_from_tool':'agere_result_1','row_prose':{'keys':['clinic_id'],'rows':[
                {'key':{'clinic_id':k},'basis':[{'result_view':'agere_result_1','row':i}],
                 'fields':{'explanation':'2 received visits.'if i==0 else'Received count remains unknown.'}}
                for i,k in enumerate(['A','B'])]}}
        B.materialize_table(self.draft,self.calls)

    def review(self,candidate):
        with patch.object(G,'ready',return_value=False):
            return B.review_final(SimpleNamespace(),'Explain each clinic and count clinics.',candidate,self.calls,
                SimpleNamespace(read={}),ledger=None,as_of='2030-05-27')

    def edit(self,candidate=None,path=None):
        return {'repair_draft':{'draft_digest':digest(candidate or self.draft),'fields':[
            {'path':path or ['headline'],'bind_literals':[{'literal':'2',
                'binding':{'result_view':'agere_result_2','row':0,'column':'clinics'}}]}]}}

    def test_mixed_errors_locate_both_scopes_and_repair_headline_without_replacing_patient_prose(self):
        feedback=self.review(self.draft);before=copy.deepcopy(self.draft)
        self.assertEqual(len(feedback['prose_repair']['targets']),1)
        self.assertEqual(len(feedback['prose_repair']['answer_targets']),1)
        focus=E.focused_prose_state(self.draft,feedback,self.calls)
        self.assertEqual(focus['scope'],'answer');self.assertEqual(focus['retained_patient_target_count'],1)
        fixed=E.apply_prose_repair(self.edit(),self.draft,calls=self.calls,feedback=feedback)
        self.assertEqual(fixed['row_prose'],before['row_prose']);self.assertEqual(fixed['table'],before['table'])
        self.assertEqual(self.draft,before)
        later=self.review(fixed)
        self.assertNotIn('answer_targets',later['prose_repair']);self.assertEqual(len(later['prose_repair']['targets']),1)
        focus=E.focused_prose_state(fixed,later,self.calls)
        self.assertEqual(focus['targets'][0]['key'],{'clinic_id':'A'})

    def test_stale_paths_values_unlocated_edits_and_number_changes_are_rejected(self):
        feedback=self.review(self.draft)
        for case in ('digest','path','duplicate','literal','row_patch'):
            edit=self.edit()
            if case=='digest':edit['repair_draft']['draft_digest']='stale'
            elif case=='path':edit['repair_draft']['fields'][0]['path']=['table',0,'events']
            elif case=='duplicate':edit['repair_draft']['fields']*=2
            elif case=='literal':edit['repair_draft']['fields'][0]['bind_literals'][0]['literal']='clinics'
            else:edit['repair_draft']={'draft_digest':digest(self.draft),'rows':[{'key':{'clinic_id':'A'},'fields':{'explanation':'Removed count'}}]}
            with self.subTest(case=case),self.assertRaises(ValueError):
                E.apply_prose_repair(edit,self.draft,calls=self.calls,feedback=feedback)
        stale=copy.deepcopy(feedback);stale['prose_repair']['answer_targets'][0]['value_digest']='stale'
        self.assertIsNone(E.answer_prose_repair_targets(self.draft,stale))
        stale=copy.deepcopy(self.draft);stale['headline']='Changed sentence.'
        self.assertIsNone(E.answer_prose_repair_targets(stale,feedback))
        numeric=copy.deepcopy(self.draft);numeric['headline']=2
        self.assertIsNone(E.answer_prose_repair_targets(numeric,self.review(numeric)))

    def test_facts_use_exact_keys_and_do_not_authorize_other_fields(self):
        draft=copy.deepcopy(self.draft);draft['facts']={'clinics.total':'2 reported clinics.'}
        feedback=self.review(draft)
        fixed=E.apply_prose_repair(self.edit(draft,['facts','clinics.total']),draft,calls=self.calls,feedback=feedback)
        self.assertEqual(fixed['headline'],draft['headline']);self.assertEqual(fixed['row_prose'],draft['row_prose'])
        self.assertEqual(E.repair_rendered_value(fixed['facts']['clinics.total'],{c['result']['result_view']:c['result']for c in self.calls}),'2 reported clinics.')

    def test_real_loop_uses_small_binding_patches_and_retains_source_uncertainty(self):
        sent=[];reviews=[]
        def review(candidate,calls):
            reviews.append(digest(candidate))
            try:B.bind_computed_prose(candidate,calls)
            except B.ComputedProseError:return self.review(candidate)
            return {'status':'repair','feedback':'Independent source support remains unknown.',
                'retry_disposition':'retain_partial_until_new_evidence','review_stop_reason':'unresolved_source_support'}
        def send(body):
            state=json.loads(body['messages'][0]['content']);focus=state['focused_prose_repair'];sent.append(copy.deepcopy(focus))
            self.assertNotIn('last_draft',state)
            if len(sent)==1:
                self.assertEqual(focus['scope'],'answer')
                self.assertIn('fields',body['output_config']['format']['schema']['properties']['repair_draft']['properties'])
                reply=self.edit();reply['repair_draft']['draft_digest']=focus['draft_digest']
            else:
                self.assertEqual(focus['targets'][0]['key'],{'clinic_id':'A'})
                reply={'repair_draft':{'draft_digest':focus['draft_digest'],'rows':[{'key':{'clinic_id':'A'},
                    'fields':{'explanation':{'bind_literals':[{'literal':'2','binding':{'result_view':'agere_result_1','column':'events','where':[]}}]}}}]}}
            return {'content':[{'type':'text','text':json.dumps(reply)}]},0.,True
        out=S.tool_loop('Explain each clinic and count clinics.','system',[],lambda u:self.fail('No tool needed'),
            send=send,model='scripted',max_tools=None,initial_calls=self.calls,initial_answer=self.draft,review=review,compact_execution_state=True)
        self.assertEqual(len(sent),2);self.assertEqual(len(reviews),3);self.assertIsNone(out.get('error'))
        self.assertEqual(out['review_stopped'],'unresolved_source_support')
        self.assertEqual(out['answer']['headline'],'2 clinics are represented.')
        self.assertEqual(out['answer']['table'][0]['explanation'],'2 received visits.')
        self.assertEqual(out['answer']['row_prose']['rows'][1],self.draft['row_prose']['rows'][1])
        self.assertEqual(out['prose_repairs'][0]['changed_answer_fields'],1)
        self.assertEqual(out['prose_repairs'][0]['changed_fields'],0)


if __name__=='__main__':unittest.main()
