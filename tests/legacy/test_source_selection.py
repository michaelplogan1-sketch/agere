import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest
import numpy as np

from clinical_core.agere.clinical import source_selection as S


class SourceSelectionTests(unittest.TestCase):
    def test_candidate_needs_explicit_selection_and_unknown_routes_fail_closed(self):
        from clinical_core.agere.clinical.stack_runtime import Stack
        stack=SimpleNamespace(path=Path('/unused'),retriever=object())
        with patch.object(S.Q,'prepare',return_value={'schema':'baseline'}) as baseline, patch.object(S,'prepare',return_value={'schema':'candidate'}) as candidate:
            self.assertEqual(Stack.prepare_question_context(stack,'P-1','Question')['schema'],'baseline')
            candidate.assert_not_called()
            self.assertEqual(Stack.prepare_question_context(stack,'P-1','Question','schema_candidate')['schema'],'candidate')
            baseline.assert_called_once();candidate.assert_called_once()
            with self.assertRaisesRegex(ValueError,'Unknown'):Stack.prepare_question_context(stack,'P-1','Question','promote_by_typo')

    def test_only_received_schema_and_bounded_label_samples_enter_descriptor(self):
        rows = [{'source_table': 'sample_labs', '_warehouse_fields': ['name', 'value', 'note'],
                 'name': 'Label '+str(i), 'value': i, 'note': 'Do not send this narrative',
                 'hidden_truth': 'secret'} for i in range(14)]
        result = S.descriptors(rows)[0]
        self.assertEqual(result['fields'], ['name', 'note', 'value'])
        self.assertEqual(result['received_records'], 14)
        self.assertEqual(len(result['sampled_labels']), 12)
        self.assertEqual(result['label_values_not_shown'], 2)
        self.assertNotIn('Do not send', result['text'])
        self.assertNotIn('hidden_truth', result['text'])
        self.assertNotIn('secret', result['text'])

    def test_profile_order_does_not_change_when_records_reordered(self):
        rows = [{'source_table': 'x', '_warehouse_fields': ['name'], 'name': x}
                for x in ('b', 'a', 'c', 'a')]
        self.assertEqual(S.descriptors(rows), S.descriptors(list(reversed(rows))))
        self.assertEqual(S.descriptors(rows)[0]['sampled_labels'], ['a', 'b', 'c'])

    def models(self, vectors):
        encoder = Mock()
        encoder.windows.side_effect = lambda text, **kw: [{'text': text}]
        encoder.encode.side_effect = lambda texts: np.array([vectors[t] for t in texts], dtype=float)
        ranker = Mock()
        ranker.rank.side_effect = lambda q, rs, **kw: [{**r, 'reranker_logit': float(i)} for i, r in enumerate(reversed(rs))]
        return encoder, ranker

    def test_low_similarity_abstains_but_exact_category_label_survives(self):
        rows = [{'id': 'test_orders', 'text': 'test'}, {'id': 'unrelated', 'text': 'other'}]
        encoder, reranker = self.models({'test orders': [1, 0], 'test': [0, 1], 'other': [0, 1]})
        result = S.rank('test orders', rows, encoder, reranker)
        self.assertEqual(result['selected_categories'], ['test_orders'])
        self.assertEqual(result['exact_categories'], ['test_orders'])
        self.assertIn('Uncalibrated', result['threshold_meaning'])

    def test_candidate_and_selection_limits_without_classifying_scores_as_probability(self):
        rows = [{'id': 'category_'+str(i), 'text': 'text'+str(i)} for i in range(20)]
        vectors = {r['text']: [1, i/100] for i, r in enumerate(rows)} | {'question': [1, 0]}
        encoder, reranker = self.models(vectors)
        result = S.rank('question', rows, encoder, reranker)
        self.assertEqual(len(result['candidates']), 12)
        self.assertEqual(len(result['selected_categories']), 4)
        self.assertEqual(len(result['dense_order']), 20)

    def test_complete_windows_are_embedded(self):
        encoder = Mock(); encoder.windows.return_value = [{'text': 'first'}, {'text': 'last'}]
        encoder.encode.return_value = np.array([[1., 0.], [0., 1.]])
        reranker = Mock(); reranker.rank.side_effect = lambda q, rs, **kw: rs
        S.rank('question', [{'id': 'x', 'text': 'long metadata'}], encoder, reranker)
        self.assertEqual(encoder.encode.call_count, 2)
        for call in encoder.encode.call_args_list: self.assertEqual(call.args[0], ['first', 'last'])

    def test_no_records_no_model_scoring(self):
        encoder, reranker = Mock(), Mock()
        self.assertEqual(S.rank('question', [], encoder, reranker)['selected_categories'], [])
        encoder.encode.assert_not_called(); reranker.rank.assert_not_called()

    def test_bounds_block_instead_of_dropping_categories_or_dispatching(self):
        with tempfile.TemporaryDirectory() as path:
            r = SimpleNamespace(encoder=SimpleNamespace(identity='e'), reranker=SimpleNamespace(identity='r'))
            records = [{'source_table': 'x', '_warehouse_fields': []}]
            preview = {'can_prepare': False, 'blockers': ['More than 200 records']}
            with patch.object(S.C, 'received', return_value=(records, {'snapshot': {'as_of': '2029-08-15'}})), \
                 patch.object(S, 'rank', return_value={'selected_categories': ['x']}), \
                 patch.object(S, 'preserve_references', return_value={'additional_categories': []}), \
                 patch.object(S.R, 'prepare', return_value={'comparison': {'id': 'c'}}), \
                 patch.object(S.Q, 'prepare', return_value={'id': 'default-scope', 'connected_record_ids': []}), \
                 patch.object(S.P, 'preview', return_value=preview), \
                 patch.object(S.P, 'prepare') as assemble:
                result = S.prepare(path, 'P-1', 'Original question', r)
                self.assertEqual(result['question'], 'Original question')
                self.assertEqual(result['status'], 'source_scope_needs_narrowing')
                self.assertIsNone(result['source_plan']); assemble.assert_not_called()
                repeated = S.prepare(path, 'P-1', 'Original question', r)
                self.assertEqual(result['id'], repeated['id']); self.assertTrue(repeated['cache_hit'])
                file = next(Path(path).glob('source-selections/*.json'))
                bad = json.loads(file.read_text()); bad['question'] = 'Changed'; file.write_text(json.dumps(bad))
                with self.assertRaisesRegex(ValueError, 'integrity'): S.prepare(path, 'P-1', 'Original question', r)
                bad['id'] = S.digest({k: v for k, v in bad.items() if k != 'id'}); file.write_text(json.dumps(bad))
                with self.assertRaisesRegex(ValueError, 'integrity'): S.prepare(path, 'P-1', 'Original question', r)

    def test_changed_patient_records_fail_before_saving_selection(self):
        with tempfile.TemporaryDirectory() as path:
            r = SimpleNamespace(encoder=SimpleNamespace(identity='e'), reranker=SimpleNamespace(identity='r'))
            with patch.object(S.C, 'received', side_effect=[([], {'snapshot': {'as_of': '2029-08-15'}}), ([{'changed': True}], {})]), \
                 patch.object(S, 'rank', return_value={'selected_categories': []}), \
                 patch.object(S, 'preserve_references', return_value={'additional_categories': []}), \
                 patch.object(S.R, 'prepare', return_value={'comparison': {'id': 'c'}}), \
                 patch.object(S.Q, 'prepare', return_value={'id': 'default-scope', 'connected_record_ids': []}), \
                 patch.object(S.P, 'preview', return_value={'can_prepare': False}):
                with self.assertRaisesRegex(ValueError, 'changed'): S.prepare(path, 'P-1', 'Question', r)
                self.assertFalse(list(Path(path).glob('source-selections/*.json')))

    def test_retains_baseline_typed_reference_component(self):
        rows = [{'record_id': 'a', 'source_table': 'first'}, {'record_id': 'b', 'source_table': 'linked'},
                {'record_id': 'c', 'source_table': 'already_included'}]
        comparison = {'variants': {'bge_reranked': ['p']}, 'candidates': [{'id': 'p', 'source': {'record_id': 'a'}}]}
        with patch.object(S.Q, 'select_sources', return_value={'connected_record_ids': ['a','b','c'], 'reference_edges': []}):
            result = S.preserve_references(rows, comparison, ['already_included'], '2029-08-15')
        self.assertEqual(result['additional_categories'], ['linked'])
        self.assertEqual(result['connected_record_ids'], ['a','b','c'])

    def test_completed_default_search_roots_remain_in_the_whole_record_plan(self):
        rows = [{'record_id': 'a', 'source_table': 'paired'},
                {'record_id': 'b', 'source_table': 'default_completion'},
                {'record_id': 'c', 'source_table': 'linked'}]
        comparison = {'variants': {'bge_reranked': ['p']}, 'candidates': [{'id': 'p', 'source': {'record_id': 'a'}}]}
        with patch.object(S.Q, 'select_sources', return_value={'connected_record_ids': ['a','b','c'], 'reference_edges': []}) as closure:
            result = S.preserve_references(rows, comparison, [], '2029-08-15', default_record_ids=['b'])
        self.assertEqual(closure.call_args.args[1], {'a', 'b'})
        self.assertEqual(result['additional_categories'], ['default_completion', 'linked'])
        self.assertEqual(result['default_reference_record_ids'], ['b'])


if __name__ == '__main__': unittest.main()
