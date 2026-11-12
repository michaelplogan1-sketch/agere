"""Corrected source collections replace unused versions, not missing evidence."""
import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path

from clinical_core.agere.clinical import backstop_agent as B, result_assembly as R
from clinical_core.agere.clinical.semantic_analyst import SemanticWarehouse


class CollectionReplacement(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        path = Path(temp.name) / 'semantic.sqlite3'
        with sqlite3.connect(path) as db:
            db.executescript("CREATE TABLE meta(key,value); INSERT INTO meta VALUES('as_of','2030-05-27'),('source_digest','authored');")
        self.wh = SemanticWarehouse(path); self.wh.define('2030-05-27'); self.addCleanup(self.wh.close)
        self.calls = []; self.native = {}
        self.add('agere_result_1', 'execute_analysis', {}, {'rows':[{'entity':'A'}, {'entity':'B'}]})
        self.add('agere_result_2', 'execute_analysis', {}, {'rows':[
            {'entity':'A', 'record_id':'r1', 'role':'incorrect_derived_role'},
            {'entity':'B', 'record_id':'r2', 'role':'incorrect_derived_role'}]})
        self.assemble('agere_result_3', 'agere_result_1', 'agere_result_2')
        self.query('agere_result_4', 'SELECT entity FROM agere_result_3 ORDER BY entity')
        self.add('agere_result_5', 'execute_analysis', {}, {'rows':[
            {'entity':'A', 'record_id':'r1', 'role':'reviewer'},
            {'entity':'B', 'record_id':'r2', 'role':None}]})
        self.assemble('agere_result_6', 'agere_result_4', 'agere_result_5')

    def add(self, view, name, args, result):
        result.setdefault('columns', list(result['rows'][0]))
        columns, rows = B.output_table(result)
        result.setdefault('columns', columns); result.setdefault('truncated', False)
        B.define_result(self.wh, view, columns, rows, receipt=result)
        result['result_view'] = view; self.native[view] = result
        self.calls.append({'name':name, 'input':args, 'result':result})

    def assemble(self, view, base, source):
        args = {'base':base, 'keys':['entity'], 'joins':[{'result':source, 'as':'roles',
            'on':{'entity':'entity'}, 'mode':'collect', 'retain_in_final':True}]}
        self.add(view, 'compose_table', args, R.compose(self.native, **args))

    def query(self, view, sql):
        result = self.wh.query(sql, max_rows=None, max_chars=None)
        result.update(executed_query=sql, native_sql_contract=B.NATIVE_SQL_CONTRACT)
        self.add(view, 'query_sql', {'sql':sql}, result)

    def check(self, calls=None, root='agere_result_6', rows=None):
        calls = self.calls if calls is None else calls
        native = {c['result']['result_view']:c['result'] for c in calls}
        plan = R.collection_retention_plan(calls, root)
        issues = R.collection_retention_issues(native, R._rows(native[root])[1] if rows is None else rows,
            plan['dependencies'], replacements=plan['replacements'])
        return plan['replacements'], issues

    def test_complete_corrected_collection_and_history_survive(self):
        original = copy.deepcopy(self.calls)
        replacement, issues = self.check()
        self.assertEqual(len(replacement), 1); self.assertEqual(issues, [])
        self.assertEqual(self.calls, original)
        self.assertTrue(replacement[0]['current_source_review_required'])
        self.assertEqual(self.native['agere_result_3']['rows'][0]['roles'][0]['role'], 'incorrect_derived_role')

    def test_missing_changed_or_cross_entity_replacement_is_still_rejected(self):
        for change in ('drop', 'edit', 'swap'):
            rows = copy.deepcopy(self.native['agere_result_6']['rows'])
            if change == 'drop':rows[0].pop('roles')
            if change == 'edit':rows[0]['roles'][0]['role'] = 'invented'
            if change == 'swap':rows[0]['roles'], rows[1]['roles'] = rows[1]['roles'], rows[0]['roles']
            with self.subTest(change=change):self.assertTrue(self.check(rows=rows)[1])
        self.assertTrue(self.check(root='agere_result_4')[1])  # omission alone is not replacement

    def test_consumed_old_collection_is_not_superseded(self):
        self.query('agere_result_7', "SELECT entity FROM agere_result_3 WHERE json_array_length(roles)>0 ORDER BY entity")
        self.assemble('agere_result_8', 'agere_result_7', 'agere_result_5')
        replacements, issues = self.check(root='agere_result_8')
        self.assertEqual(replacements, []); self.assertTrue(issues)

    def test_stale_unknown_or_changed_lineage_cannot_replace(self):
        for change in ('read_map', 'query', 'old_source', 'new_source', 'new_output', 'population', 'promise'):
            calls = copy.deepcopy(self.calls); by = {c['result']['result_view']:c for c in calls}
            if change == 'read_map':by['agere_result_4']['result'].pop('native_read_columns')
            if change == 'query':by['agere_result_4']['result']['executed_query'] += ' LIMIT 1'
            if change == 'old_source':by['agere_result_2']['result']['rows'][0]['role'] = 'different'
            if change == 'new_source':by['agere_result_5']['result']['rows'][0]['role'] = 'different'
            if change == 'new_output':by['agere_result_6']['result']['rows'][0]['roles'] = []
            if change == 'population':by['agere_result_6']['result']['rows'].pop()
            if change == 'promise':by['agere_result_6']['result']['assembly']['joins'][0]['retain_in_final'] = False
            with self.subTest(change=change):self.assertEqual(self.check(calls)[0], [])

    def test_equivalent_corrected_assembly_rebinds_only_unchanged_proposed_values(self):
        args={'base':'agere_result_3','keys':['entity'],'joins':[
            {'result':'agere_result_5','as':'corrected_roles','on':{'entity':'entity'},
             'mode':'collect','retain_in_final':True}]}
        self.add('agere_result_9','compose_table',args,R.compose(self.native,**args))
        args={'base':'agere_result_9','keys':['entity'],'base_columns':['entity','corrected_roles'],
              'rename_columns':{'corrected_roles':'roles'}}
        self.add('agere_result_10','compose_table',args,R.compose(self.native,**args))
        rows=self.native['agere_result_10']['rows'];before=copy.deepcopy(self.calls)
        proposal=R.equivalent_retained_assembly(self.calls,'agere_result_10',rows)
        self.assertEqual(proposal['candidate_result_view'],'agere_result_6')
        self.assertIsNone(proposal['compose']);self.assertTrue(proposal['full_current_review_required'])
        self.assertEqual(self.calls,before)
        changed=copy.deepcopy(rows);changed[0]['roles'][0]['record_id']='foreign'
        self.assertIsNone(R.equivalent_retained_assembly(self.calls,'agere_result_10',changed))
        self.assertIsNone(R.equivalent_retained_assembly(self.calls,'agere_result_9',self.native['agere_result_9']['rows']))


if __name__ == '__main__':unittest.main()
