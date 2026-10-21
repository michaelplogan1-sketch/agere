"""Reader-facing answers: dollars not cents, organization names next to IDs, rows unchanged."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from clinical_core.agere.clinical.answer_format import present


class AnswerFormat(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.target = Path(self.temp.name) / 'semantic.sqlite3'
        with sqlite3.connect(self.target) as db:
            db.executescript("CREATE TABLE organization(organization_id TEXT, display_name TEXT);"
                             "INSERT INTO organization VALUES ('H1','North hospital'),('H2','South hospital');")

    def test_scalar_sum_of_cents_is_dollars(self):
        result = {'request_contract': {'request': {'select': [{'kind': 'sum', 'field': 'claim.paid_cents', 'alias': 'sum_paid_cents'}]}},
                  'answer': {'value': 311668207, 'headline': '311668207 — sum paid cents', 'limitations': []}}
        a = present(result, self.target)['answer']
        self.assertEqual((a['value'], a['value_cents'], a['unit']), (3116682.07, 311668207, 'USD'))
        self.assertEqual(a['headline'], '$3,116,682.07 — sum paid')

    def test_counts_are_untouched(self):
        result = {'request_contract': {'request': {'select': [{'kind': 'count', 'alias': 'n'}]}},
                  'answer': {'count': 12, 'headline': '12 — n', 'limitations': []}}
        self.assertEqual(present(result, self.target)['answer'], {'count': 12, 'headline': '12 — n', 'limitations': []})

    def test_table_money_and_organization_names(self):
        rows = [{'hospital': 'H1', 'admissions': 34, 'paid_cents': 1050}, {'hospital': 'H2', 'admissions': 31, 'paid_cents': None}]
        a = present({'answer': {'table': rows, 'headline': '2 rows · hospital, admissions, paid_cents'}}, self.target)['answer']
        self.assertEqual(a['table'], [{'hospital': 'H1', 'hospital_name': 'North hospital', 'admissions': 34, 'paid_dollars': 10.5},
                                      {'hospital': 'H2', 'hospital_name': 'South hospital', 'admissions': 31, 'paid_dollars': None}])
        self.assertEqual(a['headline'], '2 rows · hospital, hospital_name, admissions, paid_dollars')

    def test_partial_name_match_adds_nothing(self):
        rows = [{'label': 'H1'}, {'label': 'other'}]
        self.assertEqual(present({'answer': {'table': rows}}, self.target)['answer']['table'], rows)


    def test_null_requested_value_is_not_answerable(self):
        a = present({'answer': {'status': 'partial', 'answer_type': 'value', 'value': None,
                                'missing': ['CAHPS survey responses: no such records in the received data.']}}, self.target)['answer']
        self.assertIs(a['answerable'], False)
        self.assertTrue(a['headline'].startswith('Not answerable from the received data: CAHPS'))

    def test_table_that_only_states_absence_is_not_answerable(self):
        rows = [{'patients': None, 'panel_seen': 1, 'note': 'blood type result values not observable in received data'}]
        a = present({'answer': {'status': 'answered', 'answer_type': 'table', 'table': rows, 'missing': []}}, self.target)['answer']
        self.assertIs(a['answerable'], False)
        self.assertIn('not observable', a['headline'])

    def test_real_answers_stay_answerable(self):
        rows = [{'hospital': 'H1', 'n': 3, 'note': 'complete'}]
        a = present({'answer': {'status': 'answered', 'answer_type': 'table', 'table': rows}}, self.target)['answer']
        self.assertNotIn('answerable', a)
        self.assertNotIn('answerable', present({'answer': {'status': 'answered', 'count': 0}}, self.target)['answer'])

if __name__ == '__main__':
    unittest.main()
