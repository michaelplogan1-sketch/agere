"""Modifier-order development fixtures; no protected reference questions."""
import itertools
import sqlite3
import unittest
from unittest.mock import Mock

import test_shared_planner_correctness as fixtures
from clinical_core.agere.clinical import request_language as L, request_plan as R, shared_planner as S


class ModifierComposition(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.Correctness()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.path, self.stack, self.catalog = self.fixture.path, self.fixture.stack, self.fixture.catalog

    def ask(self, question):
        return S.compose(self.stack, question, model=Mock(side_effect=AssertionError('Model fallback')))['answer']

    def test_governed_period_group_and_exclusion_permutations(self):
        for order in itertools.permutations(['in 2029', 'by payer', 'excluding ckd']):
            question = 'denial rate '+' '.join(order)
            with self.subTest(question=question):
                answer = self.ask(question)
                self.assertEqual(answer['table'], [{'payer': 'P1', 'value': 100*2/3}, {'payer': 'P2', 'value': None}])
                self.assertEqual(answer['computed_as']['period'], {'start': '2029-01-01', 'end': '2029-12-31'})
                self.assertEqual(answer['patient_ids'], ['A', 'B', 'D'])
        for question in ['no-show rate by payer in 2029', 'PMPM by payer in 2029 excluding ckd',
                         'PMPM by payer and sex in 2029', 'PMPM in 2029 grouped by payer and excluding ckd']:
            with self.subTest(question=question):
                self.assertEqual(self.ask(question)['status'], 'answered')

    def test_event_grouping_order_preserves_count_membership_and_source_coverage(self):
        for order in itertools.permutations(['in 2029', 'by payer', 'excluding ckd']):
            question = 'Count ED visits '+' '.join(order)
            with self.subTest(question=question):
                answer = self.ask(question)
                self.assertEqual(answer['table'], [{'payer_id': 'P1', 'count': 3}])
                self.assertEqual(answer['patient_ids'], ['A', 'B'])
                self.assertEqual(answer['source_coverage']['events'], 3)
        self.assertEqual(self.ask('How many ED visits were there in 2029?')['count'], 4)
        self.assertEqual(self.ask('Count claims grouped by payer in 2029')['table'],
                         [{'payer': 'P1', 'count': 3}, {'payer': 'P2', 'count': 1}, {'payer': 'P3', 'count': 1}])

    def test_drug_period_remains_bound_to_medication(self):
        for question in ['patients on a statin by payer in 2029 excluding ckd',
                         'patients on a statin in 2029 by payer excluding ckd']:
            answer = self.ask(question)
            self.assertEqual(answer['table'], [{'payer_id': 'P1', 'patients': 2}])
            self.assertEqual(answer['patient_ids'], ['A', 'B'])
        answer = self.ask('patients on a statin by payer in 2028 excluding ckd')
        self.assertEqual(answer['patient_ids'], [])
        self.assertEqual(answer['table'], [])

    def test_catalog_field_aggregations_and_copular_age(self):
        self.assertEqual(self.ask('How many patients are over 65?')['count'], 3)
        self.assertEqual(self.ask('average age by payer')['table'],
                         [{'payer_id': 'P1', 'avg_age': 67.5}, {'payer_id': 'P2', 'avg_age': 80.0},
                          {'payer_id': 'P3', 'avg_age': 90.0}])
        self.assertEqual(self.ask('total allowed cents by payer in 2029')['table'],
                         [{'payer': 'P1', 'sum_allowed_cents': 35000}, {'payer': 'P2', 'sum_allowed_cents': 0},
                          {'payer': 'P3', 'sum_allowed_cents': 90000}])

    def test_quoted_group_and_period_words_are_values(self):
        with sqlite3.connect(self.catalog.target) as db:
            db.execute("UPDATE patient SET payer_id='by sex in 2028' WHERE patient_id='A'")
            db.execute("UPDATE patient SET payer_id='2029' WHERE patient_id='B'")
        self.assertEqual(self.ask('patients for payer = "by sex in 2028" by sex')['patient_ids'], ['A'])
        answer = self.ask('denial rate for payer = "2029" in 2029')
        self.assertEqual(answer['value'], 100)
        self.assertEqual(answer['patient_ids'], ['B'])
        self.assertEqual(answer['computed_as']['period']['start'], '2029-01-01')

    def test_unsupported_or_ambiguous_clauses_never_disappear(self):
        questions = [
            'denial rate by payer in 2029 excluding preferred mornings',
            'ED visits by payer in 2029 where consent = "yes"',
            'patients on a statin by payer in 2029 excluding ckd and',
            'Count patients by payer, in 2029',
            'Count patients by payer and imaginary',
            'Count patients by payer by sex',
            'Count patients by payer and payer',
            'patients on a statin by payer in 2029 and in 2028',
            'patients by payer in 2029',
            'utilization per 1,000 in 2029',
        ]
        for question in questions:
            with self.subTest(question=question), self.assertRaises(R.PlanError):
                R.compile_request(self.catalog, question, L.parse(self.catalog, question))


if __name__ == '__main__':
    unittest.main()
