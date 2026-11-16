import copy,json,sqlite3,unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import test_option_evidence_registry as fixture
from clinical_core.agere.clinical import care_option_delivery as D,care_option_comparison as C,reference_applicability as R

class Delivery(unittest.TestCase):
    def setUp(self):
        fixture.Registry.setUp(self)
        self.comparison=fixture.Registry.save(self)
        self.request={'comparison_id':self.comparison['id'],'alternative_key':'combined','component_bindings':[],'coordination_note':'Coordinate therapy and prescribing if clinically appropriate; no clinician assigned.'}
    def binding(self,key='cbt_service',option=None):return {'component_key':key,'option_id':option or self.task['option_id'],'rationale':'Explicit unverified fixture service proposal'}
    def save(self,req=None):
        req=req or self.request;p=D.preview(self.path,req);return D.prepare(self.path,{'proposal':req,'preview_id':p['id']})
    def test_unassigned_combination_exposes_two_distinct_delivery_gaps(self):
        out=self.save();s=out['coverage_of_proposal']
        self.assertEqual((s['required_components'],s['components_with_proposals'],s['distinct_proposed_services']),(2,0,0))
        self.assertEqual(s['components_without_proposals'],['cbt_service','medication_service'])
        self.assertFalse(s['all_components_verified']);self.assertFalse(out['clinical_alternative_selected'])
    def test_single_service_proposal_does_not_fill_other_component(self):
        out=self.save({**self.request,'component_bindings':[self.binding()]})
        self.assertEqual(out['coverage_of_proposal']['components_without_proposals'],['medication_service'])
        self.assertFalse(out['components'][0]['verified_delivery_capability'])
        self.assertIsNotNone(out['components'][0]['participation'])
    def test_same_declared_service_for_both_requires_explicit_component_proposals(self):
        out=self.save({**self.request,'component_bindings':[self.binding(),self.binding('medication_service')]})
        self.assertTrue(out['coverage_of_proposal']['all_components_have_proposals'])
        self.assertFalse(out['coverage_of_proposal']['all_components_verified'])
        self.assertEqual(out['coverage_of_proposal']['distinct_proposed_services'],1)
        self.assertEqual(len(out['coordination']['same_service_multiple_components']),1)
    def test_monotherapy_requires_only_its_own_component(self):
        out=self.save({**self.request,'alternative_key':'sga','component_bindings':[self.binding('medication_service')]})
        self.assertEqual(out['coverage_of_proposal']['required_components'],1)
        with self.assertRaises(ValueError):self.save({**self.request,'alternative_key':'sga','component_bindings':[self.binding()]})
    def test_two_services_and_unavailable_component(self):
        original=C.care.inspect_plan(self.path,self.task['plan_id'])
        C.care.save_portfolio(self.path,{'buyer_id':'buyer1','expected_revision':1,'actor':'fixture','capabilities':[self.cap,{**self.cap,'id':'therapy','name':'CBT service fixture','available':False}]})
        care_req=original['request']
        plan=C.care.prepare(self.path,care_req);active=next(o for o in plan['options'] if o['capability']['id']=='pharmacy');therapy=next(o for o in plan['options'] if o['capability']['id']=='therapy')
        task=C.care.enqueue(self.path,{'plan_id':plan['id'],'option_id':active['id'],'actor':'fixture'})['task']
        manifest=R.materialize(self.path,{**self.newrequest,'task_id':task['id'],'expected_revision':task['revision'],'target':R.origin_targets(self.path,task['id'])[0]['target']})
        comparison=fixture.Registry.save(self,{**self.req,'manifest_id':manifest['id']})
        out=self.save({**self.request,'comparison_id':comparison['id'],'component_bindings':[self.binding(option=therapy['id']),self.binding('medication_service',active['id'])]})
        self.assertEqual(out['coverage_of_proposal']['distinct_proposed_services'],2)
        self.assertEqual(out['components'][0]['status'],'declared_constraints_blocked')
        self.assertTrue(out['coverage_of_proposal']['all_components_have_proposals']);self.assertFalse(out['coverage_of_proposal']['all_components_verified'])
        self.assertFalse(out['clinical_alternative_selected'])
    def test_wrong_component_service_duplicate_or_authority_rejected(self):
        for change in [{'alternative_key':'missing'},{'coordination_note':''},{'clinical_alternative_selected':True},
          {'component_bindings':[self.binding(option='other-plan')]},{'component_bindings':[self.binding(),self.binding()]},{'component_bindings':[self.binding('unknown')]}]:
            with self.assertRaises(ValueError):D.preview(self.path,{**self.request,**change})
    def test_unknown_domain_is_explicitly_unmapped(self):
        legacy=C.prepare(self.path,{'comparison':self.legacy_request,'preview_id':self.legacy['id']})
        with self.assertRaisesRegex(ValueError,'No unique component mapping'):D.choices(self.path,{'comparison_id':legacy['id']})
    def test_source_acquisition_change_stales_delivery_without_rewriting(self):
        out=self.save();target=R.target_for(self.new,self.new['questions'][0]);R.D.acquisition_context(self.path,{'target':target,'record_ids':['r1']})
        row=D.listing(self.path,{'comparison_id':self.comparison['id']})['proposals'][0]
        self.assertEqual(row['id'],out['id']);self.assertFalse(row['current'])
    def test_component_schema_change_invalidates_preview(self):
        p=D.preview(self.path,self.request)
        with patch.object(D,'identity',return_value='changed'):
            with self.assertRaises(ValueError):D.prepare(self.path,{'proposal':self.request,'preview_id':p['id']})
    def test_concurrent_replay_and_no_work_mutations(self):
        p=D.preview(self.path,self.request);req={'proposal':self.request,'preview_id':p['id']}
        with ThreadPoolExecutor(max_workers=2) as pool:rows=list(pool.map(lambda _:D.prepare(self.path,req),range(2)))
        self.assertEqual(rows[0],rows[1]);self.assertEqual(len(D.listing(self.path,{'comparison_id':self.comparison['id']})['proposals']),1)
        self.assertEqual(R.care_readiness.task(self.path,self.task['id']),self.task)
        self.assertEqual(C.snapshot(self.path,self.comparison['request']),self.comparison)
        self.assertEqual(rows[0]['care_tasks_created'],0);self.assertEqual(rows[0]['service_slots_reserved'],0)
    def test_final_freshness_check_rolls_back(self):
        p=D.preview(self.path,self.request);old=D.snapshot;calls=[]
        def change(*args):
            row=old(*args);calls.append(1)
            if len(calls)>1:row['owner']={}
            return row
        with patch.object(D,'snapshot',side_effect=change):
            with self.assertRaisesRegex(ValueError,'while saving'):D.prepare(self.path,{'proposal':self.request,'preview_id':p['id']})
        self.assertEqual(D.listing(self.path,{'comparison_id':self.comparison['id']})['proposals'],[])
    def test_immutable_history_and_tamper_detection(self):
        out=self.save();db=D.database(self.path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM proposals')
            db.execute('DROP TRIGGER immutable_proposals_UPDATE');out['clinical_alternative_selected']=True;db.execute('UPDATE proposals SET payload=?',(json.dumps(out),));db.commit()
        finally:db.close()
        with self.assertRaisesRegex(ValueError,'integrity'):D.listing(self.path,{'comparison_id':self.comparison['id']})

if __name__=='__main__':unittest.main()
