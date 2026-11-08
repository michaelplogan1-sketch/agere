"""Analytical handoff integrity and report/membership boundary regressions."""
import copy,json,sqlite3,tempfile,unittest,zlib
from pathlib import Path
from unittest.mock import patch
from clinical_core.contracts import digest
from clinical_core.agere.clinical import graph_reports as R,graph_decision as G,graph_contracts as C

class Reports(unittest.TestCase):
 def test_summary_keeps_complete_financial_delivery_on_parent_result(self):
  result={'patients_evaluated':3,'status_counts':{'reconciled_received_records':2,'review_required':1},
          'totals_by_currency':{'USD':{'received_cents':0,'balance_cents':None}},
          'account_output':{'columns':['patient_id','received_cents'],
                            'rows':[{'patient_id':'p1','received_cents':0},
                                    {'patient_id':'p2','received_cents':4000},
                                    {'patient_id':'p3','received_cents':None}],
                            'scope':{'received_subjects':3,'unresolved_selected_sources':[]}}}
  original=copy.deepcopy(result)
  summary=R.summary('financial_accounts',result)
  self.assertNotIn('account_output',summary)
  self.assertEqual(summary['patients_evaluated'],3)
  self.assertEqual(summary['status_counts'],result['status_counts'])
  self.assertEqual(summary['totals_by_currency'],result['totals_by_currency'])
  self.assertEqual(result,original)
  self.assertEqual(len(result['account_output']['rows']),3)
 def test_typed_outcome_uses_compact_and_full_catalogs(self):
  for contracts in ([{'id':'exact'}],{'columns':['id','title'],'rows':[['exact','title']]}):
   schema=C.parameter_schema('outcome',{'cohort_fields':{'record_fields':{}},'evidence_catalog':{'outcome_contracts':contracts}})
   self.assertEqual(schema['properties']['contract']['enum'],['exact'])
 def test_financial_report_does_not_nominate_service(self):
  row={'accounts':[{'status':'reconciled_received_records'}],'unassigned_sources':[],'status_counts':{'reconciled_received_records':1}}
  self.assertEqual(R.status_value('financial_accounts',row)[0],'reconciled_received_records')
  self.assertEqual(G.review_tags([{'membership':None,'report_only':True,'care_account_counts':{},'review_topics':[]}]),[])
  row['unassigned_sources']=[{'record_id':'unbound'}]
  self.assertEqual(R.status_value('financial_accounts',row)[0],'review_required')
 def test_reports_never_become_membership_inputs(self):
  for op in ('measure_review','financial_accounts','accountability_review'):
   with self.assertRaisesRegex(ValueError,'membership'):C.validate_inputs('cohort',['report'],{'report':op})
   with self.assertRaisesRegex(ValueError,'membership'):C.validate_inputs('outcome',['report'],{'report':op})
 def test_measure_unknown_components_remain_exact_gaps(self):
  row={'id':'receipt','unresolved_components':['attribution','exclusion'],'components':{'attribution':{'value':None,'status':'unbound'},'exclusion':{'value':None,'status':'unbound'}}}
  result=R.gaps('measure_review',row,{'id':'m','parameters':{'title':'quoted'}})
  self.assertEqual([r['binding'] for r in result],['attribution','exclusion'])
  self.assertTrue(all(r['target_record_id'] is None and r['request_id'] is None for r in result))
 def test_accountability_contract_scope_not_fabricated_patient_attribution(self):
  row={'id':'receipt','requirements':[{'id':'contract','scope':'contract','binding':None,'status':'unbound'},{'id':'attribution','scope':'patient','binding':None,'status':'unbound'}]}
  result=R.gaps('accountability_review',row,{'id':'a','parameters':{}})
  self.assertEqual([r['scope'] for r in result],['contract','patient']);self.assertEqual(result[1]['binding'],'attribution')
 def test_financial_gap_keeps_account_and_explicit_sources(self):
  row={'id':'r','accounts':[{'account_id':'A','status':'review_required','source_record_ids':['bill','payment'],'next_step':'Reconcile received versions'}],'unassigned_sources':[]}
  gap=R.gaps('financial_accounts',row,{'id':'f','parameters':{}})[0]
  self.assertEqual(gap['source_record_ids'],['bill','payment']);self.assertIsNone(gap['target_record_id'])
 def test_mature_no_event_still_keeps_unknown_coverage(self):
  row={'id':'r','recorded_label':0,'status':'not_observed_mature','coverage':{'status':'unknown'}}
  g=R.gaps('outcome',row,{'id':'o','parameters':{'available_by':'2029-08-15'}})
  self.assertEqual([x['status'] for x in g],['outcome_source_coverage_unknown'])
 def test_event_primary_hash_and_date_reference_both_bound(self):
  r={'evidence':{'e':{'source':{'record_id':'adt'},'record_hash':'h1','clinical_date_evidence':[{'record_id':'encounter','record_hash':'h2'}]}}}
  self.assertEqual(R.source_refs('outcome',r,{}),{'adt':'h1','encounter':'h2'})
  r['evidence']['e']['clinical_date_evidence'][0]['record_id']='adt'
  with self.assertRaisesRegex(ValueError,'Conflicting'):R.source_refs('outcome',r,{})
 def test_complete_financial_population_not_display_preview(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp);db=R.F.storage(path)
   for pid in ['a','b','c']:
    row={'id':pid,'patient_id':pid,'accounts':[],'unassigned_sources':[],'status_counts':{}}
    db.execute('INSERT INTO receipts VALUES (?,?,?)',(pid,pid,zlib.compress(json.dumps(row).encode())))
   db.commit();db.close()
   result={'patients_evaluated':3,'patients':[{'patient_id':'a'}]}
   with patch.object(R,'key',side_effect=lambda op,prep,pid:pid):R.attach(path,'financial_accounts',['a','b','c'],{},None,result,{'counts':{'matched':3}})
   self.assertEqual([r['patient_id'] for r in result['review_members']],['a','b','c'])
   self.assertTrue(all(r['membership'] is None and r['report_only'] for r in result['review_members']))
   self.assertEqual(result['population_digest'],digest(['a','b','c']))
 def test_cross_patient_receipt_rejected(self):
  with tempfile.TemporaryDirectory() as tmp:
   db=R.F.storage(tmp);db.execute('INSERT INTO receipts VALUES (?,?,?)',('a','a',zlib.compress(json.dumps({'id':'a','patient_id':'b'}).encode())));db.commit();db.close()
   with patch.object(R,'key',return_value='a'),self.assertRaisesRegex(ValueError,'patient mismatch'):R.attach(tmp,'financial_accounts',['a'],{},None,{},None)
 def test_source_change_rejected_even_if_derived_receipt_not_rebuilt(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp);record={'record_id':'record','patient_id':'p','amount':1}
   db=sqlite3.connect(path/'stack.sqlite3');db.execute('CREATE TABLE records(id,patient,payload)');db.execute('INSERT INTO records VALUES (?,?,?)',('record','p',json.dumps({**record,'amount':2})));db.commit();db.close()
   row={'id':'r','patient_id':'p','source':{'record_id':'record','record_hash':digest(record)}}
   ref={'receipt_id':'r','patient_id':'p','receipt_digest':digest(row)}
   with patch.object(R.F,'prepare',return_value={}),patch.object(R,'key',return_value='r'),patch.object(R,'read',return_value=row):
    with self.assertRaisesRegex(ValueError,'source changed'):R.evidence(path,ref,{'id':'f','operation':'financial_accounts','parameters':{}})

if __name__=='__main__':unittest.main()
