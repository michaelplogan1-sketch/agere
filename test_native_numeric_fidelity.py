"""Native value fidelity never replaces question/source meaning review."""
import copy
import json
import sqlite3
import unittest

from clinical_core.agere.clinical import backstop_agent as B
from clinical_core.agere.deliverables.receipts import uncompact_json


class NumericFidelity(unittest.TestCase):
    CALENDAR_SQL = '''WITH base AS (SELECT patient_id FROM population), amounts AS (
      SELECT patient_id,
        SUM(CASE WHEN substr(service_from,1,4)='2028' THEN paid_cents ELSE 0 END) AS prior_paid,
        SUM(CASE WHEN substr(service_from,1,4)='2029' THEN COALESCE(paid_cents,0) ELSE 0 END) AS current_paid,
        SUM(CASE WHEN substr(service_from,1,4)='2029' AND paid_cents IS NULL THEN 1 ELSE 0 END) AS missing
      FROM claim GROUP BY patient_id
    ), paired AS (
      SELECT b.patient_id,COALESCE(a.prior_paid,0) AS prior_paid,
        CASE WHEN COALESCE(a.missing,0)=0 THEN COALESCE(a.current_paid,0) END AS current_paid
      FROM base b LEFT JOIN amounts a ON a.patient_id=b.patient_id
    ) SELECT patient_id,prior_paid,current_paid,current_paid-prior_paid AS change_cents,
      ROUND(100.0*(current_paid-prior_paid)/NULLIF(prior_paid,0),2) AS change_pct
      FROM paired ORDER BY patient_id'''

    def calendar_fixture(self, sql=None):
        db=sqlite3.connect(':memory:')
        definition='SELECT patient_id FROM patient'
        db.executescript('''CREATE TABLE patient(patient_id TEXT);
          INSERT INTO patient VALUES ('A'),('B'),('C'),('D');
          CREATE TABLE claim(patient_id TEXT,service_from TEXT,paid_cents INTEGER);
          INSERT INTO claim VALUES ('A','2028-01-01',100),('A','2029-01-01',150),('A','2029-02-01',NULL),
            ('B','2029-01-01',200),('C','2028-01-01',0),('C','2029-01-01',50),
            ('D','2028-01-01',100),('D','2029-01-01',80);
          CREATE VIEW population AS '''+definition)
        sql=sql or self.CALENDAR_SQL
        cursor=db.execute(sql);columns=[c[0]for c in cursor.description]
        rows=[dict(zip(columns,row))for row in cursor.fetchall()];db.close()
        contracts={'source_digest':'calendar-fixture','as_of':'2030-01-01',
          'relations':[{'name':'patient','columns':['patient_id']},
                       {'name':'claim','columns':['patient_id','service_from','paid_cents']}],
          'definitions':[{'name':'population','query':definition}]}
        result={'result_view':'native_1','columns':columns,'rows':rows,'read_views':[],
                'executed_query':sql,'native_sql_contract':B.NATIVE_SQL_CONTRACT,
                'source_contracts':contracts,'truncated':False}
        calls=[{'name':'query_sql','input':{'sql':sql},'result':result}]
        answer={'status':'answered','headline':'Yearly paid amounts','table_from_tool':'native_1'}
        B.materialize_table(answer,calls)
        return answer,calls

    def fixture(self, sql=None):
        db = sqlite3.connect(':memory:')
        db.executescript('CREATE TABLE claim(patient_id TEXT, allowed_cents INTEGER); '
                         "INSERT INTO claim VALUES ('A',100),('A',200),('B',400);")
        sql = sql or 'SELECT patient_id, COUNT(*) AS claims, SUM(allowed_cents) AS allowed_cents FROM claim GROUP BY patient_id'
        cursor = db.execute(sql)
        columns = [c[0] for c in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        db.close()
        result = {'result_view':'native_1', 'columns':columns, 'rows':rows, 'read_views':[],
                  'executed_query':sql, 'native_sql_contract':B.NATIVE_SQL_CONTRACT,
                  'source_contracts':{'source_digest':'fixture-source', 'as_of':'2030-01-01'}, 'truncated':False}
        calls = [{'name':'query_sql', 'input':{'sql':sql}, 'result':result}]
        answer = {'status':'answered', 'headline':'Received claims', 'table_from_tool':'native_1'}
        B.materialize_table(answer, calls)
        return answer, calls

    def packets(self, answer, calls, documents=()):
        return B.support_packets('List patients and their claim totals', answer, calls, list(documents), '2030-01-01')

    def check(self, packets, decide=None):
        seen = []
        def invoke(question, options, evidence, **kwargs):
            seen.append(evidence[0]['id'])
            return decide(evidence[0]) if decide else {'label':'supported', 'decided_by':'fixture', 'steps':[]}
        result = B.check_support_packets(packets, invoke, as_of='2030-01-01', ledger=None)
        return result, seen

    def test_all_numeric_cells_checked_natively_after_global_semantic_acceptance(self):
        answer, calls = self.fixture()
        packets = self.packets(answer, calls)
        result, seen = self.check(packets)
        self.assertEqual(seen, ['summary'])
        self.assertEqual(result['label'], 'supported')
        self.assertEqual(len(result['packets']), 3)
        self.assertTrue(all(p['decided_by']=='native_numeric_table_fidelity' for p in result['packets'][1:]))
        self.assertEqual(uncompact_json(json.loads(packets[0]['text']))['all_final_rows'], answer['table'])

    def test_wrong_semantics_cannot_be_accepted_by_native_cell_equality(self):
        answer, calls = self.fixture()
        for fault in ['source', 'year', 'unit', 'denominator', 'unsupported clinical meaning']:
            with self.subTest(fault=fault):
                packets = self.packets(answer, calls)
                def reject(evidence):
                    return {'label':'contradicted', 'decided_by':'fixture', 'steps':[], 'reason':fault}
                result, seen = self.check(packets, reject)
                self.assertNotEqual(result['label'], 'supported')
                self.assertEqual(seen, ['summary'])

    def test_integer_classification_is_not_qualified_by_numeric_storage_type(self):
        answer, calls = self.fixture('SELECT patient_id, CASE WHEN SUM(allowed_cents)>200 THEN 1 ELSE 0 END AS eligible FROM claim GROUP BY patient_id')
        self.assertTrue(all(not p.get('native_numeric_fidelity') for p in self.packets(answer,calls)))

    def test_native_missing_value_count_is_arithmetic(self):
        answer, calls = self.fixture('SELECT patient_id, SUM(CASE WHEN allowed_cents IS NULL THEN 1 ELSE 0 END) AS missing_values FROM claim GROUP BY patient_id')
        _, seen = self.check(self.packets(answer,calls))
        self.assertEqual(seen,['summary'])

    def test_calendar_join_nulls_and_ratios_retain_exact_values_without_row_votes(self):
        answer,calls=self.calendar_fixture()
        self.assertEqual(answer['table'],[
            {'patient_id':'A','prior_paid':100,'current_paid':None,'change_cents':None,'change_pct':None},
            {'patient_id':'B','prior_paid':0,'current_paid':200,'change_cents':200,'change_pct':None},
            {'patient_id':'C','prior_paid':0,'current_paid':50,'change_cents':50,'change_pct':None},
            {'patient_id':'D','prior_paid':100,'current_paid':80,'change_cents':-20,'change_pct':-20.0}])
        result,seen=self.check(self.packets(answer,calls))
        self.assertEqual(seen,['summary'])
        self.assertEqual(len(result['packets']),5)
        self.assertEqual(result['label'],'supported')

    def test_calendar_arithmetic_still_requires_global_semantics(self):
        answer,calls=self.calendar_fixture()
        for label in ['contradicted','unknown']:
            result,_=self.check(self.packets(answer,calls),lambda e:{'label':label,
                'decided_by':'fixture'if label=='contradicted'else None,'steps':[],
                'reason':'Unsupported claim-reversal identity or incorrect requested missingness'})
            self.assertNotEqual(result['label'],'supported')
            self.assertFalse(any(p.get('decided_by')=='native_numeric_table_fidelity'for p in result['packets']))

    def test_equivalent_null_and_denominator_guards_do_not_fan_out_row_votes(self):
        sql=self.CALENDAR_SQL.replace('current_paid-prior_paid AS change_cents',
            'CASE WHEN current_paid IS NULL OR prior_paid IS NULL THEN NULL ELSE current_paid-prior_paid END AS change_cents').replace(
            'ROUND(100.0*(current_paid-prior_paid)/NULLIF(prior_paid,0),2)',
            'CASE WHEN current_paid IS NULL OR prior_paid IS NULL OR prior_paid=0 THEN NULL ELSE ROUND(100.0*(current_paid-prior_paid)/prior_paid,2) END')
        answer,calls=self.calendar_fixture(sql)
        baseline,_=self.calendar_fixture()
        self.assertEqual(answer['table'],baseline['table'])
        result,seen=self.check(self.packets(answer,calls))
        self.assertEqual(seen,['summary']);self.assertEqual(result['label'],'supported')
        unresolved,seen=self.check(self.packets(answer,calls),lambda e:{'label':'unknown','decided_by':None,'steps':[]})
        self.assertEqual(seen,['summary']);self.assertEqual(unresolved['label'],'unknown')
        self.assertTrue(all(r.get('status')=='awaiting_semantic_dependency'for r in unresolved['packets'][1:]))

    def test_zero_guards_cannot_hide_clinical_thresholds_or_unrelated_classifiers(self):
        for condition in ['current_paid=0','prior_paid<100','current_paid IS NULL OR prior_paid>0']:
            sql=self.CALENDAR_SQL.replace('ROUND(100.0*(current_paid-prior_paid)/NULLIF(prior_paid,0),2)',
                'CASE WHEN '+condition+' THEN NULL ELSE ROUND(100.0*(current_paid-prior_paid)/prior_paid,2) END')
            answer,calls=self.calendar_fixture(sql)
            self.assertTrue(all(not p.get('native_numeric_fidelity')for p in self.packets(answer,calls)))

    def test_joined_arithmetic_requires_resolved_source_schema(self):
        answer,calls=self.calendar_fixture()
        calls[0]['result']['source_contracts'].pop('relations')
        self.assertTrue(all(not p.get('native_numeric_fidelity')for p in self.packets(answer,calls)))

    def test_missing_counter_proof_cannot_be_replaced_with_amount_or_clinical_predicate(self):
        missing="SUM(CASE WHEN substr(service_from,1,4)='2029' AND paid_cents IS NULL THEN 1 ELSE 0 END)"
        for replacement in ['SUM(paid_cents)',
            'SUM(CASE WHEN paid_cents<100 THEN 1 ELSE 0 END)',
            "SUM(CASE WHEN substr(service_from,1,4)='2029' OR paid_cents IS NULL THEN 1 ELSE 0 END)"]:
            with self.subTest(replacement=replacement):
                answer,calls=self.calendar_fixture(self.CALENDAR_SQL.replace(missing,replacement))
                self.assertTrue(all(not p.get('native_numeric_fidelity')for p in self.packets(answer,calls)))

    def test_null_guard_aggregate_and_iso_date_partition_are_numeric(self):
        sql='''SELECT patient_id,
            CASE WHEN SUM(paid_cents IS NULL)>0 THEN NULL ELSE SUM(paid_cents) END AS known_total,
            SUM(CASE WHEN service_from>='2029-01-01' AND service_from<'2030-01-01' THEN paid_cents ELSE 0 END) AS current_paid
            FROM claim GROUP BY patient_id'''
        answer,calls=self.calendar_fixture(sql)
        _,seen=self.check(self.packets(answer,calls))
        self.assertEqual(seen,['summary'])

    def test_intermediate_native_values_keep_aggregate_lineage_and_source_identity(self):
        answer,calls=self.calendar_fixture()
        first=calls[0]['result']
        sql='SELECT patient_id,current_paid FROM native_1'
        rows=[{k:r[k]for k in ['patient_id','current_paid']}for r in answer['table']]
        second={**copy.deepcopy(first),'result_view':'native_2','executed_query':sql,
                'columns':['patient_id','current_paid'],'rows':rows,'read_views':['native_1']}
        calls.append({'name':'query_sql','input':{'sql':sql},'result':second})
        answer={'status':'answered','headline':'Current amounts','table_from_tool':'native_2'}
        B.materialize_table(answer,calls)
        _,seen=self.check(self.packets(answer,calls))
        self.assertEqual(seen,['summary'])
        second['source_contracts']['source_digest']='different-source'
        self.assertTrue(all(not p.get('native_numeric_fidelity')for p in self.packets(answer,calls)))

    def test_unknown_global_review_is_not_inherited_as_support(self):
        answer, calls = self.fixture()
        result, seen = self.check(self.packets(answer, calls), lambda e: {'label':'unknown','decided_by':None,'steps':[]})
        self.assertNotEqual(result['label'], 'supported')
        self.assertTrue(all(p.get('decided_by')!='native_numeric_table_fidelity' for p in result['packets']))

    def test_changed_packet_or_global_input_cannot_reuse_native_attestation(self):
        answer, calls = self.fixture()
        for location, field, value in [(1,'allowed_cents',999), (1,'patient_id','wrong-patient'), (0,'unit','dollars')]:
            with self.subTest(location=location, field=field):
                packets = self.packets(answer, calls)
                packet = packets[location]
                state = uncompact_json(json.loads(packet['text']))
                if location:
                    state['answer_rows'][0][field] = value
                else:
                    state['answer']['facts'] = {'unit':value}
                packet['text'] = json.dumps(state)
                packet['bytes'] = len(packet['text'].encode())
                _, seen = self.check(packets)
                self.assertIn(packet['id'] if location else 'patient_A', seen)

    def test_incomplete_unbound_text_boolean_or_mismatched_results_keep_typed_review(self):
        for kind in ['incomplete','unbound','text','boolean','wrong_amount','wrong_patient','missing_contract','source_clock']:
            with self.subTest(kind=kind):
                answer, calls = self.fixture()
                if kind=='incomplete':calls[0]['result']['truncated']=True
                if kind=='unbound':answer.pop('table_from_tool')
                if kind=='text':answer['table'][0]['note']='Unverified explanation'
                if kind=='boolean':answer['table'][0]['claims']=True
                if kind=='wrong_amount':answer['table'][0]['allowed_cents']=999
                if kind=='wrong_patient':answer['table'][0]['patient_id']='wrong'
                if kind=='missing_contract':calls[0]['result'].pop('source_contracts')
                if kind=='source_clock':calls[0]['result']['source_contracts']['as_of']='2031-01-01'
                packets = self.packets(answer,calls)
                self.assertTrue(all(not p.get('native_numeric_fidelity') for p in packets))


if __name__=='__main__':unittest.main()
