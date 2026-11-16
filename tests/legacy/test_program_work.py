"""Persistent work behavior against an independent synthetic mini-warehouse."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from clinical_core.agere.clinical import program_work as w
from clinical_core.contracts import digest


class ProgramWork(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        (self.root/'run.json').write_text(json.dumps({'synthetic':True,'source_run':'fixture',
            'source_digest':'visible','snapshot':{'as_of':'2026-09-30'}}))
        db=sqlite3.connect(self.root/'stack.sqlite3')
        db.executescript('CREATE TABLE patients(id TEXT PRIMARY KEY,payload TEXT); CREATE TABLE records(id TEXT PRIMARY KEY,patient TEXT,stream TEXT,payload TEXT); CREATE TABLE analyses(patient TEXT,operator TEXT,payload TEXT,PRIMARY KEY(patient,operator));')
        for i in range(24):
            pid=f'p{i:02d}'
            db.execute('INSERT INTO patients VALUES (?,?)',(pid,json.dumps({'patient_id':pid})))
            r={'record_id':'r'+pid,'patient_id':pid,'source_table':'notes','event_date':'2026-09-01',
               'available_date':'2026-09-02','text':'Synthetic review evidence'}
            db.execute('INSERT INTO records VALUES (?,?,?,?)',(r['record_id'],pid,'notes',json.dumps(r)))
            if i != 23:
                a={'synthetic':True,'patient_id':pid,'source_run':'fixture',
                   'workflow':{'as_of':'2026-09-30','review_candidate':i<21,'status':'review_required' if i<21 else 'no_candidate',
                               'missing_information':['Actual use and eligibility unresolved']},
                   'source_bound_facts':[{'evidence':[{'record_id':r['record_id']}]}]}
                db.execute('INSERT INTO analyses VALUES (?,?,?)',(pid,'heart_failure_review',json.dumps(a)))
        db.commit(); db.close()
        self.proposal=w.prepare(self.root,'hf_review',[f'p{i:02d}' for i in range(24)])

    def tearDown(self): self.temp.cleanup()

    def saved(self):
        w.materialize(self.root,self.proposal['proposal_id'],'fixture operator')
        return w.list_items(self.root)['items'][0]

    def event(self,item,action,**data):
        return w.change(self.root,{'item_id':item['id'],'expected_revision':item['revision'],
            'action':action,'actor':'fixture operator','request_id':str(uuid4()),'data':data})['item']

    def assigned(self): return self.event(self.saved(),'assign',owner='Synthetic PharmD',due_date='2026-10-15')

    def active(self):
        item=self.assigned()
        item=self.event(item,'start_review',note='Fixture review started')
        item=self.event(item,'finish_review',note='Simulated reviewed offer',disposition='offer_program',record_ids=['rp00'])
        item=self.event(item,'offer',note='Simulated offer')
        item=self.event(item,'enroll',note='Simulated consent recorded',consent='confirmed')
        return self.event(item,'start_program',note='Simulated program start')

    def mutate(self,pid='p00',candidate=True):
        db=sqlite3.connect(self.root/'stack.sqlite3')
        a=json.loads(db.execute('SELECT payload FROM analyses WHERE patient=?',(pid,)).fetchone()[0])
        a['workflow']['review_candidate']=candidate;a['workflow']['status']='changed'
        db.execute('UPDATE analyses SET payload=? WHERE patient=?',(json.dumps(a),pid));db.commit();db.close()

    def test_full_population_not_preview_and_unknown_retained(self):
        self.assertEqual(self.proposal['counts'],dict(selected=24,assessed=23,candidates=21,missing_assessment=1))
        self.assertEqual(len(self.proposal['patients']),10)
        self.assertEqual(w.list_items(self.root)['matched'],0)
        result=w.materialize(self.root,self.proposal['proposal_id'],'fixture operator')
        self.assertEqual(result['changes']['created'],21)
        self.assertEqual(w.list_items(self.root)['matched'],21)
        self.assertEqual(result['external_actions_executed'],0)

    def test_save_repeat_preserves_owner_and_revision(self):
        item=self.assigned()
        result=w.materialize(self.root,self.proposal['proposal_id'],'another operator')
        self.assertEqual(result['changes']['unchanged'],21)
        self.assertEqual(w.inspect_item(self.root,item['id'])['item'],item)

    def test_empty_scope_never_expands_or_closes_existing(self):
        item=self.saved();before=w.list_items(self.root)['matched']
        p=w.prepare(self.root,'hf_review',[])
        r=w.materialize(self.root,p['proposal_id'],'fixture operator')
        self.assertEqual(sum(r['changes'].values()),0)
        self.assertEqual(w.list_items(self.root)['matched'],before)
        self.assertEqual(w.inspect_item(self.root,item['id'])['item']['revision'],1)

    def test_concurrent_edits_and_idempotent_retry(self):
        item=self.saved()
        req={'item_id':item['id'],'expected_revision':1,'actor':'operator','action':'assign',
             'request_id':'repeat','data':{'owner':'PharmD','due_date':None}}
        def send(payload):
            try:return w.change(self.root,payload)
            except w.WorkConflict:return 'conflict'
        other=copy.deepcopy(req);other['request_id']='other';other['data']['owner']='Other'
        with ThreadPoolExecutor(2) as pool: results=list(pool.map(send,[req,other]))
        self.assertEqual(results.count('conflict'),1)
        winner=req if results[0]!='conflict' else other
        self.assertTrue(w.change(self.root,winner)['idempotent_replay'])
        self.assertEqual(len(w.inspect_item(self.root,item['id'])['history']),2)
        changed=copy.deepcopy(winner);changed['data']['owner']='Different'
        with self.assertRaises(w.WorkConflict):w.change(self.root,changed)

    def test_no_completion_without_review_consent_monitoring(self):
        item=self.assigned()
        with self.assertRaises(ValueError):self.event(item,'complete_program',note='invalid',record_ids=['rp00'])
        item=self.active()
        with self.assertRaisesRegex(ValueError,'current-evidence'):self.event(item,'complete_program',note='invalid',record_ids=['rp00'])
        item=self.event(item,'record_monitoring',note='Simulated monitoring',record_ids=['rp00'],due_date=None)
        item=self.event(item,'complete_program',note='Simulated completion only',record_ids=['rp00'])
        self.assertEqual(item['state'],'completed');self.assertFalse(item['clinical_goal_verified'])
        self.assertIsNone(w.list_items(self.root)['summary']['verified_clinical_goal_attainment'])

    def test_cross_patient_evidence_and_empty_evidence_rejected(self):
        item=self.event(self.assigned(),'start_review',note='Review')
        for ids in ([],['rp01'],['missing']):
            with self.assertRaises(ValueError): self.event(item,'finish_review',note='Unsupported',disposition='offer_program',record_ids=ids)
        self.assertEqual(w.inspect_item(self.root,item['id'])['item']['state'],'reviewing')

    def test_evidence_change_blocks_old_proposal_and_active_events(self):
        item=self.active();old_identity=w.preparation_identity(self.root,['hf_review'])
        self.mutate()
        self.assertNotEqual(old_identity,w.preparation_identity(self.root,['hf_review']))
        with self.assertRaises(w.WorkConflict):w.materialize(self.root,self.proposal['proposal_id'],'operator')
        with self.assertRaises(w.WorkConflict):self.event(item,'hold',note='stale',due_date='2026-10-15')
        p=w.prepare(self.root,'hf_review',['p00']);w.materialize(self.root,p['proposal_id'],'operator')
        item=w.inspect_item(self.root,item['id'])['item']
        self.assertTrue(item['reassessment_required']);self.assertEqual(item['state'],'active')
        self.assertEqual(item['owner'],'Synthetic PharmD')
        with self.assertRaises(w.WorkConflict):self.event(item,'record_monitoring',note='not reconciled',record_ids=['rp00'],due_date=None)
        item=self.event(item,'reconcile_change',note='New assessment reviewed',record_ids=['rp00'])
        with self.assertRaises(ValueError):self.event(item,'complete_program',note='Old monitoring insufficient',record_ids=['rp00'])

    def test_changed_closed_work_reopens_without_erasing_history(self):
        item=self.event(self.assigned(),'start_review',note='Review')
        item=self.event(item,'finish_review',note='No action supported',disposition='no_action_supported',record_ids=['rp00'])
        self.mutate(candidate=False)
        p=w.prepare(self.root,'hf_review',['p00']);r=w.materialize(self.root,p['proposal_id'],'operator')
        detail=w.inspect_item(self.root,item['id']);new=detail['item']
        self.assertEqual(r['changes']['reopened'],1);self.assertEqual(new['cycle'],2)
        self.assertFalse(new['review_candidate']);self.assertTrue(new['reassessment_required'])
        self.assertEqual(detail['history'][-2]['after_state'],'review_closed')
        self.assertEqual(w.list_items(self.root)['matched'],21)

    def test_append_only_evidence_and_hash_chained_events(self):
        item=self.assigned();detail=w.inspect_item(self.root,item['id']);last=None
        for event in detail['history']:
            self.assertEqual(event['previous_event_hash'],last)
            last=event['event_hash'];self.assertEqual(last,digest({k:v for k,v in event.items() if k!='event_hash'}))
        db=w.database(self.root)
        for sql in ('DELETE FROM work_events','UPDATE work_assessments SET payload=\'{}\''):
            with self.assertRaises(sqlite3.IntegrityError):db.execute(sql)
            db.rollback()
        db.close()

    def test_invalid_source_identity_and_program_are_rejected(self):
        with self.assertRaises(ValueError):w.prepare(self.root,'unknown',[])
        with self.assertRaises(ValueError):w.prepare(self.root,'hf_review',['missing'])
        meta=json.loads((self.root/'run.json').read_text());meta['source_run']='different'
        (self.root/'run.json').write_text(json.dumps(meta))
        with self.assertRaises(ValueError):w.prepare(self.root,'hf_review',['p00'])


if __name__=='__main__':unittest.main()
