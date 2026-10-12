"""Full population question binding and shared buyer work; synthetic fixtures only."""
import copy,json,sqlite3,unittest
from types import SimpleNamespace
from unittest.mock import patch
import test_domain_programs as domain_fixtures
import test_care_capabilities as care_fixtures
from clinical_core.agere.clinical import question_decision as q, cohorts

class PopulationDecision(unittest.TestCase):
    def setUp(self):
        self.f=domain_fixtures.DomainPrograms();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.path=self.f.root;self.retriever=SimpleNamespace(path=self.path)
        self.c=care_fixtures.CareCapabilities();self.c.setUp();self.addCleanup(self.c.doCleanups)
        self.cap=copy.deepcopy(self.c.cap);self.cap.update(need_tags=['symptom_support','behavioral_health'],capacity_as_of='2026-01-01',capacity_until='2030-01-01',effective_end='2030-01-01')
        q.care.save_portfolio(self.path,{'buyer_id':'buyer','expected_revision':0,'actor':'fixture','capabilities':[self.cap]})
        asof=json.loads((self.path/'run.json').read_text())['snapshot']['as_of']
        self.req={'question':'Review received respiratory sources across this population','program':self.f.program,'cohort':cohorts.empty(),'buyer_id':'buyer','as_of':asof}
    def prepare(self):return q.prepare_population(self.retriever,self.req)
    def patch(self,obj,key,**kwargs):
        p=patch.object(obj,key,**kwargs);p.start();self.addCleanup(p.stop)
    def stub_member_wrappers(self):
        self.patch(q.patient_context,'prepare',return_value={'id':'context'})
        def match(path,r):
            x=q.care.prepare(path,{k:v for k,v in r.items() if k!='patient_context_id'})
            return {**x,'participation_contexts':{o['id']:{'id':'participation'} for o in x['options']}}
        self.patch(q.patient_context,'prepare_match',side_effect=match)
        self.patch(q.patient_context,'association',return_value=None)
        self.patch(q.participation_work,'enqueue',side_effect=lambda p,r:q.care.enqueue(p,{k:v for k,v in r.items() if k!='participation_context_id'}))
        self.patch(q.patient_context,'task_event',side_effect=q.care.task_event)
    def test_full_roster_and_unknown_need_retained(self):
        d=self.prepare();self.assertEqual(d,self.prepare())
        self.assertEqual(d['selection']['counts']['evaluated'],6);self.assertEqual(len(d['members']),6)
        self.assertEqual(d['counts']['review_candidates'],1);self.assertEqual(d['counts']['need_unresolved_without_nomination'],5)
        self.assertTrue(q.reopen(self.retriever,d['id'])['dependencies_current'])
        self.assertEqual(d['service_scope'][0]['candidate_count'],1)
        self.assertFalse(d['clinical_execution_authorized'])
    def test_independent_second_program_composition(self):
        self.req['program']='behavioral_source_review';d=self.prepare()
        self.assertEqual([m['patient_id'] for m in d['members'] if m['review_candidate']],['p1'])
        self.assertEqual(d['counts']['mapped_for_documentary_review'],1)
    def test_unknown_cohort_members_never_disappear(self):
        with sqlite3.connect(self.path/'stack.sqlite3') as db:
            raw=json.loads(db.execute("SELECT payload FROM patients WHERE id='p5'").fetchone()[0]);raw['sex']=None
            db.execute("UPDATE patients SET payload=? WHERE id='p5'",(json.dumps(raw),))
        self.req['cohort']={'all':[{'kind':'patient','field':'sex','op':'eq','value':'not-observed'}],'any':[],'exclude':[]}
        d=self.prepare();self.assertEqual(sum(d['selection']['counts'][k] for k in ('matched','not_matched','unknown')),6)
        self.assertIn('p5',d['selection']['unknown_patient_ids'])
        self.assertEqual(len(d['members']),d['selection']['counts']['matched'])
        self.assertEqual(len(d['selection']['not_matched_patient_ids']),d['selection']['counts']['not_matched'])
    def test_historical_date_and_injected_tags_rejected(self):
        for request in ({**self.req,'as_of':'2020-01-01'},{**self.req,'need_tags':['symptom_support']}):
            with self.assertRaises(ValueError):q.prepare_population(self.retriever,request)
    def test_modified_source_invalidates_original_question(self):
        d=self.prepare();self.f.save_record({**self.f.record,'reliever_directions_confirmed':True})
        self.assertFalse(q.reopen(self.retriever,d['id'])['dependencies_current'])
        with self.assertRaises(ValueError):q.match_population_member(self.retriever,{'decision_id':d['id'],'patient_id':'p0'})
    def test_buyer_revision_invalidates_saved_scope(self):
        d=self.prepare();self.cap['capacity_slots']=0
        q.care.save_portfolio(self.path,{'buyer_id':'buyer','expected_revision':1,'actor':'fixture','capabilities':[self.cap]})
        self.assertFalse(q.reopen(self.retriever,d['id'])['dependencies_current'])
        now=self.prepare();self.assertEqual(now['service_scope'][0]['status'],'blocked')
    def test_wrong_patient_and_no_nomination_cannot_queue(self):
        d=self.prepare()
        for patient in ['foreign','p2']:
            with self.assertRaises(ValueError):q.match_population_member(self.retriever,{'decision_id':d['id'],'patient_id':patient})
    def test_population_to_member_to_owned_handoff_reopens(self):
        self.stub_member_wrappers();d=self.prepare();m=q.match_population_member(self.retriever,{'decision_id':d['id'],'patient_id':'p0'})
        req={'decision_id':d['id'],'match_id':m['result_id'],'option_id':m['matching']['options'][0]['id'],'actor':'fixture'}
        task=q.queue_population_member(self.retriever,req)['task']
        self.assertTrue(q.queue_population_member(self.retriever,req)['idempotent_replay'])
        self.assertEqual(task['owner']['id'],self.cap['owner']['id'])
        q.event(self.retriever,{'decision_id':d['id'],'task_id':task['id'],'expected_revision':task['revision'],'request_id':'handoff','action':'prepare_handoff','actor':'fixture','note':'Internal review only'})
        view=q.reopen(self.retriever,d['id']);self.assertEqual(view['tasks'][0]['task']['state'],'handoff_prepared_for_review')
        self.assertEqual(view['tasks'][0]['decision_binding']['question'],self.req['question'])
        self.assertFalse(view['tasks'][0]['task']['clinical_execution_authorized'])
    def test_matching_receipt_cannot_cross_population_decisions(self):
        self.stub_member_wrappers();d=self.prepare();m=q.match_population_member(self.retriever,{'decision_id':d['id'],'patient_id':'p0'})
        self.req['question']='Different retained question';other=self.prepare()
        with self.assertRaises(ValueError):q.queue_population_member(self.retriever,{'decision_id':other['id'],'match_id':m['result_id'],'option_id':m['matching']['options'][0]['id'],'actor':'fixture'})

    def test_new_question_cannot_replace_existing_task_context_or_handoff(self):
        self.stub_member_wrappers();first=self.prepare()
        m=q.match_population_member(self.retriever,{'decision_id':first['id'],'patient_id':'p0'})
        task=q.queue_population_member(self.retriever,{'decision_id':first['id'],'match_id':m['result_id'],'option_id':m['matching']['options'][0]['id'],'actor':'fixture'})['task']
        q.event(self.retriever,{'decision_id':first['id'],'task_id':task['id'],'expected_revision':task['revision'],'request_id':'original-handoff','action':'prepare_handoff','actor':'fixture','note':'Retain this original question'})
        before=q.reopen(self.retriever,first['id'])['tasks']
        self.req['question']='A distinct question selecting the same received account';second=self.prepare()
        other=q.match_population_member(self.retriever,{'decision_id':second['id'],'patient_id':'p0'})
        with self.assertRaisesRegex(q.care.work.WorkConflict,'different saved service plan'):
            q.queue_population_member(self.retriever,{'decision_id':second['id'],'match_id':other['result_id'],'option_id':other['matching']['options'][0]['id'],'actor':'fixture'})
        self.assertEqual(q.reopen(self.retriever,first['id'])['tasks'],before)
        self.assertEqual(q.reopen(self.retriever,second['id'])['tasks'],[])
    def test_no_declared_matching_capability_retains_candidate(self):
        self.cap['need_tags']=['unrelated'];q.care.save_portfolio(self.path,{'buyer_id':'buyer','expected_revision':1,'actor':'fixture','capabilities':[self.cap]})
        d=self.prepare();self.assertEqual(d['counts']['unmapped_review_candidates'],1);self.assertEqual(d['service_scope'][0]['candidate_count'],0)
    def test_changed_member_plan_cannot_queue(self):
        self.stub_member_wrappers();d=self.prepare();m=q.match_population_member(self.retriever,{'decision_id':d['id'],'patient_id':'p0'})
        self.f.save_record({**self.f.record,'reliever_directions_confirmed':True})
        with self.assertRaises(ValueError):q.queue_population_member(self.retriever,{'decision_id':d['id'],'match_id':m['result_id'],'option_id':m['matching']['options'][0]['id'],'actor':'fixture'})

    def routing_setup(self, count=2):
        from clinical_core.agere.clinical import coverage_sources
        self.stub_member_wrappers()
        for i in range(1,count):self.f.save_record({**self.f.record,'record_id':'resp-'+str(i),'patient_id':'p'+str(i)})
        self.patch(coverage_sources,'prepare',side_effect=lambda p,r:{'id':'coverage-'+r['patient_id'],'patient_id':r['patient_id'],'reports':[],'verified_benefits':False})
        self.patch(coverage_sources,'inspect',side_effect=lambda p,k:{'id':k,'patient_id':k.removeprefix('coverage-'),'reports':[],'verified_benefits':False})
        self.patch(q.participation_work.evidence,'inspect',return_value={})
        return self.prepare()
    def routes(self,d):
        for m in d['members']:
            if m['topics']:q.route_population_member(self.retriever,{'decision_id':d['id'],'patient_id':m['patient_id']})
    def allocation(self,d,n=1):
        return q.allocate_population(self.retriever,{'decision_id':d['id'],'review_limits':{self.cap['id']:n},'assumption_source':'Explicit synthetic internal review batch limit; not service capacity'})
    def execute_allocation(self,d,a):
        return q.execute_population(self.retriever,{'decision_id':d['id'],'allocation_id':a['result_id'],'actor':'fixture internal worker'})
    def test_population_routing_accounts_waiting_and_replays_execution(self):
        d=self.routing_setup();self.routes(d);a=self.allocation(d)
        self.assertEqual(a['counts'],{'proposed_internal_review':1,'waiting_for_internal_review_capacity':1,'need_unresolved_without_nomination':4})
        self.assertEqual(len(a['members']),6);self.assertTrue(a['routing_complete'])
        done=self.execute_allocation(d,a);self.assertEqual(done['prepared_count'],1)
        self.assertTrue(self.execute_allocation(d,a)['idempotent_replay'])
        after=self.allocation(d);self.assertEqual(after['counts']['existing_internal_work'],1)
        self.assertNotIn('proposed_internal_review',after['counts']);self.assertFalse(done['human_review_completed'])
    def test_population_batch_requires_all_routes_and_explicit_limits(self):
        d=self.routing_setup();a=self.allocation(d)
        self.assertFalse(a['routing_complete'])
        with self.assertRaises(ValueError):self.execute_allocation(d,a)
        with self.assertRaises(ValueError):q.allocate_population(self.retriever,{'decision_id':d['id'],'review_limits':{},'assumption_source':'fixture'})
        with self.assertRaises(ValueError):self.allocation(d,True)
    def test_population_zero_internal_limit_keeps_every_candidate(self):
        d=self.routing_setup();self.routes(d);a=self.allocation(d,0)
        self.assertEqual(a['counts']['waiting_for_internal_review_capacity'],2)
        self.assertEqual(self.execute_allocation(d,a)['prepared_count'],0)
    def test_population_blocked_service_remains_in_denominator(self):
        self.cap['capacity_slots']=0;q.care.save_portfolio(self.path,{'buyer_id':'buyer','expected_revision':1,'actor':'fixture','capabilities':[self.cap]})
        d=self.routing_setup();self.routes(d);a=self.allocation(d,10)
        self.assertEqual(a['counts']['service_routes_blocked'],2);self.assertEqual(len(a['members']),6)
    def test_population_queue_change_rejects_stale_batch(self):
        d=self.routing_setup();self.routes(d);a=self.allocation(d)
        m=next(m for m in a['members'] if m['disposition']=='proposed_internal_review')
        q.queue_population_member(self.retriever,{'decision_id':d['id'],'match_id':m['match_id'],'option_id':m['option_id'],'actor':'other internal operator'})
        with self.assertRaises(ValueError):self.execute_allocation(d,a)
    def test_population_execution_rejects_cross_decision_and_source_change(self):
        d=self.routing_setup();self.routes(d);a=self.allocation(d)
        self.req['question']='Different population question';other=self.prepare()
        with self.assertRaises(ValueError):self.execute_allocation(other,a)
        self.f.save_record({**self.f.record,'reliever_directions_confirmed':True})
        with self.assertRaises(ValueError):self.execute_allocation(d,a)
    def test_population_other_question_work_cannot_count_as_current(self):
        d=self.routing_setup();self.routes(d);a=self.allocation(d);self.execute_allocation(d,a)
        self.req['question']='Another question needs its own review';other=self.prepare();self.routes(other)
        result=self.allocation(other)
        self.assertEqual(result['counts']['existing_work_requires_reconciliation'],1)
        self.assertNotIn('existing_internal_work',result['counts'])
    def test_population_partial_failure_keeps_completed_work(self):
        d=self.routing_setup();self.routes(d);a=self.allocation(d,2)
        original=q.queue_population_member
        def fail_second(r,req):
            match=q.saved_followup(r.path,req['match_id'])
            if match['patient_id']=='p1':raise ValueError('Injected internal failure')
            return original(r,req)
        self.patch(q,'queue_population_member',side_effect=fail_second)
        done=self.execute_allocation(d,a)
        self.assertEqual(done['status'],'partial_failure');self.assertEqual(done['prepared_count'],1)
        self.assertEqual(len(q.reopen(self.retriever,d['id'])['tasks']),1)

if __name__=='__main__':unittest.main()
