"""Exact registered group sets and native result ancestry across medication groups."""
import unittest
from unittest.mock import patch
from clinical_core.agere.clinical import drug_classes as D,backstop_agent as B

def call(view,sql,rows=None):return {'name':'query_sql','input':{'sql':sql},'result':{'result_view':view,'columns':['patient_id','therapy'],'rows':rows or [{'patient_id':'A','therapy':'broad'}]}}
class Scope(unittest.TestCase):
 def setUp(self):self.groups=patch.object(D,'GROUPS',{'prevention':['narrow_a','narrow_b'],'maintenance':['broad','other']});self.groups.start();self.addCleanup(self.groups.stop)
 def test_superset_cannot_satisfy_named_group(self):
  calls=[call('agere_result_1',"SELECT * FROM fill WHERE drug_class IN ('narrow_a','narrow_b','broad')")]
  issues=D.group_query_issues('Assess prevention medications',calls,'agere_result_1');self.assertEqual(len(issues),1);self.assertIn('broad',issues[0])
 def test_exact_set_or_different_requested_group_is_not_rejected(self):
  c=[call('agere_result_1',"SELECT * FROM fill WHERE drug_class IN ('narrow_a','narrow_b')")]
  self.assertEqual(D.group_query_issues('Assess prevention medications',c,'agere_result_1'),[])
  c=[call('agere_result_1',"SELECT * FROM fill WHERE drug_class IN ('broad','other')")]
  self.assertEqual(D.group_query_issues('Assess maintenance medications',c,'agere_result_1'),[])
 def test_unrelated_exact_query_cannot_repair_wrong_final_table(self):
  c=[call('agere_result_1',"SELECT * FROM fill WHERE drug_class IN ('narrow_a','narrow_b','broad')"),call('agere_result_2',"SELECT * FROM fill WHERE drug_class IN ('narrow_a','narrow_b')"),call('agere_result_3','SELECT * FROM agere_result_1')]
  self.assertTrue(D.group_query_issues('Assess prevention medications',c,'agere_result_3'))
  c[-1]=call('agere_result_3','SELECT * FROM agere_result_2')
  self.assertEqual(D.group_query_issues('Assess prevention medications',c,'agere_result_3'),[])
 def test_repair_can_keep_original_evidence_and_use_correct_group(self):
  c=[call('agere_result_1',"SELECT * FROM fill WHERE drug_class IN ('narrow_a','narrow_b','broad')"),call('agere_result_2',"SELECT * FROM fill WHERE drug_class IN ('narrow_a','narrow_b')"),call('agere_result_3','SELECT a.patient_id,b.therapy FROM agere_result_1 a JOIN agere_result_2 b USING(patient_id)')]
  self.assertEqual(D.group_query_issues('Assess prevention medications',c,'agere_result_3'),[])
 def test_blocked_group_review_preserves_computed_rows(self):
  c=[call('agere_result_1',"SELECT * FROM fill WHERE drug_class IN ('narrow_a','narrow_b','broad')")];a={'table_from_tool':'agere_result_1'}
  r=B.review_final(None,'Assess prevention medications',a,c,None,ledger=None,as_of='2030-05-27');self.assertEqual(r['status'],'repair');self.assertEqual(len(a['table']),1)
if __name__=='__main__':unittest.main()
