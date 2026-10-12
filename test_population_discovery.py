"""Source-process behavior, independent objective, and durable question/work joins."""
import copy
import itertools
import json
import math
import sqlite3
import unittest
from unittest.mock import patch
from clinical_core.agere.clinical import population_discovery as S, question_decision as q
import test_population_decision as D


class ProcessArithmetic(unittest.TestCase):
    def record(self, rid, arrival, event='2026-01-01', text='same'):
        return {'record_id':rid,'patient_id':'p0','source_table':'notes','available_date':arrival,'event_date':event,'text':text}
    def test_exact_text_clocks_and_all_members(self):
        rows=[self.record('a','2026-01-02'),self.record('b','2026-03-02'),
            self.record('c',None),self.record('d','2025-12-01'),self.record('e','2026-04-01'),
            self.record('f','2026-02-03',None,'different'),self.record('g','2026-02-04','2026-03-01','future-source')]
        r=S.analyze(rows,['p0','p1'],'2026-03-15',3)
        self.assertEqual(r['arrival_comparison']['counts'],[1,2,1]);self.assertEqual(r['repeat_sensitivity']['counts'],[1,2,0])
        self.assertEqual(r['denominators']['excluded_records_by_reason'],{'unknown_arrival_date':1,'before_window':1,'after_cutoff':1})
        self.assertEqual(r['denominators']['members_without_included_records'],1)
        self.assertEqual(r['source_date_sensitivity']['clock_counts']['source_after_arrival'],1)
        self.assertEqual(r['members'][0]['records'][1]['repeat_of'],'a')
        self.assertEqual(r['calendar_days'],[31,28,15]);self.assertEqual(r['source_date_sensitivity']['counts'],[2,0,1])
    def test_documents_never_use_original_text_and_no_text_not_collapsed(self):
        rows=[{**self.record(str(i),'2026-01-02'),'source_table':'documents','original_text':'hidden','text':'hidden too'} for i in range(2)]
        r=S.analyze(rows,['p0'],'2026-03-15',3)
        self.assertEqual(r['repeat_sensitivity']['counts'],[2,0,0]);self.assertEqual(r['repeat_sensitivity']['records_without_received_text'],2)
    def test_patient_scoped_copy_sensitivity_and_zero_series(self):
        rows=[self.record('a','2026-01-02'),{**self.record('b','2026-01-02'),'patient_id':'p1'}]
        self.assertEqual(S.analyze(rows,['p0','p1'],'2026-03-15',3)['repeat_sensitivity']['counts'],[2,0,0])
        r=S.analyze([],[],'2026-03-15',3);self.assertEqual(r['arrival_comparison']['baseline']['records_per_day'],0)
        self.assertEqual(r['arrival_comparison']['penalized_objective_gain'],0)
    def test_existing_operator_against_exhaustive_partition_baseline(self):
        counts=[0,1,0,80,110,95];days=[31,28,31,30,31,15]
        scores=[]
        for choices in itertools.product((0,1),repeat=5):
            cuts=[0]+[i+1 for i,b in enumerate(choices) if b]+[6];score=0
            for a,b in zip(cuts,cuts[1:]):
                n=sum(counts[a:b]);t=sum(days[a:b]);score+=(n*math.log(n/t) if n else 0)-6
            scores.append(score)
        r=S.compare(counts,days);self.assertAlmostEqual(r['candidate']['objective'],max(scores))
        self.assertAlmostEqual(r['baseline']['objective'],scores[0]);self.assertGreater(len(r['candidate']['blocks']),1)


class QuestionDiscovery(unittest.TestCase):
    prepare=D.PopulationDecision.prepare
    patch=D.PopulationDecision.patch
    stub_member_wrappers=D.PopulationDecision.stub_member_wrappers
    def setUp(self):
        D.PopulationDecision.setUp(self)
        with sqlite3.connect(self.path/'stack.sqlite3') as db:
            for key, raw in list(db.execute('SELECT id,payload FROM records')):
                r=json.loads(raw);r.update(synthetic=True,identity_status='matched')
                db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(r),key))
    def request(self,d):return {'decision_id':d['id'],'stream':self.f.record['source_table'],'months':12}
    def open(self,r,pid='p0'):return S.reopen(self.retriever,{'decision_id':r['decision_id'],'result_id':r['result_id'],'patient_id':pid})
    def test_real_store_replay_and_member_inspection(self):
        d=self.prepare();req=self.request(d);r=S.run(self.retriever,req)
        self.assertEqual(r,S.run(self.retriever,req));self.assertEqual(r['selection'],d['selection']);self.assertEqual(len(r['members']),6)
        self.assertTrue(self.open(r)['current']);self.assertTrue(self.open(r)['member_evidence']['records'])
        self.assertFalse(r['clinical_change_established']);self.assertIsNone(r['search_scope']['p_value'])
        self.assertIsNone(r['cost']['total_cost_usd']);self.assertTrue(S.options(self.retriever,{'decision_id':d['id']})['streams'])
    def test_scope_input_and_cross_decision_rejection(self):
        d=self.prepare();req=self.request(d)
        for delta in ({'stream':'unknown'},{'months':True},{'months':25},{'months':1},{'as_of':'2030-01-01'}):
            with self.assertRaises(ValueError):S.run(self.retriever,{**req,**delta})
        r=S.run(self.retriever,req)
        with self.assertRaises(ValueError):self.open(r,'outside')
        with self.assertRaises(ValueError):S.reopen(self.retriever,{'decision_id':'other','result_id':r['result_id'],'patient_id':''})
    def test_changed_source_and_saved_integrity_rejected(self):
        d=self.prepare();r=S.run(self.retriever,self.request(d))
        self.f.save_record({**self.f.record,'available_date':'2026-08-01'})
        self.assertFalse(self.open(r)['current']);self.assertIsNone(self.open(r)['member_evidence'])
        self.assertEqual(r,q.saved_followup(self.path,r['result_id']))
        with q.database(self.path) as db:
            tampered=copy.deepcopy(r);tampered['clinical_change_established']=True
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('UPDATE question_decision_followups SET payload=? WHERE id=?',(json.dumps(tampered),r['result_id']))
        self.assertEqual(r,q.saved_followup(self.path,r['result_id']))
    def test_work_revision_changes_only_new_result(self):
        d=self.prepare();self.stub_member_wrappers();m=q.match_population_member(self.retriever,{'decision_id':d['id'],'patient_id':'p0'})
        task=q.queue_population_member(self.retriever,{'decision_id':d['id'],'match_id':m['result_id'],'option_id':m['matching']['options'][0]['id'],'actor':'fixture'})['task']
        r=S.run(self.retriever,self.request(d));self.assertEqual(r['members'][0]['task_ids'],[task['id']])
        q.event(self.retriever,{'decision_id':d['id'],'task_id':task['id'],'expected_revision':task['revision'],'request_id':'handoff','action':'prepare_handoff','actor':'fixture','note':'Internal only'})
        self.assertFalse(self.open(r)['current']);new=S.run(self.retriever,self.request(d));self.assertNotEqual(new['result_id'],r['result_id'])
    def test_mid_assembly_change_does_not_save(self):
        d=self.prepare();real=S.inputs;calls=0
        def changed(*args):
            nonlocal calls
            values=real(*args);calls+=1
            if calls==2:values[-1]['records_digest']='changed'
            return values
        with patch.object(S,'inputs',side_effect=changed):
            with self.assertRaises(ValueError):S.run(self.retriever,self.request(d))
        with q.database(self.path) as db:self.assertEqual(db.execute('SELECT count(*) FROM question_decision_followups').fetchone()[0],0)
    def test_complete_empty_population_and_bound_failure(self):
        self.req['cohort']={'all':[{'kind':'patient','field':'patient_id','op':'eq','value':'not-observed'}],'any':[],'exclude':[]}
        d=self.prepare();r=S.run(self.retriever,self.request(d));self.assertEqual(r['members'],[]);self.assertTrue(self.open(r,'')['current'])
        self.req['cohort']={'all':[],'any':[],'exclude':[]};d=self.prepare()
        with patch.object(S,'MAX_RECORDS',0):
            with self.assertRaisesRegex(ValueError,'nothing was sampled'):S.run(self.retriever,self.request(d))


if __name__=='__main__':unittest.main()
