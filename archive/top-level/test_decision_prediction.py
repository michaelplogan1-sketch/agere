"""Prediction question joins with actual source/outcome engines; no model fitting."""
import copy,json,sqlite3,unittest,zlib
from unittest.mock import patch
from clinical_core.contracts import digest
from clinical_core.agere.clinical import question_decision as q, baseline_evidence as B, outcomes as O, prediction_registry as R
import test_population_decision as D
import test_longitudinal as L

class PredictionDecision(unittest.TestCase):
    prepare=D.PopulationDecision.prepare
    patch=D.PopulationDecision.patch
    stub_member_wrappers=D.PopulationDecision.stub_member_wrappers
    def setUp(self):
        D.PopulationDecision.setUp(self)
        self.sources=[L.record(rid='index',patient='p0',synthetic=True,identity_status='matched'),L.record(rid='end',patient='p0',event='A03',day='2026-08-20',synthetic=True,identity_status='matched')]
        for r in self.sources:self.f.save_record(r)
        with sqlite3.connect(self.path/'stack.sqlite3') as db:
            for key,raw in list(db.execute('SELECT id,payload FROM records')):
                r=json.loads(raw);r.update(synthetic=True,identity_status='matched');db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(r),key))
        self.meta=json.loads((self.path/'run.json').read_text())
        def connect(path):
            events=[next(L.L.record_events(r,self.req['as_of'])) for r in self.sources]
            db,meta=L.connection(events);db.execute('CREATE TABLE patients(id TEXT PRIMARY KEY)');db.executemany('INSERT INTO patients VALUES (?)',[(f'p{i}',) for i in range(6)])
            meta.update({k:self.meta[k] for k in ('snapshot','source_digest','source_run')});meta.update(code_identity='fixture',reader_model_identity='fixture')
            return db,meta
        self.patch(O.ledger,'connect',side_effect=connect)
        self.patch(R,'status',return_value={'reports':[]})
    def request(self,d,adapter='index_day'):
        return {'decision_id':d['id'],'adapter':adapter,'contract':'admission_discharge_source_30d' if adapter=='index_day' else 'received_admission_future_discharge_30d','start':'2026-01-01','through':d['decision_date']}
    def open(self,r,pid='p0'):
        return q.open_population_prediction(self.retriever,{'decision_id':r['decision_id'],'result_id':r['result_id'],'patient_id':pid})
    def test_exact_scope_saved_replay_and_member_evidence(self):
        d=self.prepare();req=self.request(d);r=q.population_prediction(self.retriever,req)
        self.assertEqual(r,q.population_prediction(self.retriever,req));self.assertEqual(len(r['members']),6)
        self.assertEqual(r['selection'],d['selection']);self.assertEqual(r['question'],d['question']);self.assertEqual(r['status'],'estimate_unavailable')
        self.assertIsNone(r['baseline_comparison']['selected_population_risk']);self.assertIsNone(r['cost']['total_cost_usd'])
        opened=self.open(r);self.assertTrue(opened['current']);self.assertEqual(opened['member_evidence']['baseline']['patient_id'],'p0')
        self.assertTrue(all(not m['clinical_negative_established'] and m['patient_risk'] is None for m in r['members']))
    def test_matching_frozen_report_vs_different_window_and_stale_report(self):
        d=self.prepare();req=self.request(d);row,prep,spec,_,tasks,binding=q.prediction_inputs(self.retriever,req)
        report={'id':'saved','current':True,'task':{**binding['task'],'contract':req['contract']},'dataset_identity':prep['identity'],
            'fit_input_contract_ready':False,'readiness_gaps':['insufficient class support'],'status':'not_fitted','model_report':{'status':'not_fitted','models':{},'automatic_promotion':False},'counts':{'patients_assessed':99}}
        with patch.object(R,'status',return_value={'reports':[report]}):
            r=q.population_prediction(self.retriever,req);self.assertEqual(r['qualification']['status'],'exact_task_evidence_match');self.assertEqual(r['patients_assessed'],6)
            self.assertEqual(r['qualification']['report']['counts']['patients_assessed'],99)
            changed=q.population_prediction(self.retriever,{**req,'start':'2026-07-01'});self.assertIsNone(changed['qualification']['report'])
        self.assertFalse(self.open(r)['current'])
        with patch.object(R,'status',return_value={'reports':[{**report,'current':False,'reason':'changed source'}]}):
            r=q.population_prediction(self.retriever,req);self.assertIsNone(r['qualification']['report'])
    def test_bad_scope_clock_member_and_empty_selection(self):
        d=self.prepare();req=self.request(d)
        for delta in ({'adapter':'guess'},{'through':'2030-01-01'},{'start':'2030-01-01'},{'contract':'unregistered'},{'available_by':'2026-01-01'}):
            with self.assertRaises(ValueError):q.population_prediction(self.retriever,{**req,**delta})
        r=q.population_prediction(self.retriever,req)
        with self.assertRaises(ValueError):self.open(r,'outside')
        with self.assertRaises(ValueError):q.open_population_prediction(self.retriever,{'decision_id':'other','result_id':r['result_id'],'patient_id':''})
        self.req['cohort']={'all':[{'kind':'patient','field':'patient_id','op':'eq','value':'not-observed'}],'any':[],'exclude':[]}
        empty=self.prepare();r=q.population_prediction(self.retriever,self.request(empty));self.assertEqual(r['members'],[]);self.assertEqual(r['timing_eligible'],0)
    def test_receipt_day_distinct_window_and_unknowns(self):
        d=self.prepare();r=q.population_prediction(self.retriever,self.request(d,'receipt_day'));self.assertEqual(len(r['members']),6)
        self.assertEqual(r['binding']['task']['parameters']['receipt_from'],'2026-01-01');self.assertIn('endpoint_clock',r['task_definition'])
        self.assertTrue(self.open(r)['current'])
    def test_source_change_and_receipt_corruption_invalidate_summary(self):
        d=self.prepare();r=q.population_prediction(self.retriever,self.request(d));m=r['members'][0]
        with B.storage(self.path) as db:
            raw=json.loads(zlib.decompress(db.execute('SELECT payload FROM receipts WHERE id=?',(m['baseline_receipt_id'],)).fetchone()[0]));raw['cutoff']='wrong'
            db.execute('UPDATE receipts SET payload=? WHERE id=?',(zlib.compress(json.dumps(raw).encode()),m['baseline_receipt_id']))
        self.assertFalse(self.open(r,'')['current']);self.assertIsNone(self.open(r)['member_evidence'])
    def test_changed_source_marks_report_historical_without_rebinding(self):
        d=self.prepare();r=q.population_prediction(self.retriever,self.request(d))
        self.sources[0]['available_date']='2026-08-10';self.f.save_record(self.sources[0])
        opened=self.open(r,'');self.assertFalse(opened['current']);self.assertIsNone(opened['member_evidence'])
        self.assertEqual(q.saved_followup(self.path,r['result_id']),r)
    def test_unavailable_registry_preserves_supported_nonestimability(self):
        d=self.prepare()
        with patch.object(R,'status',side_effect=ValueError('changed registry')):
            r=q.population_prediction(self.retriever,self.request(d))
            self.assertEqual(r['qualification']['status'],'registry_unavailable');self.assertEqual(r['patients_assessed'],6)
            self.assertIsNone(r['baseline_comparison']['saved_build_comparison']);self.assertTrue(self.open(r)['current'])
    def test_work_changes_keep_historical_scope_and_new_result(self):
        d=self.prepare();self.stub_member_wrappers();m=q.match_population_member(self.retriever,{'decision_id':d['id'],'patient_id':'p0'})
        t=q.queue_population_member(self.retriever,{'decision_id':d['id'],'match_id':m['result_id'],'option_id':m['matching']['options'][0]['id'],'actor':'fixture'})['task']
        r=q.population_prediction(self.retriever,self.request(d));self.assertEqual(r['members'][0]['task_ids'],[t['id']])
        q.event(self.retriever,{'decision_id':d['id'],'task_id':t['id'],'expected_revision':t['revision'],'request_id':'handoff','action':'prepare_handoff','actor':'fixture','note':'Internal only'})
        self.assertFalse(self.open(r)['current']);new=q.population_prediction(self.retriever,self.request(d));self.assertNotEqual(r['result_id'],new['result_id']);self.assertEqual(r['members'],new['members'])
    def test_mid_assembly_change_does_not_save(self):
        d=self.prepare();original=q.prediction_inputs;calls=[]
        def altered(*a):
            v=original(*a);calls.append(1)
            if len(calls)>1:v[-1]['work_digest']='changed'
            return v
        with patch.object(q,'prediction_inputs',side_effect=altered),self.assertRaisesRegex(ValueError,'changed during'):
            q.population_prediction(self.retriever,self.request(d))
        self.assertFalse(any(f['schema']==q.POPULATION_PREDICTION for f in q.reopen(self.retriever,d['id'])['followups']))

if __name__=='__main__':unittest.main()
