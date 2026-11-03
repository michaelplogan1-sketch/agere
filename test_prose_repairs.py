"""Localized edits retain the entire candidate and receive complete validation."""
import copy,json,unittest
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere.clinical import execution_state as E,backstop_agent as B,semantic_analyst as S
from clinical_core.agere import model_policy as M
from clinical_core.contracts import digest


class ProseRepairs(unittest.TestCase):
    def setUp(self):
        self.calls=[{'name':'query_sql','input':{'sql':'SELECT patient_id,events FROM scoped'},
            'result':{'result_view':'agere_result_1','columns':['patient_id','events'],
                      'rows':[['A',2],['B',None]],'truncated':False}}]
        self.base={'status':'answered','answer_type':'table','table_from_tool':'agere_result_1',
            'headline':{'template':'Received count: {n}.','bindings':{'n':{'result_view':'agere_result_1','row':0,'column':'events'}}},
            'sources':[{'record_id':'note_A','quote':'Dated observation','date':'2030-01-01'}],
            'missing':['Current responsibility is unestablished.'],
            'row_prose':{'keys':['patient_id'],'rows':[
                {'key':{'patient_id':pid},'basis':[{'result_view':'agere_result_1','row':i}],
                 'fields':{'explanation':'A dated observation; current responsibility is unknown.',
                           'review':'Confirm current responsibility.'}}for i,pid in enumerate(['A','B'])]}}
        self.compile(self.base)

    def compile(self,a):
        B.materialize_table(a,self.calls);B.bind_computed_prose(a,self.calls)

    def patch(self,**fields):
        return {'repair_draft':{'draft_digest':digest(self.base),'rows':[{'key':{'patient_id':'A'},
            'fields':fields or {'review':'Review the dated observation before confirming current responsibility.'}}]}}

    def test_only_named_prose_changes_and_all_native_values_sources_bases_survive(self):
        original=copy.deepcopy(self.base);patch=self.patch();before=copy.deepcopy(patch)
        merged=E.apply_prose_repair(patch,self.base)
        self.assertIsInstance(merged['headline'],dict)
        self.compile(merged)
        for i in range(2):
            for key,value in original['table'][i].items():
                if i==0 and key=='review':continue
                self.assertEqual(merged['table'][i][key],value)
        self.assertEqual(merged['sources'],original['sources'])
        self.assertEqual(merged['missing'],original['missing'])
        self.assertEqual(merged['headline'],original['headline'])
        self.assertEqual([r['basis']for r in merged['row_prose']['rows']],[r['basis']for r in original['row_prose']['rows']])
        self.assertEqual(self.base,original);self.assertEqual(patch,before)
        self.assertEqual(E.draft_state(self.base)['localized_prose_repair']['draft_digest'],digest(self.base))

    def test_stale_digest_unknown_entities_duplicates_native_fields_and_deletions_rejected(self):
        for kind in ('stale','entity','duplicate','native','delete','basis','extra_top','key_type'):
            p=self.patch();r=p['repair_draft'];row=r['rows'][0]
            if kind=='stale':r['draft_digest']='stale'
            if kind=='entity':row['key']['patient_id']='C'
            if kind=='duplicate':r['rows'].append(copy.deepcopy(row))
            if kind=='native':row['fields']={'events':'changed'}
            if kind=='delete':row['fields']={'review':None}
            if kind=='basis':row['basis']=[]
            if kind=='extra_top':p['status']='answered'
            if kind=='key_type':row['key']['patient_id']=1
            before=copy.deepcopy(self.base)
            with self.subTest(kind=kind),self.assertRaises(ValueError):E.apply_prose_repair(p,self.base)
            self.assertEqual(self.base,before)

    def test_patch_cannot_bypass_numeric_or_same_entity_compiler_checks(self):
        for value in ('There were 42 events.',{'template':'Events: {n}.','bindings':{'n':{'result_view':'agere_result_1','row':1,'column':'events'}}}):
            merged=E.apply_prose_repair(self.patch(explanation=value),self.base)
            with self.subTest(value=value),self.assertRaises(ValueError):self.compile(merged)

    def test_noop_patch_is_not_a_new_repair(self):
        with self.assertRaisesRegex(ValueError,'no changed fields'):
            E.apply_prose_repair(self.patch(review=self.base['row_prose']['rows'][0]['fields']['review']),self.base)

    def test_changed_native_values_are_read_again_by_the_compiler(self):
        merged=E.apply_prose_repair(self.patch(),self.base)
        self.calls[0]['result']['rows'][0][1]=3
        self.compile(merged)
        self.assertEqual(merged['table'][0]['events'],3)
        self.assertIn('3',merged['headline'])

    def test_loop_merges_then_reviews_complete_answer_without_reexecuting_sources(self):
        sent=[];reviewed=[]
        original=copy.deepcopy(self.base)
        def review(a,c):
            self.assertEqual(c,self.calls);self.compile(a);reviewed.append(copy.deepcopy(a))
            if len(reviewed)==1:return {'status':'repair','feedback':'Repair the proposed review for A.'}
            self.assertEqual(len(a['table']),2)
            self.assertEqual(a['table'][1],original['table'][1])
            self.assertEqual(a['sources'],original['sources'])
            return {'status':'confirmed'}
        def send(body):
            sent.append(body)
            proposal=E.draft_state(self.base)['last_draft'] if len(sent)==1 else self.patch()
            if len(sent)==1:proposal['sources']=copy.deepcopy(self.base['sources'])
            else:proposal['repair_draft']['draft_digest']=digest(reviewed[-1])
            return {'content':[{'type':'text','text':json.dumps(proposal)}]},.001,False
        out=S.tool_loop('Explain each entity',[],[],lambda use:self.fail('Unexpected source execution'),send=send,
            model=M.SONNET,max_tools=None,initial_calls=self.calls,review=review)
        self.assertEqual(len(reviewed),2);self.assertEqual(len(sent),2)
        self.assertIn('repair_draft',sent[1]['messages'][-1]['content'])
        self.assertEqual(out['prose_repairs'][0]['changed_fields'],1)
        self.assertTrue(out['prose_repairs'][0]['complete_review_required'])
        self.assertEqual(out['answer']['status'],'answered')

    def test_repeated_invalid_patch_keeps_partial_answer_and_does_not_run_source_review(self):
        sent=[];checks=[]
        def send(body):
            sent.append(body)
            p=E.draft_state(self.base)['last_draft'] if len(sent)==1 else self.patch()
            if len(sent)>1:p['repair_draft']['draft_digest']='stale'
            return {'content':[{'type':'text','text':json.dumps(p)}]},.001,False
        def review(a,c):checks.append(a);return {'status':'repair','feedback':'Inspect the proposed review.'}
        out=S.tool_loop('Explain each entity',[],[],lambda use:None,send=send,model=M.SONNET,
            max_tools=None,initial_calls=self.calls,review=review)
        self.assertEqual(len(sent),3);self.assertEqual(len(checks),1)
        self.assertEqual(out['review_stopped'],'no_progress')
        self.assertEqual(out['answer']['status'],'partial')
        self.assertEqual(out['answer']['verification'],'unverified')

    def located_failure(self):
        self.base['status']='partial'
        self.base['row_prose']['rows'][0]['fields']['review']='Review two events on 2030-01-01; preserve the uncertainty.'
        # Restore quantitative headline templates, as the real draft state does.
        self.base['headline']=E.draft_state(self.base)['last_draft']['headline']
        return B.review_final(SimpleNamespace(),'Explain each entity',self.base,self.calls,
            SimpleNamespace(read={'note_A':{'text':'Dated observation'}}),ledger=None,as_of='2030-05-27')

    def bound_review(self,view='agere_result_1',row=0):
        return {'template':'Review {n} events on 2030-01-01; preserve the uncertainty.',
            'bindings':{'n':{'result_view':view,'row':row,'column':'events'}}}

    def test_compiler_targets_bind_exact_field_and_mixed_global_failures_disable_focus(self):
        feedback=self.located_failure();targets=E.prose_repair_targets(self.base,feedback)
        self.assertEqual(len(targets),1);self.assertEqual(targets[0]['field'],'review')
        self.assertEqual(targets[0]['key'],{'patient_id':'A'})
        self.assertEqual(targets[0]['code'],'unbound_literal')
        self.base['headline']='There are 99 events.'
        mixed=B.review_final(SimpleNamespace(),'Explain each entity',self.base,self.calls,
            SimpleNamespace(read={'note_A':{'text':'Dated observation'}}),ledger=None,as_of='2030-05-27')
        self.assertNotIn('prose_repair',mixed)
        self.assertIsNone(E.focused_prose_state(self.base,mixed,self.calls))

    def test_same_entity_basis_addition_keeps_prior_bases_and_requires_complete_compilation(self):
        feedback=self.located_failure()
        self.calls.append({'name':'query_sql','input':{'sql':'SELECT patient_id,events FROM source'},
            'result':{'result_view':'agere_result_2','columns':['patient_id','events'],
                'rows':[['A',2],['B',7]],'truncated':False}})
        p=self.patch(review=self.bound_review('agere_result_2'))
        p['repair_draft']['rows'][0]['basis_additions']=[{'result_view':'agere_result_2','row':0}]
        before=copy.deepcopy(self.base)
        merged=E.apply_prose_repair(p,self.base,calls=self.calls,feedback=feedback);self.compile(merged)
        self.assertEqual(merged['row_prose']['rows'][0]['basis'],before['row_prose']['rows'][0]['basis']+[{'result_view':'agere_result_2','row':0}])
        self.assertEqual(merged['table'][0]['review'],'Review 2 events on 2030-01-01; preserve the uncertainty.')
        self.assertEqual(self.base,before)
        for kind in ('foreign','missing','duplicate','incomplete','unsupported'):
            q=copy.deepcopy(p);calls=copy.deepcopy(self.calls);edit=q['repair_draft']['rows'][0]
            if kind=='foreign':edit['basis_additions'][0]['row']=1
            if kind=='missing':edit['basis_additions'][0]['row']=8
            if kind=='duplicate':edit['basis_additions']=[{'result_view':'agere_result_1','row':0}]
            if kind=='incomplete':calls[-1]['result']['truncated']=True
            if kind=='unsupported':edit['basis_extra']=edit.pop('basis_additions')
            with self.subTest(kind=kind),self.assertRaises(ValueError):
                E.apply_prose_repair(q,self.base,calls=calls,feedback=feedback)
            self.assertEqual(self.base,before)

    def test_binding_repair_cannot_delete_or_change_sentence_values_units_or_dates(self):
        feedback=self.located_failure()
        good=self.bound_review()
        for kind in ('quantity','date','meaning','unit','wrong_scalar'):
            value=copy.deepcopy(good)
            if kind=='quantity':value='Review the events on 2030-01-01; preserve the uncertainty.'
            if kind=='date':value['template']=value['template'].replace('2030-01-01','2030-01-02')
            if kind=='meaning':value['template']=value['template'].replace('; preserve the uncertainty','')
            if kind=='unit':value['template']=value['template'].replace('events','mg')
            if kind=='wrong_scalar':value['bindings']['n']['row']=1
            with self.subTest(kind=kind),self.assertRaisesRegex(ValueError,'preserve the original'):
                E.apply_prose_repair(self.patch(review=value),self.base,calls=self.calls,feedback=feedback)
        self.compile(E.apply_prose_repair(self.patch(review=good),self.base,calls=self.calls,feedback=feedback))
        self.assertEqual(E.canonical_repair_prose('Dose 10.0 mg; three events.'),E.canonical_repair_prose('Dose ten mg; 3 events.'))

    def test_scoped_state_keeps_all_native_handles_and_exact_selected_readings(self):
        feedback=self.located_failure()
        reading=copy.deepcopy(self.calls[0]);reading['result']['result_view']='agere_result_2'
        reading['result']['actor_reading']={'mode':'complete_rows','rows_digest':digest(reading['result']['rows']),
            'columns_digest':digest(reading['result']['columns'])}
        calls=self.calls+[reading,{'name':'metadata','input':{},'result':{'result_view':'metadata','kind':'non_table'}}]
        before=copy.deepcopy(calls)
        state=json.loads(E.messages('Both entities remain requested',calls,candidate=self.base,feedback=feedback)[0]['content'])
        focus=state['focused_prose_repair']
        self.assertEqual(focus['repair_entities'],1);self.assertEqual(focus['native_answer_rows'],2)
        self.assertEqual(focus['complete_row_prose_rows'],2);self.assertNotIn('last_draft',state)
        self.assertEqual(focus['native_scalar_rows'][0]['values'],{'patient_id':'A','events':2})
        self.assertEqual(len(state['executed_results']),len(calls));self.assertEqual(calls,before)
        selected=state['executed_results'][1]['repair_reading']
        self.assertEqual(selected['rows'],[{'patient_id':'A','events':2}])
        self.assertEqual(selected['native_total_rows'],2);self.assertEqual(selected['native_row_indices'],[0])
        for mutation in ('digest','field','basis','key','duplicate'):
            changed=copy.deepcopy(feedback);target=changed['prose_repair']['targets'][0]
            if mutation=='digest':changed['prose_repair']['draft_digest']='stale'
            if mutation=='field':target['field']='missing'
            if mutation=='basis':target['basis']=[]
            if mutation=='key':target['key']={'patient_id':'B'}
            if mutation=='duplicate':changed['prose_repair']['targets'].append(copy.deepcopy(target))
            with self.subTest(mutation=mutation):
                full=json.loads(E.messages('Both entities',calls,candidate=self.base,feedback=changed)[0]['content'])
                self.assertIn('last_draft',full);self.assertNotIn('focused_prose_repair',full)

    def test_invalid_patch_keeps_preservation_guard_on_the_next_attempt(self):
        feedback=self.located_failure();sent=[];reviews=[]
        def send(body):
            sent.append(body)
            p=self.patch(review='Review the events; omit the numeric detail.')
            if len(sent)==1:p['repair_draft']['rows'][0]['basis_extra']=[]
            return {'content':[{'type':'text','text':json.dumps(p)}]},0,False
        def review(a,c):reviews.append(copy.deepcopy(a));return feedback
        out=S.tool_loop('Explain both entities',[],[],lambda use:self.fail('Unexpected source execution'),send=send,
            model=M.SONNET,max_tools=None,initial_calls=self.calls,initial_answer=self.base,
            review=review,compact_execution_state=True)
        self.assertEqual(len(reviews),1);self.assertNotIn('prose_repairs',out)
        self.assertEqual(out['review_stopped'],'no_progress')
        self.assertTrue(all('focused_prose_repair'in json.loads(b['messages'][0]['content'])for b in sent))
        self.assertIn('preserve the original',sent[-1]['messages'][0]['content'])


if __name__=='__main__':unittest.main()
