"""Reviewed-source composition and invalidation; no clinical-accuracy claims."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zlib

from clinical_core.contracts import digest
from clinical_core.agere.clinical import interpretation_state as st, semantic_reader as sr
from clinical_core.agere.clinical import assertion_reconciliation as ar
from clinical_core.agere.clinical.graph_workbench import interpretation_examples
from clinical_core.agere.clinical.economical_planner import local_plan
from test_assertion_reconciliation import annotation,receipt,P,ASOF
from test_semantic_reader import FakeLocal,LABELS

PARAMS={**P,'filter':'all'}


def fixture(labels=None,review_status=None,provider='kev',revision=1):
    record,event=annotation('Patient reports difficulty paying medical bills.',rid='n')
    base=receipt([record],[event]);base['id']='base'
    target={'patient_id':'p','record_id':'n','field':'text','start':0,'end':len(record['text']),'concept':'financial hardship'}
    labels=labels or LABELS
    reading={'id':provider,'provider':provider,'target':target,'source_hash':digest(record),
        'context':sr.packet(record,record['text'],target,ASOF),'reading':{'labels':labels},'reviews':[]}
    if review_status:reading['reviews']=[{'status':review_status,'revision':revision,'labels':labels}]
    return base,reading


class ReconciliationTests(unittest.TestCase):
    def test_unreviewed_reader_does_not_overwrite_old_assertion(self):
        base,r=fixture({'statement':'patient_affirmed','time':'current_at_source','relation':'dose_change'})
        row=st.patient_receipt(base,[r],[])
        self.assertEqual(row['effective_assertions'],base['assertions']);self.assertFalse(row['superseded_assertions'])
        self.assertFalse(row['flags']['reviewed_source']);self.assertTrue(row['flags']['needs_review'])

    def test_reviewed_exact_occurrence_replaces_one_old_proposal(self):
        base,r=fixture({'statement':'patient_negated','time':'current_at_source','relation':'clinical_observation'},'corrected')
        row=st.patient_receipt(base,[r],[])
        self.assertEqual(row['effective_assertions'][0]['polarity'],'negated')
        self.assertEqual(row['effective_assertions'][0]['authority'],'source_review')
        self.assertEqual(row['superseded_assertions'][0]['prior_assertion_id'],base['assertions'][0]['id'])
        self.assertFalse(row['current_clinical_state_established']);self.assertEqual(base['assertions'][0]['polarity'],'affirmed')

    def test_conflicting_explicit_reviews_have_no_winner(self):
        base,a=fixture(review_status='accepted');_,b=fixture({**LABELS,'statement':'patient_negated'},'corrected','d1')
        row=st.patient_receipt(base,[a,b],[])
        self.assertTrue(row['flags']['review_conflict']);self.assertFalse(row['superseded_assertions'])
        self.assertIsNone(row['source_groups'][0]['effective_labels'])

    def test_one_unresolved_review_blocks_an_accepted_review(self):
        base,a=fixture(review_status='accepted');_,b=fixture(review_status='unresolved',provider='d1')
        row=st.patient_receipt(base,[a,b],[])
        self.assertEqual(row['source_groups'][0]['status'],'unresolved_review')
        self.assertFalse(row['flags']['reviewed_source'])
        self.assertFalse(row['flags']['review_conflict']);self.assertTrue(row['flags']['unresolved_review'])

    def test_rejected_other_model_does_not_override_source_review(self):
        base,a=fixture(review_status='accepted');_,b=fixture({**LABELS,'statement':'patient_negated'},'rejected','d1')
        row=st.patient_receipt(base,[a,b],[])
        self.assertTrue(row['flags']['reviewed_source']);self.assertTrue(row['flags']['model_disagreement'])
        self.assertEqual(len(row['superseded_assertions']),1)

    def test_partial_context_cannot_become_effective_review(self):
        base,r=fixture(review_status='accepted');r['context']['context_complete']=False
        row=st.patient_receipt(base,[r],[])
        self.assertEqual(row['source_groups'][0]['status'],'incomplete_review_context')
        self.assertFalse(row['superseded_assertions'])

    def test_reviewed_historical_and_other_person_remain_excluded_from_current_clock(self):
        for change in ({'time':'historical'},{'statement':'other_person'},{'statement':'hypothetical'},{'statement':'retracted'}):
            base,r=fixture({**LABELS,**change},'corrected');row=st.patient_receipt(base,[r],[])
            self.assertFalse(row['documentary_timeline'][0]['dated_accounts'])
            self.assertFalse(row['current_clinical_state_established'])

    def test_reviewed_copied_history_does_not_gain_current_timestamp(self):
        base,r=fixture(review_status='accepted');r['context']['copied_history']=True
        row=st.patient_receipt(base,[r],[])
        self.assertFalse(row['documentary_timeline'][0]['latest_by_clock'])

    def test_cross_patient_or_changed_source_is_rejected(self):
        base,r=fixture(review_status='accepted');r['target']['patient_id']='someone_else'
        with self.assertRaises(ValueError):st.patient_receipt(base,[r],[])
        base,r=fixture(review_status='accepted');r['source_hash']='changed'
        with self.assertRaises(ValueError):st.patient_receipt(base,[r],[])

    def test_dose_change_never_becomes_stop_or_tolerance(self):
        base,r=fixture({**LABELS,'relation':'dose_change'},'corrected')
        row=st.patient_receipt(base,[r],[])
        self.assertEqual(row['effective_assertions'][0]['relation'],'dose_change')
        self.assertFalse(row['clinical_eligibility_established'])

    def test_current_unmapped_notes_are_still_unassessed(self):
        base,r=fixture(review_status='accepted');base['coverage']['units_without_in_scope_assertions']=3
        row=st.patient_receipt(base,[r],[])
        self.assertEqual(row['coverage']['units_without_in_scope_assertions'],3)
        self.assertTrue(row['flags']['needs_review'])

    def test_each_example_compiles_without_a_provider(self):
        for e in interpretation_examples():
            plan,route=local_plan(e['question'],ASOF)
            self.assertEqual(route,'registered_example')
            self.assertEqual(plan['nodes'][1]['operation'],'interpretation')
        self.assertIn('preference declines',interpretation_examples()[0]['graph']['nodes'][1]['parameters']['concepts'])


class PatientCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.meta={'synthetic':True,'source_digest':'fixture','snapshot':{'as_of':ASOF}}
        (self.root/'run.json').write_text(json.dumps(self.meta))
        db=sqlite3.connect(self.root/'stack.sqlite3');db.execute('CREATE TABLE records(id TEXT,patient TEXT,payload TEXT)')
        self.base={};self.targets={}
        for pid in ('p','q'):
            record,event=annotation('Patient reports difficulty paying medical bills.',rid='n'+pid)
            record.update(patient_id=pid,synthetic=True,identity_status='matched')
            event['patient_id']=pid;event['source']['patient_id']=pid;event['record_hash']=digest(record)
            row=ar.patient_receipt(pid,[record],[event],P,self.meta)
            row['id']=digest(['base-identity',pid]);self.base[pid]=row
            self.targets[pid]={'patient_id':pid,'record_id':'n'+pid,'field':'text','start':0,'end':len(record['text']),'concept':'financial hardship'}
            db.execute('INSERT INTO records VALUES (?,?,?)',('n'+pid,pid,json.dumps(record)))
        db.commit();db.close()
        database=ar.storage(self.root)
        for pid,row in self.base.items():database.execute('INSERT INTO receipts VALUES (?,?,?,?)',(row['id'],pid,'base-identity',zlib.compress(json.dumps(row).encode())))
        database.commit();database.close()
        self.dep=patch.object(ar,'dependency',return_value=('base-identity',self.meta,{}));self.dep.start()
        self.run=patch.object(ar,'execute',return_value={'identity':'base-identity','counts':{}});self.run.start()
        self.a=sr.read(self.root,self.targets['p'],'kev',local_model=FakeLocal())
        self.b=sr.read(self.root,self.targets['q'],'kev',local_model=FakeLocal())
    def tearDown(self):self.run.stop();self.dep.stop();self.temp.cleanup()
    def review(self,r,status='accepted',labels=LABELS,revision=0):
        return sr.save_review(self.root,{'reading_id':r['id'],'expected_revision':revision,'request_id':str(revision)+r['id'],
            'actor':'ISOLATED TEST FIXTURE','status':status,'labels':labels,'rationale':'Synthetic test only, not user review.'})
    def test_changed_review_recomputes_only_affected_patient(self):
        _,first=st.execute(self.root,['p','q'],PARAMS)
        self.assertEqual(first['counts']['patient_receipts_computed'],2)
        _,second=st.execute(self.root,['p','q'],PARAMS)
        self.assertEqual(second['counts']['patient_cache_hits'],2)
        self.review(self.a)
        _,third=st.execute(self.root,['p','q'],PARAMS)
        self.assertNotEqual(first['identity'],third['identity'])
        self.assertEqual(third['counts']['patient_cache_hits'],1);self.assertEqual(third['counts']['patient_receipts_computed'],1)
        self.assertEqual(third['counts']['reviewed_source_groups'],1)
        with self.assertRaisesRegex(ValueError,'changed'):
            st.inspect(self.root,next(p['id'] for p in first['patients'] if p['patient_id']=='p'))
    def test_newest_unresolved_review_removes_prior_effective_interpretation(self):
        self.review(self.a);_,first=st.execute(self.root,['p'],PARAMS)
        self.review(self.a,status='unresolved',revision=1)
        _,second=st.execute(self.root,['p'],PARAMS)
        self.assertEqual(first['counts']['reviewed_source_groups'],1)
        self.assertEqual(second['counts']['reviewed_source_groups'],0)
        self.assertEqual(second['counts']['superseded_machine_assertions'],0)
    def test_review_status_filter_selects_complete_set_and_empty_stays_empty(self):
        self.review(self.a)
        selected,result=st.execute(self.root,['p','q'],{**PARAMS,'filter':'reviewed_source'})
        self.assertEqual(selected,{'p'});self.assertEqual(result['patients_evaluated'],2)
        self.assertEqual([r['patient_id'] for r in result['patients']],['p'])
        selected,result=st.execute(self.root,[],PARAMS)
        self.assertEqual(selected,set());self.assertEqual(result['patients_evaluated'],0)
    def test_source_change_excludes_stale_interpretation(self):
        db=sqlite3.connect(self.root/'stack.sqlite3');raw=json.loads(db.execute('SELECT payload FROM records WHERE patient="p"').fetchone()[0]);raw['text']='Changed received source';db.execute('UPDATE records SET payload=? WHERE patient="p"',(json.dumps(raw),));db.commit();db.close()
        snap=st.snapshot(self.root,PARAMS)
        self.assertNotIn('p',snap['readings']);self.assertEqual(len(snap['stale']['p']),1)


if __name__=='__main__':unittest.main()
