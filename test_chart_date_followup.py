import sqlite3
import unittest
import test_chart_reader as charts
from clinical_core.agere.clinical import chart_reader as CR

class DateFollowup(unittest.TestCase):
    def test_dated_fragment_keeps_established_patient_and_year(self):
        charts.Base.setUpClass()
        self.addCleanup(charts.Base.tearDownClass)
        fixture = charts.Base()
        for i, wording in enumerate(('date of the most recent discharge?', 'Date of the latest discharge?', 'date of the last discharge?')):
            conversation = f'discharge-fragment-{i}'
            fixture.ask('How many hospital admissions did {P} have in 2029?', conversation_id=conversation)
            answer = fixture.ask(wording, conversation_id=conversation)
            self.assertEqual(answer['patient_ids'], [charts.RICH])
            self.assertEqual(answer['value'], '2029-12-27')
        self.assertEqual(fixture.ask('date of the most recent discharge?', conversation_id='no-patient')['status'], 'unsupported')
        self.assertFalse(CR._elliptical('date of the latest discharge by practice?'))
        self.assertFalse(CR._elliptical('date of the latest discharge for all patients?'))

    def test_latest_discharge_uses_discharge_chronology(self):
        charts.Base.setUpClass()
        self.addCleanup(charts.Base.tearDownClass)
        fixture = charts.Base()
        with sqlite3.connect(fixture.stack.path / 'semantic.sqlite3') as db:
            db.execute("UPDATE utilization_event SET end_date='2030-01-02' WHERE patient_id=? AND kind='inpatient' AND event_date='2029-12-20'", (charts.RICH,))
            db.execute("UPDATE utilization_event SET end_date='2030-01-03' WHERE patient_id=? AND kind='inpatient' AND event_date='2029-06-01'", (charts.RICH,))
        answer = fixture.ask('What was the latest discharge date for {P}?')
        self.assertEqual(answer['value'], '2030-01-03')
        self.assertEqual(answer['intent']['flags'], ['order_by_discharge'])
        admission = fixture.ask('When was the most recent 2029 hospital admission of {P} discharged?')
        self.assertEqual(admission['value'], '2030-01-02')
