"""Native collections remain intact through the existing table/final compiler."""
import copy,json,unittest
from clinical_core.agere.clinical import result_assembly as A,backstop_agent as B


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
