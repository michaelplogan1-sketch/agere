"""Authored temporal supervision controls. No warehouse, provider, or pretrained model."""
import re
import sqlite3
import unittest
from unittest.mock import patch

from clinical_core.agere.clinical import denial_prediction as D
from clinical_core.agere.clinical import population_feeds as P, adjudication_history as A

ASOF = '2026-09-30'
CUTOFF = '2026-03-31'


def authored_store():
    db = sqlite3.connect(':memory:')
    for ddl, name in ((P.DDL, 'payer_claim'), (P.DDL, 'member_month'), (A.DDL, 'payer_adjudication')):
        db.executescript(re.search(r'CREATE TABLE '+name+r'\(.*?;', ddl, re.S).group(0))
    for column in ('rendering_npi_missing INTEGER', 'duplicate_submission INTEGER', 'claim_frequency_code TEXT', 'version_status TEXT'):
        db.execute('ALTER TABLE payer_claim ADD COLUMN '+column)
    for prefix, count, recv, event, arrival in (
        ('late-label-', 60, '2026-03-20', '2026-08-15', '2026-08-16'),
        ('timely-', 6, '2026-03-10', '2026-03-11', '2026-03-12'),
        ('evaluation-', 30, '2026-04-20', '2026-05-15', '2026-05-16'),
        ('future-', 1, '2026-10-01', '2026-10-02', '2026-10-03'),
        ('voided-', 1, '2026-03-20', '2026-03-21', '2026-03-22')):
        for i in range(count):
            cid = prefix+str(i); status = 'denied' if i % 2 == 0 else 'paid'; carc = 'CO-16' if i % 2 == 0 else None
            db.execute('INSERT INTO payer_claim (payer_claim_id,patient_id,payer_id,product,claim_type,place_of_service,network_status,billing_npi,principal_diagnosis,service_from,claim_received_date,first_status,rendering_npi_missing,duplicate_submission,claim_frequency_code,charge_cents,version_status,first_available_date,last_available_date,adjudications) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (cid,'authored-p','authored-payer','authored-product','professional','11','in','authored-npi','I10','2026-03-01',recv,status,0,0,'1',10000,'voided' if prefix=='voided-' else 'current',arrival,arrival,1))
            db.execute('INSERT INTO payer_adjudication (payer_claim_id,record_id,patient_id,sequence,claim_status,carc,is_first,is_last,adjudication_date,available_date) VALUES (?,?,?,?,?,?,?,?,?,?)',
                       (cid,'adjudication-'+cid,'authored-p',1,status,carc,1,1,event,arrival))
    return db


class Timing(unittest.TestCase):
    def setUp(self):
        self.db = authored_store(); self.addCleanup(self.db.close); self.p = D.validate({'score_days':365})

    def rows(self, origin=ASOF, **params):
        return D.load(self.db, None, {**self.p, **params}, origin)

    def row(self, cid='timely-0', origin=CUTOFF):
        return next(r for r in self.rows(origin) if r['claim_id']==cid)

    def split(self): return D.split_at_origin(self.rows(), CUTOFF, ASOF)

    def test_fit_origin_uses_six_timely_labels_not_sixty_future_labels(self):
        train, test, support = self.split()
        self.assertEqual((len(train),len(test)), (6,30))
        self.assertEqual(support['exclusions'], {'train:label_not_available_at_origin':60})
        self.assertTrue(all(r['label_origin']==CUTOFF for r in train))
        self.assertTrue(all(r['label_origin']==ASOF for r in test))

    def test_historical_load_preserves_unknown_targets_without_future_carc(self):
        rows = self.rows(CUTOFF)
        self.assertEqual(len(rows),66)
        unknown = [r for r in rows if r['denied'] is None]
        self.assertEqual(len(unknown),60)
        self.assertTrue(all(r['carc'] is None for r in unknown))
        self.assertEqual(sum(r['denied'] is not None for r in rows),6)

    def test_later_evaluation_labels_remain_usable(self):
        _, test, _ = self.split()
        self.assertEqual(sum(r['denied'] for r in test),15)
        self.assertEqual({r['label_witnesses'][0]['available_date'] for r in test}, {'2026-05-16'})

    def test_later_receipt_only_changes_label_eligibility(self):
        self.db.execute("UPDATE payer_adjudication SET available_date='2026-04-01' WHERE payer_claim_id='timely-0'")
        self.assertEqual(self.row()['label_status'],'label_not_available_at_origin')
        self.assertEqual(len(self.split()[0]),5)
        self.assertEqual(self.row(origin=ASOF)['denied'],1)

    def test_future_adjudication_event_is_not_available_label(self):
        self.db.execute("UPDATE payer_adjudication SET adjudication_date='2026-04-01',available_date='2026-04-02' WHERE payer_claim_id='timely-0'")
        self.assertIsNone(self.row()['denied'])
        self.assertEqual(len(self.split()[0]),5)

    def test_header_version_after_origin_cannot_train_even_with_timely_label(self):
        self.db.execute("UPDATE payer_claim SET last_available_date='2026-04-01' WHERE payer_claim_id='timely-0'")
        train, _, support = self.split()
        self.assertEqual(len(train),5)
        self.assertEqual(support['exclusions']['train:current_header_unavailable_at_origin'],1)
        self.assertEqual(self.row()['denied'],1)

    def test_first_header_arrival_cannot_backdate_current_version(self):
        self.db.execute("UPDATE payer_claim SET first_available_date='2026-03-12',last_available_date='2026-08-01',adjudications=2 WHERE payer_claim_id='timely-0'")
        self.assertEqual(len(self.split()[0]),5)

    def test_missing_header_clock_is_not_timely(self):
        self.db.execute("UPDATE payer_claim SET last_available_date=NULL WHERE payer_claim_id='timely-0'")
        self.assertEqual(len(self.split()[0]),5)

    def test_missing_invalid_label_clocks_stay_unknown(self):
        for clock in ('adjudication_date','available_date'):
            for val in (None, '', '2026-02-30', '2026-03-12Tgarbage'):
                with self.subTest(clock=clock, val=val):
                    self.db.execute('UPDATE payer_adjudication SET '+clock+'=? WHERE payer_claim_id=?',(val,'timely-0'))
                    self.assertEqual(self.row()['label_status'],'missing_or_invalid_label_clock')
                self.db.execute('UPDATE payer_adjudication SET '+clock+'=? WHERE payer_claim_id=?',('2026-03-11' if clock=='adjudication_date' else '2026-03-12','timely-0'))

    def test_inconsistent_event_receipt_is_not_timely(self):
        self.db.execute("UPDATE payer_adjudication SET available_date='2026-03-10' WHERE payer_claim_id='timely-0'")
        self.assertEqual(self.row()['label_status'],'inconsistent_label_clock')

    def test_event_cannot_precede_submission(self):
        self.db.execute("UPDATE payer_adjudication SET adjudication_date='2026-03-09' WHERE payer_claim_id='timely-0'")
        self.assertEqual(self.row()['label_status'],'inconsistent_label_clock')

    def test_wrong_patient_is_not_a_target(self):
        self.db.execute("UPDATE payer_adjudication SET patient_id='other-p' WHERE payer_claim_id='timely-0'")
        self.assertEqual(self.row()['label_status'],'first_adjudication_identity_mismatch')

    def test_multiple_first_targets_are_not_arbitrarily_selected(self):
        self.db.execute("INSERT INTO payer_adjudication (payer_claim_id,record_id,patient_id,claim_status,is_first,adjudication_date,available_date) VALUES ('timely-0','another','authored-p','paid',1,'2026-03-11','2026-03-12')")
        self.assertEqual(self.row()['label_status'],'ambiguous_first_adjudication')
        self.assertEqual(len(self.row()['label_witnesses']),2)

    def test_missing_first_witness_does_not_use_current_header_label(self):
        self.db.execute("DELETE FROM payer_adjudication WHERE payer_claim_id='timely-0'")
        self.assertEqual(self.row()['label_status'],'missing_first_adjudication')

    def test_pending_reversed_or_unknown_does_not_become_paid(self):
        for status in ('pending','reversed',None,''):
            self.db.execute('UPDATE payer_adjudication SET claim_status=? WHERE payer_claim_id=?',(status,'timely-0'))
            self.assertEqual(self.row()['label_status'],'unresolved_first_status')
            self.assertIsNone(self.row()['denied'])

    def test_conflicting_header_first_status_is_explicit(self):
        self.db.execute("UPDATE payer_claim SET first_status='paid' WHERE payer_claim_id='timely-0'")
        self.assertEqual(self.row()['label_status'],'first_status_conflict')

    def test_missing_header_status_does_not_erase_explicit_timely_source(self):
        self.db.execute("UPDATE payer_claim SET first_status=NULL WHERE payer_claim_id='timely-0'")
        self.assertEqual(self.row()['denied'],1)

    def test_void_future_and_scopes(self):
        self.assertEqual(len(self.rows()),96)
        self.assertEqual(D.load(self.db,[],self.p,ASOF),[])
        self.assertEqual(D.load(self.db,['other-p'],self.p,ASOF),[])
        self.assertEqual(len(D.load(self.db,['authored-p'],self.p,ASOF)),96)
        self.assertEqual(self.rows(payer='other-payer'),[])
        self.assertEqual(self.rows(product='other-product'),[])

    def test_dates_on_origin_are_inclusive(self):
        self.db.execute("UPDATE payer_adjudication SET available_date=? WHERE payer_claim_id='timely-0'",(CUTOFF,))
        self.assertEqual(self.row()['denied'],1)

    def test_report_cannot_qualify_insufficient_or_unarchived_features(self):
        report = D.run(self.db,None,self.p,ASOF)
        self.assertEqual(report['status'],'partial')
        self.assertEqual(report['validation']['status'],'insufficient')
        self.assertEqual(report['validation']['train_claims'],6)
        self.assertNotIn('auroc',report['validation'])
        self.assertEqual(report['refit_claims'],96)
        self.assertEqual(len(report['rows']),25)
        self.assertIn('unqualified',D.headline(report))
        answer = D.answer(report,{'coverage':'supported_observation'},'Predict denials')
        self.assertEqual(answer['status'],'partial')
        self.assertIsNone(answer['overall'].get('number'))

    def test_unknown_outcomes_do_not_pollute_observed_denominator(self):
        report = D.run(self.db,None,self.p,CUTOFF)
        self.assertEqual((report['claims'], report['labelled_claims'], report['unknown_first_pass'], report['denied_first_pass']), (66,6,60,3))
        self.assertIn('3 of 6 known first outcomes denied (50.0%)',D.headline(report))
        self.assertIn('60 outcomes unknown',D.headline(report))

    def test_recent_pending_claim_is_scored_as_unknown_not_paid(self):
        self.db.execute("UPDATE payer_adjudication SET available_date='2026-10-01' WHERE payer_claim_id='evaluation-0'")
        report = D.run(self.db,None,{**self.p,'top':500},ASOF)
        row = next(r for r in report['rows'] if r['claim_id']=='evaluation-0')
        self.assertEqual(row['actual_first_pass'],'unknown')
        self.assertIsNone(row['actual_carc'])
        self.assertEqual(report['scored']['unknown_outcomes'],1)

    def test_no_recent_claims_does_not_silently_score_older_window(self):
        report = D.run(self.db,None,{**self.p,'score_days':1},ASOF)
        self.assertEqual(report['scored']['claims'],0)
        self.assertEqual(report['rows'],[])

    def test_sufficient_split_yields_only_retrospective_metrics(self):
        # Move 44 existing late labels AND their header availability before origin.
        # This is a separate authored timing control, not a tuned model candidate.
        for i in range(44):
            cid='late-label-'+str(i)
            self.db.execute("UPDATE payer_adjudication SET adjudication_date='2026-03-21',available_date='2026-03-22' WHERE payer_claim_id=?",(cid,))
            self.db.execute("UPDATE payer_claim SET last_available_date='2026-03-22' WHERE payer_claim_id=?",(cid,))
        report = D.run(self.db,None,self.p,ASOF)
        self.assertEqual(report['validation']['train_claims'],50)
        self.assertEqual(report['validation']['status'],'retrospective_only')
        self.assertIn('auroc',report['validation'])
        self.assertEqual(report['validation']['prospective_status'],'unqualified')
        self.assertEqual(report['status'],'partial')

    def test_empty_scope_retains_unknown_rate(self):
        report=D.run(self.db,[],self.p,ASOF)
        self.assertEqual(report['claims'],0)
        self.assertIn('rate unknown',D.headline(report))

    def test_native_templates_compile_without_legacy_acceptance_corpus(self):
        for question in D.TEMPLATES:
            plan, _ = D.route(question, {}, ASOF)
            self.assertEqual(plan['nodes'][0]['operation'],D.OP)
            self.assertNotIn('validated', plan['nodes'][0].get('purpose',''))

    def test_native_execute_and_answer_keep_temporal_limits(self):
        plan, _ = D.route('Predict which claims will get denied', {}, ASOF)
        self.assertEqual(plan['nodes'][0]['operation'],D.OP)
        with patch.object(D.rc,'connect',return_value=self.db):
            report = D.execute('authored-in-memory',[],plan['nodes'][0]['parameters'],ASOF)
        answer = D.answer(report,{'coverage':'supported_observation'},'Predict which claims will get denied')
        self.assertEqual(answer['status'],'partial')
        self.assertIn('unqualified',answer['value'])
        self.assertTrue(answer['limitations'])

    def test_source_witness_clocks_are_preserved(self):
        self.db.execute("UPDATE payer_adjudication SET available_date='2026-03-12T09:00:00Z' WHERE payer_claim_id='timely-0'")
        row=self.row()
        self.assertEqual(row['label_witnesses'][0]['available_date'],'2026-03-12T09:00:00Z')
        self.assertEqual(row['label_origin'],CUTOFF)


if __name__=='__main__': unittest.main()
