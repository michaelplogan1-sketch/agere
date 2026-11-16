"""Complete native origins are retained once and bound to exact descendant SQL."""
import copy,json,sqlite3,unittest
from unittest.mock import patch
from clinical_core.agere.clinical import sql_query as Q,harness_improvement as H,execution_state as E
import test_documentary_tool_handoff as fixtures


class BoundNativeQueries(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.DocumentaryHandoff();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)

    def source(self,tools,elapsed=0.1):
        return tools.retain_result({'columns':['record_id','amount','payload'],
            'rows':[{'record_id':'R'+str(i),'amount':i,'payload':"authored O'Brien β "+('x'*4096)}for i in range(50)],
            'source_contracts':{'schema':'executed-source-contracts/1','source_digest':'authored-v1','as_of':'2029-08-15'},
            'elapsed_s':elapsed})

    def query(self,tools,source):
        view=source['result_view']
        sql=f"WITH {view} AS (SELECT -100 AS amount) SELECT SUM(amount) AS total FROM temp.{view} WHERE amount >= 10"
        return tools({'name':'query_sql','input':{'sql':sql}})

    def test_actual_query_is_compact_and_standalone_export_is_exact(self):
        t=self.fixture.tools();source=self.source(t);result=self.query(t,source)
        self.assertEqual(result['rows'],[{'total':sum(range(10,50))}])
        self.assertEqual(result['executed_query'],result['declared_executed_query'])
        self.assertEqual(result['native_query_receipt']['schema'],'native-bound-query/1')
        self.assertNotIn('authored O',json.dumps(result))
        self.assertLess(len(json.dumps(result)),len(json.dumps(source))/10)
        self.assertTrue(Q.validate_bound_native_query(result,t.executed_results))
        sql=Q.resolve_bound_native_query(result,t.executed_results)
        with sqlite3.connect(':memory:')as db:self.assertEqual(db.execute(sql).fetchall(),[(sum(range(10,50)),)])
        state=E.catalog([{'name':'fixture_source','result':source},{'name':'query_sql','result':result}])[-1]
        self.assertEqual(state['executed_query'],result['executed_query'])
        self.assertEqual(state['native_query_receipt'],result['native_query_receipt'])

    def test_missing_or_changed_data_provenance_statement_and_read_bindings_fail_closed(self):
        t=self.fixture.tools();source=self.source(t);result=self.query(t,source);name=source['result_view']
        self.assertFalse(Q.validate_bound_native_query(result,{}))
        for change in (
            lambda r:r['rows'][0].update(amount=100),
            lambda r:r['rows'][0].update(record_id='another source'),
            lambda r:r['source_contracts'].update(source_digest='changed'),
            lambda r:r['source_contracts'].update(as_of='2030-01-01'),
            lambda r:r.update(incomplete_dependencies=[{'reason':'unresolved'}])):
            changed=copy.deepcopy(source);change(changed)
            with self.subTest(change=change):
                self.assertFalse(Q.validate_bound_native_query(result,{name:changed}))
                with self.assertRaises(ValueError):Q.resolve_bound_native_query(result,{name:changed})
        for change in (
            lambda r:r.update(executed_query=r['executed_query']+' AND amount<20'),
            lambda r:r.update(read_views=[]),
            lambda r:r['native_read_columns']['sources'][name].update(columns=[]),
            lambda r:r['native_query_receipt'].update(source_bindings=[]),
            lambda r:r['native_query_receipt']['source_bindings'][0].update(result_view=[])):
            changed=copy.deepcopy(result);change(changed)
            with self.subTest(change=change):self.assertFalse(Q.validate_bound_native_query(changed,{name:source}))

    def test_source_must_precede_the_query_and_duplicate_origins_are_ambiguous(self):
        t=self.fixture.tools();source=self.source(t);result=self.query(t,source)
        producer={'name':'fixture_source','result':source};consumer={'name':'query_sql','result':result}
        self.assertEqual(Q.native_query_binding_errors([producer,consumer]),[])
        self.assertTrue(Q.native_query_binding_errors([consumer,producer]))
        self.assertTrue(Q.native_query_binding_errors([producer,producer,consumer]))

    def test_changed_binding_is_stopped_before_model_review(self):
        from clinical_core.agere.clinical import backstop_agent as B
        t=self.fixture.tools();source=self.source(t);result=self.query(t,source)
        source['rows'][0]['amount']=999
        calls=[{'name':'fixture_source','result':source},{'name':'query_sql','result':result}]
        review=B.review_final(None,'Inspect sources',{'status':'answered'},calls,None,ledger=None,as_of='2029-08-15')
        self.assertEqual(review['repair_kind'],'native_query_binding')
        self.assertEqual(review['status'],'repair');self.assertEqual(review['calls'],0)

    def test_standalone_export_requires_transitive_bound_origins(self):
        t=self.fixture.tools();source=self.source(t);first=self.query(t,source)
        second=t({'name':'query_sql','input':{'sql':f"SELECT total+1 AS adjusted FROM {first['result_view']}"}})
        with self.assertRaises(ValueError):Q.resolve_bound_native_query(second,{first['result_view']:first})
        with sqlite3.connect(':memory:')as db:
            sql=Q.resolve_bound_native_query(second,t.executed_results)
            self.assertEqual(db.execute(sql).fetchall(),[(sum(range(10,50))+1,)])

    def test_final_scope_warnings_are_bound_before_native_registration(self):
        t=self.fixture.tools();source=self.source(t)
        with patch('clinical_core.agere.clinical.drug_classes.group_query_issues',return_value=['all-history drug_fill membership requires a declared window']):
            first=self.query(t,source)
        second=t({'name':'query_sql','input':{'sql':f"SELECT total FROM {first['result_view']}"}})
        self.assertTrue(Q.validate_bound_native_query(second,t.executed_results))
        self.assertTrue(first['scope_warnings'])

    def test_chained_replay_normalizes_timing_only_after_each_raw_binding(self):
        results=[]
        for timing in (0.1,0.9):
            t=self.fixture.tools();source=self.source(t,timing);first=self.query(t,source)
            second=t({'name':'query_sql','input':{'sql':f"SELECT total+1 AS adjusted FROM {first['result_view']}"}})
            prior=[{'name':'fixture_source','result':source},{'name':'query_sql','result':first}]
            results.append(H.comparison_result('query_sql',second,prior))
        self.assertEqual(*results)

    def test_replay_normalizes_only_permitted_native_timing_after_raw_validation(self):
        t=self.fixture.tools();first=self.source(t,0.1);a=self.query(t,first)
        t2=self.fixture.tools();second=self.source(t2,0.9);b=self.query(t2,second)
        before=copy.deepcopy(a)
        expected=H.comparison_result('query_sql',a,[{'result':first}])
        self.assertEqual(expected,H.comparison_result('query_sql',b,[{'result':second}]))
        self.assertEqual(a,before)
        altered=copy.deepcopy(first);altered['rows'][0]['amount']=True
        self.assertNotEqual(expected,H.comparison_result('query_sql',a,[{'result':altered}]))

    def test_explicit_legacy_strategy_retains_exact_inline_portability(self):
        t=self.fixture.tools();source=self.source(t)
        with patch.dict('os.environ',{'AGERE_NATIVE_QUERY_RECEIPT':'inline'}):result=self.query(t,source)
        self.assertNotIn('native_query_receipt',result)
        self.assertIn('authored O',result['executed_query'])
        with sqlite3.connect(':memory:')as db:self.assertEqual(db.execute(result['executed_query']).fetchall(),[(sum(range(10,50)),)])

    def test_operational_review_read_is_portable_without_becoming_source_evidence(self):
        from clinical_core.agere.clinical import plan_fence as P,result_assembly as A
        t=self.fixture.tools();source=self.source(t)
        feedback={'status':'repair','feedback':"Inspect O'Brien's annual scope β",
            'support':{'packets':[{'id':'summary','label':'unknown'}]}}
        reference=E.review_reference(feedback,t)
        query="SELECT json_extract(feedback_json,'$.feedback') AS guidance FROM "+reference['result_view']
        reading=t({'name':'query_sql','input':{'sql':query}})
        self.assertNotIn('native_query_receipt',reading)
        self.assertEqual(reading['declared_executed_query'],query)
        self.assertIn('feedback_json',reading['executed_query'])
        with sqlite3.connect(':memory:')as db:
            self.assertEqual(db.execute(reading['executed_query']).fetchall(),[(feedback['feedback'],)])
        self.assertTrue(A.incomplete_reason(reading))
        self.assertIn('unverified_review_feedback',json.dumps(reading['incomplete_dependencies']))
        final=self.query(t,source)
        calls=[{'name':'fixture_source','result':source},
            {'name':'query_sql','input':{'sql':query},'result':reading},
            {'name':'query_sql','result':final}]
        self.assertEqual(Q.native_query_binding_errors(calls),[])
        _,errors=P.native_premise_graph({'nodes':[]},calls)
        self.assertEqual(errors,[])
        # Changing operational state later cannot rewrite its captured origin.
        t.executed_results[reference['result_view']]['rows'][0]['feedback_json']='changed'
        with sqlite3.connect(':memory:')as db:
            self.assertEqual(db.execute(reading['executed_query']).fetchall(),[(feedback['feedback'],)])

    def test_feedback_names_do_not_bypass_ordinary_missing_receipt_guards(self):
        t=self.fixture.tools();source=self.source(t);result=self.query(t,source)
        fake=copy.deepcopy(source);fake['result_view']='agere_review_feedback_'+'a'*64
        # An ordinary result only carrying a feedback-like name is not a
        # registered operational receipt and cannot escape exact hash binding.
        result['native_query_receipt']['source_bindings'][0]['result_view']=fake['result_view']
        self.assertFalse(Q.validate_bound_native_query(result,{fake['result_view']:fake}))


if __name__=='__main__':unittest.main()
