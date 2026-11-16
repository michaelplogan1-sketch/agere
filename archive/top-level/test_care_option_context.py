import json,sqlite3,unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import test_care_option_comparison as fixture
from clinical_core.agere.clinical import care_option_context as X,care_option_comparison as C

class Context(unittest.TestCase):
    acquire=fixture.Options.acquire
    event_request=fixture.Options.event_request
    commit=fixture.Options.commit
    successor=fixture.Options.successor
    comparison_request=fixture.Options.comparison_request
    def setUp(self):
        fixture.Options.setUp(self);self.saved=C.prepare(self.path,self.comparison_request())
        self.selection={'comparison_id':self.saved['id']}
        self.records.extend([
          {'record_id':'cov1','patient_id':'p1','source_table':'coverage','event_date':'2026-09-01','available_date':'2026-09-02','coverage_type':'medicare','plan_id':'reported-plan','effective_start':'2026-01-01','effective_end':'2026-12-31'},
          {'record_id':'access1','patient_id':'p1','source_table':'social_screens','event_date':'2026-09-03','available_date':'2026-09-04','respondent':'patient','transport':'difficult'},
          {'record_id':'future','patient_id':'p1','source_table':'coverage','event_date':'2026-09-01','available_date':'2026-10-11','plan_id':'future-plan'}])
    def context_request(self):
        p=X.preview(self.path,self.selection);return {**self.selection,'preview_id':p['id']}
    def test_received_reports_match_patient_cutoff_and_exact_comparison(self):
        p=X.preview(self.path,self.selection)
        self.assertEqual(p['summary']['coverage_reports'],1);self.assertEqual(p['summary']['patient_reports'],1)
        self.assertEqual(p['summary']['reported_plan_ids'],['reported-plan']);self.assertEqual(p['question'],self.saved['question'])
        self.assertEqual(p['coverage_review_targets'][0],self.saved['questions'][4]['target']);self.assertEqual(len(p['patient_review_targets']),2)
        self.assertEqual(p['coverage_context']['reports'][0]['routing_payer_type'],'unknown');self.assertIsNone(p['selected_coverage_record'])
        self.assertFalse(p['patient_preference_confirmed']);self.assertFalse(p['coverage_established'])
        self.assertFalse((self.path/'care-option-context.sqlite3').exists())
    def test_save_and_replay_preserve_original_plan_routing_and_question(self):
        req=self.context_request();out=X.prepare(self.path,req);self.assertEqual(X.prepare(self.path,req),out)
        self.assertEqual(C.snapshot(self.path,self.saved['request']),self.saved)
        self.assertTrue(X.listing(self.path,self.selection)['contexts'][0]['current'])
        self.assertEqual(out['coverage_context']['patient_id'],'p1');self.assertEqual(out['as_of'],'2026-10-10')
    def test_empty_projection_is_not_absence_or_no_coverage(self):
        self.records=self.records[:1];p=X.preview(self.path,self.selection)
        self.assertEqual(p['summary']['coverage_reports'],0);self.assertEqual(p['summary']['patient_reports'],0)
        self.assertFalse(p['coverage_established']);self.assertFalse(p['patient_preference_confirmed'])
    def test_future_service_report_retains_temporal_limit(self):
        self.records.append({'record_id':'verify','patient_id':'p1','source_table':'coverage_verifications','event_date':'2026-10-01','available_date':'2026-10-01','service_reference_date':'2026-11-01','status':'active','coverage_type_reported':'original_medicare'})
        p=X.preview(self.path,self.selection);row=next(r for r in p['coverage_context']['reports'] if r['record_id']=='verify')
        self.assertEqual(row['temporal_scope'],'future_service_date');self.assertFalse(row['verified_benefits']);self.assertIsNone(p['selected_coverage_record'])
    def test_report_changes_invalidate_preview_and_preserve_saved_history(self):
        req=self.context_request();out=X.prepare(self.path,req);self.records[1]['plan_id']='changed-plan'
        with self.assertRaisesRegex(ValueError,'context changed'):X.prepare(self.path,req)
        old=X.listing(self.path,self.selection)['contexts'][0];self.assertFalse(old['current']);self.assertEqual(old['id'],out['id'])
        new=X.prepare(self.path,self.context_request());self.assertNotEqual(new['id'],old['id'])
    def test_wrong_context_patient_or_date_is_rejected(self):
        for key,value in [('patient_id','other'),('as_of','2026-10-09')]:
            prior=X.coverage.prepare
            with patch.object(X.coverage,'prepare',side_effect=lambda *args:{**prior(*args),key:value}):
                with self.assertRaisesRegex(ValueError,'patient or cutoff'):X.preview(self.path,self.selection)
    def test_reference_withdrawal_marks_context_historical(self):
        saved=X.prepare(self.path,self.context_request());self.commit()
        old=X.listing(self.path,self.selection)['contexts'][0];self.assertFalse(old['current']);self.assertEqual(old['id'],saved['id'])
        with self.assertRaises(ValueError):X.preview(self.path,self.selection)
    def test_unknown_comparison_and_extra_fields_rejected(self):
        for req in [{'comparison_id':'0'*64},{**self.selection,'patient_id':'p1'},{**self.selection,'selected_coverage_record':'cov1'}]:
            with self.assertRaises(ValueError):X.preview(self.path,req)
    def test_concurrent_attachment_is_idempotent(self):
        req=self.context_request()
        with ThreadPoolExecutor(max_workers=2) as pool:rows=list(pool.map(lambda _:X.prepare(self.path,req),range(2)))
        self.assertEqual(rows[0],rows[1]);self.assertEqual(len(X.listing(self.path,self.selection)['contexts']),1)
    def test_partial_context_preparation_can_be_replayed(self):
        with patch.object(X.patient,'prepare',side_effect=ValueError('fixture patient projection failure')):
            with self.assertRaises(ValueError):X.preview(self.path,self.selection)
        self.assertEqual(X.listing(self.path,self.selection)['contexts'],[])
        self.assertTrue(X.prepare(self.path,self.context_request())['id'])
    def test_final_check_cannot_commit_changed_context(self):
        req=self.context_request();prior=X.snapshot;calls=[]
        def changed(*args):
            out=prior(*args);calls.append(1)
            if len(calls)>1:out['summary']['coverage_reports']+=1
            return out
        with patch.object(X,'snapshot',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'while saving'):X.prepare(self.path,req)
        self.assertEqual(X.listing(self.path,self.selection)['contexts'],[])
    def test_immutable_attachment_and_tamper_detection(self):
        out=X.prepare(self.path,self.context_request());db=X.database(self.path)
        try:
            for sql in ['DELETE FROM contexts','UPDATE contexts SET comparison=comparison']:
                with self.assertRaises(sqlite3.IntegrityError):db.execute(sql)
            db.execute('DROP TRIGGER immutable_contexts_UPDATE');out['coverage_established']=True;db.execute('UPDATE contexts SET payload=?',(json.dumps(out),));db.commit()
        finally:db.close()
        with self.assertRaisesRegex(ValueError,'integrity'):X.listing(self.path,self.selection)

if __name__=='__main__':unittest.main()
