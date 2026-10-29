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


if __name__=='__main__':unittest.main()
