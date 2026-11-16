"""The direct baseline serializes real tool calls and unconstrained findings."""
import copy
import json
import sqlite3
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere import subscription_transport as T
from clinical_core.agere.clinical import backstop_agent as B,semantic_analyst as S
from tools.ordinary_stack_comparison import FRONTIER_OUTPUT_CONFIG
from clinical_core.agere.clinical import system_one_gate as G
import test_shared_planner as fixture


class OrdinaryTransport(unittest.TestCase):
    def test_native_decision_does_not_accept_transcribed_table_before_product_review(self):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        db.execute('CREATE TABLE received(patient_id,status)')
        db.executemany('INSERT INTO received VALUES (?,?)',[('A','unresolved'),('B',None),('A','supported')])
        tools=SimpleNamespace(read={},executed_results={},document_reviews=[])
        sql="SELECT COUNT(*) AS receipt_rows,COUNT(DISTINCT patient_id) AS patients FROM received"
        final={'status':'answered','answer_type':'table','headline':'Received row and patient counts',
               'table_from_tool':'agere_result_1','sources':[]}
        responses=[{'structured_output':{'decision':{'tool_use':{'name':'query_sql','input':{'sql':sql}}}}},
            {'structured_output':{'decision':{**final,'table_from_tool':None,'table':[{'receipt_rows':2,'patients':2}]}}},
            {'structured_output':{'decision':final}}]
        bodies=[];reviews=[]
        def execute(call):
            cur=db.execute(call['input']['sql'])
            out={'result_view':'agere_result_1','columns':[c[0]for c in cur.description],
                 'rows':cur.fetchall(),'truncated':False,'executed_query':call['input']['sql']}
            tools.executed_results[out['result_view']]=out
            return out
        def send(body):bodies.append(copy.deepcopy(body));return T.respond(body),0,False
        def review(answer,calls):
            out=B.review_final(SimpleNamespace(), 'Count received rows and distinct patients.',answer,calls,tools,
                               ledger=None,as_of='2030-05-27')
            reviews.append(copy.deepcopy(out));return out
        coverage={'label':'answers_fully','decided_by':'authored_fixture','p':.99,'calls':0,'cost_usd':0}
        support={'label':'supported','decided_by':'authored_fixture','calls':0,'cost_usd':0,
                 'packets':[],'all_dependencies_checked':True}
        with patch.object(T,'run_cli',side_effect=responses)as cli,patch.object(G,'ready',return_value=True), \
                patch.object(G,'s1',return_value=SimpleNamespace(decide=lambda *a,**k:None,judge_fields=lambda *a,**k:None)),patch.object(G,'check',return_value=coverage), \
                patch.object(B,'check_support_packets',return_value=support):
            result=S.tool_loop('Count received rows and distinct patients.','Use the received evidence.',
                [{'name':'query_sql','input_schema':{'type':'object','properties':{'sql':{'type':'string'}},'required':['sql']}}],
                execute,send=B.as_tool_calls(send),model='claude-sonnet-5-5',max_tools=None,
                extra={'output_config':S.INVESTIGATOR_OUTPUT_CONFIG},review=review)
        self.assertEqual(len(reviews),2)
        self.assertEqual(reviews[0]['status'],'repair')
        self.assertIn('Do not transcribe',reviews[0]['feedback'])
        self.assertEqual(reviews[1]['status'],'confirmed',reviews[1])
        self.assertEqual(result['answer']['table'],[{'receipt_rows':3,'patients':2}])
        self.assertEqual(len(result['tool_calls']),1)
        self.assertEqual(cli.call_count,3)
        self.assertTrue(all(c.kwargs['json_schema']==T.decision_output_schema(b)for c,b in zip(cli.call_args_list,bodies)))

    def test_final_schema_keeps_repair_contract_without_output_caps(self):
        schema=S.INVESTIGATOR_OUTPUT_CONFIG['format']['schema']
        body={'output_config':S.INVESTIGATOR_OUTPUT_CONFIG,'tool_choice':{'type':'none'}}
        patch_value={'repair_draft':{'draft_digest':'retained','fields':[]}}
        with patch.object(T,'run_cli',return_value={'structured_output':patch_value})as cli:
            response=T.respond(body)
        self.assertEqual(json.loads(response['content'][0]['text']),patch_value)
        self.assertEqual(cli.call_args.kwargs['json_schema'],schema)
        self.assertTrue(schema['additionalProperties'])
        for key in ('maxLength','maxItems','maxProperties','max_tokens'):
            self.assertNotIn(key,json.dumps(schema))


class BaselineTransport(unittest.TestCase):
    def test_native_decisions_execute_sql_and_keep_free_form_final_content(self):
        tool={'name':'query_sql','description':'Read the received table',
              'input_schema':{'type':'object','properties':{'sql':{'type':'string'}},'required':['sql']}}
        final={'status':'answered','answer_type':'table','headline':'Recorded invoice totals',
               'facts':['A native computation, with arbitrary prose and literal values.'],
               'table':[{'customer':'A','cents':125}], 'unfamiliar_extension':{'meaning':'preserved'}}
        responses=[{'structured_output':{'decision':{'tool_use':{'name':'query_sql','input':{
            'sql':'SELECT customer,SUM(cents) AS cents FROM invoice GROUP BY customer'}}}}},
            {'structured_output':{'decision':final}}]
        bodies=[];queries=[]
        db=sqlite3.connect(':memory:')
        db.executescript("CREATE TABLE invoice(customer TEXT,cents INTEGER);INSERT INTO invoice VALUES('A',50),('A',75);")
        def execute(call):
            queries.append(call['input']['sql']);cur=db.execute(queries[-1])
            return {'columns':[c[0]for c in cur.description],'rows':cur.fetchall()}
        def send(body):
            bodies.append(copy.deepcopy(body));return T.respond(body),0,False
        try:
            with patch.object(T,'run_cli',side_effect=responses)as cli:
                result=S.tool_loop('Compute invoice totals','Use the received table',[tool],execute,
                    send=B.as_tool_calls(send),model='claude-sonnet-5-5',max_tools=None,
                    extra={'output_config':FRONTIER_OUTPUT_CONFIG},review=None)
        finally:db.close()
        self.assertEqual(result['answer'],final)
        self.assertEqual(queries,['SELECT customer,SUM(cents) AS cents FROM invoice GROUP BY customer'])
        self.assertEqual(result['tool_calls'][0]['result']['rows'],[('A',125)])
        self.assertEqual(cli.call_count,2)
        self.assertTrue(all('json_schema'in c.kwargs for c in cli.call_args_list))
        self.assertTrue(all(b['output_config']==FRONTIER_OUTPUT_CONFIG for b in bodies))
        self.assertFalse(result.get('answer_reviews'))

    def test_transport_does_not_weaken_per_turn_tool_permissions(self):
        tool={'name':'query_sql','input_schema':{'type':'object'}}
        body={'tools':[tool],'tool_choice':{'type':'none'},'output_config':FRONTIER_OUTPUT_CONFIG}
        self.assertEqual(T.decision_output_schema(body),FRONTIER_OUTPUT_CONFIG['format']['schema'])
        with patch.object(T,'run_cli',return_value={'structured_output':{
            'tool_use':{'name':'query_sql','input':{'sql':'SELECT received'}}}}):r=T.respond(body)
        self.assertEqual(r['stop_reason'],'end_turn')
        self.assertFalse(any(b.get('type')=='tool_use'for b in r['content']))

    def test_no_length_or_evidence_guard_is_added_to_final_schema(self):
        s=FRONTIER_OUTPUT_CONFIG['format']['schema']
        self.assertTrue(s['additionalProperties'])
        for key in ['maxLength','maxItems','maxProperties','max_tokens','computed_prose','verification']:
            self.assertNotIn(key,json.dumps(s))


class BaselineAccess(unittest.TestCase):
    setUp = fixture.SharedPlanner.setUp

    def test_raw_schema_has_only_sql_and_all_received_schema_without_governed_answers(self):
        from tools import ordinary_stack_comparison as study
        from clinical_core.agere.clinical import together,spec_exec,analyst_definitions
        def warehouse(_):return S.SemanticWarehouse(self.path/'semantic.sqlite3')
        with patch.object(together,'warehouse',side_effect=warehouse), \
             patch.object(spec_exec,'ledger_for',return_value=nullcontext(None)), \
             patch.object(S,'semantic_context',side_effect=AssertionError('No governed documentation in raw arm')), \
             patch.object(analyst_definitions,'agere_context',side_effect=AssertionError('No owner definitions in raw arm')):
            result=study.frontier(self.stack,'Count received records',execute=False,access='raw_schema',
                                  given_inputs={'authored_synthetic':True,'benchmark_cents':125})
        meta=result['baseline_configuration']
        self.assertEqual(meta['access'],'raw_schema')
        self.assertEqual(meta['tool_names'],['query_sql'])
        self.assertEqual(result['provider_calls'],0)
        self.assertIsNone(meta['output_limits'])
        self.assertFalse(meta['mandatory_answer_review'])

    def test_raw_arm_rejects_unexposed_native_tool_and_keeps_given_inputs(self):
        from tools import ordinary_stack_comparison as study
        from clinical_core.agere.clinical import together,spec_exec,legacy_proposers
        inputs={'authored_synthetic':True,'period':{'start':'2029-01-01','end':'2029-12-31'}}
        def run(payload,system,tools,execute,**kwargs):
            self.assertEqual(json.loads(payload)['given_inputs'],inputs)
            self.assertEqual([t['name']for t in tools],['query_sql'])
            self.assertIn('new_sensor_feed',system[1]['text'])
            with self.assertRaisesRegex(ValueError,'outside this baseline access'):
                execute({'name':'execute_analysis','input':{'operation':'contract_performance'}})
            return {'answer':{'status':'unsupported','answer_type':'table','headline':'Authored access-boundary fixture'},'tool_calls':[]}
        with patch.object(together,'warehouse',side_effect=lambda _:S.SemanticWarehouse(self.path/'semantic.sqlite3')), \
             patch.object(spec_exec,'ledger_for',return_value=nullcontext(None)), \
             patch.object(legacy_proposers,'_send',return_value=lambda _:None), \
             patch.object(S,'tool_loop',side_effect=run):
            result=study.frontier(self.stack,'Count received records',execute=True,access='raw_schema',given_inputs=inputs)
        self.assertEqual(result['answer']['status'],'unsupported')


if __name__=='__main__':unittest.main()
