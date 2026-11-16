import json
from unittest.mock import Mock, patch
import unittest
from clinical_core.contracts import digest
from clinical_core.agere.clinical import question_context as qc, context_builder as ctx
from test_evidence_threads import Threads
from test_warehouse_interpretation import record, ASOF

class QuestionContext(unittest.TestCase):
 setUp=Threads.setUp
 put=Threads.put
 chain=Threads.chain
 def roots(self,rows):
  return Mock(search=Mock(return_value={'evidence':[{'source':{'record_id':r['record_id'],'patient_id':r['patient_id'],'record_hash':digest(r)},'text':'Source excerpt'} for r in rows], 'method':'existing hybrid retrieval','candidate_passages':12,'cache_hit':False,'elapsed_seconds':.1}))
 def test_reference_closure_reaches_all_categories_and_cycles(self):
  self.chain();rows=[json.loads(x[0]) for x in self.db.execute('SELECT payload FROM records')]
  p=qc.select_sources(rows,['a'],ASOF)
  self.assertEqual(p['connected_record_ids'],['a','b','c','d']);self.assertEqual(len(p['selected_categories']),3)
  self.assertEqual(len(p['category_reasons']['received_pharmacy_information']['linked_records']),1)
 def test_wrong_patient_and_wrong_type_links_never_expand_scope(self):
  a=record('a','received_pharmacy_information',request_record_id='b',original_response_record_id='c')
  b=record('b','medication_information_requests',patient_id='P-2',requested_information='OTHER PATIENT')
  c=record('c','financial_screens')
  p=qc.select_sources([a,b,c],['a'],ASOF);self.assertEqual(p['connected_record_ids'],['a'])
 def test_stale_or_cross_patient_retrieval_blocks_before_context(self):
  r=record('a','medication_information_requests');self.put(r)
  for bad in [{**r,'requested_information':'different'},{**r,'patient_id':'P-2'}]:
   with patch.object(ctx,'execute') as execute:
    with self.assertRaisesRegex(ValueError,'changed|another'):qc.prepare(self.path,'P-1','What is documented?',self.roots([bad]))
    execute.assert_not_called()
 def test_no_retrieval_is_not_absence_of_care(self):
  self.put(record('a','medication_information_requests'))
  p=qc.prepare(self.path,'P-1','Unmatched question',self.roots([]))
  self.assertEqual(p['status'],'no_retrieved_evidence');self.assertIsNone(p['context_id']);self.assertFalse(p['question_fully_answerable']);self.assertEqual(len(p['unselected_categories']),1)
 def test_connected_scope_excludes_unretrieved_same_category_records(self):
  self.chain();self.put(record('e','financial_screens'),record('f','medication_information_requests'))
  rows=[json.loads(x[0]) for x in self.db.execute("SELECT payload FROM records WHERE patient='P-1' ORDER BY id")]
  r=next(r for r in rows if r['record_id']=='a')
  def create(path,patient,question,ids):
   self.assertEqual(ids,['a','b','c','d']);self.assertNotIn('f',ids)
   return {'id':'b'*64,'budget':{},'omissions':[],'record_scope':{'record_ids':ids},'dispatch':{'source_review_allowed':True}}
  with patch.object(qc.record_context,'create',side_effect=create):
   p=qc.prepare(self.path,'P-1','What was received?',self.roots([r]))
  self.assertEqual(p['records_in_selected_categories'],5);self.assertEqual(p['selected_record_count'],4)
  self.assertEqual(p['unselected_categories'],[{'source':'financial_screens','received_records':1}]);self.assertEqual(p['status'],'ready_for_source_review');self.assertEqual(p['scope_sufficiency'],'unassessed')

if __name__=='__main__':unittest.main()
