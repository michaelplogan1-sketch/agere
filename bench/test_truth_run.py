import unittest

from bench.truth_run import contract, table_rows

TABLE = {'id': 'T-1', 'answer_type': 'table'}
PSET = {'id': 'P-1', 'answer_type': 'patient_set'}


def answered(**kw):
    return {'status': 'answered', **kw}


class TableAdapter(unittest.TestCase):
    def test_all_key_columns_are_kept(self):
        out = contract(TABLE, answered(table=[{'from_class': 'ace_inhibitor', 'to_class': 'arb', 'persons': 12},
                                              {'from_class': 'statin', 'to_class': 'arb', 'persons': 3}]))
        self.assertEqual([r['key'] for r in out['table']], ['ace_inhibitor -> arb', 'statin -> arb'])
        self.assertEqual(out['table'][0]['keys'], {'from_class': 'ace_inhibitor', 'to_class': 'arb'})
        self.assertNotIn('table_issues', out)

    def test_rows_differing_only_in_an_outer_key_stay_distinct(self):
        # The old adapter kept only the innermost label, so both rows became key 'arb' and the scorer kept one.
        rows, issues = table_rows([{'lob': 'medicare', 'cls': 'arb', 'n': 5}, {'lob': 'commercial', 'cls': 'arb', 'n': 7}])
        self.assertEqual(len({r['key'] for r in rows}), 2)
        self.assertEqual(issues['duplicate_keys'], [])

    def test_every_measure_column_is_kept_and_flagged(self):
        out = contract(TABLE, answered(table=[{'lead_time': '0-7', 'no_shows': 40, 'pct': 12.5}]))
        row = out['table'][0]
        self.assertEqual(row['values'], {'no_shows': 40, 'pct': 12.5})
        self.assertEqual(row['value'], 12.5)
        self.assertEqual(out['table_issues']['multi_value_rows'], 1)

    def test_numeric_identifier_columns_are_keys(self):
        rows, _ = table_rows([{'year': 2028, 'month': 3, 'stays': 41}])
        self.assertEqual(rows[0]['key'], '2028 -> 3')
        self.assertEqual(rows[0]['value'], 41)

    def test_duplicate_keys_are_reported_not_collapsed(self):
        out = contract(TABLE, answered(table=[{'code': 'CO-45', 'n': 3}, {'code': 'CO-45', 'n': 9}]))
        self.assertEqual(len(out['table']), 2)
        self.assertEqual(out['table_issues']['duplicate_keys'], ['CO-45'])

    def test_rows_without_a_measure_are_kept(self):
        out = contract(TABLE, answered(table=[{'practice': 'North'}, {'practice': 'South', 'n': 2}]))
        self.assertEqual([r['value'] for r in out['table']], [None, 2])
        self.assertEqual(out['table_issues']['rows_without_value'], 1)

    def test_empty_table_is_an_answer_and_missing_table_is_an_abstention(self):
        self.assertEqual(contract(TABLE, answered(table=[]))['status'], 'answered')
        self.assertEqual(contract(TABLE, answered())['status'], 'abstain')


class PatientSetAdapter(unittest.TestCase):
    def test_correctly_empty_patient_set_stays_answered(self):
        out = contract(PSET, answered(patient_ids=[]))
        self.assertEqual((out['status'], out['patient_ids']), ('answered', []))

    def test_patient_ids_from_table(self):
        out = contract(PSET, answered(table=[{'patient_id': 'A'}, {'patient_id': 'B'}]))
        self.assertEqual(out['patient_ids'], ['A', 'B'])

    def test_no_patient_field_is_an_abstention(self):
        self.assertEqual(contract(PSET, answered(count=3))['status'], 'abstain')


class ScalarAdapter(unittest.TestCase):
    def test_zero_count_is_an_answer(self):
        out = contract({'id': 'C-1', 'answer_type': 'count'}, answered(count=0))
        self.assertEqual((out['status'], out['count']), ('answered', 0))

    def test_single_number_table_is_a_count(self):
        out = contract({'id': 'C-1', 'answer_type': 'count'}, answered(table=[{'label': 'all', 'n': 17}]))
        self.assertEqual(out['count'], 17)


if __name__ == '__main__':
    unittest.main()
