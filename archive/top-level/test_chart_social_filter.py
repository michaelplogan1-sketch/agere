import copy
import sqlite3
import unittest
import test_chart_reader as charts
from clinical_core.agere.clinical import chart_reader as CR, chart_reader_op as OP, acceptance_contract as AC

class SocialFilter(unittest.TestCase):
    def setUp(self):
        charts.Base.setUpClass(); self.addCleanup(charts.Base.tearDownClass)
        self.fixture = charts.Base()

    def test_guard_sees_the_filter_the_reader_actually_executes(self):
        question = 'Which social needs has '+charts.RICH+' screened positive for?'
        graph, receipt = CR.tier(self.fixture.stack, question)
        chart = graph['nodes'][0]['parameters']['chart']
        self.assertEqual(chart['screen_filter'], {'positive': 1})
        self.assertEqual(AC.evaluate(question, graph=graph, as_of=charts.AS_OF, phase='plan')['unmet'], [])
        missing = copy.deepcopy(graph); missing['nodes'][0]['parameters']['chart'].pop('screen_filter')
        self.assertTrue(any('positive' in item for item in AC.evaluate(question, graph=missing, as_of=charts.AS_OF, phase='plan')['unmet']))
        answer = OP.execute(self.fixture.stack, [charts.RICH], graph['nodes'][0]['parameters'], charts.AS_OF)['answer_contract']
        self.assertEqual(answer['intent']['screen_filter'], {'positive': 1})
        self.assertTrue(answer['table'])

    def test_a_model_cannot_declare_a_different_filter_and_execute_positive_anyway(self):
        question = 'Which social needs has '+charts.RICH+' screened positive for?'
        graph, _ = CR.tier(self.fixture.stack, question)
        params = copy.deepcopy(graph['nodes'][0]['parameters']); params['chart']['screen_filter'] = {'positive': 0}
        with self.assertRaises(ValueError): OP.execute(self.fixture.stack, [charts.RICH], params, charts.AS_OF)

    def test_only_canonical_positive_one_is_reported(self):
        with sqlite3.connect(self.fixture.stack.path / 'semantic.sqlite3') as db:
            db.execute('INSERT INTO social_need VALUES (?,?,?,?,?,?)', (charts.RICH, 'invalid-positive', 2, 'AHC', '2030-01-01', 'invalid-screen'))
        answer = self.fixture.ask('Which social needs has {P} screened positive for?')
        self.assertNotIn('invalid-positive', answer['facts']['needs'])
