"""Service output boundaries preserve bytes, permissions, source guards and usage."""
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from clinical_core.agere.clinical import semantic_analyst as S,context_student as C
from clinical_core.agere import model_policy as M
from clinical_core.agere.gateway import BudgetLedger


def response(text,stop='end_turn'):
    return {'model':M.HAIKU,'content':[{'type':'text','text':text}],
            'stop_reason':stop,'usage':{'input_tokens':3,'output_tokens':7}}


class OutputBoundary(unittest.TestCase):
    def run_parts(self,parts,*,compact=False,guard=None,send_hook=None):
        emitted=iter(parts);sent=[];executed=[]
        def send(body):
            sent.append(body)
            if send_hook:send_hook(len(sent))
            return next(emitted),.01,False
        def execute(use):executed.append(use['input']);return {'value':1}
        result=S.tool_loop('Count source records',[],[{'name':'query_sql'}],execute,send=send,
            model=M.HAIKU,max_tools=None,compact_execution_state=compact,continuation_guard=guard)
        return result,sent,executed

    def test_multiple_service_boundaries_join_exact_answer_and_meter_every_part(self):
        for compact in (False,True):
            parts=[response('{"status":"answered","value":"part ','max_tokens'),
                   response('one and ','max_tokens'),response('part two"}')]
            out,sent,executed=self.run_parts(parts,compact=compact)
            self.assertIsNone(out.get('error'));self.assertEqual(out['answer']['value'],'part one and part two')
            self.assertEqual(out['new_paid_api_calls'],3);self.assertEqual(out['output_tokens'],21)
            self.assertAlmostEqual(out['estimated_cost_usd'],.03);self.assertEqual(executed,[])
            self.assertEqual(len(out['output_continuation_receipts']),3)
            self.assertIn('part ',json.dumps(sent[1]['messages']))

    def test_partial_tool_action_executes_once_only_after_complete_response(self):
        text=json.dumps({'tool_use':{'name':'query_sql','input':{'sql':'SELECT 1'}}})
        out,_,executed=self.run_parts([response(text[:31],'max_tokens'),response(text[31:]),response('{"status":"answered","value":1}')])
        self.assertEqual(executed,[{'sql':'SELECT 1'}]);self.assertEqual(out['answer']['value'],1)
        self.assertEqual(out['new_paid_api_calls'],3)

    def test_changed_source_during_continuation_preserves_parts_without_execution(self):
        state={'source':'one'}
        text=json.dumps({'tool_use':{'name':'query_sql','input':{'sql':'SELECT 1'}}})
        def hook(turn):
            if turn==2:state['source']='two'
        out,_,executed=self.run_parts([response(text[:31],'max_tokens'),response(text[31:])],
            guard=lambda:dict(state),send_hook=hook)
        self.assertEqual(executed,[]);self.assertIn('Source/scope changed',out['error'])
        self.assertEqual(len(out['output_continuation_receipts']),2)

    def test_failed_continuation_retains_partial_text_and_known_prior_usage(self):
        emitted=iter([response('{"status":"answered",','max_tokens')])
        def send(body):
            try:return next(emitted),.01,False
            except StopIteration:raise TimeoutError('authored service failure')
        out=S.tool_loop('Question',[],[],lambda use:None,send=send,model=M.HAIKU,max_tools=None)
        self.assertEqual(out['final_text'],'{"status":"answered",')
        self.assertEqual(out['known_cost_usd'],.01);self.assertIsNone(out['estimated_cost_usd'])
        self.assertEqual(len(out['output_continuation_receipts']),1)


class FailureReceipts(unittest.TestCase):
    def test_failed_transport_retains_full_returned_usage_and_inert_partial_output(self):
        raw={'model':M.HAIKU,'usage':{'input_tokens':3,'output_tokens':9},
             'subscription_cli_result':{'is_error':True,'result':'partial native output','usage':{'output_tokens':9}}}
        error=RuntimeError('authored terminal failure');error.provider_response=raw
        def transport(*a,**kw):raise error
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'receipts';ledger=BudgetLedger(Path(d)/'ledger.sqlite3')
            with patch.dict('os.environ',{'ANTHROPIC_API_KEY':'subscription-transport'}):
                with self.assertRaises(RuntimeError):C.send({'model':M.HAIKU},'claude',ledger,out,parse=lambda x:x,transport=transport)
            status,returned=ledger.db.execute('SELECT status,response FROM calls').fetchone()
            self.assertEqual(status,'failed');self.assertEqual(json.loads(returned),raw)
            failure=json.loads(next(out.glob('*-failure.json')).read_text())
            self.assertEqual(failure['returned_usage'],raw['usage'])
            self.assertTrue(any(json.loads(p.read_text()).get('response')==raw for p in out.glob('*.json')))
            ledger.close()

    def test_unwritable_receipts_fail_before_reserving_or_calling_provider(self):
        with tempfile.TemporaryDirectory() as d:
            ledger=BudgetLedger(Path(d)/'ledger.sqlite3')
            with patch.dict('os.environ',{'ANTHROPIC_API_KEY':'subscription-transport'}),patch.object(C.tempfile,'TemporaryFile',side_effect=PermissionError('authored')),patch.object(C.urllib.request,'urlopen') as send:
                with self.assertRaises(PermissionError):C.send({'model':M.HAIKU},'claude',ledger,Path(d)/'receipts',parse=lambda x:x)
            send.assert_not_called();self.assertEqual(ledger.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0],0)
            ledger.close()


if __name__=='__main__':unittest.main()
