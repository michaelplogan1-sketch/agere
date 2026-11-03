"""Native value fidelity never replaces question/source meaning review."""
import copy
import json
import sqlite3
import unittest

from clinical_core.agere.clinical import backstop_agent as B
from clinical_core.agere.deliverables.receipts import uncompact_json


class NumericFidelity(unittest.TestCase):
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
