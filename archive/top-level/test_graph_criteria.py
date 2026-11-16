"""Criterion-to-decision binding and recomputation, without clinical claims."""
import copy,json,sqlite3,tempfile,unittest,zlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.contracts import digest
from clinical_core.agere.clinical import graph_criteria as C,graph_decision as G,care_protocols as P
import test_graph_decision as fixtures

class Binding(unittest.TestCase):
 def test_only_declared_pinned_protocol_nominates_review(self):
  p=P.definition('reduced_ef_therapy_review@2')
  row={'protocol':p,'review_candidate':True,'source':{'record_id':'r','record_hash':'h'}}
  self.assertEqual([x['need_tag'] for x in C.topics(row,'protocol_review')],['medication_review'])
  self.assertFalse(C.topics(row,'requirements'))
  self.assertFalse(C.topics({**row,'review_candidate':False},'protocol_review'))
  self.assertFalse(C.topics({**row,'protocol':{**p,'hash':'changed'}},'protocol_review'))
  self.assertFalse(C.topics({**row,'source':{}},'protocol_review'))
 def test_no_source_or_eligibility_guessed_from_receipt_hash(self):
  self.assertEqual(C.source_refs({'receipt_id':'a','receipt_hash':'b','record_id':'x'}),{})
  with self.assertRaises(ValueError):C.source_refs([{'record_id':'a','record_hash':'b'},{'record_id':'a','record_hash':'c'}])
 def test_membership_and_criterion_are_distinct(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp);db=C.R.storage(path)
   with db:
    for pid,status,value in [('yes','supported',True),('no','contradicted',False),('unknown','unresolved',None)]:
     row={'patient_id':pid,'id':pid,'status':status,'value':value}
     db.execute('INSERT INTO receipts VALUES (?,?,?)',(pid,pid,zlib.compress(json.dumps(row).encode())))
   db.close()
   with patch.object(C,'key',side_effect=lambda op,p,pid,**kw:pid):
    rows=C.member_index(path,'requirements',['yes','no','unknown'],{'filter':'supported'},{},{'yes'},{'unknown'})
   self.assertEqual([(r['value'],r['membership']) for r in rows],[(True,True),(False,False),(None,None)])
   self.assertTrue(all(not r['review_topics'] for r in rows))
 def test_nonmatched_and_unknown_members_not_service_candidates(self):
  ref={'review_topics':[{'need_tag':'medication_review'}],'membership':None}
  self.assertEqual(G.review_tags([ref]),[])
  self.assertEqual(G.review_tags([{**ref,'membership':False}]),[])
  self.assertEqual(G.review_tags([{**ref,'membership':True}]),['medication_review'])

class Connected(unittest.TestCase):
 def setUp(self):
  self.f=fixtures.GraphDecision();self.f.setUp();self.addCleanup(self.f.doCleanups)
  self.f.graph["new_paid_api_calls"]=0;self.f.update_graph()
 def test_bulk_preview_preserves_each_gap_with_constant_validations(self):
  f=self.f;m=f.member();m['evidence_gaps']*=10
  # A saved member can carry many exact gaps. Mock only the work lookup and
  # member currentness; compare every produced dependency to the old single API.
  with patch.object(G.Q,'load',return_value=m), patch.object(G.Q,'bound_task',return_value={'id':'task','owner':{'id':'owner'},'revision':1}), patch.object(G,'inspect_member',return_value=m) as check:
   out=fixtures.A.preview(f.retriever,{'decision_id':m['id'],'task_id':'task'})
   self.assertEqual(check.call_count,1)
   expected=[G.gap_dependency(f.retriever,m,{'kind':'graph_gap','gap_index':i}) for i in range(len(m['evidence_gaps']))]
   self.assertEqual(out['dependencies'],expected)
 def test_bulk_preview_rejects_changed_sources_at_final_check(self):
  f=self.f;m=f.member()
  with patch.object(G.Q,'load',return_value=m), patch.object(G.Q,'bound_task',return_value={'id':'task','owner':{'id':'owner'},'revision':1}), patch.object(G,'inspect_member',side_effect=ValueError('source changed')):
   with self.assertRaisesRegex(ValueError,'source changed'):fixtures.A.preview(f.retriever,{'decision_id':m['id'],'task_id':'task'})
 def test_refresh_keeps_original_question_and_prior_work(self):
  f=self.f;parent=f.prepare();member=f.member();task=f.queue(member)['task'];prior=f.graph['result_id']
  with patch('clinical_core.agere.clinical.analysis_graph.execute',return_value=f.graph) as execute:
   r=G.refresh(f.stack,{'decision_id':parent['id']})
  self.assertEqual(r['changes'],[]);self.assertFalse(r['task_state_changed'])
  self.assertEqual(r['current_decision_id'],parent['id']);self.assertEqual(r['previous_result_id'],prior)
  self.assertEqual(execute.call_args.kwargs['question'],parent['question'])
  self.assertEqual(len(G.Q.reopen(f.retriever,member['id'])['tasks']),1)
 def test_refresh_reports_changed_evidence_without_closing_task(self):
  f=self.f;parent=f.prepare();member=f.member();f.queue(member)
  f.graph['result_id']='c'*64;f.graph['results'][0]['result']['review_members'][0]['status']='new_source_status'
  with sqlite3.connect(f.path/'stack.sqlite3') as db:db.execute('INSERT INTO result_cache VALUES (?,?)',('c'*64,json.dumps(f.graph)))
  r=G.refresh(f.stack,{'decision_id':parent['id']})
  self.assertEqual(r['changed_member_count'],1);self.assertNotEqual(r['current_decision_id'],parent['id'])
  self.assertFalse(r['task_state_changed']);self.assertTrue(r['source_or_rule_change_is_not_clinical_change'])
 def test_no_scope_or_patient_override_during_refresh(self):
  parent=self.f.prepare()
  with self.assertRaises(ValueError):G.refresh(self.f.stack,{'decision_id':parent['id'],'patient_id':'other'})
 def test_refresh_rejects_member_as_population(self):
  m=self.f.member()
  with self.assertRaises(ValueError):G.refresh(self.f.stack,{'decision_id':m['id']})

if __name__=='__main__':unittest.main()
