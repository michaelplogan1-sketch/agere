"""Paired local retrieval diagnostics on one exact candidate set.

This is an inspection instrument, not a relevance-labelled benchmark. It wraps
the existing retriever without modifying its policy, weights or normal caches.
The service's existing model-operation lock must enclose prepare().
"""
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import math
import sqlite3
import uuid

from ...contracts import digest
from ... import local_encoders
from . import warehouse_retrieval as W, cohorts

VERSION = 'paired-retrieval-inspection/1'


@contextmanager
def database(path):
    db = sqlite3.connect(Path(path) / 'retrieval-comparisons.sqlite3')
    db.executescript('''
      CREATE TABLE IF NOT EXISTS comparisons(id TEXT PRIMARY KEY, request_key TEXT UNIQUE, payload TEXT NOT NULL);
      CREATE TRIGGER IF NOT EXISTS immutable_update BEFORE UPDATE ON comparisons BEGIN SELECT RAISE(ABORT,'Immutable comparison'); END;
      CREATE TRIGGER IF NOT EXISTS immutable_delete BEFORE DELETE ON comparisons BEGIN SELECT RAISE(ABORT,'Immutable comparison'); END;
    ''')
    try:
        with db:
            yield db
    finally:
        db.close()


def identity(retriever):
    return {'schema': VERSION, 'source_run': retriever.meta['source_run'],
            'source_digest': retriever.meta['source_digest'],
            'index_metadata': digest(retriever.meta),
            'encoder': retriever.encoder.identity,
            'reranker': retriever.reranker.identity if retriever.reranker else None,
            'annotations': getattr(retriever, 'annotation_identity', None),
            'code': {m.__name__: hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest()
                     for m in (W, cohorts, local_encoders)},
            'comparison_code': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def selected(rows, patient, limit=8):
    """The retriever's selection policy (protected exception/contradiction slots, then source diversity), verified against
    live output below."""
    from . import protected_channels

    def accept(row, seen, counts):
        s = row['source']; group = (s['patient_id'], s['record_id'], s['field'])
        if group in seen or (not patient and counts[s['patient_id']] >= 2):
            return False
        seen.add(group); counts[s['patient_id']] += 1
        return True
    return protected_channels.select([dict(r) for r in rows], limit, patient, accept)


def validate_sources(retriever, rows):
    for row in rows:
        s = row['source']
        original = retriever.db.execute('SELECT patient,payload FROM records WHERE id=?', (s['record_id'],)).fetchone()
        if not original or original[0] != s['patient_id']:
            raise ValueError('Comparison source no longer belongs to this patient')
        record = json.loads(original[1])
        parts = [p for p in W.representations(record) if p['field'] == s['field']]
        if digest(record) != s['record_hash'] or len(parts) != 1:
            raise ValueError('Comparison record changed')
        if (any(s.get(k) != record.get(k) for k in
                ('source_table', 'source_system', 'organization_id', 'event_date', 'available_date'))
                or s.get('kind') != parts[0]['kind']
                or s.get('structured_fields') != parts[0].get('fields')):
            raise ValueError('Comparison source metadata changed')
        text = parts[0]['text']
        if (digest(text) != s['text_field_hash'] or type(s['start']) is not int
                or type(s['end']) is not int or not 0 <= s['start'] < s['end'] <= len(text)
                or text[s['start']:s['end']] != row['text'] or s['quote'] != row['text']):
            raise ValueError('Comparison received source span changed')


class Recorder:
    def __init__(self, retriever):
        self.retriever, self.delegate = retriever, retriever.reranker
        # Fresh trace namespace bypasses only result-cache reuse. Actual model
        # scoring still uses the delegate's exact query/text score cache.
        self.identity = digest(['inspection-trace', self.delegate.identity, uuid.uuid4().hex])
        self.before, self.after = [], []

    def rank(self, query, rows, *, top_k):
        from .protected_channels import CHANNEL_CANDIDATES
        if len(rows) > 48 + CHANNEL_CANDIDATES or len({r['id'] for r in rows}) != len(rows):
            raise ValueError('Unexpected retrieval candidate contract')
        self.before = deepcopy(rows)
        validate_sources(self.retriever, self.before)
        self.after = deepcopy(self.delegate.rank(query, rows, top_k=top_k))
        if sorted(r['id'] for r in self.before) != sorted(r['id'] for r in self.after):
            raise ValueError('Reranker changed the candidate set')
        originals = {r['id']: r for r in self.before}
        for r in self.after:
            if any(r[k] != v for k, v in originals[r['id']].items()):
                raise ValueError('Reranker changed evidence')
            if not math.isfinite(r['reranker_logit']):
                raise ValueError('Nonfinite relevance score')
        return deepcopy(self.after)


def inspect(retriever, report_id):
    with database(retriever.path) as db:
        row = db.execute('SELECT payload FROM comparisons WHERE id=?', (report_id,)).fetchone()
    if not row:
        raise ValueError('Unknown retrieval comparison')
    report = json.loads(row[0])
    if digest({k: v for k, v in report.items() if k != 'id'}) != report_id:
        raise ValueError('Comparison integrity changed')
    if report['identity'] != identity(retriever):
        raise ValueError('Comparison models, source snapshot or implementation changed')
    validate_sources(retriever, report['candidates'])
    return report


def prepare(retriever, body):
    if not isinstance(body, dict) or not set(body) <= {'question', 'patient', 'cohort', 'analysis_window'}:
        raise ValueError('Exact question and retrieval scope required')
    if not retriever.reranker:
        raise ValueError('A configured local reranker is required for a paired comparison')
    question, patient = body.get('question'), body.get('patient') or None
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 1500:
        raise ValueError('Bounded question required')
    if patient is not None and not isinstance(patient, str):
        raise ValueError('Exact patient identifier required')
    selection = cohorts.select(retriever.path, body.get('cohort'), patient=patient)
    from .clinical_events import validate_window
    window = validate_window(body.get('analysis_window'), retriever.meta['snapshot']['as_of'])
    request = {'question': question, 'patient': patient, 'cohort': body.get('cohort'),
               'analysis_window': window}
    original_identity = identity(retriever)
    request_key = digest([original_identity, request, selection['patient_ids']])
    with database(retriever.path) as db:
        old = db.execute('SELECT id FROM comparisons WHERE request_key=?', (request_key,)).fetchone()
    if old:
        return {'comparison': inspect(retriever, old[0]), 'cache_hit': True, 'new_paid_api_calls': 0}
    recorder = Recorder(retriever)
    stats_before = deepcopy(recorder.delegate.stats)
    try:
        retriever.reranker = recorder
        actual = retriever.search(question, patient=patient, patient_ids=selection['patient_ids'], analysis_window=window)
    finally:
        retriever.reranker = recorder.delegate
    if actual['candidate_passages'] != len(recorder.before):
        raise ValueError('Candidate trace unavailable; no comparison inferred from selected results')
    baseline = selected(recorder.before, patient)
    reranked = selected(recorder.after, patient)
    ids = lambda rows: [r['id'] for r in rows]
    if ids(reranked) != ids(actual['evidence']):
        raise ValueError('Selection policy changed; paired baseline must be requalified')
    br, rr = ids(baseline), ids(reranked)
    after_ranks = {r['id']: i for i, r in enumerate(recorder.after, 1)}
    after_scores = {r['id']: r['reranker_logit'] for r in recorder.after}
    candidates = []
    for i, r in enumerate(recorder.before, 1):
        candidates.append({**r, 'rrf_rank': i, 'reranked_rank': after_ranks[r['id']],
                           'reranker_logit': after_scores[r['id']],
                           'rrf_selected_rank': br.index(r['id']) + 1 if r['id'] in br else None,
                           'reranked_selected_rank': rr.index(r['id']) + 1 if r['id'] in rr else None})
    report = {'schema': VERSION, 'identity': original_identity, 'request': request,
              'request_key': request_key, 'synthetic': True, 'candidate_set_identical': True,
              'cohort': selection, 'candidates': candidates,
              'variants': {'rrf_baseline': br, 'bge_reranked': rr},
              'selection': {'limit': 8, 'distinct_source_fields': True, 'per_patient_limit': None if patient else 2},
              'changes': {'entered_selected': [x for x in rr if x not in br],
                          'left_selected': [x for x in br if x not in rr],
                          'shared_selected': [x for x in rr if x in br]},
              'runtime': {'elapsed_seconds': actual['elapsed_seconds'],
                          'reranker_delta': {k: recorder.delegate.stats[k] - v for k, v in stats_before.items()},
                          'new_paid_api_calls': 0},
              'stage_assessment': {'candidate_generation': 'observed_fused_top_24; recall_not_assessed',
                                   'reranking': 'paired_same_candidates; relevance_not_adjudicated',
                                   'source_diversity': 'checked_against_actual_search_output',
                                   'semantic_reader': 'not_run', 'classifier': 'not_run',
                                   'planner': 'not_run', 'service_matching': 'not_run'},
              'limits': ['Rank changes are not accuracy gains or clinical evidence.',
                         'No relevance labels, clinical gold labels, new training or model promotion.',
                         'Only the fused candidate set is traced; lexical and dense recall are unassessed.',
                         'A result omitted by retrieval does not establish absence of care.',
                         'Normal question answering and work authority are unchanged.'],
              'clinical_validated': False, 'model_promoted': False, 'automatic_actions': 0}
    report['id'] = digest(report)
    with database(retriever.path) as db:
        db.execute('INSERT INTO comparisons VALUES (?,?,?)', (report['id'], request_key, json.dumps(report, allow_nan=False)))
    return {'comparison': report, 'cache_hit': False, 'new_paid_api_calls': 0}
