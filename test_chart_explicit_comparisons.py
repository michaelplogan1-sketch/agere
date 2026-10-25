import sqlite3
import unittest
import test_chart_reader as charts
from clinical_core.agere.clinical import chart_reader as CR

class ExplicitComparisons(unittest.TestCase):
    def setUp(self):
        charts.Base.setUpClass()
        self.addCleanup(charts.Base.tearDownClass)
        self.fixture = charts.Base()

    def test_paired_bounds_keep_both_components_and_caller_values(self):
        for target, expected in [('140/90', False), ('150/90', True), ('150/80', False), ('142/88', False)]:
            conv = 'bp-' + target
            self.fixture.ask('Latest blood pressure for {P}?', conversation_id=conv)
            answer = self.fixture.ask('is that at goal (under '+target+')?', conversation_id=conv)
            self.assertEqual(answer['value'], expected, target)
            self.assertEqual(answer['answer_type'], 'yes_no')
            self.assertEqual(len(answer['sources']), 2)
            self.assertEqual(len(CR.typed_chart(answer['intent'])['comparisons']), 2)

    def test_scalar_explicit_bound_and_inclusive_equality(self):
        self.assertEqual(self.fixture.ask('Is the latest HbA1c for {P} below 7?')['value'], True)
        self.assertEqual(self.fixture.ask('Is the latest HbA1c for {P} at least 6.8?')['value'], True)
        self.assertEqual(self.fixture.ask('Is the latest HbA1c for {P} above 6.8?')['value'], False)

    def test_no_target_or_patient_is_invented(self):
        self.assertEqual(self.fixture.ask('is that at goal (under 140/90)?', conversation_id='none')['status'], 'unsupported')
        self.assertIsNone(self.fixture.ask('Latest BP for {P}?')['intent'].get('comparisons'))

    def test_missing_censored_and_different_day_values_stay_unknown(self):
        path = self.fixture.stack.path / 'semantic.sqlite3'
        with sqlite3.connect(path) as db:
            db.execute("UPDATE measurement_latest SET event_date='2030-04-02' WHERE concept='diastolic_bp'")
        answer = self.fixture.ask('Is the latest BP for {P} below 150/90?')
        self.assertEqual(answer['answer_type'], 'none')
        self.assertIsNone(answer['value'])
        with sqlite3.connect(path) as db:
            db.execute("UPDATE measurement_latest SET event_date='2030-04-01',value_censor='<' WHERE concept='diastolic_bp'")
        self.assertEqual(self.fixture.ask('Is the latest BP for {P} below 150/90?')['answer_type'], 'none')
        with sqlite3.connect(path) as db:
            db.execute("DELETE FROM measurement_latest WHERE concept='diastolic_bp'")
        self.assertEqual(self.fixture.ask('Is the latest BP for {P} below 150/90?')['answer_type'], 'none')

    def test_units_are_not_silently_reinterpreted(self):
        self.assertEqual(self.fixture.ask('Is the latest HbA1c for {P} below 7%?')['value'], True)
        self.assertEqual(self.fixture.ask('Is the latest HbA1c for {P} below 48 mmol/mol?')['answer_type'], 'none')

    def test_typed_comparison_survives_graph_execution(self):
        from clinical_core.agere.clinical import chart_reader_op as op
        from clinical_core.agere.clinical import chart_intents as intents
        chart = CR.typed_chart(intents.parse(f'Is the latest BP for {charts.RICH} below 150/90?', charts.AS_OF))
        params = {'patient_id': charts.RICH, 'question': f'Is the latest BP for {charts.RICH} below 150/90?',
                  'subject_quote': charts.RICH, 'chart': chart}
        report = op.execute(self.fixture.stack, [charts.RICH], params, charts.AS_OF)
        self.assertIs(report['answer_contract']['value'], True)
        self.assertEqual(report['answer_contract']['intent']['comparisons'], chart['comparisons'])

    def test_time_intervals_are_not_measurement_bounds(self):
        from clinical_core.agere.clinical import chart_intents as intents
        self.assertIsNone(intents.explicit_comparisons('is there an a1c over 12 months?', ['hba1c']))
        self.assertIsNone(intents.explicit_comparisons('is a1c not below 7?', ['hba1c']))

    def test_paired_above_does_not_invent_and_or_meaning(self):
        from clinical_core.agere.clinical import chart_intents as intents
        self.assertIsNone(intents.explicit_comparisons('is bp above 140/90?', ['systolic_bp', 'diastolic_bp']))

    def test_invalid_typed_bounds_are_rejected(self):
        from clinical_core.agere.clinical import chart_intents as intents
        chart = CR.typed_chart(intents.parse('Is the latest BP below 150/90?', charts.AS_OF))
        chart['comparisons'] = chart['comparisons'][:1]
        with self.assertRaises(ValueError): CR.validate_chart(chart)
        chart['comparisons'][0]['value'] = float('nan')
        with self.assertRaises(ValueError): CR.validate_chart(chart)

    def test_latest_comparison_does_not_answer_historical_or_universal_questions(self):
        from clinical_core.agere.clinical import chart_intents as intents
        for question in ('Has A1c ever been above 7?', 'Was the previous A1c below 7?', 'Is BP always below 140/90?'):
            parsed = intents.parse(question, charts.AS_OF)
            self.assertNotEqual(parsed['intent'], 'compare_values', question)
            self.assertIn('comparison_scope', parsed['gaps'])
        prior = {'intent': 'latest_value', 'domain': 'lab', 'concept': {'kind': 'lab', 'value': 'hba1c', 'concepts': ['hba1c']},
                 'mode': 'latest', 'ordinal': 2}
        self.assertIn('comparison_scope', intents.parse('is that below 7?', charts.AS_OF, prior=prior)['gaps'])

    def test_declaring_latest_in_a_model_spec_does_not_override_ever(self):
        from clinical_core.agere.clinical import chart_reader_op as op, chart_intents as intents
        question = f'Has {charts.RICH} ever had HbA1c above 7?'
        chart = CR.typed_chart(intents.parse('Is the latest HbA1c above 7?', charts.AS_OF))
        report = op.execute(self.fixture.stack, [charts.RICH], {'patient_id': charts.RICH,
                            'question': question, 'subject_quote': charts.RICH, 'chart': chart}, charts.AS_OF)
        self.assertEqual(report['answer_contract']['answer_type'], 'none')
        self.assertIsNone(report['answer_contract']['value'])
