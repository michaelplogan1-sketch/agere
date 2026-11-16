import sqlite3
import unittest
import test_chart_reader as charts
from clinical_core.agere.clinical import chart_reader_op as op, chart_reader as CR, chart_intents as CI
from tools.dev_set.primary_audit import audit

class AdmissionTie(unittest.TestCase):
    def setUp(self):
        charts.Base.setUpClass()
        self.addCleanup(charts.Base.tearDownClass)
        self.fixture = charts.Base()
        with sqlite3.connect(self.fixture.stack.path / 'semantic.sqlite3') as db:
            db.execute("INSERT INTO utilization_event SELECT patient_id,kind,event_date,'2029-12-21',discharge_known,sources,in_ehr,source_records,'zz-latest',organization_id,readmitted_30d,days_to_readmission FROM utilization_event WHERE record_id='u4'")

    def test_admission_date_tie_does_not_choose_an_arbitrary_discharge(self):
        answer = self.fixture.ask('When was the latest 2029 admission of {P} discharged?')
        self.assertEqual(answer['answer_type'], 'none')
        self.assertIsNone(answer['value'])
        self.assertEqual({row['discharge_date'] for row in answer['table']}, {'2029-12-21', '2029-12-27'})
        self.assertEqual(len(answer['sources']), 2)
        self.assertTrue(answer['facts']['ambiguous_latest_admission'])

    def test_explicit_latest_discharge_is_still_a_different_question(self):
        self.assertEqual(self.fixture.ask('Latest discharge date for {P}?')['value'], '2029-12-27')

    def test_primary_metadata_preserves_unknown_instead_of_table_dates(self):
        question = f'When was the latest 2029 admission of {charts.RICH} discharged?'
        chart = CR.typed_chart(CI.parse(question, charts.AS_OF))
        params = {'patient_id': charts.RICH, 'question': question, 'subject_quote': charts.RICH, 'chart': chart}
        report = op.execute(self.fixture.stack, [charts.RICH], params, charts.AS_OF)
        answer = op.answer(report, {'coverage': 'supported_observation'}, question)
        self.assertEqual(answer['primary_answer_type'], 'none')
        self.assertIsNone(answer['primary_value'])
        self.assertEqual(audit({'split': 'authored', 'answer_type': 'date', 'key': {'answer': '2029-12-27'}}, {'answer': answer})['verdict'], 'unverified')
