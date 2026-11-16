import json,tempfile,unittest
from pathlib import Path
from clinical_core.agere.workflow_scorecard import score_case

class Scorecard(unittest.TestCase):
 def test_explicit_stage_override_preserves_original_failed_trace(self):
  self.trace('source_interpretation',status='runtime_error',error='Original reference error')
  original=(self.p/'source_interpretation.json').read_bytes();new=self.p/'new-answer.json'
  new.write_text(json.dumps({'status':'executed','response':{'id':'new','question':self.case['question'],'patient_id':self.case['patient']}}))
  row=score_case(self.case,self.p,trace_overrides={'source_interpretation':new})
  self.assertEqual(row['stages']['source_interpretation']['status'],'executed_unvalidated')
  self.assertEqual(row['trace_links']['source_interpretation'],str(new))
  self.assertEqual((self.p/'source_interpretation.json').read_bytes(),original)
 def test_source_answer_for_another_question_or_patient_is_not_success(self):
  for change in ({'question':'Unrelated question'},{'patient_id':'P-other'}):
   self.trace('source_interpretation',{'id':'answer','question':self.case['question'],'patient_id':self.case['patient'],**change})
   self.assertEqual(score_case(self.case,self.p)['stages']['source_interpretation']['status'],'input_mismatch')
 def test_unknown_override_or_missing_trace_rejected(self):
  for overrides in ({'imaginary':'unused'},{'source_interpretation':self.p/'missing.json'}):
   with self.assertRaises(ValueError):score_case(self.case,self.p,trace_overrides=overrides)
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.p=Path(self.temp.name)
  self.case={'id':'probe','patient':'P-1','question':'Original question','planning_question':'P-1: Original question','required_categories_if_received':['orders','notes']}
 def trace(self,name,response=None,**kw):
  value={'status':'executed','response':response or {},**kw};(self.p/(name+'.json')).write_text(json.dumps(value))
 def test_missing_category_is_retrieval_failure_not_reranker_error(self):
  self.trace('coverage',{'categories':[{'source_category':'orders','received_records':2,'candidates':0,'rrf_selected':0,'reranked_selected':0}]})
  self.trace('retrieval',{'candidate_set_identical':True})
  s=score_case(self.case,self.p)['stages'];self.assertEqual(s['retrieval']['missing_candidate_categories'],['orders']);self.assertEqual(s['reranking']['missing_selected_categories'],[])
  self.assertEqual(s['retrieval']['anchor_categories_not_observed'],['notes'])
 def test_candidate_lost_after_reranking_is_distinct(self):
  self.trace('coverage',{'categories':[{'source_category':'orders','received_records':2,'candidates':1,'rrf_selected':1,'reranked_selected':0}]})
  self.trace('retrieval',{'candidate_set_identical':True});s=score_case(self.case,self.p)['stages']
  self.assertEqual(s['retrieval']['status'],'checked');self.assertEqual(s['reranking']['status'],'selection_gap')
 def test_omissions_block_scope_and_missing_preflight_does_not_mean_reader_failure(self):
  self.trace('source_scope',{'question':'Original question','selected_categories':[],'context_omissions':[{}]})
  self.trace('interpretation_preflight',status='runtime_error',error='incomplete')
  s=score_case(self.case,self.p)['stages'];self.assertEqual(s['source_scope']['status'],'incomplete');self.assertEqual(s['source_interpretation']['status'],'blocked_by_scope')
 def test_planner_failure_never_becomes_unsupported_success(self):
  self.trace('planning',status='runtime_error',error='bad contract');self.assertEqual(score_case(self.case,self.p)['stages']['planning']['status'],'runtime_error')
 def test_planning_requires_patient_binding(self):
  self.trace('planning',{'plan':{'nodes':[],'coverage':'partial'},'question':self.case['planning_question']})
  self.assertEqual(score_case(self.case,self.p)['stages']['planning']['status'],'scope_not_verified')
 def test_exclusion_or_unrestricted_second_root_cannot_prove_patient_scope(self):
  atom={'kind':'patient','field':'patient_id','op':'eq','value':'P-1'}
  for nodes in ([{'operation':'cohort','inputs':[],'parameters':{'specification':{'exclude':[atom]}}}],
                [{'operation':'cohort','inputs':[],'parameters':{'specification':{'all':[atom]}}},{'operation':'evidence','inputs':[],'parameters':{}}]):
   self.trace('planning',{'graph':{'nodes':nodes,'coverage':'partial'},'question':self.case['planning_question']})
   self.assertEqual(score_case(self.case,self.p)['stages']['planning']['status'],'scope_not_verified')
 def test_prior_reader_comparison_is_never_full_question_interpretation(self):
  self.trace('reader_probe',{'id':'comparison','request':{'target':{'patient_id':'P-1'},'roles':['kev']}})
  s=score_case(self.case,self.p)['stages']
  self.assertEqual(s['reader_comparison']['status'],'isolated_diagnostic');self.assertFalse(s['reader_comparison']['original_question_connected'])
  self.assertEqual(s['source_interpretation']['status'],'not_run')
 def test_different_candidate_sets_are_not_a_reranker_comparison(self):
  self.trace('retrieval',{'candidate_set_identical':False});self.assertEqual(score_case(self.case,self.p)['stages']['reranking']['status'],'not_comparable')
 def test_changed_question_is_an_input_failure(self):
  self.trace('source_scope',{'question':'Different question'})
  self.trace('planning',{'question':'Different question','graph':{'coverage':'unsupported','nodes':[]}})
  s=score_case(self.case,self.p)['stages'];self.assertEqual(s['source_scope']['status'],'input_mismatch');self.assertEqual(s['planning']['status'],'input_mismatch')
 def test_untrusted_report_text_cannot_close_the_json_script_element(self):
  from clinical_core.agere.workflow_evaluation_view import page
  rendered=page({'error':'</script><img src=x onerror=alert(1)>'})
  self.assertNotIn('</script><img',rendered);self.assertIn('\\u003c/script\\u003e',rendered)
 def test_answer_handoff_requires_exact_question_findings_and_no_claimed_human_review(self):
  answer={'id':'answer','patient_id':'P-1','question':self.case['question'],'findings':[{'id':'f1','text':'Synthetic proposal'}]}
  origin={**answer,'kind':'answer'}
  options=[{'capability':{'id':k},'status':v} for k,v in [('available','requires_verification'),('unavailable','blocked'),('wrong-plan','blocked')]]
  self.trace('source_interpretation',answer)
  self.trace('connected_matching',{'origin':origin,'options':options})
  handoff={'task':{'id':'task','state':'handoff_prepared_for_review','handoff':{'origin':origin,'human_review_completed':False,'external_dispatch_performed':False}}}
  self.trace('connected_handoff',handoff)
  r=score_case(self.case,self.p);self.assertEqual(r['stages']['work_followthrough']['status'],'connected_review_unvalidated');self.assertEqual(r['end_to_end'],'incomplete')
  handoff['task']['handoff']['human_review_completed']=True;self.trace('connected_handoff',handoff)
  self.assertEqual(score_case(self.case,self.p)['stages']['work_followthrough']['status'],'lineage_failure')
  self.trace('connected_matching',{'origin':{**origin,'findings':[]},'options':options})
  self.assertEqual(score_case(self.case,self.p)['stages']['matching']['status'],'lineage_failure')
 def test_successful_diagnostic_handoff_is_not_end_to_end_success(self):
  self.trace('handoff',{'task':{'id':'task','state':'handoff_prepared_for_review'}})
  r=score_case(self.case,self.p);self.assertEqual(r['end_to_end'],'incomplete');self.assertEqual(r['stages']['work_followthrough']['status'],'isolated_diagnostic');self.assertFalse(r['stages']['work_followthrough']['original_question_connected'])
 def test_matching_must_exclude_unrelated_and_reject_blocked_tasks(self):
  options=[{'capability':{'id':k},'status':v} for k,v in [('available','requires_verification'),('unavailable','blocked'),('wrong-plan','blocked')]]
  self.trace('matching',{'options':options,'excluded':[{'capability_id':'unrelated'}]})
  self.assertEqual(score_case(self.case,self.p)['stages']['matching']['status'],'control_failure')
  for k in ['unavailable','wrong-plan']:self.trace('reject_'+k,status='runtime_error',http_status=400)
  self.assertEqual(score_case(self.case,self.p)['stages']['matching']['status'],'isolated_diagnostic')
class ComputedAcceptance(unittest.TestCase):
 setUp=Scorecard.setUp
 trace=Scorecard.trace
 def complete_fixture(self, unknown=False):
  from clinical_core.contracts import digest
  self.trace('coverage',{'categories':[{'source_category':n,'received_records':1,'candidates':1,'rrf_selected':1,'reranked_selected':1} for n in ['orders','notes']]})
  self.trace('retrieval',{'candidate_set_identical':True})
  self.trace('source_scope',{'context_id':'ctx','patient_id':'P-1','question':'Original question','selected_categories':['orders','notes'],'context_omissions':[]})
  answer={'id':'answer','patient_id':'P-1','question':'Original question','context_id':'ctx',
    'coverage':{'received_units':2,'submitted_units':2,'model_accounted_units':2,'unassessed_units':[]},
    'findings':[{'id':'f1','citations':[{'citation_verified':True,'quote':'reported unknown','source_unit_id':'unit','source':{'patient_id':'P-1','record_hash':'hash'}}]}],
    'unanswered':['Current eligibility unknown'] if unknown else [],'limits':['Synthetic engineering fixture, not clinical evidence'],'clinical_execution_authorized':False}
  # An unresolved finding with a supported citation remains a legitimate answer.
  if unknown:answer['findings'][0]['kind']='unresolved'
  self.trace('source_interpretation',answer)
  plan={'result_id':'plan','question':self.case['planning_question'],'graph':{'coverage':'full','nodes':[{'operation':'cohort','parameters':{'specification':{'all':[{'kind':'patient','field':'patient_id','op':'eq','value':'P-1'}]}}}]}}
  self.trace('planning',plan)
  origin={**answer,'kind':'answer'}
  options=[{'capability':{'id':k},'status':v} for k,v in [('available','requires_verification'),('unavailable','blocked'),('wrong-plan','blocked')]]
  match={'id':'matching','origin':origin,'options':options};self.trace('connected_matching',match)
  decision={'id':'decision','question':'Original question','patient_id':'P-1','answer_id':'answer','answer_digest':digest(answer),'context_id':'ctx','planning_result_id':'plan','planning_digest':digest(plan),'rule_versions':['rule-v1'],
    'service_selection':{'matching_id':'matching','matching_digest':digest(match),'method':'derived_from_supported_findings','finding_ids':['f1']}}
  self.trace('decision_context',decision)
  task={'id':'task','patient_id':'P-1','plan_id':'matching','owner':{'id':'internal-review'},'clinical_execution_authorized':False,'state':'handoff_prepared_for_review','handoff':{'origin':origin,'human_review_completed':False,'external_dispatch_performed':False}}
  self.trace('connected_handoff',{'task':task});self.trace('task_reopen',{'task':task})
  self.trace('followup',{'result_id':'followup','decision_id':'decision','task_id':'task','patient_id':'P-1','question':'Original question','previous_answer_digest':digest(answer),'source_versions':['new-version'],'changes':[],'unresolved':['Eligibility still unknown'],'external_dispatch_performed':False})
 def change(self,name,fn):
  p=self.p/(name+'.json');t=json.loads(p.read_text());fn(t['response']);p.write_text(json.dumps(t))
 def test_completion_is_computed_and_supported_unknown_is_allowed(self):
  for unknown in (False,True):
   self.complete_fixture(unknown);r=score_case(self.case,self.p)
   self.assertEqual(r['acceptance']['status'],'passed');self.assertEqual(r['end_to_end'],'complete');self.assertFalse(r['clinical_validated'])
 def test_removing_connected_evidence_changes_completion(self):
  for stage in ('decision_context','task_reopen','followup'):
   self.complete_fixture();(self.p/(stage+'.json')).unlink();r=score_case(self.case,self.p)
   self.assertNotEqual(r['acceptance']['status'],'passed');self.assertEqual(r['end_to_end'],'incomplete')
 def test_cross_patient_stale_answer_manual_choice_and_unit_omission_fail(self):
  mutations=[('source_scope',lambda r:r.update(patient_id='P-other')),
   ('decision_context',lambda r:r.update(answer_digest='stale')),
   ('decision_context',lambda r:r['service_selection'].update(method='evaluator_selected')),
   ('decision_context',lambda r:r['service_selection'].update(finding_ids=['unrelated'])),
   ('source_interpretation',lambda r:r['coverage'].update(model_accounted_units=1)),
   ('source_interpretation',lambda r:r['findings'][0]['citations'][0].update(citation_verified=False)),
   ('followup',lambda r:r.update(decision_id='unrelated')),
   ('task_reopen',lambda r:r['task'].update(plan_id='different'))]
  for name,fn in mutations:
   self.complete_fixture();self.change(name,fn);r=score_case(self.case,self.p)
   self.assertEqual(r['acceptance']['status'],'failed',(name,r['acceptance']))
 def test_empty_or_missing_checks_cannot_pass(self):
  from clinical_core.agere.workflow_scorecard import acceptance_status
  self.assertEqual(acceptance_status([]),'unassessed')
  self.assertEqual(acceptance_status([{'status':'passed'},{'status':'unassessed'}]),'unassessed')
  with self.assertRaises(ValueError):acceptance_status([{'status':'unknown'}])
 def test_six_passed_cases_cannot_complete_broader_release(self):
  from clinical_core.agere.workflow_scorecard import release_acceptance,CONTRACT
  c=json.loads(CONTRACT.read_text());rows=[{'id':n,'acceptance':{'status':'passed'}} for n in c['individual_cases']]
  r=release_acceptance(rows);self.assertEqual(r['engineering_requirements_passed'],0);self.assertEqual(r['engineering_status'],'blocked')
  self.assertIn('individual_question_to_followup',r['invalid_evidence_requirements'])
  self.assertEqual(r['scope_areas'],list(range(1,11)))
  with self.assertRaises(ValueError):release_acceptance(rows[:-1])
 def test_capability_receipts_require_snapshot_all_checks_and_unchanged_raw_evidence(self):
  from clinical_core.agere.workflow_scorecard import capability_checks
  from clinical_core.contracts import digest
  import hashlib
  raw=self.p/'raw.json';raw.write_text('{"observed_denominator":1614}')
  spec={'id':'population','axis':'engineering','required_checks':['denominator','followthrough']};snap={'release':'original'}
  receipt={'schema':'agere-capability-acceptance/1','requirement_id':'population','snapshot_digest':digest(snap),
   'checks':[{'id':n,'status':'passed','evidence':[{'path':'raw.json','sha256':hashlib.sha256(raw.read_bytes()).hexdigest()}]} for n in spec['required_checks']]}
  p=self.p/'receipt.json';p.write_text(json.dumps(receipt))
  self.assertTrue(all(c['status']=='passed' for c in capability_checks(spec,'receipt.json',snap,self.p)))
  with self.assertRaises(ValueError):capability_checks(spec,'receipt.json',{'release':'different'},self.p)
  raw.write_text('{}')
  with self.assertRaises(ValueError):capability_checks(spec,'receipt.json',snap,self.p)
  receipt['checks']=[];p.write_text(json.dumps(receipt))
  with self.assertRaises(ValueError):capability_checks(spec,'receipt.json',snap,self.p)
 def test_engineering_receipt_cannot_grant_clinical_or_external_qualification(self):
  from clinical_core.agere.workflow_scorecard import capability_checks
  for axis in ['qualification','external_dependency']:
   with self.assertRaises(ValueError):capability_checks({'axis':axis,'required_checks':['x']},'any.json',{'release':'original'},self.p)

class UnresolvedAccounting(unittest.TestCase):
 def test_explicit_unknown_is_not_a_missing_unit_or_clinical_qualification(self):
  from clinical_core.agere.workflow_scorecard import declared_units_accounted
  import copy
  unknown={'source_unit_id':'u2','status':'unresolved','reason':'Relationship cannot be determined from this received unit.'}
  row={'passes':[{'result':{'unit_reviews':[{'source_unit_id':'u1','status':'discussed'},unknown]}}],
       'coverage':{'received_units':2,'submitted_units':2,'model_accounted_units':2,'complete_declared_scope':True,
                   'unit_dispositions':{'discussed':1,'unresolved':1},'unassessed_units':[copy.deepcopy(unknown)]}}
  self.assertTrue(declared_units_accounted(row));self.assertEqual(row['coverage']['unassessed_units'],[unknown])
  for change in [lambda r:r['passes'][0]['result']['unit_reviews'].pop(),
                 lambda r:r['coverage'].update(model_accounted_units=1),
                 lambda r:r['coverage']['unassessed_units'][0].update(reason=''),
                 lambda r:r['coverage']['unassessed_units'][0].update(source_unit_id='other'),
                 lambda r:r['coverage']['unassessed_units'].append(copy.deepcopy(unknown)),
                 lambda r:r['coverage']['unit_dispositions'].update(unresolved=0),
                 lambda r:r['coverage'].update(complete_declared_scope=False),
                 lambda r:r['coverage'].update(unassessed_units=['unsupported summary'])]:
   bad=copy.deepcopy(row);change(bad);self.assertFalse(declared_units_accounted(bad))

if __name__=='__main__':unittest.main()
