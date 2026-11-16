"""Source/contract/review boundaries for the semantic reader; no clinical accuracy claim."""
import copy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from clinical_core.agere.clinical import semantic_reader as sr
from clinical_core.contracts import digest

LABELS={'statement':'patient_affirmed','time':'current_at_source','relation':'clinical_observation'}


def raw(body,labels=LABELS):
    return {'model':body['model'],'answers':{k:{'type':'choice','choice':labels[k],
        'probabilities':{c:float(c==labels[k]) for c in q['criteria']}} for k,q in body['questions'].items()}}


class FakeLocal:
    identity='fixture_model_not_a_real_model'
    def __init__(self,labels=LABELS):self.labels=labels;self.calls=0
    def __call__(self,state,questions):
        self.calls+=1
        return {**raw({'model':'fixture','questions':questions},self.labels),'stats':{'seconds':0,'input_tokens':1,'output_tokens':0}}


class SemanticTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        self.record={'record_id':'n','patient_id':'p','source_table':'notes','identity_status':'matched','synthetic':True,
            'event_date':'2026-09-01','available_date':'2026-09-02','text':'Patient reports difficulty paying medical bills today.'}
        self.meta={'synthetic':True,'source_digest':'fixture','snapshot':{'as_of':'2026-09-30'}}
        (self.path/'run.json').write_text(json.dumps(self.meta))
        c=sqlite3.connect(self.path/'stack.sqlite3');c.execute('CREATE TABLE records(id TEXT,patient TEXT,payload TEXT)')
        c.execute('INSERT INTO records VALUES (?,?,?)',('n','p',json.dumps(self.record)));c.commit();c.close()
        self.target={'patient_id':'p','record_id':'n','field':'text','start':16,'end':46,'concept':'financial hardship'}
    def tearDown(self):self.temp.cleanup()
    def read(self,labels=LABELS):return sr.read(self.path,self.target,'kev',local_model=FakeLocal(labels))
    def payload(self,reading,**extra):
        return {'reading_id':reading['id'],'expected_revision':0,'request_id':'r1','actor':'SIMULATED TEST REVIEWER',
            'status':'accepted','labels':LABELS,'rationale':'Isolated test fixture, not a user review.',**extra}
    def state_db(self,status='unobserved',value=None):
        c=sqlite3.connect(self.path/'states.sqlite3');c.execute('CREATE TABLE states(patient TEXT,payload TEXT)')
        c.execute('INSERT INTO states VALUES (?,?)',('p',json.dumps({'patient_id':'p','bindings':{
            'financial_report':{'status':status,'value':value,'evidence':[],'selected_date':'2026-09-01','current_clinical_state_established':False}}})));c.commit();c.close()

    def test_exact_source_offset_contract(self):
        r,text,m=sr.source(self.path,self.target);p=sr.packet(r,text,self.target,m['snapshot']['as_of'])
        self.assertEqual(p['target_quote'],text[16:46]);self.assertTrue(p['context_complete'])
        self.assertEqual(p['text'][p['target_start']:p['target_end']],p['target_quote'])

    def test_wrong_patient_rejected(self):
        with self.assertRaises(ValueError):sr.source(self.path,{**self.target,'patient_id':'another'})

    def test_unmapped_or_out_of_range_text_rejected(self):
        for change in ({'field':'original_text'},{'start':-1},{'end':1000},{'start':True}):
            with self.assertRaises(ValueError):sr.source(self.path,{**self.target,**change})

    def test_future_arrival_rejected(self):
        c=sqlite3.connect(self.path/'stack.sqlite3');c.execute('UPDATE records SET payload=?',(json.dumps({**self.record,'available_date':'2027-01-01'}),));c.commit();c.close()
        with self.assertRaises(ValueError):self.read()

    def test_whole_paragraph_transport_exposes_omissions(self):
        text='a'*15000+'\nThe patient reports a barrier.\n'+'z'*15000
        a=text.index('barrier');target={**self.target,'start':a,'end':a+7}
        p=sr.packet(self.record,text,target,'2026-09-30')
        self.assertFalse(p['context_complete']);self.assertEqual(p['text'],'The patient reports a barrier.')
        self.assertEqual(len(p['omitted_character_ranges']),2)

    def test_indivisible_long_statement_not_truncated(self):
        with self.assertRaises(ValueError):sr.packet(self.record,'x'*12000,self.target,'2026-09-30')

    def test_identical_contract_across_native_and_typed_models(self):
        p=sr.packet(self.record,self.record['text'],self.target,'2026-09-30')
        bodies=[sr.request(p,provider) for provider in ('kev','d1','jev','perplexity')]
        self.assertTrue(all(b['state']==bodies[0]['state'] and b['questions']==bodies[0]['questions'] for b in bodies))
        self.assertEqual(len(bodies[0]['questions']),3);self.assertLessEqual(max(len(q['criteria']) for q in bodies[0]['questions'].values()),12)

    def test_request_does_not_include_old_predictions_or_patient_id(self):
        p=sr.packet(self.record,self.record['text'],self.target,'2026-09-30');body=sr.request(p,'perplexity')
        self.assertNotIn('patient_id',body['state']);self.assertNotIn('annotation',body['state'])

    def test_wrong_model_invalid_distribution_and_wrong_choices_rejected(self):
        p=sr.packet(self.record,self.record['text'],self.target,'2026-09-30');body=sr.request(p,'perplexity')
        bad=raw(body);bad['model']='wrong'
        with self.assertRaises(ValueError):sr.parse(bad,body)
        bad=raw(body);bad['answers']['statement']['probabilities']['unknown']=float('nan')
        with self.assertRaises(ValueError):sr.parse(bad,body)
        bad=raw(body);bad['answers']['relation']['choice']='drug_stopped'
        with self.assertRaises(ValueError):sr.parse(bad,body)

    def test_dose_change_never_satisfies_hold_or_tolerance(self):
        result=sr.implications({**LABELS,'relation':'dose_change'},'medication bb')
        self.assertEqual(result['requirements_satisfied'],[])
        self.assertNotIn('therapy.bb.exception',result['candidate_requirement_bindings'])
        self.assertIn('therapy.bb.tolerance',result['candidate_requirement_bindings'])

    def test_source_coverage_never_satisfies_completed_care(self):
        result=sr.implications({**LABELS,'relation':'source_coverage'},'care nephrology')
        self.assertFalse(result['requirements_satisfied']);self.assertFalse(result['candidate_requirement_bindings'])

    def test_exact_reading_cache_avoids_another_inference(self):
        model=FakeLocal();first=sr.read(self.path,self.target,'kev',local_model=model)
        second=sr.read(self.path,self.target,'kev',local_model=model)
        self.assertEqual(model.calls,1);self.assertTrue(second['cache_hit']);self.assertFalse(first['automatic_acceptance'] if 'automatic_acceptance' in first else first['reading']['automatic_acceptance'])

    def test_contract_identity_stable_after_inference_and_in_fresh_process(self):
        before=sr.identity()
        for _ in range(4):self.read()
        self.assertEqual(before,sr.identity())
        fresh=subprocess.check_output([sys.executable,'-c',
            'from clinical_core.agere.clinical.semantic_reader import identity; print(identity())'],text=True).strip()
        self.assertEqual(before,fresh)

    def test_new_warehouse_identity_never_replays_stale_reading(self):
        model=FakeLocal();first=sr.read(self.path,self.target,'kev',local_model=model)
        (self.path/'run.json').write_text(json.dumps({**self.meta,'source_digest':'replacement_snapshot'}))
        second=sr.read(self.path,self.target,'kev',local_model=model)
        self.assertNotEqual(first['id'],second['id']);self.assertFalse(second['cache_hit'])
        self.assertEqual(sr.inspect(self.path,second['id'])['source_identity']['source_digest'],'replacement_snapshot')

    def test_listing_keeps_disagreement_without_voting(self):
        first=sr.read(self.path,self.target,'kev',local_model=FakeLocal())
        second=sr.read(self.path,self.target,'d1',local_model=FakeLocal({**LABELS,'time':'unknown'}))
        result=sr.list_readings(self.path)
        self.assertEqual(result['total'],2)
        self.assertEqual(result['comparisons_in_page'][0]['disagreement_axes'],['time'])
        self.assertFalse(result['comparisons_in_page'][0]['automatic_acceptance'])
        self.assertEqual({r['id'] for r in result['rows']},{first['id'],second['id']})

    def test_listing_excludes_old_reader_contract(self):
        self.read()
        with patch.object(sr,'VERSION','changed-contract'):
            self.assertEqual(sr.list_readings(self.path)['total'],0)

    def test_changed_source_rejects_saved_interpretation(self):
        r=self.read();c=sqlite3.connect(self.path/'stack.sqlite3');c.execute('UPDATE records SET payload=?',(json.dumps({**self.record,'text':'Changed clinical source'}),));c.commit();c.close()
        with self.assertRaises(ValueError):sr.inspect(self.path,r['id'])

    def test_review_idempotency_and_stale_revision(self):
        r=self.read();p=self.payload(r);first=sr.save_review(self.path,p);second=sr.save_review(self.path,p)
        self.assertEqual(first['revision'],1);self.assertTrue(second['replayed'])
        with self.assertRaises(ValueError):sr.save_review(self.path,{**p,'request_id':'r2'})
        with self.assertRaises(ValueError):sr.save_review(self.path,{**p,'rationale':'changed'})

    def test_accepting_changed_labels_requires_correction_status(self):
        r=self.read()
        with self.assertRaises(ValueError):sr.save_review(self.path,self.payload(r,labels={**LABELS,'statement':'patient_negated'}))
        saved=sr.save_review(self.path,self.payload(r,status='corrected',labels={**LABELS,'statement':'patient_negated'}))
        self.assertTrue(saved['training_eligible']);self.assertFalse(saved['reviewer_identity_verified'])

    def test_latest_unresolved_review_excludes_prior_accepted_label(self):
        r=self.read();sr.save_review(self.path,self.payload(r))
        self.assertEqual(len(sr.learning_export(self.path)['rows']),1)
        sr.save_review(self.path,self.payload(r,request_id='r2',expected_revision=1,status='unresolved'))
        export=sr.learning_export(self.path);self.assertEqual(export['rows'],[]);self.assertFalse(export['training_performed'])

    def test_machine_proposals_alone_never_enter_learning_export(self):
        self.read();self.assertEqual(sr.learning_export(self.path)['rows'],[])

    def test_reviews_are_append_only(self):
        r=self.read();sr.save_review(self.path,self.payload(r));database=sr.db(self.path)
        with self.assertRaises(sqlite3.IntegrityError):database.execute('DELETE FROM reviews')
        database.close()

    def test_unreviewed_reading_does_not_change_prerequisites(self):
        self.state_db();self.read()
        with patch('clinical_core.agere.clinical.clinical_state.attached',return_value=(self.path,{})):
            result=sr.prerequisites(self.path,'p')
        self.assertFalse(result['documentary_binding_changes']);self.assertFalse(result['clinical_eligibility_established'])

    def test_reviewed_report_can_supply_documentary_requirement_only(self):
        self.state_db();r=self.read();sr.save_review(self.path,self.payload(r))
        with patch('clinical_core.agere.clinical.clinical_state.attached',return_value=(self.path,{})):
            result=sr.prerequisites(self.path,'p')
        self.assertEqual(result['documentary_binding_changes'][0]['after']['status'],'documented')
        affordability=next(e for e in result['evaluations'] if e['contract']['id']=='affordability')
        self.assertEqual(affordability['status'],'requirements_unresolved')
        self.assertIn('financial_current',affordability['unresolved_bindings'])
        self.assertFalse(result['base_artifact_modified'])

    def test_opposing_review_does_not_erase_existing_same_date_report(self):
        self.state_db('documented',False);r=self.read();sr.save_review(self.path,self.payload(r))
        with patch('clinical_core.agere.clinical.clinical_state.attached',return_value=(self.path,{})):
            result=sr.prerequisites(self.path,'p')
        self.assertEqual(result['documentary_binding_changes'][0]['after']['status'],'conflicting')

    def test_opposing_reviews_of_same_occurrence_block_documentary_binding(self):
        self.state_db();a=self.read();sr.save_review(self.path,self.payload(a))
        b=sr.read(self.path,self.target,'d1',local_model=FakeLocal({**LABELS,'statement':'patient_negated'}))
        sr.save_review(self.path,self.payload(b,request_id='opposing',labels={**LABELS,'statement':'patient_negated'}))
        with patch('clinical_core.agere.clinical.clinical_state.attached',return_value=(self.path,{})):
            result=sr.prerequisites(self.path,'p')
        self.assertFalse(result['documentary_binding_changes'])
        self.assertEqual(result['source_review_groups'][0]['status'],'review_conflict')
        exported=sr.learning_export(self.path)
        self.assertFalse(exported['rows'])
        self.assertEqual({r['reason'] for r in exported['excluded']},{'conflicting_or_unresolved_reviews_of_same_source_occurrence'})


if __name__=='__main__':unittest.main()
