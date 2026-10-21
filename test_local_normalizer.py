"""Local normalizer gate: a rewording is used only when the guard passes and the free parser compiles it."""
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from clinical_core.agere.clinical import local_normalizer as N, shared_planner as S
from clinical_core.agere.clinical.question_input import QuestionNotUnderstood


class NormalizerGate(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        with sqlite3.connect(self.path / 'semantic.sqlite3') as db:
            db.executescript('''
            CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO meta VALUES ('version','semantic-layer/fixture'),('as_of','2030-05-27');
            CREATE TABLE patient(patient_id TEXT, birth_date TEXT);
            INSERT INTO patient VALUES ('A','1950-01-01'),('B','1990-01-01');
            CREATE TABLE condition(patient_id TEXT, concept TEXT, event_date TEXT, is_diagnosis INTEGER);
            INSERT INTO condition VALUES ('A','diabetes','2028-01-01',1),('B','diabetes','2028-01-01',1);
            ''')
        self.stack = SimpleNamespace(path=self.path, allow_live=False, shared_request_planning=True)
        env = patch.dict(os.environ, {'AGERE_LOCAL_NORMALIZER': 'on'}); env.start(); self.addCleanup(env.stop)

    def ask(self, question, proposal):
        with patch.object(N, 'propose', return_value=proposal):
            return S.compose(self.stack, question)

    def test_guarded_rewording_answers_and_is_disclosed(self):
        r = self.ask('DM pts over 65 — count?', 'How many patients over 65 have diabetes?')
        self.assertEqual(r['answer']['count'], 1)
        self.assertEqual(r['answer']['interpreted_as'], 'How many patients over 65 have diabetes?')
        self.assertEqual(r['new_paid_api_calls'], 0)

    def test_changed_number_negation_or_unsupported_declines(self):
        for proposal in ('How many patients over 60 have diabetes?', 'How many patients over 65 do not have diabetes?',
                         N.UNSUPPORTED, 'gibberish the parser cannot read'):
            with self.subTest(proposal=proposal), self.assertRaises(QuestionNotUnderstood):
                self.ask('DM pts over 65 — count?', proposal)

    def test_off_by_default(self):
        with patch.dict(os.environ, {'AGERE_LOCAL_NORMALIZER': 'off'}), self.assertRaises(QuestionNotUnderstood):
            self.ask('DM pts over 65 — count?', 'How many patients over 65 have diabetes?')


if __name__ == '__main__':
    unittest.main()
