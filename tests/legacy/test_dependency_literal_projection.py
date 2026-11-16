"""Native JSON payloads are data, never dependency identifiers."""
import json
import unittest
from unittest.mock import patch
from clinical_core.agere.clinical import plan_fence as P


class DependencyLiteralProjection(unittest.TestCase):
    def test_actual_native_shape_keeps_real_tables_and_qualified_columns(self):
        data = json.dumps([['FROM fake_patient JOIN fake_payer', 'patient.fake_column']]).replace("'", "''")
        sql = "SELECT p.patient_id,j.value FROM main.patient p JOIN json_each('" + data + "') j ON p.patient_id=json_extract(j.value,'$[0]')"
        self.assertEqual(P._parsed_dependencies((sql,)), (('patient',), (('patient', 'patient_id'),)))
        self.assertIn("json_each('')", P._dependency_sql(sql))
        self.assertIn("'$[0]'", P._dependency_sql(sql))
        self.assertIn(data, P.premises({'nodes': [{'parameters': {'query': sql}}]})['text'])

    def test_quoted_identifiers_and_sqlite_single_quoted_table_names_are_unchanged(self):
        cases = ["SELECT p.patient_id FROM 'patient' AS p", 'SELECT p."patient_id" FROM "patient" AS p',
                 'SELECT p.`patient_id` FROM `patient` AS p', 'SELECT p.[patient_id] FROM [patient] AS p',
                 "SELECT p.patient_id AS 'label' FROM patient p"]
        for sql in cases:
            with self.subTest(sql=sql):
                self.assertEqual(P._dependency_sql(sql), sql)
                self.assertEqual(P._parsed_dependencies((sql,))[0], ('patient',))

    def test_function_text_inside_literals_identifiers_and_comments_is_inert(self):
        cases = ["SELECT 'json_each(''not data'')' AS text FROM patient",
                 'SELECT "json_each(\'not data\')" FROM patient',
                 "SELECT 1 FROM patient -- json_each('not data')\n",
                 "SELECT 1 FROM patient /* json_each('not data') */"]
        for sql in cases:
            with self.subTest(sql=sql):
                self.assertEqual(P._dependency_sql(sql), sql)

    def test_comments_case_whitespace_and_escaped_apostrophes_preserve_structure(self):
        sql = "SELECT p.patient_id FROM patient p JOIN JSON_TREE ( /* comment */ -- line\n '[\"O''Brien\",\"JOIN absent\"]', '$') j ON 1=1"
        shortened = P._dependency_sql(sql)
        self.assertIn("JSON_TREE ( /* comment */ -- line\n '', '$')", shortened)
        self.assertEqual(P._parsed_dependencies((sql,))[0], ('patient',))
        self.assertEqual(P._parsed_dependencies((sql,))[1], (('patient', 'patient_id'),))

    def test_expressions_parameters_and_blobs_are_not_literal_json_payloads(self):
        for sql in ["SELECT * FROM json_each(data || 'suffix')", "SELECT * FROM json_each(:data)",
                    "SELECT * FROM json_each(X'5B5D')", "SELECT * FROM json_each(('[]'))"]:
            with self.subTest(sql=sql):
                self.assertEqual(P._dependency_sql(sql), sql)

    def test_cte_aliases_and_nested_real_dependencies_remain(self):
        sql = "WITH report AS (SELECT p.patient_id FROM patient p JOIN json_each('[1,2]') j ON 1=1) SELECT r.patient_id,c.amount FROM report r JOIN claim c ON c.patient_id=r.patient_id"
        tables, columns = P._parsed_dependencies((sql,))
        self.assertEqual(tables, ('claim', 'patient'))
        self.assertIn(('claim', 'patient_id'), columns)
        self.assertIn(('claim', 'amount'), columns)
        self.assertIn(('patient', 'patient_id'), columns)

    def test_data_size_does_not_change_dependencies_or_original_query(self):
        sql = "SELECT p.patient_id FROM patient p JOIN json_each('" + json.dumps([{'source': 'text ' * 100000}]) + "') j ON 1=1"
        original = sql
        self.assertLess(len(P._dependency_sql(sql)), 150)
        self.assertEqual(P._parsed_dependencies((sql,)), (('patient',), (('patient', 'patient_id'),)))
        self.assertEqual(sql, original)

    def test_unclosed_sql_literal_still_fails(self):
        import sqlglot
        sql = "SELECT * FROM patient JOIN json_each('unterminated) j"
        self.assertEqual(P._dependency_sql(sql), sql)
        with self.assertRaises(sqlglot.errors.TokenError):
            P._parsed_dependencies((sql,))

    def test_nested_and_quoted_generated_sql_matches_the_original_parser(self):
        payloads = [json.dumps([{'text': 'FROM ghost JOIN phantom; \'quoted\' " [ ]'}]),
                    json.dumps([{'text': '/* json_each( -- */'}, {'value': 7}]), '[]']
        templates = [
            'SELECT p."patient_id",j.value FROM "patient" p JOIN json_each({data}) j ON 1=1',
            'WITH x AS (SELECT p.patient_id FROM patient p JOIN json_tree({data} || p.payload) j ON 1=1) SELECT * FROM x',
            'SELECT p.patient_id FROM patient p WHERE EXISTS (SELECT 1 FROM json_each(/* literal */ {data}) j)',
            'SELECT p.[patient_id] FROM [patient] p JOIN json_each({data},\'$\') j ON 1=1',
        ]
        for payload in payloads:
            for template in templates:
                sql = template.format(data="'" + payload.replace("'", "''") + "'")
                with self.subTest(sql=sql):
                    P._parsed_dependencies.cache_clear()
                    with patch.object(P, '_dependency_sql', lambda text: text):
                        original = P._parsed_dependencies((sql,))
                    P._parsed_dependencies.cache_clear()
                    self.assertEqual(P._parsed_dependencies((sql,)), original)


if __name__ == '__main__':
    unittest.main()
