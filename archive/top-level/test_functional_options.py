import copy,json,unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import test_option_evidence_registry as fixture
from clinical_core.agere.clinical import supplemental_options as S,option_component_registry as K
from clinical_core.agere.clinical import option_evidence_registry as O,care_option_comparison as C,reference_applicability as R,reference_lifecycle as L,care_option_context as X,care_option_delivery as D,care_portfolio_delivery as F

class FunctionalOptions(unittest.TestCase):
 def setUp(self):
  fixture.Registry.setUp(self)
  self.behavioral=fixture.Registry.save(self)
  self.behavioral_delivery={'comparison_id':self.behavioral['id'],'alternative_key':'combined','component_bindings':[],'coordination_note':'Unassigned fixture components'}
  self.before_delivery=D.preview(self.path,self.behavioral_delivery)
  for module,names in [(O,['bundles']),(D,['inputs','identity','snapshot'])]:
   for name in names:self.addCleanup(setattr,module,name,getattr(module,name))
  K.install();K.install();self.falls=S.bundles()[0]['reference']
  self.fallsreq={**self.request,'reference_id':self.falls['id']};self.manifest=R.materialize(self.path,self.fallsreq)
  self.compreq={**self.req,'manifest_id':self.manifest['id'],'question':'Review falls intervention options and missing patient applicability'}
 def save(self):
  preview=C.preview(self.path,self.compreq);return C.prepare(self.path,{'comparison':self.compreq,'preview_id':preview['id']})
 def delivery(self):
  c=self.save();return {'comparison_id':c['id'],'alternative_key':'multifactorial','component_bindings':[],'coordination_note':'Assessment and tailored follow-through are not assigned.'}
 def test_legacy_respiratory_and_behavioral_results_unchanged(self):
  self.assertEqual(C.preview(self.path,self.legacy_request),self.legacy)
  self.assertEqual(C.preview(self.path,self.req),self.behavioral)
  self.assertEqual(D.preview(self.path,self.behavioral_delivery),self.before_delivery)
 def test_exact_functional_question_and_six_packet_targets(self):
  self.assertEqual(self.manifest['original_target'],self.new['original_target']);self.assertEqual(self.manifest['task_id'],self.task['id'])
  targets=R.packet_followup.targets(self.path,self.task['id']);self.assertEqual(sum(t['target'].get('manifest_id')==self.manifest['id'] for t in targets),6)
  out=self.save();self.assertTrue(all(q['state']=='applicability_unresolved' for q in out['questions']))
 def test_reported_ratios_remain_separate_from_head_to_head_and_patient_effect(self):
  out=self.save();est=out['reported_comparative_estimates'];self.assertEqual([x['point'] for x in est],[.85,.84]);self.assertTrue(all(x['null_value']==1 and not x['interval_contains_null'] for x in est))
  self.assertEqual([x['reported_rcts'] for x in est],[29,20]);self.assertEqual(out['evidence_families'],['doi:10.1001/jama.2024.4166']);self.assertFalse(out['study_counts_aggregated'])
  self.assertTrue(all(not x['head_to_head_between_listed_alternatives'] for x in est));self.assertTrue(all(a['rank'] is None and a['individual_benefit'] is None and a['proposed_service'] is None for a in out['alternatives']))
  self.assertIsNone(out['baseline']['selected_alternative']);self.assertFalse(out['baseline']['clinical_treatment_comparator']);self.assertFalse(out['reference_currency']['current_at_patient_decision'])
 def test_no_automatic_clinical_resolution_or_clinical_action(self):
  target=R.target_for(self.manifest,self.manifest['questions'][0])
  with self.assertRaisesRegex(ValueError,'cannot be established'):R.D.save(self.path,{'target':target,'disposition':'addressed'})
  out=self.save();self.assertFalse(out['clinical_applicability_established']);self.assertFalse(out['coverage_established']);self.assertEqual(out['external_actions'],0)
 def test_exact_patient_source_acquisition_stales_old_comparison(self):
  before=self.save();target=R.target_for(self.manifest,self.manifest['questions'][0]);a=R.D.acquisition_context(self.path,{'target':target,'record_ids':['r1']})
  self.assertEqual(self.contexts[a['context_id']]['parameters']['question'],R.resolve(self.path,target)['question'])
  self.assertFalse(C.listing(self.path,{'manifest_id':self.manifest['id']})['comparisons'][0]['current'])
  after=self.save();self.assertNotEqual(before['id'],after['id']);self.assertTrue(after['questions'][0]['source_contexts'][0]['current'])
 def test_received_context_preserves_unknown_preference_and_benefits(self):
  c=self.save();p=X.preview(self.path,{'comparison_id':c['id']});saved=X.prepare(self.path,{'comparison_id':c['id'],'preview_id':p['id']})
  self.assertFalse(saved['coverage_established']);self.assertFalse(saved['patient_preference_confirmed']);self.assertIsNone(saved['selected_coverage_record'])
 def test_multifactorial_components_are_assessment_and_conditional_followthrough(self):
  req=self.delivery();out=D.preview(self.path,req);self.assertEqual(out['coverage_of_proposal']['components_without_proposals'],['risk_assessment','tailored_followthrough'])
  self.assertFalse(out['component_capabilities_verified']);self.assertFalse(out['clinical_alternative_selected']);self.assertIn('only the components',out['components'][1]['checks'][0])
  saved=D.prepare(self.path,{'proposal':req,'preview_id':out['id']});self.assertEqual(saved,out);self.assertTrue(D.listing(self.path,{'comparison_id':req['comparison_id']})['proposals'][0]['current'])
 def test_exercise_component_service_proposal_is_unverified(self):
  req=self.delivery();req.update(alternative_key='exercise',component_bindings=[{'component_key':'exercise_delivery','option_id':self.task['option_id'],'rationale':'Generic service fixture, no clinical verification'}])
  out=D.preview(self.path,req);self.assertEqual(out['coverage_of_proposal']['components_without_proposals'],['exercise_followup']);self.assertFalse(out['components'][0]['verified_delivery_capability'])
  self.assertEqual(out['care_tasks_created'],0);self.assertEqual(R.care_readiness.task(self.path,self.task['id']),self.task)
 def test_full_portfolio_proposals_compose_without_rewriting_comparison(self):
  req=self.delivery();p=D.preview(self.path,req);saved=D.prepare(self.path,{'proposal':req,'preview_id':p['id']});choices=F.choices(self.path,{'delivery_id':saved['id']});self.assertEqual(choices['components'][0]['key'],'risk_assessment')
  proposal={'delivery_id':saved['id'],'component_bindings':[],'coordination_note':'No qualified service assumed.'};out=F.preview(self.path,proposal);r=F.prepare(self.path,{'proposal':proposal,'preview_id':out['id']});self.assertFalse(r['component_capabilities_verified']);self.assertEqual(F.original(self.path,saved['id']),saved)
 def test_parallel_domain_dispatch_preserves_evidence_and_component_identity(self):
  falls=self.delivery();requests=[falls,self.behavioral_delivery]*4
  with ThreadPoolExecutor(max_workers=4) as pool:rows=list(pool.map(lambda x:D.preview(self.path,x),requests))
  self.assertEqual([r['components'][0]['key'] for r in rows],['risk_assessment','cbt_service']*4)
  self.assertEqual(D.preview(self.path,self.behavioral_delivery),self.before_delivery)
  with self.assertRaises(ValueError):D.preview(self.path,{**falls,'alternative_key':'cbt'})
  self.assertEqual(D.preview(self.path,self.behavioral_delivery),self.before_delivery)
 def test_mapping_changes_stale_only_selected_mapping(self):
  req=self.delivery();out=D.preview(self.path,req);D.prepare(self.path,{'proposal':req,'preview_id':out['id']})
  data=json.loads(K.CATALOG.read_text());data['mappings'][0]['components'][0]['label']='Changed fixture label';path=self.path/'changed-components.json';path.write_text(json.dumps(data))
  with patch.object(K,'CATALOG',path):
   self.assertFalse(D.listing(self.path,{'comparison_id':req['comparison_id']})['proposals'][0]['current']);self.assertEqual(D.preview(self.path,self.behavioral_delivery),self.before_delivery)
 def test_foreign_component_service_duplicate_and_authority_rejected(self):
  req=self.delivery();b={'component_key':'risk_assessment','option_id':self.task['option_id'],'rationale':'Fixture'}
  for change in ({'clinical_alternative_selected':True},{'component_bindings':[{**b,'option_id':'other'}]},{'component_bindings':[b,b]},{'component_bindings':[{**b,'component_key':'cbt_service'}]}):
   with self.assertRaises(ValueError):D.preview(self.path,{**req,**change})
 def test_changed_bundle_and_fake_marker_cannot_borrow_evidence(self):
  saved=self.save();entries=S.bundles();entries[0]['reference']['registry_entry']['bundle_hash']='changed'
  with patch.object(S,'bundles',return_value=entries):
   with self.assertRaises(ValueError):C.preview(self.path,self.compreq)
   self.assertFalse(C.listing(self.path,{'manifest_id':self.manifest['id']})['comparisons'][0]['current'])
  fake=copy.deepcopy(saved);fake['option_evidence']['registry_entry']['bundle_hash']='unknown'
  with self.assertRaises(ValueError):K.mapping(fake)
 def test_local_withdrawal_stales_falls_but_not_other_domains(self):
  self.save();change={**self.change,'reference_id':self.falls['id'],'event_kind':'local_withdrawal','question_changes':{}}
  p=L.preview(self.path,change);L.commit(self.path,{'change':change,'preview_id':p['id'],'request_id':'falls-withdraw'})
  self.assertFalse(C.listing(self.path,{'manifest_id':self.manifest['id']})['comparisons'][0]['current']);self.assertEqual(C.preview(self.path,self.req),self.behavioral)
 def test_no_same_doi_dispatch_without_exact_registry_marker(self):
  manifest=copy.deepcopy(self.manifest);del manifest['reference']['registry_entry']
  with patch.object(R,'load',return_value=manifest),patch.object(R,'current'):
   with self.assertRaisesRegex(ValueError,'No option evidence mapping'):C.preview(self.path,self.compreq)
 def test_bad_catalog_ratios_sources_and_unreviewed_authority_rejected(self):
  original=json.loads(S.CATALOG.read_text())
  for change in ({'point':float('nan')},{'metric':'mean_difference'},{'null_value':0},{'interval':[.9,.7]},{'source_url':''},{'alternative_key':'cbt'},{'head_to_head_between_listed_alternatives':True}):
   data=copy.deepcopy(original);data['bundles'][0]['evidence']['estimates'][0].update(change);path=self.path/'bad.json';path.write_text(json.dumps(data))
   with patch.object(S,'CATALOG',path):
    with self.assertRaises(ValueError):S.bundles()
  data=copy.deepcopy(original);data['bundles'][0]['reference']['review_status']='clinician_verified';path.write_text(json.dumps(data))
  with patch.object(S,'CATALOG',path):
   with self.assertRaises(ValueError):S.bundles()
 def test_bad_component_alternative_coverage_and_keys_rejected(self):
  c=self.save();original=json.loads(K.CATALOG.read_text())
  for kind in ('missing_alternative','unknown_component','duplicate_component'):
   data=copy.deepcopy(original);m=data['mappings'][0]
   if kind=='missing_alternative':del m['alternatives']['exercise']
   elif kind=='unknown_component':m['alternatives']['exercise']=['unknown']
   else:m['components'].append(m['components'][0])
   path=self.path/'bad-components.json';path.write_text(json.dumps(data))
   with patch.object(K,'CATALOG',path):
    with self.assertRaises(ValueError):K.mapping(c)

if __name__=='__main__':unittest.main()
