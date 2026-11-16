"""Wrong-answer reports: the method that gave the answer stops answering until a reviewer settles it; time to fix is measured;
question text stays out of the hosted audit log. No model is called."""
import os
import tempfile
import unittest
from pathlib import Path

from clinical_core.agere.hosted.auth import question_ref
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


if __name__ == '__main__':
    unittest.main()
