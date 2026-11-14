"""A source field's meaning cannot change beneath retained observations."""
import copy
import unittest
from unittest.mock import patch
from clinical_core.agere.clinical import backstop_agent as B


class SourceFieldContracts(unittest.TestCase):
 def setUp(self):
  self.tools=B.Tools.__new__(B.Tools)
  self.tools.document_reviews=[]
  self.tools.as_of='2030-05-27'
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

 def test_candidate_recheck_records_exact_prior_assertions_for_local_projection(self):
  import json
  from clinical_core.agere.clinical import document_review as D
  doc={'record_id':'record-A','date':'2030-01-01','text':'MRI deferred for transport.'}
  value=D.relation_finding('A','imaging_reason',self.question,doc,
      {'start':0,'end':len(doc['text']),'text':doc['text']},
      {'polarity':'affirmed','temporality':'historical','subject':'patient'},'fixture')
  self.tools.read={'record-A':{**doc,'available':None}}
  self.tools.executed_results={'candidate':{'columns':['patient_id','review_json'],
      'rows':[{'patient_id':'A','review_json':json.dumps({'imaging_reason':[value]})}]}}
  receipt={'source_rows_digest':'subset','checked_fields':['imaging_reason']}
  def reviewed(*args,**kwargs):
   self.tools.document_reviews.append({'candidate_observation_review':receipt})
   return {'rows':[{'patient_id':'A'}],'candidate_observation_review':receipt}
  with patch('clinical_core.agere.clinical.document_review.review',side_effect=reviewed):
   out=self.call(candidate_result_view='candidate')
  self.assertEqual(out['candidate_observation_review']['checked_assertions'],
      [{'patient_id':'A','field':'imaging_reason','assertion_id':value['assertion']['id']}])
  self.assertEqual(self.tools.document_reviews[-1]['candidate_observation_review'],out['candidate_observation_review'])


if __name__=='__main__':unittest.main()
