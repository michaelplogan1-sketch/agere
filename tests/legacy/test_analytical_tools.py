"""Shared investigator -> real mathematical engines -> native result composition.
Authored engineering data only; no clinical answer keys or provider calls.
"""
import json
import os
import shutil
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere.clinical import backstop_agent as B,semantic_analyst as S,semantic_layer,analysis_tools
from test_aggregate_explain import build


class AnalyticalTools(unittest.TestCase):
    def setUp(self):
        for target in ('socket.socket.connect','socket.create_connection','subprocess.Popen'):
            p=patch(target,side_effect=AssertionError('No provider sends in offline mathematical integration'))
            p.start();self.addCleanup(p.stop)
        rows=[]
        for pid,values in [('decline',[62,61,63,60,48,46,45,44]),('stable',[60,61,59,60,62,60,61,60])]:
            for i,value in enumerate(values):rows.append((pid,'labs',{'test':'eGFR','value':value,'unit':'mL/min/1.73m2','event_date':f'2028-{i+1:02d}-15'}))
        self.path=build(rows,{pid:{'age_reported':60,'sex_reported':'F'} for pid in ('decline','stable')})
        self.addCleanup(lambda:shutil.rmtree(self.path));semantic_layer.build(self.path)
        meta=json.loads((self.path/'run.json').read_text())
        self.stack=SimpleNamespace(path=self.path,allow_live=False,retriever=SimpleNamespace(meta=meta))
        self.wh=S.SemanticWarehouse(self.path/'semantic.sqlite3');self.addCleanup(self.wh.close)
    def tools(self,cohort=None):
        tools=B.Tools(self.stack,self.wh,'2029-08-15',cohort=cohort)
        self.addCleanup(tools.db.close);return tools
    def execute(self,tools,**kw):return tools({'name':'execute_analysis','input':{'operation':'trajectory','parameters':{'concept':'egfr','direction':'down'},**kw}})

    def test_real_astronomy_derived_trajectory_without_unrelated_narrative_index_and_sql_composition(self):
        t=self.tools();menu=t({'name':'list_analyses','input':{}})
        self.assertIn('queueing',[r['operation'] for r in menu['analyses']])
        self.assertIn('concept',t({'name':'describe_analysis','input':{'operation':'trajectory'}})['parameters']['properties'])
        result=self.execute(t,patient_query_sql="SELECT patient_id FROM patient ORDER BY patient_id")
        self.assertEqual(result['status'],'answered');self.assertEqual([r['patient_id'] for r in result['rows']],['decline'])
        self.assertEqual(result['input_patient_ids'],['decline','stable'])
        count=t({'name':'query_sql','input':{'sql':f"SELECT COUNT(*) AS flagged FROM {result['result_view']}"}})
        self.assertEqual(count['rows'],[{'flagged':1}])
        answer={'answer_type':'table','table_from_tool':result['result_view']}
        B.materialize_table(answer,[{'result':result}]);self.assertEqual(answer['table'],result['rows'])
        self.assertNotIn('engine_result',S.actor_result(result))

    def test_inherited_subset_empty_and_foreign_scope_cannot_broaden(self):
        t=self.tools(['stable']);result=self.execute(t)
        self.assertEqual(result['input_patient_ids'],['stable']);self.assertEqual(result['rows'],[])
        with self.assertRaisesRegex(ValueError,'outside this request'):self.execute(t,patient_ids=['decline'])
        with patch('clinical_core.agere.clinical.analysis_graph.execute',side_effect=AssertionError('Empty cohort must not execute')):
            empty=self.execute(t,patient_ids=[])
        self.assertEqual(empty['status'],'no_matches')
        with self.assertRaises(ValueError):self.execute(t,patient_ids=['foreign'])

    def test_registered_validation_snapshot_and_side_effect_boundaries_are_enforced(self):
        t=self.tools()
        with self.assertRaises(ValueError):t({'name':'execute_analysis','input':{'operation':'trajectory','parameters':{'concept':'invented'}}})
        with self.assertRaises(ValueError):t({'name':'execute_analysis','input':{'operation':'identity_resolution','parameters':{}}})
        t.as_of='2031-01-01'
        with self.assertRaisesRegex(ValueError,'snapshot'):self.execute(t)

    def test_default_trajectory_output_retains_every_flagged_patient_and_explicit_limits_remain_explicit(self):
        from clinical_core.agere.clinical import trajectory
        patients={f'P{i}':{'age_reported':60,'sex_reported':'F'} for i in range(65)}
        records=[(pid,'labs',{'test':'eGFR','value':value,'unit':'mL/min/1.73m2','event_date':f'2028-{i+1:02d}-15'})
                 for pid in patients for i,value in enumerate([62,61,63,60,48,46,45,44])]
        path=build(records,patients);self.addCleanup(lambda:shutil.rmtree(path));semantic_layer.build(path)
        report=trajectory.execute(path,None,{'concept':'egfr','direction':'down'})
        self.assertEqual(report['flagged'],65);self.assertEqual(len(report['patients']),65)
        limited=trajectory.execute(path,None,{'concept':'egfr','direction':'down','top':10})
        self.assertEqual(limited['flagged'],65);self.assertEqual(len(limited['patients']),10)

    def test_engine_preview_does_not_repeat_complete_nested_tables_and_memberships(self):
        rows=[{'patient_id':f'P{i}','detail':'x'*80} for i in range(3000)]
        ids=[r['patient_id'] for r in rows]
        native={'rows':rows,'input_patient_ids':ids,'answer':{'table':rows,'patient_ids':ids},'result_view':'agere_result_1'}
        preview=S.actor_result(native)
        self.assertLess(len(json.dumps(preview)),12000)
        self.assertEqual(preview['input_patient_ids_count'],3000)
        self.assertEqual(preview['answer']['table_rows_computed'],3000)
        self.assertEqual(len(native['answer']['table']),3000)

    def test_complete_engine_roster_reaches_native_sql_despite_presentation_limit(self):
        rows=[{'patient_id':'decline','gap_status':'open-gap'},
              {'patient_id':'stable','gap_status':'closed-gap'}]
        report={'table':rows[:1],'complete_table':rows,'table_rows':2}
        execution={'answer':{'status':'answered','table':rows[:1]},
                   'results':[{'result':report}]}
        t=self.tools()
        with patch('clinical_core.agere.clinical.analysis_graph.execute',return_value=execution):
            result=t({'name':'execute_analysis','input':{'operation':'care_gap_closure',
                'parameters':{'focus':'all','top':1}}})
        self.assertEqual(result['rows'],rows)
        self.assertEqual(result['table_rows'],2)
        self.assertEqual(result['presentation_rows'],1)
        counts=t({'name':'query_sql','input':{'sql':f"SELECT gap_status,COUNT(*) AS n FROM {result['result_view']} GROUP BY gap_status ORDER BY gap_status"}})
        self.assertEqual(counts['rows'],[{'gap_status':'closed-gap','n':1},{'gap_status':'open-gap','n':1}])

    def test_logged_outcomes_feed_the_existing_bandit_through_the_same_mathematical_tool(self):
        with sqlite3.connect(self.path/'stack.sqlite3') as db:
            for channel,hits in [('phone',8),('text',1)]:
                for i in range(10):
                    payload={'record_id':f'{channel}{i}','patient_id':'decline','source_table':'outreach_attempts',
                        'channel':channel,'outcome':'engaged' if i<hits else 'no_answer','event_date':'2029-01-01'}
                    db.execute('INSERT INTO records VALUES (?,?,?,?)',(payload['record_id'],'decline','outreach_attempts',json.dumps(payload)))
        self.wh.close();semantic_layer.build(self.path)
        self.wh=S.SemanticWarehouse(self.path/'semantic.sqlite3');self.addCleanup(self.wh.close)
        result=self.tools(['decline'])({'name':'execute_analysis','input':{'operation':'outreach_bandit','parameters':{'next_batch':20}}})
        report=result['engine_result']['results'][-1]['result']
        self.assertEqual(report['attempts'],20)
        self.assertEqual(report['arms'][0]['channel'],'phone')
        self.assertEqual(sum(report['allocation'].values()),20)
        self.assertIsNone(report['simulation'])  # received outcomes, not fabricated responses or dispatched contacts


if __name__=='__main__':unittest.main()
