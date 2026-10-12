"""Explicit category additions to an unchanged retrieval selection.

This creates a declared record scope, not a relevance or clinical-completeness
judgment. Selected passages expand to their whole received records for review.
No model is called and no linked record is silently added outside the window.
"""
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import sqlite3

from ...contracts import digest
from . import retrieval_comparison as R, record_context as C, clinical_events as E, structured_transport as T
from .warehouse_interpretation import index
from . import reference_table_context as F
from . import source_synthesis as S

VERSION = 'retrieval-source-plan/1'


@contextmanager
def database(path):
    db = sqlite3.connect(Path(path) / 'retrieval-source-plans.sqlite3')
    db.executescript('''
      CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY, preview_id TEXT UNIQUE, payload TEXT NOT NULL);
      CREATE TRIGGER IF NOT EXISTS immutable_update BEFORE UPDATE ON plans BEGIN SELECT RAISE(ABORT,'Immutable source plan'); END;
      CREATE TRIGGER IF NOT EXISTS immutable_delete BEFORE DELETE ON plans BEGIN SELECT RAISE(ABORT,'Immutable source plan'); END;
    ''')
    try:
        with db: yield db
    finally: db.close()


def validate(body):
    if not isinstance(body, dict) or set(body) != {'comparison_id', 'baseline', 'categories'}:
        raise ValueError('Exact comparison, baseline and explicit categories required')
    if not isinstance(body['comparison_id'], str) or body['baseline'] not in ('rrf_baseline', 'bge_reranked'):
        raise ValueError('Known comparison and selection variant required')
    cats = body['categories']
    if (not isinstance(cats, list) or len(cats) > 30 or any(not isinstance(c, str) or not c for c in cats)
            or len(set(cats)) != len(cats)):
        raise ValueError('Up to 30 distinct category names required')
    return {**body, 'categories': sorted(cats)}


def preview(retriever, body):
    request = validate(body)
    report = R.inspect(retriever, request['comparison_id'])
    patient = report['request']['patient']
    if not patient: raise ValueError('A single-patient comparison is required')
    records, meta = C.received(retriever.path, patient)
    if meta['source_run'] != report['identity']['source_run'] or meta['source_digest'] != report['identity']['source_digest']:
        raise ValueError('Comparison and record snapshot differ')
    by_id = {r['record_id']: r for r in records}
    inventory = Counter(r['source_table'] for r in records)
    if set(request['categories']) - set(inventory):
        raise ValueError('Selected category has no received records for this patient')
    window = E.validate_window(report['request']['analysis_window'], meta['snapshot']['as_of'])
    related = index(records)
    baseline_passages = set(report['variants'][request['baseline']])
    roots = {r['source']['record_id'] for r in report['candidates'] if r['id'] in baseline_passages}
    if roots - set(by_id): raise ValueError('Baseline record is not received for this patient')
    states = {r['record_id']: E.window_state(r, window, meta['snapshot']['as_of'], related) for r in records}
    if any(states[r] is not True for r in roots):
        raise ValueError('Baseline selection disagrees with the declared date scope; rebuild comparison')
    chosen = {r['record_id'] for r in records if r['source_table'] in request['categories']}
    added = {r for r in chosen if states[r] is True} - roots
    selected = roots | added
    selected_categories = {by_id[r]['source_table'] for r in selected}
    excluded = Counter(r['source_table'] for r in records if r['record_id'] not in selected)
    # The date scope travels in the actual review question as well as the UI.
    # Do not mutate the frozen record-context contract or silently truncate it.
    scope_text = ('All received dates through ' + meta['snapshot']['as_of'] if window['basis'] == 'all_observed'
                  else f"{window['basis']} dates from {window['start'] or 'earliest received'} through {window['end'] or meta['snapshot']['as_of']}")
    review_question = report['request']['question'] + '\n\nEvidence scope: ' + scope_text + '. Review only the explicitly selected received records. Other records may be in separate review groups; cross-group relationships remain unassessed. Selection sufficiency and clinical completeness are unassessed; excluded or undated records do not establish absent care.'
    blockers = []
    if not selected: blockers.append('No eligible records in the declared source plan')
    if len(selected) > 200: blockers.append('More than 200 records; narrow the source plan before review')
    if len(selected_categories) > 30: blockers.append('More than 30 categories; narrow the source plan before review')
    if len(review_question) > 1500: blockers.append('Question and explicit scope exceed the 1500-character review bound; shorten the question')
    def source(rid):
        r = by_id[rid]
        return {'record_id': rid, 'record_hash': digest(r), 'source_category': r['source_table'],
                'event_date': r.get('event_date'), 'available_date': r.get('available_date')}
    result = {'schema': VERSION, 'request': request, 'patient_id': patient,
              'original_question': report['request']['question'], 'review_question': review_question,
              'source_run': meta['source_run'], 'source_digest': meta['source_digest'],
              'snapshot': meta['snapshot'], 'analysis_window': window,
              'patient_source_fingerprint': digest(records),
              'identity': {'code': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                           'dates': E.code_identity(), 'record_context': C.identity(), 'structured_transport': T.identity(),
                           'reference_tables': F.identity()},
              'baseline_passage_ids': sorted(baseline_passages), 'baseline_record_ids': sorted(roots),
              'added_record_ids': sorted(added), 'selected_record_ids': sorted(selected),
              'records': [{**source(r), 'selection_reason': 'baseline_whole_record' if r in roots else 'explicit_category_addition'} for r in sorted(selected)],
              'requested_categories': [{'source_category': c, 'received_records': inventory[c],
                                       'eligible_records': sum(states[r] is True for r in chosen if by_id[r]['source_table'] == c),
                                       'outside_window_records': sum(states[r] is False for r in chosen if by_id[r]['source_table'] == c),
                                       'unknown_date_records': sum(states[r] is None for r in chosen if by_id[r]['source_table'] == c)} for c in request['categories']],
              'category_records_excluded_by_date': [{**source(r), 'reason': 'unknown_date' if states[r] is None else 'outside_window'} for r in sorted(chosen) if states[r] is not True],
              'received_records': len(records), 'excluded_records': sum(excluded.values()),
              'excluded_by_category': dict(sorted(excluded.items())),
              'can_prepare': not blockers, 'blockers': blockers,
              'representation': 'Whole received records, including all transported fields, replace baseline passage excerpts in the review context.',
              'limits': ['Category selection is explicit; relevance and selection sufficiency remain unassessed.',
                         'No automatic linked-record expansion across the declared date scope.',
                         'Large selections partition into explicit whole-record groups. No selected record is silently dropped. Cross-group interpretation remains unassessed.',
                         'Context assembly checks whole-source byte limits; indivisible omissions prevent complete-scope dispatch.',
                         'An order is not medication use; a dispense is not ingestion; reported coverage is not verified benefits.',
                         'No model call, clinical action, service enrollment or patient contact.'],
              'clinical_validated': False, 'new_paid_api_calls': 0, 'automatic_actions': 0}
    result['id'] = digest(result)
    return result


def contexts(retriever, p):
    """Partition whole records locally before any provider dispatch."""
    groups = []
    def build(ids):
        if len(groups) >= 32: raise ValueError('Source plan needs more than 32 review groups; narrow the selection')
        context = C.create(retriever.path, p['patient_id'], p['review_question'], ids)
        original = context['id']; structured = False; error = None
        if context['dispatch']['source_review_allowed']:
            if any(u['kind'] == 'structured' and len(T.context.encode(u['reported_fields'])) > T.TARGET_BYTES
                   for u in context.get('model_context', {}).get('evidence', [])):
                try: context = T.create(retriever.path, context['id']); structured = True
                except ValueError as exc:
                    if 'exceeds declared context bound' not in str(exc): raise
                    error = str(exc)
            if error is None:
                context = F.create(retriever.path, context['id'])
                try: S.preview(retriever.path, context['id'])
                except ValueError as exc:
                    if not any(s in str(exc) for s in ('indivisible', 'source-pass cap', 'exceeding the requested cap')): raise
                    error = str(exc)
        else: error = 'Complete selected record group exceeds its context bound'
        if error and len(ids) > 1:
            middle = len(ids)//2; build(ids[:middle]); build(ids[middle:]); return
        groups.append({'context': context, 'original_context_id': original,
                       'structured_transport_applied': structured,
                       'reference_tables_applied': context.get('schema') == F.VERSION,
                       'request_fit': 'blocked' if error else 'bounded_plan_prepared', 'request_fit_error': error})
    build(p['selected_record_ids'])
    actual = [rid for g in groups for rid in g['context']['record_scope']['record_ids']]
    if len(actual) != len(set(actual)) or sorted(actual) != p['selected_record_ids']:
        raise ValueError('Whole-record review group coverage changed')
    return groups


def prepare(retriever, body):
    if not isinstance(body, dict) or set(body) != {'request', 'preview_id'}:
        raise ValueError('Exact reviewed preview and request required')
    p = preview(retriever, body['request'])
    if p['id'] != body['preview_id']: raise ValueError('Source-plan preview changed; inspect the current selection')
    if not p['can_prepare']: raise ValueError('; '.join(p['blockers']))
    with database(retriever.path) as db:
        old = db.execute('SELECT id FROM plans WHERE preview_id=?', (p['id'],)).fetchone()
    if old: return {**inspect(retriever, {'id': old[0]}), 'cache_hit': True}
    groups = contexts(retriever, p)
    if preview(retriever, p['request']) != p: raise ValueError('Sources changed during source-plan assembly')
    result = {'schema': VERSION, 'preview': p,
              'groups': [{k: v for k, v in g.items() if k != 'context'} | {
                  'context_id': g['context']['id'], 'context_hash': digest(g['context']),
                  'record_ids': g['context']['record_scope']['record_ids'], 'group_number': i}
                  for i, g in enumerate(groups, 1)],
              'cross_group_interpretation': 'not_assessed' if len(groups) > 1 else 'not_applicable',
              'status': 'declared_record_scope_prepared' if all(g['request_fit'] != 'blocked' for g in groups) else 'context_incomplete',
              'new_paid_api_calls': 0, 'automatic_actions': 0}
    result['id'] = digest(result)
    with database(retriever.path) as db:
        db.execute('INSERT INTO plans VALUES (?,?,?)', (result['id'], p['id'], json.dumps(result, allow_nan=False)))
    return {'plan': result, 'contexts': [g['context'] for g in groups], 'cache_hit': False}


def inspect(retriever, body):
    if not isinstance(body, dict) or set(body) != {'id'} or not isinstance(body['id'], str):
        raise ValueError('Exact source-plan ID required')
    with database(retriever.path) as db:
        found = db.execute('SELECT payload FROM plans WHERE id=?', (body['id'],)).fetchone()
    if not found: raise ValueError('Unknown source plan')
    plan = json.loads(found[0])
    if digest({k: v for k, v in plan.items() if k != 'id'}) != body['id'] or plan['id'] != body['id']:
        raise ValueError('Source-plan integrity changed')
    if preview(retriever, plan['preview']['request']) != plan['preview']:
        raise ValueError('Source plan or received evidence changed')
    rows = []
    for g in plan['groups']:
        context = C.inspect(retriever.path, g['context_id'])
        if digest(context) != g['context_hash'] or context['record_scope']['record_ids'] != g['record_ids']:
            raise ValueError('Prepared source context changed')
        rows.append(context)
    ids = [rid for g in plan['groups'] for rid in g['record_ids']]
    if len(ids) != len(set(ids)) or sorted(ids) != plan['preview']['selected_record_ids']:
        raise ValueError('Saved source-plan coverage changed')
    return {'plan': plan, 'contexts': rows}
