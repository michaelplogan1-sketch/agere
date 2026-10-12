"""Received-category inventory beside a paired retrieval receipt; not recall."""
from collections import defaultdict
from pathlib import Path
import hashlib
from ...contracts import digest
from . import retrieval_comparison as R


def inspect(retriever, body):
    if not isinstance(body, dict) or set(body) != {'id'}:
        raise ValueError('Exact paired comparison required')
    report = R.inspect(retriever, body['id'])
    patient = report['request']['patient']
    if not patient:
        raise ValueError('Select one patient to inspect their received category inventory')
    inventory = dict(retriever.db.execute('SELECT stream,COUNT(*) FROM records WHERE patient=? GROUP BY stream ORDER BY stream', (patient,)))
    counts = {k: defaultdict(set) for k in ('candidates', 'rrf_selected', 'reranked_selected')}
    for row in report['candidates']:
        s = row['source']; category, record = s['source_table'], s['record_id']
        counts['candidates'][category].add(record)
        if row['rrf_selected_rank']: counts['rrf_selected'][category].add(record)
        if row['reranked_selected_rank']: counts['reranked_selected'][category].add(record)
    if set(counts['candidates']) - set(inventory):
        raise ValueError('Comparison category is absent from received source inventory')
    rows = [{'source_category': stream, 'received_records': n,
             **{k: len(v[stream]) for k, v in counts.items()}} for stream, n in inventory.items()]
    result = {'schema': 'retrieval-category-inspection/1', 'comparison_id': report['id'], 'patient_id': patient,
              'source_run': report['identity']['source_run'], 'source_digest': report['identity']['source_digest'],
              'snapshot': retriever.meta['snapshot'], 'comparison_window': report['request']['analysis_window'],
              'categories': rows, 'received_records': sum(inventory.values()),
              'categories_outside_candidates': sum(r['candidates'] == 0 for r in rows),
              'categories_outside_final_selection': sum(r['reranked_selected'] == 0 for r in rows),
              'inventory_scope': 'All received records for this patient through the snapshot cutoff, including records outside any narrower query date window.',
              'count_unit': 'Distinct source records, not passages or clinical events.',
              'limits': ['Category presence does not establish relevance to this question.',
                         'These are source inventory counts, not recall, accuracy, missing-care or clinical-completeness measures.',
                         'Opening a category does not add it to an answer or dispatch it to a model.'],
              'new_paid_api_calls': 0, 'automatic_actions': 0,
              'implementation': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    result['id'] = digest(result)
    return result
