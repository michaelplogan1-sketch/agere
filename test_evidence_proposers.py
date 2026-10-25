"""Evidence, retrieval and reader proposers (clinical_core/agere/clinical/evidence_proposers.py).

Unit tests use a fake stack (in-memory roster, stub retriever/readers/providers); graphs go through the real
analysis_graph validator. One opt-in integration test (AGERE_EVIDENCE_STACK) runs the retrieval proposal through
stack.run_graph on a synthetic stack (MiniLM + BGE only; no paid call, no 4B model).
"""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from clinical_core.agere.clinical import evidence_proposers as E
from clinical_core.agere.clinical.analysis_graph import validate

AS_OF = '2026-09-30'
ROSTER = ('P-1001', 'P-1002', 'P-1003')


class Untouchable:
    def __getattr__(self, name): raise AssertionError(f'stack.{name} touched for a question of another kind')


def passage(pid, text, rid='r1'):
    return {'id': '1', 'text': text, 'source': {'patient_id': pid, 'record_id': rid, 'field': 'text', 'start': 0,
                                                 'end': len(text), 'event_date': '2026-05-01'}}


class FakeRetriever:
    def __init__(self, hits=None):
        self.meta = {'snapshot': {'as_of': AS_OF}}
        self.db = sqlite3.connect(':memory:')
        self.db.executescript('CREATE TABLE patients(id TEXT PRIMARY KEY,payload TEXT);'
                              'CREATE TABLE records(id TEXT PRIMARY KEY,patient TEXT,stream TEXT,payload TEXT);')
        self.db.executemany('INSERT INTO patients VALUES (?,?)', [(p, '{}') for p in ROSTER])
        self.db.execute('INSERT INTO records VALUES (?,?,?,?)', ('n1', 'P-1001', 'notes', json.dumps({
            'record_id': 'n1', 'patient_id': 'P-1001', 'source_table': 'notes', 'event_date': '2026-04-02',
            'available_date': '2026-04-02', 'text': 'Patient reports myalgia on atorvastatin; statin stopped.'})))
        self.hits, self.calls = hits, []

    def search(self, question, *, patient=None, limit=8, patient_ids=None, analysis_window=None):
        self.calls.append({'question': question, 'patient': patient, 'patient_ids': patient_ids, 'window': analysis_window})
        hits = self.hits(question) if callable(self.hits) else (self.hits or [])
        if patient: hits = [h for h in hits if h['source']['patient_id'] == patient]
        return {'evidence': hits, 'method': 'fake', 'cache_hit': False, 'snapshot': self.meta['snapshot']}


def fake_stack(hits=None, allow_live=False, reading='patient_affirmed', passes=True):
    tmp = tempfile.mkdtemp()
    stack = SimpleNamespace(path=Path(tmp), allow_live=allow_live, retriever=FakeRetriever(hits))
    stack.reads, stack.paid, stack.contexts = [], [], []

    def refine(target, provider):
        stack.reads.append(provider)
        return {'id': 'x', 'reading': {'labels': {'statement': reading}},
                'student': {'passes_development_threshold': passes}}

    def review(question, patient, provider):
        if not stack.allow_live: raise ValueError('Live model route disabled on this server')
        stack.paid.append(provider)
        return {'model_proposal': {'answers': [{'label': 'yes', 'unsupported_decision': False}]}, 'new_paid_api_calls': 1}

    def context(patient, question):
        stack.contexts.append(patient)
        return {'status': 'prepared', 'context_id': 'c1', 'selected_categories': ['notes']}
    stack.refine_statement, stack.review, stack.prepare_question_context = refine, review, context
    stack.review_passes = lambda *a: (_ for _ in ()).throw(AssertionError('no bundle exists'))
    return stack


class Kind(unittest.TestCase):
    def test_other_questions_return_none_without_touching_the_stack(self):
        for q in ('How many patients have diabetes?', 'What share of notes mention statins?', 'List patients with CKD',
                  'Average A1c by region', 'Which patients have notes mentioning dyspnea?'):
            self.assertIsNone(E.retrieval_tier(Untouchable(), q), q)
            self.assertIsNone(E.review_tier(Untouchable(), q), q)

    def test_review_needs_a_named_patient(self):
        self.assertIsNone(E.review_tier(fake_stack(), 'Does the chart support statin intolerance?'))

    def test_window(self):
        self.assertEqual(E.window('notes in the last 6 months', AS_OF),
                         {'basis': 'source_event', 'start': '2026-03-31', 'end': AS_OF})
        self.assertEqual(E.window('notes in 2025', AS_OF)['start'], '2025-01-01')
        self.assertEqual(E.window('what do notes say', AS_OF)['basis'], 'all_observed')


class Retrieval(unittest.TestCase):
    def test_fact_questions_continue_to_readers_without_using_passages_as_answers(self):
        for q in (
            'What did the Holter monitor show for P-1001?',
            'What did the specialist letter recommend starting for P-1001?',
            'Was P-1001 smoking according to the September note?',
            'What were the biopsy results in the documents for P-1001?',
            'Show the Holter results from the notes for P-1001',
        ):
            self.assertIsNone(E.retrieval_tier(Untouchable(), q), q)

    def test_explicit_passage_requests_remain_available(self):
        for q in ('Find notes about dyspnea for P-1001',
                  'Quote the specialist letter for P-1001',
                  'Show passages about the Holter monitor for P-1001'):
            stack = fake_stack(hits=[passage('P-1001', 'Received source excerpt.')])
            self.assertIsNotNone(E.retrieval_tier(stack, q), q)

    def test_population_wide_passage_question(self):
        stack = fake_stack(hits=[passage('P-1002', 'Patient cannot afford insulin.')])
        graph, receipt = E.retrieval_tier(stack, 'What do the notes say about medication affordability?')
        validate(graph, AS_OF)
        self.assertEqual([n['operation'] for n in graph['nodes']], ['cohort', 'retrieve'])
        self.assertEqual(graph['nodes'][0]['parameters']['specification'], {'all': [], 'any': [], 'exclude': []})
        self.assertEqual(stack.retriever.calls[0]['patient_ids'], sorted(ROSTER))  # same membership the cohort node yields
        self.assertEqual((receipt['route'], receipt['new_paid_api_calls']), ('evidence_retrieval', 0))
        self.assertIn('warehouse_retriever', receipt['components'])
        self.assertNotIn('granite_hybrid_retrieval', receipt['components'])

    def test_named_patient_scopes_the_cohort_and_selects_context(self):
        stack = fake_stack(hits=[passage('P-1001', 'Myalgia on atorvastatin.')])
        graph, receipt = E.retrieval_tier(stack, 'What did the notes for P-1001 say about atorvastatin in the last 3 months?')
        self.assertEqual(graph['nodes'][0]['parameters']['specification']['all'],
                         [{'kind': 'patient', 'field': 'patient_id', 'op': 'eq', 'value': 'P-1001'}])
        self.assertEqual(graph['nodes'][1]['parameters']['analysis_window']['basis'], 'source_event')
        self.assertEqual(stack.contexts, ['P-1001'])
        self.assertIn('question_context_selection', receipt['components'])
        from clinical_core.agere.clinical.literal_check import missing, executable_text
        self.assertEqual(missing(graph['interpretation'], executable_text([n['parameters'] for n in graph['nodes']])), [])

    def test_granite_fallback_expands_the_query_when_the_primary_finds_nothing(self):
        hits = lambda q: [passage('P-1001', 'myalgia stopped')] if 'myalgia' in q else []

        class Encoder:
            identity = 'fake-granite'
            def windows(self, text, **k): return [{'text': text, 'start': 0, 'end': len(text)}]
            def encode(self, texts): return np.array([[1.0, float(len(t) % 3)] for t in texts])
        stack = fake_stack(hits=hits)
        graph, receipt = E.retrieval_tier(stack, 'What do the notes for P-1001 say about statin muscle complaints?',
                                          granite_encoder=Encoder())
        self.assertIn('granite_hybrid_retrieval', receipt['components'])
        self.assertIn('myalgia', receipt['granite_fallback']['expansion_terms'])
        self.assertIn('myalgia', graph['nodes'][1]['parameters']['query'])

    def test_nothing_found_anywhere_is_no_proposal(self):
        self.assertIsNone(E.retrieval_tier(fake_stack(hits=[]), 'What do the notes say about hiccups?'))

    def test_patient_assertion_is_left_to_review(self):
        stack = fake_stack(hits=[passage('P-1001', 'Myalgia on atorvastatin.')])
        q = 'Does the chart support statin intolerance for P-1001?'
        self.assertIsNone(E.retrieval_tier(stack, q))
        self.assertIsNotNone(E.review_tier(stack, q))


class Review(unittest.TestCase):
    Q = 'Does the chart support statin intolerance for P-1001?'

    def test_free_readers_decide_without_live(self):
        stack = fake_stack(hits=[passage('P-1001', 'Statin intolerance: myalgia on atorvastatin.')])
        graph, receipt = E.review_tier(stack, self.Q)
        validate(graph, AS_OF)
        # The review node now carries the full reader order (local readers first), so the graph ends in review.
        self.assertEqual([n['operation'] for n in graph['nodes']], ['cohort', 'retrieve', 'review'])
        node = graph['nodes'][-1]
        self.assertEqual(node['parameters']['providers'][:len(E.LOCAL_READERS)], list(E.LOCAL_READERS))  # free readers first
        self.assertEqual((stack.paid, receipt['new_paid_api_calls']), ([], 0))  # readers run at execution, not planning
        # the readers' verdicts themselves are covered by test_review_patient_cascade

    def test_undecided_chart_escalates_to_paid_review_only_with_live(self):
        hits = [passage('P-1001', 'Statin intolerance discussed.')]
        with patch.object(E, '_comparison_cohort', return_value=None), \
                patch('clinical_core.agere.clinical.semantic_reader.read', side_effect=ValueError('no native model')):
            graph, receipt = E.review_tier(fake_stack(hits, reading='unknown'), self.Q)
            self.assertEqual(graph['nodes'][-1]['operation'], 'review')  # paid readers inside are skipped without live
            graph, receipt = E.review_tier(fake_stack(hits, allow_live=True, reading='unknown'), self.Q)
        validate(graph, AS_OF)
        node = graph['nodes'][-1]
        self.assertEqual(node['operation'], 'review')
        self.assertEqual(node['parameters']['providers'][-2:], ['jev', 'perplexity'])
        self.assertIn('jev_typesafe_review', receipt['components'])
        self.assertIn('kev_reader', receipt['components'])  # the native readers were tried (and failed) first

    def test_review_node_takes_every_reader(self):
        self.assertTrue(E.review_node_accepts(E.LOCAL_READERS, AS_OF))
        self.assertTrue(E.review_node_accepts(E.REVIEW_ORDER, AS_OF))

    def test_review_patient_cascade(self):
        hits = [passage('P-1001', 'Statin intolerance discussed.')]
        with patch.object(E, '_comparison_cohort', return_value=None), \
                patch('clinical_core.agere.clinical.document_readers.availability', return_value=[]), \
                patch('clinical_core.agere.clinical.semantic_reader.read', side_effect=ValueError('no native model')), \
                patch.dict(os.environ, {'AGERE_SYSTEM_ONE_DECIDERS': '/nonexistent/deciders.json'}):  # default Jev -> Perplexity order
            off = E.review_patient(fake_stack(hits, reading='unknown'), self.Q, 'P-1001')
            stack = fake_stack(hits, allow_live=True, reading='unknown')
            on = E.review_patient(stack, self.Q, 'P-1001')
        self.assertEqual((off['verdict'], off['paid_steps'][0]['status'], off['new_paid_api_calls']), ('unknown', 'skipped', 0))
        self.assertEqual((on['verdict'], on['decided_by'], stack.paid, on['new_paid_api_calls']), ('yes', 'jev', ['jev'], 1))

    def test_conflicting_readings_do_not_decide(self):
        self.assertEqual(E.combined(['yes', 'no', 'unknown']), 'conflict')
        self.assertEqual(E.verdict({'reading': {'labels': {'statement': 'patient_affirmed'}},
                                    'student': {'passes_development_threshold': False}}), 'unknown')


STACK = Path(os.environ.get('AGERE_EVIDENCE_STACK') or '/nonexistent')  # a clone of a synthetic stack, never the served one


@unittest.skipUnless((STACK/'run.json').exists() and (E.ROOT/'models/bge-reranker-v2-m3').exists(),
                     'AGERE_EVIDENCE_STACK not set (synthetic stack whose event bindings match this code)')
class Executes(unittest.TestCase):
    def test_retrieval_graph_runs_through_run_graph(self):
        from clinical_core.agere.clinical.stack_runtime import Stack
        stack = Stack(STACK, allow_live=False)
        try:
            q = 'What do the notes say about medication affordability?'
            found = E.retrieval_tier(stack, q)
            self.assertIsNotNone(found)
            graph, receipt = found
            result = stack.run_graph(graph, question=q)
            self.assertEqual(result['new_paid_api_calls'], 0)
            self.assertTrue(result['results'][-1]['result']['evidence'])
            from clinical_core.agere.clinical.literal_check import shape_misfit
            self.assertIsNone(shape_misfit(q, result['answer']))
        finally:
            stack.close()


if __name__ == '__main__':
    unittest.main()


class NoteCueGate(unittest.TestCase):  # 2026-10-24: the chart reader's strong note cues also open the evidence tiers
    def test_document_kind_questions_reach_the_evidence_tiers_structured_ones_do_not(self):
        from clinical_core.agere.clinical.evidence_proposers import narrative_question
        self.assertTrue(narrative_question('What did the d/c summary say about follow-up for SYN-1?'))
        self.assertTrue(narrative_question('What did the MRI report show for WHP-2?'))
        self.assertFalse(narrative_question('How many patients have diabetes?'))
        self.assertFalse(narrative_question('What is the latest A1c for SYN-3?'))
