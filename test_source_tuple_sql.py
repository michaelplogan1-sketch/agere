"""Dated source references stay one row; authored data, no providers."""
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from clinical_core.agere.clinical import sql_query as Q, backstop_agent as B
from clinical_core.agere.clinical.semantic_analyst import SemanticWarehouse


class SourceTupleSQL(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name); self.path = self.root / 'semantic.sqlite3'
        with sqlite3.connect(self.path) as db:
            db.executescript('''CREATE TABLE meta(key,value);
                INSERT INTO meta VALUES('as_of','2030-05-27'),('source_digest','authored-tuples');
                CREATE TABLE patient(patient_id);
                INSERT INTO patient VALUES('P'),('Q'),('R'),('S');
                CREATE TABLE notes(patient_id,field,date,record_id,quote,amount);
                INSERT INTO notes VALUES
                ('P','medication','2030-04-30','A','new statement',3),
                ('P','medication','2026-09-14','Z','older statement',8),
                ('P','role','2028-01-01','W','role statement',1),
                ('Q','medication','2030-04-30','Z','other patient',5),
                ('R','medication','2030-03-01','T1','tie one',4),
                ('R','medication','2030-03-01','T2','tie two',6),
                ('S','medication',NULL,'N','undated statement',7);''')
        self.wh = SemanticWarehouse(self.path); self.addCleanup(self.wh.close)
        self.wh.define()
        self.tools = B.Tools(SimpleNamespace(path=self.root, allow_live=False), self.wh, '2030-05-27')
        self.addCleanup(self.tools.db.close)
        for name in ('socket.socket.connect', 'subprocess.Popen'):
            guard = patch(name, side_effect=AssertionError('Native tuple checks stay offline'))
            guard.start(); self.addCleanup(guard.stop)

    def test_independent_extrema_do_not_construct_a_source_tuple(self):
        for date_op in ('MAX', 'MIN'):
            for id_op in ('MAX', 'MIN'):
                sql = f'SELECT patient_id,field,{date_op}(date) d,{id_op}(record_id) r FROM notes GROUP BY patient_id,field'
                with self.subTest(sql=sql):
                    with self.assertRaisesRegex(ValueError, 'same source row'):
                        self.wh.query(sql)
                    with self.assertRaisesRegex(ValueError, 'same source row'):
                        Q.execute(self.root, {'query': sql, 'returns': 'table'}, target=self.path)

    def test_error_prevents_retention_and_existing_tool_accepts_local_repair(self):
        bad = 'WITH f AS (SELECT patient_id,MAX(date) d,MAX(record_id) r FROM notes GROUP BY patient_id) SELECT * FROM f'
        with self.assertRaisesRegex(ValueError, 'ROW_NUMBER'):
            self.tools({'name': 'query_sql', 'input': {'sql': bad}})
        self.assertEqual(self.tools.executed_results, {})
        fixed = '''WITH ranked AS (SELECT *,COUNT(*) OVER(PARTITION BY patient_id,field) n,
            ROW_NUMBER() OVER(PARTITION BY patient_id,field ORDER BY date DESC,record_id DESC) rn
            FROM notes) SELECT patient_id,field,n,date,record_id,quote FROM ranked WHERE rn=1 ORDER BY patient_id,field'''
        out = self.tools({'name': 'query_sql', 'input': {'sql': fixed}})
        expected = [('P','medication',2,'2030-04-30','A','new statement'),
                    ('P','role',1,'2028-01-01','W','role statement'),
                    ('Q','medication',1,'2030-04-30','Z','other patient'),
                    ('R','medication',2,'2030-03-01','T2','tie two'),
                    ('S','medication',1,None,'N','undated statement')]
        self.assertEqual([tuple(row[c] for c in out['columns']) for row in out['rows']], expected)
        self.assertIn('native_sql_contract', out)
        self.assertEqual(out['row_count'] if 'row_count' in out else len(out['rows']), 5)
        # The tied source was selected deterministically; every source remains
        # available and an undated finding never becomes a missing finding.
        self.assertEqual(len(self.wh.query('SELECT * FROM notes')['rows']), 7)

    def test_aliases_cte_columns_nested_scopes_and_quoted_identifiers(self):
        bad = [
            'WITH f AS (SELECT date AS d,record_id AS r FROM notes) SELECT MAX(d),MAX(r) FROM f',
            'WITH f(d,r) AS (SELECT date,record_id FROM notes) SELECT MAX(d),MAX(r) FROM f',
            'WITH f AS (SELECT * FROM notes), g AS (SELECT * FROM f) SELECT MAX(date),MIN(record_id) FROM g',
            'SELECT MAX(n."date"),MAX(n."record_id") FROM notes n JOIN patient p ON n.patient_id=p.patient_id',
            'SELECT * FROM (SELECT MAX(date) d,MAX(record_id) r FROM notes)',
            'SELECT patient_id, MAX(date) OVER(PARTITION BY patient_id) d, MAX(record_id) OVER(PARTITION BY patient_id) r FROM notes',
            'SELECT MAX(event_date),MAX(record_id) FROM record',
            'WITH f AS (SELECT event_date AS clock,record_id AS ref FROM record) SELECT MAX(clock),MAX(ref) FROM f',
        ]
        for sql in bad:
            with self.subTest(sql=sql):
                self.assertTrue(Q.source_tuple_issues(sql))

    def test_valid_counts_independent_sources_row_collections_and_fixed_identity(self):
        good = [
            'SELECT patient_id,MAX(date) last_date,COUNT(*) n,SUM(amount) total FROM notes GROUP BY patient_id',
            'SELECT record_id,MAX(date),MAX(record_id) FROM notes GROUP BY record_id',
            'SELECT date,MAX(date),MAX(record_id) FROM notes GROUP BY date',
            'SELECT MAX(a.date),MAX(b.record_id) FROM notes a JOIN notes b ON a.patient_id=b.patient_id',
            'SELECT MAX(amount),MIN(amount),COUNT(*) FROM notes',
            "SELECT json_group_array(json_object('date',date,'record_id',record_id,'quote',quote)) FROM notes",
            "SELECT 'MAX(date),MAX(record_id)' AS text /* SELECT MAX(date),MAX(record_id) FROM notes */",
        ]
        for sql in good:
            with self.subTest(sql=sql): self.assertEqual(Q.source_tuple_issues(sql), [])

    def test_plain_baseline_is_not_given_product_guardrails(self):
        baseline = SemanticWarehouse(self.path); self.addCleanup(baseline.close)
        result = baseline.query('SELECT MAX(date) d,MAX(record_id) r FROM notes WHERE patient_id=\'P\'')
        self.assertEqual(result['rows'], [{'d': '2030-04-30', 'r': 'Z'}])

    def test_shared_planner_inspection_rejects_the_same_invalid_reference(self):
        from clinical_core.agere.clinical import general_analysis as G, request_plan as R
        catalog = SimpleNamespace(target=self.path, tables=self.wh.columns)
        with self.assertRaisesRegex(R.PlanError, 'same source row'):
            G._inspect(catalog, 'SELECT MAX(date) d,MAX(record_id) r FROM notes', None)
        G._inspect(catalog, 'SELECT date,record_id,quote FROM notes ORDER BY date DESC,record_id DESC', None)


if __name__ == '__main__': unittest.main()
