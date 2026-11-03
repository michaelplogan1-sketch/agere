"""Final native dependencies obey the same provider-source rule as typed plans."""
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from clinical_core.agere.clinical import backstop_agent as B, system_one_gate as G


def call(view, sql, rows=None):
    rows = rows or [{'patient_id': 'P-A', 'claims': 3, 'allowed_cents': 1200}]
    return {'name': 'query_sql', 'input': {'sql': sql}, 'result': {
        'result_view': view, 'executed_query': sql, 'columns': list(rows[0]),
        'rows': rows, 'truncated': False}}


class InvestigatorSourceScope(unittest.TestCase):
    question = 'List patients with at least 3 provider claims in 2029 and total allowed_cents.'

    def review(self, calls, answer=None, question=None):
        answer = copy.deepcopy(answer or {'status': 'answered', 'answer_type': 'table',
                                         'headline': 'Claims by patient', 'table_from_tool': calls[-1]['result']['result_view']})
        with patch.object(G, 'ready', return_value=False) as ready:
            verdict = B.review_final(SimpleNamespace(), question or self.question, answer, calls,
                                     SimpleNamespace(read={}, document_reviews=[]), ledger=None, as_of='2030-05-27')
        return verdict, ready.call_count

    def test_wrong_provider_source_rejected_before_semantic_verification(self):
        for sql in ('SELECT patient_id, COUNT(*) FROM payer_claim GROUP BY patient_id',
                    'SELECT patient_id, COUNT(*) FROM claim GROUP BY patient_id',
                    "SELECT patient_id, COUNT(*) FROM claim WHERE claim_source='medical_claims' OR allowed_cents>0 GROUP BY patient_id"):
            with self.subTest(sql=sql):
                verdict, checks = self.review([call('agere_result_1', sql)])
                self.assertEqual(verdict['status'], 'repair')
                self.assertEqual(verdict['repair_kind'], 'deterministic_compiler')
                self.assertIn('provider claims', verdict['feedback'])
                self.assertEqual(checks, 0)

    def test_intermediate_alias_cannot_hide_the_wrong_source(self):
        calls = [call('agere_result_1', 'SELECT * FROM payer_claim'),
                 call('agere_result_2', 'SELECT patient_id, SUM(allowed_cents) FROM agere_result_1 GROUP BY patient_id')]
        verdict, checks = self.review(calls)
        self.assertEqual(verdict['status'], 'repair')
        self.assertEqual(checks, 0)

    def test_corrected_source_ignores_unused_diagnostic_query(self):
        calls = [call('agere_result_1', 'SELECT * FROM payer_claim'),
                 call('agere_result_2', "SELECT * FROM claim WHERE claim_source='medical_claims'"),
                 call('agere_result_3', 'SELECT patient_id, SUM(allowed_cents) FROM agere_result_2 GROUP BY patient_id')]
        verdict, checks = self.review(calls)
        self.assertEqual(verdict['status'], 'unavailable')  # semantic review is intentionally disabled
        self.assertIsNone(verdict['feedback'])
        self.assertEqual(checks, 1)

    def test_bound_prose_cannot_import_an_unrestricted_total_into_a_correct_table(self):
        calls = [call('agere_result_1', "SELECT * FROM claim WHERE claim_source='medical_claims'"),
                 call('agere_result_2', 'SELECT COUNT(*) AS n FROM payer_claim', [{'n': 99}])]
        answer = {'status': 'answered', 'answer_type': 'table', 'table_from_tool': 'agere_result_1',
                  'headline': {'template': '{n} provider claims', 'bindings': {
                      'n': {'result_view': 'agere_result_2', 'row': 0, 'column': 'n'}}}}
        verdict, checks = self.review(calls, answer)
        self.assertEqual(verdict['status'], 'repair')
        self.assertEqual(checks, 0)

    def test_explicit_payer_or_combined_question_preserves_its_source_scope(self):
        calls = [call('agere_result_1', 'SELECT patient_id, COUNT(*) FROM payer_claim GROUP BY patient_id')]
        for question in ('List payer claims by patient.', 'Compare provider claims and payer claims by patient.'):
            verdict, checks = self.review(calls, question=question)
            self.assertEqual(verdict['status'], 'unavailable')
            self.assertIsNone(verdict['feedback'])
            self.assertEqual(checks, 1)


if __name__ == '__main__':
    unittest.main()
