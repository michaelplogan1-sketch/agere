"""Paraphrase reuse: deterministic meaning guards decide; the encoder only ranks; any doubt declines."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import numpy as np

from clinical_core.agere.clinical import paraphrase as P


class FakeEncoder:
    def encode(self, texts):
        return np.stack([np.ones(4) / 2 for _ in texts])  # every pair has similarity 1.0


class Guards(unittest.TestCase):
    def test_rewordings_with_the_same_meaning(self):
        for a, b in [('Size of the COPD population?', 'How many patients have COPD?'),
                     ('What percent of 2029 appointments were no-shows?', 'What was the appointment no-show rate in 2029?'),
                     ('Number of distinct patients with a statin fill during 2029?', 'How many patients filled a statin in 2029?'),
                     ('Number of emergency department visits in calendar year 2029?', 'How many ED visits did we have in 2029?')]:
            with self.subTest(a=a): self.assertTrue(P.same_meaning(a, b))

    def test_meaning_changes_decline(self):
        for a, b in [('How many patients do not have diabetes?', 'How many patients have diabetes?'),
                     ('How many patients have diabetes and CKD?', 'How many patients have diabetes?'),
                     ('How many ED visits did we have in 2028?', 'How many ED visits did we have in 2029?'),
                     ('How many patients had 4+ ED visits?', 'How many patients had ED visits?'),
                     ('Count the diabetics whose latest A1c is over 9.', 'Count the diabetics whose latest A1c is under 9.'),
                     ('Flu shots missed in the 2029-30 season', 'Flu shots missed in the 2028-2029 season'),
                     ('What was the 2029 denial rate for institutional claims only?', 'What was the 2029 claim denial rate?')]:
            with self.subTest(a=a): self.assertFalse(P.same_meaning(a, b))


class Find(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.db = sqlite3.connect(self.path / 'question-plans.sqlite3'); self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE shared_requests(id TEXT PRIMARY KEY,payload TEXT NOT NULL)')

    def save(self, ident, question, request, accepted=True):
        self.db.execute('INSERT INTO shared_requests VALUES (?,?)', (ident, json.dumps(
            {'question': question, 'request': request, 'interpretation': {'execution_accepted': accepted}})))
        self.db.commit()

    def test_reuses_the_matching_verified_question(self):
        self.save('a', 'How many patients have COPD?', {'r': 1})
        self.save('b', 'How many patients have asthma?', {'r': 2})
        wording, item, score = P.find(self.path, 'Size of the COPD population?', encoder=FakeEncoder())
        self.assertEqual((wording, item['request'], score), ('How many patients have COPD?', {'r': 1}, 1.0))

    def test_unverified_and_conflicting_readings_decline(self):
        self.save('a', 'How many patients have COPD?', {'r': 1}, accepted=False)
        self.assertIsNone(P.find(self.path, 'Size of the COPD population?', encoder=FakeEncoder()))
        self.save('b', 'Number of patients with COPD?', {'r': 1})
        self.save('c', 'Count of COPD patients?', {'r': 9})
        self.assertIsNone(P.find(self.path, 'Size of the COPD population?', encoder=FakeEncoder()))


if __name__ == '__main__':
    unittest.main()
