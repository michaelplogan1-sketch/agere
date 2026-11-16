"""Current-medication scope is checked before population note review is bought."""
import copy
import unittest
from unittest.mock import patch

from clinical_core.agere.clinical import backstop_agent as B, drug_classes as D, execution_state as E


def native(view, sql, rows):
    return {'name': 'query_sql', 'input': {'sql': sql},
            'result': {'result_view': view, 'columns': list(rows[0]), 'rows': rows}}


HISTORY = native('agere_result_1',
    'SELECT patient_id, CASE WHEN patient_id IN {{drug_fill:mra}} THEN 1 ELSE 0 END AS on_mra FROM patient',
    [{'patient_id': 'A', 'on_mra': 1}])
CURRENT = native('agere_result_2',
    "SELECT patient_id, 0 AS on_mra FROM fill WHERE fill_date > date(:as_of, '-12 months') AND fill_date <= :as_of",
    [{'patient_id': 'A', 'on_mra': 0}])
QUESTION = 'What GDMT are they on, and why are missing pillars not prescribed?'


class PreDispatchScope(unittest.TestCase):
    def tools(self):
        t = B.Tools.__new__(B.Tools)
        t.question, t.native_calls = QUESTION, copy.deepcopy([HISTORY, CURRENT])
        t.document_field_contracts, t.document_reviews = {}, []
        return t

    def request(self, scope):
        return {'name': 'review_documents', 'input': {
            'patient_query_sql': 'SELECT patient_id FROM agere_result_2',
            'questions': {'reason': 'What reason is documented?'},
            'field_patient_queries': {'reason': scope}}}

    def test_stale_conditional_scope_is_rejected_before_any_source_dispatch(self):
        t = self.tools()
        with patch('clinical_core.agere.clinical.document_review.review') as dispatch:
            with self.assertRaisesRegex(ValueError, 'No source review was dispatched.*reason:.*agere_result_1'):
                t._call(self.request('SELECT patient_id FROM agere_result_1 WHERE on_mra=0'))
        dispatch.assert_not_called()
        self.assertEqual(t.document_field_contracts, {})

    def test_current_scope_and_historical_membership_only_remain_available(self):
        t = self.tools()
        for sql in ('SELECT patient_id FROM agere_result_2 WHERE on_mra=0',
                    'SELECT patient_id FROM agere_result_1'):
            with patch('clinical_core.agere.clinical.document_review.review', return_value={'ok': True}) as dispatch:
                self.assertEqual(t._call(self.request(sql)), {'ok': True})
            dispatch.assert_called_once()

    def test_explicit_historical_question_remains_available(self):
        t = self.tools(); t.question = 'Review historical GDMT fills ever observed'
        with patch('clinical_core.agere.clinical.document_review.review', return_value={'ok': True}) as dispatch:
            self.assertEqual(t._call(self.request('SELECT patient_id FROM agere_result_1 WHERE on_mra=0')), {'ok': True})
        dispatch.assert_called_once()

    def test_nested_membership_filter_keeps_stale_column_dependency(self):
        calls = [HISTORY, CURRENT, native('agere_result_3',
            'SELECT c.patient_id FROM agere_result_2 c WHERE c.patient_id IN (SELECT patient_id FROM agere_result_1 WHERE on_mra=0)',
            [{'patient_id': 'A'}])]
        self.assertTrue(D.patient_scope_issues(QUESTION, calls, {'reason': 'SELECT patient_id FROM agere_result_3'}))

    def test_query_warning_is_advisory_and_survives_compiled_state(self):
        t = self.tools(); t.native_calls = []; t.semantic = None
        t.seen, t.rows_by_record = [], {}
        with patch.object(t, '_call', return_value=copy.deepcopy(HISTORY['result'])):
            out = t({'name': 'query_sql', 'input': HISTORY['input']})
        self.assertEqual(out['rows'], HISTORY['result']['rows'])
        self.assertIn('all-history', out['scope_warnings'][0])
        self.assertEqual(E.catalog(t.native_calls)[0]['scope_warnings'], out['scope_warnings'])
        self.assertEqual(len(t.native_calls), 1)


if __name__ == '__main__':
    unittest.main()
