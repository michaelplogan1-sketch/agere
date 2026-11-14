"""Local schema retrieval feeding the existing immutable whole-record plan.

Schema relevance is a proposal. It neither reads source narratives nor establishes
clinical completeness. The ordinary passage search and its receipts are retained.
"""
from collections import Counter, defaultdict
from pathlib import Path
import hashlib
import json
import re
import numpy as np

from ...contracts import digest
from . import record_context as C, warehouse_interpretation as W
from . import retrieval_comparison as R, retrieval_source_plan as P
from . import question_context as Q

VERSION = 'schema-source-selection/1'
CANDIDATES = 12
SELECTED = 4
MIN_COSINE = .3  # Engineering retrieval threshold, not a calibrated probability.
LABEL_FIELDS = {'name', 'test', 'measurement'}


def descriptors(records):
    fields = defaultdict(set); labels = defaultdict(Counter); counts = Counter()
    for r in records:
        category = r['source_table']; counts[category] += 1
        # Only explicitly transported schema fields belong to this lane.
        for f in r.get('_warehouse_fields', []):
            fields[category].add(f)
            value = r.get(f)
            if f in LABEL_FIELDS and isinstance(value, str) and len(value) <= 100:
                labels[category][value] += 1
    result = []
    for category in sorted(counts):
        relation = W.STATEMENTS.get(category, ('', '', ()))[1]
        examples = sorted(labels[category], key=lambda x: (-labels[category][x], x))[:12]
        row = {'id': category, 'received_records': counts[category],
               'fields': sorted(fields[category]), 'sampled_labels': examples,
               'label_values_not_shown': len(labels[category]) - len(examples),
               'relation': relation}
        row['text'] = ('Received source category: ' + category.replace('_', ' ') +
                       '. Source relation: ' + relation.replace('_', ' ') +
                       '. Fields: ' + ', '.join(f.replace('_', ' ') for f in row['fields']) +
                       '. Examples of source labels (not exhaustive): ' + ', '.join(examples))
        result.append(row)
    return result


def normalized(text):
    return ' '.join(re.findall(r'[a-z0-9]+', text.lower()))


def rank(question, rows, encoder, reranker):
    if not rows: return {'candidates': [], 'selected_categories': [], 'exact_categories': []}
    # Complete token windows; no truncation of schema descriptions or questions.
    def vector(text):
        windows = encoder.windows(text, size=160, overlap=24)
        v = encoder.encode([w['text'] for w in windows]).mean(axis=0)
        return v / max(float(np.linalg.norm(v)), 1e-12)
    q = vector(question)
    scored = [{**r, 'cosine': float(vector(r['text']) @ q)} for r in rows]
    scored.sort(key=lambda r: (-r['cosine'], r['id']))
    phrase = ' ' + normalized(question) + ' '
    exact = {r['id'] for r in rows if ' ' + normalized(r['id'].replace('_', ' ')) + ' ' in phrase}
    candidate_ids = {r['id'] for r in scored[:CANDIDATES]} | exact
    candidates = [{**r, 'note_id': r['id'], 'start': 0, 'protected': False}
                  for r in scored if r['id'] in candidate_ids]
    ordered = reranker.rank(question, candidates, top_k=len(candidates))
    selected = [r['id'] for r in ordered if r['cosine'] >= MIN_COSINE][:SELECTED]
    return {'candidates': ordered, 'selected_categories': sorted(set(selected) | exact),
            'exact_categories': sorted(exact),
            'dense_order': [{'category': r['id'], 'cosine': r['cosine']} for r in scored],
            'threshold_meaning': 'Uncalibrated engineering proposal threshold; not evidence sufficiency or clinical relevance.'}


def identity(retriever):
    return {'version': VERSION, 'code': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'encoder': retriever.encoder.identity, 'reranker': retriever.reranker.identity,
            'source_schema': W.identity(), 'reference_selection': hashlib.sha256(Path(Q.__file__).read_bytes()).hexdigest()}


def preserve_references(records, comparison, categories, as_of, *, default_record_ids=()):
    selected_passages = set(comparison['variants']['bge_reranked'])
    paired_roots = {r['source']['record_id'] for r in comparison['candidates'] if r['id'] in selected_passages}
    roots = paired_roots | set(default_record_ids)
    closure = Q.select_sources(records, roots, as_of) if roots else {'connected_record_ids': [], 'reference_edges': []}
    # The source-plan primitive already retains the paired baseline roots.
    # Completed default-search roots outside that set need their categories
    # explicitly added, just like their typed-reference dependencies.
    already = paired_roots | {r['record_id'] for r in records if r['source_table'] in categories}
    extra = set(closure['connected_record_ids']) - already
    added_categories = sorted({r['source_table'] for r in records if r['record_id'] in extra})
    return {'baseline_record_ids': sorted(roots), 'connected_record_ids': closure['connected_record_ids'],
            'paired_baseline_record_ids': sorted(paired_roots), 'default_reference_record_ids': sorted(default_record_ids),
            'reference_edges': closure['reference_edges'], 'additional_categories': added_categories,
            'reason': 'Preserve every baseline typed-reference dependency. The existing whole-category contract may add other records from these categories; relevance remains unassessed.'}


def prepare(path, patient, question, retriever):
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 1500:
        raise ValueError('Bounded original question required')
    records, meta = C.received(path, patient)
    profile = descriptors(records)
    key = digest([identity(retriever), patient, question, digest(records), profile])
    output = Path(path)/'source-selections'; output.mkdir(exist_ok=True)
    target = output/(key+'.json')
    if target.exists():
        saved = json.loads(target.read_text())
        if (digest({k: v for k, v in saved.items() if k != 'id'}) != saved['id']
                or saved.get('request_key') != key or saved['patient_id'] != patient or saved['question'] != question):
            raise ValueError('Source selection integrity changed')
        plan = P.inspect(retriever, {'id': saved['source_plan_id']}) if saved['source_plan_id'] else None
        return {**saved, 'source_plan': plan, 'cache_hit': True}
    routes = rank(question, profile, retriever.encoder, retriever.reranker)
    comparison = R.prepare(retriever, {'patient': patient, 'question': question})['comparison']
    # Keep the normal completed search and its verified typed-reference closure.
    # Its additional candidates are outside the fixed-set ranking experiment.
    baseline = Q.prepare(path, patient, question, retriever)
    references = preserve_references(records, comparison, routes['selected_categories'], meta['snapshot']['as_of'],
        default_record_ids=baseline['connected_record_ids'])
    request = {'comparison_id': comparison['id'], 'baseline': 'bge_reranked',
               'categories': sorted(set(routes['selected_categories']) | set(references['additional_categories']))}
    preview = P.preview(retriever, request)
    plan = P.prepare(retriever, {'request': request, 'preview_id': preview['id']}) if preview['can_prepare'] else None
    if digest(C.received(path, patient)[0]) != digest(records):
        raise ValueError('Received records changed during source selection')
    saved = {'schema': VERSION, 'request_key': key, 'patient_id': patient, 'question': question,
             'identity': identity(retriever), 'snapshot': meta['snapshot'],
             'patient_source_fingerprint': digest(records), 'metadata_inventory': profile,
             'schema_retrieval': routes, 'comparison_id': comparison['id'],
             'default_reference_scope_id': baseline['id'],
             'reference_preservation': references,
             'source_plan_id': plan['plan']['id'] if plan else None, 'preview': preview,
             'status': plan['plan']['status'] if plan else 'source_scope_needs_narrowing',
             'new_paid_api_calls': 0, 'clinical_validated': False,
             'selection_sufficiency': 'unassessed', 'complete_evidence_guarantee': False,
             'limits': ['Source metadata ranking proposes categories; it does not establish clinical relevance.',
                        'All received records in selected categories join the unchanged passage baseline.',
                        'Other categories and cross-record clinical meaning remain unassessed.',
                        'No inference or work dispatch occurs during source selection.']}
    saved['id'] = digest(saved)
    temp = target.with_suffix('.tmp'); temp.write_text(json.dumps(saved, indent=2, allow_nan=False)); temp.replace(target)
    return {**saved, 'source_plan': plan, 'cache_hit': False}
