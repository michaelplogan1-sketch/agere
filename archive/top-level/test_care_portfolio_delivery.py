import copy,json,sqlite3,unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import test_care_option_delivery as fixture
import test_option_evidence_registry as option_fixture
from clinical_core.agere.clinical import care_portfolio_delivery as P,care_option_delivery as D,care_option_comparison as C,reference_applicability as R,patient_context,coverage_sources

class PortfolioDelivery(unittest.TestCase):
    def setUp(self):
        fixture.Delivery.setUp(self)
        initial=C.care.inspect_plan(self.path,self.task['plan_id']);self.care_request=initial['request']
        self.extra={**self.cap,'id':'separate-prescriber','name':'Separate prescribing service fixture','need_tags':['prescribing','follow_up'],'owner':{'kind':'clinician','id':'fixture-prescriber'}}
        C.care.save_portfolio(self.path,{'buyer_id':'buyer1','expected_revision':1,'actor':'fixture','capabilities':[self.cap,self.extra]})
        self.rebuild(self.care_request)
    def rebuild(self,request):
        plan=patient_context.prepare_match(self.path,request)
        self.task=C.care.enqueue(self.path,{'plan_id':plan['id'],'option_id':plan['options'][0]['id'],'actor':'fixture'})['task']
        self.new=R.materialize(self.path,{**self.newrequest,'task_id':self.task['id'],'expected_revision':self.task['revision'],'target':R.origin_targets(self.path,self.task['id'])[0]['target']})
        self.comparison=option_fixture.Registry.save(self,{**self.req,'manifest_id':self.new['id']})
        self.delivery=fixture.Delivery.save(self,{**self.request,'comparison_id':self.comparison['id']})
        self.request={'delivery_id':self.delivery['id'],'component_bindings':[],'coordination_note':'Cross-service proposals only; no clinician assignment or clinical choice.'}
    def binding(self,key='medication_service',cap='separate-prescriber',tags=None):
        return {'component_key':key,'capability_id':cap,'need_tags':tags or (['prescribing'] if cap=='separate-prescriber' else ['medication_review']),'rationale':'Explicit proposed component service, not verified care'}
    def save(self,req=None):
        req=req or self.request;p=P.preview(self.path,req);return P.prepare(self.path,{'proposal':req,'preview_id':p['id']})
    def test_full_portfolio_finds_cross_tag_service_without_relevance_claim(self):
        c=P.choices(self.path,{'delivery_id':self.delivery['id']})
        self.assertEqual((c['original_matched_services'],c['portfolio_services']),(1,2))
        extra=next(s for s in c['services'] if s['capability']['id']=='separate-prescriber')
        self.assertFalse(extra['in_original_match']);self.assertEqual(extra['original_exclusion_reason'],'No declared need-tag overlap')
        self.assertFalse(c['patient_needs_inferred'])
    def test_cross_tag_match_preserves_patient_origin_coverage_and_reference(self):
        out=self.save({**self.request,'component_bindings':[self.binding()]});p=out['components'][1]['proposed_service']
        self.assertFalse(p['in_original_match']);secondary=C.care.inspect_plan(self.path,p['secondary_plan_id'])
        original=C.care.inspect_plan(self.path,self.comparison['plan_id'])
        for key in ['patient_id','origin','buyer_id','coverage']:self.assertEqual(secondary[key],original[key])
        self.assertEqual(secondary['need_tags'],['prescribing']);self.assertEqual(out['reference'],self.delivery['reference'])
        self.assertEqual(out['original_match_baseline']['proposed_components_outside_original_match'],['medication_service'])
        self.assertFalse(out['component_capabilities_verified']);self.assertFalse(out['clinical_alternative_selected'])
    def test_two_component_services_remain_proposals_and_do_not_mutate_tasks(self):
        out=self.save({**self.request,'component_bindings':[self.binding('cbt_service','pharmacy'),self.binding()]})
        self.assertEqual(out['coverage_of_proposal']['distinct_proposed_services'],2)
        self.assertTrue(out['coverage_of_proposal']['all_components_have_proposals']);self.assertFalse(out['coverage_of_proposal']['all_components_verified'])
        self.assertEqual(R.care_readiness.task(self.path,self.task['id']),self.task)
        self.assertEqual(D.snapshot(self.path,self.delivery['request']),self.delivery)
    def test_same_capability_with_different_tags_counted_once(self):
        out=self.save({**self.request,'component_bindings':[self.binding('cbt_service'),self.binding(tags=['follow_up'])]})
        self.assertEqual(out['coverage_of_proposal']['distinct_proposed_services'],1)
        self.assertEqual(len(out['coordination']['same_service_multiple_components']),1)
    def test_unassigned_components_do_not_infer_services(self):
        out=self.save();self.assertEqual(out['coverage_of_proposal']['components_without_proposals'],['cbt_service','medication_service'])
        self.assertEqual(out['original_match_baseline']['portfolio_services'],2)
    def test_empty_unknown_foreign_duplicate_and_extra_authority_rejected(self):
        bad=[{'component_bindings':[{**self.binding(),'need_tags':[]}]},{'component_bindings':[self.binding(tags=['not_declared'])]},
          {'component_bindings':[self.binding(cap='foreign-buyer-service')]},{'component_bindings':[self.binding(),self.binding()]},
          {'component_bindings':[self.binding('unknown')]},{'patient_needs_inferred':True},{'coordination_note':''},{'delivery_id':'0'*64}]
        for change in bad:
            with self.assertRaises(ValueError):P.preview(self.path,{**self.request,**change})
    def test_preserves_both_source_bindings_without_duplicating_markers(self):
        self.records.extend([
          {'record_id':'cov','patient_id':'p1','source_table':'coverage','event_date':'2026-10-01','available_date':'2026-10-02','coverage_type':'original_medicare','plan_id':'plan1','effective_start':'2026-01-01','effective_end':'2026-12-31'},
          {'record_id':'social','patient_id':'p1','source_table':'social_screens','event_date':'2026-10-01','available_date':'2026-10-02','transport':'unreliable','_warehouse_fields':['transport']}])
        cov=coverage_sources.prepare(self.path,{'patient_id':'p1','as_of':'2026-10-10'});selection=coverage_sources.select(cov,'cov')
        patient=patient_context.prepare(self.path,{'patient_id':'p1','as_of':'2026-10-10'})
        req={**self.care_request,'coverage':{**self.care_request['coverage'],**selection['routing_fields']},
          'coverage_binding':{'context_id':cov['id'],'record_id':'cov'},'patient_context_id':patient['id']}
        self.request=self.delivery['request'];self.rebuild(req)
        out=self.save({**self.request,'component_bindings':[self.binding()]});p=out['components'][1]['proposed_service']
        self.assertEqual(p['patient_context']['context'],patient);self.assertEqual(p['received_coverage']['coverage_selection'],selection)
        self.assertEqual(p['original_bound_request']['patient_context_id'],patient['id'])
        self.assertEqual(p['original_bound_request']['coverage_binding']['record_id'],'cov')
        self.assertFalse(p['patient_context']['source_binding_is_patient_confirmation'])
        self.assertTrue(out['components'][1]['patient_access_checks'])
    def test_secondary_context_or_coverage_corruption_rejected(self):
        old=patient_context.prepare_match
        def corrupt(path,req):
            p=old(path,req)
            return {**p,'id':self.comparison['plan_id']}
        with patch.object(patient_context,'prepare_match',side_effect=corrupt):
            with self.assertRaisesRegex(ValueError,'Selected capability absent'):P.preview(self.path,{**self.request,'component_bindings':[self.binding()]})
    def test_participation_change_stales_saved_proposal(self):
        row=self.save({**self.request,'component_bindings':[self.binding()]});old=C.participation.registry
        with patch.object(C.participation,'registry',side_effect=lambda *args:{**old(*args),'revision':2}):
            self.assertFalse(P.listing(self.path,{'delivery_id':self.delivery['id']})['proposals'][0]['current'])
        self.assertEqual(self.save(row['request']),row)
    def test_portfolio_change_requires_new_original_context(self):
        self.save();C.care.save_portfolio(self.path,{'buyer_id':'buyer1','expected_revision':2,'actor':'fixture','capabilities':[self.cap,{**self.extra,'available':False}]})
        self.assertFalse(P.listing(self.path,{'delivery_id':self.delivery['id']})['proposals'][0]['current'])
    def test_unavailable_cross_tag_service_reports_route_blocker(self):
        C.care.save_portfolio(self.path,{'buyer_id':'buyer1','expected_revision':2,'actor':'fixture','capabilities':[self.cap,{**self.extra,'available':False}]})
        self.request=self.delivery['request'];self.rebuild(self.care_request)
        out=self.save({**self.request,'component_bindings':[self.binding()]});c=out['components'][1]
        self.assertEqual(c['status'],'declared_constraints_blocked');self.assertIn('Buyer reports service unavailable',c['route_blockers'][0]['blocked_reasons'])
        self.assertFalse(c['verified_delivery_capability'])
    def test_maximum_rationale_is_preserved(self):
        out=self.save({**self.request,'component_bindings':[{**self.binding(),'rationale':'x'*2000}]})
        self.assertEqual(out['components'][1]['proposed_service']['matching_request']['rationale'],'x'*2000)
    def test_replay_concurrent_commit_and_bad_preview(self):
        p=P.preview(self.path,self.request);req={'proposal':self.request,'preview_id':p['id']}
        with ThreadPoolExecutor(max_workers=2) as pool:rows=list(pool.map(lambda _:P.prepare(self.path,req),range(2)))
        self.assertEqual(rows[0],rows[1]);self.assertEqual(len(P.listing(self.path,{'delivery_id':self.delivery['id']})['proposals']),1)
        with self.assertRaises(ValueError):P.prepare(self.path,{**req,'preview_id':'bad'})
    def test_final_freshness_check_rolls_back(self):
        p=P.preview(self.path,self.request);old=P.snapshot;calls=[]
        def change(*args):
            row=old(*args);calls.append(1)
            if len(calls)>1:row['portfolio_revision']+=1
            return row
        with patch.object(P,'snapshot',side_effect=change):
            with self.assertRaisesRegex(ValueError,'while saving'):P.prepare(self.path,{'proposal':self.request,'preview_id':p['id']})
        self.assertEqual(P.listing(self.path,{'delivery_id':self.delivery['id']})['proposals'],[])
    def test_immutable_history_and_tamper_detection(self):
        row=self.save();db=P.database(self.path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM proposals')
            db.execute('DROP TRIGGER immutable_proposals_UPDATE');row['clinical_alternative_selected']=True;db.execute('UPDATE proposals SET payload=?',(json.dumps(row),));db.commit()
        finally:db.close()
        with self.assertRaisesRegex(ValueError,'integrity'):P.listing(self.path,{'delivery_id':self.delivery['id']})

if __name__=='__main__':unittest.main()
