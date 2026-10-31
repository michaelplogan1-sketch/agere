"""The workspace shows complete saved rows without duplicating native execution payloads."""
import copy
import json
import unittest

from clinical_core.agere.deliverables.action_center import inspection_view


class WorkspacePayload(unittest.TestCase):
    def test_native_history_is_referenced_without_changing_patients_judgments_or_work(self):
        rows = [{'patient_id': 'A', 'quote': 'MRA held; source interpretation unresolved'},
                {'patient_id': 'B', 'quote': 'Other source retained'}]
        call = {'name': 'query_sql', 'input': {'sql': 'SELECT * FROM patient'},
                'result': {'result_view': 'agere_result_1', 'rows': rows,
                           'executed_query': 'SELECT * FROM patient'}}
        answer = {'status': 'partial', 'table_from_tool': 'agere_result_1', 'table': rows,
                  'investigation_review': {'status': 'repair', 'source_flags': ['A']}}
        context = {'id': 'ANS-fixture', 'answer': answer,
                   'patient_ids': ['A', 'B'], 'patient_rows': [{'patient_id': r['patient_id'], 'evidence_rows': [r]} for r in rows],
                   'graph': {'nodes': [{'parameters': {'query': 'retained old native SQL'}}]},
                   'planner_receipt': {'request_contract': {'kind': 'checked_graph', 'graph': {'large': 'old'}}},
                   'investigation': {'tool_calls': [call], 'document_reviews': [], 'answer_reviews': [{'status': 'repair'}]},
                   'stale': False, 'invalidated': False}
        record = {'context': context, 'items': [{'id': 'WI-1', 'state': 'needs_review', 'revision': 2}], 'counts': {'needs_review': 1}}
        prior = copy.deepcopy(record)
        result = inspection_view(record)
        for key in ('answer', 'patient_ids', 'patient_rows', 'stale', 'invalidated'):
            self.assertEqual(result['context'][key], context[key])
        self.assertEqual(result['items'], record['items'])
        self.assertEqual(result['counts'], record['counts'])
        self.assertTrue(result['context']['investigation']['complete_native_history_retained'])
        self.assertEqual(result['context']['investigation']['answer_reviews'], [{'status': 'repair'}])
        self.assertEqual(result['context']['graph']['nodes'][0]['output']['result_view'], 'agere_result_1')
        self.assertEqual(result['context']['planner_receipt']['request_contract']['graph'], result['context']['graph'])
        self.assertNotIn('tool_calls', result['context']['investigation'])
        self.assertEqual(record, prior)

    def test_noninvestigation_context_is_unchanged(self):
        record = {'context': {'id': 'ANS-fixture', 'answer': {'status': 'answered'}, 'graph': {'nodes': []}}, 'items': []}
        self.assertIs(inspection_view(record), record)


if __name__ == '__main__':
    unittest.main()
