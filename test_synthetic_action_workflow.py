"""Independent fixtures for source assessment -> mockup -> simulated agents."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import test_answer_action_center as fixtures
USER_QUESTION = fixtures.USER_QUESTION
from clinical_core.agere.deliverables import answer_context as A, synthetic_actions as S, mock_views as M
from clinical_core.agere.clinical import therapy_review as T

class SyntheticWorkflow(unittest.TestCase):
    setUp = fixtures.Workflow.setUp
    req = fixtures.Workflow.req

    def step(self, item, scenario='happy_path'):
        return S.advance(self.path, {'context_id':self.c['id'],'item_id':item['id'],'revision':item['revision'],'scenario':scenario})

    def test_pharmd_and_rpm_progress_persist_without_changing_review_or_evidence(self):
        before = A.load(self.path,self.c['id'])
        for program in ('pharmd','rpm'):
            A.create_work(self.path,self.req(program=program,patient_ids=['A']))
            item = next(i for i in A.inspect(self.path,self.c['id'])['items'] if i['program']==program)
            first = item
            for _ in S.STEPS[program]:
                item = next(i for i in self.step(item)['items'] if i['program']==program)
            self.assertEqual(item['simulation']['status'],'complete')
            self.assertEqual(item['state'],'needs_review')
            self.assertTrue(all(r['simulated'] and not r['external_dispatch_performed'] for r in item['simulation']['receipts']))
            self.assertEqual(len(item['simulation']['receipts']),7)
            with self.assertRaisesRegex(ValueError,'changed'):self.step(first)
        self.assertEqual(before,A.load(self.path,self.c['id']))
        db=A._db(self.path,before)
        self.assertEqual(db.execute('SELECT count(*) FROM work_requests').fetchone()[0],14)
        db.close()

    def test_exceptions_stop_and_preserve_followup(self):
        for pid,scenario,steps in [('A','records_needed',1),('B','patient_declines',3)]:
            A.create_work(self.path,self.req(patient_ids=[pid]))
            item=next(i for i in A.inspect(self.path,self.c['id'])['items'] if i['patient_id']==pid)
            for _ in range(steps):item=next(i for i in self.step(item,scenario)['items'] if i['patient_id']==pid)
            self.assertEqual(item['simulation']['status'],'needs_attention')
            self.assertTrue(item['simulation']['follow_up'])
            again=next(i for i in self.step(item,scenario)['items'] if i['patient_id']==pid)
            self.assertEqual(item,again)
            with self.assertRaisesRegex(ValueError,'keeps its scenario'):self.step(item,'happy_path')

    def test_mockup_is_saved_binds_original_cohort_and_escapes_script_data(self):
        self.result['answer']['table'][0]['pcp']='</script><script>alert(1)</script>'
        self.c=A.capture(self.path,self.question,self.result)
        A.create_work(self.path,self.req(patient_ids=['A']))
        d=M.create(self.path,{'context_id':self.c['id'],'template':'command','request':'My command center'})
        self.assertTrue(d['mockup']);self.assertEqual(d['new_paid_api_calls'],0)
        self.assertNotIn('</script><script>alert(1)',d['html'])
        import importlib
        C=importlib.import_module('clinical_core.agere.deliverables.compose')
        spec,b,body,html=C.load(A.workspace(self.path,A.load(self.path,self.c['id'])),d['id'])
        self.assertEqual(html,d['html']);self.assertEqual(spec['costs']['total_usd'],0)
        self.assertIn('Synthetic',html.replace('SYNTHETIC','Synthetic'))
        d2=M.create(self.path,{'context_id':self.c['id'],'template':'command','request':'My command center'})
        self.assertEqual(d['id'],d2['id'])

    def test_stale_and_cancelled_work_cannot_advance(self):
        i=A.create_work(self.path,self.req(patient_ids=['A']))['items'][0]
        A.update_work(self.path,{'context_id':self.c['id'],'item_id':i['id'],'revision':1,'state':'cancelled','owner':'Team','note':''})
        with self.assertRaisesRegex(ValueError,'active work'):self.step(A.inspect(self.path,self.c['id'])['items'][0])
        with sqlite3.connect(self.path/'semantic.sqlite3') as db:db.execute("UPDATE meta SET value='changed' WHERE key='source_digest'")
        with self.assertRaisesRegex(ValueError,'data changed'):self.step(i)
        with self.assertRaisesRegex(ValueError,'data changed'):M.create(self.path,{'context_id':self.c['id'],'template':'brief','request':''})

class TherapyAssessment(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)
        self.db=sqlite3.connect(self.path/'semantic.sqlite3');self.addCleanup(self.db.close)
        self.db.executescript("""
        CREATE TABLE meta(key,value); INSERT INTO meta VALUES('as_of','2030-05-27');
        CREATE TABLE patient(patient_id,pcp_clinician_id);
        CREATE TABLE condition(record_id,patient_id,concept,event_date,icd10);
        CREATE TABLE note_fact(patient_id,record_id,fact_type,value,unit,negated,experiencer,temporality,event_date);
        CREATE TABLE record(record_id,patient_id,stream,payload);
        CREATE TABLE clinician(clinician_id,specialty);
        CREATE TABLE encounter(patient_id,record_id,attending_clinician_id,event_date,status);
        CREATE TABLE medication_list_entry(patient_id,drug_key,name,list_status,last_listed,last_order_date,last_fill_date,record_id);
        INSERT INTO patient VALUES('A','PCP-A'),('B',NULL),('C',NULL),('D',NULL),('E',NULL);
        INSERT INTO clinician VALUES('CARD-A','Cardiology');
        INSERT INTO encounter VALUES('A','VISIT-A','CARD-A','2030-05-01','completed');
        INSERT INTO condition VALUES('DX-A','A','heart_failure','2030-01-01','I50'),('DX-B','B','heart_failure','2030-01-01','I50'),('DX-C','C','heart_failure','2030-01-01','I50'),('DX-D','D','heart_failure_reduced_ef','2030-01-01','I502'),('DX-E','E','heart_failure','2030-01-01','I50');
        INSERT INTO note_fact VALUES('A','EF-A','ejection_fraction',30,'%',0,'patient','current','2030-05-01'),('B','EF-B','ejection_fraction',60,'%',0,'patient','current','2030-05-01'),('C','EF-C','ejection_fraction',20,'%',1,'patient','current','2030-05-01'),('E','EF-E1','ejection_fraction',20,'%',0,'patient','current','2030-05-01'),('E','EF-E2','ejection_fraction',60,'%',0,'patient','current','2030-05-01');
        INSERT INTO medication_list_entry VALUES('A','carvedilol','Carvedilol','active','2030-05-01',NULL,NULL,'MED-A');
        """)
        self.db.execute('INSERT INTO record VALUES(?,?,?,?)',('MED-A','A','medication_lists',json.dumps({'dose':'6.25 mg','frequency':'BID','event_date':'2030-05-01'})));self.db.commit()

    def test_compound_question_keeps_every_requested_field_and_unknowns(self):
        self.assertTrue(T.accepts(USER_QUESTION))
        r=T.answer(self.path,USER_QUESTION)
        rows={x['patient_id']:x for x in r['answer']['table']}
        self.assertEqual(set(rows),{'A','D'})
        self.assertEqual(rows['A']['pcp'],'PCP-A');self.assertEqual(rows['A']['cardiologist'],'CARD-A')
        self.assertIn('6.25 mg',rows['A']['recorded_doses'])
        self.assertIn('Unknown',rows['A']['maximum_tolerated_dose'])
        self.assertEqual(rows['D']['ef_percent'],'Unknown')
        self.assertEqual(set(r['answer']['overall']['ef_unresolved_patient_ids']),{'C','E'})
        self.assertEqual(r['answer']['overall']['latest_ef_above_40_patient_ids'],['B'])
        self.assertEqual(r['new_paid_api_calls'],0)

    def test_structured_ef_adapter_supersedes_older_note(self):
        raw={'patient_id':'A','record_id':'ECHO-A','source_table':'echo','synthetic':True,'identity_status':'matched',
             'available_date':'2030-05-20','event_date':'2030-05-19','observation_date':'2030-05-19',
             'ejection_fraction':60,'unit':'%','status':'final'}
        self.db.execute('INSERT INTO record VALUES(?,?,?,?)',('ECHO-A','A','echo',json.dumps(raw)));self.db.commit()
        r=T.answer(self.path,USER_QUESTION)
        self.assertNotIn('A',r['answer']['patient_ids'])
        self.assertIn('A',r['answer']['overall']['latest_ef_above_40_patient_ids'])

    def test_extra_constraints_decline_instead_of_disappearing(self):
        for suffix in [' who have diabetes',' for Medicare patients',' younger than 50',' in 2029',' without CKD',' exclude patients on hospice']:
            self.assertFalse(T.accepts(USER_QUESTION+suffix))
        self.assertFalse(T.accepts('What is allowed PMPM by payer?'))

if __name__=='__main__':unittest.main()
