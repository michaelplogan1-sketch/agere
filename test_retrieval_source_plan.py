import json
import sqlite3
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.contracts import digest
from clinical_core.agere.clinical import retrieval_source_plan as P


class SourcePlanTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.r = SimpleNamespace(path=Path(temp.name))
        self.meta = {'source_run': 's', 'source_digest': 'd', 'snapshot': {'as_of': '2029-08-15'}}
        self.records = [self.record('n', 'notes'), self.record('m', 'medication_orders'),
                        self.record('past', 'medication_orders', '2027-01-01'), self.record('unknown', 'medication_orders', None)]
        self.report = {'id': 'comparison', 'identity': {'source_run': 's', 'source_digest': 'd'},
                       'request': {'patient': 'P-1', 'question': 'What do the records say?',
                                   'analysis_window': {'basis': 'all_observed', 'start': '', 'end': ''}},
                       'variants': {'bge_reranked': ['1', '2'], 'rrf_baseline': ['1']},
                       'candidates': [{'id': i, 'source': {'record_id': 'n'}} for i in ('1', '2')]}
        self.contexts = {}
        def create(path, patient, question, ids):
            c = {'patient_id': patient, 'question': question, 'record_scope': {'record_ids': ids},
                 'dispatch': {'source_review_allowed': True}}
            c['id'] = digest(c); self.contexts[c['id']] = c; return c
        for target, name, kwargs in [
            (P.R, 'inspect', {'side_effect': lambda *a: deepcopy(self.report)}),
            (P.C, 'received', {'side_effect': lambda *a: (deepcopy(self.records), deepcopy(self.meta))}),
            (P.C, 'create', {'side_effect': create}),
            (P.C, 'inspect', {'side_effect': lambda path, key: deepcopy(self.contexts[key])})]:
            p = patch.object(target, name, **kwargs); mock = p.start(); self.addCleanup(p.stop)
            if name == 'create': self.create = mock
        patcher = patch.object(P.F, 'create', side_effect=lambda path, key: deepcopy(self.contexts[key]))
        patcher.start(); self.addCleanup(patcher.stop)
        patcher = patch.object(P.S, 'preview', return_value={'source_passes': 1})
        patcher.start(); self.addCleanup(patcher.stop)
        self.request = {'comparison_id': 'comparison', 'baseline': 'bge_reranked', 'categories': ['medication_orders']}

    def record(self, rid, stream, date='2029-01-01'):
        return {'record_id': rid, 'patient_id': 'P-1', 'source_table': stream,
                'event_date': date, 'available_date': '2029-02-01'}

    def prepare(self):
        p = P.preview(self.r, self.request)
        return P.prepare(self.r, {'request': self.request, 'preview_id': p['id']})

    def test_adds_all_category_records_without_top_k_and_deduplicates_passages(self):
        p = P.preview(self.r, self.request)
        self.assertEqual(p['baseline_record_ids'], ['n'])
        self.assertEqual(p['added_record_ids'], ['m', 'past', 'unknown'])
        self.assertEqual(len(p['selected_record_ids']), 4)
        self.assertEqual(len(p['baseline_passage_ids']), 2)
        self.assertIn('Whole received records', p['representation'])

    def test_explicit_window_excludes_old_and_unknown_without_substituting_arrival(self):
        self.report['request']['analysis_window'] = {'basis': 'source_event', 'start': '2029-01-01', 'end': '2029-08-15'}
        p = P.preview(self.r, self.request)
        self.assertEqual(p['added_record_ids'], ['m'])
        self.assertEqual({r['record_id']: r['reason'] for r in p['category_records_excluded_by_date']}, {'past': 'outside_window', 'unknown': 'unknown_date'})
        self.assertIn('source_event dates from 2029-01-01', p['review_question'])
        self.assertEqual(p['excluded_records'], 2)

    def test_arrival_scope_is_distinct(self):
        self.report['request']['analysis_window'] = {'basis': 'arrival', 'start': '2029-01-01', 'end': '2029-08-15'}
        self.assertEqual(len(P.preview(self.r, self.request)['selected_record_ids']), 4)

    def test_baseline_outside_window_fails_instead_of_broadening_scope(self):
        self.report['request']['analysis_window'] = {'basis': 'source_event', 'start': '2029-02-01', 'end': ''}
        with self.assertRaisesRegex(ValueError, 'disagrees'): P.preview(self.r, self.request)

    def test_empty_additions_support_baseline_whole_record_comparison(self):
        self.request['categories'] = []
        p = P.preview(self.r, self.request)
        self.assertEqual(p['selected_record_ids'], ['n']); self.assertEqual(p['excluded_records'], 3)

    def test_invalid_scope_and_category_input_rejected(self):
        for bad in ({**self.request, 'patient': 'P-2'}, {**self.request, 'categories': ['unreceived']},
                    {**self.request, 'categories': ['notes', 'notes']}, {**self.request, 'categories': 'notes'},
                    {**self.request, 'baseline': 'made_up'}, {**self.request, 'categories': [None]}):
            with self.subTest(bad=bad), self.assertRaises(ValueError): P.preview(self.r, bad)

    def test_population_and_missing_baseline_record_rejected(self):
        self.report['request']['patient'] = None
        with self.assertRaises(ValueError): P.preview(self.r, self.request)
        self.report['request']['patient'] = 'P-1'; self.records = self.records[1:]
        with self.assertRaisesRegex(ValueError, 'not received'): P.preview(self.r, self.request)

    def test_snapshot_mismatch_rejected(self):
        self.meta['source_run'] = 'new'
        with self.assertRaisesRegex(ValueError, 'snapshot'): P.preview(self.r, self.request)

    def test_record_bound_is_visible_and_no_partial_plan_created(self):
        self.records += [self.record(str(i), 'medication_orders') for i in range(200)]
        p = P.preview(self.r, self.request)
        self.assertFalse(p['can_prepare']); self.assertEqual(len(p['selected_record_ids']), 204)
        with self.assertRaisesRegex(ValueError, '200 records'): self.prepare()
        self.create.assert_not_called()

    def test_long_question_is_not_silently_truncated(self):
        self.report['request']['question'] = 'Q' * 1400
        p = P.preview(self.r, self.request)
        self.assertFalse(p['can_prepare']); self.assertTrue(p['review_question'].startswith('Q' * 1400))

    def test_exact_replay_and_immutable_storage(self):
        first = self.prepare(); again = self.prepare()
        self.assertTrue(again['cache_hit']); self.assertEqual(first['plan'], again['plan']); self.create.assert_called_once()
        self.assertEqual(P.inspect(self.r, {'id': first['plan']['id']})['contexts'], first['contexts'])
        with P.database(self.r.path) as db:
            with self.assertRaises(sqlite3.IntegrityError): db.execute('DELETE FROM plans')

    def test_changed_sources_reject_preview_and_saved_plan(self):
        p = P.preview(self.r, self.request); saved = self.prepare()
        self.records[1]['status'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'preview changed'): P.prepare(self.r, {'request': self.request, 'preview_id': p['id']})
        with self.assertRaisesRegex(ValueError, 'evidence changed'): P.inspect(self.r, {'id': saved['plan']['id']})

    def test_changed_prepared_context_rejected(self):
        saved = self.prepare(); self.contexts[saved['contexts'][0]['id']]['question'] = 'different'
        with self.assertRaisesRegex(ValueError, 'context changed'): P.inspect(self.r, {'id': saved['plan']['id']})

    def test_changed_sources_during_context_build_leave_no_saved_plan(self):
        original = self.create.side_effect
        def changing(*a):
            c = original(*a); self.records[0]['status'] = 'changed'; return c
        self.create.side_effect = changing
        with self.assertRaisesRegex(ValueError, 'during'): self.prepare()
        with P.database(self.r.path) as db: self.assertEqual(db.execute('SELECT COUNT(*) FROM plans').fetchone()[0], 0)

    def test_incomplete_context_stays_blocked_and_is_not_misreported(self):
        original = self.create.side_effect
        def incomplete(*a):
            c = original(*a); c['dispatch']['source_review_allowed'] = False; return c
        self.create.side_effect = incomplete
        r = self.prepare()
        self.assertEqual(r['plan']['status'], 'context_incomplete')
        self.assertTrue(all(not c['dispatch']['source_review_allowed'] for c in r['contexts']))

    def test_large_scope_splits_whole_records_and_accounts_for_every_id(self):
        original = self.create.side_effect
        def bounded(*a):
            c = original(*a); c['dispatch']['source_review_allowed'] = len(a[-1]) <= 2; return c
        self.create.side_effect = bounded
        r = self.prepare()
        self.assertEqual(len(r['plan']['groups']), 2)
        self.assertEqual(sorted(rid for c in r['contexts'] for rid in c['record_scope']['record_ids']), ['m', 'n', 'past', 'unknown'])
        self.assertEqual(r['plan']['cross_group_interpretation'], 'not_assessed')
        self.assertEqual(r['plan']['status'], 'declared_record_scope_prepared')

    def test_non_size_planner_failure_is_not_hidden_by_partitioning(self):
        with patch.object(P.S, 'preview', side_effect=ValueError('source dependency changed')):
            with self.assertRaisesRegex(ValueError, 'dependency changed'): self.prepare()


if __name__ == '__main__': unittest.main()
