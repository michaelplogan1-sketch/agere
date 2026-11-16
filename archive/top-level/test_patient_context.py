"""Received patient/support accounts: source boundaries and buyer task propagation."""
import copy,json,sqlite3,unittest
from unittest.mock import patch
import test_care_capabilities as fixture
from clinical_core.agere.clinical import patient_context as p,care_capabilities as care

class PatientContext(unittest.TestCase):
    def setUp(self):
        fixture.CareCapabilities.setUp(self)
        self.records=[self.record('r1','care_support_interviews',permission_to_contact_helper=False,respondent='patient',help_available_reported='Some evenings',transport_to_medical_visits_difficult_reported=True),
                      self.record('r2','social_screens',respondent='patient',work_or_school_scheduling_constraint='Evenings only',transport='unreliable'),
                      self.record('r3','rehabilitation_access_contacts',response='decline',referral_document_record_id='specific-referral')]
        self.meta={'source_digest':'fixture','snapshot':{'as_of':'2026-10-10'}}
        patcher=patch.object(p.record_context,'received',side_effect=lambda path,patient:(copy.deepcopy(self.records),copy.deepcopy(self.meta)))
        patcher.start();self.addCleanup(patcher.stop)
        from clinical_core.agere.clinical import action_work_state
        self.addCleanup(setattr,action_work_state,'inspect',action_work_state.inspect)
        original=care.inspect_plan;self.addCleanup(setattr,care,'inspect_plan',original);p.install_plan_guard();p.install_plan_guard()
    def record(self,key,stream,**fields):
        return dict(record_id=key,patient_id='p1',source_table=stream,event_date='2026-10-01',available_date='2026-10-02',synthetic=True,identity_status='matched',_warehouse_fields=list(fields),**fields)
    def context(self,date='2026-10-10'):return p.prepare(self.path,{'patient_id':'p1','as_of':date})
    def match(self):return p.prepare_match(self.path,{**self.req,'patient_context_id':self.context()['id']})
    def queue(self,plan):return care.enqueue(self.path,{'plan_id':plan['id'],'option_id':plan['options'][0]['id'],'actor':'engineering fixture'})['task']
    def event(self,task):return {'task_id':task['id'],'expected_revision':task['revision'],'action':'prepare_handoff','actor':'engineering fixture','note':'Engineering fixture','request_id':'handoff'}
    def test_exact_null_false_and_scoped_response_never_become_consent(self):
        self.records[0]['help_available_reported']=None;c=self.context()
        first=next(r for r in c['reports'] if r['record_id']=='r1');self.assertIsNone(first['fields']['help_available_reported']);self.assertIs(first['fields']['permission_to_contact_helper'],False)
        self.assertEqual(next(r for r in c['reports'] if r['record_id']=='r3')['fields']['referral_document_record_id'],'specific-referral')
        self.assertEqual(c['current_consent'],'not_established');self.assertEqual(c['current_patient_goals'],'requires_direct_confirmation')
    def test_receipt_clock_future_and_unknown_source_dates(self):
        self.records[0]['available_date']='2026-10-09';self.records[1]['event_date']=None;self.records[2]['event_date']='2026-10-09'
        c=self.context('2026-10-05');self.assertEqual(c['later_or_undated_receipt_count'],1)
        self.assertEqual({r['temporal_scope'] for r in c['reports']},{'source_date_unknown','source_date_after_decision'})
        with self.assertRaisesRegex(ValueError,'exceeds'):self.context('2026-10-11')
    def test_unmarked_hidden_and_unselected_fields_are_excluded(self):
        self.records[0]['permission_to_contact_helper']=False;self.records[0]['preferred_modality']='virtual';self.records[0]['hidden_patient_goal']='generator truth'
        c=self.context();fields=next(r for r in c['reports'] if r['record_id']=='r1')['fields']
        self.assertNotIn('preferred_modality',fields);self.assertNotIn('hidden_patient_goal',fields)
    def test_missing_sources_remain_unknown(self):
        self.records=[];c=self.context();self.assertEqual(c['reports'],[]);self.assertEqual(c['current_service_preferences'],'requires_direct_confirmation')
        self.assertEqual(len(self.match()['options']),1)
    def test_different_values_keep_both_and_supersession_is_explicit(self):
        self.records.append(self.record('r4','care_support_interviews',permission_to_contact_helper=True,respondent='patient'))
        c=self.context();self.assertEqual(c['literal_differences'][0]['field'],'permission_to_contact_helper');self.assertEqual(len(c['reports']),4)
        self.records[-1]['supersedes_record_id']='r1';c=self.context();self.assertFalse(c['literal_differences']);self.assertEqual(next(r for r in c['reports'] if r['record_id']=='r1')['superseded_by'],['r4'])
    def test_changed_record_invalidates_context_and_old_match(self):
        plan=self.match();context=plan['patient_context']['context'];self.records[0]['help_available_reported']='No helper now'
        with self.assertRaisesRegex(care.work.WorkConflict,'reports or interpretation changed'):p.inspect(self.path,context['id'])
        with self.assertRaisesRegex(care.work.WorkConflict,'reports or interpretation changed'):care.inspect_plan(self.path,plan['id'])
    def test_patient_and_decision_date_mismatch_rejected(self):
        context=self.context()
        for request in ({**self.req,'coverage':{**self.req['coverage'],'as_of':'2026-10-09'}},self.req):
            if request is self.req:self.answer['patient_id']='another'
            with self.assertRaisesRegex(ValueError,'patient and exact decision date'):p.prepare_match(self.path,{**request,'patient_context_id':context['id']})
    def test_complete_bound_exceeded_rejects_instead_of_truncating(self):
        self.records=[self.record(str(i),'care_support_interviews',respondent='patient') for i in range(201)]
        with self.assertRaisesRegex(ValueError,'200'):self.context()
        self.records=[self.record('long','care_management_contacts',text='a'*17000)]
        with self.assertRaisesRegex(ValueError,'complete display'):self.context()
    def test_service_modality_checks_and_original_status_preserved(self):
        for modality,needed in [('virtual','digital_access'),('in_person','travel_access'),('home','home_access'),('hybrid','travel_access')]:
            self.cap['modality']=modality;self.save_request['expected_revision']=care.portfolio(self.path,'buyer1')['revision'];care.save_portfolio(self.path,self.save_request)
            plain=care.prepare(self.path,self.req);plan=self.match();checks=plan['patient_context']['option_review_checks'][plan['options'][0]['id']]
            self.assertIn(needed,{r['kind'] for r in checks});self.assertIn('helper_scope',{r['kind'] for r in checks});self.assertEqual(plan['options'][0]['status'],plain['options'][0]['status'])
            self.assertFalse(plan['patient_context']['source_binding_is_patient_confirmation'])
    def test_queue_handoff_carries_exact_context_and_fails_if_sources_change(self):
        plan=self.match();task=self.queue(plan);req=self.event(task);out=p.task_event(self.path,req)
        self.assertEqual(out['patient_context_attachment']['context'],plan['patient_context']['context']);self.assertTrue(p.task_view(self.path,task['id'])['handoff_context_current'])
        self.assertTrue(p.task_event(self.path,req)['idempotent_replay'])
        self.records.append(self.record('r4','social_screens',transport='reliable'))
        self.assertFalse(p.task_view(self.path,task['id'])['dependencies_current'])
        with self.assertRaises(care.work.WorkConflict):p.task_event(self.path,req)
    def test_packet_cannot_run_without_exact_patient_attachment(self):
        from clinical_core.agere.clinical import internal_execution,action_work_state,participation_work
        plan=self.match();task=self.queue(plan)
        # A legacy direct event path lacks the additional packet attachment.
        old=participation_work.task_event(self.path,self.event(task))['task']
        state=action_work_state.inspect(self.path,task['id']);self.assertFalse(state['packet_matches_recorded_dependencies'])
        with self.assertRaisesRegex(care.work.WorkConflict,'current evidence packet'):internal_execution.binding(self.path,task['id'])
        request={**self.event(old),'request_id':'new-packet'};p.task_event(self.path,request)
        task,state,binding=internal_execution.binding(self.path,task['id']);self.assertTrue(state['layers']['patient_context']['packet_current'])
    def test_refresh_preserves_bound_sources_and_task_when_buyer_changes(self):
        plan=self.match();task=self.queue(plan);self.cap['description']='Updated fixture service';self.save_request['expected_revision']=1;care.save_portfolio(self.path,self.save_request)
        result=p.refresh_match(self.path,{'task_id':task['id'],'expected_revision':task['revision'],'actor':'fixture'})
        self.assertEqual(result['task']['id'],task['id']);new=care.inspect_plan(self.path,result['task']['plan_id'])
        self.assertEqual(p.association(self.path,new)['context'],plan['patient_context']['context'])
        self.records[0]['permission_to_contact_helper']=True
        with self.assertRaises(care.work.WorkConflict):p.refresh_match(self.path,{'task_id':task['id'],'expected_revision':result['task']['revision'],'actor':'fixture'})
    def test_legacy_unbound_matching_remains_identical(self):
        from clinical_core.agere.clinical import participation_work
        self.assertEqual(p.prepare_match(self.path,self.req),participation_work.prepare(self.path,self.req))
        plan=p.prepare_match(self.path,self.req);task=self.queue(plan);self.assertIsNone(p.task_view(self.path,task['id'])['patient_context'])
    def test_received_coverage_binding_and_patient_context_compose(self):
        from clinical_core.agere.clinical import coverage_sources,coverage_work
        self.records.append(self.record('coverage','coverage_verifications',coverage_type_reported='original_medicare',plan_id='plan1',status='active_returned',service_reference_date='2026-10-10'))
        c=coverage_sources.prepare(self.path,{'patient_id':'p1','as_of':'2026-10-10'});selected=coverage_sources.select(c,'coverage')
        request={**self.req,'coverage':{**self.req['coverage'],**selected['routing_fields']},'coverage_binding':{'context_id':c['id'],'record_id':'coverage'},'patient_context_id':self.context()['id']}
        plan=p.prepare_match(self.path,request);self.assertEqual(plan['received_coverage']['coverage_selection'],selected)
        task=self.queue(plan);p.task_event(self.path,self.event(task));self.assertTrue(coverage_work.task_view(self.path,task['id'])['handoff_coverage_current']);self.assertTrue(p.task_view(self.path,task['id'])['handoff_context_current'])
    def test_failed_patient_attachment_leaves_packet_explicitly_incomplete(self):
        from clinical_core.agere.clinical import participation_work,action_work_state
        plan=self.match();task=self.queue(plan);req=self.event(task);real=participation_work.task_event
        def fail_after_core(path,request):
            real(path,request);raise RuntimeError('Injected sidecar failure')
        with patch.object(participation_work,'task_event',side_effect=fail_after_core):
            with self.assertRaises(RuntimeError):p.task_event(self.path,req)
        self.assertFalse(action_work_state.inspect(self.path,task['id'])['packet_matches_recorded_dependencies'])
        self.assertTrue(p.task_event(self.path,req)['idempotent_replay']);self.assertTrue(p.task_view(self.path,task['id'])['handoff_context_current'])
    def test_marker_cannot_impersonate_source_binding_and_missing_receipt_fails(self):
        req={**self.req,'coverage':{**self.req['coverage'],'preferences':p.MARKER+'x'}}
        with self.assertRaisesRegex(ValueError,'reserved'):p.prepare_match(self.path,req)
        core=care.prepare(self.path,req)
        with self.assertRaisesRegex(care.work.WorkConflict,'missing'):care.inspect_plan(self.path,core['id'])
    def test_receipts_immutable_and_exact_match_replay(self):
        self.assertEqual(self.match(),self.match());plan=self.match();task=self.queue(plan);p.task_event(self.path,self.event(task));db=p.database(self.path)
        try:
            for table in ('contexts','matches','handoffs'):
                with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM '+table)
        finally:db.close()

if __name__=='__main__':unittest.main()
