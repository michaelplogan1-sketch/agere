"""Model-free checks for recipe upkeep: the track record and the rule that decides whether a recipe keeps answering."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from clinical_core.agere.tiered import bottles as bottle_store, upkeep


class Rule(unittest.TestCase):
    def test_one_disagreement_asks_the_frontier_model_and_never_suspends_on_its_own(self):
        self.assertEqual(upkeep.decide({'check_differ': 1}, 'promoted'), (None, '', True))
        self.assertEqual(upkeep.decide({'check_differ': 1, 'check_agree': 1}, 'promoted'), (None, '', True))     # a tie the frontier model has not read
        self.assertEqual(upkeep.decide({'check_differ': 1, 'check_agree': 2}, 'promoted'), (None, '', False))

    def test_more_disagreeing_readers_suspend_and_the_frontier_model_breaks_a_tie(self):
        self.assertEqual(upkeep.decide({'check_differ': 2}, 'promoted')[0], 'suspended')
        self.assertEqual(upkeep.decide({'check_differ': 1, 'check_agree': 1, 'frontier': 'differ'}, 'promoted')[0], 'suspended')
        self.assertIsNone(upkeep.decide({'check_differ': 1, 'check_agree': 1, 'frontier': 'agree'}, 'promoted')[0])
        self.assertIsNone(upkeep.decide({'check_differ': 2, 'check_agree': 2, 'frontier': 'agree'}, 'promoted')[0])
        self.assertEqual(upkeep.decide({'check_differ': 2, 'check_agree': 3}, 'suspended')[0], 'promoted')
        self.assertEqual(upkeep.decide({'check_differ': 1, 'check_agree': 1, 'frontier': 'agree'}, 'suspended')[0], 'promoted')
        self.assertIsNone(upkeep.decide({'check_differ': 2, 'check_agree': 1}, 'suspended')[0])
        self.assertIsNone(upkeep.decide({'check_differ': 5}, 'provisional')[0])                  # only answering recipes are suspended
        self.assertEqual(upkeep.decide({'check_differ': 1}, 'suspended'), (None, '', True))

class Layout(unittest.TestCase):
    def test_the_same_figures_in_another_layout_agree_and_different_figures_do_not(self):
        long, wide = [('2028', 41), ('2029', 57)], [(41, 57)]
        self.assertTrue(upkeep.same_numbers(long, wide))
        self.assertTrue(upkeep.same_numbers([(120, 1234.567)], [(120, 1234.57)]))            # rounded to two places
        self.assertTrue(upkeep.same_numbers([('0', 10), ('1', 4)], [('zero', 10), ('one', 4)]))  # labels differ, figures agree
        self.assertFalse(upkeep.same_numbers([(443, 72, 0.1625)], [(505, 106, 0.2099)]))
        self.assertFalse(upkeep.same_numbers([('a',)], [('b',)]))                              # no figures: never agreement
        self.assertTrue(upkeep.same_numbers([('Pathology', 1)], [(1, 0)]))                     # a missing row against a zero
        self.assertFalse(upkeep.same_numbers([(17,)], [(221,)]))
        self.assertTrue(upkeep.same_numbers([(None, None)], [('Pathology', 0), ('Home Health Orders', 0)]))   # both say there is nothing
        self.assertTrue(upkeep.same_numbers([('has', 182), ('none', 618)], [(182, 618, 800)]))                  # a total added
        self.assertFalse(upkeep.same_numbers([('has', 182), ('none', 618)], [(182, 618, 801)]))
        self.assertTrue(upkeep.same_numbers([(3600, 30125.52)], [(30125.515555,)]))           # the count column left out
        self.assertTrue(upkeep.same_numbers([], [('Pathology', 0), ('Home Health Orders', 0)]))   # no rows against zero counts
        self.assertFalse(upkeep.same_numbers([], [('Pathology', 3)]))
        self.assertFalse(upkeep.same_numbers([('2027', 108), ('2028', 192)], [('2027', 108), ('2028', 190)]))
        self.assertTrue(upkeep.still_differs({'where': 'full data', 'saved_first': '[[17]]', 'second_first': '[[221]]'}))
        self.assertFalse(upkeep.still_differs({'where': 'full data', 'saved_first': "[['2028', 41]]", 'second_first': '[[41]]'}))
        self.assertFalse(upkeep.still_differs({'where': 'a patient subset'}))


class TrackRecord(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        with mock.patch.object(bottle_store.store, 'schema_version', lambda: 'v1'):
            self.b = bottle_store.Bottles(self.dir / 'b.sqlite3')
            self.b.db.execute("INSERT INTO bottle(id, template, pattern, params_json, sql, grain_json, status, schema_version) VALUES (1, 'How many in {year}', 'x', '{}', 'SELECT 1', '[]', 'promoted', 'v1')")
            self.b.db.commit()

    def test_uses_checks_and_misapplications_are_counted_per_recipe(self):
        self.b.used(1, 'How many in 2029', {'year': '2029'})
        self.b.used(1, 'How many in 2029', {'year': '2029'})
        self.b.used(1, 'How many in 2028', {'year': '2028'}, via='near')
        self.b.event(1, 'check_agree'); self.b.event(1, 'misapplied'); self.b.event(1, 'misapplied')
        self.assertEqual(self.b.record(1), {'use': 2, 'near_use': 1, 'check_agree': 1, 'check_differ': 0, 'misapplied': 2})
        self.assertEqual([q for q, _ in self.b.uses(1)], ['How many in 2028', 'How many in 2029'])   # distinct, most recent first
        self.assertEqual(self.b.near_blocked(), {1})
        self.assertEqual(self.b.db.execute('SELECT uses FROM bottle WHERE id = 1').fetchone()[0], 3)
        with self.assertRaises(ValueError): self.b.event(1, 'liked')
        self.b.event(1, 'check_differ', detail={'superseded': 'yes'})
        self.assertEqual(self.b.record(1)['check_differ'], 0)                                  # a superseded check is not counted

    def test_the_same_reader_on_the_same_question_counts_once(self):
        for _ in range(3): self.b.event(1, 'check_differ', 'How many in 2029', detail={'model': 'small'})
        self.b.event(1, 'check_agree', 'How many in 2029', detail={'model': 'frontier'})
        st = upkeep.standing(self.b, 1)
        self.assertEqual((st['check_differ'], st['check_agree']), (1, 1))
        self.assertEqual(st['frontier'], None)                       # 'frontier' here is a stand-in name, not the frontier model
        self.assertIsNone(upkeep.decide(st, 'promoted')[0])          # one reader each way: it keeps answering until the frontier model reads it
        self.b.event(1, 'check_differ', 'How many in 2028', detail={'model': 'small'})
        self.assertEqual(upkeep.decide(upkeep.standing(self.b, 1), 'promoted')[0], 'suspended')

    def test_backfill_reads_past_answers_once(self):
        led = sqlite3.connect(self.dir / 'l.sqlite3')
        led.execute('CREATE TABLE question (at, run, question, final_tier, status, recipe_json)')
        led.executemany('INSERT INTO question VALUES (?,?,?,?,?,?)', [('t', 'r', 'How many in 2029', 'bottles', 'answered', json.dumps({'bottle_id': 1, 'params': {'year': '2029'}})),
                                                                       ('t', 'r', 'other', 'llm', 'answered', '{}')])
        led.commit()
        with mock.patch.object(upkeep.bottle_store, 'Bottles', lambda path=None: self.b):
            self.assertEqual(upkeep.backfill(self.dir / 'l.sqlite3'), {'bottles': 1})
            self.assertEqual(upkeep.backfill(self.dir / 'l.sqlite3'), {'already': True})
        self.assertEqual(self.b.record(1)['use'], 1)


if __name__ == '__main__':
    unittest.main()
