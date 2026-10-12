import json
import sqlite3
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clinical_core.agere.clinical import retrieval_comparison as R
from clinical_core.contracts import digest


class Reranker:
    identity = 'fixed-test-reranker'

    def __init__(self):
        self.stats = {'pairs_scored': 0, 'cache_hits': 0}

    def rank(self, query, rows, *, top_k):
        self.stats['pairs_scored'] += len(rows)
        return [{**r, 'reranker_logit': float(i)} for i, r in reversed(list(enumerate(rows)))][:top_k]


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.r = SimpleNamespace(path=Path(self.temp.name),
                                 meta={'source_run': 's', 'source_digest': 'd', 'snapshot': {'as_of': '2029-08-15'}},
                                 encoder=SimpleNamespace(identity='fixed-test-encoder'), reranker=Reranker())
        self.original = self.r.reranker
        self.r.db = sqlite3.connect(':memory:'); self.addCleanup(self.r.db.close)
        self.r.db.execute('CREATE TABLE records(id TEXT,patient TEXT,payload TEXT)')
        self.rows = []
        for i in range(12):
            record = {'source_table': 'labs', 'value': i, 'unit': 'synthetic-unit', 'record_id': str(i)}
            text = list(R.W.representations(record))[0]['text']
            source = {'record_id': str(i), 'patient_id': 'P-1', 'source_table': 'labs', 'field': 'structured_fields',
                      'record_hash': digest(record), 'text_field_hash': digest(text), 'start': 0, 'end': len(text), 'quote': text}
            part = list(R.W.representations(record))[0]
            source.update(kind=part['kind'], structured_fields=part.get('fields'))
            self.rows.append({'id': str(i), 'text': text, 'source': source, 'note_id': str(i), 'start': 0, 'protected': False, 'rrf_score': 1/(61+i)})
            self.r.db.execute('INSERT INTO records VALUES (?,?,?)', (str(i), 'P-1', json.dumps(record)))
        self.calls = 0
        def search(question, *, patient, patient_ids, analysis_window):
            self.calls += 1
            ranked = self.r.reranker.rank(question, deepcopy(self.rows), top_k=len(self.rows)) if self.rows else []
            return {'evidence': R.selected(ranked, patient), 'candidate_passages': len(self.rows), 'elapsed_seconds': 0.1}
        self.r.search = search
        p = patch.object(R.cohorts, 'select', return_value={'patient_ids': ['P-1'], 'counts': {'matched': 1}})
        p.start(); self.addCleanup(p.stop)
        self.body = {'question': 'What do the records say?', 'patient': 'P-1'}

    def test_paired_rankings_capture_promoted_and_omitted_evidence(self):
        result = R.prepare(self.r, self.body)['comparison']
        self.assertEqual(result['variants']['rrf_baseline'], list(map(str, range(8))))
        self.assertEqual(result['variants']['bge_reranked'], list(map(str, range(11, 3, -1))))
        self.assertEqual(len(result['changes']['entered_selected']), 4)
        self.assertEqual(result['stage_assessment']['semantic_reader'], 'not_run')
        self.assertFalse(result['clinical_validated']); self.assertFalse(result['model_promoted'])
        self.assertIs(self.r.reranker, self.original)

    def test_exact_repeat_uses_immutable_receipt_without_another_search(self):
        first = R.prepare(self.r, self.body)
        again = R.prepare(self.r, self.body)
        self.assertTrue(again['cache_hit']); self.assertEqual(first['comparison'], again['comparison'])
        self.assertEqual(self.calls, 1)

    def test_changed_question_gets_distinct_receipt(self):
        first = R.prepare(self.r, self.body)['comparison']
        second = R.prepare(self.r, {**self.body, 'question': 'Different question'})['comparison']
        self.assertNotEqual(first['id'], second['id'])

    def test_changed_scope_gets_distinct_receipt(self):
        first = R.prepare(self.r, self.body)['comparison']
        body = {**self.body, 'analysis_window': {'basis': 'source_event', 'start': '2028-01-01', 'end': '2029-01-01'}}
        self.assertNotEqual(first['id'], R.prepare(self.r, body)['comparison']['id'])

    def test_provider_failure_restores_reranker_and_saves_no_comparison(self):
        with patch.object(self.original, 'rank', side_effect=ValueError('model error')):
            with self.assertRaisesRegex(ValueError, 'model error'): R.prepare(self.r, self.body)
        self.assertIs(self.r.reranker, self.original)
        with R.database(self.r.path) as db: self.assertEqual(db.execute('SELECT COUNT(*) FROM comparisons').fetchone()[0], 0)

    def test_candidate_loss_rejected(self):
        original = self.original.rank
        with patch.object(self.original, 'rank', side_effect=lambda *a, **k: original(*a, **k)[:-1]):
            with self.assertRaisesRegex(ValueError, 'candidate set'): R.prepare(self.r, self.body)
        self.assertIs(self.r.reranker, self.original)

    def test_changed_quote_rejected_before_model_run(self):
        self.rows[0]['text'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'span changed'): R.prepare(self.r, self.body)
        self.assertEqual(self.original.stats['pairs_scored'], 0)

    def test_cross_patient_source_rejected(self):
        self.rows[0]['source']['patient_id'] = 'P-other'
        with self.assertRaisesRegex(ValueError, 'belongs'): R.prepare(self.r, self.body)

    def test_source_mutation_invalidates_saved_comparison(self):
        saved = R.prepare(self.r, self.body)['comparison']
        self.r.db.execute('DELETE FROM records WHERE id=?', ('0',))
        with self.assertRaises(ValueError): R.inspect(self.r, saved['id'])

    def test_model_change_invalidates_saved_comparison(self):
        saved = R.prepare(self.r, self.body)['comparison']
        self.r.encoder.identity = 'new'
        with self.assertRaisesRegex(ValueError, 'models'): R.inspect(self.r, saved['id'])

    def test_immutable_storage(self):
        R.prepare(self.r, self.body)
        with R.database(self.r.path) as db:
            for sql in ('UPDATE comparisons SET payload=payload', 'DELETE FROM comparisons'):
                with self.assertRaises(sqlite3.IntegrityError): db.execute(sql)

    def test_no_candidates_does_not_invent_evidence(self):
        self.rows = []
        r = R.prepare(self.r, self.body)['comparison']
        self.assertEqual(r['candidates'], []); self.assertEqual(r['variants']['bge_reranked'], [])
        self.assertFalse(r['clinical_validated'])

    def test_unknown_input_and_unbounded_question_rejected(self):
        for body in ({**self.body, 'labels': ['yes']}, {**self.body, 'question': ' '}, {**self.body, 'question': 'x'*1501}, {**self.body, 'patient': 12}):
            with self.subTest(body=str(body)[:50]), self.assertRaises(ValueError): R.prepare(self.r, body)

    def test_population_diversity_and_source_field_deduplication(self):
        rows = [self.rows[0], deepcopy(self.rows[0]), *self.rows[1:]]
        self.assertEqual([r['id'] for r in R.selected(rows, None)], ['0', '1'])
        self.assertEqual(len(R.selected(rows, 'P-1')), 8)

    def test_unknown_comparison_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unknown'): R.inspect(self.r, 'missing')

    def test_default_selection_policy_drift_rejected(self):
        old = self.r.search
        def search(*a, **k):
            result = old(*a, **k); result['evidence'] = result['evidence'][:-1]; return result
        self.r.search = search
        with self.assertRaisesRegex(ValueError, 'policy changed'): R.prepare(self.r, self.body)

    def test_reranker_cannot_rewrite_evidence(self):
        old = self.original.rank
        def rank(*a, **k):
            rows = old(*a, **k); rows[0]['text'] = 'not the received evidence'; return rows
        with patch.object(self.original, 'rank', side_effect=rank):
            with self.assertRaisesRegex(ValueError, 'changed evidence'): R.prepare(self.r, self.body)

    def test_nonfinite_rank_score_rejected(self):
        old = self.original.rank
        def rank(*a, **k):
            rows = old(*a, **k); rows[0]['reranker_logit'] = float('nan'); return rows
        with patch.object(self.original, 'rank', side_effect=rank):
            with self.assertRaisesRegex(ValueError, 'Nonfinite'): R.prepare(self.r, self.body)

    def test_duplicate_candidate_id_rejected(self):
        self.rows.append(deepcopy(self.rows[0]))
        with self.assertRaisesRegex(ValueError, 'candidate contract'): R.prepare(self.r, self.body)

    def test_missing_reranker_is_not_reported_as_a_comparison(self):
        self.r.reranker = None
        with self.assertRaisesRegex(ValueError, 'configured local reranker'): R.prepare(self.r, self.body)

    def test_source_category_dates_and_kind_cannot_be_relabelled(self):
        for key, value in [('source_table','other'),('event_date','2028-01-01'),('kind','received_text')]:
            with self.subTest(key=key):
                original=deepcopy(self.rows[0]['source']);self.rows[0]['source'][key]=value
                with self.assertRaisesRegex(ValueError,'metadata'):R.prepare(self.r,self.body)
                self.rows[0]['source']=original


if __name__ == '__main__': unittest.main()
