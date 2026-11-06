"""Existing source assertions -> typed tool -> complete native patient receipts.

Authored fixtures only. Source binding and unknown-state engineering checks,
not model quality, clinical adjudication or the full Q21 question.
"""
import copy,json,shutil,sqlite3,unittest,zlib
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.contracts import digest
from clinical_core.agere.clinical import (analysis_tools as A,backstop_agent as B,
    graph_contracts as G,longitudinal as L,request_plan as R,semantic_analyst as S,
    semantic_layer,shared_planner,assertion_reconciliation as AR)
from test_aggregate_explain import build,AS_OF
from test_longitudinal import connection


class DocumentaryHandoff(unittest.TestCase):
    def setUp(self):
        for target in ('socket.socket.connect','socket.create_connection','subprocess.Popen'):
            p=patch(target,side_effect=AssertionError('No provider or index-build process allowed'))
            p.start();self.addCleanup(p.stop)
        self.ids=[f'p{i:02d}' for i in range(12)]
        records=[]
        for i,pid in enumerate(self.ids):
            records.append((pid,'financial',{'difficulty_paying_medical_bills':None if i==2 else i!=1,
                'event_date':'2029-01-02','available_date':'2029-01-03'}))
        records.append(('p03','financial',{'difficulty_paying_medical_bills':False,'event_date':'2029-01-02','available_date':'2029-01-03'}))
        for pid in ('p04','p05','p06','p07','p07'):
            records.append((pid,'notes',{'text':'Patient reports difficulty paying medical bills.',
                'event_date':'2029-02-02','available_date':'2029-02-03'}))
        self.path=build(records,{pid:{'age_reported':50,'sex_reported':'F'} for pid in self.ids})
        self.addCleanup(lambda:shutil.rmtree(self.path))
        self.meta=json.loads((self.path/'run.json').read_text())
        self.meta['synthetic']=True
        (self.path/'run.json').write_text(json.dumps(self.meta))
        from clinical_core.agere.clinical.program_registry import TABLE
        with sqlite3.connect(self.path/'stack.sqlite3')as db:db.execute(TABLE)
        semantic_layer.build(self.path)
        (self.path/'source-profile.json').write_text(json.dumps({'source_digest':'fixture','streams':{
            'financial':{'fields':{'difficulty_paying_medical_bills':{},'event_date':{}}},
            'notes':{'fields':{'text':{},'event_date':{}}}}}))
        (self.path/'longitudinal-link.json').write_text('{}')  # validated connection injected below, no artifact build
        with sqlite3.connect(self.path/'stack.sqlite3')as db:self.records=[json.loads(r[0])for r in db.execute('SELECT payload FROM records ORDER BY id')]
        events=[]
        for raw in self.records:
            events.extend(L.record_events(raw,AS_OF))
            if raw['source_table']!='notes':continue
            text=raw['text'];axes={'assertion':'affirmed','subject':'patient','time':'current','relation':'clinical_observation'}
            if raw['patient_id']=='p04':axes.update(assertion='unknown',subject='unknown',time='unknown')
            if raw['patient_id']=='p05':axes['time']='historical'
            if raw['patient_id']=='p06':axes['subject']='family'
            packet={'source':{**L.source(raw),'field':'text','start':0,'end':len(text),'quote':text},
                'axes':axes,'input':{'concept':'financial hardship'},'model_identity':'authored-unqualified-fixture'}
            events.append(L.annotation_event('annotation-'+raw['record_id'],raw['patient_id'],packet,raw,AS_OF))
        def connected(_):
            db,meta=connection(events);meta['snapshot']=self.meta['snapshot'];return db,meta
        p=patch.object(L,'connect',side_effect=connected);p.start();self.addCleanup(p.stop)
        self.stack=SimpleNamespace(path=self.path,allow_live=False,retriever=SimpleNamespace(meta=self.meta))
        self.wh=S.SemanticWarehouse(self.path/'semantic.sqlite3');self.addCleanup(self.wh.close)

    def tools(self,cohort=None):
        tools=B.Tools(self.stack,self.wh,AS_OF,cohort=cohort,question='Inspect mapped documentary accounts')
        self.addCleanup(tools.db.close);return tools

    def execute(self,tools,op='assertion_review',**extra):
        params={'streams':['financial','notes'],'concepts':['financial hardship']} if op=='assertion_review' else {
            'streams':['financial','notes'],'concept':'financial hardship','relation':'reported_barrier',
            'clock':'source_statement_date','filter':'all'}
        return tools({'name':'execute_analysis','input':{'operation':op,'parameters':params,**extra}})

    def test_exact_mapped_parameters_reach_shared_planner_and_investigator(self):
        t=self.tools();menu=t({'name':'list_analyses','input':{}})
        self.assertTrue(A.DOCUMENTARY<={r['operation']for r in menu['analyses']})
        view=shared_planner.schema_slice(R.Catalog.load(self.path),'Reconcile source assertions',operations=sorted(A.DOCUMENTARY))
        for op in A.DOCUMENTARY:
            schema=t({'name':'describe_analysis','input':{'operation':op}})['parameters']
            self.assertEqual(schema,view['selected_operator_contracts'][op]['parameter_schema'])
            if op=='assertion_review':
                self.assertEqual(schema['properties']['concepts']['minItems'],1)
                self.assertIn('financial hardship',schema['properties']['concepts']['items']['enum'])
            else:self.assertIn('reported_barrier',schema['properties']['relation']['enum'])
        with self.assertRaises(ValueError):G.validate_parameters('assertion_review',{'streams':['notes'],'concepts':[]})

    def test_all_patient_receipts_and_original_qualifiers_reach_native_sql(self):
        t=self.tools();result=self.execute(t)
        self.assertEqual({r['patient_id']for r in result['rows']},set(self.ids))
        report=result['engine_result']['results'][-1]['result']
        self.assertEqual(len(report['patients']),4);self.assertEqual(len(report['review_members']),12)
        raw={r['record_id']:r for r in self.records}
        for row in result['rows']:
            for a in row['documentary_receipt']['assertions']:
                self.assertEqual(a['source']['record_hash'],digest(raw[a['source']['record_id']]))
        p04=next(r['documentary_receipt']for r in result['rows']if r['patient_id']=='p04')
        a=next(a for a in p04['assertions']if a['authority']=='machine_annotation')
        self.assertEqual((a['polarity'],a['subject'],a['temporality']),('unknown','unknown','unknown'))
        copied=next(r['documentary_receipt']for r in result['rows']if r['patient_id']=='p07')
        self.assertTrue(copied['groups'][0]['repeated_statement_groups'])
        self.assertFalse(copied['groups'][0]['independent_corroboration_established'])
        out=t({'name':'query_sql','input':{'sql':f"SELECT COUNT(*) AS patients FROM {result['result_view']}"}})
        self.assertEqual(out['rows'],[{'patients':12}])
        self.assertNotIn('engine_result',S.actor_result(result))

    def test_state_projection_preserves_conflicts_and_unreviewed_qualifiers(self):
        result=self.execute(self.tools(),'source_state')
        states={r['patient_id']:r['documentary_receipt']for r in result['rows']}
        self.assertEqual(len(states),12)
        self.assertEqual(states['p00']['value'],'affirmed');self.assertEqual(states['p01']['value'],'negated')
        self.assertEqual(states['p03']['status'],'conflicting')
        for pid in ('p02','p04','p05','p06','p07'):
            self.assertIsNone(states[pid]['value'])
            self.assertFalse(states[pid]['current_clinical_state_established'])

    def test_inherited_population_stays_complete_and_cannot_widen(self):
        t=self.tools(['p00','p04']);result=self.execute(t)
        self.assertEqual([r['patient_id']for r in result['rows']],['p00','p04'])
        with self.assertRaisesRegex(ValueError,'outside this request'):self.execute(t,patient_ids=['p01'])
        empty=self.execute(t,patient_ids=[]);self.assertEqual(empty['rows'],[])

    def test_missing_or_changed_native_receipt_is_not_projected_as_evidence(self):
        result=self.execute(self.tools());report=result['engine_result']['results'][-1]['result']
        member=report['review_members'][0]
        with AR.storage(self.path)as db:db.execute('DELETE FROM receipts WHERE id=?',(member['receipt_id'],))
        with self.assertRaisesRegex(ValueError,'Missing documentary'):A.documentary_rows(self.path,'assertion_review',report)
        malformed=copy.deepcopy(report);malformed['review_members']=malformed['review_members'][:-1]
        with self.assertRaisesRegex(ValueError,'Incomplete documentary'):A.documentary_rows(self.path,'assertion_review',malformed)

    def test_invalid_source_metadata_is_visible_without_inventing_a_schema(self):
        with patch.object(L,'connect',side_effect=ValueError('Authored source version mismatch')):
            view=shared_planner.schema_slice(R.Catalog.load(self.path),'Source assertions',operations=['assertion_review'])
            item=view['selected_operator_contracts']['assertion_review']
            self.assertNotIn('parameter_schema',item)
            self.assertIn('source version mismatch',item['parameter_schema_unavailable'])
            with self.assertRaisesRegex(ValueError,'source version mismatch'):
                self.tools()({'name':'describe_analysis','input':{'operation':'assertion_review'}})


if __name__=='__main__':unittest.main()
