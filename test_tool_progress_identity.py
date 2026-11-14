"""Repeated native evidence must not masquerade as progress through new handles."""
import copy
import json
import unittest

from clinical_core.agere.clinical import semantic_analyst as S


class ToolProgressIdentity(unittest.TestCase):
    def run_loop(self, results, *, stronger=None, review=None, draft=None):
        outputs = iter(copy.deepcopy(results))
        models = []
        uses = []

        def send(body):
            models.append(body['model'])
            if draft is not None and len(models) == 1:
                blocks = [{'type': 'text', 'text': json.dumps(draft)}]
            elif len(uses) < len(results):
                blocks = [{'type': 'tool_use', 'id': 'call_' + str(len(models)),
                           'name': 'query_sql', 'input': {'sql': 'SELECT * FROM received'}}]
            else:
                blocks = [{'type': 'text', 'text': json.dumps({'status': 'partial', 'headline': 'Review remains open.'})}]
            return {'content': blocks}, 0, False

        def execute(use):
            uses.append(use)
            return next(outputs)

        tools = [{'name': 'query_sql', 'input_schema': {'type': 'object',
                  'properties': {'sql': {'type': 'string'}}, 'required': ['sql']}}]
        out = S.tool_loop('Inspect received observations.', 'system', tools, execute,
                          send=send, model='cheap', escalation_model=stronger,
                          max_tools=None, review=review)
        return out, models, uses

    def result(self, number, value=12, **extra):
        return {'result_view': 'agere_result_' + str(number), 'columns': ['patient_id', 'value'],
                'rows': [{'patient_id': 'P-A', 'value': value}], 'truncated': False,
                'executed_query': 'SELECT * FROM received', 'elapsed_s': number / 10, **extra}

    def test_new_view_and_transport_ids_do_not_hide_identical_evidence(self):
        results = [self.result(i) for i in range(1, 5)]
        original = copy.deepcopy(results)
        out, models, uses = self.run_loop(results)
        self.assertEqual(len(models), 2)
        self.assertIn('made no progress', out['error'])
        self.assertEqual([c['result'] for c in out['tool_calls']], original[:2])
        self.assertEqual(results, original)

    def test_same_evidence_escalates_once_in_the_existing_conversation(self):
        results = [self.result(i) for i in range(1, 4)]
        out, models, uses = self.run_loop(results, stronger='strong')
        self.assertEqual(models, ['cheap', 'cheap', 'strong'])
        self.assertEqual(len(out['model_escalations']), 1)
        self.assertEqual(out['model_escalations'][0]['retained_tool_calls'], 2)
        self.assertEqual(len(out['tool_calls']), 3)
        self.assertIn('made no progress', out['error'])

    def test_changed_values_and_return_to_an_earlier_value_are_progress(self):
        results = [self.result(i, value) for i, value in enumerate([12, 13, 12], 1)]
        out, models, uses = self.run_loop(results)
        self.assertEqual(len(models), 4)
        self.assertIsNone(out.get('error'))
        self.assertEqual([c['result'] for c in out['tool_calls']], results)

    def test_business_cells_named_like_transport_metadata_remain_evidence(self):
        for column in ('result_view', 'attempt_id', 'request_hash', 'elapsed_s'):
            with self.subTest(column=column):
                results = [self.result(1), self.result(2)]
                for i, result in enumerate(results):
                    result['columns'].append(column)
                    result['rows'][0][column] = 'source_' + str(i)
                out, models, uses = self.run_loop(results)
                self.assertEqual(len(models), 3)
                self.assertIsNone(out.get('error'))

    def test_changed_source_contract_is_progress_even_when_values_match(self):
        results = [self.result(1, source_contract={'as_of': '2029-01-01'}),
                   self.result(2, source_contract={'as_of': '2030-01-01'})]
        out, models, uses = self.run_loop(results)
        self.assertEqual(len(models), 3)
        self.assertIsNone(out.get('error'))

    def test_unresolved_review_retains_partial_answer_without_escalating(self):
        draft = {'status': 'partial', 'headline': 'The source date remains unknown.'}
        review = lambda answer, calls: {'status': 'repair', 'repair_kind': 'unresolved_verification',
                                       'feedback': 'The source date remains unresolved.'}
        out, models, uses = self.run_loop([self.result(i) for i in range(1, 5)],
                                         stronger='strong', review=review, draft=draft)
        self.assertEqual(models, ['cheap', 'cheap', 'cheap'])
        self.assertEqual(out['review_stopped'], 'no_progress')
        self.assertEqual(out['answer']['headline'], draft['headline'])
        self.assertEqual(out['answer']['verification'], 'unverified')
        self.assertNotIn('model_escalations', out)


if __name__ == '__main__':
    unittest.main()
