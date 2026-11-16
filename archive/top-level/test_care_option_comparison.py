import copy,json,sqlite3,unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import test_reference_lifecycle as fixture
from clinical_core.agere.clinical import care_option_comparison as C,reference_applicability as R,reference_lifecycle as L

class Options(unittest.TestCase):
    acquire=fixture.Lifecycle.acquire
    event_request=fixture.Lifecycle.event_request
    commit=fixture.Lifecycle.commit
    successor=fixture.Lifecycle.successor
    def setUp(self):
        fixture.Lifecycle.setUp(self)
        self.comparison={'manifest_id':self.row['id'],'question':'Which delivery options warrant review for this exact service question?',
          'rationale':'Synthetic engineering comparison; no recommendation or service enrollment','service_bindings':[]}
    def comparison_request(self,body=None):
        body=body or self.comparison;p=C.preview(self.path,body);return {'comparison':body,'preview_id':p['id']}
    def binding(self,key='remote'):
        return {'alternative_key':key,'option_id':self.task['option_id'],'rationale':'Explicitly proposed for verification, not an assertion that this pharmacy offers rehabilitation'}
    def test_source_comparison_is_unranked_and_does_not_invent_services(self):
        p=C.preview(self.path,self.comparison)
        self.assertEqual(len(p['alternatives']),2);self.assertTrue(all(a['mapping_status']=='no_service_proposed' for a in p['alternatives']))
        self.assertTrue(all(a['rank'] is None and a['individual_benefit'] is None and a['financial_effect'] is None for a in p['alternatives']))
        self.assertIsNone(p['baseline']['selected_alternative']);self.assertFalse(p['baseline']['clinical_treatment_comparator'])
        self.assertEqual(p['original_service_question'],self.row['original_target']['question']);self.assertFalse(p['clinical_applicability_established'])
        self.assertFalse((self.path/'care-option-comparisons.sqlite3').exists())
    def test_estimate_preserves_population_endpoint_interval_and_provenance(self):
        p=C.preview(self.path,self.comparison);x=p['reported_comparative_estimates'][0]
        self.assertEqual((x['point'],x['interval'],x['reported_rcts'],x['reported_participants']),(0.06,[-11,11],4,556))
        self.assertTrue(x['interval_contains_null']);self.assertIn('does not prove equivalence',x['interpretation'])
        self.assertEqual(len(p['evidence_families']),1);self.assertFalse(p['study_counts_aggregated']);self.assertFalse(p['individual_benefit_estimated'])
    def test_explicit_mapping_uses_exact_service_without_clinical_promotion(self):
        req={**self.comparison,'service_bindings':[self.binding()]};out=C.preview(self.path,req);a=out['alternatives'][1]
        self.assertEqual(a['proposed_service']['capability']['id'],'pharmacy')
        self.assertEqual(a['mapping_status'],'proposed_mapping_requires_verification');self.assertIsNotNone(a['participation'])
        self.assertFalse(a['participation']['verified_participation']);self.assertFalse(out['coverage_established'])
        self.assertIsNone(a['individual_benefit']);self.assertTrue(any('tags and names' in x for x in a['requirements']))
    def test_modality_constraint_does_not_become_medical_contraindication(self):
        out=C.preview(self.path,{**self.comparison,'service_bindings':[self.binding('center')]});a=out['alternatives'][0]
        self.assertEqual(a['mapping_status'],'declared_constraints_blocked');self.assertIn('modality',a['declared_blockers'][0])
        self.assertFalse(out['clinical_applicability_established']);self.assertIsNone(a['individual_harm'])
    def test_all_route_blockers_remain_explicit(self):
        blocked={**self.cap,'id':'blocked','available':False}
        C.care.save_portfolio(self.path,{'buyer_id':'buyer1','expected_revision':1,'actor':'fixture','capabilities':[self.cap,blocked]})
        plan=C.care.prepare(self.path,self.req);active=next(o for o in plan['options'] if o['capability']['id']=='pharmacy')
        unavailable=next(o for o in plan['options'] if o['capability']['id']=='blocked')
        task=C.care.enqueue(self.path,{'plan_id':plan['id'],'option_id':active['id'],'actor':'fixture'})['task']
        m=R.materialize(self.path,{**self.request,'task_id':task['id'],'expected_revision':task['revision'],'target':R.origin_targets(self.path,task['id'])[0]['target']})
        out=C.preview(self.path,{**self.comparison,'manifest_id':m['id'],'service_bindings':[{**self.binding(),'option_id':unavailable['id']}]})
        self.assertEqual(out['alternatives'][1]['mapping_status'],'declared_constraints_blocked')
        self.assertEqual(out['alternatives'][1]['route_blockers'][0]['reasons'],['Buyer reports service unavailable'])
    def test_wrong_service_alternative_duplicate_and_authority_rejected(self):
        bad=[{**self.comparison,'service_bindings':[{**self.binding(),'option_id':'0'*64}]},
             {**self.comparison,'service_bindings':[self.binding('unknown')]},
             {**self.comparison,'service_bindings':[self.binding(),self.binding()]},
             {**self.comparison,'service_bindings':[{**self.binding(),'clinical_verified':True}]},
             {**self.comparison,'selected_alternative':'remote'},{**self.comparison,'question':''}]
        for req in bad:
            with self.assertRaises(ValueError):C.preview(self.path,req)
    def test_no_coverage_or_patient_context_is_not_confirmation(self):
        out=C.preview(self.path,self.comparison);self.assertIsNone(out['received_coverage']);self.assertIsNone(out['patient_context'])
        self.assertFalse(out['coverage_established']);self.assertFalse(out['clinical_review_recorded'])
        self.assertFalse(out['reference_currency']['current_at_patient_decision'])
    def test_bound_patient_and_coverage_evidence_carried_without_promotion(self):
        coverage={'id':'coverage-fixture','coverage_selection':{'report':{'fields':{'payer_organization_id':'payer1'}}}}
        patient={'id':'patient-fixture','context':{'reports':[{'record_id':'travel-fixture','fields':{'transport':'reported'}}]}}
        with patch.object(C.coverage_work,'association',return_value=coverage),patch.object(C.patient_context,'association',return_value=patient):
            out=C.preview(self.path,{**self.comparison,'service_bindings':[self.binding()]})
        self.assertEqual(out['received_coverage'],coverage);self.assertEqual(out['patient_context'],patient)
        self.assertTrue(any(x['kind']=='digital_access' for x in out['alternatives'][1]['patient_access_checks']))
        self.assertFalse(out['coverage_established'])
    def test_unknown_reference_has_concrete_unavailable_result(self):
        e=C.evidence();e['reference_doi']='different'
        with patch.object(C,'evidence',return_value=e):
            with self.assertRaisesRegex(ValueError,'No option evidence mapping'):C.preview(self.path,self.comparison)
    def test_new_source_preparation_invalidates_prior_preview(self):
        req=self.comparison_request();self.acquire()
        with self.assertRaisesRegex(ValueError,'dependencies changed'):C.prepare(self.path,req)
        p=C.preview(self.path,self.comparison);self.assertEqual(len(p['questions'][0]['source_contexts']),1)
        self.assertEqual(p['questions'][0]['target'],self.target);self.assertEqual(p['questions'][0]['state'],'applicability_unresolved')
    def test_source_context_change_marks_saved_comparison_historical(self):
        a=self.acquire();out=C.prepare(self.path,self.comparison_request());self.contexts[a['context_id']]['omissions']=['changed']
        old=C.listing(self.path,{'manifest_id':self.row['id']})['comparisons'][0]
        self.assertEqual(old['id'],out['id']);self.assertFalse(old['current'])
    def test_reference_revision_preserves_comparison_history(self):
        out=C.prepare(self.path,self.comparison_request());self.commit()
        history=C.listing(self.path,{'manifest_id':self.row['id']})['comparisons'];self.assertEqual(history[0]['id'],out['id']);self.assertFalse(history[0]['current'])
        with self.assertRaises(ValueError):C.preview(self.path,self.comparison)
    def test_participation_change_invalidates_exact_mapped_comparison(self):
        request=self.comparison_request({**self.comparison,'service_bindings':[self.binding()]});old=C.participation.registry
        with patch.object(C.participation,'registry',side_effect=lambda *args:{**old(*args),'revision':2}):
            with self.assertRaisesRegex(ValueError,'dependencies changed'):C.prepare(self.path,request)
    def test_plan_change_invalidates_comparison(self):
        out=C.prepare(self.path,self.comparison_request());self.answer['question']='New answer question'
        history=C.listing(self.path,{'manifest_id':self.row['id']})['comparisons'];self.assertEqual(history[0]['id'],out['id']);self.assertFalse(history[0]['current'])
    def test_concurrent_save_and_replay_do_not_duplicate(self):
        req=self.comparison_request()
        with ThreadPoolExecutor(max_workers=2) as pool:rows=list(pool.map(lambda _:C.prepare(self.path,req),range(2)))
        self.assertEqual(rows[0],rows[1]);self.assertEqual(C.prepare(self.path,req),rows[0]);self.assertEqual(len(C.listing(self.path,{'manifest_id':self.row['id']})['comparisons']),1)
    def test_final_dependency_check_rolls_back(self):
        req=self.comparison_request();old=C.snapshot;calls=[]
        def changing(*args):
            out=old(*args);calls.append(1)
            if len(calls)>1:out['question']='changed'
            return out
        with patch.object(C,'snapshot',side_effect=changing):
            with self.assertRaisesRegex(ValueError,'while saving'):C.prepare(self.path,req)
        self.assertEqual(C.listing(self.path,{'manifest_id':self.row['id']})['comparisons'],[])
    def test_immutable_comparison_and_tamper_detection(self):
        out=C.prepare(self.path,self.comparison_request());db=C.database(self.path)
        try:
            for sql in ['DELETE FROM comparisons','UPDATE comparisons SET manifest=manifest']:
                with self.assertRaises(sqlite3.IntegrityError):db.execute(sql)
            db.execute('DROP TRIGGER immutable_comparisons_UPDATE');out['clinical_applicability_established']=True;db.execute('UPDATE comparisons SET payload=?',(json.dumps(out),));db.commit()
        finally:db.close()
        with self.assertRaisesRegex(ValueError,'integrity'):C.listing(self.path,{'manifest_id':self.row['id']})
    def test_comparison_never_changes_task_or_queues_clinical_action(self):
        C.prepare(self.path,self.comparison_request({**self.comparison,'service_bindings':[self.binding()]}))
        self.assertEqual(R.care_readiness.task(self.path,self.task['id']),self.task);self.assertEqual(R.load(self.path,self.row['id']),self.row)
    def test_invalid_numeric_evidence_is_rejected(self):
        data=json.loads(C.CATALOG.read_text());data['estimates'][0]['point']=float('nan')
        with patch.object(C.json,'loads',return_value=data):
            with self.assertRaisesRegex(ValueError,'Finite'):C.evidence()

if __name__=='__main__':unittest.main()
