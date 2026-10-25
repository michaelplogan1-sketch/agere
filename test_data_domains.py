"""Honest 'not available' for data domains the warehouse never received; anything that might exist plans normally."""
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest

from clinical_core.agere.clinical import data_domains as D, request_plan as R, shared_planner as S


class DataDomains(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        with sqlite3.connect(self.path / 'semantic.sqlite3') as db:
            db.executescript('''
            CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO meta VALUES ('version','semantic-layer/fixture'),('as_of','2030-05-27');
            CREATE TABLE patient(patient_id TEXT);
            CREATE TABLE patient_survey(patient_id TEXT, satisfaction INTEGER);
            ''')
        self.catalog = R.Catalog.load(self.path)

    def test_absent_domain_is_named(self):
        label, reason = D.check(self.catalog, 'How many prior authorization requests were denied in 2029?')
        self.assertIn('prior authorization', label)
        r = S.compose(SimpleNamespace(path=self.path, allow_live=False), 'How many prior auths were denied?')
        self.assertEqual(r['answer']['status'], 'not_available')
        self.assertIs(r['answer']['answerable'], False)
        self.assertEqual(r['new_paid_api_calls'], 0)

    def test_a_field_that_could_hold_it_falls_through(self):
        self.assertIsNone(D.check(self.catalog, 'What were our patient satisfaction scores?'))  # patient_survey exists

    def test_period_after_the_snapshot(self):
        self.assertIn('runs through 2030-05-27', D.check(self.catalog, 'How many ED visits in June 2031?')[1])
        self.assertIsNone(D.check(self.catalog, 'How many ED visits in May 2030?'))

    def test_name_tokens_not_substrings(self):
        with sqlite3.connect(self.path / 'semantic.sqlite3') as db:
            db.execute('CREATE TABLE fill(patient_id TEXT, generic_flag INTEGER, general_health TEXT)')
        catalog = R.Catalog.load(self.path)
        label, _ = D.check(catalog, 'How many patients have a positive BRCA result on file?')  # 'gene' is not found inside 'generic' / 'general'
        self.assertIn('genetic', label)

    def test_post_stage_names_what_was_checked(self):
        q = 'What is the average ED triage acuity (ESI level) of our frequent ED users?'
        self.assertIsNone(D.check(self.catalog, q))  # not stated before planning: the layers get the first try
        label, reason = D.check(self.catalog, q, stage='post')
        self.assertIn('triage', label)
        self.assertIn('Checked 2 tables', reason)
        self.assertIn('triage_level', reason)

    def test_after_decline_states_absence_and_only_for_a_decline(self):
        from clinical_core.agere.clinical.question_input import QuestionNotUnderstood
        q = 'What did the Holter monitor show for patient 12?'
        out = D.after_decline(self.path, q, QuestionNotUnderstood('no plan'))
        self.assertEqual(out['answer']['status'], 'not_available')
        self.assertTrue(out['answer']['data_absence_established'])
        self.assertEqual(out['planner_receipt']['interpretation']['stage'], 'after_decline')
        self.assertIsNone(D.after_decline(self.path, q, RuntimeError('boom')))  # an error is not a decline
        self.assertIsNone(D.after_decline(self.path, 'What is the average HbA1c of our diabetic patients?', QuestionNotUnderstood('x')))

    def test_near_miss_that_exists_is_not_absent(self):
        with sqlite3.connect(self.path / 'semantic.sqlite3') as db:
            db.executescript('''CREATE TABLE measurement(patient_id TEXT, name TEXT, lab_category TEXT);
                INSERT INTO measurement VALUES ('p1', 'ambulatory ECG 24h', 'cardiac');''')
        catalog = R.Catalog.load(self.path)
        self.assertIsNone(D.check(catalog, 'What did the Holter monitor show for patient 12?', stage='post'))  # a received name holds ECG results
        self.assertIsNone(D.check(self.catalog, 'What is the A1c of patient 3?', stage='post'))  # no domain named: never absent

    def test_pharmacy_rejects_are_not_a_missing_prior_authorization_domain(self):
        with sqlite3.connect(self.path / 'semantic.sqlite3') as db:
            db.execute('CREATE TABLE payer_pharmacy_claim(patient_id TEXT, reject_code TEXT)')
        self.catalog = R.Catalog.load(self.path)
        self.assertIsNone(D.check(self.catalog, 'How many pharmacy claims were rejected for prior authorization?', stage='post'))
        self.assertIn('prior authorization', D.check(self.catalog, 'What is the prior authorization approval rate by payer?', stage='post')[0])

    def test_note_text_mention_means_it_exists(self):
        with sqlite3.connect(self.path / 'semantic.sqlite3') as db:
            db.executescript('''CREATE TABLE record(record_id TEXT, stream TEXT, payload TEXT);
                INSERT INTO record VALUES ('r1', 'notes', '{"text": "24-hour Holter showed sinus rhythm"}');''')
        self.assertIsNone(D.check(R.Catalog.load(self.path), 'What did the Holter monitor show?', stage='post'))

    def test_switch_off(self):
        import os
        from unittest import mock
        from clinical_core.agere.clinical.question_input import QuestionNotUnderstood
        with mock.patch.dict(os.environ, {'AGERE_STATED_ABSENCE': 'off'}):
            self.assertIsNone(D.after_decline(self.path, 'What did the Holter monitor show?', QuestionNotUnderstood('x')))


if __name__ == '__main__':
    unittest.main()
