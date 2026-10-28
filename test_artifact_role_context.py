"""Artifact role keeps clinical data while referring to native executable provenance."""
import copy,importlib,json,unittest
from clinical_core.agere.deliverables.receipts import Bundle,render_projection,render_for_model,uncompact_json,fingerprint
from clinical_core.agere.clinical import harness_improvement as H

C=importlib.import_module('clinical_core.agere.deliverables.compose')

class RoleContext(unittest.TestCase):
 def bundle(self):
  sql='WITH raw AS (SELECT '+"'received literal'"+' AS observed) SELECT * FROM raw '+('/* proof */ '*1000)
  quote='SELECT and graph/sql are literal clinical source text, not executable fields.'
  row={'patient_id':'A','review_json':json.dumps({'reason':[{'value':'Cannot afford prescription','quote':quote,'record_id':'R1','date':'2030-01-01'}]}),'sql':quote}
  context={'id':'owned','question':'Inspect all findings and create a proposed service','source':{'as_of':'2030-05-27'},
           'answer':{'status':'partial','table':[row],'table_binding':{'query':sql},'investigation_review':{'status':'repair','support':{'label':'supported','decided_by':'fixture'}}},
           'graph':{'nodes':[{'parameters':{'query':sql}}]},'planner_receipt':{'request_contract':{'graph':{'nodes':[{'parameters':{'query':sql}}]}}},
           'investigation':{'tool_calls':[{'name':'query_sql','input':{'sql':sql},'sql':sql,'result':{'executed_query':sql,'rows':[row]}}],
                            'answer_reviews':[{'support':{'label':'unknown'},'reason':'Historical check failed'}],
                            'document_reviews':[{'rows':[row],'manifest':[{'patient_id':'A','received_documents':4}],'typed_checks':[{'label':'unknown'}]}]}}
  b=Bundle({'frozen_evidence':True});rid=b.add('saved_answer',{'context_id':'owned'},context);return b,rid
 def test_only_declared_executable_provenance_changes_and_every_clinical_value_remains(self):
  b,rid=self.bundle();before=copy.deepcopy(b.to_json());p=render_projection(b);data=p['data'][0]['data']
  self.assertEqual(data['answer']['table'],b.results[rid]['answer']['table'])
  self.assertEqual(data['investigation']['document_reviews'],b.results[rid]['investigation']['document_reviews'])
  self.assertEqual(data['investigation']['tool_calls'][0]['result']['rows'],b.results[rid]['answer']['table'])
  self.assertEqual(data['investigation']['answer_reviews'],b.results[rid]['investigation']['answer_reviews'])
  self.assertEqual(len(p['sql_provenance']),6)
  for ref in p['sql_provenance']:
   self.assertEqual(len(b.get(ref['native_path'])),ref['characters']);self.assertIn('$agere_native_provenance',json.dumps(data))
  self.assertEqual(b.to_json(),before);self.assertEqual(p['current_saved_status'][0]['investigation_review']['support']['label'],'supported')
  self.assertEqual(p['current_saved_status'][0]['answer_status'],'partial')
 def test_compact_role_packet_round_trips_without_changing_native_binding_paths(self):
  b,rid=self.bundle();encoded=render_for_model(b);p=uncompact_json(json.loads(encoded))
  self.assertEqual(p,render_projection(b));self.assertEqual(b.get(rid+':answer.table.0.patient_id'),'A')
  self.assertIn('Native AGERE.get/data-bind paths are unchanged',p['instructions'])
 def test_frozen_compose_and_semantic_review_use_same_role_projection(self):
  b,rid=self.bundle();prompt=C._user_prompt('proposal','physician',b,'repair')
  self.assertIn('agere-artifact-render-view/1',prompt);self.assertIn('current_saved_status',prompt)
  self.assertIn('native SQL provenance referenced explicitly',prompt)
 def test_proposal_can_design_operating_process_without_inventing_actual_execution(self):
  b,rid=self.bundle();b.meta['deliverable_purpose']='proposal'
  prompt=C._user_prompt('Design the requested service','physician',b)
  self.assertIn('PROPOSED DESIGN CONTRACT',prompt);self.assertIn('responsible function',prompt)
  self.assertIn('not observed staffing',prompt);self.assertIn('Do not invent patient facts',prompt)
  b.meta['deliverable_purpose']='inspection'
  self.assertNotIn('PROPOSED DESIGN CONTRACT',C._user_prompt('Inspect evidence','physician',b))

 def test_document_search_replays_current_native_tool_and_never_an_old_answer(self):
  actions=[{'name':'search_documents','input':{'patient_id':'A','terms':'adverse reaction','date_to':'2030-05-27'}}]
  class Tools:
   def __init__(self):self.called=[]
   def continuation_identity(self):return {'source':'fixed','scope':['A']}
   def __call__(self,use):self.called.append(use);return {'passages':[{'record_id':'R1','passage':'Current native evidence'}]}
  tools=Tools()
  with H.owned_recovery(actions,'original question',[{'reason':'Review candidate conflict'}]):out=H.recover_actions(tools,'original question')
  self.assertEqual(tools.called,actions);self.assertEqual(out['calls'][0]['result']['passages'][0]['record_id'],'R1')
  self.assertIsNone(out['final_answer']);self.assertIsNone(out['used']);self.assertIn('search_documents',H.READ_ONLY)

if __name__=='__main__':unittest.main()
