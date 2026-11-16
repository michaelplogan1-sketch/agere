import copy,unittest
from unittest.mock import patch
from clinical_core.agere.clinical import medication_review_gaps as G

class Gaps(unittest.TestCase):
 def setUp(self):
  self.selection={'id':'selection','patient_id':'p','context_id':'context','request':{'question_receipt_ids':['q']}}
  self.context={'id':'context','model_context':{'evidence':[{'id':'u1','source_ref':'r','kind':'narrative','field':'text'},{'id':'u2','source_ref':'r','kind':'structured','field':''}], 'sources':{'r':{'source':{'record_id':'r','patient_id':'p'}}}}}
  self.answer={'id':'answer','context_id':'context','patient_id':'p','coverage':{'unassessed_units':[{'source_unit_id':'u1','reason':'quarantined','disposition_authority':'system_missing_model_disposition'},{'source_unit_id':'u2','reason':'unclear'}]}}
  self.att={'id':'attachment','current':True,'request':{'selection_id':'selection','answer_id':'answer'}};self.rows=[self.att];self.partials=[]
  for module,name,fn in [(G.I,'listing',lambda *a:{'answers':copy.deepcopy(self.rows)}),(G.P,'listing',lambda *a:{'partials':copy.deepcopy(self.partials)}),(G.I,'inspect_selection',lambda *a:copy.deepcopy(self.selection)),(G.I.S,'inspect',lambda *a:copy.deepcopy(self.answer)),(G.I.S,'context',lambda *a:copy.deepcopy(self.context))]:
   p=patch.object(module,name,side_effect=fn);p.start();self.addCleanup(p.stop)
  self.request={'review_id':'review','answer_id':'answer'}
 def test_distinct_complete_records_and_original_questions(self):
  before=copy.deepcopy(self.answer);r=G.preview('.',self.request);self.assertEqual(r['counts'],{'unassessed_units':2,'records_to_review':1});self.assertEqual(r['selection_request'],{'review_id':'review','question_receipt_ids':['q'],'record_ids':['r']});self.assertEqual(self.answer,before);self.assertFalse(r['original_answer_changed']);self.assertFalse(r['failed_request_resent'])
 def test_partial_attachment_accepted_without_promoting_quarantine(self):
  self.rows=[];self.partials=[{**self.att,'answer_id':'answer'}];r=G.preview('.',self.request);self.assertEqual(r['records'][0]['unassessed_units'][0]['authority'],'system_missing_model_disposition');self.assertFalse(r['clinical_meaning_validated'])
 def test_stale_unattached_or_ambiguous_rejected(self):
  for rows in ([],[{**self.att,'current':False}],[self.att,self.att]):
   self.rows=rows
   with self.assertRaisesRegex(ValueError,'current exact-review'):G.preview('.',self.request)
 def test_cross_patient_or_context_rejected(self):
  for field in ('patient_id','context_id'):
   old=self.answer[field];self.answer[field]='foreign'
   with self.assertRaisesRegex(ValueError,'mismatch'):G.preview('.',self.request)
   self.answer[field]=old
 def test_duplicate_or_unknown_units_rejected(self):
  orig=copy.deepcopy(self.answer['coverage']['unassessed_units'])
  for rows in ([orig[0],orig[0]],[{**orig[0],'source_unit_id':'unknown'}]):
   self.answer['coverage']['unassessed_units']=rows
   with self.assertRaisesRegex(ValueError,'Unknown or duplicate'):G.preview('.',self.request)
 def test_empty_gap_and_no_authority_flags(self):
  self.answer['coverage']['unassessed_units']=[];self.assertFalse(G.preview('.',self.request)['records'])
  with self.assertRaises(ValueError):G.preview('.',{**self.request,'clinical_execution_authorized':True})

if __name__=='__main__':unittest.main()
