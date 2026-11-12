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

    def test_binding_context_uses_native_references_but_keeps_new_requested_readings(self):
        feedback=self.review(self.draft);calls=copy.deepcopy(self.calls)
        # A prior explicitly read table is useful for interpretation but is
        # not needed again to bind the existing headline's scalar count.
        for c in calls:
            c['result']['actor_reading']={'mode':'complete_rows','rows_digest':digest(c['result']['rows']),'columns_digest':digest(c['result']['columns'])}
        before=copy.deepcopy(calls)
        state=json.loads(E.messages('Explain clinics',calls,feedback=feedback,candidate=self.draft)[0]['content'])
        self.assertIn('binding_input_projection',state)
        self.assertTrue(all('complete_reading'not in e for e in state['executed_results']))
        self.assertEqual(state['executed_results'][1]['computed_rows'],[[2]])
        self.assertTrue(all(e['binding_reading_reference']['complete_native_result_retained']for e in state['executed_results']))
        state=json.loads(E.messages('Explain clinics',calls,recent_results=[calls[0]],feedback=feedback,candidate=self.draft)[0]['content'])
        self.assertIn('complete_reading',state['executed_results'][0])
        self.assertNotIn('complete_reading',state['executed_results'][1])
        full=json.loads(E.messages('Explain clinics',calls,candidate=self.draft)[0]['content'])
        self.assertNotIn('binding_input_projection',full)
        self.assertTrue(all('complete_reading'in e for e in full['executed_results']))
        self.assertEqual(calls,before)

    def test_existing_governed_scalar_survives_unrelated_year_binding_repair(self):
        calls=[*self.calls,{'name':'execute_metric','input':{'metric_id':'received_paid'},
            'result':{'result_view':'metric','metric_id':'received_paid','unit':'dollars','value':17.25}},
            {'name':'query_sql','input':{'sql':'SELECT year FROM received'},
             'result':{'result_view':'year','columns':['year'],'rows':[['2029']]}}]
        native={c['result']['result_view']:c['result']for c in calls}
        original={'template':'2029 observed amount: {paid} dollars.',
                  'bindings':{'paid':{'result_view':'metric','row':0,'column':'value'}}}
        fixed=E.bind_prose_literals(original,{'bind_literals':[{'literal':'2029',
            'binding':{'result_view':'year','row':0,'column':'year'}}]},native)
        self.assertEqual(E.repair_rendered_value(original,native),'2029 observed amount: 17.25 dollars.')
        self.assertEqual(E.repair_rendered_value(fixed,native),E.repair_rendered_value(original,native))
        self.assertEqual(fixed['bindings']['paid'],original['bindings']['paid'])
        for mutation in ({'truncated':True},{'error':'unavailable'},{'table_rows':2}):
            bad=copy.deepcopy(native);bad['metric'].update(mutation)
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                E.repair_rendered_value(original,bad)
        with self.assertRaisesRegex(ValueError,'another entity'):
            E.resolve_prose_binding(original['bindings']['paid'],native,{'patient_id':'A'})

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

    def test_explicit_wrong_quantity_correction_preserves_draft_and_requires_review(self):
        draft=copy.deepcopy(self.draft);draft['headline']='Four clinics are represented; scope remains unverified.'
        feedback=self.review(draft);edit=self.edit(draft)
        item=edit['repair_draft']['fields'][0]['bind_literals'][0];item['literal']='Four'
        with self.assertRaisesRegex(ValueError,'preserve the exact value'):
            E.apply_prose_repair(edit,draft,calls=self.calls,feedback=feedback)
        item['correction_reason']='The complete received clinic count is the native clinics scalar.'
        fixed=E.apply_prose_repair(edit,draft,calls=self.calls,feedback=feedback)
        native={c['result']['result_view']:c['result']for c in self.calls}
        self.assertEqual(E.repair_rendered_value(fixed['headline'],native),'2 clinics are represented; scope remains unverified.')
        self.assertEqual(fixed['table'],draft['table']);self.assertEqual(fixed['row_prose'],draft['row_prose'])
        self.assertEqual(fixed['status'],'partial');self.assertNotEqual(self.review(fixed).get('status'),'confirmed')
        for invalid in ('',False,None):
            item['correction_reason']=invalid
            with self.subTest(reason=invalid),self.assertRaises(ValueError):
                E.apply_prose_repair(edit,draft,calls=self.calls,feedback=feedback)
        item['correction_reason']='A reason does not authorize text or another entity.'
        for scalar in ('two',True,None,float('nan')):
            calls=copy.deepcopy(self.calls);calls[1]['result']['rows']=[[scalar]]
            with self.subTest(scalar=scalar),self.assertRaises(ValueError):
                E.apply_prose_repair(edit,draft,calls=calls,feedback=feedback)
        item['literal']='clinics'
        with self.assertRaises(ValueError):E.apply_prose_repair(edit,draft,calls=self.calls,feedback=feedback)
        item['literal']='2030'
        with self.assertRaises(ValueError):
            E.bind_prose_literals('On 2030-01-01',{'bind_literals':[item]},native)
        item['literal']='four';item['binding']={'result_view':'agere_result_2','row':0,'column':'clinics'}
        with self.assertRaisesRegex(ValueError,'another entity'):
            E.bind_prose_literals('four visits',{'bind_literals':[item]},native,key={'clinic_id':'A'})

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

    def test_aggregate_answers_bind_only_the_named_quantity_without_entity_prose(self):
        for noun,answer_type,row_prose in [('organizations','table','absent'),
                ('invoices','table',None)]:
            with self.subTest(noun=noun):
                draft={'status':'partial','answer_type':answer_type,'headline':'Recorded totals.',
                    'facts':{'total':f'2 {noun} are represented.','scope':'Historical received records.'}}
                if answer_type=='table':
                    draft['table_from_tool']='agere_result_1';B.materialize_table(draft,self.calls)
                if row_prose is None:draft['row_prose']=None
                feedback=self.review(draft);before=copy.deepcopy(draft)
                focus=E.focused_prose_state(draft,feedback,self.calls)
                self.assertEqual(focus['scope'],'answer')
                self.assertEqual(focus['complete_row_prose_rows'],0)
                self.assertEqual([x['path']for x in focus['answer_targets']],[['facts','total']])
                fixed=E.apply_prose_repair(self.edit(draft,['facts','total']),draft,
                    calls=self.calls,feedback=feedback)
                B.bind_computed_prose(fixed,self.calls)
                self.assertEqual(fixed['facts'],before['facts'])
                self.assertEqual(fixed.get('table'),before.get('table'))
                self.assertEqual(draft,before)
                self.assertEqual(fixed['headline'],before['headline'])
                stale=copy.deepcopy(feedback);stale['prose_repair']['draft_digest']='stale'
                self.assertIsNone(E.answer_prose_repair_targets(draft,stale))
                with self.assertRaises(ValueError):
                    E.apply_prose_repair(self.edit(draft,['headline']),draft,
                        calls=self.calls,feedback=feedback)

    def test_aggregate_real_loop_keeps_other_facts_and_source_review_required(self):
        draft={'status':'partial','answer_type':'table','headline':'Recorded organizations.',
            'table_from_tool':'agere_result_1','facts':{'count':'2 organizations.','scope':'Historical records only.'}}
        B.materialize_table(draft,self.calls);before=copy.deepcopy(draft);sent=[]
        def review(candidate,calls):
            try:B.bind_computed_prose(candidate,calls)
            except B.ComputedProseError:return self.review(candidate)
            return {'status':'repair','feedback':'Source interpretation still requires review.',
                'retry_disposition':'retain_partial_until_new_evidence','review_stop_reason':'unresolved_source_support'}
        def send(body):
            state=json.loads(body['messages'][0]['content']);focus=state['focused_prose_repair'];sent.append(focus)
            self.assertEqual(focus['scope'],'answer');self.assertNotIn('last_draft',state)
            reply=self.edit(draft,['facts','count']);reply['repair_draft']['draft_digest']=focus['draft_digest']
            return {'content':[{'type':'text','text':json.dumps(reply)}]},0.,True
        out=S.tool_loop('Count organizations.','system',[],lambda u:self.fail('No new tool needed'),
            send=send,model='scripted',max_tools=None,initial_calls=self.calls,initial_answer=draft,
            review=review,compact_execution_state=True)
        self.assertEqual(len(sent),1)
        self.assertEqual(out['answer']['facts'],before['facts'])
        self.assertEqual(out['answer']['table'],before['table'])
        self.assertEqual(out['answer']['status'],'partial')
        self.assertEqual(out['review_stopped'],'unresolved_source_support')
        self.assertEqual(out['prose_repairs'][0]['changed_answer_fields'],1)


if __name__=='__main__':unittest.main()
