"""Native joins materialize once while preserving exact evidence and read guards."""
import json,sqlite3,tempfile,unittest
from pathlib import Path
from clinical_core.agere.clinical import backstop_agent as B,semantic_analyst as S
from clinical_core.agere.clinical.sql_query import native_result_source,portable_native_query


class MaterializedNativeResults(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        path=Path(self.temp.name)/'semantic.sqlite3'
        with sqlite3.connect(path)as db:
            db.executescript("CREATE TABLE meta(key,value);INSERT INTO meta VALUES('as_of','2030-05-27');CREATE TABLE patient(patient_id);INSERT INTO patient VALUES('A'),('B');")
        self.semantic=S.SemanticWarehouse(path);self.addCleanup(self.semantic.close)
        self.db=self.semantic.db

    def metadata(self,sql):
        self.db.set_authorizer(None)
        try:return self.db.execute(sql).fetchone()[0]
        finally:self.db.set_authorizer(self.semantic._authorize)

    def test_values_types_nested_json_nulls_and_duplicates_equal_portable_source(self):
        columns=['patient_id','value','payload','quoted"field']
        rows=[['A',None,{'unknown':None,'flag':False},"O'Brien"],['B',True,[1,'β',None],3.25],
              ['B',True,[1,'β',None],3.25],['C',9223372036854775808,{'x':1},False]]
        source=B.define_result(self.semantic,'agere_result_1',columns,rows)
        original=[tuple(r)for r in self.db.execute(source)]
        actual=[tuple(r)for r in self.db.execute('SELECT * FROM agere_result_1')]
        self.assertEqual(actual,original)
        self.assertEqual(self.metadata("SELECT type FROM sqlite_temp_master WHERE name='agere_result_1'"),'table')
        sql='SELECT typeof(value),typeof(payload),COUNT(*) FROM agere_result_1 GROUP BY typeof(value),typeof(payload) ORDER BY 1,2'
        expected=sql.replace('agere_result_1','('+source+')')
        self.assertEqual([tuple(r)for r in self.db.execute(sql)],[tuple(r)for r in self.db.execute(expected)])

    def test_key_join_uses_materialized_index_without_rescanning_json(self):
        rows=[[f'P{i}',{'statements':[{'value':i,'unknown':None}]*4}]for i in range(4000)]
        B.define_result(self.semantic,'agere_result_1',['patient_id','receipt'],rows)
        B.define_result(self.semantic,'agere_result_2',['patient_id','flag'],[[f'P{i}',i%2]for i in range(4000)])
        query='SELECT COUNT(*) AS n,SUM(b.flag) AS flagged FROM agere_result_1 a JOIN agere_result_2 b ON b.patient_id=a.patient_id'
        plan=' '.join(str(tuple(r))for r in self.db.execute('EXPLAIN QUERY PLAN '+query))
        self.assertIn('INDEX',plan.upper());self.assertNotIn('json_each',plan)
        result=self.semantic.query(query);self.assertEqual(result['rows'],[{'n':4000,'flagged':2000}])
        self.assertEqual(set(result['read_views']),{'agere_result_1','agere_result_2'})

    def test_portable_receipt_executes_without_request_local_tables(self):
        source=B.define_result(self.semantic,'agere_result_1',['patient_id','amount'],[['A',3],['A',4],['B',None]])
        query='SELECT patient_id,SUM(amount) AS amount FROM agere_result_1 GROUP BY patient_id ORDER BY patient_id'
        portable=portable_native_query(query,{'agere_result_1':source},['agere_result_1'])
        with sqlite3.connect(':memory:')as fresh:
            self.assertEqual(fresh.execute(portable).fetchall(),[tuple(r)for r in self.db.execute(query)])

    def test_native_tables_remain_read_only_and_new_cohort_clears_tables_indexes_and_guards(self):
        B.define_result(self.semantic,'agere_result_1',['patient_id','value'],[['A',3]])
        for query in ("UPDATE agere_result_1 SET value=7","INSERT INTO main.patient VALUES('C')","DROP TABLE agere_result_1"):
            with self.subTest(query=query),self.assertRaises(sqlite3.DatabaseError):self.db.execute(query)
        B.define_cohort(self.semantic,['B'])
        self.assertEqual(self.metadata("SELECT COUNT(*) FROM sqlite_temp_master WHERE name LIKE 'agere_result_%'"),0)
        self.assertEqual(getattr(self.semantic._local,'native_results'),{})
        self.assertEqual([r[0]for r in self.db.execute('SELECT patient_id FROM cohort')],['B'])
        self.assertEqual([r[0]for r in self.db.execute('SELECT patient_id FROM main.patient')],['A','B'])

    def test_existing_result_cannot_be_overwritten_or_removed_by_failed_registration(self):
        B.define_result(self.semantic,'agere_result_1',['patient_id'],[['A']])
        with self.assertRaises(sqlite3.DatabaseError):B.define_result(self.semantic,'agere_result_1',['patient_id'],[['B']])
        self.assertEqual([r[0]for r in self.db.execute('SELECT patient_id FROM agere_result_1')],['A'])
        self.assertEqual(self.metadata('PRAGMA query_only'),1)

    def test_incomplete_producer_guards_and_shadowed_cte_binding_survive_materialization(self):
        B.define_result(self.semantic,'agere_result_1',['patient_id'],[['A']],receipt={'rows':[['A']],'columns':['patient_id'],'truncated':True})
        with self.assertRaisesRegex(ValueError,'Incomplete native dependency'):self.semantic.query('SELECT * FROM agere_result_1')
        with self.assertRaisesRegex(ValueError,'Incomplete native dependency'):self.semantic.query('SELECT COUNT(*) AS n FROM agere_result_1')
        inspected=self.semantic.query('SELECT * FROM agere_result_1',inspect_incomplete=True)
        self.assertTrue(inspected['incomplete_dependencies'])
        local=self.semantic.query("WITH agere_result_1 AS (SELECT 'B' AS patient_id) SELECT * FROM agere_result_1")
        self.assertEqual(local['rows'],[{'patient_id':'B'}]);self.assertEqual(local['read_views'],[])


if __name__=='__main__':unittest.main()
