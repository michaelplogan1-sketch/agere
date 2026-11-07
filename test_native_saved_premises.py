"""Saved native SQL closes exact receipts, never arbitrary missing table names."""
import copy,unittest
from clinical_core.agere.clinical import plan_fence as F
from clinical_core.agere.deliverables import answer_context as A
import test_bound_native_queries as fixture


def graph(query):
    return {'nodes':[{'id':'result','operation':'sql','parameters':{'query':query,'returns':'table'}}]}


class NativePremises(unittest.TestCase):
    setUp=fixture.BoundNativeQueries.setUp

    def prepare(self,shadow=False):
        tools=self.fixture.tools()
        first=tools({'name':'query_sql','input':{'sql':'SELECT p.patient_id,1 AS amount FROM patient p'}})
        name=first['result_view']
        query=(f'WITH {name} AS(SELECT -100 AS amount) SELECT SUM(amount)AS total FROM temp.{name}'
               if shadow else f'SELECT SUM(amount)AS total FROM {name}')
        second=tools({'name':'query_sql','input':{'sql':query}})
        calls=[{'name':'query_sql','input':{'sql':first['executed_query']},'result':first},
               {'name':'query_sql','input':{'sql':query},'result':second}]
        return graph(second['executed_query']),calls,second

    def issues(self,g,calls,**kw):
        return F.premise_issues(self.fixture.path,'Inspect native totals',g,native_calls=calls,**kw)

    def test_complete_native_chain_reopens_without_executing_source_or_model(self):
        g,calls,result=self.prepare();before=copy.deepcopy(calls)
        self.assertEqual(self.issues(g,calls),[]);self.assertEqual(calls,before)
        self.assertTrue(any('agere_result_'in s for s in F.premise_issues(self.fixture.path,'Inspect',g)))
        answer={'status':'partial','answer_type':'table','table':result['rows'],'headline':'Native totals',
                'actionable_cohort':False,'missing':[]}
        saved=A.capture(self.fixture.path,'Inspect native totals',{'answer':answer,'graph':g,'investigation':{'tool_calls':calls}})
        reopened=A.load(self.fixture.path,saved['id'],current=True)
        self.assertEqual(reopened['answer'],answer);self.assertFalse(reopened['invalidated'])

    def test_changed_missing_duplicate_and_forward_native_sources_fail_closed(self):
        g,calls,_=self.prepare()
        for kind in ('value','missing','duplicate','forward','statement','columns'):
            changed=copy.deepcopy(calls)
            if kind=='value':changed[0]['result']['rows'][0]['amount']=99
            if kind=='missing':changed=changed[1:]
            if kind=='duplicate':changed.insert(1,copy.deepcopy(changed[0]))
            if kind=='forward':changed.reverse()
            if kind=='statement':changed[1]['result']['executed_query']+=' WHERE amount>0'
            if kind=='columns':changed[0]['result']['columns']=['different']
            with self.subTest(kind=kind):self.assertTrue(self.issues(g,changed))

    def test_unknown_names_and_changed_graph_query_do_not_inherit_native_identity(self):
        g,calls,_=self.prepare()
        for query in ('SELECT * FROM agere_result_999','SELECT * FROM main.agere_result_1',
                      'SELECT amount FROM agere_result_1 WHERE amount>0'):
            with self.subTest(query=query):self.assertTrue(self.issues(graph(query),calls))

    def test_native_column_check_preserves_real_temp_binding_despite_cte_shadow(self):
        g,calls,result=self.prepare(shadow=True)
        self.assertEqual(result['rows'],[{'total':12}]);self.assertEqual(self.issues(g,calls),[])

    def test_physical_producer_schema_is_still_required(self):
        g,calls,_=self.prepare()
        self.assertTrue(any('patient' in s for s in self.issues(g,calls,schema={})))
        self.assertTrue(any('patient.patient_id'in s for s in self.issues(g,calls,schema={'patient':['other']})))

    def test_malformed_native_provenance_is_not_an_exception_bypass(self):
        g,calls,_=self.prepare();calls[1]['result']['native_query_receipt']['source_bindings']=None
        self.assertTrue(self.issues(g,calls))


if __name__=='__main__':unittest.main()
