"""Received-type arithmetic and encounter-scope regressions; no provider calls."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from clinical_core.agere.clinical import general_analysis as G, request_plan as R, sql_query as Q
from clinical_core.agere.clinical import semantic_analyst as S, literal_check as L, shared_planner as P
from test_schema_prefetch import warehouse


class SqlSemantics(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.path = Path(temp.name); warehouse(self.path)
        with sqlite3.connect(self.path / 'semantic.sqlite3') as db:
            db.execute('CREATE TABLE episodes(patient_id, opened, closed, start_epoch, end_epoch, paid, allowed)')
            db.executemany('INSERT INTO episodes VALUES(?,?,?,?,?,?,?)', [
                ('A', '2029-12-30', '2030-01-02', 100, 260, 80.0, 100.0),
                ('B', '2030-01-30', '2030-02-04', 400, 640, 60.0, 80.0)])
        self.catalog = R.Catalog.load(self.path)
        for name in ('socket.socket.connect', 'socket.create_connection', 'urllib.request.urlopen', 'subprocess.Popen'):
            p = patch(name, side_effect=AssertionError('Semantic fixes stay offline'))
            p.start(); self.addCleanup(p.stop)

    def analysis(self, sql, question='Report average elapsed days from opened to closed.'):
        return {'steps': [{'name': 'elapsed', 'purpose': question, 'sql': sql}], 'final': 'elapsed',
                'returns': 'value', 'requirements': [{'quote': question, 'applied_in': ['elapsed']}], 'unresolved': []}

    def test_raw_date_subtraction_is_repairable_in_planner_executor_and_investigator(self):
        wrong = 'SELECT AVG(closed-opened)/86400.0 AS days FROM episodes'
        with self.assertRaisesRegex(R.PlanError, 'julianday.*elapsed days'):
            G.prepare(self.catalog, 'Report average elapsed days from opened to closed.', self.analysis(wrong))
        with self.assertRaisesRegex(ValueError, 'numeric coercion'):
            Q.execute(self.path, {'query': wrong, 'returns': 'value'}, target=self.catalog.target)
        reader = S.SemanticWarehouse(self.catalog.target); self.addCleanup(reader.close); reader.define()
        with self.assertRaisesRegex(ValueError, 'numeric coercion'):
            reader.query(wrong)
        fixed = 'SELECT AVG(julianday(closed)-julianday(opened)) AS days FROM episodes'
        self.assertEqual(reader.query(fixed)['rows'], [{'days': 4.0}])
        contract = G.prepare(self.catalog, 'Report average elapsed days from opened to closed.', self.analysis(fixed))
        self.assertEqual(G.preview(self.catalog, contract)[0]['value'], 4.0)

    def test_aliases_nested_ctes_quoted_identifiers_and_date_functions_keep_types(self):
        bad = [
            'WITH d AS (SELECT opened AS a,closed AS b FROM episodes) SELECT AVG(b-a) FROM d',
            'WITH d(a,b) AS (SELECT opened,closed FROM episodes) SELECT AVG(b-a) FROM d',
            'WITH d AS (SELECT * FROM episodes) SELECT AVG(d.closed-d.opened) FROM d',
            'SELECT AVG("e"."closed" - "e"."opened") FROM episodes "e"',
            "SELECT date('2030-01-02') - date('2029-12-30')", 
            "SELECT '2030-01-02' + 1"]
        with sqlite3.connect(self.catalog.target) as db:
            for sql in bad:
                with self.subTest(sql=sql): self.assertTrue(Q.date_arithmetic_issues(db, sql))

    def test_numeric_epochs_money_year_extraction_and_converted_dates_remain_valid(self):
        good = [
            'SELECT AVG(end_epoch-start_epoch) FROM episodes',
            'SELECT SUM(allowed-paid) FROM episodes',
            "SELECT CAST(strftime('%Y',closed) AS INT)-CAST(strftime('%Y',opened) AS INT) FROM episodes",
            'SELECT AVG(unixepoch(closed)-unixepoch(opened))/86400.0 FROM episodes',
            'SELECT AVG(julianday(closed)-julianday(opened)) FROM episodes',
            "SELECT 'not-a-date' AS explanation", 'SELECT 1.0/4 AS ratio']
        with sqlite3.connect(self.catalog.target) as db:
            for sql in good:
                with self.subTest(sql=sql): self.assertEqual(Q.date_arithmetic_issues(db, sql), [])
        result = Q.execute(self.path, {'query': good[3], 'returns': 'value'}, target=self.catalog.target)
        self.assertEqual(result['value'], 4.0)

    def test_actual_types_are_checked_again_after_source_values_change(self):
        with sqlite3.connect(self.catalog.target) as db:
            query = 'SELECT closed-opened FROM episodes'
            self.assertTrue(Q.date_arithmetic_issues(db, query))
            db.execute('UPDATE episodes SET opened=start_epoch,closed=end_epoch')
            self.assertEqual(Q.date_arithmetic_issues(db, query), [])

    def test_explicit_encounter_qualifiers_survive_different_diseases_and_question_shapes(self):
        for noun in ('inpatient', 'outpatient'):
            for disease in ('heart_failure', 'copd', 'diabetes'):
                question = f'List patients with an {noun} encounter for {disease}.'
                sql = f"SELECT patient_id FROM stay WHERE diagnosis='{disease}'"
                self.assertTrue(any(noun in gap for gap in L.missing(question, sql)))
                self.assertEqual(L.missing(question, sql + f" AND type='{noun}'"), [])

    def test_existing_planner_repairs_the_type_error_before_executing(self):
        from types import SimpleNamespace
        q = 'Report average elapsed days from opened to closed.'
        bad = self.analysis('SELECT AVG(closed-opened)/86400.0 AS days FROM episodes')
        good = self.analysis('SELECT AVG(julianday(closed)-julianday(opened)) AS days FROM episodes')
        model = Mock(side_effect=[(json.dumps({'analysis': bad}), {}),
                                  (json.dumps({'analysis': good}), {}),
                                  (json.dumps({'equivalent': True, 'issues': []}), {})])
        stack = SimpleNamespace(path=self.path, allow_live=False)
        with patch.object(P, 'schema_slice', return_value={'selected_schemas': {}}), patch.object(P, 'schema_prompt_view', side_effect=lambda v:v):
            contract, interpretation = P.interpret(self.catalog, q, model=model)
        self.assertIn('julianday', contract['query'])
        feedback = json.loads(model.call_args_list[1].args[1])['validator_feedback']
        self.assertIn('numeric coercion', str(feedback))
        self.assertEqual(G.preview(self.catalog, contract)[0]['value'], 4.0)

    def test_plain_warehouse_sql_prompt_uses_actual_relations_without_a_raw_store(self):
        from clinical_core.agere.clinical import source_profile, bottled
        from tools.training import adapter_tasks as T
        self.assertFalse((self.path/'stack.sqlite3').exists())
        profile = source_profile.load(self.path)
        self.assertEqual(profile['source_kind'], 'received_sqlite_relations')
        self.assertEqual(profile['relations']['episodes']['fields']['opened']['populated'], 2)
        self.assertEqual(profile['streams'], {})
        prompt = bottled.messages('Report elapsed days for episodes.', profile, '2030-05-27')[1]['content']
        self.assertIn('episodes(patient_id, opened, closed, start_epoch, end_epoch, paid, allowed)', prompt)
        self.assertNotIn('age_reported', prompt)
        self.assertNotIn('concept_patient(', prompt)
        ctx = T.Context(self.path)
        self.assertIn('episodes(', ctx.messages('bottled', {'question': 'Report elapsed days for episodes.'})[1]['content'])
        with sqlite3.connect(self.catalog.target) as db:
            db.execute('UPDATE episodes SET opened=NULL')
        changed = source_profile.load(self.path)
        self.assertEqual(changed['relations']['episodes']['fields']['opened']['populated'], 0)
        self.assertNotEqual(profile['snapshot_identity'], changed['snapshot_identity'])


if __name__ == '__main__': unittest.main()
