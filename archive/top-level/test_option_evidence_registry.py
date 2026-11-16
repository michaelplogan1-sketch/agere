import copy,json,unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import test_reference_lifecycle as fixture
from clinical_core.agere.clinical import option_evidence_registry as O,care_option_comparison as C,reference_applicability as R,reference_lifecycle as L,care_option_context as X

class Registry(unittest.TestCase):
    def setUp(self):
        fixture.Lifecycle.setUp(self)
        self.legacy_request={'manifest_id':self.row['id'],'question':'Compare source-backed alternatives','rationale':'Engineering comparison fixture','service_bindings':[]}
        self.legacy=C.preview(self.path,self.legacy_request)
        for module,names in [(C,['evidence','snapshot','choices']),(L,['_BASE_REFERENCES'])]:
            for name in names:self.addCleanup(setattr,module,name,getattr(module,name))
        O.install();O.install()
        self.ref=O.bundles()[0]['reference'];self.newrequest={**self.request,'reference_id':self.ref['id']}
        self.new=R.materialize(self.path,self.newrequest)
        self.req={**self.legacy_request,'manifest_id':self.new['id']}
    def save(self,request=None):
        req=request or self.req;p=C.preview(self.path,req)
        return C.prepare(self.path,{'comparison':req,'preview_id':p['id']})
    def test_legacy_identity_and_receipts_exactly_unchanged(self):
        self.assertEqual(C.preview(self.path,self.legacy_request),self.legacy)
        self.assertEqual(C.evidence()['key'],'pulmonary-rehabilitation-delivery')
        self.assertEqual(R.load(self.path,self.row['id']),self.row)
    def test_registered_reference_in_actual_work_and_packet_targets(self):
        self.assertEqual(len(R.inspect(self.path,self.task['id'])['sets']),2)
        targets=R.packet_followup.targets(self.path,self.task['id'])
        self.assertEqual(sum(t['target'].get('manifest_id')==self.new['id'] for t in targets),6)
        self.assertEqual(self.new['original_target'],self.row['original_target'])
    def test_three_unranked_alternatives_without_indication_or_benefit(self):
        out=self.save();self.assertEqual([a['key'] for a in out['alternatives']],['cbt','sga','combined'])
        self.assertEqual(out['reported_comparative_estimates'],[])
        self.assertTrue(all(a['individual_benefit'] is None and a['rank'] is None and a['proposed_service'] is None for a in out['alternatives']))
        self.assertFalse(out['clinical_applicability_established']);self.assertFalse(out['clinical_review_recorded'])
        self.assertIsNone(out['baseline']['selected_alternative'])
        self.assertEqual(len(C.choices(self.path,{'manifest_id':self.new['id']})['alternatives']),3)
    def test_known_update_is_unreconciled_not_current_guidance(self):
        out=self.save();e=out['option_evidence']
        self.assertEqual(e['currency_evidence']['status'],'known_update_content_unreconciled')
        self.assertFalse(e['currency_evidence']['current_recommendation_verified'])
        self.assertFalse(out['reference_currency']['current_at_patient_decision'])
        self.assertIn('no comparative harm rates',e['harm_summary'])
        self.assertTrue(any('March 2026' in q['question'] for q in out['questions']))
    def test_exact_source_question_acquisition_and_snapshot_recheck(self):
        target=R.target_for(self.new,self.new['questions'][0]);before=self.save()
        acquired=R.D.acquisition_context(self.path,{'target':target,'record_ids':['r1']})
        self.assertEqual(self.contexts[acquired['context_id']]['parameters']['question'],R.resolve(self.path,target)['question'])
        self.assertFalse(C.listing(self.path,{'manifest_id':self.new['id']})['comparisons'][0]['current'])
        after=self.save();self.assertNotEqual(before['id'],after['id'])
        self.assertTrue(after['questions'][0]['source_contexts'][0]['current'])
        self.assertEqual(after['questions'][0]['state'],'applicability_unresolved')
    def test_local_revision_retains_correct_bundle_without_transferring_answer(self):
        change={**self.change,'reference_id':self.ref['id'],'question_changes':{'assessment':'What assessment, prior treatment, safety review and follow-up are needed?'}}
        p=L.preview(self.path,change);event=L.commit(self.path,{'change':change,'preview_id':p['id'],'request_id':'new-domain'})
        m=L.successor(self.path,{'manifest_id':self.new['id'],'event_id':event['id'],'expected_task_revision':self.task['revision'],'reason':'Explicit fixture revision'})
        out=C.preview(self.path,{**self.req,'manifest_id':m['id']});self.assertEqual(len(out['alternatives']),3)
        with self.assertRaises(ValueError):C.preview(self.path,self.req)
    def test_withdrawn_reference_blocks_new_comparison(self):
        self.save();change={**self.change,'reference_id':self.ref['id'],'event_kind':'local_withdrawal','question_changes':{}}
        p=L.preview(self.path,change);L.commit(self.path,{'change':change,'preview_id':p['id'],'request_id':'withdraw-domain'})
        self.assertFalse(C.listing(self.path,{'manifest_id':self.new['id']})['comparisons'][0]['current'])
        self.assertEqual(C.preview(self.path,self.legacy_request),self.legacy)
    def test_parallel_domains_never_share_evidence(self):
        requests=[self.req,self.legacy_request]*5
        with ThreadPoolExecutor(max_workers=4) as pool:out=list(pool.map(lambda r:C.preview(self.path,r),requests))
        self.assertEqual([len(r['alternatives']) for r in out],[3,2]*5)
        self.assertEqual(C.evidence()['key'],'pulmonary-rehabilitation-delivery')
    def test_failed_dispatch_resets_context(self):
        with self.assertRaises(ValueError):C.preview(self.path,{**self.req,'service_bindings':[{'alternative_key':'unknown','option_id':'bad','rationale':'Invalid fixture'}]})
        self.assertEqual(C.preview(self.path,self.legacy_request),self.legacy)
    def test_changed_bundle_refuses_saved_use_and_preserves_original(self):
        row=self.save();entries=O.bundles();entries[0]['reference']['registry_entry']['bundle_hash']='changed'
        with patch.object(O,'bundles',return_value=entries):
            with self.assertRaisesRegex(ValueError,'bundle changed'):C.preview(self.path,self.req)
            self.assertFalse(C.listing(self.path,{'manifest_id':self.new['id']})['comparisons'][0]['current'])
        self.assertEqual(R.load(self.path,self.new['id']),self.new)
        self.assertTrue(C.listing(self.path,{'manifest_id':self.new['id']})['comparisons'][0]['current'])
    def test_same_doi_without_registered_identity_cannot_borrow_bundle(self):
        manifest=copy.deepcopy(self.new);del manifest['reference']['registry_entry']
        with patch.object(R,'load',return_value=manifest),patch.object(R,'current'):
            with self.assertRaisesRegex(ValueError,'No option evidence mapping'):C.preview(self.path,self.req)
    def test_received_context_composes_and_leaves_questions_unresolved(self):
        saved=self.save();p=X.preview(self.path,{'comparison_id':saved['id']});out=X.prepare(self.path,{'comparison_id':saved['id'],'preview_id':p['id']})
        self.assertEqual(out['summary']['coverage_reports'],0)
        self.assertEqual(len(out['coverage_review_targets']),1);self.assertEqual(len(out['patient_review_targets']),2)
        self.assertFalse(out['patient_preference_confirmed']);self.assertFalse(out['coverage_established'])
        self.assertEqual(R.care_readiness.task(self.path,self.task['id']),self.task)
    def test_replay_and_bad_preview(self):
        row=self.save();self.assertEqual(self.save(),row)
        with self.assertRaises(ValueError):C.prepare(self.path,{'comparison':self.req,'preview_id':'bad'})
        self.assertEqual(len(C.listing(self.path,{'manifest_id':self.new['id']})['comparisons']),1)
    def test_proposed_generic_service_does_not_prove_cbt_capability(self):
        b={'alternative_key':'cbt','option_id':self.task['option_id'],'rationale':'Unverified engineering proposal'}
        a=self.save({**self.req,'service_bindings':[b]})['alternatives'][0]
        self.assertEqual(a['mapping_status'],'proposed_mapping_requires_verification')
        self.assertTrue(any('structured CBT' in q for q in a['delivery_questions']))
        self.assertIsNone(a['individual_benefit'])
    def test_registry_rejects_malformed_ambiguous_or_promoted_evidence(self):
        data=json.loads(O.CATALOG.read_text());bad=[]
        x=copy.deepcopy(data);x['bundles']*=2;bad.append(x)
        for where,key,value in [('reference','review_status','clinically_reviewed'),('evidence','reference_doi','other'),('evidence','alternatives',[]),('evidence','quantitative_evidence_status','')]:
            x=copy.deepcopy(data);x['bundles'][0][where][key]=value;bad.append(x)
        p=self.path/'bad-registry.json'
        for x in bad:
            p.write_text(json.dumps(x))
            with patch.object(O,'CATALOG',p):
                with self.assertRaises(ValueError):O.bundles()

if __name__=='__main__':unittest.main()
