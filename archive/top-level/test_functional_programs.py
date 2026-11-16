"""Functional source nomination -> composed population -> durable review work."""
import copy,json,sqlite3,unittest
from unittest.mock import patch
from clinical_core.agere.clinical import functional_programs as f, functional_review as review
from clinical_core.agere.clinical import program_work as work, program_work_v1 as legacy
from clinical_core.agere.clinical.analysis_graph import execute,validate
from test_analysis_graph import GraphExecution,node,graph

class FunctionalPrograms(unittest.TestCase):
    def setUp(self):
        GraphExecution.setUp(self)
        self.addCleanup(self.temp.cleanup)
        self.record={'record_id':'function-p0','patient_id':'p0','source_table':'functional_assessments',
            'event_date':'2026-07-01','available_date':'2026-07-02','identity_status':'matched','synthetic':True,
            'falls_past_year_reported':1,'help_with_appointments_or_medications_reported':True}
        self.save_record(self.record)
    def save_record(self,r):
        with sqlite3.connect(self.root/'stack.sqlite3') as db:
            db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?,?)',(r['record_id'],r['patient_id'],r['source_table'],json.dumps(r)))
    def prepare(self,ids=None):return work.prepare(self.root,f.PROGRAM,['p0','p1'] if ids is None else ids)
    def saved(self):
        p=self.prepare();work.materialize(self.root,p['proposal_id'],'synthetic test runner')
        return work.list_items(self.root,program=f.PROGRAM)['items'][0]
    def test_complete_counts_missing_sources_and_exact_replay(self):
        p=self.prepare(['p'+str(i) for i in range(6)])
        self.assertEqual(p['counts'],{'selected':6,'assessed':1,'candidates':1,'missing_assessment':5})
        self.assertEqual(p,self.prepare(['p'+str(i) for i in range(6)]))
        self.assertEqual(p['patients'][0]['patient_id'],'p0')
    def test_literal_source_provenance_and_authority(self):
        item=self.saved();a=work.inspect_item(self.root,item['id'])['assessment']
        self.assertEqual({e['record_id'] for e in a['evidence']},{'function-p0'})
        self.assertFalse(a['clinical_eligibility_established']);self.assertFalse(item['clinical_goal_verified'])
        self.assertEqual(a['review']['functional_source_review']['home_health_eligibility'],'unresolved')
    def test_save_is_idempotent_and_assignment_is_only_operational(self):
        item=self.saved();req={'item_id':item['id'],'expected_revision':1,'action':'assign','actor':'synthetic runner','request_id':'assign-once','data':{'owner':'synthetic review queue','due_date':None}}
        updated=work.change(self.root,req);self.assertFalse(updated['item']['clinical_goal_verified'])
        self.assertTrue(work.change(self.root,req)['idempotent_replay'])
        p=self.prepare();r=work.materialize(self.root,p['proposal_id'],'synthetic runner')
        self.assertEqual(r['changes']['unchanged'],1)
        self.assertEqual(work.inspect_item(self.root,item['id'])['item']['revision'],2)
    def test_changed_source_blocks_old_proposal_and_events(self):
        p=self.prepare();item=self.saved();self.save_record({**self.record,'falls_past_year_reported':2})
        with self.assertRaises(work.WorkConflict):work.materialize(self.root,p['proposal_id'],'synthetic runner')
        with self.assertRaises(work.WorkConflict):work.change(self.root,{'item_id':item['id'],'expected_revision':1,'action':'assign','actor':'synthetic runner','request_id':'stale','data':{'owner':'queue','due_date':None}})
    def test_refresh_marks_changed_evidence_without_fabricating_review(self):
        item=self.saved();self.save_record({**self.record,'falls_past_year_reported':0,'help_with_appointments_or_medications_reported':False})
        p=self.prepare();r=work.materialize(self.root,p['proposal_id'],'synthetic runner')
        self.assertEqual(r['changes']['evidence_changed'],1)
        d=work.inspect_item(self.root,item['id']);self.assertTrue(d['item']['reassessment_required']);self.assertEqual(d['item']['state'],'review_needed')
        self.assertEqual([e['action'] for e in d['history']],['created','evidence_changed'])
    def test_no_signal_is_not_no_need_and_empty_scope_does_not_expand(self):
        self.saved();p=self.prepare([]);r=work.materialize(self.root,p['proposal_id'],'synthetic runner')
        self.assertEqual(sum(r['changes'].values()),0)
        self.assertEqual(work.list_items(self.root,program=f.PROGRAM)['matched'],1)
        population=review.prepare(self.root);yes,unknown,_=f.membership(population,['p0','p1'],{'filter':'source_review_candidates'})
        self.assertEqual(yes,{'p0'});self.assertEqual(unknown,{'p1'})
    def test_explicit_received_absent_source_filters_are_not_clinical(self):
        population=review.prepare(self.root)
        for kind,expected in [('source_available',{'p0'}),('source_absent',{'p1'})]:
            yes,unknown,result=f.membership(population,['p0','p1'],{'filter':kind})
            self.assertEqual(yes,expected);self.assertFalse(unknown);self.assertFalse(result['clinical_state_established'])
    def test_source_bound_request_rejects_stale_cross_scope_and_empty(self):
        pop=review.prepare(self.root);request={'review_id':pop['id'],'patient_ids':['p0']}
        result=f.prepare_work(self.root,request);self.assertEqual(result['functional_review_id'],pop['id'])
        for ids in [[],['unknown'],['p0','p0']]:
            with self.assertRaises(ValueError):f.prepare_work(self.root,{**request,'patient_ids':ids})
        self.save_record({**self.record,'falls_past_year_reported':2})
        with self.assertRaisesRegex(ValueError,'changed'):f.prepare_work(self.root,request)
    def test_historical_work_identity_and_definitions_preserved(self):
        p=legacy.prepare(self.root,'hf_review',['p0']);self.assertEqual(p,work.prepare(self.root,'hf_review',['p0']))
        self.assertEqual(work.code_identity(),legacy.code_identity())
        self.assertEqual(work.definition('hf_review'),legacy.definition('hf_review'))
        with patch.object(f,'identity',return_value='new-functional-version'):
            self.assertEqual(p,work.prepare(self.root,'hf_review',['p0']))
            work.materialize(self.root,p['proposal_id'],'synthetic runner')
    def plan(self):return graph([self.base,node('function','functional_review',['base'],{'filter':'source_review_candidates'}),node('work','worklist',['function'],{'program':f.PROGRAM})])
    def test_graph_complete_population_unknowns_work_and_replay(self):
        r=execute(self.stack,self.plan());pop=r['results'][1]['result'];p=r['results'][2]['result']
        self.assertEqual(p['counts']['selected'],1);self.assertEqual(p['counts']['candidates'],1)
        self.assertEqual(pop['patients_evaluated'],6);self.assertEqual(pop['counts']['matched'],1);self.assertEqual(pop['counts']['unknown'],5)
        self.assertEqual(pop['functional_review_id'],review.prepare(self.root)['id'])
        self.assertTrue(execute(self.stack,self.plan())['cache_hit'])
        self.assertEqual(work.list_items(self.root,program=f.PROGRAM)['matched'],0)
    def test_graph_recomputes_when_functional_source_changes(self):
        a=execute(self.stack,self.plan());self.save_record({**self.record,'falls_past_year_reported':0,'help_with_appointments_or_medications_reported':False})
        b=execute(self.stack,self.plan());self.assertNotEqual(a['result_id'],b['result_id']);self.assertFalse(b['cache_hit'])
        self.assertEqual(b['results'][2]['result']['counts']['selected'],0)
    def test_graph_contract_rejects_unscoped_or_unknown_filters(self):
        for mutate in ['scope','filter']:
            p=self.plan()
            if mutate=='scope':p['nodes'][1]['inputs']=[]
            else:p['nodes'][1]['parameters']['filter']='clinical_eligible'
            with self.assertRaises(ValueError):validate(p,'2026-09-30')

if __name__=='__main__':unittest.main()
