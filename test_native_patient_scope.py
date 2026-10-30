"""Native role and medication domains survive result composition."""
import json,unittest
from clinical_core.agere.clinical import drug_classes as D

class NativeScope(unittest.TestCase):
 def test_current_flags_cannot_be_bound_to_all_history_membership(self):
  calls=[{'name':'query_sql','input':{'sql':'SELECT patient_id, CASE WHEN patient_id IN {{drug_fill:mra}} THEN 1 ELSE 0 END AS on_mra FROM patient'},
   'result':{'result_view':'agere_result_1','columns':['patient_id','on_mra'],'rows':[{'patient_id':'A','on_mra':1}]}},
   {'name':'query_sql','input':{'sql':'SELECT * FROM agere_result_1'},'result':{'result_view':'agere_result_2','columns':['patient_id','on_mra'],'rows':[{'patient_id':'A','on_mra':1}]}},
   {'name':'query_sql','input':{'sql':'SELECT patient_id, 0 AS on_mra FROM medication WHERE event_date > date(:as_of, \'-12 months\')'},
   'result':{'result_view':'agere_result_3','columns':['patient_id','on_mra'],'rows':[{'patient_id':'A','on_mra':0}]}}]
  self.assertTrue(any('all-history'in x for x in D.group_query_issues('What GDMT are they on?',calls,'agere_result_2')))
  self.assertFalse(D.group_query_issues('Review historical GDMT fills',calls,'agere_result_2'))
  self.assertFalse(D.group_query_issues('What GDMT are they on?',calls,'agere_result_3'))

 def test_requested_doses_exclude_other_therapies_while_separate_context_is_retained(self):
  doses=[{'drug_key':'carvedilol','reference_max_status':'unknown_dose'}, {'drug_key':'atorvastatin','reference_max_status':'below_reference'}]
  def calls(field):return[{'name':'query_sql','input':{'sql':'SELECT patient_id,dose_evidence FROM source'},'result':{'result_view':'agere_result_1','columns':['patient_id',field],'rows':[{'patient_id':'A',field:json.dumps(doses)}]}}]
  errors=D.group_query_issues('Review GDMT dose evidence',calls('dose_evidence'),'agere_result_1')
  self.assertTrue(any('atorvastatin'in x for x in errors))
  self.assertFalse(D.group_query_issues('Review GDMT dose evidence',calls('other_medication_context'),'agere_result_1'))

if __name__=='__main__':unittest.main()
