"""Historical source populations survive current-membership dependencies and review."""
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from clinical_core.agere.clinical import backstop_agent as B, decision_context as D
from clinical_core.agere.clinical import plan_fence as P, system_one_gate as G


QUESTION = ('For patients with recorded COPD evidence and a 2029 respiratory encounter, '
            'distinguish reported use and pharmacy dispensing.')


def native(view, sql):
    return {'name': 'query_sql', 'input': {'sql': sql}, 'result': {
        'result_view': view, 'executed_query': sql,
        'columns': ['patient_id'], 'rows': [{'patient_id': 'A'}], 'truncated': False}}


class HistoricalPopulation(unittest.TestCase):
    def test_policy_filters_and_nested_dependencies_are_detected(self):
        for sql in [
            'SELECT patient_id FROM condition_member',
            'SELECT patient_id FROM latest_lab',
            'SELECT patient_id FROM medicare_member',
            'SELECT patient_id FROM phenotype_hfref',
            'WITH x AS(SELECT * FROM condition_member) SELECT patient_id FROM x',
            'SELECT * FROM patient WHERE patient_id IN(SELECT patient_id FROM condition_member)',
            'SELECT p.* FROM patient p JOIN condition_member c USING(patient_id)',
            'SELECT p.* FROM patient p LEFT JOIN condition_member c USING(patient_id) WHERE c.patient_id IS NOT NULL',
            'SELECT * FROM patient WHERE patient_id NOT IN(SELECT patient_id FROM death)',
            'SELECT p.* FROM patient p WHERE NOT EXISTS(SELECT 1 FROM death d WHERE d.patient_id=p.patient_id)',
        ]:
            with self.subTest(sql=sql):self.assertTrue(D._living_restriction(sql))

    def test_optional_current_status_and_unused_exploration_do_not_narrow_the_population(self):
        for sql in [
            'SELECT * FROM condition_evidence',
            "SELECT 'FROM condition_member' AS label FROM patient",
            'WITH condition_member AS(SELECT * FROM patient) SELECT * FROM condition_member',
            'WITH unused AS(SELECT * FROM condition_member) SELECT * FROM patient',
            'SELECT p.*,c.concept FROM patient p LEFT JOIN condition_member c USING(patient_id)',
            'SELECT c.concept,p.* FROM condition_member c RIGHT JOIN patient p USING(patient_id)',
            'SELECT patient_id,(SELECT count(*) FROM condition_member) AS current_n FROM patient',
            'SELECT patient_id,CASE WHEN patient_id NOT IN(SELECT patient_id FROM death) THEN 1 ELSE 0 END AS living FROM patient',
        ]:
            with self.subTest(sql=sql):self.assertFalse(D._living_restriction(sql))

    def calls(self):
        return [native('agere_result_1', 'SELECT patient_id FROM condition_member'),
                native('agere_result_2', 'SELECT patient_id FROM agere_result_1'),
                native('agere_result_3', 'SELECT patient_id FROM condition_evidence'),
                {'name': 'compose_table', 'input': {'base': 'agere_result_3'}, 'result': {
                    'result_view': 'agere_result_4', 'assembly': {'base_result_view': 'agere_result_3'},
                    'columns': ['patient_id'], 'rows': [{'patient_id': 'A'}]}}]

    def test_native_population_lineage_checks_the_final_base_not_the_whole_history(self):
        calls = self.calls()
        self.assertEqual(D.living_policy_results(calls), {'agere_result_1','agere_result_2'})
        self.assertTrue(D.population_scope_issues(QUESTION, calls=calls, result_view='agere_result_2'))
        self.assertFalse(D.population_scope_issues(QUESTION, calls=calls, result_view='agere_result_4'))
        calls[-1]['input']['base'] = calls[-1]['result']['assembly']['base_result_view'] = 'agere_result_2'
        self.assertTrue(D.population_scope_issues(QUESTION, calls=calls, result_view='agere_result_4'))
        self.assertFalse(D.population_scope_issues(QUESTION+' For living patients only.', calls=calls, result_view='agere_result_4'))

    def test_source_review_stops_before_dispatch_on_an_excluded_population(self):
        tools = B.Tools.__new__(B.Tools)
        tools.question,tools.native_calls = QUESTION,self.calls()
        tools.document_field_contracts,tools.document_reviews = {},[]
        for sql in ['SELECT patient_id FROM condition_member','SELECT patient_id FROM agere_result_2']:
            with patch('clinical_core.agere.clinical.document_review.review') as dispatch:
                with self.assertRaisesRegex(ValueError,'No source review was dispatched.*living-only'):
                    tools._call({'name':'review_documents','input':{'patient_query_sql':sql,
                        'questions':{'use':'What medication use is reported?'}}})
            dispatch.assert_not_called()
        with patch('clinical_core.agere.clinical.document_review.review',return_value={'ok':True}) as dispatch:
            tools._call({'name':'review_documents','input':{'patient_query_sql':'SELECT patient_id FROM agere_result_3',
                'questions':{'use':'What medication use is reported?'}}})
        dispatch.assert_called_once()

    def test_final_review_and_saved_premises_reject_before_paid_judgment(self):
        calls = self.calls()
        answer={'status':'answered','answer_type':'table','table_from_tool':'agere_result_2','headline':'Recorded evidence'}
        with patch.object(G,'ready',return_value=False) as ready:
            verdict=B.review_final(SimpleNamespace(),QUESTION,copy.deepcopy(answer),calls,
                SimpleNamespace(read={},document_reviews=[]),ledger=None,as_of='2030-05-27')
        self.assertEqual(verdict['repair_kind'],'native_population_scope')
        ready.assert_not_called()
        with patch.object(P,'native_premise_graph',return_value=({'nodes':[]},[])):
            issues=P.premise_issues(None,QUESTION,{'nodes':[]},schema={},native_calls=calls,
                answer={'table_binding':{'result_view':'agere_result_2'}})
        self.assertTrue(any('population:' in issue for issue in issues))


if __name__ == '__main__':unittest.main()
