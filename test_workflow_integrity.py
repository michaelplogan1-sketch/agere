"""Authored shared-boundary regressions. Scripted reviewers are wiring checks only."""
import copy
import importlib
import json
from pathlib import Path
import sqlite3
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from clinical_core.agere.clinical import backstop_agent as B, general_analysis as G, proposers as P, system_one_gate as S
from clinical_core.agere.clinical import answer_dependencies as D, workflow_contract as W
from clinical_core.agere.deliverables import answer_context as A
from clinical_core.agere.deliverables.receipts import Bundle
C = importlib.import_module('clinical_core.agere.deliverables.compose')


class Warehouse(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        with sqlite3.connect(self.path/'semantic.sqlite3') as db:
            db.executescript("""
              CREATE TABLE meta(key,value);
              INSERT INTO meta VALUES('as_of','2030-05-27'),('source_digest','integrity-fixture'),('version','fixture');
              CREATE TABLE patient(patient_id PRIMARY KEY);
              INSERT INTO patient VALUES('A'),('B');
              CREATE TABLE claim(patient_id,payer,allowed_dollars);
              INSERT INTO claim VALUES('A','X',12),('A','X',5),('B','Y',100);
              CREATE TABLE payer(payer,label); INSERT INTO payer VALUES('X','First'),('Y','Second');
              CREATE VIEW aggregate_claims(total) AS SELECT sum(allowed_dollars) FROM claim;
            """)

    def semantic(self):
        from clinical_core.agere.clinical.semantic_analyst import SemanticWarehouse
        semantic = SemanticWarehouse(self.path/'semantic.sqlite3')
        self.addCleanup(semantic.close)
        return semantic

    def tools(self, semantic, cohort):
        t = B.Tools.__new__(B.Tools)
        t.semantic,t.cohort,t.as_of,t.result_sources = semantic,cohort,'2030-05-27',{}
        return t

    def test_sql_scope_is_applied_before_counts_dollars_views_and_ctes(self):
        s = self.semantic()
        queries = [
            ('SELECT count(*) FROM patient', 1),
            ('SELECT sum(allowed_dollars) FROM claim', 17),
            ('SELECT * FROM aggregate_claims', 17),
            ("WITH cohort AS (SELECT 'B' AS patient_id) SELECT sum(allowed_dollars) FROM claim",17),
            ('WITH patient AS (SELECT * FROM claim) SELECT count(*) FROM patient',2),
            ('SELECT sum(c.allowed_dollars) FROM claim c JOIN payer p USING(payer)',17),
        ]
        for q,want in queries:
            with self.subTest(q=q):
                out = self.tools(s,['A'])._call({'name':'query_sql','input':{'sql':q,'complete':True}})
                self.assertEqual(next(iter(out['rows'][0].values())),want)
                self.assertIn('json_each',out['executed_query'].lower())
        self.assertEqual(list(self.tools(s,[])._call({'name':'query_sql','input':{'sql':'SELECT count(*) FROM patient'}})['rows'][0].values()),[0])
        self.assertEqual(list(self.tools(s,None)._call({'name':'query_sql','input':{'sql':'SELECT count(*) FROM patient'}})['rows'][0].values()),[2])
        with self.assertRaisesRegex(ValueError,'unqualified'):
            self.tools(s,['A'])._call({'name':'query_sql','input':{'sql':'SELECT count(*) FROM main.patient'}})

    def test_temp_definition_and_result_views_keep_full_rows_and_scope(self):
        s = self.semantic()
        B.define_cohort(s,['A'])
        B.define_result(s,'agere_result_1',['patient_id','visits'],[['A',4],['B',19]])
        out = self.tools(s,['A'])._call({'name':'query_sql','input':{'sql':'SELECT sum(visits) FROM agere_result_1'}})
        # Result views can contain joined/aggregated upstream evidence. Their own
        # exposed membership is scoped as well as any underlying source tables.
        self.assertEqual(list(out['rows'][0].values()),[4])

    def test_saved_investigation_is_complete_immutable_and_evidence_identified(self):
        result={'answer':{'status':'answered','answer_type':'table','table':[{'patient_id':'A'}],'patient_ids':['A']},
                'investigation':{'tool_calls':[{'name':'query_sql','input':{'sql':'SELECT ...'},
                                               'result':{'table':[{'allowed_dollars':17}], 'elapsed_s':3}}],
                                 'document_reviews':[{'manifest':['N1'], 'findings':[{'reason':'UNKNOWN_TAIL'}]}]}}
        first=A.capture(self.path,'Inspect selected evidence',result)
        saved=A.load(self.path,first['id'],current=True)
        self.assertEqual(saved['investigation'],result['investigation'])
        self.assertIn('UNKNOWN_TAIL',A.evidence_bundle(self.path,first['id'])[1].digest_for_model(max_list=None,max_str=None,max_chars=None))
        result['investigation']['tool_calls'][0]['result']['elapsed_s']=9
        self.assertEqual(A.capture(self.path,'Inspect selected evidence',result)['id'],first['id'])
        result['investigation']['tool_calls'][0]['result']['table'][0]['allowed_dollars']=18
        self.assertNotEqual(A.capture(self.path,'Inspect selected evidence',result)['id'],first['id'])
        self.assertEqual(A.load(self.path,first['id'])['investigation']['tool_calls'][0]['result']['elapsed_s'],3)

    def test_current_reuse_enforces_evidence_and_premises_but_history_opens(self):
        result={'answer':{'status':'answered','count':1,'answer_type':'count','patient_ids':['A']}}
        key=A.capture(self.path,'Observed patients',result)['id']
        real=D.validate
        for field in ('evidence_complete','premises_current'):
            def invalid(*a,**kw):
                check=real(*a,**kw)
                return {**check,field:False,'current':False}
            with self.subTest(field=field),patch.object(D,'validate',side_effect=invalid):
                self.assertTrue(A.load(self.path,key)['invalidated'])
                with self.assertRaisesRegex(ValueError,'invalidated'): A.load(self.path,key,current=True)

    def test_checked_followup_preserves_parent_and_explicit_empty_scope(self):
        parent={'id':'ANS-'+'a'*24,'patient_rows':[{'patient_id':'A'}], 'answer':{'actionable_cohort':True},
                'planner_receipt':{'request_contract':{'original_question':'parent'}}}
        receipt={'request_contract':{'kind':'checked_graph','executed':{'answer':{'status':'answered','count':0}}},
                 'new_paid_api_calls':0,'interpretation':{'interpreter':'authored','proposer':{'calibration':'scripted'}}}
        out=P.execute(SimpleNamespace(), 'Count selected', {},receipt,started=time.perf_counter(),population=[],parent=parent)
        self.assertEqual(out['cohort_transform']['input_patient_ids'],[])
        self.assertEqual(out['cohort_transform']['parent_context_id'],parent['id'])
        saved=A.load(self.path,A.capture(self.path,'Count selected',out)['id'])
        self.assertEqual(saved['input_patient_ids'],[])
        with self.assertRaisesRegex(ValueError,'explicit input membership'):
            P.execute(SimpleNamespace(),'q',{},receipt,started=time.perf_counter(),parent=parent)
        bad=copy.deepcopy(receipt);bad['request_contract']['executed']['answer']['patient_ids']=['B']
        with self.assertRaisesRegex(ValueError,'escaped'):
            P.execute(SimpleNamespace(),'q',{},bad,started=time.perf_counter(),population=['A'])


class CompleteReview(unittest.TestCase):
    def test_native_governed_rows_survive_and_incomplete_receipts_cannot_bind(self):
        for n in (57,2005):
            t=B.Tools.__new__(B.Tools);t.cohort=None;t.as_of='2030-05-27';t.stack=SimpleNamespace(path='/unused');t.governed=[]
            rows=[{'patient_id':str(i),'dollars':i} for i in range(n)]
            result={'answer':{'status':'answered','table':rows,'table_rows':n}}
            from clinical_core.agere.clinical import shared_planner,decision_context
            with patch.object(shared_planner,'_compose',return_value=result),patch.object(decision_context,'finalize',side_effect=lambda p,o,state:o):
                native=t.ask_agere('List every patient')
            self.assertEqual(len(native['table']),n)
            a={'table_from_tool':'agere_result_1'}
            B.materialize_table(a,[{'result':{**native,'result_view':'agere_result_1'}}])
            self.assertEqual(sum(r['dollars'] for r in a['table']),n*(n-1)//2)
            for broken in ({'truncated':True},{'table_rows':n+1}):
                with self.assertRaises(ValueError):
                    B.materialize_table({'table_from_tool':'agere_result_1'},[{'result':{**native,'result_view':'agere_result_1',**broken}}])

    def test_support_sees_final_tail_and_intermediate_unknowns(self):
        rows=[[f'P{i}','unknown' if i==56 else 'yes'] for i in range(57)]
        calls=[{'name':'query_sql','input':{'sql':'SELECT ...'},'result':{'result_view':'agere_result_1','columns':['exception'],'rows':[['INTERMEDIATE_EXCEPTION']]}},
               {'name':'query_sql','input':{'sql':'SELECT ...'},'result':{'result_view':'agere_result_2','columns':['patient_id','eligible'],'rows':rows}}]
        def decide(q,options,evidence,**kw):
            serialized=json.dumps(evidence)
            self.assertIn('P56',serialized);self.assertIn('unknown',serialized);self.assertIn('INTERMEDIATE_EXCEPTION',serialized)
            return {'label':'contradicted','decided_by':'scripted','steps':[]}
        with patch.object(S,'ready',return_value=True),patch.object(S,'check',return_value={'label':'answers_fully','calls':0,'cost_usd':0}),patch.object(S,'s1',return_value=SimpleNamespace(decide=decide)):
            out=B.review_final(SimpleNamespace(),'All eligible?',{'answer_type':'table','headline':'Every patient is eligible','table_from_tool':'agere_result_2'},calls,
                               SimpleNamespace(read={},document_reviews=[]),ledger=None,as_of='2030-05-27')
        self.assertEqual(out['status'],'repair')
        self.assertEqual(out['evidence_coverage']['final_table_rows'],57)

    def test_paid_accounting_excludes_local_and_preserves_distinct_retries(self):
        steps=[{'provider':'local_student','cost_usd':0}, {'provider':'jev','cache_hit':True,'cost_usd':0.1},
               {'provider':'jev','status':'prepared'}, {'provider':'jev','attempt_id':'one','cost_usd':0.2},
               {'provider':'jev','attempt_id':'one','cost_usd':0.2},
               {'provider':'perplexity','request_hash':'same','cost_usd':0.3},
               {'provider':'perplexity','request_hash':'same','cost_usd':0.3,'error':'charged failure'},
               {'provider':'perplexity','error':'usage unknown'}]
        calls,usd=S.cost({'steps':steps})
        self.assertEqual(calls,3);self.assertAlmostEqual(usd,0.8)

    def test_monitor_initialization_is_distinct_from_ready_or_failed_work(self):
        self.assertTrue(W.initialized({'kind':'monitor','status':'watching'}))
        for step in ({'kind':'monitor','status':'ready'},{'kind':'monitor','status':'watching','unresolved':['missing']},
                     {'kind':'deliverable','status':'watching'},{'kind':'simulation','status':'blocked'}):
            self.assertFalse(W.initialized(step))


class ArtifactMeaning(unittest.TestCase):
    def setUp(self):
        self.bundle=Bundle({'frozen_evidence':True})
        self.rid=self.bundle.add('saved_answer',{}, {'answer':{'table':[{'status':'yes'}]*6+[{'status':'UNKNOWN_TAIL'}], 'observed_admissions':17}},data_fp='fixed')

    def test_frozen_composition_and_review_receive_late_exceptions_and_long_context(self):
        self.bundle.results[self.rid]['long_context']='x'*1000+' LAST_SENTENCE'
        prompt=C._user_prompt('Review the evidence',None,self.bundle)
        self.assertIn('UNKNOWN_TAIL',prompt);self.assertIn('LAST_SENTENCE',prompt)
        ledger=C.Ledger()
        def send(body):
            text=body['messages'][0]['content']
            self.assertIn('UNKNOWN_TAIL',text);self.assertIn('LAST_SENTENCE',text)
            return {'content':[{'type':'text','text':json.dumps({'coverage':'incomplete','support':'contradicted','issues':[{'requirement':'eligibility','reason':'Unknown is not no'}]})}]},{'cost_estimate':{'estimated_usd':0.02}}
        report=C._review_artifact('<p>All criteria met</p>',self.bundle,'Report eligibility',None,send,ledger,{'passed':True,'violations':[]})
        self.assertFalse(report['passed']);self.assertEqual(ledger.calls[0]['purpose'],'semantic_review')
        self.assertAlmostEqual(ledger.summary()['total_usd'],0.02)

    def test_wrong_label_repairs_through_existing_delivery_path(self):
        ws=SimpleNamespace(slug='fixture')
        ctx=SimpleNamespace(calls=[],allow_model_ask=False)
        responses=iter([
            {'coverage':'complete','support':'contradicted','issues':[{'requirement':'meaning','reason':'Admission count labeled contraindications'}]},
            'TITLE: Observed events\n```html\n<p>Observed admissions</p>\n```',
            {'coverage':'complete','support':'supported','issues':[]}])
        def send(body):
            value=next(responses)
            return {'content':[{'type':'text','text':json.dumps(value) if isinstance(value,dict) else value}]},{'cost_estimate':{'estimated_usd':0.01}}
        with patch.object(C.verifymod,'verify',return_value={'passed':True,'violations':[],'violation_count':0}):
            out=C._deliver(ws,{'id':'test'},self.bundle,'Counts','<p>Confirmed contraindications</p>',C.Ledger(),ctx,send,'Describe admissions',None,time.time(),False)
        self.assertEqual(out['status'],'delivered');self.assertTrue(out['repaired'])
        self.assertEqual([c['purpose'] for c in out['model_calls']],['semantic_review','repair','semantic_review'])
        self.assertEqual(out['new_paid_api_calls'],3)
        self.assertAlmostEqual(out['costs']['total_usd'],0.03)

