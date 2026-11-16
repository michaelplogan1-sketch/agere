"""Wrong-answer reports: the method that gave the answer stops answering until a reviewer settles it; time to fix is measured;
question text stays out of the hosted audit log. No model is called."""
import os
import tempfile
import unittest
from pathlib import Path

from clinical_core.agere.hosted.auth import AuthError, AuthStore, question_ref
from clinical_core.agere.tiered import bottles as bottle_store, corrections, router


class Corrections(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ['AGERE_TIERED_CORRECTIONS'] = str(Path(self.tmp.name) / 'c.sqlite3')
        self.bottles = bottle_store.Bottles(Path(self.tmp.name) / 'b.sqlite3')

    def tearDown(self):
        os.environ.pop('AGERE_TIERED_CORRECTIONS', None)
        self.tmp.cleanup()

    def saved(self):
        bid, why = self.bottles.add('How many widgets in 2029?', {'sql': 'SELECT :year AS year', 'question_template': 'How many widgets in {year}?',
                                                                  'params': {'year': {'type': 'year', 'value': '2029'}}, 'grain': []},
                                    {'ok': True}, {'tier': 'llm'}, status='promoted')
        self.assertIsNotNone(bid, why)
        return bid

    def test_a_note_is_required(self):
        with self.assertRaises(ValueError):
            corrections.report('How many widgets in 2029?', {'tier': 'llm'}, '  ')

    def test_reported_saved_recipe_stops_answering_until_resolved(self):
        bid = self.saved()
        rid = corrections.report('How many widgets in 2029?', {'tier': 'bottles', 'recipe': {'bottle_id': bid, 'sql': 'SELECT 1'}}, 'counts the wrong year', 'u1', bottles=self.bottles)
        self.assertEqual(next(b['status'] for b in self.bottles.all() if b['id'] == bid), 'reported')
        self.assertIsNone(self.bottles.match('How many widgets in 2029?', statuses=('promoted',)))
        self.assertEqual(corrections.summary()['open'], 1)
        corrections.resolve(rid, 'not_wrong', 'reviewer', 'the definition counts it this way', bottles=self.bottles)
        self.assertEqual(next(b['status'] for b in self.bottles.all() if b['id'] == bid), 'promoted')
        s = corrections.summary()
        self.assertEqual((s['open'], s['resolved'], s['by_resolution']), (0, 1, {'not_wrong': 1}))
        self.assertIsNotNone(s['median_hours_to_resolve'])
        with self.assertRaises(ValueError):
            corrections.resolve(rid, 'fixed', 'reviewer')

    def test_a_saved_recipe_found_wrong_is_retired(self):
        bid = self.saved()
        rid = corrections.report('How many widgets in 2029?', {'tier': 'bottles', 'recipe': {'bottle_id': bid}}, 'wrong table', bottles=self.bottles)
        corrections.resolve(rid, 'fixed', 'reviewer', bottles=self.bottles)
        self.assertEqual(next(b['status'] for b in self.bottles.all() if b['id'] == bid), 'retired')

    def test_reported_built_in_recipe_skips_that_wording_only(self):
        q = 'What was the in-hospital mortality rate in 2029?'
        corrections.report(q, {'tier': 'recipes', 'recipe': {'recipe_id': 'in_hospital_mortality', 'sql': 'SELECT 1'}}, 'should exclude hospice')
        self.assertEqual(corrections.blocked(q), {'in_hospital_mortality'})
        self.assertEqual(corrections.blocked('What was the in hospital mortality rate in 2029'), {'in_hospital_mortality'})   # punctuation aside
        self.assertEqual(corrections.blocked('What was the in-hospital mortality rate in 2028?'), set())
        o = router.answer(q, tiers=('recipes',))
        self.assertNotEqual(o['status'], 'answered')
        self.assertIn('reported_wrong_for_this_question', [s.get('outcome') for s in o['steps']])

    def test_audit_keeps_a_fingerprint_not_the_question(self):
        ref = question_ref('How many visits did patient WHP-90A1F7047375 have?')
        self.assertNotIn('WHP', str(ref))
        self.assertEqual(ref, question_ref('how many visits  did patient WHP-90A1F7047375 have?'))
        self.assertEqual(set(ref), {'question_fingerprint', 'question_chars'})


    def test_hosted_reports_stay_in_their_organization(self):
        bid = self.saved()
        q = 'How many widgets in 2029?'
        tok = corrections.ORG.set('acme')
        try:
            rid = corrections.report(q, {'tier': 'bottles', 'recipe': {'bottle_id': bid}}, 'wrong', bottles=self.bottles)
            self.assertEqual(corrections.blocked(q), {bid})                     # blocked for acme
            self.assertEqual([r['id'] for r in corrections.reports()], [rid])
        finally:
            corrections.ORG.reset(tok)
        self.assertEqual(next(b['status'] for b in self.bottles.all() if b['id'] == bid), 'promoted')   # the shared recipe is not retired
        self.assertEqual(corrections.blocked(q), set())                       # not for the local app
        tok = corrections.ORG.set('globex')
        try:
            self.assertEqual(corrections.blocked(q), set())                   # nor for another organization
            self.assertEqual(corrections.reports(), [])
            with self.assertRaises(KeyError):
                corrections.resolve(rid, 'fixed', 'someone at globex')
        finally:
            corrections.ORG.reset(tok)
        corrections.resolve(rid, 'fixed', 'acme reviewer', org='acme', bottles=self.bottles)
        self.assertEqual(next(b['status'] for b in self.bottles.all() if b['id'] == bid), 'promoted')

    def test_hosted_ledger_keeps_no_question_text(self):
        from clinical_core.agere.tiered import ledger
        os.environ['AGERE_HOSTED'] = '1'
        try:
            led = ledger.Ledger(Path(self.tmp.name) / 'l.sqlite3')
            led.write('ask', 'Visits for patient WHP-90A1F7047375?', {'status': 'answered', 'steps': [], 'final_tier': 'recipes', 'rows': [[1]], 'columns': ['n'],
                                                                    'validation': {'ok': True}, 'recipe': None, 'seconds': 0.1})
            q, rows = led.db.execute('SELECT question, rows_json FROM question').fetchone()
            self.assertTrue(q.startswith('q:') and 'WHP' not in q)
            self.assertIsNone(rows)
        finally:
            os.environ.pop('AGERE_HOSTED', None)


class Members(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = AuthStore(os.path.join(self.tmp.name, 'h.sqlite3'))
        self.org = self.s.create_org('Acme Health')
        self.owner = self.s.create_user('owner@example.com', 'correct horse battery')
        self.admin = self.s.create_user('admin@example.com', 'correct horse battery')
        self.analyst = self.s.create_user('analyst@example.com', 'correct horse battery')
        self.s.add_member(self.org, self.owner, 'owner')
        self.s.add_member(self.org, self.admin, 'admin')
        self.s.add_member(self.org, self.analyst, 'analyst')

    def tearDown(self):
        self.s.close(); self.tmp.cleanup()

    def test_an_admin_cannot_demote_or_remove_the_owner_or_another_admin(self):
        with self.assertRaises(AuthError):
            self.s.add_member(self.org, self.owner, 'viewer', actor=self.admin, actor_role='admin')
        with self.assertRaises(AuthError):
            self.s.remove_member(self.org, self.owner, actor=self.admin, actor_role='admin')
        self.s.add_member(self.org, self.analyst, 'viewer', actor=self.admin, actor_role='admin')   # below the admin: allowed

    def test_the_last_owner_stays(self):
        with self.assertRaises(AuthError):
            self.s.remove_member(self.org, self.owner, actor=self.owner, actor_role='owner')
        self.s.add_member(self.org, self.admin, 'owner', actor=self.owner, actor_role='owner')
        self.s.remove_member(self.org, self.owner, actor=self.admin, actor_role='owner')       # another owner remains


if __name__ == '__main__':
    unittest.main()
