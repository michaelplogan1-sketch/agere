"""Registered-domain population selection, evidence persistence and adapter isolation."""
import json,sqlite3,unittest
from types import SimpleNamespace
from unittest.mock import patch
import test_analysis_graph as fixture
from clinical_core.agere.clinical import domain_programs as domain,domain_review as review
from clinical_core.agere.clinical import program_work as work,program_work_v1 as legacy
from clinical_core.agere.clinical import work_adapters as registry,synthesis_work
from clinical_core.agere.clinical.analysis_graph import execute,validate

class DomainPrograms(unittest.TestCase):
    def setUp(self):
        fixture.GraphExecution.setUp(self);self.addCleanup(self.temp.cleanup)
        self.record={'record_id':'resp-p0','patient_id':'p0','source_table':'condition_assessments','event_date':'2026-07-01','available_date':'2026-07-02','identity_status':'matched','synthetic':True,'condition':'asthma','reliever_directions_confirmed':False}
        self.save_record(self.record)
        self.behavioral={**self.record,'record_id':'behavior-p1','patient_id':'p1','source_table':'conditions','event_date':None,'description':'MDD, recurrent'};self.save_record(self.behavioral)
        self.program='respiratory_source_review'
    def save_record(self,r):
        with sqlite3.connect(self.root/'stack.sqlite3') as db:db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?,?)',(r['record_id'],r['patient_id'],r['source_table'],json.dumps(r)))
    def prepare(self,ids=None,**kwargs):return work.prepare(self.root,self.program,['p0','p1'] if ids is None else ids,**kwargs)
    def save(self,p):return work.materialize(self.root,p['proposal_id'],'synthetic fixture')
    def saved(self):
        self.save(self.prepare());return work.list_items(self.root,program=self.program)['items'][0]
    def request(self,item,request_id='assign'):
        return {'item_id':item['id'],'expected_revision':item['revision'],'action':'assign','actor':'synthetic fixture','request_id':request_id,'data':{'owner':'synthetic evidence queue','due_date':None}}
    def plan(self):return fixture.graph([self.base,fixture.node('resp','domain_review',['base'],{'domain':'respiratory','filter':'source_review_candidates'}),fixture.node('work','worklist',['resp'],{'program':self.program})])
    def test_complete_roster_counts_and_exact_replay(self):
        ids=['p'+str(i) for i in range(6)];p=self.prepare(ids)
        self.assertEqual(p['counts'],{'selected':6,'assessed':1,'candidates':1,'missing_assessment':5});self.assertEqual(p,self.prepare(ids))
    def test_patient_evidence_and_authority_remain_explicit(self):
        item=self.saved();a=work.inspect_item(self.root,item['id'])['assessment'];r=a['review']['domain_source_review']
        self.assertEqual({e['record_id'] for e in a['evidence']},{'resp-p0'});self.assertFalse(a['clinical_eligibility_established']);self.assertFalse(item['clinical_goal_verified'])
        self.assertIs(r['issues'][0]['evidence'][0]['value'],False);self.assertFalse(r['clinical_state_established']);self.assertEqual(a['engine_identity'],work.engine(self.program).code_identity(self.program))
    def test_behavioral_label_creates_review_not_current_screen(self):
        p=work.prepare(self.root,'behavioral_source_review',['p0','p1']);self.save(p)
        item=work.list_items(self.root,program='behavioral_source_review')['items'][0];r=work.inspect_item(self.root,item['id'])['assessment']['review']['domain_source_review']
        self.assertEqual(item['patient_id'],'p1');self.assertIsNone(r['sources'][0]['source_date']);self.assertTrue(any('patient safety' in v for v in r['unknowns']));self.assertFalse(r['clinical_execution_authorized'])
    def test_save_and_operational_assignment_are_idempotent(self):
        item=self.saved();request=self.request(item);result=work.change(self.root,request)
        self.assertEqual(result['item']['state'],'review_needed');self.assertFalse(result['item']['clinical_goal_verified']);self.assertTrue(work.change(self.root,request)['idempotent_replay'])
        self.assertEqual(self.save(self.prepare())['changes']['unchanged'],1)
    def test_optimistic_revision_and_request_id_conflicts(self):
        item=self.saved();work.change(self.root,self.request(item))
        with self.assertRaises(work.WorkConflict):work.change(self.root,self.request(item,'other'))
        bad=self.request(item);bad['data']['owner']='different'
        with self.assertRaises(work.WorkConflict):work.change(self.root,bad)
    def test_stale_source_rejects_proposal_and_event(self):
        p=self.prepare();item=self.saved();self.save_record({**self.record,'reliever_directions_confirmed':True})
        with self.assertRaises(work.WorkConflict):self.save(p)
        with self.assertRaises(work.WorkConflict):work.change(self.root,self.request(item))
    def test_refresh_requires_reassessment_and_fabricates_no_review(self):
        item=self.saved();self.save_record({**self.record,'reliever_directions_confirmed':True});self.assertEqual(self.save(self.prepare())['changes']['evidence_changed'],1)
        r=work.inspect_item(self.root,item['id']);self.assertTrue(r['item']['reassessment_required']);self.assertEqual(r['item']['state'],'review_needed');self.assertEqual([e['action'] for e in r['history']],['created','evidence_changed'])
    def test_other_patient_source_does_not_invalidate_selected_assessment(self):
        p=self.prepare(['p0']);self.save_record({**self.record,'record_id':'resp-p2','patient_id':'p2'})
        self.assertEqual(p,self.prepare(['p0']));self.assertEqual(self.save(p)['changes']['created'],1)
    def test_no_accounts_retains_unknown_need_and_empty_does_not_expand(self):
        self.saved();p=self.prepare([]);self.assertEqual(sum(self.save(p)['changes'].values()),0)
        yes,unknown,detail=domain.membership(review.prepare(self.root,'respiratory'),['p0','p1'],{'domain':'respiratory','filter':'source_review_candidates'})
        self.assertEqual(yes,{'p0'});self.assertEqual(unknown,{'p1'});self.assertFalse(detail['clinical_state_established'])
    def test_source_coverage_filters_and_scope_checks(self):
        population=review.prepare(self.root,'respiratory')
        for kind,expected in [('source_available',{'p0'}),('source_absent',{'p1'})]:
            yes,unknown,_=domain.membership(population,['p0','p1'],{'domain':'respiratory','filter':kind});self.assertEqual(yes,expected);self.assertFalse(unknown)
        with self.assertRaises(ValueError):domain.membership(population,['p0'],{'domain':'behavioral','filter':'source_available'})
        with self.assertRaises(ValueError):domain.membership(population,['unknown'],{'domain':'respiratory','filter':'source_available'})
    def test_source_bound_prepare_rejects_stale_empty_duplicate_unknown(self):
        population=review.prepare(self.root,'respiratory');req={'review_id':population['id'],'patient_ids':['p0']}
        self.assertEqual(domain.prepare_work(self.root,req)['domain_review_id'],population['id'])
        for ids in [[],['p0','p0'],['unknown']]:
            with self.assertRaises(ValueError):domain.prepare_work(self.root,{**req,'patient_ids':ids})
        self.save_record({**self.record,'reliever_directions_confirmed':True})
        with self.assertRaisesRegex(ValueError,'changed'):domain.prepare_work(self.root,req)
    def test_historical_and_functional_engine_identities_unchanged(self):
        from clinical_core.agere.clinical import program_work_current as current
        for program,engine in [('hf_review',legacy),('functional_support_review',current)]:self.assertEqual(work.prepare(self.root,program,['p0']),engine.prepare(self.root,program,['p0']))
        self.assertEqual(work.code_identity(),legacy.code_identity())
    def test_unrelated_registration_does_not_invalidate_selected_work(self):
        p=self.prepare();item=self.saved();adapter=SimpleNamespace(definition=lambda p:{'id':p,'definition_identity':'fixture-only'})
        with patch.object(registry,'REGISTRATIONS',(*registry.REGISTRATIONS,('fixture_extra',adapter))):
            self.assertEqual(p,self.prepare());self.assertEqual(self.save(p)['changes']['unchanged'],1);work.change(self.root,self.request(item))
    def test_selected_adapter_change_rejects_work(self):
        p=self.prepare();item=self.saved();original=domain.definition
        with patch.object(domain,'definition',side_effect=lambda p:{**original(p),'definition_identity':'fixture-new'}):
            with self.assertRaises(work.WorkConflict):self.save(p)
            with self.assertRaises(work.WorkConflict):work.change(self.root,self.request(item))
    def test_registry_rejects_duplicate_and_shadowing(self):
        for registrations in [(*registry.REGISTRATIONS,registry.REGISTRATIONS[0]),(('hf_review',domain),),(('functional_support_review',domain),)]:
            with patch.object(registry,'REGISTRATIONS',registrations),self.assertRaises(ValueError):registry.entries()
    def test_graph_unknowns_work_and_cache_replay(self):
        r=execute(self.stack,self.plan());pop=r['results'][1]['result'];p=r['results'][2]['result']
        self.assertEqual(pop['counts']['matched'],1);self.assertEqual(pop['counts']['unknown'],5);self.assertEqual(pop['patients_evaluated'],6);self.assertEqual(p['counts']['selected'],1)
        self.assertTrue(execute(self.stack,self.plan())['cache_hit']);self.assertEqual(work.list_items(self.root,program=self.program)['matched'],0)
    def test_graph_recomputes_after_source_change(self):
        a=execute(self.stack,self.plan());self.save_record({**self.record,'reliever_directions_confirmed':True});b=execute(self.stack,self.plan())
        self.assertNotEqual(a['result_id'],b['result_id']);self.assertFalse(b['cache_hit'])
    def test_mixed_graph_and_read_view_preserve_program_separation(self):
        self.save_record({**self.record,'record_id':'function-p0','source_table':'functional_assessments','falls_past_year_reported':1})
        plan=self.plan();plan['nodes']+=[fixture.node('behavior','domain_review',['base'],{'domain':'behavioral','filter':'source_review_candidates'}),fixture.node('both','union',['resp','behavior']),fixture.node('function','functional_review',['base'],{'filter':'source_review_candidates'}),fixture.node('fwork','worklist',['function'],{'program':'functional_support_review'}),fixture.node('oldwork','worklist',['base'],{'program':'hf_review'})]
        result=execute(self.stack,plan);self.assertEqual(result['results'][4]['result']['counts']['matched'],2)
        for row in result['results']:
            if row['node']['operation']=='worklist':self.save(row['result'])
        board=work.list_items(self.root);self.assertEqual(len({p['id'] for p in board['programs']}),len(board['programs']));self.assertEqual(work.list_items(self.root,program=self.program)['matched'],1)
    def test_graph_rejects_missing_scope_and_unsupported_filter_domain(self):
        for kind in ['scope','filter','domain']:
            plan=self.plan()
            if kind=='scope':plan['nodes'][1]['inputs']=[]
            else:plan['nodes'][1]['parameters'][kind]='unsupported'
            with self.assertRaises(ValueError):validate(plan,'2026-09-30')
    def test_new_engine_preserves_attachments_and_rejects_old_proposal(self):
        context={'schema':synthesis_work.VERSION,'patient_id':'p0','code_identity':synthesis_work.identity(),'authority':synthesis_work.AUTHORITY,'answers':[{'answer_id':'fixture-answer'}]}
        with patch.object(synthesis_work,'verify'),patch.object(synthesis_work,'contexts',return_value={'p0':context}):
            old=self.prepare(['p0']);attached=self.prepare(['p0'],synthesis_contexts={'p0':context});self.save(attached)
            refreshed=self.prepare(['p0','p1']);self.assertEqual(refreshed['synthesis_assessments'],1);self.assertEqual(self.save(refreshed)['changes']['unchanged'],1)
            with self.assertRaisesRegex(work.WorkConflict,'would be lost'):self.save(old)

class DomainPlanning(unittest.TestCase):
    def test_all_local_commands_validate_without_live_model(self):
        from clinical_core.agere.clinical import economical_planner as planner,domain_question_plans
        for row in domain_question_plans.examples():
            graph,reason=planner.local_plan(row['question'],'2026-09-30')
            self.assertEqual(graph['nodes'][1]['operation'],'domain_review')
            self.assertEqual(reason,'exact_domain_source_review')
    def test_extra_scope_clinical_decisions_and_negation_never_dropped(self):
        from clinical_core.agere.clinical.economical_planner import local_plan
        for question in ['Prepare respiratory source-review work only for Medicare','Prepare behavioral source-review work and enroll patients','Do not prepare respiratory source-review work','Review respiratory sources across the population before 2026','Prepare behavioral source-review work for p0']:
            self.assertIsNone(local_plan(question,'2026-09-30')[0],question)
    def test_frontier_contracts_describe_supported_domain_inputs(self):
        from clinical_core.agere.clinical.graph_planner import CONTRACTS,SCHEMA
        self.assertIn('domain_review',SCHEMA['properties']['nodes']['items']['properties']['operation']['enum'])
        self.assertIn('source_absent',CONTRACTS['domain_review']['parameters']['filter'])
        self.assertIn('behavioral_source_review',CONTRACTS['worklist']['parameters']['program'])

if __name__=='__main__':unittest.main()
