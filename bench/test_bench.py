import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from bench import adapters
from bench.common import Warehouse, empty_answer, load_questions, reference_answer
from bench.run import run_one, suite_digest
from bench.score import score_answer, summarize, read_latest


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'warehouse.sqlite3'
        db = sqlite3.connect(self.path)
        db.executescript('''CREATE TABLE releases(id TEXT PRIMARY KEY,manifest TEXT);
            CREATE TABLE patients(release TEXT,id TEXT,payload TEXT);
            CREATE TABLE records(release TEXT,id TEXT,patient TEXT,stream TEXT,event TEXT,arrival TEXT,
                identity TEXT,supersedes TEXT,hash TEXT,payload TEXT);
            CREATE TABLE source_entities(release TEXT,kind TEXT,id TEXT,payload TEXT);
            CREATE TABLE results(key TEXT,payload TEXT);''')
        db.execute('INSERT INTO releases VALUES (?,?)', ('r1', json.dumps({'as_of': '2029-08-15'})))
        for pid in ('P-1008', 'P-4007'):
            db.execute('INSERT INTO patients VALUES (?,?,?)', ('r1', pid, json.dumps({'patient_id': pid})))
        rows = [
            ('d1','P-1008','conditions',None,'2028-01-01','matched',{'icd10':'E11.9'}),
            ('d2','P-4007','conditions',None,'2028-01-01','matched',{'snomed':'44054006'}),
            ('l1','P-1008','labs','2028-01-01','2028-01-02','matched',{'name':'HbA1c','value':9.4,'unit':'%'}),
            ('l2','P-4007','labs',None,'2028-01-02','matched',{'name':'Hemoglobin A1c','value':11.8,'unit':'%'}),
            ('future','P-1008','labs','2028-01-01','2030-01-01','matched',{'name':'HbA1c','value':30,'unit':'%'}),
            ('unmatched','P-1008','labs',None,'2028-01-01','ambiguous',{'name':'HbA1c','value':30,'unit':'%'}),
            ('outside','OUTSIDE','labs',None,'2028-01-01','matched',{'name':'HbA1c','value':30,'unit':'%'}),
        ]
        for rid,pid,stream,event,arrival,identity,payload in rows:
            db.execute('INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?)',
                       ('r1',rid,pid,stream,event,arrival,identity,None,'source-hash',json.dumps(payload)))
        db.execute('INSERT INTO results VALUES (?,?)', ('hidden','{"answer": 999}'))
        db.commit(); db.close()
        self.before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.warehouse = Warehouse(self.path, timeout_s=2)

    def add_records(self, rows):
        """Extend only this test's disposable fixture, never the shared source."""
        self.warehouse.close()
        with sqlite3.connect(self.path) as db:
            for rid,pid,stream,event,arrival,payload in rows:
                db.execute('INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?)',
                           ('r1',rid,pid,stream,event,arrival,'matched',None,'fixture',json.dumps(payload)))
        self.before=hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.warehouse=Warehouse(self.path, timeout_s=2)

    def answer(self, ident):
        q=next(q for q in load_questions() if q['id']==ident)
        return reference_answer(q,self.warehouse)[0]

    def tearDown(self):
        self.warehouse.close()
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), self.before)
        self.temp.cleanup()


class WarehouseTests(Fixture):
    def test_scope_excludes_future_unmatched_and_outside_roster(self):
        self.assertEqual(self.warehouse.query('SELECT COUNT(*) AS n FROM records')['rows'], [{'n':4}])

    def test_json_vocabulary_inspection_works(self):
        rows = self.warehouse.query("SELECT DISTINCT key FROM records,json_each(records.payload) WHERE stream='labs'")['rows']
        self.assertEqual({r['key'] for r in rows}, {'name','value','unit'})

    def test_base_tables_and_cached_answers_cannot_bypass_scope(self):
        for sql in ['SELECT * FROM main.records', 'SELECT * FROM main.patients', 'SELECT * FROM results']:
            with self.subTest(sql=sql), self.assertRaises((sqlite3.DatabaseError,ValueError)):
                self.warehouse.query(sql)

    def test_materialized_cte_cannot_impersonate_scoped_view(self):
        for schema in ('main','MAIN','"main"',"'main'",'[main]','`main`'):
            sql=f'WITH records AS MATERIALIZED (SELECT * FROM {schema} /* bypass */ .records) SELECT COUNT(*) FROM records'
            with self.subTest(schema=schema), self.assertRaises(ValueError):
                self.warehouse.query(sql)

    def test_schema_guard_does_not_reject_comments_or_payload_text(self):
        self.assertEqual(self.warehouse.query("SELECT 'main.records' AS label /* main.records */")['rows'],[{'label':'main.records'}])

    def test_mutations_attach_pragmas_and_extensions_rejected(self):
        statements = ["DELETE FROM records", "UPDATE main.records SET patient='oops'",
                      "ATTACH ':memory:' AS other", 'PRAGMA query_only=OFF', 'CREATE TABLE nope(x)',
                      "SELECT load_extension('nope')", "SELECT 1; DELETE FROM records"]
        for sql in statements:
            with self.subTest(sql=sql), self.assertRaises((sqlite3.Error,ValueError)):
                self.warehouse.query(sql)

    def test_truncation_is_explicit(self):
        result = self.warehouse.query('SELECT id FROM records', max_rows=1)
        self.assertTrue(result['truncated']); self.assertEqual(len(result['rows']),1)

    def test_materialized_cte_count_is_allowed(self):
        result=self.warehouse.query('WITH selected AS MATERIALIZED (SELECT DISTINCT patient FROM records) SELECT COUNT(*) AS count FROM selected')
        self.assertEqual(result['rows'],[{'count':2}])

    def test_expensive_query_times_out(self):
        self.warehouse.timeout_s=.001
        with self.assertRaises(sqlite3.OperationalError):
            self.warehouse.query('WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x) SELECT SUM(n) FROM x')

    def test_diabetes_reference_supports_both_codings_and_missing_lab_code(self):
        q = next(q for q in load_questions() if q['id']=='D05')
        answer,_ = reference_answer(q,self.warehouse)
        self.assertEqual(answer['patient_ids'],['P-1008','P-4007'])

    def test_runner_reference_end_to_end(self):
        q = next(q for q in load_questions() if q['id']=='D05')
        args=argparse.Namespace(system='reference',jobs=1,database=self.path,release=None,sql_timeout=2)
        result=run_one(q,args,suite_digest([q]))
        self.assertTrue(result['score']['correct']); self.assertEqual(result['estimated_cost_usd'],0)
        self.assertIsNone(result['error'])


class ReferenceSemanticsTests(Fixture):
    def test_readmission_boundaries_distinct_encounters_and_receipt_cutoff(self):
        self.add_records([
            ('a','P-1008','adt','2028-01-01','2028-01-02',{'event':'discharge','encounter_id':'stay1'}),
            ('b','P-1008','adt','2028-01-31','2028-02-01',{'event':'admission','encounter_id':'stay2'}),
            ('c','P-4007','adt','2028-01-01','2028-01-02',{'event':'discharge','encounter_id':'stay3'}),
            ('d','P-4007','adt','2028-02-01','2028-02-02',{'event':'admission','encounter_id':'stay4'}),
            ('e','P-4007','adt','2028-01-02','2028-01-03',{'event':'admission','encounter_id':'stay3'}),
            ('f','P-4007','adt','2028-01-02','2030-01-01',{'event':'admission','encounter_id':'stay5'}),
        ])
        self.assertEqual(self.answer('D14')['count'],1)
        self.assertEqual(self.answer('C10')['value'],50)

    def test_latest_date_arrival_id_and_units_are_not_any_result(self):
        self.add_records([
            ('new1','P-1008','labs','2028-02-01','2028-02-02',{'name':'HbA1c','value':8,'unit':'%'}),
            ('new2','P-1008','labs','2028-02-01','2028-02-03',{'name':'HbA1c','value':11,'unit':'%'}),
            ('new3','P-1008','labs','2028-02-01','2028-02-03',{'name':'HbA1c','value':7,'unit':'%'}),
            ('mmol','P-4007','labs','2028-03-01','2028-03-02',{'name':'HbA1c','value':90,'unit':'mmol/mol'}),
        ])
        self.assertEqual(self.answer('D07')['patient_ids'],[])
        self.assertEqual(self.answer('D05')['patient_ids'],['P-1008','P-4007'])

    def test_egfr_equivalent_units_but_not_unindexed_clearance(self):
        self.add_records([
            ('eg1','P-1008','labs',None,'2028-01-01',{'name':'eGFR','value':20,'unit':'mL/min/1.73m²'}),
            ('eg2','P-4007','labs',None,'2028-01-01',{'name':'eGFR','value':20,'unit':'mL/min'}),
        ])
        self.assertEqual(self.answer('D08')['count'],1)

    def test_gross_payments_require_received_usd_integer_cents(self):
        self.add_records([
            ('pay1','P-1008','patient_payments','2028-01-01','2028-01-02',{'status':'received','currency':'USD','amount_cents':100}),
            ('pay2','P-4007','patient_payments','2028-01-02','2028-01-03',{'status':'received','currency':'USD','amount_cents':200}),
            ('pay3','P-4007','patient_payments','2028-01-02','2028-01-03',{'status':'pending','currency':'USD','amount_cents':500}),
            ('pay4','P-4007','patient_payments','2028-01-02','2028-01-03',{'status':'received','currency':'GBP','amount_cents':500}),
            ('ref1','P-1008','patient_refunds','2028-01-02','2028-01-03',{'status':'issued','currency':'USD','amount_cents':50}),
        ])
        self.assertEqual(self.answer('D27')['value'],3)

    def test_boolean_false_is_distinct_from_missing_and_text(self):
        self.add_records([
            ('s1','P-1008','social_screens',None,'2028-01-01',{'food_difficulty_reported':True}),
            ('s2','P-4007','social_screens',None,'2028-01-01',{'food_difficulty_reported':'true'}),
            ('s3','P-4007','social_screens',None,'2028-01-01',{}),
            ('s4','P-1008','social_screens',None,'2028-01-01',{'food_difficulty_reported':False}),
        ])
        self.assertEqual(self.answer('D24')['count'],1)
        self.assertEqual(self.answer('D26')['count'],1)

    def test_dispense_reference_requires_same_patient(self):
        self.add_records([
            ('order1','P-1008','medication_orders',None,'2028-01-01',{}),
            ('fill1','P-4007','pharmacy_dispenses',None,'2028-01-01',{'medication_order_record_id':'order1'}),
        ])
        self.assertEqual(self.answer('C12')['count'],1)


class ScorerTests(unittest.TestCase):
    def setUp(self):
        self.q={'answer_type':'count'}
        self.expected={**empty_answer('count'),'count':2}

    def test_exact_count(self):
        self.assertTrue(score_answer(self.q,self.expected,self.expected)['correct'])

    def test_missing_answer_does_not_infer_from_other_fields(self):
        self.assertEqual(score_answer(self.q,self.expected,None)['reason'],'missing_answer')

    def test_boolean_count_invalid(self):
        answer={**self.expected,'count':True}
        self.assertFalse(score_answer(self.q,self.expected,answer)['correct'])

    def test_wrong_answer_is_false_assertion(self):
        self.assertTrue(score_answer(self.q,self.expected,{**self.expected,'count':0})['false_assertion'])

    def test_matching_partial_is_not_complete(self):
        score=score_answer(self.q,self.expected,{**self.expected,'status':'partial','unknown_count':100})
        self.assertTrue(score['value_match']); self.assertFalse(score['correct'])

    def test_missing_requirements_prevent_complete_count(self):
        score=score_answer(self.q,self.expected,{**self.expected,'missing':['unmapped codes']})
        self.assertFalse(score['correct']); self.assertFalse(score['covered'])

    def test_reported_excluded_unknown_dates_do_not_fail_correct_scoped_count(self):
        score=score_answer(self.q,self.expected,{**self.expected,'unknown_count':817})
        self.assertTrue(score['correct']); self.assertTrue(score['covered'])

    def test_same_count_wrong_members_fails(self):
        expected={**empty_answer('patient_set'),'count':2,'patient_ids':['a','b']}
        actual={**expected,'patient_ids':['a','c']}
        self.assertFalse(score_answer({'answer_type':'patient_set'},expected,actual)['correct'])

    def test_duplicate_members_invalid(self):
        actual={**empty_answer('patient_set'),'count':2,'patient_ids':['a','a']}
        self.assertEqual(score_answer({'answer_type':'patient_set'},actual,actual)['reason'],'invalid_answer_contract')

    def test_values_have_explicit_tolerance_and_reject_nan(self):
        q={'answer_type':'value','tolerance':.005}
        expected={**empty_answer('value'),'value':10}
        for actual,correct in [(10.001,True),(10.01,False),(float('nan'),False),(True,False)]:
            self.assertEqual(score_answer(q,expected,{**expected,'value':actual})['correct'],correct)

    def test_table_order_ignored_but_duplicates_and_columns_count(self):
        q={'answer_type':'table'}
        expected={**empty_answer('table'),'table':[{'org':'a','n':2},{'org':'b','n':3}]}
        self.assertTrue(score_answer(q,expected,{**expected,'table':list(reversed(expected['table']))})['correct'])
        self.assertFalse(score_answer(q,expected,{**expected,'table':[{'org':'a','n':2}]*2})['correct'])

    def test_unsupported_requires_specific_missing_prerequisites(self):
        q={'answer_type':'unsupported','expected_behavior':{'required_missing':[
            {'id':'contract','any_of':['contract']},{'id':'attribution','any_of':['attribution','attributed']}]}}
        expected={**empty_answer('unsupported','unsupported'),'missing':['contract and attribution missing']}
        self.assertTrue(score_answer(q,expected,expected)['correct_abstention'])
        self.assertFalse(score_answer(q,expected,{**expected,'missing':['not enough data']})['correct'])
        self.assertTrue(score_answer(q,expected,self.expected)['false_assertion'])

    def test_unknown_cost_is_not_free(self):
        rows=[{'score':score_answer(self.q,self.expected,self.expected),'estimated_cost_usd':None,'latency_s':3}]
        result=summarize(rows,50)
        self.assertFalse(result['cost_complete']);self.assertIsNone(result['cost_per_correct_usd'])
        self.assertEqual(result['attempted'],1);self.assertEqual(result['total_questions'],50)

    def test_correct_number_wrong_type_is_contract_failure_not_false_assertion(self):
        actual={**empty_answer('value'),'value':self.expected['count']}
        result=score_answer(self.q,self.expected,actual)
        self.assertFalse(result['correct']);self.assertTrue(result['value_match'])
        self.assertFalse(result['false_assertion'])
        self.assertEqual(result['reason'],'answer_type_mismatch')

    def test_partial_spend_counts_without_claiming_complete_cost(self):
        rows=[{'score':score_answer(self.q,self.expected,self.expected),
               'estimated_cost_usd':None,'known_cost_usd':.2},
              {'system':'agere','estimated_cost_usd':None,'response':{
                  'planning_economics':{'new_api_estimate_usd':.3,'known_api_estimate_usd':99}}}]
        result=summarize(rows,50)
        self.assertAlmostEqual(result['known_cost_usd'],.5)
        self.assertFalse(result['cost_complete']);self.assertIsNone(result['cost_per_correct_usd'])

    def test_zero_correct_does_not_report_zero_cost_per_correct(self):
        rows=[{'score':score_answer(self.q,self.expected,None),'estimated_cost_usd':1,'latency_s':3}]
        self.assertIsNone(summarize(rows,50)['cost_per_correct_usd'])

    def test_latest_attempt_not_best_score(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)/'agere';folder.mkdir()
            rows=[{'system':'agere','id':'D01','started_at':'2026-01-01','score':{'correct':True}},
                  {'system':'agere','id':'D01','started_at':'2026-01-02','score':{'correct':False}}]
            (folder/'run.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
            self.assertFalse(read_latest(d)[('agere','D01')]['score']['correct'])

    def test_suite_has_30_development_20_challenge(self):
        questions=load_questions()
        self.assertEqual(sum(q['split']=='development' for q in questions),30)
        self.assertEqual(sum(q['split']=='challenge' for q in questions),20)


class AdapterTests(Fixture):
    def test_agere_posts_only_question_and_requires_top_level_answer(self):
        def transport(url,body):
            self.assertEqual(body,{'question':'test'})
            return {'counts':{'matched':2},'new_paid_api_calls':0}
        result=adapters.agere({'question':'test'},transport=transport)
        self.assertIsNone(result['answer']); self.assertEqual(result['estimated_cost_usd'],0)

    def test_agere_additional_calls_make_planner_only_usage_incomplete(self):
        result=adapters.agere({'question':'test'},transport=lambda *a:{'new_paid_api_calls':3,
            'planner_receipt':{'new_paid_api_calls':2},'planning_economics':{'new_api_estimate_usd':.1,'new_input_tokens':20}})
        self.assertIsNone(result['estimated_cost_usd']);self.assertIsNone(result['input_tokens'])

    def test_agere_complete_planning_usage(self):
        result=adapters.agere({'question':'test'},transport=lambda *a:{'new_paid_api_calls':2,
            'planner_receipt':{'new_paid_api_calls':2},'planning_economics':{
                'new_api_estimate_usd':.1,'new_input_tokens':20,'new_output_tokens':10}})
        self.assertEqual(result['estimated_cost_usd'],.1);self.assertEqual(result['input_tokens'],20)

    def test_frontier_sql_executes_and_accounts_for_every_turn(self):
        responses=[{'content':[{'type':'tool_use','id':'t1','name':'query_sql','input':{'sql':'SELECT COUNT(*) AS n FROM patients'}}],
                    'usage':{'input_tokens':10,'output_tokens':5}},
                   {'content':[{'type':'text','text':json.dumps({**empty_answer('count'),'count':2})}],
                    'usage':{'input_tokens':20,'output_tokens':10},'stop_reason':'end_turn'}]
        sent=[]
        def transport(url,body,headers):
            sent.append(body);return responses.pop(0)
        result=adapters.frontier_sql({'question':'Count patients','as_of':'2029-08-15'},self.warehouse,key='fake',transport=transport)
        self.assertIsNone(result['error']);self.assertEqual(result['answer']['count'],2)
        self.assertEqual(result['input_tokens'],30);self.assertEqual(result['output_tokens'],15)
        self.assertAlmostEqual(result['estimated_cost_usd'],.00042)
        self.assertEqual(result['tool_calls'][0]['result']['rows'],[{'n':2}])
        self.assertNotIn('reference_sql',sent[0]['messages'][0]['content'])

    def test_tool_cap_stops_execution_even_with_multiple_tool_uses(self):
        uses=[{'type':'tool_use','id':str(i),'name':'query_sql','input':{'sql':'SELECT 1 AS n'}} for i in range(3)]
        responses=[{'content':uses,'usage':{'input_tokens':1,'output_tokens':1}},
                   {'content':[{'type':'text','text':json.dumps({**empty_answer('count'),'count':1})}],
                    'usage':{'input_tokens':1,'output_tokens':1}}]
        result=adapters.frontier_sql({'question':'test','as_of':'2029-08-15'},self.warehouse,key='fake',max_tools=1,
                                    transport=lambda *a:responses.pop(0))
        self.assertEqual(sum('rows' in call['result'] for call in result['tool_calls']),1)
        self.assertEqual(sum('error' in call['result'] for call in result['tool_calls']),2)

    def test_invalid_final_json_preserves_paid_usage(self):
        result=adapters.frontier_sql({'question':'test','as_of':'2029-08-15'},self.warehouse,key='fake',
            transport=lambda *a:{'content':[{'type':'text','text':'oops'}],'usage':{'input_tokens':10,'output_tokens':5}})
        self.assertIsNotNone(result['error']);self.assertGreater(result['estimated_cost_usd'],0)

    def test_disconnected_provider_preserves_known_spend_but_total_is_unknown(self):
        from http.client import RemoteDisconnected
        replies = iter([{'content':[{'type':'tool_use','id':'t1','name':'query_sql',
                                    'input':{'sql':'SELECT 1 AS n'}}],
                         'usage':{'input_tokens':10,'output_tokens':5}}])
        def transport(*args):
            reply = next(replies, None)
            if reply is None:
                raise RemoteDisconnected('Provider closed response')
            return reply
        result=adapters.frontier_sql({'question':'test','as_of':'2029-08-15'},self.warehouse,
                                    key='fake',transport=transport)
        self.assertIn('RemoteDisconnected',result['error'])
        self.assertIsNone(result['estimated_cost_usd'])
        self.assertAlmostEqual(result['known_cost_usd'],.00014)
        self.assertEqual(result['input_tokens'],10)

    def test_key_loader_only_reads_requested_variable(self):
        from unittest.mock import patch
        p=Path(self.temp.name)/'.env';p.write_text('UNRELATED=ignore\nANTHROPIC_API_KEY="fake-value"\n')
        with patch.dict('os.environ',{},clear=True):
            self.assertEqual(adapters.api_key(p),'fake-value')


if __name__=='__main__':
    unittest.main()
