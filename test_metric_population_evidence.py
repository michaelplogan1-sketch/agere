"""Full-population measure evidence must survive the actual analyst tool/view boundary."""
import copy,json,sqlite3,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere.clinical import request_vocabulary as V,backstop_agent as B,semantic_analyst as S

class PopulationEvidence(unittest.TestCase):
    def setUp(self):
        self.m=V.definitions()['measure.cbp']
        self.definition={'id':'cbp','unit':'patient','denominator':{'definition':'authored fixture'}}
        self.report={'schema':'fixture-measure/1','measure':'cbp','unit':'patient',
            'period':{'start':'2029-01-01','end':'2029-12-31'},'declined':False,
            'overall':{'eligible':5,'excluded':1,'denominator':4,'numerator':0,'rate':0.0},'warnings':[]}
    def population(self,report=None,group=None,count=9):
        return V.measure_population_evidence(report or self.report,self.m,self.definition,
            group=group or {},population_count=count,value=0.0,as_of='2030-05-27')
    def test_input_roster_does_not_replace_actual_denominator_and_zero_survives(self):
        r=self.population();self.assertEqual((r['input_population_count'],r['eligible'],r['excluded'],r['denominator'],r['numerator'],r['rate']),(9,5,1,4,0,0.0))
        self.assertEqual(r['population_evidence_status'],'computed');self.assertEqual(r['unknown_patient_count'],0)
        self.assertEqual(r['engine_evidence_digest'],V.digest(self.report));self.assertEqual(r['definition_digest'],V.digest(self.m))
        self.assertEqual(r['measure_definition_digest'],V.digest(self.definition))
    def test_indeterminate_population_never_promotes_known_subset(self):
        report=copy.deepcopy(self.report);report.update(partial=True,declined=True,known_evaluation_overall=report.pop('overall'),
            unknown_measurements=[{'patient_id':'p1','evidence':'unknown1'},{'patient_id':'p1','evidence':'unknown2'},{'patient_id':'p2','evidence':'unknown3'}])
        r=self.population(report);self.assertEqual(r['population_evidence_status'],'indeterminate');self.assertEqual(r['unknown_patient_count'],2)
        self.assertIsNone(r['numerator']);self.assertIsNone(r['denominator']);self.assertIsNone(r['rate'])
        self.assertIsNone(r['value'])
        self.assertEqual((r['known_subset_numerator'],r['known_subset_denominator']),(0,4))
    def test_unavailable_inputs_are_not_zero_unknown_patients(self):
        report={**self.report,'declined':True,'overall':{}}
        r=self.population(report);self.assertEqual(r['population_evidence_status'],'unavailable');self.assertIsNone(r['unknown_patient_count']);self.assertIsNone(r['denominator'])
    def test_zero_denominator_remains_zero_not_input_roster_or_missing(self):
        report={**self.report,'overall':{'eligible':0,'excluded':0,'denominator':0,'numerator':0,'rate':None}}
        r=self.population(report);self.assertEqual(r['denominator'],0);self.assertIsNone(r['rate'])
    def test_grouped_execution_retains_each_actual_group_and_complete_engine_evidence(self):
        with tempfile.TemporaryDirectory()as tmp:
            src=Path(tmp)/'semantic.sqlite3'
            with sqlite3.connect(src)as db:
                db.executescript("CREATE TABLE patient(patient_id,birth_date,payer_id); INSERT INTO patient VALUES('a','1980-01-01','x'),('b','1981-01-01','x'),('c','1982-01-01','y');")
            catalog=SimpleNamespace(target=src,tables={'patient':['patient_id','birth_date','payer_id']},identity={'as_of':'2030-05-27'})
            reports=[]
            def evaluate(db,m,start,end,asof,keep):
                r=copy.deepcopy(self.report);r['overall'].update(eligible=len(keep),excluded=0,denominator=len(keep));reports.append(r);return r
            with patch('clinical_core.agere.clinical.request_plan.Catalog.load',return_value=catalog),patch('clinical_core.agere.clinical.measures.evaluate_db',side_effect=evaluate):
                out=V.execute(tmp,None,{'id':self.m['id'],'version':self.m['version'],'definition_digest':V.digest(self.m),'period':self.report['period'],'group_by':['payer']})
            rows=out['answer']['population_evidence'];self.assertEqual([(r['payer'],r['input_population_count'],r['denominator'])for r in rows],[('x',2,2),('y',1,1)])
            self.assertEqual(out['evidence'],reports);self.assertEqual(out['answer']['table'],[{'payer':'x','value':0.0},{'payer':'y','value':0.0}])
    def test_partial_tool_becomes_bindable_native_view_without_losing_engine_report(self):
        report={**self.report,'partial':True,'declined':True,'overall':{},'known_evaluation_overall':self.report['overall'],'unknown_measurements':[{'patient_id':'a'}]}
        row=self.population(report);row['value']=None
        out={'answer':{'status':'partial','population_evidence':[row],'value':None,'computed_as':{'metric_id':self.m['id']},'analysis_population_count':9},'evidence':[report]}
        with tempfile.TemporaryDirectory()as tmp:
            src=Path(tmp)/'semantic.sqlite3'
            with sqlite3.connect(src)as db:db.executescript("CREATE TABLE meta(key,value);INSERT INTO meta VALUES('as_of','2030-05-27');")
            sem=S.SemanticWarehouse(src);self.addCleanup(sem.close)
            t=B.Tools.__new__(B.Tools);t.stack=SimpleNamespace(path=Path(tmp));t.db=sem.db;t.cohort=['a'];t.as_of='2030-05-27';t.semantic=sem
            t.seen=[];t.result_sources={};t.executed_results={};t.rows_by_record={};t.native_calls=[]
            with patch.object(V,'execute',return_value=out)as execute:
                result=t({'name':'execute_metric','input':{'metric_id':self.m['id'],'period_start':'2029-01-01','period_end':'2029-12-31'}})
            self.assertEqual(execute.call_count,1);self.assertEqual(result['engine_result']['evidence'],[report])
            self.assertNotIn('engine_result',S.actor_result(result));self.assertIn('population_evidence',S.actor_result(result))
            observed=sem.db.execute('SELECT numerator,denominator,known_subset_numerator,known_subset_denominator,unknown_patient_count FROM '+result['result_view']).fetchone()
            self.assertEqual(tuple(observed),(None,None,0,4,1))
            a={'headline':{'template':'Known subset {n} of {d}; unresolved {u}.','bindings':{k:{'result_view':result['result_view'],'row':0,'column':c}for k,c in [('n','known_subset_numerator'),('d','known_subset_denominator'),('u','unknown_patient_count')]}},'table_from_tool':result['result_view']}
            B.materialize_table(a,t.native_calls);B.bind_computed_prose(a,t.native_calls)
            self.assertEqual(a['headline'],'Known subset 0 of 4; unresolved 1.')
            self.assertIsNone(a['table'][0]['denominator'])

if __name__=='__main__':unittest.main()
