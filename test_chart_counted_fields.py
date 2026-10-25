import sqlite3
import unittest
import test_chart_reader as charts

class CountedFields(unittest.TestCase):
    def setUp(self):
        charts.Base.setUpClass()
        self.addCleanup(charts.Base.tearDownClass)
        self.fixture = charts.Base()

    def test_classes_are_distinct_and_do_not_replace_fill_counts(self):
        for text in ('How many different drug classes did {P} fill in the last year?',
                     'How many distinct medication classes did {P} fill in the last year?',
                     'How many classes of drugs did {P} fill in the last year?'):
            answer = self.fixture.ask(text)
            self.assertEqual(answer['value'], 2, text)
            self.assertEqual(answer['facts']['fills'], 4)
        self.assertEqual(self.fixture.ask('How many fills did {P} have in the last year?')['value'], 4)
        self.assertEqual(self.fixture.ask('How many statin fills did {P} have in the last year?')['value'], 3)

    def test_unclassified_fills_do_not_create_a_fictitious_exact_class_count(self):
        with sqlite3.connect(self.fixture.stack.path / 'semantic.sqlite3') as db:
            db.execute("UPDATE fill SET drug_class=NULL WHERE fill_id=4")
        answer = self.fixture.ask('How many drug classes did {P} fill in the last year?')
        self.assertEqual(answer['answer_type'], 'none')
        self.assertIsNone(answer['value'])
        self.assertEqual(answer['facts']['unclassified_fills'], 1)
        self.assertEqual(answer['facts']['distinct_recorded_drug_classes'], 1)

    def test_recorded_death_date_and_no_date_are_distinct(self):
        self.fixture.ask('Is '+charts.DEAD+' still alive?', conversation_id='death-date')
        answer = self.fixture.ask('when did they die?', conversation_id='death-date')
        self.assertEqual(answer['value'], '2029-03-23')
        self.assertEqual(answer['answer_type'], 'date')
        self.assertEqual(self.fixture.ask('When did {P} die?')['answer_type'], 'none')
        with sqlite3.connect(self.fixture.stack.path / 'semantic.sqlite3') as db:
            db.execute('UPDATE death SET sources_disagree=1')
        self.assertEqual(self.fixture.ask('When did '+charts.DEAD+' die?')['answer_type'], 'none')
