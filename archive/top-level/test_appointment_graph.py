"""Integration and temporal/source invariants; no training or held-out labels."""
import copy
import json
import sqlite3
import unittest
from clinical_core.agere.clinical import appointment_graph as A, analysis_graph as G, graph_contracts as C
from clinical_core.agere.clinical import program_work
import test_analysis_graph as fixture
node, graph = fixture.node, fixture.graph
from test_appointment_followthrough import pair, source

class AppointmentGraph(unittest.TestCase):
    def setUp(self):
        fixture.GraphExecution.setUp(self)
        records=[]
        for i,status in enumerate(('no_show','fulfilled','cancelled')):
            records += [{**r,'patient_id':'p'+str(i),'record_id':str(i)+r['record_id']} for r in pair(status)]
        records += [{**source('3booking'),'patient_id':'p3'}]
        records += [{**r,'patient_id':'p4','record_id':'4'+r['record_id']} for r in pair()]
        records += [{**pair('no_show')[1],'patient_id':'p4','record_id':'4conflict'}]
        with sqlite3.connect(self.root/'stack.sqlite3') as db:
            for r in records:db.execute('INSERT INTO records VALUES (?,?,?,?)',(r['record_id'],r['patient_id'],'appointments',json.dumps(r)))
        self.params={'available_by':'2026-09-30','filter':'all'}
        self.appt=node('appointments','appointment_followthrough',['base'],self.params)
        self.question='Which received appointments remain unresolved, and what financial evidence is documented?'

    def tearDown(self):
        fixture.GraphExecution.tearDown(self)

    def test_full_denominator_and_exact_documentary_states(self):
        result=G.execute(self.stack,graph([self.base,self.appt]),question=self.question)
        detail=result['results'][1]['result']
        self.assertEqual(detail['counts'],{'evaluated':6,'matched':6,'not_matched':0,'unknown':0})
        self.assertEqual(detail['status_counts'],{'explicit_no_show':1,'explicit_fulfilled':1,'explicit_cancelled':1,'no_explicit_terminal_report':1,'unresolved_followthrough':1,'no_received_booking':1})
        self.assertEqual(result['question'],self.question)
        row=A.inspect(self.root,detail['appointment_graph_id'])
        self.assertEqual(row['patients'][0]['terminal_record_ids'],['0terminal'])
        self.assertFalse(row['patients'][4]['clinical_outcome_established'])
        self.assertEqual(detail['source_count'],10)

    def test_status_membership_composes_without_absence_as_negative(self):
        self.appt['parameters']['filter']='explicit_no_show'
        result=G.execute(self.stack,graph([self.base,self.appt,self.text,node('joined','intersect',['appointments','hardship'])]))
        detail=result['results'][1]['result']
        self.assertEqual(detail['selected_patient_ids'],['p0'])
        self.assertEqual(detail['unknown_patient_ids'],['p3','p4','p5'])
        self.assertEqual(result['results'][3]['result']['counts']['matched'],1)
        self.appt['parameters']['filter']='unresolved'
        detail=G.execute(self.stack,graph([self.base,self.appt]))['results'][1]['result']
        self.assertEqual(detail['selected_patient_ids'],['p3','p4','p5'])

    def test_historical_cutoff_excludes_future_reports_and_keeps_maturity(self):
        self.appt['parameters']['available_by']='2026-01-25'
        result=G.execute(self.stack,graph([self.base,self.appt]),question=self.question)
        d=result['results'][1]['result'];self.assertIsNone(d['appointment_review_id'])
        row=A.inspect(self.root,d['appointment_graph_id'])
        self.assertEqual(row['patients'][0]['status'],'incomplete_followup')
        self.assertEqual([s['record_id'] for s in row['patients'][0]['sources']],['0booking'])
        with self.assertRaisesRegex(ValueError,'Historical appointment'):
            A.prepare_work(self.stack,{'result_id':result['result_id'],'node_id':'appointments','patient_id':'p0'})

    def test_historical_work_guard_traverses_set_edges_before_execution(self):
        self.appt['parameters']['available_by']='2026-01-25'
        plan=graph([self.base,self.appt,self.text,node('joined','intersect',['appointments','hardship']),
                    node('work','worklist',['joined'],{'program':'appointment_followthrough_source_review'})])
        with self.assertRaisesRegex(ValueError,'Historical appointment'):G.validate(plan,'2026-09-30')
        self.assertFalse((self.root/'program-work.sqlite3').exists())

    def test_cache_tracks_source_updates_deletions_and_exact_question(self):
        plan=graph([self.base,self.appt]);first=G.execute(self.stack,plan,question=self.question)
        self.assertTrue(G.execute(self.stack,plan,question=self.question)['cache_hit'])
        second=G.execute(self.stack,plan,question='Different source question')
        self.assertNotEqual(first['result_id'],second['result_id'])
        with sqlite3.connect(self.root/'stack.sqlite3') as db:
            raw=json.loads(db.execute("SELECT payload FROM records WHERE id='0terminal'").fetchone()[0]);raw['status']='fulfilled'
            db.execute("UPDATE records SET payload=? WHERE id='0terminal'",(json.dumps(raw),))
        changed=G.execute(self.stack,plan,question=self.question)
        self.assertFalse(changed['cache_hit']);self.assertEqual(changed['results'][1]['result']['status_counts']['explicit_fulfilled'],2)
        with self.assertRaisesRegex(ValueError,'changed'):A.inspect(self.root,first['results'][1]['result']['appointment_graph_id'])
        with self.assertRaisesRegex(ValueError,'Graph dependencies changed'):
            A.prepare_work(self.stack,{'result_id':first['result_id'],'node_id':'appointments','patient_id':'p0'})
        with sqlite3.connect(self.root/'stack.sqlite3') as db:db.execute("DELETE FROM records WHERE id='0terminal'")
        deleted=G.execute(self.stack,plan,question=self.question)
        self.assertFalse(deleted['cache_hit']);self.assertEqual(deleted['results'][1]['result']['status_counts']['no_explicit_terminal_report'],2)

    def test_question_linked_work_reuses_existing_adapter_and_replay(self):
        result=G.execute(self.stack,graph([self.base,self.appt]),question=self.question)
        request={'result_id':result['result_id'],'node_id':'appointments','patient_id':'p0'}
        proposal=A.prepare_work(self.stack,request)
        self.assertEqual(proposal['input_membership']['question_context']['original_question'],self.question)
        self.assertEqual(proposal['input_membership']['question_context']['graph_result_id'],result['result_id'])
        saved=program_work.materialize(self.root,proposal['proposal_id'],'local fixture verification')
        replay=program_work.materialize(self.root,proposal['proposal_id'],'local fixture verification')
        self.assertEqual(saved['changes']['created'],1);self.assertEqual(replay['changes']['unchanged'],1)
        self.assertEqual(program_work.list_items(self.root,program='appointment_followthrough_source_review')['matched'],1)
        for update in ({'patient_id':'absent'},{'node_id':'base'}):
            with self.assertRaises(ValueError):A.prepare_work(self.stack,{**request,**update})

    def test_worklist_keeps_original_question_and_source_receipt(self):
        self.appt['parameters']['filter']='explicit_no_show'
        result=G.execute(self.stack,graph([self.base,self.appt,node('work','worklist',['appointments'],{'program':'appointment_followthrough_source_review'})]),question=self.question)
        work=result['results'][-1]['result']
        self.assertEqual(work['counts']['selected'],1)
        self.assertEqual(work['input_membership']['question_context']['original_question'],self.question)
        self.assertEqual(work['input_membership']['question_context']['appointment_observations'][0]['selected_patient_ids'],['p0'])

    def test_invalid_cutoff_filter_and_patient_rejected(self):
        for p in ({'available_by':'snapshot as_of','filter':'all'},{'available_by':'2027-01-01','filter':'all'}, {'available_by':'2026-09-30','filter':'missing_care'}):
            with self.assertRaises(ValueError):G.validate(graph([self.base,{**self.appt,'parameters':p}]),'2026-09-30')
        with self.assertRaises(ValueError):A.build(self.root,['absent'],self.params)

if __name__=='__main__':unittest.main()
