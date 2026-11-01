"""Complete native calculations, bounded reading views, and inherited gaps.

Authored financial/event data; no warehouse answer keys or model calls.
"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clinical_core.agere.clinical import backstop_agent as B, result_assembly as R
from clinical_core.agere.clinical.semantic_analyst import SemanticWarehouse, actor_result


class NativeCompleteness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'semantic.sqlite3'
        with sqlite3.connect(self.path) as db:
            db.executescript('''
                CREATE TABLE meta(key,value);
                INSERT INTO meta VALUES('as_of','2030-05-27'),('source_digest','authored-events');
                CREATE TABLE patient(patient_id);
                INSERT INTO patient VALUES('P');
                CREATE TABLE financial_event(event_id,amount,detail);
            ''')
            db.executemany('INSERT INTO financial_event VALUES(?,?,?)',
                           [(i, i + 1, 'received amount detail ' * 8) for i in range(5001)])
        self.wh = SemanticWarehouse(self.path)
        self.addCleanup(self.wh.close)
        self.tools = B.Tools(SimpleNamespace(path=self.root, allow_live=False), self.wh, '2030-05-27')
        self.addCleanup(self.tools.db.close)
        for target in ('socket.socket.connect', 'subprocess.Popen'):
            guard = patch(target, side_effect=AssertionError('Offline native integration cannot dispatch'))
            guard.start()
            self.addCleanup(guard.stop)

    def query(self, sql, **options):
        return self.tools({'name': 'query_sql', 'input': {'sql': sql, **options}})

    def legacy(self):
        return self.tools.retain_result({'columns': ['event_id', 'amount'],
            'rows': [{'event_id': 0, 'amount': 1}, {'event_id': 1, 'amount': 2}], 'truncated': True})

    def test_default_query_preserves_every_row_and_amount_for_downstream_sql(self):
        result = self.query('SELECT * FROM financial_event ORDER BY event_id')
        self.assertEqual(len(result['rows']), 5001)
        self.assertFalse(result['truncated'])
        counted = self.query('SELECT COUNT(*) AS n, SUM(amount) AS total FROM ' + result['result_view'])
        self.assertEqual(counted['rows'], [{'n': 5001, 'total': 5001 * 5002 // 2}])
        self.assertIn('native_sql_contract', result)

    def test_complete_argument_cannot_change_stored_membership(self):
        sql = 'SELECT * FROM financial_event ORDER BY event_id'
        ordinary = self.query(sql, complete=False)
        explicit = self.query(sql, complete=True)
        self.assertEqual(len(ordinary['rows']), 5001)
        self.assertEqual(ordinary['rows'], explicit['rows'])

    def test_large_complete_result_has_a_small_actor_reading_projection(self):
        result = self.query('SELECT * FROM financial_event ORDER BY event_id')
        reading = actor_result(result)
        self.assertLess(len(json.dumps(reading)), 20000)
        self.assertEqual(len(result['rows']), 5001)

    def test_legacy_input_gap_survives_a_complete_count(self):
        source = self.legacy()
        result = self.query('SELECT COUNT(*) AS n FROM ' + source['result_view'])
        self.assertFalse(result['truncated'])  # the one-row aggregate itself is intact
        self.assertTrue(result.get('incomplete_dependencies'))
        self.assertEqual(result['read_views'], [source['result_view']])
        from clinical_core.agere.clinical.execution_state import messages
        state = json.loads(messages('Count received events', self.tools.native_calls)[0]['content'])
        entry = next(row for row in state['executed_results'] if row.get('result_view') == result['result_view'])
        self.assertTrue(entry['incomplete_dependencies'])
        with self.assertRaisesRegex(ValueError, 'incomplete|Incomplete'):
            B.materialize_table({'table_from_tool': result['result_view']}, [{'result': result}])
        with self.assertRaisesRegex(ValueError, 'incomplete|Incomplete'):
            R.compose(self.tools.executed_results, base=result['result_view'], keys=['n'])
        with self.assertRaisesRegex(ValueError, 'incomplete|Incomplete'):
            B.bind_computed_prose({'headline': {'template': '{n} received events', 'bindings': {
                'n': {'result_view': result['result_view'], 'row': 0, 'column': 'n'}}}}, [{'result': result}])

    def test_gap_survives_two_derived_views_and_empty_output(self):
        source = self.legacy()
        middle = self.query('SELECT * FROM ' + source['result_view'] + ' WHERE amount < 0')
        final = self.query('SELECT COUNT(*) AS n FROM ' + middle['result_view'])
        self.assertEqual(final['rows'], [{'n': 0}])
        self.assertTrue(final.get('incomplete_dependencies'))

    def test_direct_scope_consumer_cannot_authorize_from_incomplete_native_input(self):
        source = self.legacy()
        with self.assertRaisesRegex(ValueError, 'incomplete|Incomplete'):
            self.wh.query('SELECT event_id FROM ' + source['result_view'])

    def test_real_reads_include_count_exists_quoted_and_case_insensitive_names(self):
        source = self.legacy()
        name = source['result_view']
        for sql in ['SELECT COUNT(*) AS n FROM ' + name,
                    'SELECT 1 AS n FROM ' + name + ' LIMIT 1',
                    'SELECT EXISTS(SELECT 1 FROM ' + name + ') AS n',
                    'SELECT * FROM ' + name.upper(),
                    'SELECT * FROM temp."' + name + '"',
                    'WITH selected AS (SELECT * FROM ' + name + ') SELECT COUNT(*) AS n FROM selected']:
            with self.subTest(sql=sql):
                result = self.query(sql)
                self.assertEqual(result.get('read_views'), [name])
                self.assertTrue(result.get('incomplete_dependencies'))

    def test_literals_comments_and_shadowing_ctes_are_not_native_dependencies(self):
        name = self.legacy()['result_view']
        for sql in ["SELECT '" + name + "' AS label",
                    'SELECT 1 AS n /* ' + name + ' */',
                    'WITH ' + name + ' AS (SELECT 9 AS n) SELECT * FROM ' + name,
                    'WITH ' + name + ' AS (SELECT amount FROM financial_event) SELECT COUNT(*) AS n FROM ' + name,
                    "WITH " + name + " AS (SELECT value FROM json_each('[7,8]')) SELECT COUNT(*) AS n FROM " + name]:
            with self.subTest(sql=sql):
                result = self.query(sql)
                self.assertEqual(result.get('read_views', []), [])
                self.assertFalse(result.get('incomplete_dependencies'))

    def test_cardinality_mismatch_is_inherited_even_without_a_truncation_flag(self):
        source = self.tools.retain_result({'columns': ['event_id'], 'rows': [{'event_id': 0}],
                                           'table_rows': 2, 'truncated': False})
        derived = self.query('SELECT COUNT(*) AS n FROM ' + source['result_view'])
        self.assertTrue(derived.get('incomplete_dependencies'))

    def test_fresh_request_cannot_inherit_another_request_native_gap(self):
        old = self.legacy()['result_view']
        fresh = B.Tools(SimpleNamespace(path=self.root, allow_live=False), self.wh, '2030-05-27')
        self.addCleanup(fresh.db.close)
        result = fresh({'name': 'query_sql', 'input': {'sql': 'SELECT 3 AS amount'}})
        self.assertEqual(result['result_view'], old)
        self.assertFalse(result.get('incomplete_dependencies'))

    def test_oversized_native_literal_fails_without_registering_a_clipped_view(self):
        self.wh.db.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 1000)
        with self.assertRaises(sqlite3.DatabaseError):
            self.query('SELECT * FROM financial_event')
        self.assertEqual(self.tools.executed_results, {})

    def test_sql_timeout_is_an_error_without_registering_partial_rows(self):
        self.wh.timeout_s = .0001
        with self.assertRaises(sqlite3.DatabaseError):
            self.query('WITH RECURSIVE events(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM events WHERE n<10000000) SELECT SUM(n) AS total FROM events')
        self.assertEqual(self.tools.executed_results, {})

    def test_registered_domain_is_not_limited_to_observed_preview_keys(self):
        out = self.query("WITH medicines(name) AS (VALUES ('spironolactone'),('metoprolol_succinate'),"
                         "('quinapril'),('labetalol'),('unclassified received name')) "
                         "SELECT name, drug_group_member(replace(name,'_',' '),'gdmt') AS member FROM medicines")
        self.assertEqual([row['member'] for row in out['rows']], [1, 1, 1, 0, None])
        self.assertEqual(out['registered_domain_functions'][0]['name'], 'drug_group_member')
        self.assertTrue(out['registered_domain_functions'][0]['registry_identity'])

    def test_unknown_registered_group_fails_instead_of_becoming_a_false_membership(self):
        with self.assertRaises(sqlite3.DatabaseError):
            self.query("SELECT drug_group_member('quinapril','invented_group') AS member")

    def test_portable_receipt_preserves_quoted_case_and_cte_bindings(self):
        source = self.query('SELECT event_id, amount FROM financial_event WHERE event_id < 3')
        for sql in ['SELECT SUM(amount) AS total FROM temp."' + source['result_view'].upper() + '"',
                    'WITH selected AS (SELECT * FROM ' + source['result_view'] + ') SELECT COUNT(*) AS n FROM selected',
                    '/* retained comment */ SELECT COUNT(*) AS n FROM ' + source['result_view']]:
            with self.subTest(sql=sql):
                out = self.query(sql)
                with sqlite3.connect(':memory:') as fresh:
                    fresh.row_factory = sqlite3.Row
                    replayed = [dict(row) for row in fresh.execute(out['executed_query'])]
                self.assertEqual(replayed, out['rows'])


if __name__ == '__main__':
    unittest.main()
