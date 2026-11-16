"""Native role and medication domains survive result composition."""
import json,unittest
from clinical_core.agere.clinical import drug_classes as D

class NativeScope(unittest.TestCase):
 def test_repaired_coverage_diagnostic_names_the_stale_dependent_plan(self):
  from clinical_core.agere.clinical.result_assembly import compose
  def call(view,sql,rows):
   return {'name':'query_sql','input':{'sql':sql},'result':{'result_view':view,'columns':list(rows[0]),'rows':rows}}
  calls=[call('agere_result_1','SELECT patient_id, CASE WHEN patient_id IN {{drug_fill:mra}} THEN 1 ELSE 0 END AS on_mra FROM patient',[{'patient_id':'A','on_mra':1}]),
   call('agere_result_2',"SELECT patient_id, 0 AS on_mra FROM fill WHERE fill_date > date(:as_of, '-12 months')",[{'patient_id':'A','on_mra':0}]),
   call('agere_result_3',"SELECT patient_id, CASE WHEN on_mra=0 THEN 'review missing MRA' ELSE 'no gap' END AS proposed_step FROM agere_result_1",[{'patient_id':'A','proposed_step':'no gap'}])]
  results={c['result']['result_view']:c['result']for c in calls}
  args={'base':'agere_result_2','keys':['patient_id'],'joins':[{'result':'agere_result_3','as':'plan','on':{'patient_id':'patient_id'},'mode':'one','columns':['proposed_step']}]}
  out=compose(results,**args);out['result_view']='agere_result_4';calls.append({'name':'compose_table','input':args,'result':out})
  errors=D.group_query_issues('What GDMT are they on?',calls,'agere_result_4')
  self.assertEqual(len(errors),1)
  self.assertIn('Affected final fields: agere_result_4.plan.',errors[0])
  self.assertNotIn('agere_result_4.on_mra',errors[0])
  calls[2]['input']['sql']=calls[2]['input']['sql'].replace('agere_result_1','agere_result_2')
  self.assertFalse(D.group_query_issues('What GDMT are they on?',calls,'agere_result_4'))

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

 def test_historical_supporting_fields_do_not_invalidate_repaired_medication_columns(self):
  calls=[{'name':'query_sql','input':{'sql':'SELECT patient_id, CASE WHEN patient_id IN {{drug_fill:mra}} THEN 1 ELSE 0 END AS on_mra, source_note FROM patient'},
   'result':{'result_view':'agere_result_1','columns':['patient_id','on_mra','source_note'],'rows':[{'patient_id':'A','on_mra':1,'source_note':'retained source'}]}},
   {'name':'query_sql','input':{'sql':"SELECT patient_id, 0 AS on_mra FROM fill WHERE fill_date > date(:as_of, '-12 months') AND fill_date <= :as_of"},
   'result':{'result_view':'agere_result_2','columns':['patient_id','on_mra'],'rows':[{'patient_id':'A','on_mra':0}]}},
   {'name':'query_sql','input':{'sql':'SELECT n.patient_id,c.on_mra,n.source_note FROM agere_result_1 n JOIN agere_result_2 c USING(patient_id)'},
   'result':{'result_view':'agere_result_3','columns':['patient_id','on_mra','source_note'],'rows':[{'patient_id':'A','on_mra':0,'source_note':'retained source'}]}}]
  self.assertFalse(D.group_query_issues('What GDMT are they on?',calls,'agere_result_3'))
  calls[-1]['input']['sql']=calls[-1]['input']['sql'].replace('c.on_mra','n.on_mra')
  calls[-1]['result']['rows'][0]['on_mra']=1
  self.assertTrue(any('all-history'in x for x in D.group_query_issues('What GDMT are they on?',calls,'agere_result_3')))

 def test_macro_care_source_uses_only_historical_membership_keys(self):
  calls=[{'name':'query_sql','input':{'sql':'SELECT patient_id, CASE WHEN patient_id IN {{drug_fill:mra}} THEN 1 ELSE 0 END AS on_mra FROM patient'},
   'result':{'result_view':'agere_result_1','columns':['patient_id','on_mra'],'rows':[{'patient_id':'A','on_mra':1}]}},
   {'name':'query_sql','input':{'sql':'SELECT c.patient_id,c.role FROM {{care_team}} c WHERE c.patient_id IN (SELECT patient_id FROM agere_result_1)'},
   'result':{'result_view':'agere_result_2','columns':['patient_id','role'],'rows':[{'patient_id':'A','role':'primary_care'}]}},
   {'name':'query_sql','input':{'sql':"SELECT patient_id, 0 AS on_mra FROM fill WHERE fill_date > date(:as_of, '-12 months')"},
   'result':{'result_view':'agere_result_3','columns':['patient_id','on_mra'],'rows':[{'patient_id':'A','on_mra':0}]}},
   {'name':'query_sql','input':{'sql':'SELECT n.patient_id,c.on_mra,n.role FROM agere_result_2 n JOIN agere_result_3 c USING(patient_id)'},
   'result':{'result_view':'agere_result_4','columns':['patient_id','on_mra','role'],'rows':[{'patient_id':'A','on_mra':0,'role':'primary_care'}]}}]
  self.assertFalse(D.group_query_issues('What GDMT are they on?',calls,'agere_result_4'))
  calls[1]['input']['sql']=calls[1]['input']['sql'].replace('SELECT patient_id FROM agere_result_1','SELECT patient_id FROM agere_result_1 WHERE on_mra=1')
  self.assertTrue(any('all-history'in x for x in D.group_query_issues('What GDMT are they on?',calls,'agere_result_4')))

 def test_opaque_macro_star_projection_preserves_exact_membership_only_lineage(self):
  calls=[{'name':'query_sql','input':{'sql':'SELECT patient_id, CASE WHEN patient_id IN {{drug_fill:mra}} THEN 1 ELSE 0 END AS on_mra FROM patient'},
   'result':{'result_view':'agere_result_1','columns':['patient_id','on_mra'],'rows':[{'patient_id':'A','on_mra':1}]}},
   {'name':'query_sql','input':{'sql':'SELECT cd.* FROM {{current_dose}} cd WHERE cd.patient_id IN (SELECT patient_id FROM agere_result_1)'},
   'result':{'result_view':'agere_result_2','columns':['patient_id','drug_key'],'rows':[{'patient_id':'A','drug_key':'carvedilol'}]}},
   {'name':'query_sql','input':{'sql':'SELECT patient_id,drug_key FROM agere_result_2'},
   'result':{'result_view':'agere_result_3','columns':['patient_id','drug_key'],'rows':[{'patient_id':'A','drug_key':'carvedilol'}]}}]
  self.assertFalse(D.group_query_issues('Review GDMT doses',calls,'agere_result_3'))
  calls[1]['input']['sql']=calls[1]['input']['sql'].replace('SELECT patient_id FROM agere_result_1','SELECT patient_id FROM agere_result_1 WHERE on_mra=1')
  self.assertTrue(any('all-history'in x for x in D.group_query_issues('Review GDMT doses',calls,'agere_result_3')))

 def test_correlated_count_uses_only_outer_key_unless_a_flag_filters_rows(self):
  calls=[{'name':'query_sql','input':{'sql':'SELECT patient_id, CASE WHEN patient_id IN {{drug_fill:mra}} THEN 1 ELSE 0 END AS on_mra FROM patient'},
   'result':{'result_view':'agere_result_1','columns':['patient_id','on_mra'],'rows':[{'patient_id':'A','on_mra':1}]}},
   {'name':'query_sql','input':{'sql':'SELECT patient_id,drug_key FROM dose'},
   'result':{'result_view':'agere_result_2','columns':['patient_id','drug_key'],'rows':[{'patient_id':'A','drug_key':'carvedilol'}]}},
   {'name':'query_sql','input':{'sql':'SELECT r.patient_id,r.on_mra,(SELECT COUNT(*) FROM agere_result_2 d WHERE d.patient_id=r.patient_id) AS dose_rows FROM agere_result_1 r'},
   'result':{'result_view':'agere_result_3','columns':['patient_id','on_mra','dose_rows'],'rows':[{'patient_id':'A','on_mra':1,'dose_rows':1}]}},
   {'name':'query_sql','input':{'sql':'SELECT patient_id,dose_rows FROM agere_result_3'},
   'result':{'result_view':'agere_result_4','columns':['patient_id','dose_rows'],'rows':[{'patient_id':'A','dose_rows':1}]}}]
  self.assertFalse(D.group_query_issues('Review GDMT doses',calls,'agere_result_4'))
  calls[2]['input']['sql']+=' WHERE r.on_mra=1'
  self.assertTrue(any('all-history'in x for x in D.group_query_issues('Review GDMT doses',calls,'agere_result_4')))
  calls[2]['input']['sql']=calls[2]['input']['sql'].replace(' WHERE r.on_mra=1','').replace('d.patient_id=r.patient_id','d.patient_id=r.patient_id AND r.on_mra=1')
  self.assertTrue(any('all-history'in x for x in D.group_query_issues('Review GDMT doses',calls,'agere_result_4')))

if __name__=='__main__':unittest.main()
