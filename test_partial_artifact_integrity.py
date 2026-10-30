"""Generic inspection/proposal artifacts retain source gaps without authorizing work."""
import copy,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from clinical_core.agere.clinical import backstop_agent as B,workflow_contract as C,plan_fence as F
from clinical_core.agere.deliverables import workflow as W

class Proposal(unittest.TestCase):
 def step(self,kind='deliverable',purpose='proposal'):
  return {'kind':kind,'title':'Navigation design','template':'brief','quote':'create a navigation brief','unresolved':[],'purpose':purpose}
 def test_only_explicit_deliverable_purpose_is_accepted(self):
  for purpose in ('final','inspection','proposal'):
   self.assertEqual(C.validate([self.step(purpose=purpose)],'Show received facts; create a navigation brief')[0]['purpose'],purpose)
  with self.assertRaises(ValueError):C.validate([self.step(purpose='execute')],'Show facts; create a navigation brief')
  bad={'kind':'simulation','title':'Run','program':'custom','quote':'create an agent','unresolved':[],'purpose':'proposal'}
  with self.assertRaises(ValueError):C.validate([bad],'Show facts; create an agent')
 def test_complete_table_survives_unresolved_downstream_definition(self):
  a={'status':'partial','answer_type':'table','table_from_tool':'agere_result_1','workflow':[{**self.step(),'unresolved':['Define the audience']} ]}
  calls=[{'result':{'result_view':'agere_result_1','columns':['patient_id','value'],'rows':[{'patient_id':'A','value':None}],'truncated':False}}]
  out=B.review_final(None,'Show facts; create a navigation brief',a,calls,None,ledger=None,as_of='2030-05-27')
  self.assertEqual(out['status'],'repair');self.assertEqual(a['table'],[{'patient_id':'A','value':None}]);self.assertTrue(a['table_binding']['copied_from_executed_result'])
 def test_truncated_table_is_still_rejected(self):
  a={'status':'partial','table_from_tool':'agere_result_1','workflow':[self.step()]}
  out=B.review_final(None,'Show facts; create a navigation brief',a,[{'result':{'result_view':'agere_result_1','truncated':True}}],None,ledger=None,as_of='2030-05-27')
  self.assertEqual(out['status'],'repair');self.assertNotIn('table',a)
 def test_partial_proposal_builds_while_final_and_patient_work_remain_blocked(self):
  c={'answer':{'status':'partial','missing':['Current treatment unknown']},'workflow':[self.step(),self.step(purpose='final'),{'kind':'simulation','title':'Agent','program':'custom','unresolved':[]}],'patient_ids':['A']}
  saved=[]
  with patch.object(W.A,'load',return_value=c),patch.object(W,'read',return_value={}),patch.object(W,'_save',side_effect=lambda *a:saved.append(a[-1])),patch.object(W.A,'compose_frontier',return_value={'status':'delivered','id':'D-1','costs':{'known_api_estimate_usd':.01,'api_cost_known':True},'new_paid_api_calls':1}) as compose,patch.object(W.A,'create_work',side_effect=AssertionError('Partial evidence cannot authorize patient work')):
   result=W.materialize(Path('/authored'),'ANS-authored',allow_live=True,generation='frontier')
  self.assertEqual([s['status'] for s in result['steps']],['ready','blocked','blocked']);compose.assert_called_once()
  self.assertIn('unknowns and open items',compose.call_args.args[1]['request']);self.assertFalse(result['external_dispatch_performed'])
 def test_stale_or_invalid_source_is_not_bypassed_by_proposal(self):
  with patch.object(W.A,'load',side_effect=ValueError('source changed')),patch.object(W.A,'compose_frontier',side_effect=AssertionError('No generation from invalid source')):
   with self.assertRaises(ValueError):W.materialize(Path('/authored'),'ANS-authored',allow_live=True,generation='frontier')

class Views(unittest.TestCase):
 def test_registered_definition_views_are_checked_with_real_source_schema(self):
  with tempfile.TemporaryDirectory() as t:
   with sqlite3.connect(Path(t)/'semantic.sqlite3') as d:
    d.executescript("CREATE TABLE meta(key,value); INSERT INTO meta VALUES('as_of','2030-05-27'); CREATE TABLE patient(patient_id,birth_date); CREATE TABLE death(patient_id);")
   schema=F._schema(t)
   self.assertIn('living_patient',schema);self.assertIn('age',schema['living_patient']);self.assertNotIn('phenotype_not_registered',schema)
   graph={'nodes':[{'operation':'sql','parameters':{'query':'SELECT patient_id FROM living_patient'}}]}
   self.assertEqual(F.premise_issues(t,'authored',graph),[])
   graph['nodes'][0]['parameters']['query']='SELECT missing FROM living_patient p WHERE p.missing=1'
   self.assertTrue(any('missing' in x for x in F.premise_issues(t,'authored',graph)))

if __name__=='__main__':unittest.main()
