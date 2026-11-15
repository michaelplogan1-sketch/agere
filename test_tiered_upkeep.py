"""Model-free checks for recipe upkeep: the track record and the rule that decides whether a recipe keeps answering."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from clinical_core.agere.tiered import bottles as bottle_store, upkeep


class Rule(unittest.TestCase):
    def test_one_disagreement_asks_for_a_tie_break_and_never_suspends_on_its_own(self):
        self.assertEqual(upkeep.decide({'check_differ': 1}, 'promoted'), (None, '', True))
        self.assertEqual(upkeep.decide({'check_differ': 1, 'check_agree': 1}, 'promoted'), (None, '', False))

    def test_two_disagreements_outweighing_agreements_suspend_and_later_agreement_restores(self):
        self.assertEqual(upkeep.decide({'check_differ': 2}, 'promoted')[0], 'suspended')
        self.assertIsNone(upkeep.decide({'check_differ': 2, 'check_agree': 2}, 'promoted')[0])   # a tie keeps it answering
        self.assertEqual(upkeep.decide({'check_differ': 2, 'check_agree': 3}, 'suspended')[0], 'promoted')
        self.assertIsNone(upkeep.decide({'check_differ': 2, 'check_agree': 1}, 'suspended')[0])
        self.assertIsNone(upkeep.decide({'check_differ': 5}, 'provisional')[0])                  # only answering recipes are suspended


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
