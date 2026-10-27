"""General executable-measure checks with explicit authored SQL, no model or bank."""
import unittest
import json
from unittest.mock import Mock
from clinical_core.agere.clinical import literal_check as L


class MeasureInterpretation(unittest.TestCase):
    def gaps(self, question, query):
        return L.missing(question, L.executable_text({'query': query, 'returns': 'value'}))

    def test_percent_alias_does_not_turn_a_count_into_a_percent(self):
        self.assertTrue(self.gaps('What percentage of members are covered?',
                                  'SELECT COUNT(*) AS percentage FROM member WHERE covered=1'))

    def test_average_alias_does_not_turn_a_count_into_an_average(self):
        self.assertTrue(self.gaps('What is the average paid amount?',
                                  'SELECT COUNT(paid_amount) AS mean FROM claim'))

    def test_median_alias_does_not_turn_a_count_into_a_median(self):
        self.assertIsNotNone(L.aggregate_misfit('What is the median age?',
                                               'SELECT COUNT(age) AS median FROM member'))

    def test_cast_and_filtered_count_are_still_counts(self):
        for query in ['SELECT CAST(COUNT(*) AS REAL) AS percentage FROM member',
                      'SELECT COUNT(*) FILTER(WHERE covered=1) AS percentage FROM member']:
            with self.subTest(query=query):
                self.assertIsNotNone(L.aggregate_misfit('What percentage of members are covered?', query))

    def test_ratio_calculation_is_not_rejected(self):
        self.assertEqual(self.gaps('What percentage of members are covered?',
                                  'SELECT 100.0*SUM(covered)/NULLIF(COUNT(*),0) AS answer FROM member'), [])

    def test_average_calculation_is_not_rejected(self):
        self.assertEqual(self.gaps('What is the average paid amount?',
                                  'SELECT AVG(paid_amount) AS mean FROM claim'), [])

    def test_received_percentage_column_is_not_rejected(self):
        self.assertIsNone(L.aggregate_misfit('What percentage was recorded?',
                                             'SELECT recorded_percentage FROM observation'))

    def test_count_of_records_with_percentage_condition_stays_a_count(self):
        self.assertIsNone(L.aggregate_misfit('How many observations recorded an ejection fraction below 40 percent?',
                                             'SELECT COUNT(*) FROM observation WHERE ejection_fraction<40'))

    def test_restated_measure_words_cannot_authorize_count(self):
        text = L.executable_text({'query': 'SELECT COUNT(*) AS percentage FROM member',
                                 'purpose': 'compute percentage with a denominator',
                                 'statement': 'mean percent denominator'})
        self.assertTrue(L.missing('What percentage of members are covered?', text))

    def test_unfamiliar_or_multistep_sql_is_not_given_a_guessed_semantic_verdict(self):
        self.assertIsNone(L.aggregate_misfit('What percentage was recorded?',
                                             'WITH x AS (SELECT COUNT(*) AS n FROM member) SELECT * FROM x'))
        self.assertIsNone(L._single_query(L.executable_text({'steps': [
            {'sql': 'SELECT COUNT(*) FROM member'}, {'sql': 'SELECT recorded_percentage FROM observation'}]})))


class MeasureRepair(unittest.TestCase):
    def test_shared_interpreter_repairs_count_before_accepting_percentage(self):
        import test_shared_planner as fixtures
        from clinical_core.agere.clinical import shared_planner as S, general_analysis as G
        fixtures.SharedPlanner.setUp(self)
        question = 'What percentage of patients are female?'
        queries = ["SELECT COUNT(*) AS percentage FROM patient WHERE sex_reported='F'",
                   "SELECT 100.0*SUM(CASE WHEN sex_reported='F' THEN 1 ELSE 0 END)/COUNT(*) AS percentage FROM patient"]
        proposals = []

        def scripted(system, user, **kwargs):
            if 'result_preview' in json.loads(user):
                # An agreeing model review must not authorize the wrong arithmetic.
                return json.dumps({'equivalent': True, 'issues': []}), {}
            query = queries[min(len(proposals), 1)]
            proposals.append(json.loads(user))
            spec = {'steps': [{'name': 'answer_rows', 'purpose': question, 'sql': query}],
                    'returns': 'value', 'requirements': [{'quote': question, 'applied_in': ['answer_rows']}],
                    'unresolved': []}
            return json.dumps({'analysis': spec}), {}

        contract, _ = S.interpret(self.catalog, question, model=Mock(side_effect=scripted), force_model=True)
        report, _ = G.preview(self.catalog, contract)
        self.assertAlmostEqual(report['value'], 60.0)
        self.assertEqual(len(proposals), 2)
        self.assertIn('only a count', str(proposals[1].get('validator_feedback')))


if __name__ == '__main__':
    unittest.main()
