"""Source-review continuation retains exact feedback without provider calls."""
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere.clinical import execution_state as E, semantic_analyst as S, backstop_agent as B


class NativeTools:
    def __init__(self):
        self.db = sqlite3.connect(':memory:')
        self.semantic = SimpleNamespace(db=self.db, _authorize=lambda *args: sqlite3.SQLITE_OK,
                                        _local=SimpleNamespace())
        self.semantic.register_native_result = lambda name, receipt: S.SemanticWarehouse.register_native_result(self.semantic, name, receipt)
        self.result_sources, self.executed_results, self.rows_by_record = {}, {}, {}
        self.seen = []

    def retain_result(self, out):
        return B.Tools.retain_result(self, out)

    def __call__(self, use):
        assert use['name'] == 'query_sql'
        cursor = self.db.execute(use['input']['sql'])
        columns = [c[0] for c in cursor.description]
        return self.retain_result({'columns': columns, 'rows': [dict(zip(columns, row)) for row in cursor.fetchall()], 'truncated': False})


def feedback():
    return {'status': 'repair', 'feedback': 'Inspect the exact source dependency. ' + json.dumps([{'record_id': 'r1', 'detail': 'retained source context ' * 20000}]) + ' Preserve the original population and report unknown dates.',
            'coverage': {'label': 'answers_partially', 'reasons': ['A period remains unresolved'], 'reject': False},
            'support': {'label': 'unknown', 'all_dependencies_checked': True,
                        'failed_packet_ids': ['opaque-7'], 'packets': [
                {'id': 'opaque-7', 'patient_id': 'A', 'label': 'unknown', 'reason': 'Ambiguous date',
                 'claim_feedback': [{'field': 'encounter_date', 'path': ['answer_rows', 0, 'encounter_date'],
                                     'label': 'unknown', 'source_record_ids': ['r1', 'r2'], 'quote': 'Source text. ' * 20000}]},
                {'id': 'opaque-supported', 'label': 'supported', 'claim_feedback': []}]}}


class NativeReviewFeedback(unittest.TestCase):
    def setUp(self):
        self.tools = NativeTools()
        self.addCleanup(self.tools.db.close)

    def test_complete_receipt_and_claim_paths_remain_exact_and_queryable(self):
        review = feedback()
        before = copy.deepcopy(review)
        ref = E.review_reference(review, self.tools)
        row = self.tools.db.execute('SELECT feedback_json,feedback_digest FROM ' + ref['result_view']).fetchone()
        self.assertEqual(json.loads(row[0]), before)
        self.assertEqual(row[1], ref['feedback_digest'])
        query = "SELECT json_extract(p.value,'$.id'), json_extract(p.value,'$.patient_id'), json_extract(c.value,'$.field'), json_extract(c.value,'$.source_record_ids') FROM " + ref['result_view'] + " r, json_each(r.feedback_json,'$.support.packets') p, json_each(p.value,'$.claim_feedback') c"
        self.assertEqual(self.tools.db.execute(query).fetchall(), [('opaque-7', 'A', 'encounter_date', '["r1","r2"]')])
        state = json.loads(E.messages('Keep the original population and dates', [], feedback=review, feedback_native=ref)[0]['content'])
        self.assertLess(len(json.dumps(state)), 6000)
        visible = state['current_review_feedback']
        self.assertTrue(visible['feedback'].startswith('Inspect the exact source dependency. '))
        self.assertTrue(visible['feedback'].endswith(' Preserve the original population and report unknown dates.'))
        self.assertEqual(visible['coverage']['label'], 'answers_partially')
        self.assertEqual(visible['unresolved_dependencies'], [{'id': 'opaque-7', 'patient_id': 'A', 'label': 'unknown', 'reason': 'Ambiguous date', 'claim_feedback_entries': 1}])
        self.assertEqual(visible['failed_dependency_ids'], ['opaque-7'])
        self.assertIn('not patient evidence', visible['native_feedback']['instruction'])
        self.assertEqual(review, before)

    def test_identical_feedback_reuses_one_view_and_changed_feedback_gets_new_identity(self):
        review = feedback()
        one = E.review_reference(review, self.tools)
        self.assertEqual(E.review_reference(review, self.tools), one)
        self.assertEqual(len(self.tools.executed_results), 1)
        review['support']['packets'][0]['claim_feedback'][0]['label'] = 'contradicted'
        two = E.review_reference(review, self.tools)
        self.assertNotEqual(one['feedback_digest'], two['feedback_digest'])
        self.assertNotEqual(one['result_view'], two['result_view'])
        with self.assertRaisesRegex(ValueError, 'current receipt'):
            E.messages('q', [], feedback=review, feedback_native=one)

    def test_changed_retained_feedback_is_rejected(self):
        review = feedback()
        ref = E.review_reference(review, self.tools)
        self.tools.executed_results[ref['result_view']]['rows'][0]['feedback_json'] = '{}'
        with self.assertRaisesRegex(ValueError, 'native view changed'):
            E.review_reference(review, self.tools)

    def test_compiler_repairs_and_non_native_callers_keep_existing_exact_feedback(self):
        review = {'status': 'repair', 'repair_kind': 'deterministic_compiler', 'feedback': 'Missing date scope.'}
        self.assertIsNone(E.review_reference(review, self.tools))
        self.assertEqual(json.loads(E.messages('q', [], feedback=review)[0]['content'])['current_review_feedback'], review)
        self.assertIsNone(E.review_reference(feedback(), lambda use: None))
        self.assertEqual(len(self.tools.executed_results), 0)

    def test_prose_is_preserved_without_prefix_or_word_ceiling(self):
        text = 'Review the scope [not JSON] and preserve every instruction. ' * 2000
        self.assertEqual(E.review_guidance(text), text)
        self.assertEqual(E.review_guidance('Keep the exact rule and [{"id":1}].'), 'Keep the exact rule and [{"id":1}].')
        text = 'Unicode: β. Before ' + json.dumps([{'quote': 'exact α text' * 200}]) + ' After the data, preserve this rule.'
        projected = E.review_guidance(text)
        import re
        start, length = map(int, re.search(r"\),([0-9]+),([0-9]+)\)", projected).groups())
        self.assertEqual(json.loads(text[start-1:start-1+length]), [{'quote': 'exact α text' * 200}])
        self.assertTrue(projected.endswith(' After the data, preserve this rule.'))

    def test_compiled_tool_loop_queries_the_retained_feedback_before_finishing(self):
        review = feedback()
        before = copy.deepcopy(review)
        sent = []

        def send(body):
            sent.append(copy.deepcopy(body))
            state = json.loads(body['messages'][0]['content'])
            ref = state['current_review_feedback']['native_feedback']
            self.assertLess(len(json.dumps(body)), 10000)
            if len(sent) == 1:
                sql = "SELECT json_extract(c.value,'$.label') AS unresolved_label FROM " + ref['result_view'] + " r,json_each(r.feedback_json,'$.support.packets') p,json_each(p.value,'$.claim_feedback') c"
                return {'content': [{'type': 'tool_use', 'id': 'inspect-review', 'name': 'query_sql', 'input': {'sql': sql}}]}, 0, False
            self.assertEqual(state['executed_results'][0]['computed_rows'], [{'unresolved_label': 'unknown'}])
            return {'content': [{'type': 'text', 'text': json.dumps({'status': 'partial', 'checked': True, 'missing': ['Ambiguous source date']})}]}, 0, False

        def assess(answer, calls):
            return {'status': 'confirmed'} if answer.get('checked') else review

        out = S.tool_loop('Original population and date question', 'system', [t for t in B.TOOLS if t['name'] == 'query_sql'], self.tools,
                          send=send, model='scripted', max_tools=None, review=assess,
                          initial_answer={'status': 'partial'}, compact_execution_state=True)
        self.assertIsNone(out.get('error'))
        self.assertEqual(out['answer']['status'], 'partial')
        self.assertEqual(out['answer']['missing'], ['Ambiguous source date'])
        self.assertEqual(len(out['tool_calls']), 1)
        self.assertEqual(review, before)
        self.assertEqual(sum(v.get('kind') == 'unverified_review_feedback' for v in self.tools.executed_results.values()), 1)


class ProductionFeedbackQuery(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        path = self.root / 'semantic.sqlite3'
        with sqlite3.connect(path) as db:
            db.executescript("CREATE TABLE meta(key,value); INSERT INTO meta VALUES('as_of','2030-05-27'),('source_digest','authored-review-fixture'); CREATE TABLE patient(patient_id); INSERT INTO patient VALUES('A');")
        self.wh = S.SemanticWarehouse(path)
        self.addCleanup(self.wh.close)
        self.tools = B.Tools(SimpleNamespace(path=self.root, allow_live=False), self.wh, '2030-05-27')
        self.addCleanup(self.tools.db.close)

    def query(self, sql):
        return self.tools({'name': 'query_sql', 'input': {'sql': sql}})

    def test_real_query_authorizer_json_paths_and_sequence_remain_correct(self):
        first = self.query('SELECT patient_id FROM patient')
        ref = E.review_reference(feedback(), self.tools)
        self.assertEqual(first['result_view'], 'agere_result_1')
        query = "SELECT json_extract(p.value,'$.id') AS packet_id,json_extract(c.value,'$.field') AS field FROM " + ref['result_view'] + " r,json_each(r.feedback_json,'$.support.packets') p,json_each(p.value,'$.claim_feedback') c"
        out = self.query(query)
        self.assertNotIn('error', out)
        self.assertEqual(out['rows'], [{'packet_id': 'opaque-7', 'field': 'encounter_date'}])
        self.assertEqual(out['result_view'], 'agere_result_2')
        self.assertFalse(out['truncated'])
        self.assertTrue(out['incomplete_dependencies'])
        self.assertEqual(self.query('SELECT patient_id FROM patient')['result_view'], 'agere_result_3')

    def test_review_derived_quotes_cannot_ground_a_citation_or_final_table(self):
        ref = E.review_reference(feedback(), self.tools)
        query = "SELECT json_extract(c.value,'$.source_record_ids[0]') AS record_id,json_extract(c.value,'$.quote') AS quote FROM " + ref['result_view'] + " r,json_each(r.feedback_json,'$.support.packets') p,json_each(p.value,'$.claim_feedback') c"
        out = self.query(query)
        self.assertNotIn('error', out)
        self.assertNotIn('r1', self.tools.rows_by_record)
        with patch.object(self.tools, 'read_document', side_effect=ValueError('No received document')):
            kept, dropped = B.verified_sources({'sources': [{'record_id': 'r1', 'quote': out['rows'][0]['quote']}]}, self.tools)
        self.assertEqual(kept, [])
        self.assertEqual(dropped, 1)
        with self.assertRaisesRegex(ValueError, 'incomplete|Incomplete'):
            B.materialize_table({'table_from_tool': out['result_view']}, [{'result': out}])


if __name__ == '__main__':
    unittest.main()
