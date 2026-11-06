"""Named populations bind actual count cells, not matching numbers or labels."""
import copy,sqlite3,unittest
from clinical_core.agere.clinical import membership as M,backstop_agent as B

class NativePopulations(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.db.executescript("CREATE TABLE patient(patient_id);INSERT INTO patient VALUES('A'),('B'),('C');CREATE TABLE agere_result_1(patient_id,kind,flag);INSERT INTO agere_result_1 VALUES('A','x',0),('A','x',0),('B','y',0),('C',NULL,1);")
        self.source={'source_digest':'authored','as_of':'2030-05-27'}
        self.producer={'name':'execute_analysis','input':{},'result':{'result_view':'agere_result_1','columns':['patient_id','kind','flag'],'rows':[['A','x',0],['A','x',0],['B','y',0],['C',None,1]]}}

    def execute(self,query,parameters):
        cur=self.db.execute(query,parameters);return [c[0]for c in cur.description],[list(r)for r in cur]

    def call(self,query):
        columns,rows=self.execute(query,{})
        return {'name':'query_sql','input':{'sql':query},'result':{
            'result_view':'agere_result_2','columns':columns,'rows':rows,'truncated':False,
            'source_contracts':self.source.copy(),'native_sql_contract':B.NATIVE_SQL_CONTRACT,
            'declared_executed_query':query,'executed_query':query}}

    def derive(self,query,mutate=None):
        call=self.call(query)
        if mutate:mutate(call)
        calls=[self.producer,call];before=copy.deepcopy(calls)
        out=M.native_populations(calls,['agere_result_2'],self.execute,{'A','B','C'},self.source)
        self.assertEqual(calls,before);return out

    def test_distinct_patients_keep_duplicates_as_source_rows_and_original_predicates(self):
        out=self.derive("SELECT 'open' label,COUNT(*) source_rows,COUNT(DISTINCT patient_id) patients FROM agere_result_1 WHERE flag=0")
        self.assertEqual(len(out['scopes']),1);s=out['scopes'][0]
        self.assertEqual((s['patient_count'],s['counted_source_rows'],s['patient_ids']),(2,3,['A','B']))
        self.assertEqual(s['proof']['binding'],{'result_view':'agere_result_2','row':0,'column':'patients'})
        self.assertIn('flag = 0',s['proof']['selection_query']);self.assertFalse(s['actionable_cohort'])

    def test_union_groups_and_null_group_keys_preserve_each_published_count(self):
        query="SELECT 'groups' label,kind,COUNT(*) rows,COUNT(DISTINCT patient_id) patients FROM agere_result_1 GROUP BY kind UNION ALL SELECT 'empty',NULL,COUNT(*),COUNT(DISTINCT patient_id) FROM agere_result_1 WHERE flag=99 ORDER BY 1,2"
        scopes=self.derive(query)['scopes'];self.assertEqual(len(scopes),4)
        self.assertEqual(sorted(s['patient_count']for s in scopes),[0,1,1,1])
        empty=next(s for s in scopes if s['patient_count']==0);self.assertEqual(empty['patient_ids'],[])
        null=next(s for s in scopes if s['proof']['group_values']==[None]);self.assertEqual(null['patient_ids'],['C'])
        self.assertIn(' IS ',null['proof']['selection_query'])

    def test_published_top_group_is_not_broadened_when_group_limit_is_removed(self):
        scopes=self.derive('SELECT kind,COUNT(DISTINCT patient_id) AS patients,COUNT(*) AS rows FROM agere_result_1 GROUP BY kind ORDER BY rows DESC,kind LIMIT 1')['scopes']
        self.assertEqual(len(scopes),1);self.assertEqual(scopes[0]['patient_ids'],['A'])
        self.assertEqual(scopes[0]['proof']['group_values'],['x'])

    def test_identical_union_output_rows_cannot_choose_different_patient_sets(self):
        out=self.derive("SELECT 'same' label,COUNT(DISTINCT patient_id) patients FROM agere_result_1 WHERE kind='x' UNION ALL SELECT 'same',COUNT(DISTINCT patient_id) FROM agere_result_1 WHERE kind='y'")
        self.assertEqual(out['scopes'],[])

    def test_event_counts_and_amounts_do_not_become_patient_scopes(self):
        for query in ('SELECT COUNT(*) AS events FROM agere_result_1','SELECT SUM(flag) AS amount FROM agere_result_1'):
            with self.subTest(query=query):self.assertEqual(self.derive(query)['scopes'],[])

    def test_wrong_source_date_incomplete_query_and_changed_output_fail_closed(self):
        query='SELECT COUNT(DISTINCT patient_id) AS patients FROM agere_result_1'
        for kind in ('date','source','incomplete','output','query'):
            def mutate(call):
                r=call['result']
                if kind=='date':r['source_contracts']['as_of']='2031-01-01'
                elif kind=='source':r['source_contracts']['source_digest']='different'
                elif kind=='incomplete':r['truncated']=True
                elif kind=='output':r['rows'][0][0]=99
                else:r['declared_executed_query']+=' WHERE flag=0'
            with self.subTest(kind=kind):self.assertEqual(self.derive(query,mutate)['scopes'],[])

    def test_unreceived_ids_and_windows_are_not_guessed(self):
        self.db.execute("INSERT INTO agere_result_1 VALUES('unreceived','z',0)")
        self.assertEqual(self.derive('SELECT COUNT(DISTINCT patient_id) patients FROM agere_result_1')['scopes'],[])
        out=self.derive('WITH r AS (SELECT patient_id,ROW_NUMBER() OVER (ORDER BY flag) n FROM agere_result_1) SELECT COUNT(DISTINCT patient_id) patients FROM r WHERE n<=2')
        self.assertEqual(out['scopes'],[]);self.assertIn('ranking',out['unavailable'][0]['reason'])

    def test_received_looking_values_do_not_turn_other_identifiers_into_patients(self):
        for query in ("WITH f AS (SELECT kind AS patient_id FROM agere_result_1) SELECT COUNT(DISTINCT patient_id) patients FROM f",
                      "WITH f(patient_id) AS (SELECT kind FROM agere_result_1) SELECT COUNT(DISTINCT patient_id) patients FROM f"):
            self.db.execute("UPDATE agere_result_1 SET kind='A'")
            with self.subTest(query=query):self.assertEqual(self.derive(query)['scopes'],[])

if __name__=='__main__':unittest.main()
