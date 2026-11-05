"""The direct baseline serializes real tool calls and unconstrained findings."""
import copy
import json
import sqlite3
import unittest
from unittest.mock import patch
from clinical_core.agere import subscription_transport as T
from clinical_core.agere.clinical import backstop_agent as B,semantic_analyst as S
from tools.ordinary_stack_comparison import FRONTIER_OUTPUT_CONFIG


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


if __name__=='__main__':unittest.main()
