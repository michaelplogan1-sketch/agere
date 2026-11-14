"""Native source-only restoration preserves programs, rows and reading ownership."""
import copy,json,sqlite3,unittest
from clinical_core.agere.clinical import sql_query as Q,execution_state as E,semantic_analyst as S
from clinical_core.contracts import digest


class NativeProgramView(unittest.TestCase):
    def setUp(self):
        self.columns=['patient_id','event_date','n','literal']
        self.rows=[['A','2029-01-02',None,"Patient's source."],['A','2029-01-02',2,'Repeat intact.'],
                   ['B','2028-01-01',0,'三'],['C','2029-02-01',False,'Not a clinical label.']]
        self.result={'columns':self.columns,'rows':self.rows,'truncated':False}
        self.source=Q.native_result_source(self.columns,self.rows)
    def test_exact_sql_result_and_all_scope_logic_survive_snapshot_projection(self):
        sql="SELECT patient_id,COUNT(*) AS rows,SUM(n) AS n FROM ("+self.source+") AS a WHERE event_date>='2029-01-01' AND patient_id IN(SELECT value FROM json_each('[\"A\",\"B\",\"C\"]')) GROUP BY patient_id"
        before=copy.deepcopy(self.result);p=Q.native_query_model_projection(sql,{'agere_result_1':self.result})
        self.assertIsNotNone(p);self.assertIn('2029-01-01',p['query']);self.assertIn('GROUP BY',p['query'])
        self.assertIn('["A","B","C"]',p['query']);self.assertNotIn("Patient's source.",p['query'])
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        db.execute('CREATE TEMP TABLE agere_result_1(patient_id,event_date,n,literal)')
        db.executemany('INSERT INTO agere_result_1 VALUES(?,?,?,?)',self.rows)
        self.assertEqual(db.execute(sql).fetchall(),db.execute(p['query']).fetchall())
        self.assertEqual(self.result,before);self.assertEqual(p['source_bindings'][0]['native_receipt_digest'],digest(before))
        self.assertEqual(p['source_bindings'][0]['rows'],4)
        self.assertIn('not the original executed SQL',p['scope'])
    def test_stale_incomplete_ambiguous_and_non_source_programs_remain_unprojected(self):
        query='SELECT * FROM ('+self.source+') a'
        for kind in ('stale','incomplete','ambiguous','layout','non_source'):
            native={'agere_result_1':copy.deepcopy(self.result)};sql=query
            if kind=='stale':native['agere_result_1']['rows'][0][2]=0
            elif kind=='incomplete':native['agere_result_1']['truncated']=True
            elif kind=='ambiguous':native['agere_result_2']=copy.deepcopy(self.result)
            elif kind=='layout':native['agere_result_1']['columns']=list(reversed(self.columns))
            else:sql='SELECT * FROM ('+self.source+' WHERE 1=1) a'
            with self.subTest(kind=kind):self.assertIsNone(Q.native_query_model_projection(sql,native))
    def test_unsorted_source_order_survives_native_lookup_indexes(self):
        rows=[self.rows[i]for i in [3,1,2,0]];result={**self.result,'rows':rows}
        sql='SELECT * FROM ('+Q.native_result_source(self.columns,rows)+") a WHERE patient_id IN ('A','B','C')"
        projection=Q.native_query_model_projection(sql,{'agere_result_1':result})
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        db.execute('CREATE TEMP TABLE agere_result_1(patient_id,event_date,n,literal)')
        db.executemany('INSERT INTO agere_result_1 VALUES(?,?,?,?)',rows)
        db.execute('CREATE INDEX native_patient_key ON agere_result_1(patient_id)')
        self.assertEqual(db.execute(sql).fetchall(),db.execute(projection['query']).fetchall())
    def test_catalog_keeps_original_receipts_and_labels_the_projected_program(self):
        producer={'name':'execute_analysis','input':{'operation':'authored'},'result':{**self.result,'result_view':'agere_result_1'}}
        sql='SELECT * FROM ('+self.source+") a WHERE event_date<'2030-01-01'"
        call={'name':'query_sql','input':{'sql':'SELECT * FROM agere_result_1'},'result':{
            'executed_query':sql,'result_view':'agere_result_2','columns':self.columns,'rows':self.rows,'truncated':False}}
        calls=[producer,call];before=copy.deepcopy(calls);entry=E.catalog(calls)[1]
        self.assertNotIn('executed_query',entry);self.assertIn('executed_program_view',entry)
        self.assertEqual(entry['executed_program_view']['original_query_digest'],digest(sql));self.assertEqual(calls,before)
    def test_scalar_metric_frames_keep_period_definition_and_unknowns_without_engine_history(self):
        result={'result_view':'agere_result_1','metric_id':'authored','name':'Recorded source quantity','value':None,
                'unit':'percent','status':'partial','period':{'start':'2029-01-01','end':'2029-12-31'},
                'computed_as':{'definition_digest':'source-bound'},'population_evidence':[{'numerator':2,'denominator':None}],
                'missing':['The denominator remains unknown.'],'engine_result':{'large_native_receipt':['native data']*1000}}
        before=copy.deepcopy(result);entry=E.catalog([{'name':'execute_metric','input':{},'result':result}])[0]
        self.assertEqual(entry['columns'],['numerator','denominator']);self.assertEqual(entry['status'],'partial')
        self.assertEqual(entry['period'],result['period']);self.assertEqual(entry['computed_as'],result['computed_as'])
        self.assertEqual(entry['missing'],result['missing']);self.assertNotIn('observation',entry);self.assertEqual(result,before)


class ReadingOwnership(unittest.TestCase):
    def reading(self,view='agere_result_1'):
        rows=[{'patient_id':'A','text':'Complete dated source, with an exception.'}];columns=list(rows[0])
        return {'columns':columns,'rows':rows,'result_view':view,'truncated':False,
                'actor_reading':{'mode':'complete_rows','rows_digest':digest(rows),'columns_digest':digest(columns)}}
    def test_source_only_context_does_not_claim_this_actor_read_old_sources(self):
        calls=[{'name':'query_sql','input':{'sql':'SELECT * FROM received'},'result':self.reading()}];before=copy.deepcopy(calls)
        self.assertIn('complete_reading',E.catalog(calls)[0])
        entry=E.catalog(calls,readings_start=1)[0]
        self.assertNotIn('complete_reading',entry);self.assertIn('prior_reading_not_inherited',entry)
        calls.append({'name':'query_sql','input':{'sql':'SELECT * FROM agere_result_1'},'result':self.reading('agere_result_2')})
        self.assertIn('complete_reading',E.catalog(calls,readings_start=1)[1]);self.assertEqual(calls[0],before[0])
    def test_same_actor_restoration_preserves_complete_readings_even_with_source_only_default(self):
        calls=[{'name':'query_sql','input':{'sql':'SELECT * FROM received'},'result':self.reading()}];seen=[]
        def send(body):
            seen.append(json.loads(body['messages'][0]['content']))
            return {'content':[{'type':'text','text':'{"status":"partial","answer_type":"note","headline":"A source remains unqualified."}'}]},0,False
        S.tool_loop('q',[],[],lambda c:self.fail('No extra source read'),send=send,model='fixture',max_tools=None,
            compact_execution_state=True,initial_calls=calls,initial_readings=False,
            restored_messages=[{'role':'user','content':'The same actor is continuing.'}])
        self.assertIn('complete_reading',seen[0]['executed_results'][0])


if __name__=='__main__':unittest.main()
