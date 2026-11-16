"""Follow-up narrowing of a saved cohort (FM-20): demographic, payer, condition, lab and period narrowing is deterministic, no model."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import test_shared_planner as fixtures
from clinical_core.agere.clinical import request_language as L, request_plan as R, shared_planner as S
from clinical_core.agere.deliverables import answer_context as AC

COHORT = ['A', 'B', 'C', 'D']  # everyone but OUTSIDE: the saved cohort the follow-ups narrow


class Narrowing(unittest.TestCase):
    def setUp(self):
        fixtures.SharedPlanner.setUp(self)
        with sqlite3.connect(self.path / 'semantic.sqlite3') as db:
            db.executescript("""
                ALTER TABLE visit ADD COLUMN encounter_id;
                UPDATE visit SET encounter_id = rowid;
                CREATE TABLE measurement(patient_id, concept, value_canonical, result_current, event_date);
                INSERT INTO measurement VALUES ('A','hba1c',9.5,1,'2029-03-01'),('B','hba1c',7.0,1,'2029-03-01'),
                    ('C','hba1c',10.0,0,'2029-03-01'),('D','hba1c',9.1,1,'2028-03-01');""")
        self.catalog = R.Catalog.load(self.path)
        self.addCleanup(self.doCleanups)

    ask = fixtures.SharedPlanner.ask

    def narrow(self, question, population=COHORT):
        out = self.ask(question, population=list(population))
        self.assertEqual(out['answer']['status'], 'answered', question)
        return out['answer']['patient_ids']

    def test_demographic_payer_condition_lab_and_period_narrowing(self):
        self.assertEqual(self.narrow('Of those, which are women?'), ['A', 'C'])
        self.assertEqual(self.narrow('Of those, which are men?'), ['B', 'D'])
        self.assertEqual(self.narrow('Which of those are on Medicare?'), ['A', 'C'])
        self.assertEqual(self.narrow('Of those, which are women?', ['A', 'B']), ['A'])  # only the saved cohort is ever searched
        self.assertEqual(self.narrow('Who among them has diabetes?'), ['A', 'B', 'C'])
        self.assertEqual(self.narrow('Of those, who have a history of diabetes?'), ['A', 'B', 'C'])
        self.assertEqual(self.narrow('And of those, which had an A1c above 9?'), ['A', 'D'])
        self.assertEqual(self.narrow('Of those, which had an A1c above 9 in 2029?'), ['A'])
        self.assertEqual(self.narrow('Of those, which had a visit in 2029?'), ['A', 'B', 'C'])

    def test_mapped_lab_label_followed_by_a_comparison(self):  # rq-05 crashed: \b at the start of the slice after the label
        parser = L.Parser(self.catalog, 'x'); parser.labs = {'hba1c': 'hba1c'}
        table, _, atoms, quote, tail = parser.thing('HbA1c above 9')
        self.assertEqual((table, len(atoms), quote, tail.strip()), ('measurement', 1, 'HbA1c', 'above 9'))

    def test_age_uses_the_governed_definition_and_chains_compose(self):
        self.assertEqual(self.narrow('And which of them are over 65?'), ['A', 'C'])   # B turns 65 on the as-of date: not over 65
        women = self.narrow('Of those, which are women?')
        self.assertEqual(self.narrow('And which of them are over 65?', women), ['A', 'C'])
        self.assertEqual(self.narrow('Of those, which are women and on Medicare?'), ['A', 'C'])

    def test_counts_over_the_cohort_and_unscoped_questions_are_unchanged(self):
        self.assertEqual(self.ask('How many of them are women?', population=COHORT)['answer']['count'], 2)
        self.assertEqual(self.ask('Which patients are women?')['answer']['patient_ids'], ['A', 'C', 'OUTSIDE'])

    def test_unrecognised_narrowing_is_still_declined_not_widened(self):
        with self.assertRaises(R.PlanError):  # the grammar declines; nothing is rewritten into a whole-population question
            L.parse(self.catalog, 'Of those, which are frobnicated?', bound=True)

    def test_sex_words_resolve_to_the_stored_code(self):
        parser = L.Parser(self.catalog, 'x')
        self.assertEqual([parser.sex_value(w) for w in ('women', 'Female', 'men', 'male', 'unknown')], ['F', 'F', 'M', 'M', None])
        self.assertEqual(parser.observed_literal('patient.sex_reported', 'female'), 'F')


class SavedCohortValidity(unittest.TestCase):
    """A system quarantine does not invalidate the exact saved membership for a follow-up; a user correction does."""

    def load(self, corrections, **kw):
        with tempfile.TemporaryDirectory() as d:
            key = 'ANS-' + 'a' * 24
            (Path(d) / 'answers' / key).mkdir(parents=True)
            data = {'source': 'S', 'question': 'Which members have an open gap?', 'planner_receipt': {'route': 'cohort_family', 'version': 'v'},
                    'context_policy_identity': 'ACTIVE', 'patient_ids': ['A']}
            (Path(d) / 'answers' / key / 'answer.json').write_text(json.dumps(data))
            with patch.object(AC, 'root', lambda p: Path(p) / 'answers'), patch.object(AC, 'source_identity', lambda p: 'S'), \
                    patch.object(AC, 'scope_options', lambda c: {}), patch.object(AC, 'supports_patient_work', lambda c: True), \
                    patch('clinical_core.agere.clinical.shared_planner.policy_identity', lambda: 'ACTIVE'), \
                    patch('clinical_core.agere.clinical.answer_dependencies.validate', lambda *a, **k: {'derivation_current': True, 'observation_current': True}), \
                    patch('clinical_core.agere.clinical.cohort_families.scope_rejection', lambda q: None), \
                    patch('clinical_core.agere.clinical.learning.correction_context', lambda p, q: corrections), \
                    patch('clinical_core.agere.clinical.learning.feedback_identity', lambda p, q: 'now'):
                return AC.load(d, key, current=True, **kw)

    def test_quarantine_only_blocks_new_work_not_a_follow_up(self):
        quarantine = [{'note': 'quarantine: QC-METRIC', 'status': 'needs_revalidation'}]
        with self.assertRaisesRegex(ValueError, 'invalidated'): self.load(quarantine)
        self.assertEqual(self.load(quarantine, follow_up=True)['patient_ids'], ['A'])

    def test_a_user_correction_still_invalidates_a_follow_up(self):
        with self.assertRaisesRegex(ValueError, 'invalidated'):
            self.load([{'note': 'quarantine: QC-METRIC'}, {'note': 'this cohort is wrong', 'status': 'needs_revalidation'}], follow_up=True)


if __name__ == '__main__':
    unittest.main()
