"""Complete measure populations through existing typed tools and native SQL.
Authored engineering fixtures, no clinical qualification or provider calls.
"""
import json,shutil,sqlite3,unittest
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere.clinical import (backstop_agent as B,semantic_analyst as S,
    semantic_layer,library_measure as L,measures as M)
from test_aggregate_explain import build,AS_OF
from test_measures import cov,cond,lab


class QualityRosterTools(unittest.TestCase):
    def setUp(self):
        for target in ('socket.socket.connect','socket.create_connection','subprocess.Popen'):
            p=patch(target,side_effect=AssertionError('No provider calls in native roster checks'));p.start();self.addCleanup(p.stop)
        self.ids=[f'P{i:03d}'for i in range(521)]
        records=[]
        for pid in self.ids:
            records.extend([cov(pid),cond(pid,'Type 2 diabetes mellitus without complications','E11.9','2027-01-01'),
                lab(pid,'HbA1c',10.0,'%','2028-10-01')])
        self.path=build(records,{pid:{'birth_date':'1970-01-01','age_reported':59,'sex_reported':'F'}for pid in self.ids})
        self.addCleanup(lambda:shutil.rmtree(self.path));semantic_layer.build(self.path)
        self.meta=json.loads((self.path/'run.json').read_text())
        self.stack=SimpleNamespace(path=self.path,allow_live=False,retriever=SimpleNamespace(meta=self.meta))
        self.wh=S.SemanticWarehouse(self.path/'semantic.sqlite3');self.addCleanup(self.wh.close)

    def tools(self,cohort=None):
        t=B.Tools(self.stack,self.wh,AS_OF,cohort=cohort);self.addCleanup(t.db.close);return t

    def execute(self,t,output='patients',**extra):
        return t({'name':'execute_analysis','input':{'operation':'library_measure','parameters':{
            'measure':'gsd_poor_control','period':{'start':'2028-01-01','end':'2028-12-31'},
            'group_by':'none','output':output},**extra}})

    def test_complete_patient_rows_counts_and_summary_reach_native_composition(self):
        t=self.tools();menu=t({'name':'list_analyses','input':{}})
        self.assertIn('library_measure',[r['operation']for r in menu['analyses']])
        schema=t({'name':'describe_analysis','input':{'operation':'library_measure'}})['parameters']
        self.assertIn('patients',schema['properties']['output']['enum'])
        result=self.execute(t);report=result['engine_result']['results'][-1]['result']
        self.assertEqual(len(result['rows']),521);self.assertEqual({r['patient_id']for r in result['rows']},set(self.ids))
        self.assertEqual((report['overall']['eligible'],report['overall']['numerator'],report['overall']['denominator'],report['overall']['rate']),(521,521,521,1.0))
        self.assertNotIn('nobody met the denominator',report['reading'])
        counts=t({'name':'query_sql','input':{'sql':f"SELECT COUNT(*) AS rows, SUM(met) AS numerator, SUM(CASE WHEN excluded=0 THEN 1 ELSE 0 END) AS denominator FROM {result['result_view']}"}})
        self.assertEqual(counts['rows'],[{'rows':521,'numerator':521,'denominator':521}])
        self.assertNotIn('engine_result',S.actor_result(result))

    def test_all_gaps_and_inherited_subset_are_retained(self):
        t=self.tools();result=self.execute(t,'gaps');report=result['engine_result']['results'][-1]['result']
        self.assertEqual(report['gap_count'],521);self.assertEqual(len(report['care_gaps']),521)
        self.assertEqual(len(result['rows']),521)
        self.assertEqual(set(result['answer']['patient_ids']),set(self.ids))
        subset=self.execute(self.tools(self.ids[:2]));self.assertEqual(len(subset['rows']),2)
        with self.assertRaisesRegex(ValueError,'outside this request'):self.execute(self.tools(self.ids[:2]),patient_ids=[self.ids[3]])

    def test_invalid_and_future_periods_never_reach_measure_evaluation(self):
        for period in ({'start':'2028-02-30','end':'2028-12-31'},
                       {'start':'2028-12-31','end':'2028-01-01'},
                       {'start':'2030-01-01','end':'2030-12-31'}):
            with self.subTest(period=period),patch.object(M,'evaluate_db',side_effect=AssertionError('No evaluation of invalid period')),patch.object(semantic_layer,'ensure',side_effect=AssertionError('No warehouse build for invalid period')):
                with self.assertRaises(ValueError):L.execute(self.path,None,{'measure':'gsd_poor_control','period':period},AS_OF)

    def test_indeterminate_measurement_is_native_unknown_not_gap(self):
        records=[cov(pid)for pid in ('known','unknown')]+[
            lab('known','eGFR',3,'mL/min/1.73m2','2028-06-01'),
            lab('unknown','eGFR','<10','mL/min/1.73m2','2028-06-01')]
        path=build(records,{pid:{'birth_date':'1970-01-01','age_reported':59,'sex_reported':'F'}for pid in ('known','unknown')})
        self.addCleanup(lambda:shutil.rmtree(path));semantic_layer.build(path)
        wh=S.SemanticWarehouse(path/'semantic.sqlite3');self.addCleanup(wh.close)
        stack=SimpleNamespace(path=path,allow_live=False,retriever=SimpleNamespace(meta=json.loads((path/'run.json').read_text())))
        t=B.Tools(stack,wh,AS_OF);self.addCleanup(t.db.close)
        criterion={'type':'measurement','label':'Authored threshold','concept':'egfr','window':{'start':'period_start','end':'period_end'},'value':{'op':'<','threshold':5}}
        measure={'id':'authored','name':'Authored threshold','status':'internal','spec_year':'authored fixture','citations':[],
            'direction':'higher_is_better','unit':'patient','denominator':{'criteria':[],'enrollment':{'allowed_gap_months':0}},'numerator':{'criteria':[criterion]}}
        with patch.object(M,'library',return_value={'authored':measure}):
            result=t({'name':'execute_analysis','input':{'operation':'library_measure','parameters':{'measure':'authored','period':{'start':'2028-01-01','end':'2028-12-31'},'output':'patients'}}})
        report=result['engine_result']['results'][-1]['result']
        self.assertEqual(result['status'],'partial');self.assertIsNone(report['overall']['numerator']);self.assertIsNone(report['overall']['rate'])
        self.assertEqual({r['patient_id']:r['met']for r in result['rows']},{'known':True,'unknown':None})
        self.assertEqual(report['care_gaps'],[])


if __name__=='__main__':unittest.main()
