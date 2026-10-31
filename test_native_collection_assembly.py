"""Native collections remain intact through the existing table/final compiler."""
import copy,json,unittest
from unittest.mock import patch
from clinical_core.agere.clinical import result_assembly as A,backstop_agent as B


class ParentIdentityRetention(unittest.TestCase):
    def issues(self,expected,actual,*,parent=None,final_parent=None,keys=None,on=None):
        parent={'patient_id':'A'}if parent is None else parent
        final_parent=parent if final_parent is None else final_parent
        keys=['patient_id']if keys is None else keys
        on={'patient_id':'patient_id'}if on is None else on
        source={**parent,'items':expected}
        results={'bound':{'rows':[source],'columns':list(source),'assembly':{'keys':keys,
            'joins':[{'as':'items','on':on,'mode':'collect','retain_in_final':True}]}}}
        return A.collection_retention_issues(results,[{**final_parent,'items':actual}],{'bound'})

    def test_redundant_join_identity_can_be_added_or_carried_by_the_parent(self):
        item={'drug_key':'example','dose':5,'record_id':'r','date':'2029-01-01'}
        for old,new in [([item],[{**item,'patient_id':'A'}]),([{**item,'patient_id':'A'}],[item]),
                        ([item,{**item,'patient_id':'A'}],[{**item,'patient_id':'A'},item])]:
            before=copy.deepcopy((old,new))
            self.assertFalse(self.issues(old,new));self.assertEqual((old,new),before)
            self.assertFalse(self.issues(old,json.dumps(new)))

    def test_multiplicity_parent_attribution_and_nested_assertions_stay_strict(self):
        item={'patient_id':'A','record_id':'r','assertion':{'patient_id':'A','question_digest':'q'}}
        mutations=[[{'record_id':'r','assertion':item['assertion']}],
                   [{**item,'patient_id':'B'}],
                   [{**item,'assertion':{'patient_id':'B','question_digest':'q'}}],
                   [{**item,'record_id':'different'}],[]]
        self.assertFalse(self.issues([item],mutations[0]))
        for actual in mutations[1:]:
            with self.subTest(actual=actual):self.assertTrue(self.issues([item],actual))
        self.assertTrue(self.issues([item,item],[item]))
        self.assertTrue(self.issues([{'patient_id':'B','record_id':'r'}],[{'record_id':'r'}]))
        source={'rows':[{'patient_id':p,'items':[{'patient_id':p,'record_id':p}]}for p in ['A','B']],
                'columns':['patient_id','items'],'assembly':{'keys':['patient_id'],'joins':[
                    {'as':'items','on':{'patient_id':'patient_id'},'mode':'collect','retain_in_final':True}]}}
        swapped=[{'patient_id':p,'items':[{'patient_id':q,'record_id':q}]}for p,q in [('A','B'),('B','A')]]
        self.assertTrue(A.collection_retention_issues({'bound':source},swapped,{'bound'}))

    def test_null_type_and_nonkey_changes_do_not_borrow_parent_identity(self):
        for child in [None,'7',7.0,True]:
            with self.subTest(child=child):
                self.assertTrue(self.issues([{'patient_id':child,'dose':1}],[{'dose':1}],parent={'patient_id':7}))
        self.assertTrue(self.issues([{'patient_id':None,'dose':1}],[{'dose':1}],parent={'patient_id':None}))
        self.assertFalse(self.issues([{'patient_id':7,'dose':1}],[{'dose':1}],parent={'patient_id':7}))
        for value in [1.0,True,'1',None]:
            with self.subTest(value=value):self.assertTrue(self.issues([{'dose':1}],[{'dose':value}]))
        self.assertTrue(self.issues([{'dose':1}],[{'dose':1,'current_use':True}]))

    def test_only_the_source_side_of_a_retained_identity_join_is_normalized(self):
        self.assertFalse(self.issues([{'source_patient':'A','dose':1}],[{'dose':1}],on={'patient_id':'source_patient'}))
        self.assertTrue(self.issues([{'patient_id':'A','dose':1}],[{'dose':1}],on={'patient_id':'source_patient'}))
        self.assertTrue(self.issues([{'patient_id':'A','dose':1}],[{'dose':1}],parent={'patient_id':'A','cohort':'A'},on={'cohort':'patient_id'}))
        parent={'patient_id':'A','other_id':'B'}
        self.assertTrue(self.issues([{'id':'A','dose':1}],[{'dose':1}],parent=parent,
            keys=['patient_id','other_id'],on={'patient_id':'id','other_id':'id'}))

    def test_composite_identity_does_not_elide_a_mismatching_component(self):
        parent={'patient_id':'A','episode':'E'};on={'patient_id':'patient_id','episode':'episode'}
        self.assertFalse(self.issues([{'patient_id':'A','episode':'E','dose':1}],[{'dose':1}],parent=parent,keys=list(parent),on=on))
        self.assertTrue(self.issues([{'patient_id':'A','episode':'OTHER','dose':1}],[{'dose':1}],parent=parent,keys=list(parent),on=on))

    def test_native_rename_keeps_the_same_parent_binding(self):
        results={'base':{'rows':[{'patient_id':'A'}]},'source':{'rows':[
            {'patient_id':'A','record_id':'r','dose':5}]}}
        results['bound']=A.compose(results,base='base',keys=['patient_id'],joins=[
            {'result':'source','as':'items','on':{'patient_id':'patient_id'},'mode':'collect','retain_in_final':True}])
        final=A.compose(results,base='bound',keys=['patient_id'],rename_columns={'items':'source_context'})
        results['final']=final;rows=copy.deepcopy(final['rows']);rows[0]['source_context'][0].pop('patient_id')
        self.assertFalse(A.collection_retention_issues(results,rows,{'bound','final'}))
        rows[0]['source_context'][0]['dose']=6
        self.assertTrue(A.collection_retention_issues(results,rows,{'bound','final'}))

    def test_heterogeneous_and_scalar_collections_retain_exact_nongrain_fields(self):
        self.assertTrue(self.issues([{'a':1},{'a':1,'b':2}],[{'a':1,'b':2},{'a':1,'b':2}]))
        self.assertFalse(self.issues(['x','x',1],['x',1,'x']))
        self.assertTrue(self.issues(['x','x',1],['x',1]))
        bad=self.issues([{'patient_id':'A','record_id':'original','dose':1}],[{'dose':2}])
        self.assertIn('original',str(bad));self.assertIn('record_ids',str(bad))

    def test_large_collection_uses_linear_multiset_comparison(self):
        count=10000;old=[{'record_id':str(i),'dose':i}for i in range(count)]
        new=[{**r,'patient_id':'A'}for r in reversed(old)]
        with patch.object(A,'digest',wraps=A.digest)as digested:
            self.assertFalse(self.issues(old,new))
        self.assertLessEqual(digested.call_count,4*count)


class Collections(unittest.TestCase):
    def fixture(self):
        items=[{'name':'Dr. Example','date':'2029-01-01','record_id':'r1','quote':'Signed source.',
            'relationship':'signed_correspondence_author','current_assignment':'not_established'},
            {'name':'Dr. Other','date':'2028-01-01','record_id':'r2','quote':'Copied source.',
            'relationship':'correspondence_addressee','current_assignment':'not_established'}]
        results={'base':{'columns':['patient_id'],'rows':[{'patient_id':'A'},{'patient_id':'B'}]},
            'source':{'columns':['patient_id','collection_json'],'rows':[{'patient_id':'A','collection_json':json.dumps(items)},
                {'patient_id':'B','collection_json':'[]'},{'patient_id':'C','collection_json':json.dumps([items[0]])}]}}
        join={'result':'source','as':'dated_roles','on':{'patient_id':'patient_id'},'mode':'collect','items_from':'collection_json'}
        return items,results,join

    def test_complete_objects_scope_and_empty_are_preserved(self):
        items,results,join=self.fixture();old=copy.deepcopy(results)
        out=A.compose(results,base='base',keys=['patient_id'],joins=[join])
        self.assertEqual(out['rows'][0]['dated_roles'],items);self.assertEqual(out['rows'][1]['dated_roles'],[])
        self.assertEqual(out['assembly']['joins'][0]['source_collection_items'],3)
        self.assertEqual(out['assembly']['joins'][0]['output_collection_items'],2)
        self.assertEqual(out['assembly']['joins'][0]['source_rows_outside_base_keys'],1);self.assertEqual(results,old)

    def test_final_table_compiler_retains_every_quote_date_relationship(self):
        items,results,join=self.fixture();out=A.compose(results,base='base',keys=['patient_id'],joins=[join]);out['result_view']='composed'
        answer={'table_from_tool':'composed'}
        B.materialize_table(answer,[{'name':'compose_table','input':{'base':'base'},'result':out}])
        self.assertEqual(answer['table'][0]['dated_roles'],items);self.assertEqual(len(answer['table']),2)

    def test_invalid_or_object_json_cannot_be_silently_dropped(self):
        for value in ['not JSON','{"a":1}',42]:
            _,results,join=self.fixture();results['source']['rows'][0]['collection_json']=value
            with self.subTest(value=value),self.assertRaises(ValueError):A.compose(results,base='base',keys=['patient_id'],joins=[join])

    def test_items_from_cannot_be_combined_with_scalar_or_partial_projection(self):
        for extra in [{'mode':'one'},{'columns':['patient_id']},{'items_from':'invented'}]:
            _,results,join=self.fixture()
            with self.subTest(extra=extra),self.assertRaises(ValueError):A.compose(results,base='base',keys=['patient_id'],joins=[{**join,**extra}])

    def test_nonclinical_contract_collections_use_the_same_join(self):
        _,results,join=self.fixture();clause={'clause':'settlement follows measurement year','record_id':'contract','date':'2030-01-01'}
        results['source']['rows'][0]['collection_json']=json.dumps([clause]);join['as']='contract_terms'
        out=A.compose(results,base='base',keys=['patient_id'],joins=[join]);self.assertEqual(out['rows'][0]['contract_terms'],[clause])

    def test_explicit_final_retention_detects_dropped_roles_or_changed_dates(self):
        _,results,join=self.fixture();join['retain_in_final']=True
        out=A.compose(results,base='base',keys=['patient_id'],joins=[join]);results['composed']=out
        self.assertEqual(A.collection_retention_issues(results,out['rows'],{'composed'}),[])
        for mutation in ('drop','change','omit'):
            final=copy.deepcopy(out['rows'])
            if mutation=='drop':final[0]['dated_roles'].pop()
            elif mutation=='change':final[0]['dated_roles'][0]['date']='2030-05-27'
            else:final[0].pop('dated_roles')
            with self.subTest(mutation=mutation):self.assertTrue(A.collection_retention_issues(results,final,{'composed'}))

    def test_explicit_rename_preserves_retention_and_still_rejects_lost_or_mutated_objects(self):
        items,results,join=self.fixture();join['retain_in_final']=True
        results['composed']=A.compose(results,base='base',keys=['patient_id'],joins=[join])
        renamed=A.compose(results,base='composed',keys=['patient_id'],rename_columns={'dated_roles':'legacy_roles'})
        results['renamed']=renamed
        self.assertEqual(renamed['rows'][0]['legacy_roles'],items)
        self.assertEqual(A.collection_retention_issues(results,renamed['rows'],{'composed','renamed'}),[])
        for mutation in ('drop','change','omit'):
            final=copy.deepcopy(renamed['rows'])
            if mutation=='drop':final[0]['legacy_roles'].pop()
            elif mutation=='change':final[0]['legacy_roles'][0]['date']='2030-05-27'
            else:final[0].pop('legacy_roles')
            with self.subTest(mutation=mutation):self.assertTrue(A.collection_retention_issues(results,final,{'composed','renamed'}))
        # Equal-looking field names outside an executed rename are not proof.
        self.assertTrue(A.collection_retention_issues(results,renamed['rows'],{'composed'}))

    def test_rename_cannot_drop_identity_overwrite_fields_or_invent_sources(self):
        _,results,join=self.fixture();results['composed']=A.compose(results,base='base',keys=['patient_id'],joins=[join])
        for mapping in ({'patient_id':'other_id'},{'dated_roles':'patient_id'},{'missing':'audit'},{'dated_roles':''}):
            with self.subTest(mapping=mapping),self.assertRaises(ValueError):
                A.compose(results,base='composed',keys=['patient_id'],rename_columns=mapping)

    def test_renamed_context_retains_original_when_new_projection_reuses_its_old_name(self):
        items,results,join=self.fixture();join['retain_in_final']=True
        results['composed']=A.compose(results,base='base',keys=['patient_id'],joins=[join])
        richer=[{**item,'source_context':'additional native metadata'}for item in items]
        results['enriched']={'rows':[{'patient_id':'A','collection':richer}]}
        final=A.compose(results,base='composed',keys=['patient_id'],
            joins=[{'result':'enriched','as':'new_roles','on':{'patient_id':'patient_id'},'mode':'collect','items_from':'collection'}],
            rename_columns={'dated_roles':'prior_roles_context','new_roles':'dated_roles'})
        results['final']=final
        self.assertEqual(final['rows'][0]['prior_roles_context'],items)
        self.assertEqual(final['rows'][0]['dated_roles'],richer)
        self.assertEqual(A.collection_retention_issues(results,final['rows'],{'composed','final'}),[])
        for mutation in ('omit','change'):
            bad=copy.deepcopy(final['rows'])
            if mutation=='omit':bad[0].pop('prior_roles_context')
            else:bad[0]['prior_roles_context'][0]['date']='invented'
            with self.subTest(mutation=mutation):self.assertTrue(A.collection_retention_issues(results,bad,{'composed','final'}))

    def test_retention_feedback_groups_keys_without_losing_any_affected_identity(self):
        _,results,join=self.fixture();join['retain_in_final']=True
        results['base']['rows']=[{'patient_id':'A'},{'patient_id':'B'}]
        out=A.compose(results,base='base',keys=['patient_id'],joins=[join]);results['composed']=out
        bad=[{'patient_id':'A'},{'patient_id':'B'}]
        issues=A.collection_retention_issues(results,bad,{'composed'})
        self.assertEqual(len(issues),1)
        self.assertIn('[["A"], ["B"]]',issues[0]);self.assertIn('compose_table.rename_columns',issues[0])
        self.assertIn('required_object_fields=',issues[0])

    def test_financial_audit_alias_uses_the_same_native_retention(self):
        results={'base':{'rows':[{'contract':'C1'}]},'source':{'rows':[{'contract':'C1','clauses':'[{"record_id":"c","quote":"Payment follows reconciliation."}]'}]}}
        out=A.compose(results,base='base',keys=['contract'],joins=[{'result':'source','as':'terms','on':{'contract':'contract'},'mode':'collect','items_from':'clauses','retain_in_final':True}],rename_columns={'terms':'prior_terms_for_audit'})
        results['final']=out
        self.assertEqual(A.collection_retention_issues(results,out['rows'],{'final'}),[])
        self.assertEqual(out['rows'][0]['prior_terms_for_audit'][0]['record_id'],'c')

    def test_explicit_row_domain_excludes_other_specialties_without_dropping_members(self):
        results={'base':{'rows':[{'patient_id':'A'},{'patient_id':'B'}]},'roles':{'rows':[
            {'patient_id':'A','specialty':'Family Medicine','role':'primary_care','record_id':'p'},
            {'patient_id':'A','specialty':'Cardiology','role':'consulting_specialist','record_id':'c'},
            {'patient_id':'B','specialty':'Family Medicine','role':'primary_care','record_id':'b'}]}}
        out=A.compose(results,base='base',keys=['patient_id'],joins=[{'result':'roles','as':'cardiology_roles',
            'on':{'patient_id':'patient_id'},'mode':'collect','domain':{'column':'specialty','values':['Cardiology']},'retain_in_final':True}])
        self.assertEqual(len(out['rows']),2);self.assertEqual(out['rows'][0]['cardiology_roles'],[results['roles']['rows'][1]])
        self.assertIsNone(out['rows'][1]['cardiology_roles'])
        proof=out['assembly']['joins'][0];self.assertEqual(proof['source_rows_in_domain'],1);self.assertEqual(proof['source_rows_outside_domain'],2)
        results['composed']=out;bad=copy.deepcopy(out['rows']);bad[0]['cardiology_roles']=[]
        self.assertTrue(A.collection_retention_issues(results,bad,{'composed'}))

    def test_domain_contract_cannot_guess_columns_or_coerce_source_value_types(self):
        results={'base':{'rows':[{'patient_id':'A'}]},'source':{'rows':[{'patient_id':'A','kind':1},{'patient_id':'A','kind':'1'}]}}
        join={'result':'source','as':'items','on':{'patient_id':'patient_id'},'mode':'collect','domain':{'column':'kind','values':[1]}}
        out=A.compose(results,base='base',keys=['patient_id'],joins=[join]);self.assertEqual(out['rows'][0]['items'],[results['source']['rows'][0]])
        for domain in ({'column':'invented','values':[1]},{'column':'kind','values':[]},{'column':'kind','values':[{}]}):
            with self.subTest(domain=domain),self.assertRaises(ValueError):A.compose(results,base='base',keys=['patient_id'],joins=[{**join,'domain':domain}])

    def test_financial_domain_uses_the_same_native_join(self):
        results={'base':{'rows':[{'contract':'C1'},{'contract':'C2'}]},'claims':{'rows':[
            {'contract':'C1','status':'paid','amount':20,'record_id':'r1'},
            {'contract':'C1','status':'denied','amount':80,'record_id':'r2'}]}}
        out=A.compose(results,base='base',keys=['contract'],joins=[{'result':'claims','as':'paid_claims','on':{'contract':'contract'},
            'mode':'collect','domain':{'column':'status','values':['paid']},'retain_in_final':True}])
        self.assertEqual(out['rows'][0]['paid_claims'],[results['claims']['rows'][0]]);self.assertIsNone(out['rows'][1]['paid_claims'])


if __name__=='__main__':unittest.main()
