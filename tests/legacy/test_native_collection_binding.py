"""Consumer addresses follow explicit native constructors, never name guessing."""
import copy,json,unittest
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere.clinical import result_assembly as R,backstop_agent as B,system_one_gate as G

class NativeCollectionBinding(unittest.TestCase):
 def source(self):
  return {'base':{'columns':['patient_id'],'rows':[['A'],['B']]},
          'source':{'columns':['patient_id','observations'],'rows':[['A',[{'value':3,'record_id':'R1'}]],['B',None]]}}
 def assembled(self,**change):
  n=self.source();j={'result':'source','as':'arbitrary_container','on':{'patient_id':'patient_id'},'mode':'collect','items_from':'observations','retain_in_final':True};j.update(change)
  n['root']=R.compose(n,base='base',keys=['patient_id'],joins=[j]);return n
 def bindings(self,n):return R.collection_contract_renames(n,'root',['observations'],keys=['patient_id'])
 def test_unique_explicit_retained_source_field_binds_without_changing_any_value(self):
  n=self.assembled();old=copy.deepcopy(n['root']);mapping=self.bindings(n);self.assertEqual(mapping,{'arbitrary_container':'observations'})
  out=R.compose(n,base='root',keys=['patient_id'],rename_columns=mapping)
  self.assertEqual([r['observations']for r in out['rows']],[r['arbitrary_container']for r in old['rows']]);self.assertEqual(n['root'],old)
  n['bound']=out;self.assertEqual(R.collection_retention_issues(n,out['rows'],['root','bound']),[])
 def test_existing_destination_and_explicit_prior_rename_are_never_overridden(self):
  n=self.assembled();n['root']['columns'].append('observations')
  for row in n['root']['rows']:row['observations']=None
  self.assertEqual(self.bindings(n),{})
  n=self.assembled();n['root']['assembly']['column_renames']={'arbitrary_container':'prior_container'}
  self.assertEqual(self.bindings(n),{})
 def test_ambiguous_constructors_require_an_explicit_producer_choice(self):
  n=self.source();joins=[{'result':'source','as':alias,'on':{'patient_id':'patient_id'},'mode':'collect','items_from':'observations','retain_in_final':True}for alias in('one_container','other_container')]
  n['root']=R.compose(n,base='base',keys=['patient_id'],joins=joins);self.assertEqual(self.bindings(n),{})
 def test_changed_values_sources_incompleteness_and_nonretained_shapes_do_not_bind(self):
  for change in('output','source','incomplete','not_retained','wrong_name','wrong_identity'):
   n=self.assembled()
   if change=='output':n['root']['rows'][0]['arbitrary_container'][0]['value']=99
   elif change=='source':n['source']['rows'][0][1][0]['value']=99
   elif change=='incomplete':n['source']['truncated']=True
   elif change=='not_retained':n['root']['assembly']['joins'][0]['retain_in_final']=False
   elif change=='wrong_name':n['root']['assembly']['joins'][0]['items_from']='other_type'
   else:n['root']['assembly']['joins'][0]['on']={'patient_id':'other_patient_id'}
   with self.subTest(change=change):self.assertEqual(self.bindings(n),{})
 def test_review_records_a_pure_native_binding_before_paid_checks(self):
  base={'columns':['patient_id','on_mra_pillar','dose_rows','gdmt_dose_rows'],'rows':[['A',1,0,None]]}
  fill={'drug_class':'mra','drug_name':'Example','latest_fill_date_in_window':'2030-01-01','observed_fill_count':1}
  care={'clinician_id':'C1','role':'primary_care','assignment_status':'documented_assignment','assigned_at_snapshot':1,
        'effective_start':'2030-01-01','effective_end':None,'source_date':'2030-01-01','source_record_ids':['R1']}
  native={'base':base,'fills':{'columns':['patient_id','recent_gdmt_fill_observations'],'rows':[['A',[fill]]]},
          'care':{'columns':['patient_id','care_assignments'],'rows':[['A',[care]]]}}
  args={'base':'base','keys':['patient_id'],'joins':[{'result':s,'as':a,'on':{'patient_id':'patient_id'},'mode':'collect','items_from':f,'retain_in_final':True}for s,a,f in(('fills','free_fill_label','recent_gdmt_fill_observations'),('care','free_care_label','care_assignments'))]}
  native['agere_result_1']={**R.compose(native,**args),'result_view':'agere_result_1'}
  class Tools:
   executed_results=native;read={};document_reviews=[]
   def __call__(self,use):
    result={**R.compose(self.executed_results,**use['input']),'result_view':'agere_result_2'};self.executed_results['agere_result_2']=result;return result
  calls=[{'name':'compose_table','input':args,'result':native['agere_result_1']}]
  a={'status':'partial','answer_type':'table','table_from_tool':'agere_result_1','headline':'Source review is pending.',
     'workflow':[{'kind':'deliverable','title':'Medication review','template':'brief','purpose':'inspection','quote':'create a report','unresolved':[]}]}
  with patch.object(G,'ready',return_value=False):review=B.review_final(SimpleNamespace(),'Review medications and create a report',a,calls,Tools(),ledger=None,as_of='2030-05-27')
  self.assertEqual(review['status'],'unavailable');self.assertEqual(len(calls),2)
  self.assertEqual(a['table'][0]['recent_gdmt_fill_observations'],[fill]);self.assertEqual(a['table'][0]['care_assignments'],[care])
  self.assertEqual(calls[-1]['input']['rename_columns'],{'free_fill_label':'recent_gdmt_fill_observations','free_care_label':'care_assignments'})

if __name__=='__main__':unittest.main()
