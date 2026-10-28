"""Explicit read-only recovery does not reuse answers or select learning candidates."""
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from clinical_core.agere.clinical import harness_improvement as H
import run_automatic_workflow as W

class Tools:
 def __init__(self):self.calls=[];self.source='same';self.fail=False
 def continuation_identity(self):return {'source':self.source}
 def __call__(self,action):self.calls.append(action);return {'error':'missing'} if self.fail else {'rows':[{'value':'fresh'}]}

class Recovery(unittest.TestCase):
 def test_literal_native_execution_and_no_final_answer_or_selection(self):
  t=Tools();actions=[{'name':'query_sql','input':{'sql':'SELECT 1'}},{'name':'review_documents','input':{'patient_ids':['A'],'questions':{'x':'authored'}}}]
  with H.owned_recovery(actions,'q'):
   r=H.recover_actions(t,'q');self.assertIsNone(H.recover_actions(t,'different question'))
  self.assertEqual(t.calls,actions);self.assertIsNone(r['final_answer']);self.assertIsNone(r['used']);self.assertIsNone(H.recover_actions(t,'q'))
 def test_failed_tool_stops_the_old_prefix(self):
  t=Tools();t.fail=True
  with H.owned_recovery([{'name':'query_sql','input':{'sql':'SELECT 1'}}]*2,'q'):r=H.recover_actions(t,'q')
  self.assertEqual(len(t.calls),1);self.assertEqual(r['calls'][0]['result'],{'error':'missing'})
 def test_unknown_action_or_changed_state_cannot_continue(self):
  t=Tools()
  with H.owned_recovery([{'name':'send_device','input':{}}],'q'):
   with self.assertRaises(ValueError):H.recover_actions(t,'q')
  def changed(action):t.source='changed';return {}
  class Changing(Tools):
   def __call__(self,action):self.source='changed';return {}
  with H.owned_recovery([{'name':'query_sql','input':{'sql':'SELECT 1'}}],'q'):
   with self.assertRaisesRegex(ValueError,'changed'):H.recover_actions(Changing(),'q')
 def test_attempt_must_bind_same_question_runtime_and_source(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);runtime=p/'runtime';runtime.mkdir();request={'question':'authored question','scope':'warehouse'}
   x={'question':request['question'],'synthetic':True,'action_context':{'id':'ANS-owned'},'investigation':{'tool_calls':[{'name':'query_sql','input':{'sql':'SELECT 1'},'result':{'untrusted':'ignored'}}]},'answer':{'untrusted':'ignored'}}
   (p/'result.json').write_text(json.dumps(x));(p/'preflight.json').write_text(json.dumps({'runtime':str(runtime)}))
   with patch('clinical_core.agere.deliverables.answer_context.load',return_value={'source':{'id':'same'},'question':request['question']}),patch('clinical_core.agere.deliverables.answer_context.source_identity',return_value={'id':'same'}):
    actions,proof=W.retained_actions(p,runtime,request);self.assertEqual(actions,[{'name':'query_sql','input':{'sql':'SELECT 1'}}]);self.assertFalse(proof['reuse_final_answer'])
    with self.assertRaises(ValueError):W.retained_actions(p,runtime,{'question':'changed','scope':'warehouse'})
    with self.assertRaises(ValueError):W.retained_actions(p,runtime,{**request,'scope':'selected'})
   with patch('clinical_core.agere.deliverables.answer_context.load',return_value={'source':{'id':'old'},'question':request['question']}),patch('clinical_core.agere.deliverables.answer_context.source_identity',return_value={'id':'new'}):
    with self.assertRaisesRegex(ValueError,'source/question changed'):W.retained_actions(p,runtime,request)

if __name__=='__main__':unittest.main()
