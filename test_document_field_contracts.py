"""A source field's meaning cannot change beneath retained observations."""
import copy
import unittest
from unittest.mock import patch
from clinical_core.agere.clinical import backstop_agent as B


class SourceFieldContracts(unittest.TestCase):
 def setUp(self):
  self.tools=B.Tools.__new__(B.Tools)
  self.tools.document_reviews=[]
  self.tools.document_field_contracts={}
  self.question={'question':'What reason is documented for deferring imaging?',
   'entity':{'kind':'service','id':'mri','aliases':['MRI','magnetic resonance imaging']},
   'relation':'reason_for_deferral','subject':'patient'}

 def call(self,questions=None,**kwargs):
  return self.tools._call({'name':'review_documents','input':{
   'patient_ids':['A'],'questions':questions or {'imaging_reason':self.question},**kwargs}})

 def test_exact_contract_allows_new_patient_source_and_conditional_scopes(self):
  with patch('clinical_core.agere.clinical.document_review.review',return_value={})as dispatch:
   self.call()
   self.call(patient_ids=['A','B'],record_ids=['new-source'])
   self.call(field_patient_queries={'imaging_reason':'SELECT patient_id FROM eligible'})
  self.assertEqual(dispatch.call_count,3)

 def test_changed_question_relation_subject_entity_or_aliases_fail_before_dispatch(self):
  variants=[]
  for key,value in [('question','What imaging status is documented?'),('relation','documented_status'),('subject','family_member')]:
   q=copy.deepcopy(self.question);q[key]=value;variants.append(q)
  q=copy.deepcopy(self.question);q['entity']['id']='ct';variants.append(q)
  q=copy.deepcopy(self.question);q['entity']['aliases'].append('scan');variants.append(q)
  with patch('clinical_core.agere.clinical.document_review.review',return_value={})as dispatch:
   self.call()
   for q in variants:
    with self.subTest(question=q),self.assertRaisesRegex(ValueError,'Source field contract changed'):
     self.call({'imaging_reason':q})
  self.assertEqual(dispatch.call_count,1)

 def test_new_versioned_field_is_available_without_relabeling_prior_findings(self):
  before=copy.deepcopy(self.question)
  with patch('clinical_core.agere.clinical.document_review.review',return_value={})as dispatch:
   self.call()
   changed=copy.deepcopy(self.question);changed['relation']='documented_status'
   self.call({'imaging_status_v2':changed})
  self.assertEqual(dispatch.call_count,2)
  self.assertEqual(self.tools.document_field_contracts['imaging_reason'],before)
  self.assertEqual(self.tools.document_field_contracts['imaging_status_v2'],changed)

 def test_actor_mutation_and_failed_dispatch_do_not_erase_a_declared_meaning(self):
  before=copy.deepcopy(self.question)
  with patch('clinical_core.agere.clinical.document_review.review',side_effect=ValueError('source failure'))as dispatch:
   with self.assertRaisesRegex(ValueError,'source failure'):self.call()
   self.question['relation']='documented_status'
   with self.assertRaisesRegex(ValueError,'Source field contract changed'):self.call()
  self.assertEqual(dispatch.call_count,1)
  self.assertEqual(self.tools.document_field_contracts['imaging_reason'],before)

 def test_restored_native_review_preserves_contract_and_conflicting_versions_require_new_id(self):
  self.tools.document_reviews=[{'questions':{'imaging_reason':copy.deepcopy(self.question)}}]
  with patch('clinical_core.agere.clinical.document_review.review',return_value={})as dispatch:
   self.call()
   changed=copy.deepcopy(self.question);changed['relation']='documented_status'
   self.tools.document_reviews.append({'questions':{'imaging_reason':changed}})
   with self.assertRaisesRegex(ValueError,'Source field contract changed'):self.call()
   self.call({'imaging_status_v2':changed})
  self.assertEqual(dispatch.call_count,2)


if __name__=='__main__':unittest.main()
