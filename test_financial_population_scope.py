"""Financial source membership is independent of care-liveness defaults."""
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere.clinical import source_scope as S,analysis_tools as T,analysis_graph as A

QUESTION='Among patients with a recorded outstanding balance at the snapshot, distinguish amounts from documentary affordability statements.'


class FinancialPopulationScope(unittest.TestCase):
    def check(self,query,question=QUESTION,**kwargs):
        return S.financial_population_problem(question,query,**kwargs)

    def test_actual_failed_query_and_alias_cte_variants_are_rejected(self):
        for query in ('SELECT patient_id FROM living_patient',
                      'SELECT l.patient_id FROM temp.living_patient l',
                      'WITH selected AS (SELECT patient_id FROM living_patient) SELECT * FROM selected'):
            with self.subTest(query=query):self.assertIn('Recorded balances',self.check(query))

    def test_explicit_living_and_exact_inherited_or_full_membership_remain_available(self):
        query='SELECT patient_id FROM living_patient'
        self.assertIsNone(self.check(query,'Show outstanding balances for living patients.'))
        self.assertIsNone(self.check(query,'Show balances for patients who are still alive.'))
        self.assertIsNone(self.check(query,selected_ids=['A'],inherited_ids=['A']))
        self.assertIsNone(self.check(query,selected_ids=['A','D'],available_ids=['D','A']))
        self.assertIsNone(self.check(query,selected_ids=[],inherited_ids=[]))
        self.assertIsNotNone(self.check(query,selected_ids=['A'],inherited_ids=['A','D']))
        self.assertIsNotNone(self.check(query,'Include living and deceased patients.'))
        self.assertIsNotNone(self.check(query,'Not only living patients: inspect every recorded balance.'))

    def test_whole_source_and_cte_shadowing_do_not_impose_a_life_filter(self):
        self.assertIsNone(self.check('SELECT patient_id FROM patient'))
        self.assertIsNone(self.check('WITH living_patient AS (SELECT patient_id FROM patient) SELECT * FROM living_patient'))
        self.assertIsNone(self.check(None))

    def test_retained_query_membership_keeps_actual_living_lineage(self):
        calls=[{'name':'query_sql','input':{'sql':'SELECT patient_id FROM living_patient'},
                'result':{'result_view':'agere_result_1','columns':['patient_id'],'rows':[['A']]}}]
        self.assertIsNotNone(S.financial_population_problem(QUESTION,'SELECT patient_id FROM agere_result_1',calls))
        calls[0]['input']['sql']='SELECT patient_id FROM patient'
        self.assertIsNone(S.financial_population_problem(QUESTION,'SELECT patient_id FROM agere_result_1',calls))

    def test_execute_rejects_before_financial_engine_and_preserves_other_operations(self):
        db=sqlite3.connect(':memory:');db.execute('CREATE TABLE patient(patient_id TEXT)')
        db.executemany('INSERT INTO patient VALUES (?)',[('A',),('D',)])
        tools=SimpleNamespace(question=QUESTION,cohort=None,db=db,native_calls=[],as_of='2030-05-27',
            continuation_identity=lambda:{},stack=SimpleNamespace(retriever=SimpleNamespace(meta={'snapshot':{'as_of':'2030-05-27'}})))
        try:
            with patch.object(T,'population',return_value=['A']),patch.object(A,'validate',side_effect=RuntimeError('reached normal graph validation')):
                with self.assertRaisesRegex(ValueError,'financial population'):
                    T.execute(tools,'financial_accounts',{},patient_query_sql='SELECT patient_id FROM living_patient')
                tools.question='Show balances for living patients.'
                with self.assertRaisesRegex(RuntimeError,'reached normal'):
                    T.execute(tools,'financial_accounts',{},patient_query_sql='SELECT patient_id FROM living_patient')
                tools.question=QUESTION
                with self.assertRaisesRegex(RuntimeError,'reached normal'):
                    T.execute(tools,'source_state',{},patient_query_sql='SELECT patient_id FROM living_patient')
        finally:db.close()


if __name__=='__main__':unittest.main()
