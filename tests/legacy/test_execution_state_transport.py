"""Compiled-state wire encoding preserves evidence and failed-attempt receipts."""
import copy,json,os,unittest
from unittest.mock import patch
from clinical_core.agere.clinical import execution_state as E,semantic_analyst as S
from clinical_core.agere.deliverables.receipts import uncompact_json

class StateTransport(unittest.TestCase):
    def test_every_row_and_json_string_survives_with_smaller_wire(self):
        state={'schema':E.VERSION,'original_request':'Keep every patient and date.',
            'rows':[{'patient_id':str(i),'source':'Complete source statement. '*100,
                'unknown':None,'nested_json':json.dumps([{'status':'unknown','date':'2030-01-01'}])}for i in range(65)]}
        original=[{'role':'user','content':json.dumps(state)}];before=copy.deepcopy(original)
        encoded,proofs=E.transport_messages(original)
        self.assertEqual(uncompact_json(json.loads(encoded[0]['content'])),state)
        self.assertEqual(original,before);self.assertLess(len(encoded[0]['content']),len(original[0]['content']))
        self.assertTrue(proofs[0]['all_native_values_retained'])

    def test_tool_protocol_and_nonstate_messages_remain_identical(self):
        messages=[{'role':'user','content':'not JSON'}, {'role':'assistant','content':[{'type':'tool_use','id':'t','name':'query_sql','input':{}}]},
            {'role':'user','content':[{'type':'tool_result','tool_use_id':'t','content':'complete output'}]}]
        encoded,proofs=E.transport_messages(messages);self.assertEqual(encoded,messages);self.assertEqual(proofs,[])

    def test_failed_send_keeps_actual_prompt_size_and_unknown_cost(self):
        def fail(body):raise RuntimeError('authored context refusal')
        with patch.dict(os.environ,{'AGERE_EXECUTION_WIRE_ENCODING':'on'}):
            result=S.tool_loop('Original question','Instructions',[],lambda _:None,send=fail,model='authored',compact_execution_state=True,max_tools=None)
        self.assertIn('context refusal',result['error']);self.assertIsNone(result['estimated_cost_usd'])
        receipt=result['actor_prompt_receipts'][0]
        self.assertEqual(receipt['status'],'failed');self.assertGreater(receipt['message_bytes'],0)

    def test_prefix_cache_candidate_keeps_the_native_prefix_stable_state_on_the_wire(self):
        # The re-encoded state shares little prefix between turns; the cache candidate therefore sends the native form.
        seen=[]
        def capture(body):seen.append(body);raise RuntimeError('stop after capture')
        big={'schema':E.VERSION,'rows':[{'source':'Complete source statement. '*100}for _ in range(20)]}
        self.assertLess(len(E.transport_messages([{'role':'user','content':json.dumps(big)}])[0][0]['content']),len(json.dumps(big)))
        for flag,encoded in (('off',True),('on',False)):
            with patch.dict(os.environ,{'AGERE_EXECUTION_WIRE_ENCODING':'on','AGERE_SUBSCRIPTION_PREFIX_CACHE':flag}),\
                 patch.object(E,'transport_messages',wraps=E.transport_messages)as wire:
                result=S.tool_loop('Original question','Instructions',[],lambda _:None,send=capture,model='authored',compact_execution_state=True,max_tools=None)
            self.assertEqual(wire.called,encoded)
            self.assertEqual(result['actor_prompt_receipts'][0]['prefix_cache_candidate'],not encoded)
            self.assertEqual(json.loads(seen[-1]['messages'][0]['content'])['schema'],E.VERSION)

if __name__=='__main__':unittest.main()
